import hashlib
import pathlib
import sqlite3

from app.config.config import load_config
from app.db.models import bootstrap


def test_bootstrap_tables(tmp_path):
    # Load config from the real config/default.yaml
    config_path = pathlib.Path("config/default.yaml")
    settings = load_config(str(config_path))

    # Use tmp_path for the DB
    db_path = tmp_path / "test_bootstrap.db"
    from sqlalchemy import create_engine
    engine = create_engine(f"sqlite:///{db_path}")

    # Bootstrap with real settings and a seed
    seed = 42
    bootstrap(engine, settings, seed)

    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    # 1. Assert worlds.config_sha256 == sha256 of that file's bytes
    cursor.execute("SELECT config_sha256 FROM worlds")
    db_sha = cursor.fetchone()[0]

    with open(config_path, 'rb') as f:
        expected_sha = hashlib.sha256(f.read()).hexdigest()

    assert db_sha == expected_sha, f"Expected {expected_sha}, found {db_sha}"

    # 2. Check if 32 tables exist (25 from M3 + memories + ai_requests +
    # dialogue_turns in M4)
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    tables = [row[0] for row in cursor.fetchall()]
    assert len(tables) == 41, f"Expected 38 tables, found {len(tables)}: {tables}"

    # Check basic rows
    cursor.execute("SELECT id, seed FROM worlds")
    world = cursor.fetchone()
    assert world[0] == settings.world.world_id
    assert world[1] == seed

    cursor.execute("SELECT game_timestamp FROM world_clock")
    clock = cursor.fetchone()
    assert clock[0] == 0

    conn.close()
