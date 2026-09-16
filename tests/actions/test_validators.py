import random

from app.actions.lifecycle import enqueue_task
from app.actions.utility import choose_action
from app.db.models import Character, CharacterTask, Location, ResourceBalance, WorldObject
from app.inventory import adjust_resource, create_object


def test_validator_sleep(session, settings, world_id):
    # Setup character at home
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
    char.home_location_id = char.location_id
    session.flush()

    # Happy path: Sleep at home, energy < 100
    from app.db.models import CharacterNeeds
    needs = session.query(CharacterNeeds).filter_by(character_id=char.id).one()
    needs.energy = 50
    session.flush()

    # Test SLEEP via choose_action (if energy is low, SLEEP should be high utility)
    # Force other needs high to isolate energy
    needs.hunger = 100
    needs.thirst = 100
    session.flush()

    # If energy is critical, it should be a candidate
    needs.energy = 5
    session.flush()
    action, params, move_id = choose_action(session, world_id, char, 0, settings)
    assert action in ["SLEEP", "EAT", "DRINK"]

    # Reject: Energy == 100 -> SLEEP should not be chosen over other critical needs
    needs.energy = 100
    needs.hunger = 5
    session.flush()
    action, params, move_id = choose_action(session, world_id, char, 0, settings)
    assert action != "SLEEP"

def test_validator_eat(session, settings, world_id):
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

    # Case 1: Kitchen stocked, character needs food
    kitchen_loc = session.query(Location).filter_by(world_id=world_id, type="kitchen").first()

    from app.db.models import CharacterNeeds
    needs = session.query(CharacterNeeds).filter_by(character_id=char.id).one()
    needs.hunger = 10
    needs.thirst = 100
    needs.energy = 100
    session.flush()

    action, params, move_id = choose_action(session, world_id, char, 0, settings)
    assert action == "EAT"
    if char.location_id != kitchen_loc.id:
        assert move_id == kitchen_loc.id
    else:
        assert move_id is None

    # Case 2: No food in kitchen
    session.query(WorldObject).filter_by(location_id=kitchen_loc.id).delete()
    session.flush()
    action, params, move_id = choose_action(session, world_id, char, 0, settings)
    assert action != "EAT"

def test_validator_drink(session, settings, world_id):
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

    # Water > 0
    adjust_resource(session, world_id, "water", "organization", "1", 100, 0)
    session.flush()

    from app.db.models import CharacterNeeds
    needs = session.query(CharacterNeeds).filter_by(character_id=char.id).one()
    needs.thirst = 10
    needs.hunger = 100
    needs.energy = 100
    session.flush()

    action, params, move_id = choose_action(session, world_id, char, 0, settings)
    assert action == "DRINK"

    # Water == 0
    session.query(ResourceBalance).filter_by(resource_key="water").update({"quantity": 0})
    session.flush()
    action, params, move_id = choose_action(session, world_id, char, 0, settings)
    assert action != "DRINK"

def test_validator_work(session, settings, world_id):
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

    # (a) Character HAS job, other needs satisfied -> WORK chosen
    from app.db.models import CharacterNeeds
    needs = session.query(CharacterNeeds).filter_by(character_id=char.id).one()
    needs.hunger = 100
    needs.thirst = 100
    needs.energy = 100
    session.flush()

    action, params, move_id = choose_action(session, world_id, char, 0, settings)
    assert action == "WORK"

    # (b) Delete CharacterJob rows for char -> WORK not chosen
    from app.db.models import CharacterJob
    session.query(CharacterJob).filter_by(character_id=char.id).delete()
    session.flush()

    action, params, move_id = choose_action(session, world_id, char, 0, settings)
    assert action != "WORK"

def test_validator_buy_item(session, settings, world_id):
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

    shop_loc = session.query(Location).filter_by(world_id=world_id, type="shop").first()
    create_object(session, world_id, "food_bread", shop_loc.id, 10)
    session.flush()

    from app.db.models import Account
    acc = session.query(Account).filter_by(owner_type="character", owner_id=char.id).one()
    acc.balance = 1000
    session.flush()

    from app.db.models import CharacterNeeds
    needs = session.query(CharacterNeeds).filter_by(character_id=char.id).one()
    needs.hunger = 10
    needs.thirst = 100
    needs.energy = 100
    session.flush()

    # Kitchen empty
    session.query(WorldObject).filter(WorldObject.location_id != shop_loc.id).delete()
    session.flush()

    action, params, move_id = choose_action(session, world_id, char, 0, settings)
    assert action == "BUY_ITEM"
    assert move_id == shop_loc.id

    # Poor
    acc.balance = 0
    session.flush()
    action, params, move_id = choose_action(session, world_id, char, 0, settings)
    assert action != "BUY_ITEM"

    # No stock
    acc.balance = 1000
    session.flush()
    session.query(WorldObject).filter_by(object_type="food_bread").delete()
    session.flush()
    action, params, move_id = choose_action(session, world_id, char, 0, settings)
    assert action != "BUY_ITEM"

def test_validator_move_idle(session, settings, world_id):
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

    task_move = enqueue_task(
        session, world_id, char, "MOVE", "test",
        {"path": [1, 2], "total_minutes": 10}, 0, settings
    )
    assert task_move is not None

    # Second enqueue (IDLE while MOVE planned) IS allowed
    task_idle = enqueue_task(session, world_id, char, "IDLE", "test", {}, 0, settings)
    assert task_idle is not None

    # Assert the character now has 2 planned tasks
    planned_count = (
        session.query(CharacterTask)
        .filter_by(character_id=char.id, status="planned")
        .count()
    )
    assert planned_count == 2

def test_enqueue_rejects_active(session, settings, world_id):
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

    from app.db.models import CharacterTask
    active = CharacterTask(
        character_id=char.id, priority=0, task_type="IDLE",
        status="active", source="test", parameters="{}", created_at=0
    )
    session.add(active)
    session.flush()

    res = enqueue_task(session, world_id, char, "EAT", "test", {}, 0, settings)
    assert res is None
