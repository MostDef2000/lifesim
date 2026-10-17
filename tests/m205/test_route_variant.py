"""W205e — route-level pin: seed-selected auto-outfit variant injection.

PR #212 review finding 1: the portrait-only injection in
``visual_routes._generate_asset``
(``descriptor["outfit_auto_variant"] = _AUTO_OUTFIT_POOL[seed % len(pool)]``)
was covered only by descriptor-level m205 tests; the ROUTE contract was
unpinned. This module pins the three route-level invariants:

1. POST /visual/portraits/{cid} (fresh auto-outfit character, nothing worn):
   the returned asset's scene_descriptor carries "outfit_auto_variant" that
   is a member of _AUTO_OUTFIT_POOL, asset.prompt ends with f", {variant}",
   and the STORED row (GET /visual/assets/{id}) keeps both — the key lands
   in the built prompt AND the persisted descriptor.
2. POST /visual/scenes: the load-bearing assertions are the key absence
   (the injection is guarded by asset_type == "portrait") and the pool
   fragment absence in the scene prompt; the round-trip
   prompt == build_prompt(stored descriptor) is auxiliary — the scene
   branch of build_prompt ignores the variant key, so the round-trip
   alone cannot catch a guard removal.
3. Selection is seed % len(pool), NOT random: for the exact existing-asset
   count N each POST saw, the variant equals
   _AUTO_OUTFIT_POOL[_deterministic_seed(world_id, cid, "portrait", N)
   % len(pool)] and the response seed equals that recomputed seed.

Transport: the stub transport from config/default.yaml (StubFluxTransport,
deterministic placeholder PNG) — the m6 route-test pattern, no monkeypatch.
"""
import os
import random
import sys

import pytest
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from fastapi.testclient import TestClient  # noqa: E402

from app.api.app import create_app  # noqa: E402
from app.api.visual_routes import _deterministic_seed  # noqa: E402
from app.characters.generator import generate_population  # noqa: E402
from app.config.config import load_config  # noqa: E402
from app.db.models import bootstrap, create_engine_factory  # noqa: E402
from app.visual.descriptor import _AUTO_OUTFIT_POOL, build_prompt  # noqa: E402
from app.world.seed_world import seed_world  # noqa: E402

SETTINGS = None


def setup_module(module):
    global SETTINGS
    SETTINGS = load_config("config/default.yaml")


def make_settings(tmp_path):
    """Visual enabled + stub transport (default.yaml transport=stub) + 8x8
    images — the m6 chunk-B route-test settings shape."""
    visual = SETTINGS.visual.model_copy(update={
        "enabled": True,
        "storage_dir": str(tmp_path / "assets"),
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
    """App + populated world + registered player with a fresh auto-outfit
    character (nothing worn — the pool fragment, not the wearing list, ends
    the auto prompt) and one location for scene POSTs."""
    settings = make_settings(tmp_path)
    engine = create_engine_factory(settings)
    with sessionmaker(bind=engine)() as session:
        bootstrap(engine, settings, seed=42)
        seed_world(session, settings, settings.world.world_id)
        rng = random.Random(42)
        generate_population(session, settings, rng, settings.world.world_id, 20)
        session.commit()

    factory = sessionmaker(bind=engine, expire_on_commit=False)
    client = TestClient(create_app(settings, factory))
    r = client.post("/auth/register", json={
        "username": "w205eartfan",
        "email": "w205e@x.com",
        "password": "password123",
        "age_confirmed": True,
    })
    assert r.status_code == 201, r.text
    client.post("/auth/login", json={
        "username": "w205eartfan", "password": "password123"})
    r = client.post("/characters", json={"name": "Pablo", "sex": "M", "age": 25})
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    return settings, factory, client, cid


class TestPortraitRouteInjection:
    def test_descriptor_and_prompt_carry_pool_variant(self, world):
        _, _, client, cid = world
        r = client.post(f"/visual/portraits/{cid}")
        assert r.status_code == 201, r.text
        asset = r.json()["asset"]
        variant = asset["scene_descriptor"]["outfit_auto_variant"]
        assert variant in _AUTO_OUTFIT_POOL
        # the built prompt ends with exactly the selected pool item
        assert asset["prompt"].endswith(f", {variant}")
        # the STORED row keeps both halves of the contract (the response
        # payload is built from the same row, but the GET re-reads the
        # persisted scene_descriptor JSON column — pinning storage too)
        meta = client.get(f"/visual/assets/{asset['id']}").json()
        assert meta["scene_descriptor"]["outfit_auto_variant"] == variant
        assert meta["prompt"] == asset["prompt"]


class TestSceneRouteGuard:
    def test_scene_asset_never_carries_the_variant(self, world):
        _, _, client, cid = world
        location_id = client.get(f"/characters/{cid}").json()["location_id"]
        r = client.post("/visual/scenes", json={"location_id": location_id})
        assert r.status_code == 201, r.text
        scene = r.json()["asset"]
        descriptor = scene["scene_descriptor"]
        # injection is guarded by asset_type == "portrait"
        assert "outfit_auto_variant" not in descriptor
        # no pool fragment rides the scene prompt (tail or anywhere)
        for item in _AUTO_OUTFIT_POOL:
            assert item not in scene["prompt"]
        # prompt bytes are exactly build_prompt over the stored descriptor
        assert scene["prompt"] == build_prompt(descriptor)


class TestSeedSelectionIsDeterministic:
    def test_variant_is_pool_seed_mod_len_per_existing_count(self, world):
        settings, _, client, cid = world
        pool = _AUTO_OUTFIT_POOL
        # Each POST sees existing == n portrait assets (fresh world, no
        # canonical reuse): the route seed is
        # _deterministic_seed(world_id, cid, "portrait", n) and the variant
        # is pool[seed % len(pool)] — a random pick would not reproduce
        # this formula across three regenerations.
        for n in range(3):
            r = client.post(f"/visual/portraits/{cid}")
            assert r.status_code == 201, r.text
            asset = r.json()["asset"]
            expected_seed = _deterministic_seed(
                settings.world.world_id, cid, "portrait", n)
            assert asset["seed"] == expected_seed
            assert asset["scene_descriptor"]["outfit_auto_variant"] == (
                pool[expected_seed % len(pool)])
            # the prompt tail is pinned at EVERY count: the prompt must be
            # built AFTER the injection (mutation M2 — build_prompt before
            # injection — leaves the pool[0] fallback in the tail, which
            # only coincides with the selected variant when seed % len(pool)
            # == 0; across three counts the coincidence probability is
            # ~(1/len(pool))**3, so this kills M2 deterministically).
            assert asset["prompt"].endswith(
                f", {pool[expected_seed % len(pool)]}")
