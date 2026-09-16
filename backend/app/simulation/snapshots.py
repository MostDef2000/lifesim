import os
import sqlite3
from datetime import datetime

from sqlalchemy.orm import Session

from app.db.models import WorldSnapshot


def take_snapshot(
    db_path_or_engine, session: Session, world_id: str,
    game_timestamp: int, snapshot_dir: str
):
    """
    Creates a SQLite snapshot of the current database.
    - Copies live DB to snapshot_dir/world_<world_id>_day_<game_day>.db using backup API.
    - Inserts a row into world_snapshots.
    """
    # Calculate day from timestamp for the filename
    game_day = game_timestamp // 1440
    # Ensure directory exists
    os.makedirs(snapshot_dir, exist_ok=True)

    snapshot_path = os.path.join(snapshot_dir, f"world_{world_id}_day_{game_day}.db")

    # Snapshot captures the persisted state: commit pending session work
    # first (also releases the DBAPI transaction so the backup API does not
    # contend with it on subsequent snapshots).
    session.commit()

    # SQLite Backup API — works for both file paths and live engines
    # (in-memory test DBs included).
    if isinstance(db_path_or_engine, str):
        src = sqlite3.connect(db_path_or_engine)
        try:
            dst = sqlite3.connect(snapshot_path)
            with dst:
                src.backup(dst)
            dst.close()
        finally:
            src.close()
    else:
        # If we are passed an engine, back up the underlying SQLite database.
        url = db_path_or_engine.url
        if url.drivername != "sqlite":
            # Non-sqlite engines are not supported by this backup API
            raise NotImplementedError("Snapshots only supported for SQLite")

        if url.database and url.database != ":memory:":
            # File-based DB: connect to the file path directly
            src = sqlite3.connect(url.database)
            try:
                dst = sqlite3.connect(snapshot_path)
                with dst:
                    src.backup(dst)
                dst.close()
            finally:
                src.close()
        else:
            # In-memory SQLite: a fresh engine.raw_connection() would see an
            # EMPTY database (each :memory: connection is its own DB), so back
            # up the session's live DBAPI connection, which owns the schema.
            # The session-owned connection is not closed here.
            raw = session.connection().connection.driver_connection
            dst = sqlite3.connect(snapshot_path)
            with dst:
                raw.backup(dst)
            dst.close()

    # Record in DB
    snapshot = WorldSnapshot(
        world_id=world_id,
        game_timestamp=game_timestamp,
        created_real=datetime.utcnow().isoformat(),
        path=snapshot_path
    )
    session.add(snapshot)
    session.flush()
