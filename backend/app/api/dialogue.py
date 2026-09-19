"""M5 (SPEC §104/R7, §65): NPC dialogue pipeline.

player message → safety gate → context (memories/relationship/world) →
LLM (M4 transport) OR deterministic fallback (llm-off, П2) → npc reply
+ 3 suggested responses. Journal-only: dialogue never mutates the world.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.db.models import (
    Character,
    DialogueSession,
    Location,
    Memory,
    Relationship,
)


def safety_check(content: str) -> Optional[str]:
    """Deterministic gate (R7/П4). Returns error string or None."""
    if not content or not content.strip():
        return "message must not be empty"
    if len(content) > 2000:
        return "message must be at most 2000 chars"
    return None


def build_context(
    session: Session, world_id: str, user_char: Character, npc: Character
) -> Dict[str, Any]:
    """World state for the §65 pipeline: memories, relationship, location."""
    memories = (
        session.query(Memory)
        .filter_by(world_id=world_id, character_id=npc.id)
        .order_by(Memory.importance.desc(), Memory.id.desc())
        .limit(5)
        .all()
    )
    rel = (
        session.query(Relationship)
        .filter_by(world_id=world_id, character_a=user_char.id, character_b=npc.id)
        .first()
    )
    if rel is None:
        rel = (
            session.query(Relationship)
            .filter_by(world_id=world_id, character_a=npc.id, character_b=user_char.id)
            .first()
        )
    loc = session.get(Location, npc.location_id)
    # 013 (R4): NPC's most recent mainland trip enters the dialogue context
    from app.db.models import WorldEvent

    last_return = (
        session.query(WorldEvent)
        .filter_by(
            world_id=world_id,
            event_type="TRAVEL_EXTERNAL_RETURNED",
            actor_id=npc.id,
        )
        .order_by(WorldEvent.id.desc())
        .first()
    )
    context = {
        "npc_name": f"{npc.first_name} {npc.last_name}".strip(),
        "npc_memories": [m.summary for m in memories],
        "relationship": (
            {
                "trust": rel.trust, "affection": rel.affection, "respect": rel.respect,
            }
            if rel is not None
            else {"trust": 0.0, "affection": 0.0, "respect": 0.0}
        ),
        "npc_location": loc.name if loc is not None else None,
    }
    if last_return is not None:
        import json as _json

        payload = _json.loads(last_return.payload) if last_return.payload else {}
        parts = [payload.get("purpose", "поездка")]
        if payload.get("healed"):
            parts.append("полечился")
        context["npc_recent_trip"] = "; ".join(str(p) for p in parts)
    return context


_FALLBACK_BY_AFFECTION = [
    # (affection >= threshold, template)
    (50.0, "Привет, {player}! Рад тебя видеть. Давно не разговаривали — как ты?"),
    (0.0, "Привет, {player}. Чем могу помочь?"),
    (-50.0, "{player}, я тебя слышу. Но давай по существу."),
]


def fallback_reply(context: Dict[str, Any], player_name: str) -> str:
    """Deterministic NPC reply when llm.enabled=false (П2)."""
    base = _fallback_base(context, player_name)
    trip = context.get("npc_recent_trip")
    if trip:
        return f"{base} Только что вернулся с материка: {trip}."
    return base


def _fallback_base(context: Dict[str, Any], player_name: str) -> str:
    affection = context.get("relationship", {}).get("affection", 0.0)
    for threshold, template in _FALLBACK_BY_AFFECTION:
        if affection >= threshold:
            return template.format(player=player_name)
    return "Привет."


def suggested_responses(context: Dict[str, Any]) -> List[str]:
    """3 deterministic suggestions (§64). Player may always type free text."""
    npc_name = context.get("npc_name", "собеседник")
    return [
        f"Спросить у {npc_name}, как дела",
        f"Рассказать {npc_name} свежие новости",
        "Попрощаться и уйти",
    ]


def llm_reply(
    session: Session,
    settings,
    world_id: str,
    session_row: DialogueSession,
    history: List[Dict[str, str]],
    context: Dict[str, Any],
    user_message: str,
) -> Optional[str]:
    """
    Full §65 LLM path. Returns None when llm is disabled or the response
    is unusable — caller falls back (П2).
    """
    import json as _json

    from app.ai.transports import build_transport

    if not settings.llm.enabled:
        return None
    transport = build_transport(settings.llm)
    prompt = (
        "identity: {name}\n"
        "Ты — {name}, житель города. Отвечай кратко (1-3 предложения), "
        "в характерe персонажа, на русском языке.\n"
        "Контекст: местоположение {loc}. Отношение к собеседнику: "
        "доверие {trust:.0f}, симпатия {aff:.0f}.\n"
        "Воспоминания: {mem}\n"
        "Последние сообщения: {hist}\n"
        "Сообщение игрока: {msg}".format(
            name=context.get("npc_name", "NPC"),
            loc=context.get("npc_location"),
            trust=context.get("relationship", {}).get("trust", 0.0),
            aff=context.get("relationship", {}).get("affection", 0.0),
            mem="; ".join(context.get("npc_memories", [])[:3]) or "нет",
            hist=_json.dumps(history[-4:], ensure_ascii=False),
            msg=user_message,
        )
    )
    try:
        raw = transport.complete(prompt, "dialogue")
    except Exception:
        return None
    if not raw or not isinstance(raw, str):
        return None
    reply = raw.strip()
    # Stub transport (and §53 contract) replies with a JSON object
    try:
        parsed = _json.loads(reply)
        reply = parsed.get("reply") or ""
    except (_json.JSONDecodeError, AttributeError):
        pass  # free-form reply (real LLM without JSON wrapper)
    reply = (reply or "").strip()
    if not reply or len(reply) > 2000:
        return None
    return reply
