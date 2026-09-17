"""M5 Chunk B tests (SPEC 005-player, T13): control modes, direct actions, goals."""

import os
import sys

import pytest
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from app.api.app import create_app  # noqa: E402
from app.api.intent import parse_intent  # noqa: E402
from app.config.config import load_config  # noqa: E402
from app.db.models import bootstrap, create_engine_factory  # noqa: E402
from app.simulation.engine import Engine, TickScheduler, WorldClock  # noqa: E402
from app.world.seed_world import seed_world  # noqa: E402

SETTINGS = None


def setup_module(module):
    global SETTINGS
    SETTINGS = load_config("config/default.yaml")


@pytest.fixture()
def world(tmp_path):
    """World with one registered+logged-in player character (plr_0001)."""
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
    from fastapi.testclient import TestClient
    client = TestClient(app)
    client.post("/auth/register", json={
        "username": "pilot", "email": "p@x.com", "password": "password123", "age_confirmed": True,
    })
    client.post("/auth/login", json={"username": "pilot", "password": "password123"})
    r = client.post("/characters", json={"name": "Ivan Petrov", "sex": "M", "age": 30})
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    return settings, app, client, cid, engine, factory


def _engine_step(engine, factory, settings, minutes):
    """Run the simulation engine for `minutes` on the shared DB."""

    with sessionmaker(bind=engine)() as session:
        clock = WorldClock()
        scheduler = TickScheduler()
        sim = Engine(clock, scheduler)
        sim.step(minutes, session=session, world_id=settings.world.world_id, settings=settings)
        session.commit()


# ---------- Intent parser (unit) ----------

def test_parse_intent_travel():
    parsed = parse_intent("Иди в shop немедленно")
    assert parsed["goal_type"] == "travel_to"


def test_parse_intent_acquire():
    parsed = parse_intent("Купи инструменты завтра")
    assert parsed["goal_type"] == "acquire_items"
    assert parsed["params"]["item_key"] == "tool"


def test_parse_intent_social():
    parsed = parse_intent("Поговори с Марией")
    assert parsed["goal_type"] == "socialize_with"


def test_parse_intent_unknown():
    assert parse_intent("стань космонавтом")["goal_type"] is None
    assert parse_intent("")["goal_type"] is None


# ---------- Control flow (R4) ----------

def test_control_mode_invalid_rejected(world):
    _, _, client, cid, *_ = world
    r = client.post(f"/characters/{cid}/control", json={"mode": "GOD"})
    assert r.status_code == 422


def test_control_requires_ownership(world):
    _, _, client, cid, engine, factory = world
    # second user registers, cannot control pilot's character
    client.post("/auth/logout")
    client.post("/auth/register", json={
        "username": "intruder",
        "email": "i@x.com",
        "password": "password123",
        "age_confirmed": True,
    })
    client.post("/auth/login", json={"username": "intruder", "password": "password123"})
    r = client.post(f"/characters/{cid}/control", json={"mode": "DIRECT"})
    assert r.status_code == 403
    r = client.get(f"/characters/{cid}/goals")
    assert r.status_code == 403


def test_control_switch_event_and_state(world):
    _, _, client, cid, engine, factory = world
    r = client.post(f"/characters/{cid}/control", json={"mode": "DIRECT"})
    assert r.status_code == 200
    assert r.json()["control_mode"] == "DIRECT"
    # event logged
    from app.db.models import WorldEvent
    with factory() as session:
        ev = (
            session.query(WorldEvent)
            .filter_by(world_id=SETTINGS.world.world_id, event_type="CONTROL_CHANGED")
            .one()
        )
        assert ev.actor_id == cid
        import json as _json
        payload = _json.loads(ev.payload)
        assert payload["from"] == "AUTONOMOUS" and payload["to"] == "DIRECT"


# ---------- Direct actions (R5) ----------

def test_direct_action_autonomous_rejected(world):
    _, _, client, cid, *_ = world
    r = client.post("/actions", json={"character_id": cid, "action_type": "SLEEP"})
    assert r.status_code == 409


def test_direct_action_unknown_type(world):
    _, _, client, cid, *_ = world
    client.post(f"/characters/{cid}/control", json={"mode": "DIRECT"})
    r = client.post("/actions", json={"character_id": cid, "action_type": "FLY_TO_MOON"})
    assert r.status_code == 422


def test_direct_action_move_precondition(world):
    """BUY_ITEM in DIRECT away from shop: validator ok + MOVE precondition (П1)."""
    _, _, client, cid, *_ = world
    client.post(f"/characters/{cid}/control", json={"mode": "DIRECT"})
    r = client.post("/actions", json={"character_id": cid, "action_type": "BUY_ITEM"})
    assert r.status_code == 201, r.text
    r = client.get(f"/actions/{r.json()['task_id']}")
    assert r.json()["status"] in ("planned", "active")


def test_direct_action_needs_gate_422(world):
    """SLEEP with full energy → validator rejects with reason (П1)."""
    _, _, client, cid, *_ = world
    client.post(f"/characters/{cid}/control", json={"mode": "DIRECT"})
    r = client.post("/actions", json={"character_id": cid, "action_type": "SLEEP"})
    assert r.status_code == 422
    assert "Energy already full" in r.json()["detail"]


def test_direct_action_runs_and_completes(world):
    """DIRECT: SLEEP task issued by player executes via engine (AE1''' fragment)."""
    settings, _, client, cid, engine, factory = world
    client.post(f"/characters/{cid}/control", json={"mode": "DIRECT"})
    r = client.post("/actions", json={"character_id": cid, "action_type": "WORK"})
    assert r.status_code == 201, r.text
    task_id = r.json()["task_id"]

    # before step: planned
    r = client.get(f"/actions/{task_id}")
    assert r.status_code == 200
    assert r.json()["status"] in ("planned", "active")

    _engine_step(engine, factory, settings, 60)

    r = client.get(f"/actions/{task_id}")
    assert r.json()["status"] == "active"

    # sleep duration passes → completed
    _engine_step(engine, factory, settings, 600)
    r = client.get(f"/actions/{task_id}")
    assert r.json()["status"] == "completed"


def test_direct_action_get_other_users_task_403(world):
    _, _, client, cid, *_ = world
    client.post(f"/characters/{cid}/control", json={"mode": "DIRECT"})
    r = client.post("/actions", json={"character_id": cid, "action_type": "WORK"})
    assert r.status_code == 201, r.text
    tid = r.json()["task_id"]
    client.post("/auth/logout")
    client.post("/auth/register", json={
        "username": "snoop", "email": "s@x.com", "password": "password123", "age_confirmed": True,
    })
    client.post("/auth/login", json={"username": "snoop", "password": "password123"})
    r = client.get(f"/actions/{tid}")
    assert r.status_code == 403


# ---------- Intercept: DIRECT cancels AI tasks (§61) ----------

def test_intercept_cancels_active_utility_task(world):
    """AUTONOMOUS char has utility task after a step; → DIRECT cancels it."""
    settings, _, client, cid, engine, factory = world
    # let utility assign a task
    _engine_step(engine, factory, settings, 30)
    from app.db.models import CharacterTask
    with factory() as session:
        tasks = (
            session.query(CharacterTask)
            .filter_by(character_id=cid)
            .filter(CharacterTask.status.in_(("planned", "active")))
            .all()
        )
        assert tasks, "utility should have assigned a task in AUTONOMOUS"

    r = client.post(f"/characters/{cid}/control", json={"mode": "DIRECT"})
    assert r.status_code == 200
    assert r.json()["cancelled"] >= 1

    with factory() as session:
        remaining = (
            session.query(CharacterTask)
            .filter_by(character_id=cid)
            .filter(CharacterTask.status.in_(("planned", "active")))
            .filter(CharacterTask.source != "player")
            .all()
        )
        assert remaining == []


def test_direct_no_new_utility_tasks_but_needs_decay(world):
    """In DIRECT the engine must not assign utility tasks (R1/R4)."""
    settings, _, client, cid, engine, factory = world
    client.post(f"/characters/{cid}/control", json={"mode": "DIRECT"})
    _engine_step(engine, factory, settings, 120)
    from app.db.models import CharacterNeeds, CharacterTask
    with factory() as session:
        tasks = (
            session.query(CharacterTask)
            .filter_by(character_id=cid)
            .filter(CharacterTask.source != "player")
            .all()
        )
        assert tasks == []
        needs = session.query(CharacterNeeds).filter_by(character_id=cid).one()
        assert needs.hunger < 100.0  # needs still tick


# ---------- Goals (R6) ----------

def test_goal_travel_to_flow(world):
    """Text goal → queued → engine converts (GUIDED) → task → done."""
    settings, _, client, cid, engine, factory = world
    client.post(f"/characters/{cid}/control", json={"mode": "GUIDED"})
    r = client.post(f"/characters/{cid}/goals", json={"text": "Иди в shop"})
    assert r.status_code == 201, r.text
    goal = r.json()
    assert goal["goal_type"] == "travel_to"
    assert goal["status"] == "queued"
    assert goal["params"]["destination_location_id"] > 0

    _engine_step(engine, factory, settings, 120)

    r = client.get(f"/characters/{cid}/goals")
    goals = r.json()
    assert goals[0]["status"] in ("active", "done"), goals
    # GOAL_QUEUED event exists
    from app.db.models import WorldEvent
    with factory() as session:
        ev = (
            session.query(WorldEvent)
            .filter_by(event_type="GOAL_QUEUED")
            .filter(WorldEvent.payload.contains(f'"goal_id": {goal["goal_id"]}'))
            .first()
        )
        assert ev is not None


def test_goal_invalid_destination_422(world):
    _, _, client, cid, *_ = world
    client.post(f"/characters/{cid}/control", json={"mode": "GUIDED"})
    r = client.post(f"/characters/{cid}/goals", json={"text": "Съезди в Атлантиду"})
    assert r.status_code == 422


def test_goal_unknown_intent_422(world):
    _, _, client, cid, *_ = world
    r = client.post(f"/characters/{cid}/goals", json={"text": "стань космонавтом"})
    assert r.status_code == 422


def test_goal_autonomous_ownership_ok_but_not_converted(world):
    """In AUTONOMOUS goals queue but the engine ignores them (R1)."""
    settings, _, client, cid, engine, factory = world
    r = client.post(f"/characters/{cid}/goals", json={"text": "Иди в shop"})
    assert r.status_code == 201
    _engine_step(engine, factory, settings, 60)
    r = client.get(f"/characters/{cid}/goals")
    assert r.json()[0]["status"] == "queued"


def test_goal_acquire_items_flow(world):
    """acquire_items converts to BUY chain (MOVE+BUY_ITEM) in GUIDED."""
    settings, _, client, cid, engine, factory = world
    client.post(f"/characters/{cid}/control", json={"mode": "GUIDED"})
    r = client.post(f"/characters/{cid}/goals", json={"text": "Купи еду"})
    assert r.status_code == 201, r.text
    assert r.json()["goal_type"] == "acquire_items"
    _engine_step(engine, factory, settings, 240)
    r = client.get(f"/characters/{cid}/goals")
    assert r.json()[0]["status"] in ("active", "done"), r.json()
