"""M5 (SPEC §104/R7, §65): NPC dialogue pipeline.

player message → safety gate → context (memories/relationship/world) →
LLM (M4 transport) OR deterministic fallback (llm-off, П2) → npc reply
+ 3 suggested responses. One mutation (#183): the encounter affection —
at most one relationship delta per cooldown window per (character, npc)
pair, reusing the SOCIALIZE rule affection-only (apply_dialogue_encounter
below). #195: when that delta CREATES the canonical Relationship row, the
hook also emits exactly one RELATIONSHIP_FORMED WorldEvent inside the
same transaction. #195 slice 2: on a PRE-EXISTING row whose encounter
crosses a band boundary (ACTUAL before/after bands differ), the hook
additionally emits exactly one RELATIONSHIP_CHANGED WorldEvent plus the
linked RelationshipEvent row — mirroring the SOCIALIZE site in
lifecycle.py; creation emits FORMED only (the documented asymmetry:
formation and crossing are mutually exclusive by construction).
Everything else stays journal-only: the dialogue itself never mutates
the world beyond the encounter affection.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.db.models import (
    Character,
    DialogueMessage,
    DialogueSession,
    Location,
    Memory,
    Relationship,
    RelationshipEvent,
)
from app.events.events import EventType, log_event
from app.social.affection import affection_band


def safety_check(content: str) -> Optional[str]:
    """Deterministic gate (R7/П4). Returns error string or None."""
    if not content or not content.strip():
        return "message must not be empty"
    if len(content) > 2000:
        return "message must be at most 2000 chars"
    return None


# #183 v1: the encounter cooldown, in GAME MINUTES (world clock, 1440/day)
# per (character_id, npc_id) pair. Deliberately a named constant, not a
# config knob — v1 keeps the tuning surface at zero (no config schema
# churn); revisit only if real tuning demand appears.
DIALOGUE_ENCOUNTER_COOLDOWN_MIN = 30


def apply_dialogue_encounter(
    session: Session, world_id: str, session_row, now_ts: int, settings
) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """#183: one relationship delta per «encounter» (not per message).

    An encounter is a cluster of messages inside the cooldown window:
    the delta applies only when NO prior message exchange of the same
    (character_id, npc_id) pair falls inside the window. The mutation is
    exactly the shared SOCIALIZE rule — app.social.affection.
    apply_socialize_affection — affection-only by construction (the
    helper writes Relationship.affection + updated_at and nothing else),
    so trust/respect/fear/anger/attraction/romantic_interest/familiarity
    are byte-untouched, and both the LLM and the fallback path get the
    same behavior (parity by construction: this runs once, after the
    reply source is resolved, before the caller's single commit).

    Eligibility SQL (exact window):
        exists DialogueMessage m
        join DialogueSession s on s.id = m.session_id
        where s.world_id = :world_id
          and s.character_id = :character_id and s.npc_id = :npc_id
          and m.game_timestamp > :now_ts - 30          -- strict: a message
          -- exactly 30 game minutes old is out of the window
          and m.id != :this_request_message_id

    The current request's own message rows are excluded BY ID, not by a
    ``game_timestamp < now_ts`` upper bound: the flushed user message
    carries game_timestamp == now_ts, but with a frozen clock a PRIOR
    exchange can share the same now_ts — a timestamp upper bound would
    hide it and allow a second delta inside one window (the exact bug
    this cooldown exists to prevent). At hook time the only message of
    this request already visible to queries is the flushed user message
    (== the latest row of this session); the npc reply is added after
    the hook returns.

    Emits NO band events (no SOCIAL_INTERACTION; RELATIONSHIP_CHANGED
    from dialogue fires only per the slice-2 crossing contract below).
    #195: the ONE exception is relationship CREATION — when this call
    creates the canonical Relationship row, exactly one RELATIONSHIP_FORMED
    WorldEvent is emitted (actor = the player character, target = the npc,
    location None; payload {target_id, affection_delta (RULE),
    affection_after}). #195 slice 2: on a PRE-EXISTING row the hook
    detects a band crossing from the ACTUAL before/after values
    (affection_band on both — never before + rule_delta, so a clamp cannot
    invent a crossing): when the bands differ, exactly one
    RELATIONSHIP_CHANGED WorldEvent (actor = the player, target = the npc,
    location None; payload {band (the AFTER band), affection_before,
    affection_after}) plus the linked RelationshipEvent row (canonical
    pair, event_id = the WorldEvent id, impact = the RULE delta) —
    mirroring the SOCIALIZE site in lifecycle.py. The documented
    asymmetry: CREATION emits FORMED only — band_changed is None even
    when the initial affection lands in / crosses a band (formation and
    crossing are mutually exclusive by construction; SOCIALIZE's lazy
    creation CAN emit CHANGED — that side stays in lifecycle.py).
    Atomic: runs inside the caller's single dialogue_message
    transaction (one commit for journal + effect + events — rollback
    drops all; log_event flushes, does not commit).

    Returns a 3-tuple (effect, formed, band_changed):
        effect: {"applied": True, "affection_delta": <float>,
        "affection_after": <float>} or {"applied": False} (cooldown window
        not elapsed). affection_delta is the HONEST applied change (row
        after − row before; 0 at the ±100 clamp) — the shared helper's
        RULE delta (+2.0/-5.0) equals it everywhere except at the clamp.
        formed: the RELATIONSHIP_FORMED payload dict when this call
        created the canonical Relationship row (EXACTLY the event
        payload), else None.
        band_changed: the RELATIONSHIP_CHANGED payload dict when this
        call hit a PRE-EXISTING row whose ACTUAL before/after bands
        differ (EXACTLY the event payload), else None — always None on
        the cooldown-skip and on creation (the asymmetry above).
    """
    from app.social.affection import apply_socialize_affection

    latest_here = (
        session.query(DialogueMessage)
        .filter_by(session_id=session_row.id)
        .order_by(DialogueMessage.id.desc())
        .first()
    )
    window_start = now_ts - DIALOGUE_ENCOUNTER_COOLDOWN_MIN
    cooling = (
        session.query(DialogueMessage.id)
        .join(DialogueSession, DialogueSession.id == DialogueMessage.session_id)
        .filter(
            DialogueSession.world_id == world_id,
            DialogueSession.character_id == session_row.character_id,
            DialogueSession.npc_id == session_row.npc_id,
            DialogueMessage.game_timestamp > window_start,
        )
    )
    if latest_here is not None:
        cooling = cooling.filter(DialogueMessage.id != latest_here.id)
    if cooling.first() is not None:
        return {"applied": False}, None, None

    # The helper returns the RULE delta (+2.0/-5.0 — the SOCIAL_INTERACTION
    # contract; lifecycle parity). The RESPONSE reports the honest applied
    # change (row after − row before): they are equal everywhere except at
    # the ±100 clamp, where honesty requires the actual 0 — the UI note
    # («Симпатия +0») says what really happened.
    a, b = sorted([session_row.character_id, session_row.npc_id])
    rel = (
        session.query(Relationship)
        .filter_by(world_id=world_id, character_a=a, character_b=b)
        .first()
    )
    # #195: creation flag captured from the SAME pre-helper read (no extra
    # query) — the canonical row is absent exactly when this encounter is
    # the one creating it.
    created = rel is None
    affection_before = (
        rel.affection if rel is not None
        else settings.social.initial_affection
    )
    # The helper's RULE delta feeds the lifecycle/SOCIALIZE contract; the
    # response reports the honest post-clamp change computed below.
    rule_delta = apply_socialize_affection(
        session, world_id, session_row.character_id, session_row.npc_id,
        now_ts, settings,
    )
    rel = (
        session.query(Relationship)
        .filter_by(world_id=world_id, character_a=a, character_b=b)
        .first()
    )
    effect = {
        "applied": True,
        "affection_delta": rel.affection - affection_before,
        "affection_after": rel.affection if rel is not None else None,
    }
    if not created:
        # #195: the row already existed — an encounter on a pre-existing
        # relationship is not a formation. #195 slice 2: the ONE band
        # event — when the encounter's ACTUAL before/after bands differ,
        # exactly one RELATIONSHIP_CHANGED + the linked RelationshipEvent
        # row, mirroring the SOCIALIZE site (lifecycle.py): payload band =
        # the AFTER band, impact = the RULE delta. Detection uses the
        # actual values on both sides (never before + rule_delta — a
        # clamp at ±100 cannot invent a crossing). log_event only
        # flushes: everything shares the caller's single commit/rollback.
        band_before = affection_band(affection_before)
        band_after = affection_band(rel.affection)
        if band_before != band_after:
            changed = {
                "band": band_after,
                "affection_before": affection_before,
                "affection_after": rel.affection,
            }
            event_id = log_event(
                session, world_id, now_ts, EventType.RELATIONSHIP_CHANGED,
                actor_id=session_row.character_id,
                target_id=session_row.npc_id,
                location_id=None, payload=dict(changed),
            )
            session.add(RelationshipEvent(
                world_id=world_id, character_a=a, character_b=b,
                event_type=EventType.RELATIONSHIP_CHANGED.value,
                impact=rule_delta, event_id=event_id,
                game_timestamp=now_ts,
            ))
            return effect, None, changed
        return effect, None, None
    # #195: this encounter CREATED the canonical Relationship row — emit
    # exactly one RELATIONSHIP_FORMED milestone. The payload carries the
    # RULE delta (the helper's +2.0/-5.0, honest at creation except at the
    # initial_affection clamp); actor = the player character, target = the
    # npc, location None (SOCIAL_INTERACTION precedent). log_event only
    # flushes: the event shares the caller's single commit/rollback.
    # Slice 2: band_changed is None ALWAYS on creation — even when the
    # initial affection lands in / crosses a band (the documented
    # asymmetry: formation and crossing are mutually exclusive by
    # construction; SOCIALIZE's lazy creation CAN emit CHANGED).
    formed = {
        "target_id": session_row.npc_id,
        "affection_delta": rule_delta,
        "affection_after": rel.affection if rel is not None else None,
    }
    log_event(
        session, world_id, now_ts, EventType.RELATIONSHIP_FORMED,
        actor_id=session_row.character_id, target_id=session_row.npc_id,
        location_id=None, payload=dict(formed),
    )
    return effect, formed, None


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

    # #89 (A7): public roles enter the dialogue context (derive-on-read,
    # authoritative rows only). Appended keys — existing keys keep their
    # positions/shapes (m-units pin those).
    from app.social.roles import derive_roles

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
        "npc_roles": derive_roles(session, world_id, npc.id),
        "player_roles": derive_roles(session, world_id, user_char.id),
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


def _roles_titles(roles: Any, limit: int = 3) -> str:
    """#61 v2: bounded «, »-joined role titles (pure formatting).

    Roles enter here already derived — build_context puts npc_roles /
    player_roles into the context dict from app.social.roles.derive_roles
    (the exact set GET /characters/{cid}/roles returns: roles.py has no
    visibility field, derive-on-read roles are public facts by
    construction — an unreported crime is a secret fact and never grants
    a role; app.py get_character_roles mirrors derive_roles unfiltered).
    """
    if not isinstance(roles, list):
        return ""
    return ", ".join(
        str(r.get("title", "")) for r in roles[:limit] if r.get("title")
    )


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
    # #61 v2: social roles as world-perceived context, right after the
    # identity line. Reuses the lists build_context already derived
    # (npc_roles/player_roles) instead of re-deriving; each line omitted
    # when that side derives no roles — the prompt stays byte-identical
    # to the pre-roles text then (LLM-off behavior unchanged: the
    # fallback path below never sees this prompt).
    roles_block = ""
    npc_roles = _roles_titles(context.get("npc_roles"))
    if npc_roles:
        roles_block += f"Роли: {npc_roles}\n"
    player_roles = _roles_titles(context.get("player_roles"))
    if player_roles:
        roles_block += f"Роли собеседника: {player_roles}\n"
    prompt = (
        "identity: {name}\n"
        "{roles_block}"
        "Ты — {name}, житель города. Отвечай кратко (1-3 предложения), "
        "в характерe персонажа, на русском языке.\n"
        "Контекст: местоположение {loc}. Отношение к собеседнику: "
        "доверие {trust:.0f}, симпатия {aff:.0f}.\n"
        "Воспоминания: {mem}\n"
        "Последние сообщения: {hist}\n"
        "Сообщение игрока: {msg}".format(
            name=context.get("npc_name", "NPC"),
            roles_block=roles_block,
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
