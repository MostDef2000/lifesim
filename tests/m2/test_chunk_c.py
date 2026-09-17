import random

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.characters.generator import generate_population
from app.config.config import load_config
from app.db.models import bootstrap
from app.simulation.invariants import run_invariant_checks
from app.world.seed_world import seed_world
from app.world.social_seed import seed_social


def test_social_invariants_vacuously_pass():
    # Case: social.enabled = False. Invariants should pass trivially.
    settings = load_config("config/default.yaml")
    settings.social.enabled = False

    engine = create_engine("sqlite:///:memory:")
    with Session(engine) as session:
        bootstrap(engine, settings, 42)
        world_id = settings.world.world_id
        seed_world(session, settings, world_id)

        rng = random.Random(42)
        generate_population(session, settings, rng, world_id, 10)

        # No seed_social here because social is disabled

        results = run_invariant_checks(session, world_id, settings)
        social_invariants = [
            "relationship_range", "social_event_integrity",
            "relationship_event_link", "org_membership_integrity"
        ]
        for res in results:
            if res["name"] in social_invariants:
                assert res["ok"], f"Invariant {res['name']} failed vacuously: {res['details']}"

def test_social_invariants_with_data():
    # Case: social.enabled = True. Data is present.
    settings = load_config("config/default.yaml")
    settings.social.enabled = True

    engine = create_engine("sqlite:///:memory:")
    with Session(engine) as session:
        bootstrap(engine, settings, 42)
        world_id = settings.world.world_id
        seed_world(session, settings, world_id)

        rng = random.Random(42)
        generate_population(session, settings, rng, world_id, 10)
        seed_social(session, settings, world_id, rng)

        results = run_invariant_checks(session, world_id, settings)
        social_invariants = [
            "relationship_range", "social_event_integrity",
            "relationship_event_link", "org_membership_integrity"
        ]
        for res in results:
            if res["name"] in social_invariants:
                assert res["ok"], f"Invariant {res['name']} failed with data: {res['details']}"
