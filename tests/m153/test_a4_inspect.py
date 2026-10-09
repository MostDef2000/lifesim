"""#86 [alpha-v1][A4] Inspect Surroundings — Scene view + authoritative visual context.

The issue body is the spec of record (A1/A2/A3 precedent). Covers:

- Descriptor enrichment: build_scene_descriptor gains player_character_id
  (backward-compatible default None) → authoritative `player` section
  {id, name, sex, age, looks, worn} from committed state only, and
  `references` filled with the player's canonical portrait asset id
  (same query /visual/characters/{cid}/portrait serves). No canonical → [].
- Inspect never mutates: POST /visual/scenes with visual DISABLED → 503 and
  zero WorldEvent/VisualAsset rows; with transport failure → the documented
  502 path (visual_routes _generate_asset maps ValueError → 502) and still
  zero mutations.
- Enabled + fake transport → 201 with the enriched descriptor echoed in the
  asset payload (scene_descriptor carries player + references; prompt carries
  the reference fragment).
- UI byte-pins: A4 scene-state strings pinned exactly as implemented;
  innerHTML stays 0; hash-profile/scene-view/getElementById counts and the
  m150 media blocks survive byte-exact.
"""

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
    VisualAsset,
    WorldEvent,
    WorldObject,
    bootstrap,
    create_engine_factory,
)
from app.visual.descriptor import build_scene_descriptor  # noqa: E402
from app.world.seed_world import seed_world  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
APP_JS = os.path.join(ROOT, "backend/app/web/app.js")
APP_CSS = os.path.join(ROOT, "backend/app/web/style.css")

SETTINGS = None
LOOKS = "Высокий, седая борода, шрам над левой бровью"


def setup_module(module):
    global SETTINGS
    SETTINGS = load_config("config/default.yaml")


def make_settings(tmp_path, visual_enabled=True):
    visual = SETTINGS.visual.model_copy(update={
        "enabled": visual_enabled,
        "storage_dir": str(tmp_path / "assets"),
        "image_size": "8x8",
    })
    return SETTINGS.model_copy(update={
        "persistence": SETTINGS.persistence.model_copy(
            update={"db_path": str(tmp_path / "w.db")}
        ),
        "visual": visual,
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


def make_player(settings, engine, username="inspector86"):
    """App + client + registered player with a character that has `looks`."""
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app(settings, factory)
    client = TestClient(app)
    r = client.post("/auth/register", json={
        "username": username, "email": f"{username[0]}@x.com",
        "password": "password123", "age_confirmed": True,
    })
    assert r.status_code == 201, r.text
    client.post("/auth/login", json={"username": username, "password": "password123"})
    r = client.post("/characters", json={
        "name": "Inspect Tester", "sex": "M", "age": 34, "looks": LOOKS,
    })
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    loc_id = client.get(f"/characters/{cid}").json()["location_id"]
    return app, client, factory, cid, loc_id


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


# ---------- a) descriptor enrichment: player section + references ----------

def test_descriptor_player_section_without_canonical(tmp_path):
    settings = make_settings(tmp_path)
    engine = build_world(settings)
    _, client, factory, cid, loc_id = make_player(settings, engine)
    wid = settings.world.world_id

    with sessionmaker(bind=engine)() as session:
        d = build_scene_descriptor(session, wid, loc_id, player_character_id=cid)
        # Player section is authoritative, committed-state only.
        assert d["player"]["id"] == cid
        assert d["player"]["name"] == "Inspect Tester"
        assert d["player"]["sex"] == "M"
        assert d["player"]["age"] == 34
        assert d["player"]["looks"] == LOOKS
        assert isinstance(d["player"]["worn"], list)
        assert set(d["player"].keys()) == {"id", "name", "sex", "age", "looks", "worn"}
        # No canonical portrait exists yet → references stays [].
        assert d["references"] == []

        # Backward compat: existing callers (no player_character_id) are
        # byte-identical to the pre-A4 shape — no player section.
        d0 = build_scene_descriptor(session, wid, loc_id)
        assert d0["player"] is None
        assert d0["references"] == []


def test_descriptor_player_worn_items(tmp_path):
    """018 (§28): worn wearable WorldObjects (object_metadata "worn") land in
    the player section as item names."""
    settings = make_settings(tmp_path)
    engine = build_world(settings)
    _, client, factory, cid, loc_id = make_player(settings, engine)
    wid = settings.world.world_id

    with sessionmaker(bind=engine)() as session:
        session.add(WorldObject(
            world_id=wid, object_type="jacket", owner_character_id=cid,
            location_id=loc_id, quantity=1,
            object_metadata=json.dumps({"worn": True, "slot": "upper_body"}),
        ))
        # Same type but NOT worn — must not appear.
        session.add(WorldObject(
            world_id=wid, object_type="boots", owner_character_id=cid,
            location_id=loc_id, quantity=1, object_metadata=json.dumps({}),
        ))
        session.commit()

    with sessionmaker(bind=engine)() as session:
        d = build_scene_descriptor(session, wid, loc_id, player_character_id=cid)
        assert d["player"]["worn"] == ["jacket"]


def test_descriptor_references_use_canonical_portrait(tmp_path):
    settings = make_settings(tmp_path)
    engine = build_world(settings)
    _, client, factory, cid, loc_id = make_player(settings, engine)
    wid = settings.world.world_id

    # Canonical VisualAsset created via the model directly (§70 rows).
    with sessionmaker(bind=engine)() as session:
        asset = VisualAsset(
            world_id=wid, asset_type="portrait", character_id=cid,
            scene_descriptor={}, prompt="p", storage_path="ref-only-a4",
            seed=1, model="flux.1-schnell", canonical=True, created_at="ts0",
        )
        session.add(asset)
        session.commit()
        asset_id = asset.id

    with sessionmaker(bind=engine)() as session:
        d = build_scene_descriptor(session, wid, loc_id, player_character_id=cid)
        assert d["references"] == [asset_id]
        # An unknown player id → no player section, references untouched.
        d2 = build_scene_descriptor(session, wid, loc_id, player_character_id="npc_nobody_a4")
        assert d2["player"] is None
        assert d2["references"] == []


# ---------- b) visual disabled: 503, zero mutation ----------

def test_scene_disabled_503_mutates_nothing(tmp_path):
    settings = make_settings(tmp_path, visual_enabled=False)
    engine = build_world(settings)
    _, client, factory, cid, loc_id = make_player(settings, engine)

    with factory() as session:
        events_before = session.query(WorldEvent).count()
        assets_before = session.query(VisualAsset).count()

    r = client.post("/visual/scenes", json={"location_id": loc_id})
    assert r.status_code == 503, r.text

    with factory() as session:
        assert session.query(WorldEvent).count() == events_before, (
            "inspect must never mutate world_events"
        )
        assert session.query(VisualAsset).count() == assets_before
        char = session.get(Character, cid)
        assert char.location_id == loc_id, "no gameplay state change"


# ---------- c) enabled + transport failure: 502, zero mutation ----------

def test_scene_transport_failure_502_mutates_nothing(tmp_path):
    """_generate_asset maps transport ValueError → HTTP 502 (visual_routes)."""
    settings = make_settings(tmp_path)
    engine = build_world(settings)
    app, client, factory, cid, loc_id = make_player(settings, engine)

    class BoomTransport:
        def generate(self, prompt, seed, size):
            raise ValueError("flux exploded")

    app.state.flux_transport_factory = lambda: BoomTransport()

    with factory() as session:
        events_before = session.query(WorldEvent).count()

    r = client.post("/visual/scenes", json={"location_id": loc_id})
    assert r.status_code == 502, r.text

    with factory() as session:
        assert session.query(WorldEvent).count() == events_before, (
            "generation failure must not log or roll back anything"
        )
        assert session.query(VisualAsset).count() == 0


# ---------- d) enabled + fake transport: 201 with enriched descriptor ----------

def test_scene_enabled_returns_enriched_descriptor(tmp_path):
    settings = make_settings(tmp_path)
    engine = build_world(settings)
    app, client, factory, cid, loc_id = make_player(settings, engine)
    wid = settings.world.world_id

    from app.visual.transports import StubFluxTransport

    app.state.flux_transport_factory = lambda: StubFluxTransport()

    # Canonical portrait first → the scene must reference it (§70).
    r = client.post(f"/visual/portraits/{cid}")
    assert r.status_code == 201, r.text
    portrait_id = r.json()["asset"]["id"]
    r = client.post(f"/visual/assets/{portrait_id}/canonical",
                    json={"canonical": True})
    assert r.status_code == 200, r.text

    r = client.post("/visual/scenes", json={"location_id": loc_id})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["reused"] is False
    scene = body["asset"]
    assert scene["asset_type"] == "scene"
    assert scene["location_id"] == loc_id

    # The enriched descriptor is echoed in the asset payload.
    d = scene["scene_descriptor"]
    assert d["player"]["id"] == cid
    assert d["player"]["looks"] == LOOKS
    assert portrait_id in d["references"], (
        "canonical character reference must be used when available"
    )
    assert f"reference portraits: [{portrait_id}]" in scene["prompt"]
    assert d["location"]["id"] == loc_id

    # Image retrievable with the current auth path.
    f = client.get(f"/visual/assets/{scene['id']}/file")
    assert f.status_code == 200
    assert f.headers["content-type"] == "image/png"

    # Inspect created exactly the two assets and nothing else in world state.
    with factory() as session:
        assert session.query(VisualAsset).filter_by(world_id=wid).count() == 2
        assert session.get(Character, cid).location_id == loc_id


# ---------- e) UI byte-pins (Scene view additions) ----------

def test_ui_scene_state_pins_without_damaging_existing_pins():
    js = _read(APP_JS)
    css = _read(APP_CSS)
    # A4 additions — pin exactly what this slice implements.
    assert js.count('"Осматриваю окрестности…"') == 1
    assert js.count('"Визуал недоступен, показываю данные"') == 1
    assert js.count('"К карте"') == 1
    assert js.count("async function inspectSurroundings()") == 1
    # m14 carry: the inspect action posts the current location.
    assert js.count('api("/visual/scenes", { method: "POST",') == 1
    assert js.count("body: { location_id: S.character.location_id }") == 1
    # A3 placeholder superseded by the real A4 inspect action (the m151 pin
    # is updated to ==0 there, with the supersession documented in the test).
    assert js.count("Осмотр — в следующем срезе") == 0
    # Carry-forward pins (m146/m147/m149/m151 lineage) must survive.
    assert js.count("innerHTML") == 0
    assert js.count('location.hash = "#/profile";') == 3
    assert js.count('location.hash === "#/world"') == 1
    assert js.count("scene-view") == 3
    assert js.count('document.getElementById("scene-view")') == 2
    assert js.count('"Осмотреть окрестности"') == 1
    assert js.count('"Осмотреть"') == 1
    assert js.count('"Открыть Scene"') == 1
    # m150: first 880px/520px media blocks byte-exact.
    grid_media = css.split("@media (max-width: 880px) {", 1)[1].split("\n}", 1)[0]
    assert grid_media == "\n  .grid { grid-template-columns: 1fr; }"
    small_media = css.split("@media (max-width: 520px) {", 1)[1].split("\n}", 1)[0]
    assert small_media == (
        "\n  .anchor .dot { width: 7px; height: 7px; }"
        "\n  .anchor .map-label { font-size: 9px; }"
    )
