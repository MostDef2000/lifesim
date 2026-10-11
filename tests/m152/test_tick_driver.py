"""#151 [alpha-v1][engine] Live tick driver — the uvicorn runtime advances game time.

The issue body is the spec of record. Covers:

- a) ticker enabled → after a few intervals world_clock.game_timestamp
  ADVANCES (DB row + the GET /world payload the UI topbar reads) and a
  created MOVE task completes on its own (arrival without a manual
  progress_tick).
- b) world.live_tick_enabled=false → old semantics pinned: clock frozen.
- c) is_paused → clock frozen while paused; unpause → resumes.
- d) a tick that raises (Engine.step throws once) does NOT kill the driver:
  subsequent ticks still advance the clock; the server stays responsive.
- e) invariants: clock monotonic across repeated ticks; the completed MOVE
  is applied exactly once (single CHARACTER_MOVED; task stays completed
  with a stable completed_at).

Fixture pattern follows tests/m151 (real default.yaml + model_copy
overrides, tmp db, bootstrap + seed_world, create_app + TestClient). These
tests use a CONTEXT-MANAGED TestClient — the only suite that does — because
the driver starts in the app lifespan (uvicorn runs the same lifespan for
`vl1 serve` and `uvicorn app.run:app`).
"""

import os
import sys
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from app.api.app import create_app  # noqa: E402
from app.config.config import load_config  # noqa: E402
from app.db.models import (  # noqa: E402
    Character,
    CharacterTask,
    Location,
    WorldClock,
    WorldEvent,
    bootstrap,
    create_engine_factory,
)
from app.events.events import EventType  # noqa: E402
from app.simulation.engine import Engine  # noqa: E402
from app.world.seed_world import seed_world  # noqa: E402

PW = "Correct-Horse-9!"
SETTINGS = None

# Live-test numbers: 0.1 s real interval; time_scale 6000 → 6000 game-min
# per real-min → 10 game-minutes earned per tick. A 5-minute MOVE (house →
# settlement) completes on the first tick; every test stays well under 10 s.
TICK_INTERVAL_S = 0.1
TIME_SCALE = 6000.0
POLL_TIMEOUT_S = 8.0


def setup_module(module):
    global SETTINGS
    SETTINGS = load_config("config/default.yaml")


def _build_world(tmp_path, live_tick_enabled=True):
    settings = SETTINGS.model_copy(update={
        "world": SETTINGS.world.model_copy(update={
            "live_tick_enabled": live_tick_enabled,
            "live_tick_interval_s": TICK_INTERVAL_S,
        }),
        "ticks": SETTINGS.ticks.model_copy(update={"time_scale": TIME_SCALE}),
        "persistence": SETTINGS.persistence.model_copy(
            update={"db_path": str(tmp_path / "w.db")}
        ),
    })
    engine = create_engine_factory(settings)
    with sessionmaker(bind=engine)() as session:
        bootstrap(engine, settings, seed=42)
        seed_world(session, settings, settings.world.world_id)
        session.commit()
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app(settings, factory)
    return settings, app, factory


@pytest.fixture()
def world(tmp_path):
    settings, app, factory = _build_world(tmp_path)
    with TestClient(app) as client:
        yield settings, app, client, factory


@pytest.fixture()
def frozen_world(tmp_path):
    settings, app, factory = _build_world(tmp_path, live_tick_enabled=False)
    with TestClient(app) as client:
        yield settings, app, client, factory


# ---------- helpers (m151 pattern) ----------

def _register_and_login(client, username):
    r = client.post("/auth/register", json={
        "username": username, "email": f"{username}@example.com",
        "password": PW, "age_confirmed": True,
    })
    assert r.status_code == 201, r.text
    r = client.post("/auth/login", json={"username": username, "password": PW})
    assert r.status_code == 200, r.text


def _make_character(client, name):
    r = client.post("/characters", json={"name": name, "sex": "M", "age": 30})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _to_direct(client, cid):
    """Players default to AUTONOMOUS (§61) — direct actions need DIRECT."""
    r = client.post(f"/characters/{cid}/control", json={"mode": "DIRECT"})
    assert r.status_code == 200, r.text


def _loc_by_type(factory, world_id, loc_type):
    with factory() as session:
        row = (
            session.query(Location)
            .filter_by(world_id=world_id, type=loc_type)
            .order_by(Location.id)
            .first()
        )
        return row.id if row is not None else None


def _clock(factory, world_id):
    with factory() as session:
        row = (
            session.query(WorldClock)
            .filter_by(world_id=world_id)
            .first()
        )
        return int(row.game_timestamp) if row is not None else 0


def _char_loc(factory, cid):
    with factory() as session:
        return session.get(Character, cid).location_id


def _set_paused(factory, world_id, paused):
    with factory() as session:
        row = session.query(WorldClock).filter_by(world_id=world_id).first()
        row.is_paused = paused
        session.commit()


def _wait_until(pred, timeout=POLL_TIMEOUT_S):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return pred()


def _create_move(client, factory, cid, world_id, loc_type="settlement"):
    _to_direct(client, cid)
    target_id = _loc_by_type(factory, world_id, loc_type)
    r = client.post("/actions", json={
        "character_id": cid, "action_type": "MOVE",
        "params": {"location_id": target_id},
    })
    assert r.status_code == 201, r.text
    return target_id, int(r.json()["task_id"])


# ---------- a) ticker advances the clock and completes a MOVE ----------

def test_ticker_advances_clock_and_completes_move(world):
    settings, app, client, factory = world
    wid = settings.world.world_id
    _register_and_login(client, "ticker151")
    cid = _make_character(client, "Tick Mover")
    target_id, task_id = _create_move(client, factory, cid, wid)

    # The ticker is live, so the clock may already have moved during setup —
    # the proof of ticking is a strictly LATER reading, not an absolute zero.
    baseline = _clock(factory, wid)
    assert _wait_until(lambda: _clock(factory, wid) > baseline), (
        "live ticker did not advance the clock"
    )

    # The /world payload the UI topbar reads moves too.
    body = client.get("/world").json()
    assert body["game_timestamp"] > 0

    # MOVE completes on its own — arrival without a manual progress_tick.
    assert _wait_until(lambda: _char_loc(factory, cid) == target_id), (
        f"MOVE never arrived; loc={_char_loc(factory, cid)}"
    )
    with factory() as session:
        task = session.get(CharacterTask, task_id)
        assert task is not None and task.status == "completed"


# ---------- b) disabled = old semantics: frozen clock ----------

def test_disabled_ticker_freezes_clock(frozen_world):
    settings, app, client, factory = frozen_world
    wid = settings.world.world_id
    # Ten beats of real time pass — the clock must not move (regression pin
    # for the pre-#151 semantics).
    time.sleep(TICK_INTERVAL_S * 10)
    assert _clock(factory, wid) == 0, (
        "clock must stay frozen with world.live_tick_enabled=false"
    )


# ---------- c) is_paused freezes; resume unfreezes ----------

def test_pause_freezes_clock_and_resume_resumes(world):
    settings, app, client, factory = world
    wid = settings.world.world_id
    assert _wait_until(lambda: _clock(factory, wid) > 0), "ticker not running"

    _set_paused(factory, wid, True)
    frozen_at = _clock(factory, wid)
    time.sleep(TICK_INTERVAL_S * 10)
    assert _clock(factory, wid) == frozen_at, "clock advanced while paused"

    _set_paused(factory, wid, False)
    assert _wait_until(lambda: _clock(factory, wid) > frozen_at), (
        "clock did not resume after unpause"
    )


# ---------- d) a failing tick does not kill the driver ----------

def test_tick_exception_does_not_kill_driver(world, monkeypatch):
    settings, app, client, factory = world
    wid = settings.world.world_id

    real_step = Engine.step
    state = {"raised": False}

    def flaky_step(self, *args, **kwargs):
        if not state["raised"]:
            state["raised"] = True
            raise RuntimeError("injected tick failure (#151)")
        return real_step(self, *args, **kwargs)

    monkeypatch.setattr(Engine, "step", flaky_step)

    assert _wait_until(
        lambda: state["raised"] and _clock(factory, wid) > 0
    ), "driver must survive a failing tick and advance afterwards"
    # The server itself is unharmed.
    assert client.get("/health").status_code == 200


# ---------- e) repeated ticks never double-apply ----------

def test_repeated_ticks_never_double_apply(world):
    settings, app, client, factory = world
    wid = settings.world.world_id
    _register_and_login(client, "mono151")
    # Setup race guard: the live driver can tick BETWEEN POST /characters and
    # the DIRECT switch, and a fresh player character defaults to AUTONOMOUS
    # (§61) — a tick in that window legally runs its utility AI (routine
    # DRINK / autonomous MOVEs), logging extra CHARACTER_MOVED events for
    # this very character that _arrivals() below would count as a second
    # arrival. The engine is correct; the test was racy. Freezing the world
    # for the whole setup makes it deterministic (step_world_live no-ops on
    # is_paused, so no tick can touch the character before it is DIRECT).
    _set_paused(factory, wid, True)
    cid = _make_character(client, "Mono Mover")
    target_id, task_id = _create_move(client, factory, cid, wid)
    _set_paused(factory, wid, False)

    assert _wait_until(lambda: _char_loc(factory, cid) == target_id)

    def _arrivals(session):
        return (
            session.query(WorldEvent)
            .filter_by(world_id=wid,
                       event_type=EventType.CHARACTER_MOVED.value,
                       actor_id=cid)
            .count()
        )

    with factory() as session:
        task = session.get(CharacterTask, task_id)
        assert task.status == "completed"
        completed_at = task.completed_at
        assert _arrivals(session) == 1, "arrival must be applied exactly once"

    # Clock is monotonic across further repeated ticks.
    prev = _clock(factory, wid)
    for _ in range(30):
        time.sleep(0.02)
        cur = _clock(factory, wid)
        assert cur >= prev, "clock went backwards across ticks"
        prev = cur

    # …and the completed task was not re-processed afterwards.
    with factory() as session:
        task = session.get(CharacterTask, task_id)
        assert task.status == "completed"
        assert task.completed_at == completed_at, (
            "completed task re-processed by later ticks"
        )
        assert _arrivals(session) == 1, "duplicate arrival event"


def test_lock_held_disables_ticker(world):
    """Review F1 (#151): when the single-writer lock is held elsewhere
    (OSError from flock), start_tick_driver must return None — no ticker,
    frozen clock. The lock itself is kernel-managed (flock on
    <db>.tick.lock); here the conflict is simulated at the seam."""
    settings, app, client, factory = world
    import time
    from unittest.mock import patch

    calls = []

    def conflicting(fd, operation):
        calls.append(operation)
        raise OSError(11, "Resource temporarily unavailable")

    from app.api import ticker as ticker_mod
    fd_before = ticker_mod._TICK_LOCK_FD
    with patch("app.api.ticker.fcntl.flock", conflicting):
        with client:
            with factory() as s:
                before = s.query(WorldClock).filter_by(
                    world_id=settings.world.world_id).first().game_timestamp
            time.sleep(2.0)
            with factory() as s:
                after = s.query(WorldClock).filter_by(
                    world_id=settings.world.world_id).first().game_timestamp

    assert calls, "guard never attempted the lock"
    assert after == before, (
        "ticked despite the lock being held elsewhere")
    assert ticker_mod._TICK_LOCK_FD is fd_before, (
        "conflicting driver stored a lock fd")
