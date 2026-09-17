"""M6 Chunk B tests (SPEC 006-flux, T11): /visual/* routes — portraits, scenes, canon, reading."""
import os
import random
import sys

import pytest
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from fastapi.testclient import TestClient  # noqa: E402

from app.api.app import create_app  # noqa: E402
from app.config.config import load_config  # noqa: E402
from app.db.models import bootstrap, create_engine_factory  # noqa: E402
from app.world.seed_world import seed_world  # noqa: E402

SETTINGS = None


def setup_module(module):
    global SETTINGS
    SETTINGS = load_config("config/default.yaml")


def make_settings(tmp_path, visual_enabled=True, storage_dir=None):
    visual = SETTINGS.visual.model_copy(update={
        "enabled": visual_enabled,
        "storage_dir": storage_dir or str(tmp_path / "assets"),
        "image_size": "8x8",
    })
    return SETTINGS.model_copy(update={
        "persistence": SETTINGS.persistence.model_copy(
            update={"db_path": str(tmp_path / "w.db")}
        ),
        "visual": visual,
    })


@pytest.fixture()
def world(tmp_path):
    """App + populated world + registered player with character."""
    settings = make_settings(tmp_path)
    engine = create_engine_factory(settings)
    from app.characters.generator import generate_population

    with sessionmaker(bind=engine)() as session:
        bootstrap(engine, settings, seed=42)
        seed_world(session, settings, settings.world.world_id)
        rng = random.Random(42)
        generate_population(session, settings, rng, settings.world.world_id, 20)
        session.commit()

    factory = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app(settings, factory)
    from fastapi.testclient import TestClient

    client = TestClient(app)
    r = client.post("/auth/register", json={
        "username": "artfan",
        "email": "a@x.com",
        "password": "password123",
        "age_confirmed": True,
    })
    assert r.status_code == 201, r.text
    client.post("/auth/login", json={"username": "artfan", "password": "password123"})
    r = client.post("/characters", json={"name": "Pablo", "sex": "M", "age": 25})
    assert r.status_code == 201, r.text
    cid = r.json()["id"]

    # a location with objects for scene tests
    from sqlalchemy import select

    from app.db.models import Location
    with factory() as session:
        location_id = session.scalars(select(Location.id).order_by(Location.id)).first()
    return settings, factory, client, cid, location_id


class TestPortraits:
    def test_create_and_repeat(self, world):
        _, _, client, cid, _ = world
        r = client.post(f"/visual/portraits/{cid}")
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["reused"] is False
        assert body["asset"]["asset_type"] == "portrait"
        assert body["asset"]["character_id"] == cid
        # repeat without canonical -> new generation
        r2 = client.post(f"/visual/portraits/{cid}")
        assert r2.status_code == 201
        assert r2.json()["asset"]["id"] != body["asset"]["id"]
        # different seeds (generation counter in seed hash)
        assert r2.json()["asset"]["seed"] != body["asset"]["seed"]

    def test_canonical_reuse(self, world):
        _, _, client, cid, _ = world
        first = client.post(f"/visual/portraits/{cid}").json()["asset"]
        r = client.post(f"/visual/assets/{first['id']}/canonical", json={"canonical": True})
        assert r.status_code == 200
        assert r.json()["canonical"] is True
        # reuse: no new generation
        r2 = client.post(f"/visual/portraits/{cid}")
        assert r2.status_code == 200
        body = r2.json()
        assert body["reused"] is True
        assert body["asset"]["id"] == first["id"]

    def test_get_canonical_portrait(self, world):
        _, _, client, cid, _ = world
        r = client.get(f"/visual/characters/{cid}/portrait")
        assert r.status_code == 404
        asset = client.post(f"/visual/portraits/{cid}").json()["asset"]
        client.post(f"/visual/assets/{asset['id']}/canonical", json={"canonical": True})
        r2 = client.get(f"/visual/characters/{cid}/portrait")
        assert r2.status_code == 200
        assert r2.json()["id"] == asset["id"]

    def test_file_roundtrip(self, world):
        _, _, client, cid, _ = world
        asset = client.post(f"/visual/portraits/{cid}").json()["asset"]
        r = client.get(f"/visual/assets/{asset['id']}/file")
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/png"
        assert r.content.startswith(b"\x89PNG")
        assert r.content == client.get(f"/visual/assets/{asset['id']}/file").content

    def test_metadata(self, world):
        _, _, client, cid, _ = world
        asset = client.post(f"/visual/portraits/{cid}").json()["asset"]
        r = client.get(f"/visual/assets/{asset['id']}")
        assert r.status_code == 200
        meta = r.json()
        assert meta["prompt"]
        assert meta["scene_descriptor"]["camera"] == "portrait"
        assert meta["model"] == "flux.1-schnell"


class TestScenes:
    def test_create_scene_with_references(self, world):
        _, _, client, cid, _ = world
        asset = client.post(f"/visual/portraits/{cid}").json()["asset"]
        client.post(f"/visual/assets/{asset['id']}/canonical", json={"canonical": True})
        player_location = client.get(f"/characters/{cid}").json()["location_id"]
        r = client.post("/visual/scenes", json={"location_id": player_location})
        assert r.status_code == 201, r.text
        scene = r.json()["asset"]
        assert scene["asset_type"] == "scene"
        assert scene["location_id"] == player_location
        descriptor = scene["scene_descriptor"]
        assert descriptor["location"]["id"] == player_location
        # player is at their location -> their canonical portrait is a reference (§70)
        assert asset["id"] in descriptor["references"]
        assert f"reference portraits: [{asset['id']}]" in scene["prompt"]

    def test_unknown_location_404(self, world):
        _, _, client, *_ = world
        r = client.post("/visual/scenes", json={"location_id": 999999})
        assert r.status_code == 404


class TestAccess:
    def test_portrait_requires_owner(self, world):
        settings, factory, client, cid, _ = world
        other = TestClient(create_app(settings, factory))
        r = other.post("/auth/register", json={
            "username": "intruder",
            "email": "i@x.com",
            "password": "password123",
            "age_confirmed": True,
        })
        assert r.status_code == 201, r.text
        other.post("/auth/login", json={"username": "intruder", "password": "password123"})
        r = other.post(f"/visual/portraits/{cid}")
        assert r.status_code == 403

    def test_requires_auth(self, world):
        settings, factory, client, cid, _ = world
        anon = TestClient(create_app(settings, factory))
        assert anon.get("/visual/assets/1").status_code == 401
        assert anon.post(f"/visual/portraits/{cid}").status_code == 401

    def test_missing_character_404(self, world):
        _, _, client, *_ = world
        r = client.post("/visual/portraits/plr_9999")
        assert r.status_code == 404

    def test_disabled_503(self, world):
        settings, factory, client, cid, location_id = world
        off_settings = settings.model_copy(update={
            "visual": settings.visual.model_copy(update={"enabled": False})
        })
        off_client = TestClient(create_app(off_settings, factory))
        off_client.post("/auth/login", json={"username": "artfan", "password": "password123"})
        for method, url in [
            ("POST", f"/visual/portraits/{cid}"),
            ("POST", "/visual/scenes"),
            ("GET", "/visual/assets/1"),
            ("GET", "/visual/assets/1/file"),
            ("GET", f"/visual/characters/{cid}/portrait"),
        ]:
            r = off_client.request(
                method, url,
                json={"location_id": location_id} if method == "POST" and "scenes" in url else None,
            )
            assert r.status_code == 503, (url, r.status_code, r.text)
