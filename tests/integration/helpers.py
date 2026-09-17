"""Shared in-process full-simulation helper for integration tests."""
import random
from pathlib import Path

import yaml
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.characters.generator import generate_population
from app.config.config import Settings
from app.db.models import bootstrap
from app.simulation.engine import Engine, TickScheduler, WorldClock
from app.simulation.report import build_report
from app.world.seed_world import seed_world


def load_settings(tmp_path, db_name):
    """Load the real default config with a temp DB path."""
    config_file = Path("config/default.yaml")
    with open(config_file, "r") as f:
        data = yaml.safe_load(f)
    data["persistence"]["db_path"] = str(tmp_path / db_name)
    return Settings(**data)


def run_full_simulation(settings: Settings, seed: int, days: int = 30):
    """
    Runs a full simulation in-process with the real default config.
    Returns (report, session, engine).
    """
    db_path = settings.persistence.db_path
    engine = create_engine(f"sqlite:///{db_path}")
    session = sessionmaker(bind=engine)()

    bootstrap(engine, settings, seed)
    world_id = settings.world.world_id
    seed_world(session, settings, world_id)
    sim_rng = random.Random(seed)
    generate_population(
        session, settings, sim_rng, world_id,
        settings.world.initial_population
    )
    # M2: social seeding (org membership/leaders, seeded conflicts) —
    # continues the same worldgen RNG stream for determinism.
    from app.world.social_seed import seed_social
    seed_social(session, settings, world_id, sim_rng)

    clock = WorldClock()
    scheduler = TickScheduler()
    sim_engine = Engine(clock, scheduler)

    # One bulk step per game day: the engine's boundary checks hit the
    # hour/day modulo at the end of each step (timestamps are multiples
    # of 1440), which triggers the daily handlers exactly once per day.
    for _ in range(days):
        sim_engine.step(1440, session, world_id, settings)

    report = build_report(session, world_id, settings, 0.0, seed)
    return report, session, engine
