"""#197: NPC first names must come from the config pool matching the drawn sex.

Prod evidence: npc_0001 "Elena Kuznetsov" carried sex=M with a portrait prompt
"63-year-old M" — the generator drew the name and the sex independently.
"""
import random

from sqlalchemy.orm import Session

from app.characters.generator import generate_population
from app.config.config import Settings, load_config
from app.db.models import (
    Account,
    Character,
    CharacterHealth,
    CharacterNeeds,
    CharacterProfile,
    CharacterTrait,
)

# Mirrors config/production.yaml (post-split). NOTE: hand-copied constants —
# renaming a pool name in YAML requires updating these pins (no auto-drift check).
FIRST_NAMES_M = ["Ivan", "Dmitry", "Sergei", "Alexei", "Pavel"]
FIRST_NAMES_F = ["Anna", "Elena", "Olga", "Maria", "Natalia"]


def _roster(session: Session, world_id: str):
    return (
        session.query(Character)
        .filter_by(world_id=world_id)
        .order_by(Character.id)
        .all()
    )


def test_first_name_matches_sex(session: Session, settings: Settings, world_id: str):
    """Every NPC's first name is drawn from the pool matching its sex."""
    settings.population.first_names_m = list(FIRST_NAMES_M)
    settings.population.first_names_f = list(FIRST_NAMES_F)
    rng = random.Random(42)
    generate_population(session, settings, rng, world_id, 16)

    chars = _roster(session, world_id)
    assert len(chars) == 16
    for c in chars:
        pool = FIRST_NAMES_M if c.sex == "M" else FIRST_NAMES_F
        assert c.first_name in pool, (
            f"{c.id}: first name {c.first_name!r} does not match sex {c.sex!r}"
        )
    # Sanity: the deterministic seed exercises both sexes, so the pin above
    # is not vacuous.
    assert {c.sex for c in chars} == {"M", "F"}


def test_same_seed_same_roster(session: Session, settings: Settings, world_id: str):
    """Same seed -> identical roster (first+last names and sexes), in order."""
    settings.population.first_names_m = list(FIRST_NAMES_M)
    settings.population.first_names_f = list(FIRST_NAMES_F)

    def run_gen():
        rng = random.Random(197)
        generate_population(session, settings, rng, world_id, 16)
        return [(c.first_name, c.last_name, c.sex) for c in _roster(session, world_id)]

    roster1 = run_gen()

    # Clear DB between runs (same pattern as tests/characters/test_generator.py).
    session.query(Character).delete()
    session.query(CharacterProfile).delete()
    session.query(CharacterTrait).delete()
    session.query(CharacterNeeds).delete()
    session.query(CharacterHealth).delete()
    session.query(Account).delete()
    session.commit()

    roster2 = run_gen()
    assert roster1 == roster2


def test_full_names_unique_across_union(session: Session, settings: Settings, world_id: str):
    """Regression: two-pool pairing keeps full names unique across the UNION
    of the m/f pools (no cross-pool collisions at 20 NPCs)."""
    settings.population.first_names_m = list(FIRST_NAMES_M)
    settings.population.first_names_f = list(FIRST_NAMES_F)
    settings.population.last_names = [f"L{i}" for i in range(5)]
    rng = random.Random(42)
    generate_population(session, settings, rng, world_id, 20)

    chars = _roster(session, world_id)
    full_names = [(c.first_name, c.last_name) for c in chars]
    assert len(full_names) == 20
    assert len(full_names) == len(set(full_names))


def test_legacy_first_names_key_still_loads(default_settings: Settings, tmp_path):
    """Regression pin (#197): a config still carrying the legacy single mixed
    `first_names` key must not break config loading — pydantic ignores the
    unknown key; the split pools are authoritative."""
    import yaml

    data = default_settings.model_dump()
    data["population"]["first_names"] = ["Ivan", "Anna"]  # legacy mixed key
    data["population"]["first_names_m"] = ["Ivan"]
    data["population"]["first_names_f"] = ["Anna"]
    config_file = tmp_path / "legacy_first_names.yaml"
    config_file.write_text(yaml.safe_dump(data))

    settings = load_config(str(config_file))
    assert settings.population.first_names_m == ["Ivan"]
    assert settings.population.first_names_f == ["Anna"]
