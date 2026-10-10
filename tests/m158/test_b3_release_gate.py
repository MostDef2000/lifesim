"""Block 3 (FINAL) — #93 [alpha-v1][A11] Alpha resilience + E2E release gate.

The issue body is the spec of record (gh issue view 93); the block plan is
#83 issuecomment-6089997917 (Блок 3 → #93, executed last as the gate).

Covers:

#93 resilience:
- Master journey at API level (deterministic: llm.enabled=False,
  visual.enabled=False — both OFF in default config): register → login →
  create character → DIRECT → locations → MOVE (authoritative POST /actions,
  completed by the real tick engine) → arrival (CHARACTER_MOVED + occupants)
  → scene act look (committed-state descriptor) → dialogue start + message
  (fallback source) → inventory read → tasks read → desire set
  (relationship) → opportunities read. Every step asserts the authoritative
  result.
- LLM-down smoke: a full 3-exchange dialogue with the LLM disabled stays
  playable (source="fallback" every turn, non-empty replies, history
  readable) and is journal-only (zero WorldEvents — dialogue never mutates
  the world).
- Flux-down smoke: with visual.enabled=False the committed gameplay result
  (scene act look descriptor) still serves; POST /visual/scenes degrades to
  503 without mutating anything; the client pins the fallback state machine
  (S.scene state:"fallback" + «Повторить осмотр» retry affordance + toast).
- Transient failure handling pins: every alpha-critical catch path in
  app.js (world load, chat load, inventory load, tasks load, scene act,
  desire save, travel) shows an explicit retry hint, does NOT destroy
  in-progress typed input, and leaves the previous screen state rendered
  (byte-pins; the chat draft pattern from m156 stays intact).
- Low graphics + reduced motion smoke (byte-pins): S.mapReducedMotion is
  initialised from prefers-reduced-motion, both scroll behaviours honour it,
  and the mapQuality "low-mobile" paths exist (NPC clustering + bounded
  event markers) with the scene fallback usable. NOTE: the BROWSER-side
  smoke (real prefers-reduced-motion emulation, rendered low-mobile map,
  live click-through of the master journey) is the ORCHESTRATOR's pass —
  this worker provides API-level journey + pins only (no servers/browsers).
- Journey pins: the master-journey route chain exists in app.js — hash
  routes #/world→#/chat→#/inventory→#/tasks→#/profile all wired and the
  topbar buttons rendered for each.

No feature expansion: no new DB tables/models (tests/db/test_bootstrap.py
pins 43 tables), no gameplay mechanics, no visual redesign.
"""

import json
import os
import random
import sys

import pytest
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from fastapi.testclient import TestClient  # noqa: E402

from app.actions.lifecycle import progress_tick  # noqa: E402
from app.api.app import create_app  # noqa: E402
from app.config.config import load_config  # noqa: E402
from app.db.models import (  # noqa: E402
    Character,
    CharacterTask,
    WorldClock,
    WorldEvent,
    bootstrap,
    create_engine_factory,
)
from app.events.events import EventType  # noqa: E402
from app.world.seed_world import seed_world  # noqa: E402
from app.world.social_seed import seed_social  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
APP_JS = os.path.join(ROOT, "backend/app/web/app.js")

PW = "Correct-Horse-9!"
SETTINGS = None


def setup_module(module):
    global SETTINGS
    SETTINGS = load_config("config/default.yaml")


def make_settings(tmp_path):
    # #93 gate runs the deterministic alpha profile: llm.enabled=False and
    # visual.enabled=False ship OFF in default config — assert it below so a
    # future config flip cannot silently invalidate the smoke semantics.
    return SETTINGS.model_copy(update={
        "persistence": SETTINGS.persistence.model_copy(
            update={"db_path": str(tmp_path / "w.db")}
        ),
    })


def build_world(settings, seed=42):
    engine = create_engine_factory(settings)
    with sessionmaker(bind=engine)() as session:
        bootstrap(engine, settings, seed=seed)
        seed_world(session, settings, settings.world.world_id)
        rng = random.Random(seed)
        from app.characters.generator import generate_population

        generate_population(session, settings, rng, settings.world.world_id, 20)
        seed_social(session, settings, settings.world.world_id, rng)
        session.commit()
    return engine


def _register_and_login(client, username):
    r = client.post("/auth/register", json={
        "username": username, "email": f"{username}@x.com",
        "password": PW, "age_confirmed": True,
    })
    assert r.status_code == 201, r.text
    r = client.post("/auth/login", json={"username": username, "password": PW})
    assert r.status_code == 200, r.text


def _travel_to_npc(client, factory, settings, cid):
    """MOVE (authoritative POST /actions + real tick) to a location that has
    an NPC; returns (npc_id, target_loc, task_id). The player starts
    AUTONOMOUS-side of town in the seed — the dialogue smoke needs an NPC at
    the location."""
    wid = settings.world.world_id
    locs = client.get("/locations").json()
    start_loc = client.get(f"/characters/{cid}").json()["location_id"]
    target = next(
        (loc for loc in locs if loc["id"] != start_loc
         and any(o["kind"] == "npc" for o in loc["occupants"])),
        None)
    assert target is not None, "seeded island must have an NPC somewhere"
    r = client.post("/actions", json={
        "character_id": cid, "action_type": "MOVE",
        "params": {"location_id": target["id"]},
    })
    assert r.status_code == 201, r.text
    task_id = r.json()["task_id"]
    with factory() as session:
        progress_tick(session, wid, 10_000, settings)
        session.commit()
    me = client.get(f"/characters/{cid}").json()
    assert me["location_id"] == target["id"], "MOVE must relocate the character"
    npc_id = next(o["id"] for o in target["occupants"] if o["kind"] == "npc")
    return npc_id, target, task_id


@pytest.fixture()
def world(tmp_path):
    """Seeded world + app/client + a DIRECT-mode player character."""
    settings = make_settings(tmp_path)
    assert settings.llm.enabled is False, "gate assumes the LLM-off alpha profile"
    assert settings.visual.enabled is False, "gate assumes the visual-off alpha profile"
    engine = build_world(settings)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app(settings, factory)
    client = TestClient(app)
    _register_and_login(client, "b3player")
    r = client.post("/characters", json={"name": "Борис Орлов", "sex": "M", "age": 30})
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    r = client.post(f"/characters/{cid}/control", json={"mode": "DIRECT"})
    assert r.status_code == 200, r.text
    assert r.json()["control_mode"] == "DIRECT"
    return settings, app, client, factory, cid, engine


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ---------- 1. Master journey at API level (the release-gate chain) ----------

def test_master_journey_api_level(world):
    """register→login→create→DIRECT→locations→MOVE→arrival→scene look→
    dialogue→inventory→tasks→desire→opportunities — every step asserts its
    authoritative result. This IS the master journey, API level."""
    settings, _app, client, factory, cid, _engine = world
    wid = settings.world.world_id

    # World + locations read (map prerequisites).
    r = client.get("/world")
    assert r.status_code == 200, r.text
    assert r.json()["game_timestamp"] is not None
    r = client.get("/locations")
    assert r.status_code == 200, r.text
    locs = r.json()
    assert locs, "seeded island must have locations"

    # Authoritative MOVE (same endpoint the map click uses) to an NPC location.
    npc_id, target, task_id = _travel_to_npc(client, factory, settings, cid)

    # Arrival is authoritative: task status + event + occupants.
    task = client.get(f"/actions/{task_id}").json()
    assert task["status"] == "completed"
    with factory() as session:
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
        assert payload.get("to_location") == target["id"]
    locs = client.get("/locations").json()
    here = next(loc for loc in locs if loc["id"] == target["id"])
    assert any(o["id"] == cid for o in here["occupants"])

    # Scene act look: committed-state descriptor (visual service NOT involved).
    r = client.post("/scene/act", json={"text": "осмотреться"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["action"] == "look"
    assert body["scene"]["location"]["name"] == target["name"]
    assert body["scene"]["location"]["id"] == target["id"]

    # Dialogue (Block 1): start + message; LLM is off → fallback source.
    r = client.post("/dialogue/start", json={"npc_id": npc_id})
    assert r.status_code == 201, r.text
    sid = r.json()["session_id"]
    r = client.post(f"/dialogue/{sid}/message", json={"content": "Привет!"})
    assert r.status_code == 200, r.text
    assert r.json()["source"] == "fallback"
    assert r.json()["npc_reply"].strip()

    # Inventory + tasks reads (Block 2 read models).
    r = client.get(f"/characters/{cid}/inventory")
    assert r.status_code == 200, r.text
    inv = r.json()
    assert isinstance(inv, (list, dict))
    r = client.get(f"/characters/{cid}/tasks")
    assert r.status_code == 200, r.text
    tasks = r.json()
    for key in ("active", "planned", "recent_terminal"):
        assert key in tasks, f"tasks read model must expose {key}"

    # Desire (Block 2): set a relationship desire, read it + opportunities.
    r = client.post(f"/characters/{cid}/desire", json={"text": "найти друзей"})
    assert r.status_code == 201, r.text
    r = client.get(f"/characters/{cid}/desire")
    assert r.status_code == 200, r.text
    desire = r.json()["desire"]
    assert desire is not None
    assert desire["catalog_key"] == "relationship"
    r = client.get(f"/characters/{cid}/desire/opportunities")
    assert r.status_code == 200, r.text
    assert "opportunities" in r.json() and "desire" in r.json()


# ---------- 2. LLM-down smoke: fallback stays playable, journal-only ----------

def test_llm_down_three_exchanges_playable_fallback(world):
    """LLM unavailable (enabled=False): a 3-exchange dialogue never dead-ends
    — every turn answers with source="fallback", and the exchange is
    journal-only: zero WorldEvents (dialogue never mutates the world)."""
    settings, _app, client, factory, cid, _engine = world
    wid = settings.world.world_id

    npc_here, _target, _task = _travel_to_npc(client, factory, settings, cid)

    r = client.post("/dialogue/start", json={"npc_id": npc_here})
    assert r.status_code == 201, r.text
    sid = r.json()["session_id"]

    with factory() as session:
        events_before = (
            session.query(WorldEvent).filter_by(world_id=wid).count())

    turns = ["Привет!", "Чем занимаешься?", "Пока!"]
    for content in turns:
        r = client.post(f"/dialogue/{sid}/message", json={"content": content})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["source"] == "fallback", (
            "LLM is off — the deterministic fallback must answer")
        assert body["npc_reply"].strip(), "fallback must stay playable"
        assert body["suggested_responses"], "suggestions must still be offered"

    hist = client.get(f"/dialogue/{sid}").json()
    msgs = hist["messages"]
    assert len(msgs) == 6, "3 user + 3 npc messages"
    assert [m["sender"] for m in msgs] == ["user", "npc"] * 3

    with factory() as session:
        events_after = (
            session.query(WorldEvent).filter_by(world_id=wid).count())
    assert events_after == events_before, "dialogue is journal-only (no events)"


# ---------- 3. Flux-down smoke: result commits, client degrades ----------

def test_flux_down_scene_result_commits_and_serves(world):
    """Visual service unavailable (enabled=False): the committed gameplay
    result still serves — scene act look returns the authoritative
    committed-state descriptor — while POST /visual/scenes degrades to 503
    with zero mutation. The client-side fallback state machine is pinned."""
    settings, _app, client, factory, cid, _engine = world
    wid = settings.world.world_id

    # Flux endpoint is down → 503, and it must not touch gameplay state.
    loc_id = client.get(f"/characters/{cid}").json()["location_id"]
    with factory() as session:
        obj_count = session.query(WorldClock).filter_by(world_id=wid).count()
        char_before = (
            session.query(Character)
            .filter_by(id=cid)
            .with_entities(
                Character.location_id,
                Character.alive,
                Character.updated_at,
            )
            .first()
        )
        tasks_before = (
            session.query(CharacterTask).filter_by(character_id=cid).count()
        )
    r = client.post("/visual/scenes", json={"location_id": loc_id})
    assert r.status_code == 503, r.text
    with factory() as session:
        assert session.query(WorldClock).filter_by(world_id=wid).count() == obj_count
        char_after = (
            session.query(Character)
            .filter_by(id=cid)
            .with_entities(
                Character.location_id,
                Character.alive,
                Character.updated_at,
            )
            .first()
        )
        assert char_after == char_before
        assert session.query(CharacterTask).filter_by(character_id=cid).count() == tasks_before

    # The committed-state descriptor still serves (gameplay result persists).
    r = client.post("/scene/act", json={"text": "осмотреться"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["action"] == "look"
    assert body["scene"]["location"]["id"] == loc_id

    # Portrait endpoint degrades the same way (visual off → 503).
    r = client.get(f"/visual/characters/{cid}/portrait")
    assert r.status_code == 503, r.text

    # Client pins: fallback state + retry affordance + honest toast.
    js = _read(APP_JS)
    assert js.count('S.scene = { state: "fallback" };') == 1
    assert js.count("sceneFallbackData()") == 2  # def + call
    assert js.count('"Визуал недоступен, показываю данные"') == 1
    assert js.count('"Повторить осмотр"') == 1


# ---------- 4. Transient-failure pins (every alpha-critical catch path) ----------

def test_ui_transient_failure_catch_pins():
    """Every alpha-critical catch path shows an explicit retry hint, keeps
    in-progress typed input, and leaves the previous screen state rendered.

    - Load failures (world/chat/inventory/tasks): the dead «Загрузка…» node is
      replaced by an inline explanation + a Повторить button (topbar stays).
    - Scene act error: the typed draft is restored into the re-rendered bar
      (S.sceneActDraft, the chat-draft pattern) and a toast fires.
    - Travel error: no re-render at all → the typed input survives by
      construction; only S.travel resets and a toast fires.
    - Desire save error: input untouched + status hint + toast.
    - Dialogue in-message error (m156 gold pattern) stays intact.
    """
    js = _read(APP_JS)
    # Shared retry affordance: one helper, four alpha-critical screens.
    assert js.count("const RETRY_HINT =") == 1
    assert js.count("function loadRetryNode(") == 1
    # Four alpha-critical call sites (the def line also contains
    # "loadRetryNode(loading," — pin the calls by their retry closures).
    assert js.count("loadRetryNode(loading, () => view") == 4
    assert js.count('"Загрузка не удалась. " + RETRY_HINT') == 1
    assert js.count('el("button", { type: "button", onclick: retry }, "Повторить")') == 1
    # Scene act draft preservation (never uses the chat literals — m156 pins
    # 'input.value = draft;' == 1 and 'input.value = "";' == 2 exactly).
    # S-init + read + ok:false-keep + success-clear + error-set:
    assert js.count("sceneActDraft") == 5
    assert js.count("S.sceneActDraft = text;") == 2  # 200 ok:false AND catch — both recoverable
    assert js.count("S.sceneActDraft = null;") == 1  # only a real success clears it
    assert js.count("value: S.sceneActDraft || ") == 1
    # §61 refusals surface localized (F1): the raw-English toast form is gone.
    assert js.count("toast(isAutonomousRefusal(e) ? AUTONOMOUS_HINT : e.message, true);") == 1
    assert js.count("toast(e.message, true);\n  } finally {") == 0
    # Desire save failure: explicit hint, typed text kept (no re-render).
    assert js.count('"Не удалось сохранить желание. " + RETRY_HINT') == 1
    # Travel failure: state reset + toast only — the input survives because
    # the catch never re-renders the world view. (The other S.travel = null
    # site is onOwnArrival's normal arrival — hence the contextual pin.)
    assert js.count(
        'S.travel = null;\n    const stale = '
        'document.getElementById("travel-status");') == 1
    assert js.count("toast(travelErrorText(e), true);") == 1
    # Dialogue gold pattern (m156) still exactly as pinned.
    assert js.count("input.value = draft;") == 1
    assert js.count('"Не удалось отправить — проверьте связь и повторите"') == 1
    assert js.count('input.value = "";') == 2


def test_ui_master_journey_route_pins():
    """The master-journey route chain exists: hash routes and topbar buttons
    for #/world → #/chat → #/inventory → #/tasks → #/profile, each wired
    exactly once."""
    js = _read(APP_JS)
    tabs = [("#/world", "Мир"), ("#/chat", "Чат"), ("#/inventory", "Инвентарь"),
            ("#/tasks", "Дела"), ("#/profile", "Профиль")]
    for tab, label in tabs:
        assert js.count(f'["{tab}", "{label}"]') == 1, f"topbar button {tab}"
    routes = {"#/world": "viewWorld", "#/chat": "viewChat",
              "#/inventory": "viewInventory", "#/tasks": "viewTasks",
              "#/profile": "viewProfile"}
    for tab, view in routes.items():
        assert js.count(f'"{tab}": {view}') == 1, f"route {tab} → {view}"


# ---------- 5. Low graphics + reduced motion smoke (byte-pins) ----------

def test_ui_low_graphics_and_reduced_motion_pins():
    """Low graphics + reduced motion smoke, client-side deterministic half.

    NOTE: the browser-side smoke — real prefers-reduced-motion emulation,
    the rendered low-mobile map, and the click-through of the master
    journey — is the ORCHESTRATOR's pass (worker runs no browsers). Here we
    pin the deterministic code paths that make those modes usable:
    - S.mapReducedMotion is initialised from prefers-reduced-motion and both
      programmatic scrolls honour it (auto vs smooth);
    - the mapQuality "low-mobile" paths exist (clustered NPC markers +
      bounded event markers) so weak devices get a cheaper map;
    - the scene fallback path (Flux down) renders usable data.
    """
    js = _read(APP_JS)
    assert js.count(
        'mapReducedMotion: window.matchMedia("(prefers-reduced-motion: reduce)")'
        '.matches') == 1
    assert js.count('S.mapReducedMotion ? "auto" : "smooth"') == 3
    assert js.count('mapQuality: "balanced"') == 1
    # Four low-graphics paths: NPC clustering, bounded event markers,
    # night-light limit, marker-budget label.
    assert js.count('S.mapQuality === "low-mobile"') == 4
    # Scene fallback stays usable (summary data + retry + act bar).
    assert js.count('S.scene = { state: "fallback" };') == 1
    assert js.count("sceneFallbackData()") == 2
    assert js.count('"Повторить осмотр"') == 1
