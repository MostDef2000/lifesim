"""M4 NPC memory (SPEC §55-57): capture, daily consolidation, recall."""
import json
from typing import List

from sqlalchemy.orm import Session

from app.db.models import (
    Character,
    Memory,
    WorldEvent,
)
from app.events.events import EventType, log_event

# Deterministic Tier-0 importance/valence maps (spec R8); the LLM classify
# path (stub or real) may refine importance for non-catalogued events later.
IMPORTANCE_MAP = {
    "CONFLICT": 70,
    "RECONCILIATION": 60,
    "ELECTION": 50,
    "LAW_VIOLATION": 40,
    "ORG_FEAST": 30,
}
VALENCE_MAP = {
    "CONFLICT": -0.6,
    "RECONCILIATION": 0.7,
    "ORG_FEAST": 0.5,
}
DEATH_IMPORTANCE = 90
DEATH_VALENCE = -0.9


def _summary_for(event: WorldEvent) -> str:
    try:
        payload = json.loads(event.payload) if event.payload else {}
    except (ValueError, TypeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    parts = [event.event_type]
    if event.actor_id:
        parts.append(f"actor={event.actor_id}")
    if event.target_id:
        parts.append(f"target={event.target_id}")
    law = payload.get("law_key")
    if law:
        parts.append(f"law={law}")
    org = payload.get("organization_id")
    if org is not None:
        parts.append(f"org={org}")
    return " ".join(parts)


def _participants(session: Session, world_id: str, event: WorldEvent,
                  include_witnesses: bool) -> List[str]:
    ids = []
    if event.actor_id:
        ids.append(event.actor_id)
    if event.target_id and event.target_id != event.actor_id:
        ids.append(event.target_id)
    if include_witnesses:
        actor = session.query(Character).filter_by(
            world_id=world_id, id=event.actor_id).first() if event.actor_id \
            else None
        if actor is not None:
            witnesses = (
                session.query(Character)
                .filter(
                    Character.world_id == world_id,
                    Character.location_id == actor.location_id,
                    Character.alive.is_(True),
                )
                .all()
            )
            for w in witnesses:
                if w.id not in ids:
                    ids.append(w.id)
    return ids


def capture_memories(session: Session, world_id: str, settings,
                     game_timestamp: int) -> int:
    """Capture memories for the PREVIOUS day's significant events.
    Gate R1 first line: zero work when llm is disabled. Returns created."""
    if not settings.llm.enabled:
        return 0
    day_start = (game_timestamp // 1440) * 1440
    window_start = day_start - 1440

    events = (
        session.query(WorldEvent)
        .filter(
            WorldEvent.world_id == world_id,
            WorldEvent.game_timestamp >= window_start,
            WorldEvent.game_timestamp < day_start,
        )
        .order_by(WorldEvent.id)
        .all()
    )

    existing = {
        (m.character_id, m.event_id)
        for m in session.query(Memory).filter_by(world_id=world_id).all()
    }
    created = 0
    for ev in events:
        if ev.event_type == EventType.CHARACTER_DIED.value:
            # Death is remembered by every alive character (bounded pop).
            alive = (
                session.query(Character)
                .filter(Character.world_id == world_id,
                        Character.alive.is_(True))
                .all()
            )
            for c in alive:
                if (c.id, ev.id) in existing:
                    continue
                session.add(Memory(
                    world_id=world_id, character_id=c.id, event_id=ev.id,
                    memory_type=EventType.CHARACTER_DIED.value,
                    importance=DEATH_IMPORTANCE,
                    emotional_valence=DEATH_VALENCE,
                    summary=_summary_for(ev), created_at=day_start))
                created += 1
            continue
        importance = IMPORTANCE_MAP.get(ev.event_type)
        if importance is None:
            continue
        valence = VALENCE_MAP.get(ev.event_type, 0.0)
        for cid in _participants(session, world_id, ev,
                                 settings.llm.memory.witnesses):
            if (cid, ev.id) in existing:
                continue
            session.add(Memory(
                world_id=world_id, character_id=cid, event_id=ev.id,
                memory_type=ev.event_type, importance=importance,
                emotional_valence=valence, summary=_summary_for(ev),
                created_at=day_start))
            created += 1

    if created:
        session.flush()

    # MEMORY_CREATED events: one per captured memory (spec R10).
    for m in (
        session.query(Memory)
        .filter(Memory.world_id == world_id,
                Memory.created_at == day_start)
        .all()
    ):
        if m.memory_type.startswith("consolidated_"):
            continue
        log_event(session, world_id, game_timestamp,
                  EventType.MEMORY_CREATED, actor_id=m.character_id,
                  target_id=None, payload={
                      "event_id": m.event_id,
                      "memory_type": m.memory_type,
                      "importance": m.importance,
                  })
    return created


def consolidate_memories(session: Session, world_id: str, settings,
                         game_timestamp: int, transport) -> int:
    """§57: group yesterday's memories per (character, type); groups >= 2
    become one consolidated record (transport summarize), originals removed.
    Derived-cache deletion only — world_events stays the source of truth."""
    if not settings.llm.enabled:
        return 0
    from app.ai.prompts import build_summarize_prompt

    day_start = (game_timestamp // 1440) * 1440

    rows = (
        session.query(Memory)
        .filter(
            Memory.world_id == world_id,
            # The just-captured batch (capture stamps created_at=day_start).
            Memory.created_at == day_start,
            ~Memory.memory_type.startswith("consolidated_"),
        )
        .order_by(Memory.id)
        .all()
    )
    groups = {}
    for m in rows:
        groups.setdefault((m.character_id, m.memory_type), []).append(m)

    total_created = total_removed = 0
    touched_chars = set()
    for (cid, mtype), group in groups.items():
        if len(group) < 2:
            continue
        summaries = [g.summary for g in group]
        raw = transport.complete(build_summarize_prompt(summaries),
                                 "summarize")
        try:
            data = __import__("json").loads(raw)
            summary = str(data.get("summary", "")).strip()
        except (ValueError, AttributeError):
            summary = ""
        if not summary:
            summary = "consolidated: " + ", ".join(
                sorted({s.split(" ", 1)[0] for s in summaries}))
        session.add(Memory(
            world_id=world_id, character_id=cid,
            event_id=group[0].event_id,
            memory_type=f"consolidated_{mtype}",
            importance=max(g.importance for g in group),
            emotional_valence=group[0].emotional_valence,
            summary=summary, created_at=day_start))
        for g in group:
            session.delete(g)
        total_created += 1
        total_removed += len(group)
        touched_chars.add(cid)

    if total_created:
        session.flush()
        log_event(session, world_id, game_timestamp,
                  EventType.MEMORY_CONSOLIDATED, actor_id=None, target_id=None,
                  payload={"character_ids": sorted(touched_chars),
                           "groups": total_created,
                           "removed": total_removed})
    return total_created


def select_memories(session: Session, world_id: str, character_id: str,
                    k: int, now: int) -> List[Memory]:
    """Top-K by importance DESC, created_at DESC; updates last_recalled_at."""
    rows = (
        session.query(Memory)
        .filter(Memory.world_id == world_id,
                Memory.character_id == character_id)
        .order_by(Memory.importance.desc(), Memory.created_at.desc(),
                  Memory.id.desc())
        .limit(k)
        .all()
    )
    for m in rows:
        m.last_recalled_at = now
    if rows:
        session.flush()
    return rows
