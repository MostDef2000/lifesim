import json
from enum import Enum
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.db.models import WorldEvent


class EventType(Enum):
    CHARACTER_CREATED = "CHARACTER_CREATED"
    CHARACTER_MOVED = "CHARACTER_MOVED"
    ITEM_TRANSFERRED = "ITEM_TRANSFERRED"
    ITEM_CONSUMED = "ITEM_CONSUMED"
    CHARACTER_DIED = "CHARACTER_DIED"
    SALARY_PAID = "SALARY_PAID"
    SUPPLY_ARRIVED = "SUPPLY_ARRIVED"
    PURCHASE = "PURCHASE"
    TASK_FAILED = "TASK_FAILED"
    TASK_COMPLETED = "TASK_COMPLETED"

def log_event(
    session: Session,
    world_id: str,
    game_timestamp: int,
    event_type: EventType,
    actor_id: Optional[str] = None,
    target_id: Optional[str] = None,
    location_id: Optional[int] = None,
    payload: Optional[Dict[str, Any]] = None
) -> int:
    event = WorldEvent(
        world_id=world_id,
        game_timestamp=game_timestamp,
        event_type=event_type.value,
        actor_id=actor_id,
        target_id=target_id,
        location_id=location_id,
        payload=json.dumps(payload or {})
    )
    session.add(event)
    session.flush()
    return event.id

def get_events(session: Session, world_id: str, event_type: Optional[EventType] = None):
    query = session.query(WorldEvent).filter(WorldEvent.world_id == world_id)
    if event_type:
        query = query.filter(WorldEvent.event_type == event_type.value)
    return query.order_by(WorldEvent.id).all()
