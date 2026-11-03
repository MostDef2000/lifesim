"""#258 — WS hello carries the per-world snapshot tail; silent/non-cursor
clients start at the tail (no legacy full replay from 0).

Before #258 the server always started polling world_events from cursor 0 on
every connect: a silent client (or one whose first frame was not a cursor
declaration) paid for a FULL backlog replay from 0. Now the hello carries the
per-world snapshot tail (MAX(WorldEvent.id), 0 for an empty world) and that
tail is the DEFAULT start position:

    server -> {"type": "hello", "cursor": <tail>}
    (silent / malformed / non-cursor first frame) -> stream starts at <tail>
    explicit {"type": "cursor", "id": 0}          -> full replay from 0
                                                     (recovery path preserved)

The client half (feedInit, hello deadline, tail-100 window) is byte-pinned in
tests/m258/test_ws_boot_client_pins.py; the #179 sendWsCursor census and the
#177/#178 bootCursor/liveness literals are owned by
tests/m179/test_ws_cursor_client_pins.py and tests/m176.

All WS receives in THIS file use bounded waits: a single reader thread
enqueues frames and every wait is time-limited, so no test here can block
indefinitely — the pre-#258 non-cursor test hung on a blocking receive_json()
waiting for a replay batch that no longer exists. (The m179 legacy handshake
tests still use blocking receives where a frame is guaranteed to arrive.)
"""

import json
import os
import queue
import sys
import threading

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from app.api.app import create_app  # noqa: E402
from app.config.config import load_config  # noqa: E402
from app.db.models import bootstrap, create_engine_factory  # noqa: E402
from app.world.seed_world import seed_world  # noqa: E402

SETTINGS = None


def setup_module(module):
    global SETTINGS
    SETTINGS = load_config("config/default.yaml")


def _build(tmp_path):
    settings = SETTINGS.model_copy(update={
        "persistence": SETTINGS.persistence.model_copy(
            update={"db_path": str(tmp_path / "w.db")}
        )
    })
    engine = create_engine_factory(settings)
    import random

    from app.characters.generator import generate_population
    from app.world.social_seed import seed_social

    with sessionmaker(bind=engine)() as session:
        bootstrap(engine, settings, seed=42)
        seed_world(session, settings, settings.world.world_id)
        rng = random.Random(42)
        generate_population(session, settings, rng, settings.world.world_id, 20)
        seed_social(session, settings, settings.world.world_id, rng)
        session.commit()
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app(settings, factory)
    return settings, app, engine, factory


def _build_unseeded(tmp_path):
    """Build an app whose configured world exists but has NO events."""
    settings = SETTINGS.model_copy(update={
        "persistence": SETTINGS.persistence.model_copy(
            update={"db_path": str(tmp_path / "w.db")}
        )
    })
    engine = create_engine_factory(settings)
    with sessionmaker(bind=engine)() as session:
        bootstrap(engine, settings, seed=42)
        # NOTE: no seed_world / population — the world is empty
        session.commit()
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app(settings, factory)
    return settings, app, engine, factory


@pytest.fixture()
def work(tmp_path):
    settings, app, engine, factory = _build(tmp_path)
    # WS auth rides the httpOnly session cookie set by /auth/login (#124).
    client = TestClient(app)
    client.post("/auth/register", json={
        "username": "cursor", "email": "c@x.com", "password": "password123",
        "age_confirmed": True,
    })
    client.post("/auth/login", json={"username": "cursor", "password": "password123"})
    return settings, app, client, engine, factory


class _WsReader:
    """Bounded-wait WS receiver: a single daemon thread enqueues every frame;
    wait(timeout) is time-limited so no test can block indefinitely.

    A single persistent reader is required: a per-call worker thread that
    times out stays blocked on receive and would consume the NEXT frame.
    """

    def __init__(self, ws):
        self.queue = queue.Queue()
        self.thread = threading.Thread(target=self._run, args=(ws,), daemon=True)
        self.thread.start()

    def _run(self, ws):
        while True:
            try:
                self.queue.put(ws.receive_json())
            except Exception:
                break

    def wait(self, timeout):
        try:
            return self.queue.get(timeout=timeout)
        except queue.Empty:
            return None


def _seed_events(factory, world_id, n):
    """Insert n synthetic world events; return (first_id, last_id)."""
    from app.db.models import WorldEvent

    with factory() as session:
        rows = [
            WorldEvent(
                world_id=world_id,
                event_type="TEST_HELLO_TAIL",
                payload=json.dumps({"i": i}),
                game_timestamp=1440 + i,
            )
            for i in range(n)
        ]
        session.add_all(rows)
        session.commit()
        return rows[0].id, rows[-1].id


def _max_event_id(factory, world_id):
    from app.db.models import WorldEvent

    with factory() as session:
        return (
            session.query(func.max(WorldEvent.id))
            .filter_by(world_id=world_id)
            .scalar()
        )


def _min_event_id(factory, world_id):
    from app.db.models import WorldEvent

    with factory() as session:
        return (
            session.query(func.min(WorldEvent.id))
            .filter_by(world_id=world_id)
            .scalar()
        )


# ---------- 1. hello carries the per-world snapshot tail ----------

def test_hello_carries_per_world_tail(work):
    settings, app, client, engine, factory = work
    wid = settings.world.world_id
    _seed_events(factory, wid, 30)
    tail = _max_event_id(factory, wid)
    with client.websocket_connect("/ws") as ws:
        hello = ws.receive_json()
        assert hello == {"type": "hello", "cursor": tail}


def test_hello_empty_world_cursor_zero(tmp_path):
    # empty world: MAX(WorldEvent.id) is NULL -> the hello cursor is 0
    settings, app, engine, factory = _build_unseeded(tmp_path)
    client = TestClient(app)
    client.post("/auth/register", json={
        "username": "cursor", "email": "c@x.com", "password": "password123",
        "age_confirmed": True,
    })
    client.post("/auth/login", json={"username": "cursor", "password": "password123"})
    with client.websocket_connect("/ws") as ws:
        hello = ws.receive_json()
        assert hello == {"type": "hello", "cursor": 0}


# ---------- 2. per-world isolation ----------

def test_hello_per_world_isolation(work):
    settings, app, client, engine, factory = work
    from app.db.models import World

    wid = settings.world.world_id
    # the configured world already has seed events; add a few more
    _first, last = _seed_events(factory, wid, 5)
    # a second world with LARGER ids must not leak into the hello tail
    with factory() as session:
        session.add(World(
            id="other_world", seed=7,
            start_real="2026-01-01T00:00:00Z", config_sha256="x",
        ))
        session.commit()
    _ofirst, olast = _seed_events(factory, "other_world", 5)
    assert olast > last  # the other world's ids are larger (global autoincrement)
    with client.websocket_connect("/ws") as ws:
        hello = ws.receive_json()
        # the tail reflects ONLY the configured world, not the other world
        assert hello == {"type": "hello", "cursor": last}


# ---------- 3. silent client starts at the tail (no pre-tail replay) ----------

def test_silent_client_starts_at_tail(work, monkeypatch):
    settings, app, client, engine, factory = work
    import app.api.ws as ws_mod

    # least invasive hook: patch the module-level handshake timeout constant
    # (production derives max(2.0, 2 * ws_interval_s) from settings).
    monkeypatch.setattr(ws_mod, "WS_CURSOR_HANDSHAKE_TIMEOUT_S", 0.2)
    wid = settings.world.world_id
    _seed_events(factory, wid, 5)
    tail = _max_event_id(factory, wid)
    with client.websocket_connect("/ws") as ws:
        reader = _WsReader(ws)
        hello = reader.wait(5.0)
        assert hello == {"type": "hello", "cursor": tail}
        # bounded wait proves SILENCE: no pre-tail batch arrives (the legacy
        # full replay from 0 is gone for a silent client)
        assert reader.wait(1.5) is None
        # a NEW event (beyond the tail) DOES arrive
        _seed_events(factory, wid, 1)
        batch = reader.wait(3.0)
        assert batch is not None and batch["type"] == "events"
        assert [e["id"] for e in batch["events"]] == [tail + 1]


# ---------- 4. explicit cursor 0 -> full replay (recovery path preserved) ----------

def test_explicit_cursor_zero_full_replay(work):
    settings, app, client, engine, factory = work
    wid = settings.world.world_id
    _seed_events(factory, wid, 30)
    min_id = _min_event_id(factory, wid)
    with client.websocket_connect("/ws") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello"
        # an explicit cursor 0 declaration is the recovery path: full replay
        ws.send_json({"type": "cursor", "id": 0})
        ack = ws.receive_json()
        assert ack == {"type": "cursor", "id": 0}
        batch = ws.receive_json()
        assert batch["type"] == "events"
        # the replay starts at the very first event (cursor 0), bounded by
        # the server cap
        assert batch["events"][0]["id"] == min_id
        assert len(batch["events"]) <= 100


# ---------- 5. non-cursor first frame starts at the tail ----------

def test_non_cursor_first_frame_starts_at_tail(work, monkeypatch):
    settings, app, client, engine, factory = work
    import app.api.ws as ws_mod

    monkeypatch.setattr(ws_mod, "WS_CURSOR_HANDSHAKE_TIMEOUT_S", 0.2)
    wid = settings.world.world_id
    _seed_events(factory, wid, 5)
    tail = _max_event_id(factory, wid)
    with client.websocket_connect("/ws") as ws:
        reader = _WsReader(ws)
        hello = reader.wait(5.0)
        assert hello == {"type": "hello", "cursor": tail}
        # a non-cursor first frame is ignored (no ack, no crash): the stream
        # starts at the tail, NOT a full replay from 0
        ws.send_json({"type": "x"})
        assert reader.wait(1.5) is None  # bounded wait proves silence
        # a NEW event (beyond the tail) DOES arrive
        _seed_events(factory, wid, 1)
        batch = reader.wait(3.0)
        assert batch is not None and batch["type"] == "events"
        assert [e["id"] for e in batch["events"]] == [tail + 1]
