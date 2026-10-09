"""#87 [alpha-v1][A5] Freeform Scene Action v1 — interpret → validate → commit → refresh.

The issue body is the spec of record (A1-A4 precedent). Covers:

- Interpreter catalog: each intent maps to the right task type through the
  REAL POST /scene/act with a real (TestClient) DB — move (grounded via the
  A3 helper), socialize (target resolved among present characters), sleep,
  eat, drink, idle, buy, wear/take-off (the SAME toggle_wear path as
  POST /wear). Look/observation is a PURE read (no task, no mutation).
- Unsupported / ambiguous / validator-rejected intents → 200 {"ok": false,
  reason, detail} with NO task row and NO WorldEvent (validate() is
  read-only; the interpreter is a pure text→intent function — there is NO
  LLM call anywhere in this slice, so neither interpreter nor model can
  write the DB/world directly).
- Gates: AUTONOMOUS → 409 (same as POST /actions); unauthenticated → 401.
- events field of an ok response ⊆ DB (actor-scoped, strictly after the
  pre-call watermark) — "result/event visible before/with scene refresh".
- UI byte-pins: the always-visible free-text act bar + result line, with
  every A3/A4 carry-forward pin intact (innerHTML 0, scene-view 3,
  hash-profile 3, media blocks byte-exact, inspectSurroundings reuse).
- Catalog + exclusions as module data (SCENE_ACTIONS / SCENE_EXCLUDED).
"""

import inspect
import json
import os
import random
import sys

from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from fastapi.testclient import TestClient  # noqa: E402

from app.api.app import create_app  # noqa: E402
from app.config.config import load_config  # noqa: E402
from app.db.models import (  # noqa: E402
    Character,
    CharacterNeeds,
    CharacterTask,
    Location,
    WorldEvent,
    WorldObject,
    bootstrap,
    create_engine_factory,
)
from app.world.seed_world import seed_world  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
APP_JS = os.path.join(ROOT, "backend/app/web/app.js")

PW = "Correct-Horse-9!"
SETTINGS = None


def setup_module(module):
    global SETTINGS
    SETTINGS = load_config("config/default.yaml")


def make_settings(tmp_path, social_enabled=False):
    social = SETTINGS.social.model_copy(update={"enabled": social_enabled})
    return SETTINGS.model_copy(update={
        "persistence": SETTINGS.persistence.model_copy(
            update={"db_path": str(tmp_path / "w.db")}
        ),
        "social": social,
    })


def build_world(settings, seed=42):
    engine = create_engine_factory(settings)
    from app.characters.generator import generate_population

    with sessionmaker(bind=engine)() as session:
        bootstrap(engine, settings, seed=seed)
        seed_world(session, settings, settings.world.world_id)
        rng = random.Random(seed)
        generate_population(session, settings, rng, settings.world.world_id, 20)
        session.commit()
    return engine


def make_player(settings, engine, username="actor87", direct=True):
    """App + client + registered player with a character (DIRECT by default —
    players spawn AUTONOMOUS per §61, and /scene/act refuses AUTONOMOUS)."""
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app(settings, factory)
    client = TestClient(app)
    r = client.post("/auth/register", json={
        "username": username, "email": f"{username[0]}@x.com",
        "password": PW, "age_confirmed": True,
    })
    assert r.status_code == 201, r.text
    client.post("/auth/login", json={"username": username, "password": PW})
    r = client.post("/characters", json={
        "name": "Scene Tester", "sex": "M", "age": 30,
    })
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    loc_id = client.get(f"/characters/{cid}").json()["location_id"]
    if direct:
        r = client.post(f"/characters/{cid}/control", json={"mode": "DIRECT"})
        assert r.status_code == 200, r.text
    return app, client, factory, cid, loc_id


def _loc_by_type(factory, world_id, loc_type):
    with factory() as session:
        row = (
            session.query(Location)
            .filter_by(world_id=world_id, type=loc_type)
            .order_by(Location.id)
            .first()
        )
        return row.id if row is not None else None


def _set_needs(factory, cid, **kw):
    with factory() as session:
        needs = session.get(CharacterNeeds, cid)
        for k, v in kw.items():
            setattr(needs, k, v)
        session.commit()


def _add_npc(factory, world_id, loc_id, npc_id, first, last):
    with factory() as session:
        session.add(Character(
            id=npc_id, world_id=world_id, type="npc",
            first_name=first, last_name=last,
            birth_date="1990-01-01", age=36, sex="F", alive=True,
            location_id=loc_id, created_at=0, updated_at=0,
        ))
        session.commit()


def _add_owned_object(factory, world_id, cid, loc_id, obj_type, worn):
    with factory() as session:
        obj = WorldObject(
            world_id=world_id, object_type=obj_type, owner_character_id=cid,
            location_id=loc_id, quantity=1,
            object_metadata=json.dumps({"worn": worn}),
        )
        session.add(obj)
        session.commit()
        return obj.id


def _task_count(factory, cid):
    with factory() as session:
        return session.query(CharacterTask).filter_by(character_id=cid).count()


def _event_count(factory):
    with factory() as session:
        return session.query(WorldEvent).count()


def _actor_events(factory, world_id, cid, after_id):
    with factory() as session:
        rows = (
            session.query(WorldEvent)
            .filter(
                WorldEvent.world_id == world_id,
                WorldEvent.actor_id == cid,
                WorldEvent.id > after_id,
            )
            .order_by(WorldEvent.id)
            .all()
        )
        return [
            {"id": e.id, "event_type": e.event_type, "actor_id": e.actor_id,
             "location_id": e.location_id, "day": e.game_timestamp // 1440,
             "game_timestamp": e.game_timestamp,
             "payload": json.loads(e.payload) if e.payload else {}}
            for e in rows
        ]


def _max_event_id(factory):
    with factory() as session:
        row = session.query(WorldEvent.id).order_by(WorldEvent.id.desc()).first()
        return row[0] if row else 0


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


# ---------- a) catalog intents → task types through the REAL endpoint ----------

def test_look_is_pure_read_without_task_or_events(tmp_path):
    """«осмотреться» → 200, CURRENT scene descriptor data, «Вы осматриваетесь»,
    zero task rows, zero new WorldEvents — pure read (issue: no mutation)."""
    settings = make_settings(tmp_path)
    engine = build_world(settings)
    _, client, factory, cid, loc_id = make_player(settings, engine)

    tasks_before = _task_count(factory, cid)
    events_before = _event_count(factory)

    r = client.post("/scene/act", json={"text": "осмотреться"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["action"] == "look"
    assert body["result"].startswith("Вы осматриваетесь")
    # build_scene_descriptor fields: authoritative committed state.
    assert body["scene"]["location"]["id"] == loc_id
    assert "characters" in body["scene"] and "time" in body["scene"]
    assert body["interpretation"]["intent"] == "look"

    assert _task_count(factory, cid) == tasks_before == 0
    assert _event_count(factory) == events_before


def test_move_grounds_destination_and_creates_move_task(tmp_path):
    settings = make_settings(tmp_path)
    engine = build_world(settings)
    _, client, factory, cid, _ = make_player(settings, engine)
    wid = settings.world.world_id
    well_id = _loc_by_type(factory, wid, "well")
    watermark = _max_event_id(factory)

    r = client.post("/scene/act", json={"text": "идти к Water Well"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["task_type"] == "MOVE"
    assert body["interpretation"]["intent"] == "move"
    assert body["interpretation"]["params"]["destination_id"] == well_id
    assert body["interpretation"]["params"]["destination_name"] == "Water Well"

    with factory() as session:
        task = session.get(CharacterTask, int(body["task_id"]))
        assert task is not None and task.task_type == "MOVE"
        assert task.source == "player"
        params = json.loads(task.parameters)
        assert params["path"][-1] == well_id
    # events in the response are exactly the DB rows past the watermark.
    assert body["events"] == _actor_events(factory, wid, cid, watermark)


def test_socialize_resolves_present_target(tmp_path):
    settings = make_settings(tmp_path, social_enabled=True)
    engine = build_world(settings)
    _, client, factory, cid, loc_id = make_player(settings, engine)
    wid = settings.world.world_id
    _add_npc(factory, wid, loc_id, "npc_a5soc", "Иван", "Смирнов")
    _set_needs(factory, cid, social=10.0)

    r = client.post("/scene/act", json={"text": "поговорить со Смирновым"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["task_type"] == "SOCIALIZE"
    assert body["interpretation"]["intent"] == "socialize"
    assert body["interpretation"]["params"]["target_name"] == "Иван Смирнов"

    with factory() as session:
        task = session.get(CharacterTask, int(body["task_id"]))
        assert task is not None and task.task_type == "SOCIALIZE"


def test_sleep_eat_drink_idle_buy_map_to_task_types(tmp_path):
    settings = make_settings(tmp_path)
    engine = build_world(settings)
    _, client, factory, cid, _ = make_player(settings, engine)
    _set_needs(factory, cid, hunger=30.0, thirst=30.0, energy=30.0)

    cases = [
        ("поспать", "SLEEP"),
        ("поесть", "EAT"),
        ("выпить воды", "DRINK"),
        ("ничего не делать", "IDLE"),
        ("постоять", "IDLE"),
        ("купить еду", "BUY_ITEM"),
    ]
    for text, expected_type in cases:
        r = client.post("/scene/act", json={"text": text})
        assert r.status_code == 201, f"{text}: {r.text}"
        body = r.json()
        assert body["ok"] is True, f"{text}: {body}"
        assert body["task_type"] == expected_type, f"{text}: {body}"
        with factory() as session:
            task = session.get(CharacterTask, int(body["task_id"]))
            assert task is not None and task.task_type == expected_type

    r = client.post("/scene/act", json={"text": "купить еду"})
    assert r.json()["interpretation"]["params"]["item"] == "food"


def test_wear_and_take_off_toggle_via_wear_path(tmp_path):
    """«надеть куртку» / «снять куртку» flip the SAME worn flag POST /wear
    toggles (toggle_wear is the only writer; no task is created)."""
    settings = make_settings(tmp_path)
    engine = build_world(settings)
    _, client, factory, cid, loc_id = make_player(settings, engine)
    wid = settings.world.world_id
    _add_owned_object(factory, wid, cid, loc_id, "jacket", worn=False)

    r = client.post("/scene/act", json={"text": "надеть куртку"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["ok"] is True and body["worn"] is True
    assert body["action"] == "wear"
    with factory() as session:
        obj = session.get(WorldObject, int(body["object_id"]))
        assert json.loads(obj.object_metadata)["worn"] is True

    r = client.post("/scene/act", json={"text": "снять куртку"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["ok"] is True and body["worn"] is False
    assert body["action"] == "take_off"
    with factory() as session:
        obj = session.get(WorldObject, int(body["object_id"]))
        assert json.loads(obj.object_metadata)["worn"] is False
    assert _task_count(factory, cid) == 0, "wear path creates no task"


# ---------- b) unsupported → explanation, NO mutation ----------

def test_unsupported_text_returns_explanation_without_mutation(tmp_path):
    settings = make_settings(tmp_path)
    engine = build_world(settings)
    _, client, factory, cid, _ = make_player(settings, engine)

    tasks_before = _task_count(factory, cid)
    events_before = _event_count(factory)

    r = client.post("/scene/act", json={"text": "спою песню"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False
    assert body["reason"] == "unsupported_action"
    # the explanation carries the supported catalog (mirrors the A3 UX).
    assert "поговорить" in body["detail"]
    assert "осмотреться" in body["detail"]
    # empty text is deterministically unsupported too.
    r2 = client.post("/scene/act", json={"text": ""})
    assert r2.status_code == 200
    assert r2.json()["ok"] is False

    assert _task_count(factory, cid) == tasks_before == 0
    assert _event_count(factory) == events_before


# ---------- c) ambiguous target → candidates, no task ----------

def test_ambiguous_socialize_lists_candidates_without_task(tmp_path):
    settings = make_settings(tmp_path, social_enabled=True)
    engine = build_world(settings)
    _, client, factory, cid, loc_id = make_player(settings, engine)
    wid = settings.world.world_id
    _add_npc(factory, wid, loc_id, "npc_a5a", "Иван", "Смирнов")
    _add_npc(factory, wid, loc_id, "npc_a5b", "Иван", "Петров")
    _set_needs(factory, cid, social=10.0)

    r = client.post("/scene/act", json={"text": "поговорить с Иваном"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False
    assert body["reason"] == "ambiguous"
    options = body["options"]
    assert "Иван Смирнов" in options and "Иван Петров" in options
    assert _task_count(factory, cid) == 0


def test_move_ambiguous_destination_lists_options_without_task(tmp_path):
    settings = make_settings(tmp_path)
    engine = build_world(settings)
    _, client, factory, cid, _ = make_player(settings, engine)

    r = client.post(
        "/scene/act",
        json={"text": "идти между Water Well и Community Kitchen"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False
    assert body["reason"] == "ambiguous"
    assert len(body["options"]) >= 2
    assert _task_count(factory, cid) == 0


# ---------- d) validator rejection → not_possible, no task, no events ----------

def test_validator_rejection_eat_without_food_is_not_possible(tmp_path):
    """EAT far from any food: validator reason surfaces verbatim as
    not_possible; validate() is read-only so nothing mutates."""
    settings = make_settings(tmp_path)
    engine = build_world(settings)
    _, client, factory, cid, _ = make_player(settings, engine)
    wid = settings.world.world_id
    pier_id = _loc_by_type(factory, wid, "pier")

    with factory() as session:
        session.query(WorldObject).filter(
            WorldObject.object_type.like("food%")
        ).delete(synchronize_session=False)
        char = session.get(Character, cid)
        char.location_id = pier_id  # far from the kitchen
        session.commit()
    _set_needs(factory, cid, hunger=30.0)

    tasks_before = _task_count(factory, cid)
    events_before = _event_count(factory)

    r = client.post("/scene/act", json={"text": "поесть"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False
    assert body["reason"] == "not_possible"
    assert "No food available anywhere" in body["detail"]

    assert _task_count(factory, cid) == tasks_before == 0
    assert _event_count(factory) == events_before


# ---------- e) control-mode and auth gates ----------

def test_autonomous_character_is_409(tmp_path):
    """Players spawn AUTONOMOUS (§61) — /scene/act refuses like POST /actions."""
    settings = make_settings(tmp_path)
    engine = build_world(settings)
    _, client, factory, cid, _ = make_player(settings, engine, direct=False)

    r = client.post("/scene/act", json={"text": "поспать"})
    assert r.status_code == 409, r.text
    assert "AUTONOMOUS" in str(r.json()["detail"])
    assert _task_count(factory, cid) == 0


def test_unauthenticated_is_401(tmp_path):
    settings = make_settings(tmp_path)
    engine = build_world(settings)
    app = create_app(settings, sessionmaker(bind=engine, expire_on_commit=False))
    client = TestClient(app)
    r = client.post("/scene/act", json={"text": "поспать"})
    assert r.status_code == 401, r.text


# ---------- f) events in the ok response exist in DB ----------

def test_response_events_match_db_rows_after_watermark(tmp_path):
    settings = make_settings(tmp_path)
    engine = build_world(settings)
    _, client, factory, cid, _ = make_player(settings, engine)
    wid = settings.world.world_id

    watermark = _max_event_id(factory)
    r = client.post("/scene/act", json={"text": "идти к Water Well"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert isinstance(body["events"], list)
    # Every event the response claims is a real committed WorldEvent row for
    # this actor, strictly after the pre-call watermark — no fabrication.
    assert body["events"] == _actor_events(factory, wid, cid, watermark)
    for ev in body["events"]:
        with factory() as session:
            assert session.get(WorldEvent, ev["id"]) is not None


# ---------- g) UI byte-pins: act bar + result line, carry-forwards intact ----------

def test_ui_scene_act_pins_without_damaging_existing_pins():
    js = _read(APP_JS)
    # A5 additions — the free-text act bar is ALWAYS part of the scene panel.
    assert js.count("Что вы делаете? Например: поговорить с Ивановым") == 1
    assert js.count('"Сделать"') == 1
    assert js.count('"scene-act-input"') == 2
    assert js.count('"scene-act-go"') == 2
    assert js.count('"scene-act-result"') == 1
    assert js.count("async function submitSceneAct") == 1
    assert js.count("sceneAct: null") == 1
    # result line: interpretation + result, ok:false shows the explanation.
    assert js.count('"Распознано: "') == 1
    assert js.count('"Принято: "') == 1
    assert js.count('"Надето"') == 1
    assert js.count('"Снято"') == 1
    assert js.count('"Не удалось выполнить действие"') == 1
    assert js.count('api("/scene/act"') == 1
    # XSS hygiene: el() only — innerHTML stays banned.
    assert js.count("innerHTML") == 0
    # Carry-forward pins (m146/m147/m149/m150/m151/m153 lineage) survive.
    assert js.count('location.hash = "#/profile";') == 3
    assert js.count("scene-view") == 3
    assert js.count('document.getElementById("scene-view")') == 2
    assert js.count('"Осмотреть окрестности"') == 1
    assert js.count("«Осмотреть окрестности»") == 1
    assert js.count('"Осмотреть"') == 1
    assert js.count('"Открыть Scene"') == 1
    assert js.count('"К карте"') == 2
    assert js.count("S.scene = null;") >= 2
    assert js.count('S.scene.state === "loading"') >= 1
    assert js.count("async function inspectSurroundings()") == 1
    assert js.count("scene.scrollIntoView") == 2
    assert js.count('location.hash === "#/world"') == 1
    assert js.count('api("/visual/scenes", { method: "POST",') == 1
    assert js.count("body: { location_id: S.character.location_id }") == 1
    assert js.count("Осмотр — в следующем срезе") == 0


# ---------- h) catalog + exclusions as documented module data ----------

def test_scene_catalog_and_exclusions_are_module_data():
    from app.api import scene_act

    assert scene_act.SCENE_ACTIONS == [
        "look", "move", "socialize", "sleep", "eat", "drink", "idle", "buy",
        "wear", "take_off",
    ]
    # The exclusion list itself is data, with the documented alpha exclusions.
    assert any("object creation" in e for e in scene_act.SCENE_EXCLUDED)
    assert any("economy" in e or "relationship" in e for e in scene_act.SCENE_EXCLUDED)
    assert any("skill" in e for e in scene_act.SCENE_EXCLUDED)
    assert any("universal" in e for e in scene_act.SCENE_EXCLUDED)
    assert any("LLM" in e for e in scene_act.SCENE_EXCLUDED)


def test_interpreter_is_pure_and_deterministic():
    """The interpreter must be a pure text→intent function: no Session/DB
    parameter, deterministic output — so it cannot write DB/world, and this
    slice contains no LLM call at all."""
    from app.api import scene_act

    sig = inspect.signature(scene_act.interpret_scene_action)
    assert list(sig.parameters) == ["text"]

    samples = [
        ("осмотреться", {"intent": "look", "params": {}}),
        ("идти к Water Well", {"intent": "move",
                               "params": {"destination_text": "water well"}}),
        ("поговорить со Смирновым", {"intent": "socialize",
                                     "params": {"target_text": "смирновым"}}),
        ("поспать", {"intent": "sleep", "params": {}}),
        ("поесть", {"intent": "eat", "params": {}}),
        ("выпить воды", {"intent": "drink", "params": {}}),
        ("ничего не делать", {"intent": "idle", "params": {}}),
        ("купить еду", {"intent": "buy", "params": {"item": "food"}}),
        ("надеть куртку", {"intent": "wear",
                           "params": {"object_text": "куртку", "wearable": "jacket"}}),
        ("снять сапоги", {"intent": "take_off",
                          "params": {"object_text": "сапоги", "wearable": "boots"}}),
        ("спою песню", None),
    ]
    for text, expected in samples:
        first = scene_act.interpret_scene_action(text)
        second = scene_act.interpret_scene_action(text)
        assert first == second, f"non-deterministic for {text!r}"
        assert first == expected, f"{text!r} → {first}"
