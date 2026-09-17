"""M4 prompt context builder (SPEC §54): only bounded per-character fields,
never the whole database (constitution П1 + §54).
"""
import json
from typing import Dict, List

from sqlalchemy.orm import Session

from app.ai.memory import select_memories
from app.ai.transports import character_identity
from app.db.models import (
    Character,
    CharacterNeeds,
    CharacterTask,
    CharacterTrait,
    Relationship,
)


def nearby_character_ids(session: Session, world_id: str, character_id: str,
                         limit: int = 5) -> List[str]:
    me = session.query(Character).filter_by(
        world_id=world_id, id=character_id).first()
    if me is None or not me.alive:
        return []
    rows = (
        session.query(Character)
        .filter(
            Character.world_id == world_id,
            Character.id != character_id,
            Character.location_id == me.location_id,
            Character.alive.is_(True),
        )
        .order_by(Character.id)
        .limit(limit)
        .all()
    )
    return [c.id for c in rows]


def important_relationships(session: Session, world_id: str,
                            character_id: str, limit: int = 3) -> List[Dict]:
    rows = (
        session.query(Relationship)
        .filter(
            Relationship.world_id == world_id,
            (Relationship.character_a == character_id)
            | (Relationship.character_b == character_id),
        )
        .all()
    )
    pairs = []
    for r in rows:
        other = (r.character_b if r.character_a == character_id
                 else r.character_a)
        pairs.append((abs(r.affection), other, r.affection))
    pairs.sort(key=lambda p: (-p[0], p[1]))
    return [{"character_id": other, "affection": aff}
            for _, other, aff in pairs[:limit]]


def _needs_line(session: Session, character_id: str) -> str:
    n = session.query(CharacterNeeds).filter_by(
        character_id=character_id).first()
    if n is None:
        return "needs: unknown"
    fields = {
        k: getattr(n, k)
        for k in ("hunger", "energy", "hygiene", "social", "fun")
        if hasattr(n, k)
    }
    lowest = sorted(fields.items(), key=lambda kv: kv[1])[:2]
    return "needs: " + ", ".join(f"{k}={v:.0f}" for k, v in lowest)


def _goal_line(session: Session, character_id: str) -> str:
    task = (
        session.query(CharacterTask)
        .filter_by(character_id=character_id, status="STARTED")
        .order_by(CharacterTask.id.desc())
        .first()
    )
    return f"current_task: {task.task_type}" if task else "current_task: none"


def _traits_line(session: Session, character_id: str) -> str:
    rows = session.query(CharacterTrait).filter_by(
        character_id=character_id).all()
    parts = [f"{r.trait_key}={r.value}" for r in rows]
    return "personality: " + (", ".join(parts) if parts else "unknown")


def build_decision_prompt(session: Session, world_id: str, character_id: str,
                          settings, event_type: str,
                          now: int = 0) -> Dict:
    """Returns {prompt, available_actions, nearby}. Available actions follow
    the M4 catalog: `socialize_with` requires at least one nearby alive
    character (spec R7)."""
    me = session.query(Character).filter_by(
        world_id=world_id, id=character_id).first()
    nearby = nearby_character_ids(session, world_id, character_id)
    recent = select_memories(session, world_id, character_id,
                             settings.llm.memory.top_k, now)

    available_actions: List[str] = ["socialize_with"] if nearby else []
    identity = character_identity(session, character_id)
    location = me.location_id if me else None

    lines = [
        f"identity: {identity}",
        _traits_line(session, character_id),
        _needs_line(session, character_id),
        _goal_line(session, character_id),
        f"current_location: {location}",
        f"nearby_characters: {json.dumps(nearby)}",
        "important_relationships: "
        + json.dumps(important_relationships(session, world_id, character_id)),
        "recent_memories: "
        + json.dumps([m.summary for m in recent]),
        f"current_event: {event_type}",
        f"available_actions: {json.dumps(available_actions)}",
        "",
        "Decide the next action. Reply with strict JSON: "
        '{"decision": str, "target_character_id": str|null, '
        '"confidence": float, "reason": str}',
    ]
    return {
        "prompt": "\n".join(lines),
        "available_actions": available_actions,
        "nearby": nearby,
    }


def build_dialogue_prompt(session: Session, world_id: str, character_id: str,
                          messages: List[Dict]) -> str:
    identity = character_identity(session, character_id)
    history = "\n".join(
        f"{m.get('role', 'user')}: {m.get('content', '')}" for m in messages
    )
    return (
        f"identity: {identity}\n"
        f"conversation:\n{history}\n\n"
        "Reply as this character with strict JSON: "
        '{"reply": str, "intent": str|null, "confidence": float}'
    )


def build_summarize_prompt(summaries: List[str]) -> str:
    return (
        "recent_memories:\n"
        + "\n".join(f"- {s}" for s in summaries)
        + "\n\nSummarize into one line. Reply with strict JSON: "
        '{"summary": str}'
    )
