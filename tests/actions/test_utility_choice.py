import random

from app.actions.lifecycle import activate, enqueue_task, progress_tick
from app.actions.utility import choose_action
from app.db.models import Character, CharacterNeeds, CharacterTask, Location, WorldObject
from app.events.events import EventType


def test_utility_starving_kitchen(session, settings, world_id):
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

    # Kitchen stocked (already seeded)
    kitchen_loc = session.query(Location).filter_by(world_id=world_id, type="kitchen").first()

    # Starving
    needs = session.query(CharacterNeeds).filter_by(character_id=char.id).one()
    needs.hunger = 0
    needs.thirst = 100
    needs.energy = 100
    session.flush()

    # (a) choose_action returns EAT with needs_move=kitchen
    action, params, move_id = choose_action(session, world_id, char, 0, settings)
    assert action == "EAT"
    if char.location_id != kitchen_loc.id:
        assert move_id == kitchen_loc.id

    # Simulate MOVE then EAT
    from app.world.seed_world import find_path
    path, total_min = find_path(session, world_id, char.location_id, kitchen_loc.id)

    ts = 0
    # Move
    enqueue_task(session, world_id, char, "MOVE", "need",
                           {"path": path, "total_minutes": total_min, "from": char.location_id},
                           ts, settings)
    activate(session, char, ts, settings)
    ts += total_min
    progress_tick(session, world_id, ts, settings)

    # Now at kitchen, EAT
    food_obj = (
        session.query(WorldObject)
        .filter_by(location_id=kitchen_loc.id, object_type="food_bread")
        .first()
    )
    enqueue_task(
        session, world_id, char, "EAT", "need",
        {"object_id": food_obj.id}, ts, settings
    )
    activate(session, char, ts, settings)
    # Gradual restore: recovery is applied per game-minute, so tick
    # minute-by-minute through the EAT duration (fixture: 1.0/min x 15 min).
    for _ in range(settings.actions.EAT.duration_minutes):
        ts += 1
        progress_tick(session, world_id, ts, settings)

    # Check hunger restored
    session.refresh(needs)
    assert needs.hunger == 15.0

def test_utility_rich_shop_empty_kitchen(session, settings, world_id):
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

    # Shop stocked, Kitchen empty
    shop_loc = session.query(Location).filter_by(world_id=world_id, type="shop").first()
    kitchen_loc = session.query(Location).filter_by(world_id=world_id, type="kitchen").first()
    session.query(WorldObject).filter_by(location_id=kitchen_loc.id).delete()
    session.flush()

    # Rich
    from app.db.models import Account
    acc = session.query(Account).filter_by(owner_type="character", owner_id=char.id).one()
    acc.balance = 1000
    session.flush()

    # Hungry
    needs = session.query(CharacterNeeds).filter_by(character_id=char.id).one()
    needs.hunger = 10
    session.flush()

    # (b) BUY_ITEM chosen
    action, params, move_id = choose_action(session, world_id, char, 0, settings)
    assert action == "BUY_ITEM"
    assert move_id == shop_loc.id

    # Drive MOVE -> BUY_ITEM
    from app.world.seed_world import find_path
    path, total_min = find_path(session, world_id, char.location_id, shop_loc.id)

    ts = 0
    enqueue_task(
        session, world_id, char, "MOVE", "need",
        {"path": path, "total_minutes": total_min, "from": char.location_id},
        ts, settings
    )
    activate(session, char, ts, settings)
    ts += total_min
    progress_tick(session, world_id, ts, settings)

    # Buy item
    food_obj = (
        session.query(WorldObject)
        .filter_by(location_id=shop_loc.id, object_type="food_bread")
        .first()
    )
    price = settings.economy.prices["food_bread"]
    enqueue_task(
        session, world_id, char, "BUY_ITEM", "need",
        {"object_id": food_obj.id, "price": price}, ts, settings
    )
    activate(session, char, ts, settings)
    ts += settings.actions.BUY_ITEM.duration_minutes
    progress_tick(session, world_id, ts, settings)

    # Assertions
    session.refresh(acc)
    assert acc.balance == 1000 - price

    # Char owns the purchased item (initial 'item1' also exists)
    obj = (
        session.query(WorldObject)
        .filter_by(owner_character_id=char.id, object_type="food_bread")
        .first()
    )
    assert obj is not None

    # Events
    from app.db.models import WorldEvent
    events = session.query(WorldEvent).filter(WorldEvent.event_type.in_([
        EventType.PURCHASE.value,
        EventType.ITEM_TRANSFERRED.value,
        EventType.TASK_COMPLETED.value
    ])).all()
    assert len(events) >= 3

def test_idle_interrupt(session, settings, world_id):
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

    # Active IDLE task
    task = enqueue_task(session, world_id, char, "IDLE", "test", {}, 0, settings)
    activate(session, char, 0, settings)
    # Set ends_at far in the future
    task.ends_at = 1000
    session.flush()

    # Hunger critical
    needs = session.query(CharacterNeeds).filter_by(character_id=char.id).one()
    needs.hunger = 5
    session.flush()

    # Kitchen stocked (seeded)

    # progress_tick at ts=1 should interrupt
    progress_tick(session, world_id, 1, settings)

    # IDLE task failed
    session.refresh(task)
    assert task.status == "failed"
    # Reason should be "interrupted"
    from app.db.models import WorldEvent
    event = session.query(WorldEvent).filter_by(event_type=EventType.TASK_FAILED.value).first()
    import json
    payload = json.loads(event.payload)
    assert payload["reason"] == "interrupted"

    # Non-IDLE planned task exists (EAT or MOVE to kitchen)
    new_task = (
        session.query(CharacterTask)
        .filter_by(character_id=char.id, status="planned")
        .first()
    )
    assert new_task is not None
    assert new_task.task_type != "IDLE"
