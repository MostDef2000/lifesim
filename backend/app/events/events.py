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
    SOCIAL_INTERACTION = "SOCIAL_INTERACTION"
    RELATIONSHIP_CHANGED = "RELATIONSHIP_CHANGED"
    CONFLICT = "CONFLICT"
    ELECTION = "ELECTION"
    ORG_DUES = "ORG_DUES"
    ORG_FEAST = "ORG_FEAST"
    LAW_ENACTED = "LAW_ENACTED"
    LAW_VIOLATION = "LAW_VIOLATION"
    RECONCILIATION = "RECONCILIATION"
    AI_DECISION = "AI_DECISION"
    # M5 (SPEC §104): player control audit + goal queue
    CONTROL_CHANGED = "CONTROL_CHANGED"
    GOAL_QUEUED = "GOAL_QUEUED"
    # M6 (SPEC §105): visual asset audit
    VISUAL_ASSET_CREATED = "VISUAL_ASSET_CREATED"
    # M7 (SPEC §106/39): external travel audit
    TRAVEL_EXTERNAL_DEPARTED = "TRAVEL_EXTERNAL_DEPARTED"
    TRAVEL_EXTERNAL_RETURNED = "TRAVEL_EXTERNAL_RETURNED"
    MEMORY_CREATED = "MEMORY_CREATED"
    MEMORY_CONSOLIDATED = "MEMORY_CONSOLIDATED"
    WEATHER_CHANGED = "WEATHER_CHANGED"  # 010 (§71): daily weather record
    OBJECT_BURNING = "OBJECT_BURNING"  # 011 (§72): object caught fire
    OBJECT_BURNED = "OBJECT_BURNED"  # 011 (§72): object fully burned
    MARKET_LISTED = "MARKET_LISTED"  # 012 (§74): item offered for sale
    MARKET_SOLD = "MARKET_SOLD"  # 012 (§74): offer bought via ledger
    CONSTRUCTED = "CONSTRUCTED"  # 012 (§75): new object built
    OBJECT_DESTROYED = "OBJECT_DESTROYED"  # 012 (§76): condition reached 0
    MESSAGE_SENT = "MESSAGE_SENT"  # 013: letters/phone via contacts

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
