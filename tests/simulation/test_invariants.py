
import random

import pytest

from app.db.models import Account, Character, CharacterNeeds, Location, WorldObject


def _populate(session, settings, world_id):
    from app.characters.generator import generate_population

    generate_population(session, settings, random.Random(42), world_id, 20)


def test_invariants_success(session, default_settings, world_id):
    from app.simulation.invariants import run_invariant_checks

    # The `session` fixture already seeded the world (seeded_session);
    # seeding again would duplicate stock and rightly fail conservation.
    _populate(session, default_settings, world_id)
    results = run_invariant_checks(session, world_id, default_settings)
    assert all(res["ok"] for res in results)


def test_negative_balance_fails(session, default_settings, world_id):
    from app.simulation.invariants import run_invariant_checks
    # Force a negative balance
    account = session.query(Account).filter(Account.world_id == world_id).first()
    account.balance = -100
    session.commit()

    results = run_invariant_checks(session, world_id, default_settings)
    bal_res = next(res for res in results if res["name"] == "no_negative_balances")
    assert not bal_res["ok"]

def test_impossible_needs_fails(session, default_settings, world_id):
    from app.simulation.invariants import run_invariant_checks

    _populate(session, default_settings, world_id)
    # Force hunger = 150
    needs = session.query(CharacterNeeds).filter(CharacterNeeds.character_id.in_(
        session.query(Character.id).filter(Character.world_id == world_id)
    )).first()
    if needs is None:
        pytest.fail("No CharacterNeeds found for given world_id")
    needs.hunger = 150.0
    session.commit()

    results = run_invariant_checks(session, world_id, default_settings)
    state_res = next(res for res in results if res["name"] == "no_impossible_states")
    assert not state_res["ok"]

def test_orphan_location_fails(session, default_settings, world_id):
    """A character pointing at a non-existent location must fail referential integrity."""
    from app.db.models import Character
    from app.simulation.invariants import run_invariant_checks

    _populate(session, default_settings, world_id)
    char = session.query(Character).filter(
        Character.world_id == world_id
    ).first()
    char.location_id = 999999
    session.commit()

    results = run_invariant_checks(session, world_id, default_settings)
    state_res = next(
        res for res in results if res["name"] == "no_impossible_states"
    )
    assert not state_res["ok"]


def test_object_conservation_fails_on_unaccounted_creation(session, default_settings, world_id):
    """Creating objects outside the accounted sources must fail conservation."""
    from app.simulation.invariants import run_invariant_checks

    _populate(session, default_settings, world_id)
    loc = session.query(Location).filter_by(world_id=world_id).first()
    session.add(WorldObject(
        world_id=world_id,
        object_type="food_bread",
        location_id=loc.id,
        quantity=999,
        object_metadata="{}",
    ))
    session.commit()

    results = run_invariant_checks(session, world_id, default_settings)
    cons_res = next(
        res for res in results if res["name"] == "object_conservation"
    )
    assert not cons_res["ok"]
