import json
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.db.models import ResourceBalance, WorldObject
from app.events.events import EventType, log_event


def create_object(
    session: Session,
    world_id: str,
    object_type: str,
    location_id: int,
    quantity: int,
    owner_character_id: Optional[str] = None,
    owner_organization_id: Optional[int] = None,
    metadata: Optional[Dict[str, Any]] = None
) -> WorldObject:
    """Creates a WorldObject. metadata dict is converted to JSON string."""
    obj = WorldObject(
        world_id=world_id,
        object_type=object_type,
        location_id=location_id,
        quantity=quantity,
        owner_character_id=owner_character_id,
        owner_organization_id=owner_organization_id,
        object_metadata=json.dumps(metadata or {})
    )
    session.add(obj)
    session.flush()
    return obj

def transfer_object(
    session: Session,
    world_id: str,
    game_timestamp: int,
    object_id: int,
    quantity: int,
    new_owner_character_id: Optional[str] = None,
    new_owner_organization_id: Optional[int] = None,
    location_id: Optional[int] = None
) -> None:
    """
    Moves quantity of an object.
    Splits rows if partial quantity is moved.
    Emits ITEM_TRANSFERRED.
    """
    obj = session.query(WorldObject).filter_by(id=object_id).one()
    if obj.quantity < quantity:
        raise ValueError("Insufficient object quantity")

    old_owner_char = obj.owner_character_id
    old_owner_org = obj.owner_organization_id
    old_location = obj.location_id

    # Handle splitting
    if obj.quantity > quantity:
        # Create new object for the moved part
        new_obj = WorldObject(
            world_id=world_id,
            object_type=obj.object_type,
            subtype=obj.subtype,
            owner_character_id=new_owner_character_id,
            owner_organization_id=new_owner_organization_id,
            location_id=location_id if location_id is not None else obj.location_id,
            condition=obj.condition,
            quantity=quantity,
            object_metadata=obj.object_metadata
        )
        session.add(new_obj)
        # Subtract from original
        obj.quantity -= quantity
    else:
        # Move the entire object
        obj.owner_character_id = new_owner_character_id
        obj.owner_organization_id = new_owner_organization_id
        if location_id is not None:
            obj.location_id = location_id

    # Emit event
    log_event(
        session, world_id, game_timestamp, EventType.ITEM_TRANSFERRED,
        payload={
            "object_type": obj.object_type,
            "quantity": quantity,
            "from_owner": {"character": old_owner_char, "organization": old_owner_org},
            "to_owner": {
                "character": new_owner_character_id,
                "organization": new_owner_organization_id
            },
            "location": location_id if location_id is not None else old_location
        }
    )
    session.flush()

def consume_object(
    session: Session,
    world_id: str,
    game_timestamp: int,
    object_id: int,
    quantity: int,
    consumer_character_id: str
) -> None:
    """Decrements object quantity (deletes at 0). Emits ITEM_CONSUMED."""
    obj = session.query(WorldObject).filter_by(id=object_id).one()
    if obj.quantity < quantity:
        raise ValueError("Insufficient quantity to consume")

    obj.quantity -= quantity
    if obj.quantity <= 0:
        session.delete(obj)

    log_event(
        session, world_id, game_timestamp, EventType.ITEM_CONSUMED,
        actor_id=consumer_character_id,
        target_id=str(object_id),
        payload={"object_type": obj.object_type, "quantity": quantity}
    )
    session.flush()

def adjust_resource(
    session: Session,
    world_id: str,
    resource_key: str,
    owner_type: str,
    owner_id: str,
    delta: float,
    game_timestamp: int
) -> ResourceBalance:
    """Adjusts a resource balance (e.g., water). Rejects result < 0."""
    res = session.query(ResourceBalance).filter_by(
        world_id=world_id, resource_key=resource_key, owner_type=owner_type, owner_id=owner_id
    ).first()

    if not res:
        # Create if not exists (usually handled by bootstrap for water)
        res = ResourceBalance(
            world_id=world_id,
            resource_key=resource_key,
            owner_type=owner_type,
            owner_id=owner_id,
            quantity=0.0
        )
        session.add(res)

    if res.quantity + delta < 0:
        raise ValueError(f"Resource {resource_key} balance cannot be negative")

    res.quantity += delta
    session.flush()
    return res

def add_supply(
    session: Session,
    world_id: str,
    game_timestamp: int,
    resource_key: str,
    owner_type: str,
    owner_id: str,
    amount: float
) -> None:
    """Adds supply to a resource and emits SUPPLY_ARRIVED."""
    adjust_resource(session, world_id, resource_key, owner_type, owner_id, amount, game_timestamp)

    log_event(
        session, world_id, game_timestamp, EventType.SUPPLY_ARRIVED,
        target_id=owner_id,
        payload={"resource_key": resource_key, "amount": amount}
    )
    session.flush()
