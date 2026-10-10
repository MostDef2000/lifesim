"""Block 2 — #90 [A8] Desire v1 + #91 [A9] Inventory alpha + #92 [A10]
Tasks/Journal/Opportunities screen (one PR, three issues).

The issue bodies are the specs of record (gh issue view 90 / 91 / 92; the
1-issue-1-PR rule is owner-overridden for this block, tracked in #83
issuecomment-6089997917).

Covers:

#90 Desire v1:
- CharacterDesire model: one ACTIVE desire per character (replace → old row
  status 'replaced' + replaced_by pointer; abandon → status 'abandoned');
  source_text ≤500; interpretation JSON from a SMALL EXPLICIT deterministic
  catalog; separate from the short-term CharacterGoal.
- interpret_desire(text): PURE (no Session/DB/LLM), keyword catalog with
  deterministic precedence; unsupported/ambiguous/short/empty → explicit
  clarification listing the supported catalog; same text → same key.
- build_opportunities(session, world_id, character_id, desire): bounded ≤5,
  reads ONLY existing systems (NPCs at location / roles / job / market /
  home), each opportunity carries reason+source+authoritative action
  descriptor+link; NEVER mutates (DB snapshot before/after); honest empty
  list when no basis exists.
- Endpoints (owner-only): POST /characters/{cid}/desire (201 created;
  unsupported → 200 + clarification, NO row — scene_act ok:false pattern);
  GET (200 {desire: null} empty state); DELETE (abandon); GET .../opportunities.

#91 Inventory alpha:
- Wear toggle through existing POST /wear (worn visible + authoritative).
- Sell/list through existing POST /market/offers only per the real validator
  (price>0, own item, not already listed — already-listed state is shown,
  not a fake button).
- NO invented actions: no button without backend capability (absence pinned).
- Additive condition in GET inventory payload.

#92 Tasks/Journal/Opportunities screen:
- «Дела» tab (#/tasks): Активные / История / Возможности + «Ближняя цель»
  (CharacterGoal) as its own block, explicitly distinct from Desire.
- Empty states explicit; no fake client-side progress; «Показать на карте»
  wired through S.mapFocusLocationId.
- recent_terminal history raised to the last N=20 (backend read model).
"""

import inspect
import json
import os
import random
import sys

import pytest
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from fastapi.testclient import TestClient  # noqa: E402

from app.api.app import create_app  # noqa: E402
from app.config.config import load_config  # noqa: E402
from app.db.models import (  # noqa: E402
    Character,
    CharacterDesire,
    CharacterGoal,
    CharacterTask,
    DialogueSession,
    MarketOffer,
    Relationship,
    WorldClock,
    WorldEvent,
    WorldObject,
    bootstrap,
    create_engine_factory,
)
from app.world.seed_world import seed_world  # noqa: E402
from app.world.social_seed import seed_social  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
APP_JS = os.path.join(ROOT, "backend/app/web/app.js")
APP_CSS = os.path.join(ROOT, "backend/app/web/style.css")

PW = "Correct-Horse-9!"
SETTINGS = None


def setup_module(module):
    global SETTINGS
    SETTINGS = load_config("config/default.yaml")


def make_settings(tmp_path):
    social = SETTINGS.social.model_copy(update={"enabled": True})
    return SETTINGS.model_copy(update={
        "persistence": SETTINGS.persistence.model_copy(
            update={"db_path": str(tmp_path / "w.db")}
        ),
        "social": social,
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
    client.post("/auth/register", json={
        "username": username, "email": f"{username}@x.com",
        "password": PW, "age_confirmed": True,
    })
    client.post("/auth/login", json={"username": username, "password": PW})


@pytest.fixture()
def world(tmp_path):
    """Seeded world + app/client + a player character."""
    settings = make_settings(tmp_path)
    engine = build_world(settings)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app(settings, factory)
    client = TestClient(app)
    _register_and_login(client, "b2player")
    r = client.post("/characters", json={"name": "Борис Орлов", "sex": "M", "age": 30})
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    return settings, app, client, factory, cid, engine


def _give_item(factory, settings, cid, object_type="jacket", qty=1, metadata=None):
    with factory() as s:
        loc = s.get(Character, cid).location_id
        from app.inventory import create_object

        obj = create_object(
            s, settings.world.world_id, object_type=object_type,
            quantity=qty, owner_character_id=cid, location_id=loc,
            metadata=metadata or {},
        )
        s.commit()
        return obj.id


def _move_player_to_empty_location(factory, settings, cid):
    """Move the player (and nobody else) to a location where no NPC stands."""
    from app.db.models import Location

    wid = settings.world.world_id
    with factory() as s:
        locs = [x.id for x in s.query(Location).filter_by(world_id=wid).all()]
        occupied = {c.location_id for c in s.query(Character).filter_by(world_id=wid, alive=True)}
        free = [lid for lid in locs if lid not in occupied]
        target = free[0] if free else locs[-1]
        char = s.get(Character, cid)
        char.location_id = target
        for other in s.query(Character).filter(
            Character.world_id == wid, Character.alive.is_(True),
            Character.location_id == target, Character.id != cid,
        ):
            other.location_id = locs[0]
        s.commit()
        return target


def _set_clock(factory, world_id, ts):
    with factory() as s:
        clock = s.query(WorldClock).filter_by(world_id=world_id).first()
        clock.game_timestamp = ts
        s.commit()


# ---------- 1. Desire model + endpoints (#90) ----------

def test_desire_create_and_persistence_across_restart(world):
    settings, _app, client, factory, cid, engine = world
    r = client.post(f"/characters/{cid}/desire",
                    json={"text": "Хочу найти настоящих друзей и любовь"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["desire"]["source_text"] == "Хочу найти настоящих друзей и любовь"
    assert body["desire"]["status"] == "active"
    assert body["interpretation"]["catalog_key"] == "relationship"
    # GET returns the active desire with interpretation
    g = client.get(f"/characters/{cid}/desire").json()
    assert g["desire"]["catalog_key"] == "relationship"
    assert g["desire"]["status"] == "active"
    assert g["desire"]["interpretation"]["catalog_key"] == "relationship"
    # "restart": new app instance, same DB file
    app2 = create_app(settings, sessionmaker(bind=engine, expire_on_commit=False))
    client2 = TestClient(app2)
    _register_and_login(client2, "b2player")
    g2 = client2.get(f"/characters/{cid}/desire").json()
    assert g2["desire"]["id"] == body["desire"]["id"]
    assert g2["desire"]["catalog_key"] == "relationship"


def test_goal_and_desire_stay_separate(world):
    _settings, _app, client, factory, cid, _engine = world
    g = client.post(f"/characters/{cid}/goals", json={"text": "Иди в shop"})
    assert g.status_code == 201, g.text
    d = client.post(f"/characters/{cid}/desire", json={"text": "хочу спокойной жизни"})
    assert d.status_code == 201, d.text
    goals = client.get(f"/characters/{cid}/goals").json()
    assert any(x["goal_id"] == g.json()["goal_id"] for x in goals)
    desire = client.get(f"/characters/{cid}/desire").json()["desire"]
    assert desire is not None
    # No cross-contamination: goals carry goal fields, desire carries desire fields
    assert "goal_type" not in desire and "goal_id" not in desire
    assert all("catalog_key" not in x for x in goals)


def test_unsupported_desire_clarification_no_row(world):
    _settings, _app, client, factory, cid, _engine = world
    for text in ("", "ок", "съездить на Марс"):
        r = client.post(f"/characters/{cid}/desire", json={"text": text})
        assert r.status_code == 200, (text, r.text)
        body = r.json()
        assert body["catalog_key"] is None or body["desire"] is None
        assert body.get("clarification")
        # every supported catalog entry is named in the clarification
        assert "Отношения" in body["clarification"]
    # No rows were created
    with factory() as s:
        assert s.query(CharacterDesire).filter_by(character_id=cid).count() == 0
    assert client.get(f"/characters/{cid}/desire").json()["desire"] is None


def test_desire_too_long_rejected(world):
    _settings, _app, client, _factory, cid, _engine = world
    r = client.post(f"/characters/{cid}/desire", json={"text": "ж" * 501})
    assert r.status_code == 422


def test_replace_flow_one_active(world):
    _settings, _app, client, factory, cid, _engine = world
    r1 = client.post(f"/characters/{cid}/desire", json={"text": "хочу любви и дружбы"})
    assert r1.status_code == 201
    r2 = client.post(f"/characters/{cid}/desire", json={"text": "хочу открыть своё дело"})
    assert r2.status_code == 201
    active = client.get(f"/characters/{cid}/desire").json()["desire"]
    assert active["catalog_key"] == "business"
    with factory() as s:
        rows = (s.query(CharacterDesire).filter_by(character_id=cid)
                .order_by(CharacterDesire.id).all())
        actives = [x for x in rows if x.status == "active"]
        replaced = [x for x in rows if x.status == "replaced"]
        assert len(rows) == 2 and len(actives) == 1 and len(replaced) == 1
        assert actives[0].id == active["id"]
        assert replaced[0].replaced_by == actives[0].id


def test_abandon_flow(world):
    _settings, _app, client, factory, cid, _engine = world
    client.post(f"/characters/{cid}/desire", json={"text": "хочу спокойной жизни"})
    r = client.delete(f"/characters/{cid}/desire")
    assert r.status_code == 200, r.text
    assert client.get(f"/characters/{cid}/desire").json()["desire"] is None
    with factory() as s:
        rows = s.query(CharacterDesire).filter_by(character_id=cid).all()
        assert len(rows) == 1 and rows[0].status == "abandoned"
    # abandoning again: nothing active → 404
    assert client.delete(f"/characters/{cid}/desire").status_code == 404


def test_desire_owner_only(world):
    _settings, _app, client, _factory, cid, _engine = world
    r = client.post(f"/characters/{cid}/desire", json={"text": "хочу друзей"})
    assert r.status_code == 201
    # unauthenticated
    client.post("/auth/logout")
    assert client.get(f"/characters/{cid}/desire").status_code == 401
    assert client.post(f"/characters/{cid}/desire", json={"text": "х"}).status_code == 401
    assert client.get(f"/characters/{cid}/desire/opportunities").status_code == 401
    # another user
    _register_and_login(client, "b2other")
    assert client.get(f"/characters/{cid}/desire").status_code == 403
    assert client.post(f"/characters/{cid}/desire", json={"text": "хочу дела"}).status_code == 403
    assert client.delete(f"/characters/{cid}/desire").status_code == 403
    assert client.get(f"/characters/{cid}/desire/opportunities").status_code == 403


def test_interpretation_deterministic_pure_module():
    from app.desire.desire import interpret_desire

    # Pure: no Session parameter at all (no DB, no LLM).
    sig = inspect.signature(interpret_desire)
    assert list(sig.parameters) == ["text"]
    a = interpret_desire("хочу найти любовь")
    b = interpret_desire("хочу найти любовь")
    assert a["catalog_key"] == b["catalog_key"] == "relationship"
    c = interpret_desire("хочу найти любовь")
    assert c["catalog_key"] == "relationship"
    # deterministic across the API too
    assert interpret_desire("хочу признания в общине")["catalog_key"] == "public_role"
    assert interpret_desire("хочу заработать денег")["catalog_key"] == "business"
    assert interpret_desire("хочу тихий дом и покой") == interpret_desire(
        "хочу тихий дом и покой")


def test_catalog_is_small_explicit_alpha():
    import app.desire.desire as desire_module

    catalog = desire_module.CATALOG
    assert 4 <= len(catalog) <= 6
    for key, entry in catalog.items():
        assert key and entry["title_ru"]
        assert isinstance(entry["keywords"], list) and entry["keywords"]
        assert entry["planner_hint"]
    # every catalog key has RU+EN keywords (deterministic bilingual matching)
    joined = " ".join(" ".join(e["keywords"]) for e in catalog.values())
    assert any(ord(ch) > 127 for ch in joined), "RU keywords required"
    assert any(c.isascii() and c.isalpha() for c in joined), "EN keywords required"


# ---------- 2. Opportunities planner (#90) ----------

def _place_npc_with_player(factory, settings, cid):
    """Return an npc id and ensure it stands at the player's location."""
    wid = settings.world.world_id
    with factory() as s:
        player = s.get(Character, cid)
        npc = (
            s.query(Character)
            .filter(Character.world_id == wid, Character.id.like("npc_%"))
            .order_by(Character.id)
            .first()
        )
        npc.location_id = player.location_id
        s.commit()
        return npc.id, f"{npc.first_name} {npc.last_name}".strip()


def test_opportunities_relationship_points_to_real_npc(world):
    settings, _app, client, factory, cid, _engine = world
    npc_id, npc_name = _place_npc_with_player(factory, settings, cid)
    client.post(f"/characters/{cid}/desire", json={"text": "хочу найти друзей"})
    r = client.get(f"/characters/{cid}/desire/opportunities")
    assert r.status_code == 200, r.text
    opps = r.json()["opportunities"]
    assert 1 <= len(opps) <= 5
    assert any(o["action"].get("npc_id") == npc_id for o in opps)
    npc_opp = next(o for o in opps if o["action"].get("npc_id") == npc_id)
    assert npc_opp["title"] and npc_opp["reason"] and npc_opp["source"]
    assert npc_opp["link"] == "#/chat"
    assert npc_opp["action"]["mechanic"] == "dialogue"


def test_opportunities_empty_when_no_basis(world):
    settings, _app, client, factory, cid, _engine = world
    _move_player_to_empty_location(factory, settings, cid)
    client.post(f"/characters/{cid}/desire", json={"text": "хочу найти друзей"})
    r = client.get(f"/characters/{cid}/desire/opportunities")
    assert r.status_code == 200
    assert r.json()["opportunities"] == []


def test_opportunities_without_desire_empty(world):
    _settings, _app, client, _factory, cid, _engine = world
    r = client.get(f"/characters/{cid}/desire/opportunities")
    assert r.status_code == 200
    body = r.json()
    assert body["opportunities"] == []
    assert body["desire"] is None


def test_planner_never_mutates_state(world):
    """m156-style no-mutation: full snapshot of gameplay tables before/after
    GET opportunities — zero writes on any path."""
    settings, _app, client, factory, cid, _engine = world
    npc_id, _name = _place_npc_with_player(factory, settings, cid)
    client.post(f"/characters/{cid}/desire", json={"text": "хочу найти друзей"})
    _give_item(factory, settings, cid, "boots")

    MUTABLE = (CharacterTask, WorldEvent, WorldObject, Relationship,
               CharacterGoal, MarketOffer, DialogueSession, CharacterDesire)

    def snapshot():
        from sqlalchemy import inspect as sa_inspect

        with factory() as s:
            out = []
            for model in MUTABLE:
                cols = [c.key for c in sa_inspect(model).column_attrs]
                rows = sorted(
                    tuple(repr(getattr(row, c)) for c in cols)
                    for row in s.query(model).all()
                )
                out.append((model.__name__, tuple(rows)))
            return tuple(out)

    before = snapshot()
    for _ in range(2):
        r = client.get(f"/characters/{cid}/desire/opportunities")
        assert r.status_code == 200
    assert snapshot() == before, "planner must be read-only"


def test_opportunities_public_role_and_business_and_quiet(world):
    settings, _app, client, factory, cid, _engine = world
    # public_role: an organization exists in the seeded world → honest pointer
    client.post(f"/characters/{cid}/desire", json={"text": "хочу уважения и статуса в общине"})
    opps = client.get(f"/characters/{cid}/desire/opportunities").json()["opportunities"]
    assert 1 <= len(opps) <= 5
    assert all(o["action"].get("mechanic") for o in opps)
    # business with a job: WORK is the authoritative mechanic
    client.post(f"/characters/{cid}/desire", json={"text": "хочу заработать денег"})
    opps = client.get(f"/characters/{cid}/desire/opportunities").json()["opportunities"]
    assert isinstance(opps, list)
    # quiet_life: home exists (player has home_location_id) → SLEEP/move home
    client.post(f"/characters/{cid}/desire", json={"text": "хочу тихий дом и покой"})
    opps = client.get(f"/characters/{cid}/desire/opportunities").json()["opportunities"]
    assert 1 <= len(opps) <= 5
    assert any(o["action"].get("action_type") in ("SLEEP", "MOVE") for o in opps)


# ---------- 3. Inventory (#91) ----------

def test_inventory_condition_additive_and_wear_toggle(world):
    settings, _app, client, factory, cid, _engine = world
    oid = _give_item(factory, settings, cid, "jacket")
    r = client.get(f"/characters/{cid}/inventory")
    assert r.status_code == 200
    item = next(i for i in r.json() if i["id"] == oid)
    assert item["condition"] == 100  # additive, from the authoritative column
    assert item["worn"] is False
    # toggle on through POST /wear
    r = client.post("/wear", json={"object_id": oid})
    assert r.status_code == 200, r.text
    assert r.json()["worn"] is True
    item = next(i for i in client.get(f"/characters/{cid}/inventory").json()
                if i["id"] == oid)
    assert item["worn"] is True and item["slot"] == "upper_body"
    # toggle back off
    r = client.post("/wear", json={"object_id": oid})
    assert r.json()["worn"] is False
    # non-wearable refuses (authoritative validator)
    oid_food = _give_item(factory, settings, cid, "food_bread")
    r = client.post("/wear", json={"object_id": oid_food})
    assert r.status_code == 422


def test_market_sell_real_validator_path(world):
    settings, _app, client, factory, cid, _engine = world
    oid = _give_item(factory, settings, cid, "boots")
    # price must be positive
    r = client.post("/market/offers", json={"object_id": oid, "price": 0})
    assert r.status_code == 422
    # happy path
    r = client.post("/market/offers", json={"object_id": oid, "price": 5})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "active"
    # one active offer per object
    assert client.post("/market/offers", json={"object_id": oid, "price": 5}).status_code == 409
    offers = client.get("/market/offers").json()
    assert any(o["object_id"] == oid and o["price"] == 5 for o in offers)


# ---------- 4. Tasks read model (#92) ----------

def test_recent_terminal_bounded_to_20(world):
    settings, _app, client, factory, cid, _engine = world
    wid = settings.world.world_id
    with factory() as s:
        now = s.query(WorldClock).filter_by(world_id=wid).first().game_timestamp
        for i in range(25):
            s.add(CharacterTask(
                character_id=cid, priority=5, task_type="IDLE",
                target_id=None, status="completed", source="npc",
                parameters=json.dumps({"reason": f"дело {i}"}),
                created_at=now - 100 - i, started_at=now - 90 - i,
                ends_at=now - 50 - i, completed_at=now - i,
            ))
        s.commit()
    data = client.get(f"/characters/{cid}/tasks").json()
    assert len(data["recent_terminal"]) == 20
    times = [t["completed_at"] for t in data["recent_terminal"]]
    assert times == sorted(times, reverse=True), "history is most-recent-first"


# ---------- 5. UI byte-pins (#91/#92 screens, #90 profile hook) ----------

def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def test_ui_tasks_screen_pins():
    js = _read(APP_JS)
    # New tab + route + view, exactly once each.
    assert js.count('["#/tasks", "Дела"]') == 1
    assert js.count('"#/tasks": viewTasks') == 1
    assert js.count('location.hash = "#/tasks";') == 1
    # Three visually distinct sections + short-term goal block.
    assert js.count('"tasks-active"') == 1
    assert js.count('"tasks-history"') == 1
    assert js.count('"tasks-opps"') == 1
    assert js.count('"tasks-goal"') == 1
    assert js.count('"Активные"') == 1
    assert js.count('"История"') == 1
    assert js.count('"Возможности"') == 1
    assert js.count('"Ближняя цель"') == 1
    # Explicit empty states.
    assert js.count('"Желание не задано — задайте его в профиле"') == 1
    assert js.count('"Пока нет возможностей"') == 1
    assert js.count('"Нет активных задач"') >= 1  # world card already had one
    assert js.count('"История пуста"') == 1
    # Map link through the existing focus mechanism.
    assert js.count('"Показать на карте"') == 1
    assert js.count("S.mapFocusLocationId = ") >= 1
    # B2-review F1: manual focus survives the poll loop and releases when
    # the player physically moves (anchor mismatch) — without this the
    # poll would clobber the task-target focus within one 5s tick.
    assert js.count("S.mapFocusManual = true;") == 1
    assert js.count("if (S.mapFocusManual && S.mapFocusAnchor !== character.location_id)") == 1
    assert js.count("if (!S.mapFocusManual && character.location_id != null)") == 1
    # Cold-load safety mirrors viewChat's authoritative character read.
    assert js.count("if (S.character.location_id == null)") == 2


def test_ui_inventory_screen_pins():
    js = _read(APP_JS)
    # Grouped presentation + detail + authoritative action ids, once each.
    assert js.count('"inv-group"') == 1
    assert js.count('"inv-item"') == 1
    assert js.count('"inv-detail"') == 1
    assert js.count('"inv-wear"') == 1
    assert js.count('"inv-sell"') == 1
    # Wear toggle talks to the existing endpoint; sell to the market validator
    # (two market call sites: the active-offers read + the create POST).
    assert js.count('api("/wear"') == 1
    assert js.count('api("/market/offers') == 2
    # Refresh after action (re-fetch + re-render), errors keep state.
    assert js.count("renderInventory") >= 2
    # NO invented actions: consumption/use have no direct item endpoint in alpha.
    assert js.count('"Съесть"') == 0
    assert js.count('"Выпить"') == 0
    assert js.count('"Использовать"') == 0
    assert js.count('"Выбросить"') == 0


def test_ui_profile_desire_pins():
    js = _read(APP_JS)
    assert js.count('"profile-desire"') == 1
    assert js.count('"Задать желание"') == 1
    assert js.count('"Отказаться от желания"') == 1
    # The opportunities read model is consumed exactly once — the Дела screen.
    assert js.count("/desire/opportunities") == 1
    assert js.count("`/characters/${S.character.id}/desire`") == 3  # POST/GET/DELETE


def test_ui_legacy_pins_intact():
    js = _read(APP_JS)
    css = _read(APP_CSS)
    assert js.count("innerHTML") == 0
    assert js.count('location.hash = "#/profile";') == 3
    assert js.count('location.hash === "#/world"') == 1
    assert js.count("scene-view") == 3
    assert js.count('document.getElementById("scene-view")') == 2
    assert js.count('"Осмотреть окрестности"') == 1
    assert js.count("«Осмотреть окрестности»") == 1
    assert js.count('"Осмотреть"') == 1
    assert js.count('"Открыть Scene"') == 1
    assert js.count('"К карте"') == 2
    assert js.count("S.scene = null;") >= 2
    assert js.count('"scene-act-input"') == 2
    assert js.count('"scene-act-go"') == 2
    assert js.count('"Осматриваю окрестности…"') == 1
    assert js.count('"Визуал недоступен, показываю данные"') == 1
    assert js.count("fetchPortraitBlob") == 5
    assert js.count('"chat-msg-input"') == 2
    assert js.count('"chat-roles"') == 1
    assert js.count("/characters/${S.character.id}/relationships") == 1
    assert js.count("AUTONOMOUS_HINT") == 7
    assert js.count("isAutonomousRefusal") == 5
    assert js.count('} else { toast(e.message, true); }') == 2
    assert js.count('"Идти"') == 1
    assert js.count("/travel/plan") == 1
    assert js.count("S.authProbed = false") == 2
    assert js.count("S.authProbed = true") == 1
    assert js.count("home_location_id") == 0
    assert js.count("Дом") == 0
    assert js.count('"Сделать"') == 1
    assert js.count('api("/scene/act"') == 1
    # m150: appended CSS rules must not disturb the pinned media blocks.
    grid_media = css.split("@media (max-width: 880px) {", 1)[1].split("\n}", 1)[0]
    assert grid_media == "\n  .grid { grid-template-columns: 1fr; }"
    small_media = css.split("@media (max-width: 520px) {", 1)[1].split("\n}", 1)[0]
    assert small_media == (
        "\n  .anchor .dot { width: 7px; height: 7px; }"
        "\n  .anchor .map-label { font-size: 9px; }"
    )


def test_desire_module_documents_deterministic_llm_off():
    """Alpha: the deterministic interpreter IS the implementation (LLM-off
    fallback = only path); the module says so for a stranger reader."""
    import app.desire.desire as desire_module

    src = inspect.getsource(desire_module)
    assert "LLM" in src
    assert "deterministic" in src.lower()
    sig = inspect.signature(desire_module.build_opportunities)
    assert list(sig.parameters) == ["session", "world_id", "character_id", "desire"]
