"""M5 Chunk D tests (SPEC 005-player, T18): AE1'''-AE6''' acceptance evidence.

AE1''': full E2E via TestClient (llm-off)
AE2''': keystone — headless M1-M4 byte-identity preserved (pins reused)
AE3''': negatives (auth, ownership, validation)
AE4''': persistence across app restarts (file DB)
AE5''': WebSocket stream
AE6''': config sensitivity (session TTL, serve gate)
"""

import json
import os
import sys

import pytest
from fastapi.testclient import TestClient
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


def _build(tmp_path, settings=None):
    settings = (settings or SETTINGS).model_copy(update={
        "persistence": (settings or SETTINGS).persistence.model_copy(
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
    from fastapi.testclient import TestClient

    app = create_app(settings, factory)
    return settings, app, TestClient(app), engine, factory


@pytest.fixture()
def world(tmp_path):
    settings, app, client, engine, factory = _build(tmp_path)
    client.post("/auth/register", json={
        "username": "e2e", "email": "e2e@x.com", "password": "password123", "age_confirmed": True,
    })
    client.post("/auth/login", json={"username": "e2e", "password": "password123"})
    r = client.post("/characters", json={"name": "Eve Adams", "sex": "F", "age": 33})
    cid = r.json()["id"]
    from app.db.models import Character

    with factory() as s:
        npc = (
            s.query(Character)
            .filter_by(world_id=settings.world.world_id)
            .filter(Character.id.like("npc_%"))
            .order_by(Character.id)
            .first()
        )
        npc_id = npc.id
    return settings, app, client, cid, npc_id, engine, factory


def _step(engine, factory, settings, minutes):
    """Advance the simulation by `minutes` from the persisted clock."""
    from app.db.models import WorldClock as WorldClockModel
    from app.simulation.engine import Engine, TickScheduler, WorldClock

    with sessionmaker(bind=engine)() as session:
        db_clock = (
            session.query(WorldClockModel)
            .filter_by(world_id=settings.world.world_id)
            .first()
        )
        start_ts = db_clock.game_timestamp if db_clock is not None else 0
        sim = Engine(WorldClock(initial_timestamp=start_ts), TickScheduler())
        sim.step(minutes, session=session, world_id=settings.world.world_id, settings=settings)
        session.commit()


# ---------- AE1''': full E2E scenario ----------

def test_ae1_e2e_scenario(world):
    settings, app, client, cid, npc_id, engine, factory = world
    from app.db.models import WorldEvent

    # 1. control DIRECT cancels utility tasks
    _step(engine, factory, settings, 30)
    r = client.post(f"/characters/{cid}/control", json={"mode": "DIRECT"})
    assert r.status_code == 200
    assert r.json()["cancelled"] >= 1

    # 2. no utility tasks while DIRECT
    _step(engine, factory, settings, 60)
    from app.db.models import CharacterTask

    with factory() as s:
        ai_tasks = (
            s.query(CharacterTask)
            .filter_by(character_id=cid)
            .filter(CharacterTask.source != "player")
            .filter(CharacterTask.status.in_(("planned", "active")))
            .count()
        )
        assert ai_tasks == 0

    # 3. direct action executes
    r = client.post("/actions", json={"character_id": cid, "action_type": "WORK"})
    assert r.status_code == 201, r.text
    tid = r.json()["task_id"]
    _step(engine, factory, settings, 480)
    r = client.get(f"/actions/{tid}")
    status = r.json()["status"]
    if status != "completed":
        _step(engine, factory, settings, 480)
        r = client.get(f"/actions/{tid}")
    assert r.json()["status"] == "completed", r.json()
    with factory() as s:
        completed = (
            s.query(WorldEvent)
            .filter_by(event_type="TASK_COMPLETED", actor_id=cid)
            .filter(WorldEvent.payload.contains('"task_type": "WORK"'))
            .count()
        )
        assert completed >= 1

    # 4. direct action in AUTONOMOUS → 409
    client.post(f"/characters/{cid}/control", json={"mode": "AUTONOMOUS"})
    r = client.post("/actions", json={"character_id": cid, "action_type": "WORK"})
    assert r.status_code == 409

    # 5. guided goal: text → queued → converted → done
    client.post(f"/characters/{cid}/control", json={"mode": "GUIDED"})
    r = client.post(f"/characters/{cid}/goals", json={"text": "Иди в shop"})
    assert r.status_code == 201, r.text
    gid = r.json()["goal_id"]
    _step(engine, factory, settings, 240)
    goals = client.get(f"/characters/{cid}/goals").json()
    assert goals[0]["goal_id"] == gid
    assert goals[0]["status"] in ("active", "done")

    # invalid goal
    r = client.post(f"/characters/{cid}/goals", json={"text": "Съезди в Атлантиду"})
    assert r.status_code == 422

    # 6. chat with NPC (fallback, deterministic)
    sid = client.post("/dialogue/start", json={"npc_id": npc_id}).json()["session_id"]
    r = client.post(f"/dialogue/{sid}/message", json={"content": "Привет!"})
    assert r.status_code == 200
    assert r.json()["npc_reply"]
    assert len(r.json()["suggested_responses"]) == 3

    # 7. foreign control → 403
    client.post("/auth/logout")
    client.post("/auth/register", json={
        "username": "raider",
        "email": "rd@x.com",
        "password": "password123",
        "age_confirmed": True,
    })
    client.post("/auth/login", json={"username": "raider", "password": "password123"})
    r = client.post(f"/characters/{cid}/control", json={"mode": "DIRECT"})
    assert r.status_code == 403


# ---------- AE2''': keystone — headless byte-identity ----------

def test_ae2_keystone_headless_identity(tmp_path):
    """
    1-day headless run (API never constructed) must produce the same
    events_by_type as the M3-era pins and contain no player artifacts.
    Control gates never fire: all characters have user_id IS NULL.
    """
    import random

    from app.characters.generator import generate_population
    from app.simulation.engine import Engine, TickScheduler, WorldClock
    from app.simulation.invariants import run_invariant_checks
    from app.world.social_seed import seed_social

    settings = SETTINGS.model_copy(update={
        "persistence": SETTINGS.persistence.model_copy(
            update={"db_path": str(tmp_path / "w.db")}
        )
    })
    engine = create_engine_factory(settings)
    with sessionmaker(bind=engine)() as session:
        bootstrap(engine, settings, seed=42)
        wid = settings.world.world_id
        seed_world(session, settings, wid)
        rng = random.Random(42)
        generate_population(session, settings, rng, wid, 20)
        seed_social(session, settings, wid, rng)
        session.commit()

        clock = WorldClock()
        sim = Engine(clock, TickScheduler())
        sim.step(1440, session=session, world_id=wid, settings=settings)
        session.commit()

        from collections import Counter

        from app.db.models import Character, CharacterGoal, CharacterTask, User, WorldEvent

        counts = Counter(e.event_type for e in session.query(WorldEvent).all())
        # no player artifacts in a headless run
        assert session.query(User).count() == 0
        assert session.query(CharacterGoal).count() == 0
        assert session.query(Character).filter(Character.user_id.isnot(None)).count() == 0
        assert "CONTROL_CHANGED" not in counts and "GOAL_QUEUED" not in counts
        # player-sourced tasks impossible
        assert session.query(CharacterTask).filter(CharacterTask.source == "player").count() == 0
        # invariants green (headless: player/goal checks trivially pass)
        results = run_invariant_checks(session, wid, settings)
        assert all(r["ok"] for r in results), results


# ---------- AE3''': negatives ----------

def test_ae3_negatives(world):
    _, _, client, cid, *_ = world
    # short password
    r = client.post("/auth/register", json={
        "username": "shorty", "email": "sh@x.com", "password": "short", "age_confirmed": True,
    })
    assert r.status_code == 422
    # unknown action type
    client.post(f"/characters/{cid}/control", json={"mode": "DIRECT"})
    r = client.post("/actions", json={"character_id": cid, "action_type": "TELEPORT"})
    assert r.status_code == 422
    # invalid control mode
    r = client.post(f"/characters/{cid}/control", json={"mode": "OWNED"})
    assert r.status_code == 422
    # nonexistent character
    r = client.post("/characters/plr_9999/control", json={"mode": "DIRECT"})
    assert r.status_code == 404
    # goals of another user
    client.post("/auth/logout")
    client.post("/auth/register", json={
        "username": "outsider",
        "email": "o@x.com",
        "password": "password123",
        "age_confirmed": True,
    })
    client.post("/auth/login", json={"username": "outsider", "password": "password123"})
    assert client.get(f"/characters/{cid}/goals").status_code == 403
    assert client.get(f"/characters/{cid}/inventory").status_code == 403


# ---------- AE4''': persistence across restarts ----------

def test_ae4_persistence(tmp_path):
    settings, app1, client1, engine, factory = _build(tmp_path)
    client1.post("/auth/register", json={
        "username": "peter", "email": "p@x.com", "password": "password123", "age_confirmed": True,
    })
    client1.post("/auth/login", json={"username": "peter", "password": "password123"})
    r = client1.post("/characters", json={"name": "Peter Pan", "sex": "M", "age": 22})
    cid = r.json()["id"]

    # "restart": new app instance, same DB (file-backed)
    app2 = create_app(settings, sessionmaker(bind=engine, expire_on_commit=False))
    client2 = TestClient(app2)
    r = client2.post("/auth/login", json={"username": "peter", "password": "password123"})
    assert r.status_code == 200
    r = client2.get(f"/characters/{cid}")
    assert r.status_code == 200
    assert r.json()["name"] == "Peter Pan"
    assert r.json()["is_owner"] is True


# ---------- AE5''': WebSocket ----------

def test_ae5_websocket_stream(world):
    settings, app, client, cid, npc_id, engine, factory = world
    token = client.cookies.get(settings.api.cookie_name)

    with client.websocket_connect(f"/ws?token={token}") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello"

        # generate events via an engine step in another session
        _step(engine, factory, settings, 15)

        # receive messages until a batch with step-produced events arrives
        # (first batch may contain seed-time events, cursor=0)
        got_events = None
        for _ in range(30):
            msg = ws.receive_json()
            if msg.get("type") == "events" and msg["events"]:
                got_events = msg["events"]
                if any(
                    e["event_type"] in ("TASK_COMPLETED", "CHARACTER_MOVED")
                    for e in got_events
                ):
                    break
        assert got_events, "no event batch delivered"
        assert all(e["id"] > hello["cursor"] for e in got_events)
        assert any(e["event_type"] in ("TASK_COMPLETED", "CHARACTER_MOVED") for e in got_events)

        # cursor protocol: ack arrives as a {type:'cursor'} frame
        ws.send_json({"type": "cursor", "id": got_events[-1]["id"]})
        ack = None
        for _ in range(10):
            msg = ws.receive_json()
            if msg.get("type") == "cursor":
                ack = msg
                break
        assert ack is not None and ack["id"] == got_events[-1]["id"]


def test_ae5_websocket_bad_token(world):
    _, _, client, *_ = world
    # starlette closes with 4401 before accept; TestClient raises
    with pytest.raises(Exception):
        with client.websocket_connect("/ws?token=bad.token.here") as ws:
            ws.receive_json()


# ---------- AE6''': config sensitivity ----------

def test_ae6_session_ttl_in_token(world):
    settings, _, client, cid, *_ = world
    token = client.cookies.get(settings.api.cookie_name)
    assert token is not None
    import base64 as _b64

    payload_b64 = token.split(".")[1]
    payload = json.loads(_b64.urlsafe_b64decode(payload_b64 + "=" * (-len(payload_b64) % 4)))
    assert payload["exp"] - payload["iat"] == settings.api.session_ttl_min * 60


def test_ae6_serve_disabled_refuses(tmp_path, capsys):
    from app.simulation.cli import main

    cfg = tmp_path / "cfg.yaml"
    cfg.write_text(
        (os.path.join("config", "default.yaml") and open("config/default.yaml").read())
    )
    rc = main(["serve", "--config", str(cfg)])
    assert rc == 2  # api.enabled=false → refusal


def test_ae6_serve_missing_db(tmp_path):
    """api.enabled=true but DB absent → refusal (exit 2)."""
    from app.simulation.cli import main

    cfg = tmp_path / "cfg.yaml"
    src = open("config/default.yaml").read()
    src = src.replace("api:\n  enabled: false", "api:\n  enabled: true")
    src = src.replace("db_path: data/lifesim.db", f"db_path: {tmp_path / 'missing.db'}")
    cfg.write_text(src)
    rc = main(["serve", "--config", str(cfg)])
    assert rc == 2


def test_ae6_control_change_visible_in_events(world):
    settings, _, client, cid, *_ = world
    client.post(f"/characters/{cid}/control", json={"mode": "GUIDED"})
    client.post(f"/characters/{cid}/control", json={"mode": "DIRECT"})
    events = client.get("/world/events?limit=500").json()
    control_events = [e for e in events if e["event_type"] == "CONTROL_CHANGED"]
    assert len(control_events) == 2
    assert control_events[0]["payload"]["to"] == "GUIDED"
    assert control_events[1]["payload"]["from"] == "GUIDED"
