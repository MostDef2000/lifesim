"""#179 — server-side WS cursor handshake (TestClient websocket evidence).

Before #179 the server always started polling world_events from cursor 0 on
every connect: a reconnecting client paid for a FULL backlog replay (up to
100 events per batch, every reconnect) — the bandwidth/DB cost class behind
#177/#178. Now the client may declare its poll START position right after
hello:

    client -> {"type": "cursor", "id": N}
    server -> {"type": "cursor", "id": N}   (ack — same shape as the drain path)
    server  -> polls world_events (id > N)

Legacy clients (send nothing) keep the old behavior after a bounded
handshake window — WS_CURSOR_HANDSHAKE_TIMEOUT_S, derived as
max(2.0, 2 * ws_interval_s) in production; tests patch the module constant
to keep the fallback wait fast. Garbage/negative cursor ids clamp to >= 0
and never kill the connection (drain semantics preserved).

The client half (sendWsCursor arming) is byte-pinned in
test_ws_cursor_client_pins.py.
"""

import json
import os
import sys
import time

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
    # app is exposed for app.state reads (ws_connections counter, m8 style)
    return settings, app, client, engine, factory


def _seed_events(factory, world_id, n):
    """Insert n synthetic world events; return (first_id, last_id)."""
    from app.db.models import WorldEvent

    with factory() as session:
        rows = [
            WorldEvent(
                world_id=world_id,
                event_type="TEST_CURSOR_PING",
                payload=json.dumps({"i": i}),
                game_timestamp=1440 + i,
            )
            for i in range(n)
        ]
        session.add_all(rows)
        session.commit()
        return rows[0].id, rows[-1].id


def _min_event_id(factory, world_id):
    from app.db.models import WorldEvent

    with factory() as session:
        return (
            session.query(func.min(WorldEvent.id))
            .filter_by(world_id=world_id)
            .scalar()
        )


# ---------- 1. the declared cursor is honored as the poll START ----------

def test_cursor_handshake_starts_poll_at_declared_cursor(work):
    settings, app, client, engine, factory = work
    first, last = _seed_events(factory, settings.world.world_id, 30)
    start = first + 9  # the 10th seeded event id — nothing at or below it may arrive
    with client.websocket_connect("/ws") as ws:
        hello = ws.receive_json()
        assert hello == {"type": "hello", "cursor": 0}  # hello payload unchanged
        ws.send_json({"type": "cursor", "id": start})
        ack = ws.receive_json()
        assert ack == {"type": "cursor", "id": start}  # existing ack shape
        batch = ws.receive_json()
        assert batch["type"] == "events"
        # the FIRST batch contains ONLY ids beyond the declared cursor —
        # no backlog replay of ids <= start
        assert [e["id"] for e in batch["events"]] == list(range(start + 1, last + 1))


# ---------- 2. legacy client (silent) keeps the full-replay fallback ----------

def test_legacy_client_silent_handshake_falls_back_to_full_replay(work, monkeypatch):
    settings, app, client, engine, factory = work
    import app.api.ws as ws_mod

    # least invasive hook: patch the module-level handshake timeout constant
    # (production derives max(2.0, 2 * ws_interval_s) from settings).
    monkeypatch.setattr(ws_mod, "WS_CURSOR_HANDSHAKE_TIMEOUT_S", 0.2)
    _seed_events(factory, settings.world.world_id, 5)
    min_id = _min_event_id(factory, settings.world.world_id)
    with client.websocket_connect("/ws") as ws:
        hello = ws.receive_json()
        assert hello == {"type": "hello", "cursor": 0}
        # nothing sent — wait past the handshake timeout, then the first
        # batch must still be the FULL replay from the very first event
        # (cursor 0), not the tail.
        time.sleep(0.4)
        batch = ws.receive_json()
        assert batch["type"] == "events"
        assert batch["events"], "legacy fallback must replay the backlog"
        assert batch["events"][0]["id"] == min_id


# ---------- 3. garbage cursors clamp, never crash, stream stays sane ----------

def test_garbage_cursor_survives_and_clamps(work):
    settings, app, client, engine, factory = work
    first, last = _seed_events(factory, settings.world.world_id, 10)
    min_id = _min_event_id(factory, settings.world.world_id)
    with client.websocket_connect("/ws") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello"
        # unparsable cursor id -> clamped to 0, acked, connection alive
        ws.send_json({"type": "cursor", "id": "abc"})
        ack = ws.receive_json()
        assert ack == {"type": "cursor", "id": 0}
        # negative and then a valid cursor ride the regular drain path —
        # no crash, the stream keeps flowing
        ws.send_json({"type": "cursor", "id": -5})
        ws.send_json({"type": "cursor", "id": first + 4})
        batch = None
        extra_acks = []
        for _ in range(30):
            msg = ws.receive_json()
            if msg.get("type") == "events":
                batch = msg
                break
            extra_acks.append(msg)
        assert batch is not None, "connection died on garbage cursor input"
        assert all(a.get("type") == "cursor" for a in extra_acks)
        ids = [e["id"] for e in batch["events"]]
        # clamped to >= 0: the replay starts at the very first event (cursor
        # never went negative), bounded by the server cap
        assert ids[0] == min_id
        assert len(ids) <= 100


# ---------- 4. load-bound: the declared START bounds the first payload ----------

def test_declared_cursor_bounds_first_batch(work):
    settings, app, client, engine, factory = work
    first, last = _seed_events(factory, settings.world.world_id, 1200)
    start = last - 10  # exactly 10 events beyond the declared cursor
    with client.websocket_connect("/ws") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello"
        ws.send_json({"type": "cursor", "id": start})
        ack = ws.receive_json()
        assert ack == {"type": "cursor", "id": start}
        batch = ws.receive_json()
        assert batch["type"] == "events"
        ids = [e["id"] for e in batch["events"]]
        # the single first batch carries exactly the 10 events after the
        # declared START (server cap 100 unchanged; the START is the point)
        assert ids == list(range(start + 1, last + 1))
        assert len(ids) == 10


# ---------- 5. fix-round pins: disconnect, non-cursor, overflowing float ----------

def test_disconnect_during_handshake_decrements_connection_counter(work):
    settings, app, client, engine, factory = work
    _seed_events(factory, settings.world.world_id, 5)
    assert getattr(app.state, "ws_connections", 0) == 0
    with client.websocket_connect("/ws") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello"
        # close the client side immediately, NOTHING declared: the disconnect
        # must land inside the handshake wait (default window is ~2s). The
        # TestClient context exit waits for the endpoint coroutine to finish,
        # so the counter read below is deterministic (m8-style app.state read).
    # the endpoint exited through the disconnect path: incremented on accept,
    # decremented exactly once on the handshake disconnect
    assert app.state.ws_connections == 0


def test_non_cursor_first_message_falls_back_to_full_replay(work, monkeypatch):
    settings, app, client, engine, factory = work
    import app.api.ws as ws_mod

    monkeypatch.setattr(ws_mod, "WS_CURSOR_HANDSHAKE_TIMEOUT_S", 0.2)
    _seed_events(factory, settings.world.world_id, 5)
    min_id = _min_event_id(factory, settings.world.world_id)
    with client.websocket_connect("/ws") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello"
        # a non-cursor first message inside the handshake window is ignored
        # (no ack, no crash): the legacy fallback applies — full replay from
        # the very first event
        ws.send_json({"type": "ping"})
        time.sleep(0.4)
        batch = ws.receive_json()
        assert batch["type"] == "events"
        assert batch["events"], "legacy fallback must replay the backlog"
        assert batch["events"][0]["id"] == min_id
        # the connection still works afterward: a cursor update rides the
        # regular drain path and is acked on the same socket
        ws.send_json({"type": "cursor", "id": min_id + 50})
        ack = None
        for _ in range(10):
            msg = ws.receive_json()
            if msg.get("type") == "cursor":
                ack = msg
                break
        assert ack is not None and ack["id"] == min_id + 50


def test_overflowing_float_cursor_clamps_and_survives(work):
    settings, app, client, engine, factory = work
    first, last = _seed_events(factory, settings.world.world_id, 5)
    with client.websocket_connect("/ws") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello"
        # 1e400 serializes to JSON infinity; int(inf) raises OverflowError —
        # the connection must SURVIVE and clamp to 0 (F2 regression pin)
        ws.send_json({"type": "cursor", "id": 1e400})
        ack = ws.receive_json()
        assert ack == {"type": "cursor", "id": 0}
        # the stream keeps flowing on the same connection (fallback replay)
        batch = ws.receive_json()
        assert batch["type"] == "events"
        assert batch["events"], "fallback replay delivered after overflow clamp"
        # a subsequent valid cursor is acked via the regular drain path
        ws.send_json({"type": "cursor", "id": last})
        drained_ack = None
        for _ in range(10):
            msg = ws.receive_json()
            if msg.get("type") == "cursor":
                drained_ack = msg
                break
        assert drained_ack is not None and drained_ack["id"] == last
