import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config.config import Settings
from app.db.models import bootstrap


@pytest.fixture
def default_settings():
    # Minimal valid settings based on default.yaml
    return Settings(
        world={
            "world_id": "test_world",
            "initial_population": 20,
            "start_real_timestamp": "2026-01-01T00:00:00Z",
        },
        ticks={
            "time_scale": 1.0,
            "persistence_commit_interval_game_minutes": 60,
            "snapshot_interval_game_days": 1,
        },
        needs={
            "decay_rates": {"hunger": 0.1, "thirst": 0.1, "energy": 0.1, "social": 0.1},
            "recovery_rates": {
                "SLEEP": {"energy": 1.0},
                "EAT": {"hunger": 1.0},
                "DRINK": {"thirst": 1.0},
            },
            "critical_thresholds": {"hunger": 20.0, "thirst": 20.0, "energy": 10.0},
            "health_decay_rate": 0.01,
        },
        utility={"weights": {"hunger": 1.0, "thirst": 1.0, "energy": 1.0, "social": 1.0}},
        actions={
            "SLEEP": {
                "duration_minutes": 480,
                "max_duration_minutes": 480,
                "base_utility_weight": 1.0,
            },
            "EAT": {
                "duration_minutes": 15,
                "max_duration_minutes": 30,
                "base_utility_weight": 1.0,
            },
            "DRINK": {
                "duration_minutes": 5,
                "max_duration_minutes": 15,
                "base_utility_weight": 1.0,
            },
            "WORK": {
                "duration_minutes": 480,
                "max_duration_minutes": 600,
                "base_utility_weight": 1.0,
            },
            "MOVE": {
                "duration_minutes": 10,
                "max_duration_minutes": 120,
                "base_utility_weight": 1.0,
            },
            "BUY_ITEM": {
                "duration_minutes": 5,
                "max_duration_minutes": 30,
                "base_utility_weight": 1.0,
            },
            "IDLE": {
                "duration_minutes": 1,
                "max_duration_minutes": 60,
                "base_utility_weight": 1.0,
            },
        },
        economy={
            "starting_balance": 1000,
            "default_salary_day": 50,
            "shop_price_multiplier": 1.0,
            "prices": {"food_bread": 2},
            "shop_stock": {"food_bread": 100},
            "kitchen_stock": {"food_bread": 20},
            "water_daily_supply_amount": 50.0,
            "water_initial_quantity": 1000.0,
            "salaries": {"farmer": 40},
            "organizations": {
                "government": {
                    "name": "Gov",
                    "starting_balance": 10000,
                    "location": "settlement",
                    "type": "government",
                },
                "merchant_guild": {
                    "name": "Guild",
                    "starting_balance": 5000,
                    "location": "shop",
                    "type": "merchant",
                },
                "community": {
                    "name": "Comm",
                    "starting_balance": 2000,
                    "location": "settlement",
                    "type": "community",
                },
                "shop": {
                    "name": "Shop",
                    "starting_balance": 1000,
                    "location": "shop",
                    "type": "business",
                },
            },
        },
        movement={
            "default_travel_minutes": 10,
            "max_capacity_default": 100,
            "edges": [
                {"from": "island", "to": "settlement", "travel_minutes": 30},
                {"from": "settlement", "to": "shop", "travel_minutes": 5},
                {"from": "settlement", "to": "workshop", "travel_minutes": 5},
                {"from": "settlement", "to": "kitchen", "travel_minutes": 5},
                {"from": "settlement", "to": "well", "travel_minutes": 5},
                {"from": "settlement", "to": "storage", "travel_minutes": 5},
                {"from": "settlement", "to": "pier", "travel_minutes": 10},
            ],
        },
        population={
            "min_age": 18,
            "max_age": 80,
            "first_names": ["A"],
            "last_names": ["B"],
            "trait_keys": ["sociability"],
            "jobs": [
                {"title": "Farmer", "salary": 40, "schedule": {"start_hour": 6, "end_hour": 18}}
            ],
        },
        persistence={
            "db_path": ":memory:",
            "wal_mode": True,
            "synchronous": "NORMAL",
            "snapshot_dir": "snapshots",
        },
        invariants={
            "max_duration_minutes": 1440,
            "min_balance": 0,
            "health_range": [0, 100],
            "needs_range": [0, 100],
            "max_death_rate_per_day": 0.1,
        },
        generation={"trait_range": [-100, 100], "initial_items": ["item1"]},
        locations={
            "houses_count": 24,
            "locations": {
                "island": {"name": "I", "type": "island", "parent": None, "capacity": 100},
                "settlement": {"name": "S", "type": "settlement", "parent": None, "capacity": 100},
                "home": {"name": "H", "type": "house", "parent": "settlement", "capacity": 1},
                "shop": {"name": "Sh", "type": "shop", "parent": "settlement", "capacity": 20},
                "kitchen": {
                    "name": "K",
                    "type": "kitchen",
                    "parent": "settlement",
                    "capacity": 20,
                },
                "workshop": {
                    "name": "W",
                    "type": "workshop",
                    "parent": "settlement",
                    "capacity": 20,
                },
                "storage": {
                    "name": "St",
                    "type": "storage",
                    "parent": "settlement",
                    "capacity": 50,
                },
                "well": {
                    "name": "We",
                    "type": "well",
                    "parent": "settlement",
                    "capacity": 10,
                },
                "pier": {"name": "P", "type": "pier", "parent": "settlement", "capacity": 20},
            }
        },
    )

@pytest.fixture
def db_engine(default_settings):
    engine = create_engine("sqlite:///:memory:")
    bootstrap(engine, default_settings, 42)
    return engine

@pytest.fixture
def db_session(db_engine):
    Session = sessionmaker(bind=db_engine)
    session = Session()
    yield session
    session.close()

@pytest.fixture
def world_id(default_settings):
    return default_settings.world.world_id

@pytest.fixture
def seeded_session(db_session, default_settings, world_id):
    from app.world.seed_world import seed_world
    seed_world(db_session, default_settings, world_id)
    return db_session

@pytest.fixture
def session(seeded_session):
    return seeded_session

@pytest.fixture
def settings(default_settings):
    return default_settings
