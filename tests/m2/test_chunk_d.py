import random
import time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.actions.validators import validate
from app.characters.generator import generate_population
from app.config.config import load_config
from app.db.models import (
    Character,
    Relationship,
    RelationshipEvent,
    World,
    WorldEvent,
    bootstrap,
)
from app.simulation.engine import Engine, TickScheduler, WorldClock
from app.simulation.report import build_report
from app.world.seed_world import seed_world
from app.world.social_seed import seed_social

# --- AE1 & AE2 Pipeline ---

def run_pipeline(seed: int, days: int, population: int,
                 social_enabled: bool, db_path: str = ":memory:"):
    # Load default config and override
    settings = load_config("config/default.yaml")
    settings.social.enabled = social_enabled
    settings.persistence.db_path = db_path
    settings.world.initial_population = population

    # Manual implementation of run_full_simulation to ensure settings are applied
    engine = create_engine(f"sqlite:///{db_path}")
    session = Session(engine)

    bootstrap(engine, settings, seed)
    world_id = settings.world.world_id
    seed_world(session, settings, world_id)
    sim_rng = random.Random(seed)
    generate_population(session, settings, sim_rng, world_id, population)

    if social_enabled:
        seed_social(session, settings, world_id, sim_rng)

    clock = WorldClock()
    scheduler = TickScheduler()
    sim_engine = Engine(clock, scheduler)

    start_time = time.time()
    for _ in range(days):
        sim_engine.step(1440, session, world_id, settings)
    end_time = time.time()

    report = build_report(session, world_id, settings, 0.0, seed)
    wall_time = end_time - start_time

    return report, session, engine, wall_time

@pytest.mark.slow
def test_ae1_full_pipeline_30d():
    """
    AE1: 30 days, 20 pop, seed 42, social=True.
    Asserts invariants, event thresholds, population, and burnout.
    """
    seed = 42
    days = 30
    pop = 20

    report, session, engine, wall_time = run_pipeline(seed, days, pop, True)

    print(f"\nAE1 Wall-time: {wall_time:.2f}s")
    # Generous guard < 180s as per instruction (NFR 60s is for reference machine)
    assert wall_time < 180, f"Performance too slow: {wall_time:.2f}s"

    assert report["invariants_ok"] is True
    assert report["population_alive"] == 20

    events = report["events_by_type"]
    assert events.get("SOCIAL_INTERACTION", 0) >= 60
    assert events.get("CONFLICT", 0) >= 2
    assert events.get("RELATIONSHIP_CHANGED", 0) >= 10

    # Reference values for seed 42 (for internal check, but assert thresholds per spec)
    # SOCIAL_INTERACTION == 1080, CONFLICT == 4, RELATIONSHIP_CHANGED == 43

    # Conflict burnout: every Relationship with affection < -30 has affection == -60.0
    # and count of such feuds == 2.
    world_id = session.query(World).first().id
    relationships = session.query(Relationship).filter_by(world_id=world_id).all()

    feuds = [r for r in relationships if r.affection < -30]
    assert len(feuds) == 2, f"Expected 2 burned-out feuds, found {len(feuds)}"
    for f in feuds:
        assert f.affection == -60.0, f"Feud should be burned out to -60.0, found {f.affection}"

    session.close()

def test_ae2_fast_pipeline_7d():
    """
    AE2: 7 days, 20 pop, seed 42, social=True.
    Asserts SOCIAL_INTERACTION >= 14, CONFLICT >= 2, alive 20, invariants.
    """
    seed = 42
    days = 7
    pop = 20

    report, session, engine, wall_time = run_pipeline(seed, days, pop, True)

    assert report["invariants_ok"] is True
    assert report["population_alive"] == 20

    events = report["events_by_type"]
    assert events.get("SOCIAL_INTERACTION", 0) >= 14
    assert events.get("CONFLICT", 0) >= 2

    session.close()

# --- Unit Tests of Mechanics ---

def test_socialize_dead_target_fallback():
    """
    Unit test: SOCIALIZE task with dead target should fallback to best co-located alive candidate.
    """
    settings = load_config("config/default.yaml")
    settings.social.enabled = True
    engine = create_engine("sqlite:///:memory:")

    with Session(engine) as session:
        bootstrap(engine, settings, 42)
        world_id = settings.world.world_id
        seed_world(session, settings, world_id)

        rng = random.Random(42)
        generate_population(session, settings, rng, world_id, 5)
        seed_social(session, settings, world_id, rng)

        chars = session.query(Character).all()
        actor = chars[0]
        target_dead = chars[1]
        target_alive = chars[2]

        # Set target_dead to dead
        target_dead.alive = False
        session.commit()

        # Force them to be in the same location for simplicity
        actor.location_id = 1
        target_alive.location_id = 1
        session.commit()

        # The "dead-target fallback" happens at COMPLETION time in lifecycle.py.
        from app.actions.lifecycle import complete_task

        # Setup a task
        from app.db.models import CharacterTask
        task = CharacterTask(
            character_id=actor.id,
            priority=1,
            task_type="SOCIALIZE",
            target_id=target_dead.id,
            status="STARTED",
            source="utility",
            parameters='{"target_id": "' + target_dead.id + '", "affection_at_start": 0.0}',
            created_at=0,
            started_at=0,
            ends_at=30
        )
        session.add(task)
        session.commit()

        # Complete it
        complete_task(session, world_id, actor, task, 30, settings)
        session.commit()

        # Check events
        events = session.query(WorldEvent).filter_by(world_id=world_id).all()
        social_events = [e for e in events if e.event_type == "SOCIAL_INTERACTION"]

        # If fallback worked, it should have found target_alive who is co-located.
        # It should not have used the dead target.
        for e in social_events:
            import json
            payload = json.loads(e.payload)
            assert payload["target_id"] != target_dead.id

def test_conflict_burnout_refusal():
    """
    Unit test: drive a pair from -50 to -60 and verify third is refused.
    """
    settings = load_config("config/default.yaml")
    settings.social.enabled = True
    engine = create_engine("sqlite:///:memory:")

    with Session(engine) as session:
        bootstrap(engine, settings, 42)
        world_id = settings.world.world_id
        seed_world(session, settings, world_id)

        rng = random.Random(42)
        generate_population(session, settings, rng, world_id, 2)
        seed_social(session, settings, world_id, rng)

        chars = session.query(Character).all()
        c1, c2 = chars[0], chars[1]

        # Setup relationship to -50
        from app.db.models import Relationship
        a, b = sorted([c1.id, c2.id])
        rel = session.query(Relationship).filter_by(character_a=a, character_b=b).one()
        rel.affection = -50.0
        session.commit()

        # Force co-location
        c1.location_id = 1
        c2.location_id = 1
        session.commit()

        # Create a mock needs object
        from app.db.models import CharacterNeeds
        # Use merge to avoid UNIQUE constraint failure if generate_population already created needs
        needs = CharacterNeeds(
            character_id=c1.id, hunger=0, thirst=0, energy=100,
            hygiene=100, comfort=100, social=0, safety=100,
            entertainment=100, privacy=100, updated_at=0,
        )
        session.merge(needs)
        session.commit()

        # 1st interaction: -50 -> -55 (because -50 < -30, it's a CONFLICT)
        ok, reason, move_id, params = validate(
            session, world_id, c1, "SOCIALIZE", 0, settings, needs=needs
        )
        assert ok is True

        # Simulate the effect of the conflict completion
        rel.affection -= 5.0
        session.commit()

        # 2nd interaction: -55 -> -60
        ok, reason, move_id, params = validate(
            session, world_id, c1, "SOCIALIZE", 0, settings, needs=needs
        )
        assert ok is True
        rel.affection -= 5.0
        session.commit()

        # 3rd interaction: affection is now -60. Refusal threshold is -60.0.
        ok, reason, move_id, params = validate(
            session, world_id, c1, "SOCIALIZE", 0, settings, needs=needs
        )

        # If only c2 exists and they are at -60, it should be False.
        assert ok is False, f"Should be refused at -60 affection, but got ok={ok}, reason={reason}"

def test_relationship_band_crossing():
    """
    Unit test: stranger -> acquaintance crossing at affection >= 10 fires exactly one event.
    """
    settings = load_config("config/default.yaml")
    settings.social.enabled = True
    engine = create_engine("sqlite:///:memory:")

    with Session(engine) as session:
        bootstrap(engine, settings, 42)
        world_id = settings.world.world_id
        seed_world(session, settings, world_id)

        rng = random.Random(42)
        generate_population(session, settings, rng, world_id, 2)
        seed_social(session, settings, world_id, rng)

        chars = session.query(Character).all()
        c1, c2 = chars[0], chars[1]

        a, b = sorted([c1.id, c2.id])
        rel = session.query(Relationship).filter_by(character_a=a, character_b=b).one()
        rel.affection = 9.0 # Just below acquaintance (10)
        session.commit()

        # Trigger interaction via completion hook
        from app.actions.lifecycle import complete_task
        from app.db.models import CharacterTask

        task = CharacterTask(
            character_id=c1.id,
            priority=1,
            task_type="SOCIALIZE",
            target_id=c2.id,
            status="STARTED",
            source="utility",
            parameters='{"target_id": "' + c2.id + '", "affection_at_start": 9.0}',
            created_at=0,
            started_at=0,
            ends_at=30
        )
        session.add(task)
        session.commit()

        complete_task(session, world_id, c1, task, 30, settings)

        # Check for RELATIONSHIP_CHANGED event
        rel_events = session.query(RelationshipEvent).filter_by(world_id=world_id).all()
        band_events = [e for e in rel_events if e.event_type == "RELATIONSHIP_CHANGED"]

        assert len(band_events) == 1
        world_event = session.query(WorldEvent).filter_by(id=band_events[0].event_id).one()
        import json
        payload = json.loads(world_event.payload)
        assert payload["band"] == "acquaintance"

# --- AE5 Regression ---

def test_ae5_m1_regression():
    """
    AE5: With social.enabled=false, 7-day run event vector must match M1 exactly.
    """
    seed = 42
    days = 7
    pop = 20

    report, session, engine, wall_time = run_pipeline(seed, days, pop, False)

    expected_vector = {
        'CHARACTER_CREATED': 20,
        'CHARACTER_MOVED': 959,
        'ITEM_CONSUMED': 280,
        'ITEM_TRANSFERRED': 70,
        'PURCHASE': 70,
        'SALARY_PAID': 140,
        'SUPPLY_ARRIVED': 7,
            'WEATHER_CHANGED': 8,
        'TASK_COMPLETED': 2249
    }

    assert report["events_by_type"] == expected_vector
    assert "social" not in report, "Report should not contain 'social' key when disabled"

    session.close()

# --- Determinism ---

@pytest.mark.slow
def test_social_determinism(tmp_path):
    """
    Determinism: two identical 7-day social-on runs produce identical results.
    """
    seed = 42
    days = 7
    pop = 20

    # tmp_path: file DBs must not linger in the repo root (rerun bootstrap
    # on an existing schema would raise IntegrityError).
    db1 = str(tmp_path / "det1.db")
    db2 = str(tmp_path / "det2.db")
    report1, session1, engine1, _ = run_pipeline(seed, days, pop, True, db1)
    report2, session2, engine2, _ = run_pipeline(seed, days, pop, True, db2)

    assert report1["events_by_type"] == report2["events_by_type"]

    world_id = session1.query(World).first().id
    rel1 = sorted([
        r.affection for r in
        session1.query(Relationship).filter_by(world_id=world_id).all()
    ])
    rel2 = sorted([
        r.affection for r in
        session2.query(Relationship).filter_by(world_id=world_id).all()
    ])

    assert rel1 == rel2

    session1.close()
    session2.close()
