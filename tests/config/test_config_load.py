import pytest
import yaml

from app.config.config import load_config


def test_config_load_valid(tmp_path):
    config_file = tmp_path / "config.yaml"
    content = {
        "world": {
            "world_id": "w1",
            "seed": 1,
            "initial_population": 10,
            "start_real_timestamp": "2026-01-01T00:00:00Z",
        },
        "ticks": {
            "time_scale": 1.0,
            "persistence_commit_interval_game_minutes": 60,
            "snapshot_interval_game_days": 1,
        },
        "needs": {
            "decay_rates": {"hunger": 0.1, "thirst": 0.1, "energy": 0.1, "social": 0.1},
            "recovery_rates": {
                "SLEEP": {"energy": 1.0},
                "EAT": {"hunger": 1.0},
                "DRINK": {"thirst": 1.0},
            },
            "critical_thresholds": {"hunger": 20.0, "thirst": 20.0, "energy": 10.0},
            "health_decay_rate": 0.01,
        },
        "utility": {"weights": {"hunger": 1.0, "thirst": 1.0, "energy": 1.0, "social": 1.0}},
        "actions": {
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
        "economy": {
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
                "government": {"name": "Gov", "starting_balance": 10000},
                "merchant_guild": {"name": "Guild", "starting_balance": 5000},
            },
        },
        "movement": {
            "default_travel_minutes": 10,
            "max_capacity_default": 100,
            "edges": [{"from": "island", "to": "settlement", "travel_minutes": 30}],
        },
        "population": {
            "min_age": 18,
            "max_age": 80,
            "first_names": ["A"],
            "last_names": ["B"],
            "trait_keys": ["sociability"],
            "jobs": [
                {"title": "Farmer", "salary": 40, "schedule": {"start_hour": 6, "end_hour": 18}}
            ],
        },
        "persistence": {
            "db_path": "test.db",
            "wal_mode": True,
            "synchronous": "NORMAL",
            "snapshot_dir": "snapshots",
        },
        "invariants": {
            "max_duration_minutes": 1440,
            "min_balance": 0,
            "health_range": [0, 100],
            "needs_range": [0, 100],
            "max_death_rate_per_day": 0.1,
        },
        "generation": {"trait_range": [-100, 100], "initial_items": ["item1"]},
        "locations": {
            "locations": {
                "island": {"name": "I", "type": "island"},
                "settlement": {"name": "S", "type": "settlement"},
                "home": {"name": "H", "type": "house"},
                "shop": {"name": "Sh", "type": "shop"},
                "workshop": {"name": "W", "type": "workshop"},
                "storage": {"name": "St", "type": "storage"},
                "well": {"name": "We", "type": "well"},
                "pier": {"name": "P", "type": "pier"},
            }
        },
    }
    config_file.write_text(yaml.dump(content))
    settings = load_config(str(config_file))
    assert settings.world.world_id == "w1"

def test_config_load_invalid(tmp_path):
    config_file = tmp_path / "invalid.yaml"
    # Missing required field 'world'
    config_file.write_text(yaml.dump({"ticks": {}}))
    with pytest.raises(Exception):
        load_config(str(config_file))
