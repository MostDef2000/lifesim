import random

from app.actions.lifecycle import activate, enqueue_task, progress_tick
from app.db.models import Character, Location
from app.events.events import EventType


def test_move_travel_time(session, settings, world_id):
    # character at settlement, MOVE to pier
    from app.characters.generator import generate_population
    rng = random.Random(42)
    generate_population(session, settings, rng, world_id, 20)
    pop = (
        session.query(Character)
        .filter_by(world_id=world_id)
        .order_by(Character.id)
        .all()
    )
    char = pop[0]

    settlement = session.query(Location).filter_by(world_id=world_id, type="settlement").first()
    pier = session.query(Location).filter_by(world_id=world_id, type="pier").first()
    char.location_id = settlement.id
    session.flush()

    # Enqueue MOVE to pier
    from app.world.seed_world import find_path
    path, total_min = find_path(session, world_id, settlement.id, pier.id)

    task = enqueue_task(session, world_id, char, "MOVE", "test",
                       {"path": path, "total_minutes": total_min, "from": settlement.id},
                       0, settings)

    # Activate
    active_task = activate(session, char, 0, settings)

    assert active_task.id == task.id
    assert active_task.ends_at == 0 + total_min

    # Progress tick to completion
    progress_tick(session, world_id, active_task.ends_at, settings)

    # Verify location updated
    session.refresh(char)
    assert char.location_id == pier.id

    # Verify event exists
    from app.db.models import WorldEvent
    event = session.query(WorldEvent).filter_by(
        world_id=world_id,
        event_type=EventType.CHARACTER_MOVED.value
    ).first()
    assert event is not None
    assert event.actor_id == char.id
