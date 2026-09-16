import sqlite3

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import WorldSnapshot, bootstrap
from app.simulation.snapshots import take_snapshot


@pytest.fixture
def snap_session(default_settings):
    engine = create_engine("sqlite:///:memory:")
    bootstrap(engine, default_settings, 42)
    session = sessionmaker(bind=engine)()
    yield session, engine
    session.close()


def test_snapshot_creates_file_and_row(tmp_path, snap_session, world_id):
    session, engine = snap_session
    snap_dir = tmp_path / "snaps"

    take_snapshot(engine, session, world_id, 1440, str(snap_dir))

    snap_path = snap_dir / f"world_{world_id}_day_1.db"
    assert snap_path.exists()

    # The snapshot opens as a real SQLite DB
    conn = sqlite3.connect(str(snap_path))
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    conn.close()
    assert "characters" in tables

    row = session.query(WorldSnapshot).filter_by(
        world_id=world_id, game_timestamp=1440
    ).one()
    assert row.path.endswith(f"world_{world_id}_day_1.db")


def test_snapshot_second_day_creates_second_file(tmp_path, snap_session, world_id):
    session, engine = snap_session
    snap_dir = tmp_path / "snaps"

    take_snapshot(engine, session, world_id, 1440, str(snap_dir))
    take_snapshot(engine, session, world_id, 2880, str(snap_dir))

    assert (snap_dir / f"world_{world_id}_day_1.db").exists()
    assert (snap_dir / f"world_{world_id}_day_2.db").exists()
    assert session.query(WorldSnapshot).count() == 2
