
from app.events.events import EventType, get_events, log_event


def test_event_generation(db_session):
    # Setup for the test session if needed, but fixture provides it.
    # Need to ensure World exists for FK
    from app.db.models import World
    # Use a unique world ID to avoid IntegrityError if fixture already bootstrapped
    world_id = "test_world_gen"
    world = World(id=world_id, seed=42, start_real="now", config_sha256="sha")
    db_session.add(world)
    db_session.commit()
    # ... rest of test


    payload = {"detail": "test"}
    event_id = log_event(
        db_session, "test_world", 10,
        EventType.CHARACTER_CREATED,
        actor_id="npc_1",
        payload=payload
    )

    assert event_id is not None

    events = get_events(db_session, "test_world")
    assert len(events) == 1
    assert events[0].event_type == EventType.CHARACTER_CREATED.value
    assert events[0].actor_id == "npc_1"

    import json
    assert json.loads(events[0].payload) == payload

def test_event_monotone_id(db_session):
    from app.db.models import World
    # Use a unique world ID to avoid IntegrityError if fixture already bootstrapped
    world_id = "test_world_mono"
    world = World(id=world_id, seed=42, start_real="now", config_sha256="sha")
    db_session.add(world)
    db_session.commit()
    # ... rest of test


    id1 = log_event(db_session, "test_world", 10, EventType.CHARACTER_CREATED)
    id2 = log_event(db_session, "test_world", 11, EventType.CHARACTER_MOVED)
    assert id2 > id1
