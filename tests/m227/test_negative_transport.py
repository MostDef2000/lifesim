"""#227 Phase 2b (lane-A half) — negative prompt rides the transport contract.

Contract under test (#227 lever B, spec 019 Phase 2b):
- prod evidence: prompt-side framing phrases cannot suppress the Flux
  waist-crop prior (0/3 this round, 0/4 in the #227 preset round); a canvas
  bump is blocked (flux fp8 93.5% VRAM at 512; spec 019 excludes >512).
  The remaining spec-sanctioned lever: the transport carries a NEGATIVE.
- descriptor.compose_negative(canonical) joins the canonical safety negative
  (visual_profile.guardrail.canonical_negative; may be None/empty) with the
  composition constant — canonical first, composition second, empty parts
  dropped, no double commas. Deterministic.
- transports: generate() gains a trailing optional `negative: str = ""` on
  the protocol, the stub (accepted; renderer stays a pure function of
  prompt+seed+size) and HttpFluxTransport (always includes "negative" in
  the JSON payload — fixed schema, old host adapters ignore it).
- routes pass compose_negative(canonical from the descriptor's
  visual_profile guardrail, None when absent) at BOTH call sites, via a
  compat dispatch so legacy 3-arg transports (existing test fakes) keep
  working ("keep all existing call signatures backward-compatible").
- deploy/comfy_adapter.py (repo copy; HOST deploy = later lane-B handoff):
  do_POST reads str(data.get("negative", "")) and APPENDS it to the adapter
  floor (build_negative) — augment, never replace. Payloads without the
  field keep byte-identical behavior.

RED discipline: on base 193b19a the legs fail because the negative field /
param does not exist (descriptor symbols missing → ImportError at
collection; transport param missing → TypeError; capture sink shows the
sentinel default).
"""
import io
import json
import os
import random
import sys
import threading
import urllib.request
from http.server import ThreadingHTTPServer

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../deploy"))

from app.config.config import VisualConfig, load_config  # noqa: E402
from app.db.models import (  # noqa: E402
    Character,
    bootstrap,
    create_engine_factory,
)
from app.visual.descriptor import (  # noqa: E402
    _COMPOSITION_NEGATIVE,
    _PROFILE_CANONICAL_NEGATIVE,
    compose_negative,
)
from app.visual.transports import HttpFluxTransport  # noqa: E402
from app.world.seed_world import seed_world  # noqa: E402

SETTINGS = None
PW = "Correct-Horse-9!"

PREVIEW_BODY = {
    "sex": "F",
    "age": 27,
    "appearance_prompt": "выразительные глаза",
    "appearance": {"face": "мягкие черты", "hair_color": "рыжий"},
}


def setup_module(module):
    global SETTINGS
    SETTINGS = load_config("config/default.yaml")


def _make_settings(tmp_path):
    visual = SETTINGS.visual.model_copy(update={
        "enabled": True,
        "storage_dir": str(tmp_path / "assets"),
        "image_size": "8x8",
    })
    return SETTINGS.model_copy(update={
        "persistence": SETTINGS.persistence.model_copy(
            update={"db_path": str(tmp_path / "w227.db")}
        ),
        "visual": visual,
    })


def _register_and_login(client, username):
    r = client.post("/auth/register", json={
        "username": username, "email": f"{username}@x.com",
        "password": PW, "age_confirmed": True,
    })
    assert r.status_code == 201, r.text
    r = client.post("/auth/login", json={"username": username, "password": PW})
    assert r.status_code == 200, r.text


def _build(tmp_path, username):
    from sqlalchemy.orm import sessionmaker

    from app.characters.generator import generate_population

    settings = _make_settings(tmp_path)
    engine = create_engine_factory(settings)
    with sessionmaker(bind=engine)() as session:
        bootstrap(engine, settings, seed=42)
        seed_world(session, settings, settings.world.world_id)
        generate_population(
            session, settings, random.Random(42),
            settings.world.world_id, 20,
        )
        session.commit()
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    from app.api.app import create_app  # noqa: E402

    app = create_app(settings, factory)
    client = TestClient(app)
    _register_and_login(client, username)
    return settings, engine, factory, app, client


def _npc_location(engine):
    from sqlalchemy.orm import sessionmaker

    with sessionmaker(bind=engine)() as session:
        npc = (
            session.query(Character)
            .filter(Character.user_id.is_(None), Character.alive.is_(True))
            .first()
        )
        assert npc is not None
        return npc.location_id


class _CaptureTransport:
    """Sink-dict capture fake (m61 _CaptureTransport style) that renders
    real stub PNG bytes. `negative` defaults to a sentinel so the RED run
    shows "the call never carried the field" instead of a TypeError."""

    SENTINEL = "<negative-not-passed>"

    def __init__(self, sink):
        self._sink = sink

    def generate(self, prompt, seed, size, negative=SENTINEL):
        self._sink["args"] = (prompt, seed, size)
        self._sink["negative"] = negative
        from app.visual.transports import StubFluxTransport

        return StubFluxTransport().generate(prompt, seed, size)


class _LegacyTransport:
    """Exact 3-arg fake (m216 BoomTransport family shape): the negative
    must NOT be passed to it (compat dispatch keeps old signatures)."""

    def __init__(self, sink):
        self._sink = sink

    def generate(self, prompt, seed, size):
        self._sink["args"] = (prompt, seed, size)
        from app.visual.transports import StubFluxTransport

        return StubFluxTransport().generate(prompt, seed, size)


class TestComposedNegativeRidesGeneration:
    def test_a_preview_capture_canonical_plus_composition(self, tmp_path):
        """(a) preview path: the transport call carries the exact composed
        negative — canonical safety floor first, composition second."""
        settings, engine, factory, app, client = _build(tmp_path, "w227pre")
        sink = {}
        app.state.flux_transport_factory = lambda: _CaptureTransport(sink)

        r = client.post("/visual/preview", json=PREVIEW_BODY)
        assert r.status_code == 200, r.text
        expected = compose_negative(_PROFILE_CANONICAL_NEGATIVE)
        assert sink["negative"] == expected, (
            f"preview transport call must carry the composed negative "
            f"{expected!r}; captured {sink['negative']!r}"
        )
        assert expected == (
            f"{_PROFILE_CANONICAL_NEGATIVE}, {_COMPOSITION_NEGATIVE}"
        )

    def test_b_scene_without_player_composition_only(self, tmp_path):
        """(b) scene path, caller has no character (no visual_profile in
        the descriptor) → the negative is composition-only; the safety
        floor rides every render via the adapter."""
        settings, engine, factory, app, client = _build(tmp_path, "w227scene")
        sink = {}
        app.state.flux_transport_factory = lambda: _CaptureTransport(sink)

        r = client.post(
            "/visual/scenes", json={"location_id": _npc_location(engine)})
        assert r.status_code == 201, r.text
        assert sink["negative"] == _COMPOSITION_NEGATIVE, (
            f"scene without a player profile must carry composition-only "
            f"negative {_COMPOSITION_NEGATIVE!r}; captured "
            f"{sink['negative']!r}"
        )

    def test_b2_scene_with_player_canonical_plus_composition(self, tmp_path):
        """(b2) scene path, caller HAS a character (visual_profile present)
        → canonical + composition, same as the portrait/preview paths."""
        settings, engine, factory, app, client = _build(tmp_path, "w227scen2")
        sink = {}
        app.state.flux_transport_factory = lambda: _CaptureTransport(sink)

        r = client.post("/characters", json={
            "name": "Носитель", "sex": "F", "age": 31})
        assert r.status_code == 201, r.text
        cid = r.json()["id"]
        loc = client.get(f"/characters/{cid}").json()["location_id"]
        r = client.post("/visual/scenes", json={"location_id": loc})
        assert r.status_code == 201, r.text
        assert sink["negative"] == compose_negative(
            _PROFILE_CANONICAL_NEGATIVE), (
            f"scene with a player profile must carry canonical + "
            f"composition; captured {sink['negative']!r}"
        )


class TestTransportContract:
    def test_c_http_payload_includes_negative_key(self, monkeypatch):
        """(c) HttpFluxTransport always includes "negative" in the JSON
        payload (fixed schema; old adapters ignore unknown fields)."""
        captured = {}

        def fake_urlopen(request, timeout):
            captured["data"] = json.loads(request.data)
            return io.BytesIO(b"\x89PNG fake-bytes")

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        config = VisualConfig(
            transport="http", base_url="http://flux.internal:7860",
            model="flux.1-schnell", lora="vl1-style", timeout_sec=5.0,
        )
        HttpFluxTransport(config).generate("p", 1, "8x8", negative="n terms")
        assert captured["data"]["negative"] == "n terms", (
            f"payload must carry the negative field; got "
            f"{sorted(captured['data'])}"
        )
        # default (no arg): fixed schema keeps the key, empty value.
        HttpFluxTransport(config).generate("p", 1, "8x8")
        assert captured["data"]["negative"] == ""

    def test_e_legacy_three_arg_transport_still_works(self, tmp_path):
        """(e) compat dispatch: an exact 3-arg transport (existing test
        fakes) keeps working — the negative rides only when the signature
        declares it ("keep all existing call signatures backward-compatible")."""
        settings, engine, factory, app, client = _build(tmp_path, "w227leg")
        sink = {}
        app.state.flux_transport_factory = lambda: _LegacyTransport(sink)

        r = client.post("/visual/preview", json=PREVIEW_BODY)
        assert r.status_code == 200, r.text
        assert "image_base64" in r.json()
        assert len(sink["args"]) == 3


class TestAdapterAugments:
    def _patch_comfy(self, monkeypatch, sink):
        import comfy_adapter

        def fake_post(path, payload):
            sink["graph"] = payload["prompt"]
            return {"prompt_id": "job-1"}

        def fake_history(prompt_id):
            return {"job-1": {
                "status": {"status_str": "success"},
                "outputs": {"7": {"images": [{
                    "filename": "lifesim_00001_.png",
                    "subfolder": "", "type": "output",
                }]}},
            }}

        monkeypatch.setattr(comfy_adapter, "comfy_post", fake_post)
        monkeypatch.setattr(comfy_adapter, "comfy_history", fake_history)
        monkeypatch.setattr(
            comfy_adapter, "comfy_view",
            lambda *a, **k: b"\x89PNG fake-bytes")
        return comfy_adapter

    def test_d_payload_with_negative_appends_to_floor(self, monkeypatch):
        """(d) payload WITH negative → node-3 text is adapter floor + ",
        " + negative (augment, never replace)."""
        sink = {}
        adapter = self._patch_comfy(monkeypatch, sink)
        adapter.generate_png(
            "flux", "p", 1, "512x512", "",
            negative="bare midriff, exposed stomach, navel")
        node3 = sink["graph"]["3"]["inputs"]["text"]
        assert node3 == (
            adapter.build_negative("flux")
            + ", bare midriff, exposed stomach, navel"
        ), f"node-3 must be floor + app negative; got {node3!r}"

    def test_d4_pony_negative_appends_after_prefix_and_floor(
            self, monkeypatch):
        """(d) pony path: the app negative lands after the pony booru-style
        prefix + floor — exact bytes PONY_NEGATIVE_PREFIX + NEGATIVE
        + ", " + negative."""
        sink = {}
        adapter = self._patch_comfy(monkeypatch, sink)
        adapter.generate_png(
            "pony", "p", 1, "512x512", "", negative="sussy terms")
        node3 = sink["graph"]["3"]["inputs"]["text"]
        assert node3 == (
            adapter.PONY_NEGATIVE_PREFIX
            + adapter.NEGATIVE
            + ", sussy terms"
        ), (
            f"pony node-3 must be prefix + floor + app negative; "
            f"got {node3!r}"
        )

    def test_d2_payload_without_negative_byte_identical_floor(
            self, monkeypatch):
        """(d) backward-compat pin: payload WITHOUT the field → node-3
        text is EXACTLY the legacy floor (old behavior byte-identical)."""
        sink = {}
        adapter = self._patch_comfy(monkeypatch, sink)
        adapter.generate_png("flux", "p", 1, "512x512", "")
        node3 = sink["graph"]["3"]["inputs"]["text"]
        assert node3 == adapter.build_negative("flux"), (
            f"legacy payload must keep the exact floor; got {node3!r}"
        )

    def test_d3_do_POST_reads_negative_field(self, monkeypatch):
        """(d) do_POST threads str(data.get("negative", "")) through to
        generate_png."""
        sink = {}
        adapter = self._patch_comfy(monkeypatch, sink)
        seen = {}
        real_generate_png = adapter.generate_png

        def spy_generate_png(model_key, prompt, seed, size, lora,
                             negative=""):
            seen["negative"] = negative
            return real_generate_png(model_key, prompt, seed, size, lora,
                                     negative)

        monkeypatch.setattr(adapter, "generate_png", spy_generate_png)
        server = ThreadingHTTPServer(("127.0.0.1", 0), adapter.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            payload = json.dumps({
                "prompt": "p", "seed": 1, "size": "512x512",
                "model": "flux", "lora": "",
                "negative": "extra terms",
            }).encode()
            request = urllib.request.Request(
                f"http://127.0.0.1:{port}/generate", data=payload,
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=10) as response:
                assert response.read().startswith(b"\x89PNG")
        finally:
            server.shutdown()
        assert seen["negative"] == "extra terms", (
            f"do_POST must read the negative field; got {seen['negative']!r}"
        )
