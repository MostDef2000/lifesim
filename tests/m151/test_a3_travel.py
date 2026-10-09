"""#82 [alpha-v1][A3] Travel Slice 1 — authoritative MOVE + free-text destination.

The issue body is the spec of record (A1/A2 precedent: no specs/ dirs). Covers:

- MOVE-fix regression: POST /actions MOVE must validate the route at creation
  and, at task completion, actually set the character's location and broadcast
  CHARACTER_MOVED (payload: character_id, from_location, to_location,
  arrived_at). On base this is a server-side no-op: the validator is a stub,
  the task carries no path, completion mutates nothing (test MUST fail on base).
- impossible route → 422 "impossible route", no mutation, no task row.
- POST /travel/plan {text}: deterministic grounding (exact display name →
  case-insensitive substring → config aliases; LLM disabled in this slice);
  unique → the SAME authoritative MOVE; ambiguous → 422 with options;
  unknown → 422 "unknown destination".
- UI byte-pins on the Living Map additions (el() only — innerHTML stays
  banned) and the m146 first-880px/520px media blocks staying byte-exact.
"""

import json
import os
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from app.actions.lifecycle import progress_tick  # noqa: E402
from app.api.app import create_app  # noqa: E402
from app.config.config import load_config  # noqa: E402
from app.db.models import (  # noqa: E402
    Character,
    CharacterTask,
    Location,
    WorldEvent,
    bootstrap,
    create_engine_factory,
)
from app.events.events import EventType  # noqa: E402
from app.world.seed_world import find_path, seed_world  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
APP_JS = os.path.join(ROOT, "backend/app/web/app.js")
APP_CSS = os.path.join(ROOT, "backend/app/web/style.css")

PW = "Correct-Horse-9!"
SETTINGS = None


def setup_module(module):
    global SETTINGS
    SETTINGS = load_config("config/default.yaml")


@pytest.fixture()
def world(tmp_path):
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
    return settings, app, TestClient(app), factory


def _register_and_login(client, username):
    r = client.post("/auth/register", json={
        "username": username, "email": f"{username}@example.com",
        "password": PW, "age_confirmed": True,
    })
    assert r.status_code == 201, r.text
    uid = r.json()["id"]
    r = client.post("/auth/login", json={"username": username, "password": PW})
    assert r.status_code == 200, r.text
    return uid


def _make_character(client, name):
    r = client.post("/characters", json={"name": name, "sex": "M", "age": 30})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _to_direct(client, cid):
    """Players default to AUTONOMOUS (§61) — direct actions need DIRECT."""
    r = client.post(f"/characters/{cid}/control", json={"mode": "DIRECT"})
    assert r.status_code == 200, r.text


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _loc_by_type(factory, world_id, loc_type):
    with factory() as session:
        row = (
            session.query(Location)
            .filter_by(world_id=world_id, type=loc_type)
            .order_by(Location.id)
            .first()
        )
        loc_id = row.id if row is not None else None
    return loc_id


# ---------- a) MOVE-fix regression: arrival + CHARACTER_MOVED ----------

def test_move_task_completes_with_arrival_and_event(world):
    """MUST FAIL ON BASE: the MOVE validator is a stub, so the task completes
    without moving the character and without CHARACTER_MOVED."""
    settings, app, client, factory = world
    wid = settings.world.world_id
    _register_and_login(client, "mover82")
    cid = _make_character(client, "Mover Test")
    _to_direct(client, cid)

    with factory() as session:
        char = session.get(Character, cid)
        from_loc_id = char.location_id  # auto-assigned free house
    settlement_id = _loc_by_type(factory, wid, "settlement")
    assert from_loc_id != settlement_id

    r = client.post("/actions", json={
        "character_id": cid, "action_type": "MOVE",
        "params": {"location_id": settlement_id},
    })
    assert r.status_code == 201, r.text
    task_id = r.json()["task_id"]

    # Run the engine the way the tick loop does: activate + complete.
    with factory() as session:
        progress_tick(session, wid, 10_000, settings)
        session.commit()

    with factory() as session:
        char = session.get(Character, cid)
        assert char.location_id == settlement_id, (
            "completed MOVE must leave the character at the destination"
        )
        task = session.get(CharacterTask, int(task_id))
        assert task is not None and task.status == "completed"

        event = (
            session.query(WorldEvent)
            .filter_by(world_id=wid, event_type=EventType.CHARACTER_MOVED.value,
                       actor_id=cid)
            .order_by(WorldEvent.id.desc())
            .first()
        )
        assert event is not None, "CHARACTER_MOVED must be broadcast on arrival"
        payload = json.loads(event.payload) if event.payload else {}
        assert payload.get("character_id") == cid
        assert payload.get("from_location") == from_loc_id
        assert payload.get("to_location") == settlement_id
        assert "arrived_at" in payload


# ---------- b) impossible route: 422, no mutation, no task ----------

def test_move_impossible_route_is_422_without_mutation(world):
    settings, app, client, factory = world
    wid = settings.world.world_id
    _register_and_login(client, "caver82")
    cid = _make_character(client, "Cave Tester")
    _to_direct(client, cid)

    # A location with no location_links: unreachable from anywhere.
    with factory() as session:
        cave = Location(world_id=wid, type="cave", name="Far Cave", capacity=5)
        session.add(cave)
        session.commit()
        cave_id = cave.id
        char = session.get(Character, cid)
        before = char.location_id

    r = client.post("/actions", json={
        "character_id": cid, "action_type": "MOVE",
        "params": {"location_id": cave_id},
    })
    assert r.status_code == 422, r.text
    assert "impossible route" in str(r.json()["detail"])

    with factory() as session:
        char = session.get(Character, cid)
        assert char.location_id == before, "no state mutation on 422"
        assert session.query(CharacterTask).filter_by(
            character_id=cid).count() == 0, "no task row on 422"


# ---------- c) /travel/plan success (alias grounding «причал») ----------

def test_travel_plan_grounds_alias_and_creates_move(world):
    settings, app, client, factory = world
    wid = settings.world.world_id
    _register_and_login(client, "sailer82")
    cid = _make_character(client, "Sailer Test")
    _to_direct(client, cid)

    pier_id = _loc_by_type(factory, wid, "pier")
    with factory() as session:
        char = session.get(Character, cid)
        from_loc_id = char.location_id
        path, total_min = find_path(session, wid, from_loc_id, pier_id)
    assert path, "pier must be reachable from the starting house"

    r = client.post("/travel/plan", json={"text": "идти к причалу"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["location_id"] == pier_id
    assert body["name"] == "Main Pier"
    assert body["travel_minutes"] == total_min
    assert body["task_id"]

    with factory() as session:
        task = session.get(CharacterTask, int(body["task_id"]))
        assert task is not None
        assert task.task_type == "MOVE"
        assert task.source == "player"
        params = json.loads(task.parameters)
        assert params["path"][-1] == pier_id
        assert params["total_minutes"] == total_min


def test_travel_plan_exact_display_name(world):
    settings, app, client, factory = world
    _register_and_login(client, "exact82")
    cid = _make_character(client, "Exact Tester")
    _to_direct(client, cid)
    r = client.post("/travel/plan", json={"text": "Main Pier"})
    assert r.status_code == 201, r.text
    assert r.json()["name"] == "Main Pier"


# ---------- d) ambiguous: 422 with options, no task ----------

def test_travel_plan_ambiguous_returns_options_without_task(world):
    settings, app, client, factory = world
    _register_and_login(client, "ambig82")
    cid = _make_character(client, "Ambig Tester")
    _to_direct(client, cid)

    # Substring grounding matches full display names inside the text —
    # "Water Well" and "Community Kitchen" are both canonical names from
    # config/default.yaml, so this text must resolve to 2+ candidates and
    # 422 without a silent choice.
    r = client.post("/travel/plan", json={
        "text": "между Water Well и Community Kitchen"})
    assert r.status_code == 422, r.text
    body = r.json()
    assert body["detail"] == "ambiguous destination"
    options = body["options"]
    assert isinstance(options, list) and len(options) >= 2
    assert "Water Well" in options
    assert "Community Kitchen" in options

    with factory() as session:
        assert session.query(CharacterTask).filter_by(
            character_id=cid).count() == 0, "no silent choice, no task"


# ---------- e) unknown: 422 "unknown destination", no task ----------

def test_travel_plan_unknown_destination(world):
    settings, app, client, factory = world
    _register_and_login(client, "lost82")
    cid = _make_character(client, "Lost Tester")
    _to_direct(client, cid)

    r = client.post("/travel/plan", json={"text": "к библиотеке"})
    assert r.status_code == 422, r.text
    assert r.json() == {"detail": "unknown destination"}

    with factory() as session:
        assert session.query(CharacterTask).filter_by(
            character_id=cid).count() == 0


def test_travel_plan_requires_auth_and_character(world):
    settings, app, client, factory = world
    # Unauthenticated → 401.
    r = client.post("/travel/plan", json={"text": "Main Pier"})
    assert r.status_code == 401
    # Authenticated but characterless → 422 (existing convention).
    _register_and_login(client, "bare82")
    r = client.post("/travel/plan", json={"text": "Main Pier"})
    assert r.status_code == 422


# ---------- f) direct map-click path still 201s ----------

def test_direct_map_click_move_still_creates_task(world):
    settings, app, client, factory = world
    wid = settings.world.world_id
    _register_and_login(client, "clicker82")
    cid = _make_character(client, "Clicker Tester")
    _to_direct(client, cid)

    settlement_id = _loc_by_type(factory, wid, "settlement")
    r = client.post("/actions", json={
        "character_id": cid, "action_type": "MOVE",
        "params": {"location_id": settlement_id},
    })
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["task_type"] == "MOVE"
    r = client.get(f"/actions/{body['task_id']}")
    assert r.status_code == 200
    assert r.json()["task_type"] == "MOVE"


# ---------- g) UI byte-pins (Living Map additions) ----------

def test_ui_travel_bar_pins_without_damaging_existing_pins():
    js = _read(APP_JS)
    css = _read(APP_CSS)
    # A3 additions — pin exactly what this slice implements.
    assert js.count('"Идти"') == 1
    assert js.count("/travel/plan") == 1
    assert js.count('"Осмотреть"') == 1
    # #86 (A4) supersedes the A3 placeholder: the locationView «Осмотреть»
    # button now runs the real inspect action (inspectSurroundings), so the
    # placeholder toast string is gone — pin updated 1 → 0 by A4.
    assert js.count("Осмотр — в следующем срезе") == 0
    assert js.count("Уточняю маршрут") == 1
    # Carry-forward pins (m146/m149/m150 lineage) must survive.
    assert js.count("innerHTML") == 0
    assert js.count('location.hash = "#/profile";') == 3
    assert js.count("S.authProbed = false") == 2
    assert js.count('"Выйти"') == 1
    assert js.count('"Удалить аккаунт"') == 1
    assert js.count("/auth/account/delete") == 1
    # m146: first 880px/520px media blocks byte-exact.
    grid_media = css.split("@media (max-width: 880px) {", 1)[1].split("\n}", 1)[0]
    assert grid_media == "\n  .grid { grid-template-columns: 1fr; }"
    small_media = css.split("@media (max-width: 520px) {", 1)[1].split("\n}", 1)[0]
    assert small_media == (
        "\n  .anchor .dot { width: 7px; height: 7px; }"
        "\n  .anchor .map-label { font-size: 9px; }"
    )


# ---------- h) grounding is deterministic (LLM disabled) ----------

def test_travel_plan_is_deterministic_without_llm(world):
    assert SETTINGS.llm.enabled is False, "slice 1 is deterministic only"
    settings, app, client, factory = world
    assert settings.llm.enabled is False
    _register_and_login(client, "det82")
    cid = _make_character(client, "Det Tester")
    _to_direct(client, cid)
    r = client.post("/travel/plan", json={"text": "идти к причалу"})
    assert r.status_code == 201, r.text
    assert r.json()["name"] == "Main Pier"


def test_move_without_location_id_is_422(world):
    """Review F1: destination-less MOVE (client POST with empty params) used
    to enqueue a silent no-op task — reject it, don't accept."""
    _, _, client, factory = world
    _register_and_login(client, "nof138")
    cid = _make_character(client, "Nof Dest")
    _to_direct(client, cid)
    r = client.post("/actions", json={
        "character_id": cid, "action_type": "MOVE", "params": {},
    })
    assert r.status_code == 422, r.text
    assert "location_id required" in r.text
    with factory() as s:
        assert s.query(CharacterTask).count() == 0


def test_move_to_current_location_is_422(world):
    """Review F5: same-location MOVE would produce a 0-minute no-op with a
    spurious CHARACTER_MOVED — the server guards what the client guards."""
    settings, _, client, factory = world
    _register_and_login(client, "here138")
    cid = _make_character(client, "Here Already")
    _to_direct(client, cid)
    with factory() as s:
        loc_id = s.get(Character, cid).location_id
    r = client.post("/actions", json={
        "character_id": cid, "action_type": "MOVE",
        "params": {"location_id": loc_id},
    })
    assert r.status_code == 422, r.text
    assert "already at destination" in r.text
