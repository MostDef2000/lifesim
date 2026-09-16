import pytest

from app.db.models import WorldObject
from app.events.events import EventType, get_events
from app.inventory import (
    add_supply,
    adjust_resource,
    consume_object,
    create_object,
    transfer_object,
)


def test_create_transfer_consume(db_session, default_settings):
    world_id = default_settings.world.world_id

    # 1. Create
    obj = create_object(db_session, world_id, "food_bread", 10, 10, owner_character_id="char_1")
    assert obj.quantity == 10

    # 2. Transfer partial
    transfer_object(
        db_session, world_id, 10, obj.id, 4, new_owner_character_id="char_2", location_id=11
    )

    # Verify split
    objs = db_session.query(WorldObject).filter_by(object_type="food_bread").all()
    assert len(objs) == 2
    # Sum of quantity should be conserved
    assert sum(o.quantity for o in objs) == 10

    # 3. Consume
    # Find the object owned by char_2
    obj_char2 = next(o for o in objs if o.owner_character_id == "char_2")
    consume_object(db_session, world_id, 20, obj_char2.id, 2, "char_2")

    objs_after = db_session.query(WorldObject).filter_by(object_type="food_bread").all()
    assert sum(o.quantity for o in objs_after) == 8

def test_water_resource_conservation(db_session, default_settings):
    world_id = default_settings.world.world_id
    org_id = "org_1"

    # Start with 100
    res = adjust_resource(db_session, world_id, "water", "organization", org_id, 100, 0)
    assert res.quantity == 100

    # Consume 30
    adjust_resource(db_session, world_id, "water", "organization", org_id, -30, 10)
    assert res.quantity == 70

    # Reject negative result
    with pytest.raises(ValueError, match="balance cannot be negative"):
        adjust_resource(db_session, world_id, "water", "organization", org_id, -80, 20)

def test_supply_arrived_emission(db_session, default_settings):
    world_id = default_settings.world.world_id
    org_id = "org_1"

    add_supply(db_session, world_id, 100, "water", "organization", org_id, 50.0)

    events = get_events(db_session, world_id, EventType.SUPPLY_ARRIVED)
    assert len(events) == 1
    import json
    payload = json.loads(events[0].payload)
    assert payload["resource_key"] == "water"
    assert payload["amount"] == 50.0
