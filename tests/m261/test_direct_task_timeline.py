"""#261 [player][lifecycle] DIRECT player tasks start at max(last_end, now).

Issue #261: a user-linked DIRECT character's newly activated task was dated
from the stale personal timeline (max(ends_at) of completed history) — a
command issued at world time T produced TASK_COMPLETED/consumption events
dated before T, and the idle gap never closed (each command advanced the
personal timeline by one task duration).

Fix: in _advance_character, for owned DIRECT characters the activation start
is clamped to max(start_at, now), immediately before activate(). Everything
else keeps historical chaining semantics:

- genesis reconstruction (fresh character, last_end=0) is unchanged;
- NPC/AUTONOMOUS chain dating (successor at predecessor's ends_at) is
  unchanged, even across a large observation jump;
- unowned characters keep historical timing even with unusual mode fields
  (ownership isolation, specs/005-player/spec.md:36);
- activate() itself is untouched (contract: use the supplied timestamp).

Known intended consequence (pinned in test_restore_accrues_from_activation):
a starving DIRECT player's recovery accrues over the task's elapsed minutes
from activation instead of landing retroactively at the first observing
tick; total recovery duration is unchanged (ends_at = started_at + duration).

Style: m5 player-test fixture (real default.yaml, tmp db, TestClient without
a context manager → the lifespan tick driver never runs). Per the m152
lesson the world is additionally paused in the DB during setup, so no tick
can touch the character while it is being arranged; all observation happens
through explicit progress_tick(N) calls, so every asserted timestamp is
deterministic.
"""

import os
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from app.actions.lifecycle import enqueue_task, progress_tick  # noqa: E402
from app.api.app import create_app  # noqa: E402
from app.config.config import load_config  # noqa: E402
from app.db.models import (  # noqa: E402
    Character,
    CharacterNeeds,
    CharacterTask,
    WorldClock,
    WorldEvent,
    bootstrap,
    create_engine_factory,
)
from app.world.seed_world import seed_world  # noqa: E402

SETTINGS = None
EAT_DURATION = 15  # config/default.yaml: actions.EAT.duration_minutes
EAT_HUNGER_RATE = 5.0  # config/default.yaml: needs.recovery_rates.EAT.hunger


def setup_module(module):
    global SETTINGS
    SETTINGS = load_config("config/default.yaml")


@pytest.fixture()
def world(tmp_path):
    """Player world: registered+logged-in user with one character."""
    settings = SETTINGS.model_copy(update={
        "persistence": SETTINGS.persistence.model_copy(
            update={"db_path": str(tmp_path / "w.db")}
        )
    })
    engine = create_engine_factory(settings)
    with sessionmaker(bind=engine)() as session:
        bootstrap(engine, settings, seed=42)
        seed_world(session, settings, settings.world.world_id)
        session.commit()
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app(settings, factory)
    client = TestClient(app)
    client.post("/auth/register", json={
        "username": "pilot261", "email": "p261@x.com",
        "password": "password123", "age_confirmed": True,
    })
    client.post("/auth/login", json={"username": "pilot261", "password": "password123"})
    r = client.post("/characters", json={"name": "Ivan Direct", "sex": "M", "age": 30})
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    # m152 lesson: freeze the world for the whole setup — no tick may touch
    # the character while it is being arranged (this fixture has no live
    # ticker anyway; the pause makes that guarantee explicit).
    _set_paused(factory, settings.world.world_id, True)
    return settings, client, cid, factory


# ---------- helpers ----------

def _to_direct(client, cid):
    r = client.post(f"/characters/{cid}/control", json={"mode": "DIRECT"})
    assert r.status_code == 200, r.text


def _enqueue(client, cid, action_type):
    r = client.post("/actions", json={
        "character_id": cid, "action_type": action_type,
    })
    assert r.status_code == 201, r.text
    return int(r.json()["task_id"])


def _tick(factory, settings, world_id, ts):
    """Observe the world at an explicit game timestamp (deterministic)."""
    with factory() as session:
        progress_tick(session, world_id, ts, settings)
        session.commit()


def _set_paused(factory, world_id, paused):
    with factory() as session:
        row = session.query(WorldClock).filter_by(world_id=world_id).one()
        row.is_paused = paused
        session.commit()


def _set_clock(factory, world_id, ts):
    with factory() as session:
        row = session.query(WorldClock).filter_by(world_id=world_id).one()
        row.game_timestamp = ts
        session.commit()


def _set_hunger(factory, cid, value):
    with factory() as session:
        row = session.query(CharacterNeeds).filter_by(character_id=cid).one()
        row.hunger = value
        session.commit()


def _hunger(factory, cid):
    with factory() as session:
        return session.query(CharacterNeeds).filter_by(character_id=cid).one().hunger


def _task(factory, task_id):
    with factory() as session:
        return session.get(CharacterTask, task_id)


def _assert_task(factory, task_id, **expected):
    task = _task(factory, task_id)
    for field, value in expected.items():
        actual = getattr(task, field)
        assert actual == value, f"{field}: expected {value}, got {actual}"
    return task


def _first_task_id(factory, character_id):
    with factory() as session:
        row = (
            session.query(CharacterTask)
            .filter_by(character_id=character_id)
            .order_by(CharacterTask.id)
            .first()
        )
        return row.id


def _max_event_id(factory):
    with factory() as session:
        row = (
            session.query(WorldEvent)
            .order_by(WorldEvent.id.desc())
            .first()
        )
        return row.id if row else 0


def _make_unowned_character(factory, world_id, char_id, control_mode="AUTONOMOUS"):
    """Insert an unowned character (user_id None) with a needs row."""
    with factory() as session:
        template = (
            session.query(Character)
            .filter(Character.user_id.isnot(None))
            .first()
        )
        npc = Character(
            id=char_id, world_id=world_id, type="npc",
            first_name="Chain", last_name="Npc",
            birth_date="1996-01-01", age=30, sex="M",
            location_id=template.location_id,
            home_location_id=template.home_location_id,
            created_at=0, updated_at=0,
            user_id=None, control_mode=control_mode,
        )
        session.add(npc)
        session.add(CharacterNeeds(
            character_id=char_id,
            hunger=50.0, thirst=50.0, energy=100.0, hygiene=100.0,
            comfort=100.0, social=50.0, safety=100.0, entertainment=50.0,
            privacy=50.0, updated_at=0,
        ))
        session.commit()


# ---------- a) THE #261 red test ----------

def test_direct_command_after_idle_starts_at_now(world):
    """Stale completed history + command at world time T → task dated >= T.

    On base (d2abd93) the task activated at the stale last_end (15) and even
    completed retroactively at 30 while the world stood at 2005.
    """
    settings, client, cid, factory = world
    wid = settings.world.world_id
    _to_direct(client, cid)

    # Stale completed history: a real EAT at [0, 15) leaves last_end = 15.
    _set_hunger(factory, cid, 50.0)
    first = _enqueue(client, cid, "EAT")
    _tick(factory, settings, wid, 0)
    _tick(factory, settings, wid, 15)
    _assert_task(factory, first, status="completed", started_at=0, ends_at=15)

    # The player idles; the world clock jumps far past the personal timeline.
    T = 2000
    _set_clock(factory, wid, T)
    _set_hunger(factory, cid, 10.0)
    task_id = _enqueue(client, cid, "EAT")  # command issued at world time T
    watermark = _max_event_id(factory)

    # Observed at N > T: the task starts at the observation time, not at the
    # stale last_end.
    N = T + 5
    _tick(factory, settings, wid, N)
    _assert_task(
        factory, task_id,
        status="active", started_at=max(15, N), ends_at=N + EAT_DURATION,
    )

    # Completion lands at ends_at (>= T) and every event logged since the
    # command is dated >= T — the feed never runs backwards.
    _tick(factory, settings, wid, N + EAT_DURATION)
    _assert_task(
        factory, task_id,
        status="completed", completed_at=N + EAT_DURATION,
    )
    with factory() as session:
        late = (
            session.query(WorldEvent)
            .filter(WorldEvent.id > watermark, WorldEvent.actor_id == cid)
            .all()
        )
        assert late, "completion must log events"
        assert all(e.game_timestamp >= T for e in late), [
            (e.event_type, e.game_timestamp) for e in late
        ]


# ---------- b) a second idle gap does not recur ----------

def test_second_idle_gap_does_not_recur(world):
    """Complete a task, idle with a large clock jump, command again: the new
    task starts at the new now — the gap does not recur per command."""
    settings, client, cid, factory = world
    wid = settings.world.world_id
    _to_direct(client, cid)

    # First command across an idle gap (the #261 scenario, fixed).
    _set_hunger(factory, cid, 50.0)
    first = _enqueue(client, cid, "EAT")
    _tick(factory, settings, wid, 0)
    _tick(factory, settings, wid, 15)
    _assert_task(factory, first, status="completed", completed_at=15)
    _set_clock(factory, wid, 2000)
    _set_hunger(factory, cid, 10.0)
    second = _enqueue(client, cid, "EAT")
    _tick(factory, settings, wid, 2005)
    _tick(factory, settings, wid, 2020)
    _assert_task(factory, second, status="completed", completed_at=2020)

    # Second idle gap, then a new command: starts at the new now.
    _set_clock(factory, wid, 5000)
    _set_hunger(factory, cid, 10.0)
    task_id = _enqueue(client, cid, "EAT")
    _tick(factory, settings, wid, 5003)
    _assert_task(
        factory, task_id,
        status="active", started_at=5003, ends_at=5003 + EAT_DURATION,
    )


# ---------- c) NPC chain dating is unchanged ----------

def test_npc_chain_dates_from_predecessor_end(world):
    """Unowned AUTONOMOUS character across a large observation jump: the
    successor starts at the predecessor's ends_at (chain_time semantics),
    NOT at now — historical chaining must survive the DIRECT clamp."""
    settings, client, cid, factory = world
    wid = settings.world.world_id
    _make_unowned_character(factory, wid, "npc_m261_chain")

    with factory() as session:
        npc = session.get(Character, "npc_m261_chain")
        enqueue_task(session, wid, npc, "EAT", "utility", {}, 0, settings)
        enqueue_task(session, wid, npc, "EAT", "utility", {}, 0, settings)
        session.commit()

    _tick(factory, settings, wid, 3000)

    with factory() as session:
        tasks = (
            session.query(CharacterTask)
            .filter_by(character_id="npc_m261_chain")
            .order_by(CharacterTask.id)
            .all()
        )
        first, second = tasks[0], tasks[1]
        assert (first.started_at, first.ends_at) == (0, EAT_DURATION)
        # Successor chains at the predecessor's ends_at, not at now (3000).
        assert second.started_at == first.ends_at == EAT_DURATION
        assert second.ends_at == EAT_DURATION * 2


# ---------- d) genesis unchanged; owned DIRECT first task at now ----------

def test_genesis_unowned_first_activation_dates_from_zero(world):
    """A fresh unowned character's first activation still reconstructs its
    schedule from zero (genesis), even when first observed far later."""
    settings, client, cid, factory = world
    wid = settings.world.world_id
    _make_unowned_character(factory, wid, "npc_m261_gen")

    with factory() as session:
        npc = session.get(Character, "npc_m261_gen")
        enqueue_task(session, wid, npc, "EAT", "utility", {}, 0, settings)
        session.commit()

    _tick(factory, settings, wid, 777)

    planned_id = _first_task_id(factory, "npc_m261_gen")
    _assert_task(
        factory, planned_id,
        status="completed", started_at=0, ends_at=EAT_DURATION,
        completed_at=EAT_DURATION,
    )


def test_fresh_direct_player_first_task_activates_at_now(world):
    """A fresh owned DIRECT character's first task activates at now (no
    genesis backdating to 0, no deadlock)."""
    settings, client, cid, factory = world
    wid = settings.world.world_id
    client.post("/auth/logout")
    client.post("/auth/register", json={
        "username": "fresh261", "email": "f261@x.com",
        "password": "password123", "age_confirmed": True,
    })
    client.post("/auth/login", json={"username": "fresh261", "password": "password123"})
    r = client.post("/characters", json={"name": "Fresh Direct", "sex": "M", "age": 28})
    assert r.status_code == 201, r.text
    fresh_cid = r.json()["id"]
    _to_direct(client, fresh_cid)

    _set_clock(factory, wid, 3000)
    _set_hunger(factory, fresh_cid, 10.0)
    task_id = _enqueue(client, fresh_cid, "EAT")

    N = 3005
    _tick(factory, settings, wid, N)
    _assert_task(
        factory, task_id,
        status="active", started_at=N, ends_at=N + EAT_DURATION,
    )

    # No deadlock: the task completes at its own ends_at on a later tick.
    _tick(factory, settings, wid, N + EAT_DURATION)
    _assert_task(factory, task_id, status="completed", completed_at=N + EAT_DURATION)


# ---------- e) restore accrues from activation ----------

def test_restore_accrues_from_activation(world):
    """With the clamp, recovery accrues over the task's elapsed minutes from
    activation: zero at the activation tick, proportional partway, the full
    configured duration eventually; repeated same-timestamp ticks are
    idempotent via the restored_until marker."""
    settings, client, cid, factory = world
    wid = settings.world.world_id
    _to_direct(client, cid)

    # Stale history → last_end = 15; command at T = 1000.
    _set_hunger(factory, cid, 50.0)
    _enqueue(client, cid, "EAT")
    _tick(factory, settings, wid, 0)
    _tick(factory, settings, wid, 15)

    T = 1000
    _set_clock(factory, wid, T)
    _set_hunger(factory, cid, 10.0)
    task_id = _enqueue(client, cid, "EAT")

    N = T + 5
    _tick(factory, settings, wid, N)  # activation tick
    assert _hunger(factory, cid) == 10.0, (
        "restore window starts at activation: zero recovery at the activation tick"
    )
    _tick(factory, settings, wid, N)  # repeated same-timestamp tick
    assert _hunger(factory, cid) == 10.0, "same-timestamp tick must be idempotent"

    _tick(factory, settings, wid, N + 7)  # partway
    assert _hunger(factory, cid) == 10.0 + EAT_HUNGER_RATE * 7
    _tick(factory, settings, wid, N + 7)  # idempotent at the same timestamp
    assert _hunger(factory, cid) == 10.0 + EAT_HUNGER_RATE * 7

    _tick(factory, settings, wid, N + EAT_DURATION)  # full duration elapsed
    assert _hunger(factory, cid) == 10.0 + EAT_HUNGER_RATE * EAT_DURATION
    _assert_task(
        factory, task_id,
        status="completed", completed_at=N + EAT_DURATION,
    )


# ---------- f) ownership isolation: unowned DIRECT-labelled character ----------

def test_unowned_direct_label_keeps_historical_timing(world):
    """user_id None + control_mode "DIRECT" (unusual mode fields on an
    unowned row) keeps historical timing — the clamp is ownership-gated
    (specs/005-player/spec.md:36)."""
    settings, client, cid, factory = world
    wid = settings.world.world_id
    _make_unowned_character(factory, wid, "npc_m261_dir", control_mode="DIRECT")

    with factory() as session:
        npc = session.get(Character, "npc_m261_dir")
        # Stale completed history (ends 15) + a planned successor.
        session.add(CharacterTask(
            character_id=npc.id, priority=0, task_type="EAT",
            status="completed", source="utility", parameters="{}",
            created_at=0, started_at=0, ends_at=15, completed_at=15,
        ))
        planned = enqueue_task(session, wid, npc, "EAT", "utility", {}, 0, settings)
        session.commit()
        planned_id = planned.id

    _tick(factory, settings, wid, 900)

    # Historical chaining: the planned task starts at the predecessor's
    # ends_at (15), NOT at the observation time (900).
    _assert_task(
        factory, planned_id,
        status="completed", started_at=15, ends_at=15 + EAT_DURATION,
        completed_at=15 + EAT_DURATION,
    )
