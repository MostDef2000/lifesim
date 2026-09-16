import random

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.characters.generator import generate_population
from app.db.models import CharacterNeeds, WorldEvent, bootstrap
from app.simulation.engine import Engine, TickScheduler, WorldClock


@pytest.fixture
def sim(default_settings, world_id):
    engine = create_engine("sqlite:///:memory:")
    bootstrap(engine, default_settings, 42)
    session = sessionmaker(bind=engine)()
    from app.world.seed_world import seed_world

    seed_world(session, default_settings, world_id)
    rng = random.Random(42)
    generate_population(session, default_settings, rng, world_id, 20)
    clock = WorldClock(initial_timestamp=0)
    engine_obj = Engine(clock, TickScheduler())
    yield default_settings, session, engine_obj, world_id
    session.close()


def test_engine_step_advances_clock(sim):
    settings, session, engine, world_id = sim
    engine.step(minutes=1, session=session, world_id=world_id, settings=settings)
    assert engine.clock.timestamp == 1
    engine.step(minutes=59, session=session, world_id=world_id, settings=settings)
    assert engine.clock.timestamp == 60


def test_engine_needs_decay_over_minutes(sim):
    settings, session, engine, world_id = sim
    needs = session.query(CharacterNeeds).order_by(CharacterNeeds.character_id).first()
    start = needs.hunger

    engine.step(minutes=10, session=session, world_id=world_id, settings=settings)
    session.expire_all()
    needs = session.query(CharacterNeeds).order_by(CharacterNeeds.character_id).first()
    expected_decay = settings.needs.decay_rates.hunger * 10
    assert needs.hunger == pytest.approx(start - expected_decay)


def test_engine_day_rollover_triggers_single_supply(sim):
    settings, session, engine, world_id = sim
    # Step to exactly minute 1440 (end of day 1)
    engine.step(minutes=1440, session=session, world_id=world_id, settings=settings)
    assert engine.clock.timestamp == 1440

    events = session.query(WorldEvent).filter_by(
        world_id=world_id,
        event_type="SUPPLY_ARRIVED",
        game_timestamp=1440,
    ).all()
    assert len(events) == 1

    # No duplicate when stepping into day 2
    engine.step(minutes=1, session=session, world_id=world_id, settings=settings)
    events = session.query(WorldEvent).filter_by(
        world_id=world_id,
        event_type="SUPPLY_ARRIVED",
        game_timestamp=1440,
    ).all()
    assert len(events) == 1


def test_engine_progress_tick_runs_tasks(sim):
    settings, session, engine, world_id = sim
    # After a few minutes, characters should have created tasks via utility AI
    engine.step(minutes=5, session=session, world_id=world_id, settings=settings)
    from app.db.models import CharacterTask

    tasks = session.query(CharacterTask).filter_by(world_id=None).all() \
        if hasattr(CharacterTask, "world_id") else session.query(CharacterTask).all()
    # 20 characters made at least one decision (task rows exist)
    assert len(tasks) >= 20


def test_engine_commit_interval_no_crash(sim):
    settings, session, engine, world_id = sim
    # Step past one commit interval (60 minutes) — must not raise
    engine.step(minutes=61, session=session, world_id=world_id, settings=settings)
    assert engine.clock.timestamp == 61
