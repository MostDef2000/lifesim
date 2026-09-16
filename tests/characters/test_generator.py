import random

from sqlalchemy.orm import Session

from app.characters.generator import generate_population
from app.config.config import Settings
from app.db.models import (
    Account,
    Character,
    CharacterHealth,
    CharacterNeeds,
    CharacterProfile,
    CharacterTrait,
)
from app.economy import get_balance


def test_generator_basic(session: Session, settings: Settings, world_id: str):
    rng = random.Random(42)
    count = 10
    generate_population(session, settings, rng, world_id, count)

    chars = session.query(Character).filter_by(world_id=world_id).all()
    assert len(chars) == count

    for char in chars:
        assert char.age >= 18
        # Check traits range
        traits = session.query(CharacterTrait).filter_by(character_id=char.id).all()
        assert len(traits) == len(settings.population.trait_keys)
        for t in traits:
            assert -100 <= t.value <= 100

        # Check balance
        acc = session.query(Account).filter_by(owner_id=char.id, owner_type="character").one()
        assert get_balance(session, acc.id) == settings.economy.starting_balance

def test_generator_determinism(session: Session, settings: Settings, world_id: str):
    # We need two fresh sessions or databases. Since we use a shared session in fixture,
    # we clear the DB between runs.

    def run_gen():
        rng = random.Random(42)
        generate_population(session, settings, rng, world_id, 5)
        # Collect state
        chars = sorted([c.first_name for c in session.query(Character).all()])
        traits = sorted([t.value for t in session.query(CharacterTrait).all()])
        return chars, traits

    # Run 1
    res1_chars, res1_traits = run_gen()

    # Clear DB (manual delete for test)
    session.query(Character).delete()
    session.query(CharacterProfile).delete()
    session.query(CharacterTrait).delete()
    session.query(CharacterNeeds).delete()
    session.query(CharacterHealth).delete()
    session.query(Account).delete()
    session.commit()

    # Run 2
    res2_chars, res2_traits = run_gen()

    assert res1_chars == res2_chars
    assert res1_traits == res2_traits

def test_generator_housing_capacity(session: Session, settings: Settings, world_id: str):
    rng = random.Random(42)
    count = 5
    generate_population(session, settings, rng, world_id, count)

    # Check that each NPC is in a unique house
    houses = [c.home_location_id for c in session.query(Character).all()]
    assert len(set(houses)) == count

def test_generator_full_name_uniqueness(session: Session, settings: Settings, world_id: str):
    """R3: unique full names — cross-product pairing, no duplicates at 20 NPCs."""
    # The default fixture ships a 1x1 name pool (['A'] x ['B']); give the
    # pairing algorithm a realistic pool to collide against.
    settings.population.first_names = [f"F{i}" for i in range(10)]
    settings.population.last_names = [f"L{i}" for i in range(5)]
    rng = random.Random(42)
    generate_population(session, settings, rng, world_id, 20)

    chars = session.query(Character).filter_by(world_id=world_id).all()
    full_names = [(c.first_name, c.last_name) for c in chars]
    assert len(full_names) == 20
    assert len(full_names) == len(set(full_names))
