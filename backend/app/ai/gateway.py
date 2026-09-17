"""M4 AI Gateway (SPEC §49-51): enqueue-only facade; the worker drains.

The gateway never talks to a transport — it only records requests into
`ai_requests` (constitution П1: auditable server state).
"""
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.db.models import AiRequest

PRIORITY_RANK = {"critical": 0, "interactive": 1, "background": 2}


def enqueue(
    session: Session,
    world_id: str,
    character_id: Optional[str],
    task: str,
    context: Dict[str, Any],
    priority: str,
    game_timestamp: int,
) -> int:
    if priority not in PRIORITY_RANK:
        raise ValueError(f"unknown priority: {priority}")
    row = AiRequest(
        world_id=world_id,
        character_id=character_id,
        task=task,
        context=context,
        priority=priority,
        status="pending",
        created_at=game_timestamp,
        game_timestamp=game_timestamp,
    )
    session.add(row)
    session.flush()
    return row.id


def _decisions_today(session: Session, world_id: str, character_id: str,
                     game_timestamp: int, task: str) -> int:
    day_start = (game_timestamp // 1440) * 1440
    return (
        session.query(AiRequest)
        .filter(
            AiRequest.world_id == world_id,
            AiRequest.character_id == character_id,
            AiRequest.task == task,
            AiRequest.created_at >= day_start,
            AiRequest.created_at < day_start + 1440,
        )
        .count()
    )


def enqueue_decide(
    session: Session,
    world_id: str,
    settings,
    character_id: str,
    event_id: int,
    event_type: str,
    game_timestamp: int,
) -> Optional[int]:
    """Conflict-triggered decision request (spec R7): at most one per
    character per game day. Gate R1 is the first line."""
    if not settings.llm.enabled:
        return None
    if _decisions_today(session, world_id, character_id,
                        game_timestamp, "decide") >= 1:
        return None
    return enqueue(
        session, world_id, character_id, "decide",
        {"event_id": event_id, "event_type": event_type},
        "interactive", game_timestamp,
    )


def enqueue_classify(session, world_id, settings, character_id, event_id,
                     event_type, game_timestamp) -> Optional[int]:
    if not settings.llm.enabled:
        return None
    return enqueue(
        session, world_id, character_id, "classify",
        {"event_id": event_id, "event_type": event_type},
        "background", game_timestamp,
    )


def enqueue_summarize(session, world_id, settings, character_id, context,
                      game_timestamp) -> Optional[int]:
    if not settings.llm.enabled:
        return None
    return enqueue(
        session, world_id, character_id, "summarize", context,
        "background", game_timestamp,
    )


def enqueue_memory_select(session, world_id, settings, character_id,
                          game_timestamp) -> Optional[int]:
    if not settings.llm.enabled:
        return None
    return enqueue(
        session, world_id, character_id, "memory_select", {},
        "background", game_timestamp,
    )


def enqueue_dialogue(session, world_id, settings, character_id, messages,
                     game_timestamp, session_id: str) -> Optional[int]:
    if not settings.llm.enabled:
        return None
    return enqueue(
        session, world_id, character_id, "dialogue",
        {"messages": messages, "session_id": session_id},
        "interactive", game_timestamp,
    )
