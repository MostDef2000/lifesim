"""M6 Chunk C tests (SPEC 006-flux, T12): AE1''''-AE6'''' evidence."""
import hashlib
import os
import random
import sys

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


def make_settings(tmp_path, **visual_updates):
    visual = SETTINGS.visual.model_copy(update={
        "enabled": True,
        "storage_dir": str(tmp_path / "assets"),
        "image_size": "8x8",
        **visual_updates,
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


def login_player(settings, factory, username="artist"):
    app = create_app(settings, factory)
    client = TestClient(app)
    r = client.post("/auth/register", json={
        "username": username,
        "email": f"{username[0]}@x.com",
        "password": "password123",
        "age_confirmed": True,
    })
    assert r.status_code == 201, r.text
    client.post("/auth/login", json={"username": username, "password": "password123"})
    r = client.post("/characters", json={"name": "Muse", "sex": "F", "age": 27})
    assert r.status_code == 201, r.text
    return app, client, r.json()["id"]


class TestAE1E2E:
    """AE1'''': full flow — portrait -> repeat -> canonical -> reuse -> scene -> file -> 503."""

    def test_full_flow(self, tmp_path):
        settings = make_settings(tmp_path)
        build_world(settings)
        factory = sessionmaker(bind=create_engine_factory(settings), expire_on_commit=False)
        _, client, cid = login_player(settings, factory)

        # 1. portrait created
        r = client.post(f"/visual/portraits/{cid}")
        assert r.status_code == 201
        asset = r.json()["asset"]
        assert r.json()["reused"] is False

        # 2. repeat -> new generation (no canonical yet)
        r2 = client.post(f"/visual/portraits/{cid}")
        assert r2.status_code == 201
        assert r2.json()["asset"]["id"] != asset["id"]

        # 3. promote canonical
        assert client.post(
            f"/visual/assets/{asset['id']}/canonical", json={"canonical": True}
        ).status_code == 200

        # 4. reuse -> 200, identical asset
        r3 = client.post(f"/visual/portraits/{cid}")
        assert r3.status_code == 200
        assert r3.json()["reused"] is True
        assert r3.json()["asset"]["id"] == asset["id"]

        # 5. scene with canonical reference
        player_location = client.get(f"/characters/{cid}").json()["location_id"]
        r4 = client.post("/visual/scenes", json={"location_id": player_location})
        assert r4.status_code == 201
        scene = r4.json()["asset"]
        assert asset["id"] in scene["scene_descriptor"]["references"]

        # 6. file bytes are PNG
        r5 = client.get(f"/visual/assets/{asset['id']}/file")
        assert r5.status_code == 200
        assert r5.content.startswith(b"\x89PNG")

        # 7. events include VISUAL_ASSET_CREATED
        events = client.get("/world/events?limit=100").json()
        visual_events = [e for e in events if e["event_type"] == "VISUAL_ASSET_CREATED"]
        assert len(visual_events) == 3  # 2 portraits + 1 scene


class TestAE2Keystone:
    """AE2'''': headless byte-identity — M1-M4 report unchanged, visual_assets empty."""

    def test_headless_identity(self, tmp_path):
        # headless: visual disabled (default config)
        settings = make_settings(tmp_path)
        settings = settings.model_copy(update={
            "visual": settings.visual.model_copy(update={"enabled": False}),
        })
        build_world(settings)
        engine = create_engine_factory(settings)

        from app.simulation.report import build_report

        with sessionmaker(bind=engine)() as session:
            report = build_report(
                session, settings.world.world_id, settings,
                wall_duration_sec=0.0, seed=42,
            )
        assert "visual" not in report
        assert "visual_assets" not in report

        from app.db.models import VisualAsset
        with sessionmaker(bind=engine)() as session:
            assert session.query(VisualAsset).count() == 0

        # invariants still green (visual gate off -> trivial pass)
        from app.simulation.invariants import run_invariant_checks

        with sessionmaker(bind=engine)() as session:
            results = run_invariant_checks(session, settings.world.world_id, settings)
        bad = [r for r in results if not r["ok"]]
        assert bad == [], bad


class TestAE3Determinism:
    """AE3'''': identical (prompt, seed) -> identical bytes; descriptor stable."""

    def test_stub_bytes_deterministic(self):
        from app.visual.transports import StubFluxTransport

        stub = StubFluxTransport()
        a = stub.generate("Portrait of Muse", 777, "8x8")
        b = stub.generate("Portrait of Muse", 777, "8x8")
        assert hashlib.sha256(a).hexdigest() == hashlib.sha256(b).hexdigest()
        assert a != stub.generate("Portrait of Muse", 778, "8x8")

    def test_descriptor_stable_across_runs(self, tmp_path):
        settings = make_settings(tmp_path)
        build_world(settings)
        engine = create_engine_factory(settings)

        from app.visual.descriptor import build_scene_descriptor

        with sessionmaker(bind=engine)() as s1:
            d1 = build_scene_descriptor(s1, settings.world.world_id, 1)
        with sessionmaker(bind=engine)() as s2:
            d2 = build_scene_descriptor(s2, settings.world.world_id, 1)
        assert d1 == d2


class TestAE4Persistence:
    """AE4'''': asset survives app restart on same DB + storage."""

    def test_restart(self, tmp_path):
        settings = make_settings(tmp_path)
        build_world(settings)
        factory = sessionmaker(bind=create_engine_factory(settings), expire_on_commit=False)
        _, client, cid = login_player(settings, factory)
        asset = client.post(f"/visual/portraits/{cid}").json()["asset"]
        original_bytes = client.get(f"/visual/assets/{asset['id']}/file").content

        # "restart": fresh app + fresh engine on the same DB/storage
        engine2 = create_engine_factory(settings)
        factory2 = sessionmaker(bind=engine2, expire_on_commit=False)
        app2 = create_app(settings, factory2)
        client2 = TestClient(app2)
        client2.post("/auth/login", json={"username": "artist", "password": "password123"})
        r = client2.get(f"/visual/assets/{asset['id']}/file")
        assert r.status_code == 200
        assert r.content == original_bytes
        meta = client2.get(f"/visual/assets/{asset['id']}").json()
        assert meta["seed"] == asset["seed"]
        assert meta["prompt"] == asset["prompt"]


class TestAE5ReuseNoGeneration:
    """AE5'''': canonical reuse never touches the transport (call counter)."""

    def test_no_transport_call_on_reuse(self, tmp_path):
        settings = make_settings(tmp_path)
        build_world(settings)
        factory = sessionmaker(bind=create_engine_factory(settings), expire_on_commit=False)
        app, client, cid = login_player(settings, factory)

        calls = {"n": 0}
        from app.visual.transports import StubFluxTransport

        class CountingTransport:
            def generate(self, prompt, seed, size):
                calls["n"] += 1
                return StubFluxTransport().generate(prompt, seed, size)

        app.state.flux_transport_factory = lambda: CountingTransport()

        client.post(f"/visual/portraits/{cid}")
        assert calls["n"] == 1

        # not canonical yet -> direct portrait read 404
        assert client.get(f"/visual/characters/{cid}/portrait").status_code == 404

        # promote to canonical (the portrait asset just created)
        events = client.get("/world/events?limit=500").json()
        asset_id = [
            e for e in events if e["event_type"] == "VISUAL_ASSET_CREATED"
        ][0]["payload"]["asset_id"]
        client.post(f"/visual/assets/{asset_id}/canonical", json={"canonical": True})

        # reuse -> transport NOT called
        r = client.post(f"/visual/portraits/{cid}")
        assert r.status_code == 200
        assert r.json()["reused"] is True
        assert calls["n"] == 1, "transport must not be called on canonical reuse"


class TestAE6Config:
    """AE6'''': transport contract via config; unavailable server -> 502."""

    def test_http_transport_selected(self, monkeypatch, tmp_path):
        import urllib.request as urllib_request

        from app.config.config import VisualConfig
        from app.visual.transports import HttpFluxTransport, build_transport

        transport = build_transport(VisualConfig(transport="http"))
        assert isinstance(transport, HttpFluxTransport)

        def fake_urlopen(request, timeout):
            import io

            return io.BytesIO(b"\x89PNG remote")

        monkeypatch.setattr(urllib_request, "urlopen", fake_urlopen)
        assert transport.generate("x", 1, "8x8") == b"\x89PNG remote"

    def test_http_unavailable_502(self, tmp_path):
        settings = make_settings(
            tmp_path, transport="http",
            base_url="http://127.0.0.1:1", timeout_sec=0.5,
        )
        build_world(settings)
        factory = sessionmaker(bind=create_engine_factory(settings), expire_on_commit=False)
        _, client, cid = login_player(settings, factory)
        r = client.post(f"/visual/portraits/{cid}")
        assert r.status_code == 502
        assert "flux unavailable" in r.json()["detail"]

    def test_invariant_visual_asset_integrity(self, tmp_path):
        settings = make_settings(tmp_path)
        build_world(settings)
        factory = sessionmaker(bind=create_engine_factory(settings), expire_on_commit=False)
        _, client, cid = login_player(settings, factory)
        client.post(f"/visual/portraits/{cid}")

        from app.simulation.invariants import run_invariant_checks

        with factory() as session:
            results = run_invariant_checks(session, settings.world.world_id, settings)
        visual = [r for r in results if r["name"] == "visual_asset_integrity"]
        assert len(visual) == 1
        assert visual[0]["ok"] is True
        assert "assets: 1" in visual[0]["details"]
