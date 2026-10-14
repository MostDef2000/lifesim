"""#191 — lazy canonical NPC portrait generation on GET.

The issue body is the spec of record (gh issue view 191; visual spec:
specs/006-flux R8.1, §69/§70). Before #191 NPC portraits 404'd forever:
POST /visual/portraits requires ownership (an NPC has no owner) and GET
/visual/characters/{cid}/portrait was a pure read. Now GET lazily generates
the canonical portrait for an NPC (user_id is None, alive, this world)
exactly once:

- first GET → 200 with the canonical asset row (canonical=true is committed
  in the SAME transaction as the stored file — no poisoned window);
- further GETs (sequential or parallel) never re-call the transport
  (per-(world_id, cid) single-flight lock + double-check inside);
- player characters keep the pure-read contract: own → POST-only (m6 pin:
  GET own without canonical → 404), another player's → 404, no transport;
- dead/unknown character → 404, transport never built;
- transport failure → the SAME 404 JSON as today («canonical portrait not
  found») plus a 300s negative cache: retries inside the TTL do not touch
  the transport; the test-visible reset (clear_npc_portrait_negative_cache,
  mirroring clear_desire_interpretation_cache) or a zeroed TTL re-enables
  generation; a successful generation clears the entry;
- visual.enabled=false → NPC GET is 404 and the transport is never built,
  while player-owned GETs keep the 503 gate (m6/m158 pins).
"""

import os
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from fastapi.testclient import TestClient  # noqa: E402

import app.api.visual_routes as visual_routes  # noqa: E402
from app.api.app import create_app  # noqa: E402
from app.api.visual_routes import (  # noqa: E402
    NPC_PORTRAIT_NEGATIVE_MAX,
    NPC_PORTRAIT_NEGATIVE_TTL_SEC,
    clear_npc_portrait_negative_cache,
)
from app.config.config import load_config  # noqa: E402
from app.db.models import (  # noqa: E402
    Character,
    VisualAsset,
    bootstrap,
    create_engine_factory,
)
from app.visual.transports import StubFluxTransport  # noqa: E402
from app.world.seed_world import seed_world  # noqa: E402

SETTINGS = None


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


def register_and_login(client, username):
    r = client.post("/auth/register", json={
        "username": username, "email": f"{username}@x.com",
        "password": "password123", "age_confirmed": True,
    })
    assert r.status_code == 201, r.text
    client.post("/auth/login", json={"username": username, "password": "password123"})


def pick_npc(factory, settings):
    """Any alive NPC (user_id is NULL) of the world."""
    with factory() as session:
        npc = (
            session.query(Character)
            .filter_by(world_id=settings.world.world_id, user_id=None, alive=True)
            .order_by(Character.id)
            .first()
        )
        assert npc is not None, "seeded world must contain alive NPCs"
        return npc.id


class CountingTransport:
    """Fake Flux transport (app.state.flux_transport_factory seam, m6 style):
    counts generate() calls, returns stub PNG bytes, optionally fails."""

    def __init__(self, calls, fail=False):
        self._calls = calls
        self._fail = fail

    def generate(self, prompt, seed, size):
        self._calls["n"] += 1
        if self._fail:
            raise ValueError("flux down")
        return StubFluxTransport().generate(prompt, seed, size)


@pytest.fixture(autouse=True)
def _reset_npc_portrait_state():
    """The negative cache/locks are module-level: clear around every test."""
    clear_npc_portrait_negative_cache()
    yield
    clear_npc_portrait_negative_cache()


@pytest.fixture()
def world(tmp_path):
    """Visual-enabled app + logged-in player with own character + an NPC id."""
    settings = make_settings(tmp_path)
    engine = build_world(settings)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app(settings, factory)
    client = TestClient(app)
    register_and_login(client, "m191player")
    r = client.post("/characters", json={"name": "Смотритель", "sex": "M", "age": 28})
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    npc_id = pick_npc(factory, settings)
    return settings, app, client, factory, cid, npc_id


class TestLazyNpcGeneration:
    def test_first_get_generates_canonical(self, world):
        settings, app, client, factory, cid, npc_id = world
        wid = settings.world.world_id
        calls = {"n": 0}
        app.state.flux_transport_factory = lambda: CountingTransport(calls)

        r = client.get(f"/visual/characters/{npc_id}/portrait")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["asset_type"] == "portrait"
        assert body["character_id"] == npc_id
        assert body["canonical"] is True
        assert calls["n"] == 1
        # the canonical row exists, committed together with the file
        with factory() as session:
            row = (
                session.query(VisualAsset)
                .filter_by(world_id=wid, asset_type="portrait", character_id=npc_id)
                .one()
            )
            assert row.canonical is True

    def test_second_get_does_not_regenerate(self, world):
        settings, app, client, factory, cid, npc_id = world
        calls = {"n": 0}
        app.state.flux_transport_factory = lambda: CountingTransport(calls)

        first = client.get(f"/visual/characters/{npc_id}/portrait")
        second = client.get(f"/visual/characters/{npc_id}/portrait")
        assert first.status_code == 200 and second.status_code == 200
        assert second.json()["id"] == first.json()["id"]
        assert calls["n"] == 1, "canonical reuse must not re-call the transport"

    def test_parallel_gets_single_transport_call(self, world):
        """Deterministic overlap: the first GET sleeps INSIDE generate (0.5s),
        so the second request provably arrives while the per-cid lock is
        held — it must queue and then hit the canonical double-check, never
        calling the transport itself. (A threading.Barrier(2) would deadlock
        the CORRECT implementation: the second thread must never enter
        generate at all.)"""
        settings, app, client, factory, cid, npc_id = world
        calls = {"n": 0}

        class SlowCountingTransport(CountingTransport):
            def generate(self, prompt, seed, size):
                self._calls["n"] += 1
                time.sleep(0.5)  # widen the race window: both GETs overlap
                return StubFluxTransport().generate(prompt, seed, size)

        app.state.flux_transport_factory = lambda: SlowCountingTransport(calls)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(
                lambda _: client.get(f"/visual/characters/{npc_id}/portrait"),
                range(2),
            ))
        assert [r.status_code for r in results] == [200, 200]
        assert calls["n"] == 1, "parallel GETs must produce exactly ONE transport call"
        assert results[0].json()["id"] == results[1].json()["id"]


class TestGuards:
    def test_dead_character_404_no_transport(self, world):
        settings, app, client, factory, cid, npc_id = world
        calls = {"n": 0}
        app.state.flux_transport_factory = lambda: CountingTransport(calls)
        with factory() as session:
            npc = session.get(Character, npc_id)
            npc.alive = False
            session.commit()

        r = client.get(f"/visual/characters/{npc_id}/portrait")
        assert r.status_code == 404, r.text
        assert r.json()["detail"] == "canonical portrait not found"
        assert calls["n"] == 0

    def test_unknown_character_404_no_transport(self, world):
        settings, app, client, factory, cid, npc_id = world
        calls = {"n": 0}
        app.state.flux_transport_factory = lambda: CountingTransport(calls)

        r = client.get("/visual/characters/npc_9999_unknown/portrait")
        assert r.status_code == 404, r.text
        assert r.json()["detail"] == "canonical portrait not found"
        assert calls["n"] == 0

    def test_other_players_character_404_no_transport(self, world):
        """Privacy: GET never generates/serves another player's portrait —
        and the own-player rule is unchanged (own portraits flow through POST)."""
        settings, app, client, factory, cid, npc_id = world
        other = TestClient(app)
        register_and_login(other, "m191other")
        r = other.post("/characters", json={"name": "Чужой Художник", "sex": "F", "age": 27})
        assert r.status_code == 201, r.text
        other_cid = r.json()["id"]

        calls = {"n": 0}
        app.state.flux_transport_factory = lambda: CountingTransport(calls)
        # another player's character (alive): 404, no generation
        r = client.get(f"/visual/characters/{other_cid}/portrait")
        assert r.status_code == 404, r.text
        assert r.json()["detail"] == "canonical portrait not found"
        assert calls["n"] == 0
        # the owner's own GET is the same 404 (POST-only contract)
        r = other.get(f"/visual/characters/{other_cid}/portrait")
        assert r.status_code == 404, r.text
        assert calls["n"] == 0


class TestFailureHonesty:
    def test_failure_then_negative_cache_then_retry_after_reset(
        self, world, monkeypatch
    ):
        settings, app, client, factory, cid, npc_id = world
        wid = settings.world.world_id
        calls = {"n": 0}
        app.state.flux_transport_factory = lambda: CountingTransport(calls, fail=True)

        r = client.get(f"/visual/characters/{npc_id}/portrait")
        assert r.status_code == 404, r.text
        assert r.json()["detail"] == "canonical portrait not found"
        assert calls["n"] == 1
        assert (wid, npc_id) in visual_routes._npc_portrait_negative

        # immediate retry: negative cache answers 404, transport untouched
        r = client.get(f"/visual/characters/{npc_id}/portrait")
        assert r.status_code == 404
        assert r.json()["detail"] == "canonical portrait not found"
        assert calls["n"] == 1

        # test-visible reset (mirrors clear_desire_interpretation_cache) → retried
        clear_npc_portrait_negative_cache()
        r = client.get(f"/visual/characters/{npc_id}/portrait")
        assert r.status_code == 404
        assert calls["n"] == 2

        # TTL manipulation: a zero TTL makes the entry immediately stale → retry
        monkeypatch.setattr(visual_routes, "NPC_PORTRAIT_NEGATIVE_TTL_SEC", 0.0)
        r = client.get(f"/visual/characters/{npc_id}/portrait")
        assert r.status_code == 404
        assert calls["n"] == 3

    def test_success_clears_negative_entry_and_stops_retrying(self, world):
        settings, app, client, factory, cid, npc_id = world
        wid = settings.world.world_id
        calls = {"n": 0}
        app.state.flux_transport_factory = lambda: CountingTransport(calls, fail=True)
        assert client.get(f"/visual/characters/{npc_id}/portrait").status_code == 404
        assert calls["n"] == 1
        assert (wid, npc_id) in visual_routes._npc_portrait_negative

        # transport recovers; after the reset the GET succeeds
        app.state.flux_transport_factory = lambda: CountingTransport(calls)
        clear_npc_portrait_negative_cache()
        r = client.get(f"/visual/characters/{npc_id}/portrait")
        assert r.status_code == 200, r.text
        asset_id = r.json()["id"]
        assert calls["n"] == 2
        # a SUCCESSFUL generation removes the negative entry
        assert (wid, npc_id) not in visual_routes._npc_portrait_negative
        # and the canonical row now short-circuits every further GET
        r = client.get(f"/visual/characters/{npc_id}/portrait")
        assert r.status_code == 200 and r.json()["id"] == asset_id
        assert calls["n"] == 2


class TestDisabledGate:
    def test_npc_get_disabled_404_transport_never_built(self, tmp_path):
        settings = make_settings(tmp_path, visual_enabled=False)
        engine = build_world(settings)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        app = create_app(settings, factory)
        client = TestClient(app)
        register_and_login(client, "m191offline")
        npc_id = pick_npc(factory, settings)

        def _boom_factory():
            raise AssertionError("transport must never be built while disabled")

        app.state.flux_transport_factory = _boom_factory
        r = client.get(f"/visual/characters/{npc_id}/portrait")
        assert r.status_code == 404, r.text
        assert r.json()["detail"] == "canonical portrait not found"

    def test_disabled_unknown_and_player_character_pins(self, tmp_path):
        """Character guard precedes the disabled gate on the canonical-miss
        path: unknown cid → 404 (no transport), while an alive own player
        character keeps the 503 gate (m6/m158 pins' intent)."""
        settings = make_settings(tmp_path, visual_enabled=False)
        engine = build_world(settings)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        app = create_app(settings, factory)
        client = TestClient(app)
        register_and_login(client, "m191gatepins")
        r = client.post("/characters", json={"name": "Вратарь Гейтов", "sex": "M", "age": 31})
        assert r.status_code == 201, r.text
        own_cid = r.json()["id"]

        def _boom_factory():
            raise AssertionError("transport must never be built while disabled")

        app.state.flux_transport_factory = _boom_factory

        # unknown cid: the guard's 404 precedes the disabled gate
        r = client.get("/visual/characters/npc_9999_unknown/portrait")
        assert r.status_code == 404, r.text
        assert r.json()["detail"] == "canonical portrait not found"

        # alive own player character: the 503 gate still applies
        r = client.get(f"/visual/characters/{own_cid}/portrait")
        assert r.status_code == 503, r.text
        assert r.json()["detail"] == "visual generation disabled"


class TestPlayerFlowUnchanged:
    def test_post_own_portrait_regression(self, world):
        """POST /visual/portraits/{own} keeps the exact pre-#191 flow:
        201 (canonical=false), GET own without canonical → pure-read 404,
        promote → GET serves the canonical asset."""
        settings, app, client, factory, cid, npc_id = world
        calls = {"n": 0}
        app.state.flux_transport_factory = lambda: CountingTransport(calls)

        r = client.post(f"/visual/portraits/{cid}")
        assert r.status_code == 201, r.text
        assert r.json()["reused"] is False
        assert r.json()["asset"]["canonical"] is False
        asset_id = r.json()["asset"]["id"]
        assert calls["n"] == 1
        # own GET without canonical: still 404 (no lazy generation for players)
        r = client.get(f"/visual/characters/{cid}/portrait")
        assert r.status_code == 404, r.text
        assert calls["n"] == 1
        # promote → GET found-branch serves it, no new generation
        r = client.post(f"/visual/assets/{asset_id}/canonical", json={"canonical": True})
        assert r.status_code == 200, r.text
        r = client.get(f"/visual/characters/{cid}/portrait")
        assert r.status_code == 200, r.text
        assert r.json()["canonical"] is True
        assert r.json()["id"] == asset_id
        assert calls["n"] == 1


class TestNegativeCacheMechanics:
    def test_eviction_capped_expired_first_then_oldest(self):
        """Cap 512: expired entries dropped first, then oldest (desire-cache
        discipline; pop-based removal only)."""
        clear_npc_portrait_negative_cache()
        now = 10_000.0
        for i in range(NPC_PORTRAIT_NEGATIVE_MAX):
            visual_routes._npc_portrait_negative[("w", f"k{i}")] = now - 10.0
        # one expired entry and one fresh entry on top of the full cache
        visual_routes._npc_portrait_negative[("w", "expired")] = (
            now - NPC_PORTRAIT_NEGATIVE_TTL_SEC - 1.0
        )
        visual_routes._npc_portrait_negative[("w", "fresh")] = now

        visual_routes._evict_npc_portrait_negative(now)

        cache = visual_routes._npc_portrait_negative
        assert len(cache) == NPC_PORTRAIT_NEGATIVE_MAX
        assert ("w", "expired") not in cache
        assert ("w", "fresh") in cache  # newest entry survives
        clear_npc_portrait_negative_cache()
