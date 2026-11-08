"""#119 remainder — STORYBOOK_SCENE is a route-level POSITIVE PREFIX (API).

Contract under test (backend/app/api/visual_routes.py + style_presets.py):
- POST /visual/scenes: the TRANSPORT positive prompt is PREFIX-composed
  f"{STORYBOOK_SCENE}, {build_prompt(descriptor)}" — the strengthened
  scene-style constant is prepended at the route call site (no user-facing
  style parameter on scenes).
- The STORED asset prompt / response prompt stays the RAW build_prompt
  bytes (no prefix) — mirrors the m205 TestSceneRouteGuard pin.
- The negative is unchanged by the prefix work: non-nude scenes still
  receive compose_negative(canonical_negative) — composition part
  "bare midriff, exposed stomach, navel" present; the canonical guardrail
  part is prepended when the descriptor carries a visual_profile (#227 2b).
- Portrait isolation: style="oil" still composes f"{OIL_PORTRAIT}, {prompt}"
  and the scene prefix never leaks into a portrait transport prompt.
- Containment guard: the legacy literal "semi-realistic digital painting,
  warm palette, storybook realism" is a substring of STORYBOOK_SCENE —
  this is what keeps the historical prompt-continuity claim true.
- build_prompt isolation: build_prompt scene bytes contain NO storybook
  fragment (the constant is route-level only).

RED discipline: on base c835bf9 the route composes the SUFFIX
(f"{prompt}, {STORYBOOK_SCENE}"), so the prefix-composition assertion
(test_a) FAILS there and PASSES after the change; tests b/c/d/e/f are
regression guards and must pass on BOTH sides of the change. Offline only:
the API runs against a tmp SQLite DB + FakeTransport (no network, no
ComfyUI) — fixture approach reused from tests/m234/test_reference_consume_api.py.
"""
import os
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from app.config.config import load_config  # noqa: E402

SETTINGS = None
PW = "Correct-Horse-9!"

LEGACY_STORYBOOK_PHRASE = (
    "semi-realistic digital painting, warm palette, storybook realism"
)
COMPOSITION_NEGATIVE = "bare midriff, exposed stomach, navel"


def setup_module(module):
    global SETTINGS
    SETTINGS = load_config("config/default.yaml")


def _make_settings(tmp_path):
    visual = SETTINGS.visual.model_copy(update={
        "enabled": True,
        "storage_dir": str(tmp_path / "assets"),
        "references_dir": str(tmp_path / "references"),
        "image_size": "8x8",
    })
    return SETTINGS.model_copy(update={
        "persistence": SETTINGS.persistence.model_copy(
            update={"db_path": str(tmp_path / "w119prefix.db")}
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


def _build(tmp_path):
    from sqlalchemy.orm import sessionmaker

    from app.api.app import create_app
    from app.db.models import bootstrap, create_engine_factory
    from app.world.seed_world import seed_world

    settings = _make_settings(tmp_path)
    engine = create_engine_factory(settings)
    with sessionmaker(bind=engine)() as session:
        bootstrap(engine, settings, seed=42)
        seed_world(session, settings, settings.world.world_id)
        session.commit()
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app(settings, factory)
    client = TestClient(app)
    return settings, engine, factory, app, client


class _FakeTransport:
    """API-test fake (app.state.flux_transport_factory seam): captures the
    positive prompt and the negative passed to the transport."""

    def __init__(self, sink):
        self._sink = sink

    def generate(self, prompt, seed, size, negative="",
                 reference_b64=None):
        self._sink["calls"] = self._sink.get("calls", 0) + 1
        self._sink["prompt"] = prompt
        self._sink["negative"] = negative
        from app.visual.transports import StubFluxTransport

        return StubFluxTransport().generate(prompt, seed, size)


def _mount(app, sink):
    app.state.flux_transport_factory = lambda: _FakeTransport(sink)


def _create_character(client, name="Носитель"):
    r = client.post("/characters", json={
        "name": name, "sex": "F", "age": 31})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _prompt_of(sink):
    prompt = sink.get("prompt")
    assert prompt, f"the transport was never called: {sorted(sink)}"
    return prompt


def _scene_at_player_location(client, cid):
    loc = client.get(f"/characters/{cid}").json()["location_id"]
    r = client.post("/visual/scenes", json={"location_id": loc})
    assert r.status_code == 201, r.text
    return r.json()["asset"]


class TestScenePrefixComposition:
    def test_a_scene_transport_prompt_is_prefix_composed(self, tmp_path):
        """(A) the transport prompt == f"{STORYBOOK_SCENE}, {build_prompt(
        descriptor)}" — the scene style is a POSITIVE PREFIX composed at
        the route level (RED on the base suffix composition)."""
        from app.visual.descriptor import build_prompt
        from app.visual.style_presets import STORYBOOK_SCENE

        settings, _engine, _factory, app, client = _build(tmp_path)
        _register_and_login(client, "m119prefix")
        sink = {}
        _mount(app, sink)
        cid = _create_character(client)

        asset = _scene_at_player_location(client, cid)
        expected = f"{STORYBOOK_SCENE}, {build_prompt(asset['scene_descriptor'])}"
        assert _prompt_of(sink) == expected, (
            f"the scene transport prompt must be PREFIX-composed "
            f"{expected!r}; got {_prompt_of(sink)!r}")

    def test_b_scene_stored_prompt_stays_raw(self, tmp_path):
        """(B) the asset row / response prompt == build_prompt(descriptor)
        byte-exact — no prefix in the STORED bytes (m205
        TestSceneRouteGuard pin)."""
        from app.visual.descriptor import build_prompt

        settings, _engine, _factory, app, client = _build(tmp_path)
        _register_and_login(client, "m119raw")
        sink = {}
        _mount(app, sink)
        cid = _create_character(client)

        asset = _scene_at_player_location(client, cid)
        assert asset["prompt"] == build_prompt(asset["scene_descriptor"]), (
            f"the stored prompt must stay the raw build_prompt bytes; got "
            f"{asset['prompt']!r}")

    def test_c_scene_negative_unchanged_by_prefix(self, tmp_path):
        """(C) the prefix work does not touch the negative: a non-nude
        scene receives compose_negative(canonical_negative) — the
        composition part is present and the canonical guardrail part is
        prepended when the descriptor carries a visual_profile."""
        from app.visual.descriptor import compose_negative

        settings, _engine, _factory, app, client = _build(tmp_path)
        _register_and_login(client, "m119neg")
        sink = {}
        _mount(app, sink)
        cid = _create_character(client)

        asset = _scene_at_player_location(client, cid)
        canonical = (
            asset["scene_descriptor"].get("visual_profile", {})
            .get("guardrail", {}).get("canonical_negative")
        )
        assert canonical, (
            "the player-location scene descriptor must carry a "
            "visual_profile guardrail for this pin")
        assert sink["negative"] == compose_negative(canonical), (
            f"the negative must stay compose_negative(canonical); got "
            f"{sink['negative']!r}")
        assert COMPOSITION_NEGATIVE in sink["negative"], (
            f"the composition part {COMPOSITION_NEGATIVE!r} must ride the "
            f"negative; got {sink['negative']!r}")
        assert sink["negative"].startswith(canonical), (
            f"the canonical part must be prepended; got "
            f"{sink['negative']!r}")

    def test_d_portrait_oil_isolated_from_scene_prefix(self, tmp_path):
        """(D) a portrait render with style="oil" still composes
        f"{OIL_PORTRAIT}, {prompt}" and the scene prefix never leaks into
        the portrait transport prompt."""
        from app.visual.descriptor import build_prompt
        from app.visual.style_presets import (
            OIL_PORTRAIT,
            STORYBOOK_SCENE,
        )

        settings, _engine, _factory, app, client = _build(tmp_path)
        _register_and_login(client, "m119oil")
        sink = {}
        _mount(app, sink)
        cid = _create_character(client)

        r = client.post(f"/visual/portraits/{cid}", json={"style": "oil"})
        assert r.status_code == 201, r.text
        asset = r.json()["asset"]
        expected = f"{OIL_PORTRAIT}, {build_prompt(asset['scene_descriptor'])}"
        assert _prompt_of(sink) == expected, (
            f'the oil portrait transport prompt must be {expected!r}; got '
            f'{_prompt_of(sink)!r}')
        assert STORYBOOK_SCENE not in _prompt_of(sink), (
            f"the scene prefix must never leak into a portrait transport "
            f"prompt; got {_prompt_of(sink)!r}")


class TestScenePrefixContainment:
    def test_e_legacy_storybook_literal_is_contained(self):
        """(E) the legacy literal is a substring of STORYBOOK_SCENE — the
        historical prompt-continuity claim stays true across the
        strengthening."""
        from app.visual.style_presets import STORYBOOK_SCENE

        assert LEGACY_STORYBOOK_PHRASE in STORYBOOK_SCENE, (
            f"STORYBOOK_SCENE ({STORYBOOK_SCENE!r}) must contain the legacy "
            f"literal {LEGACY_STORYBOOK_PHRASE!r}")

    def test_f_build_prompt_has_no_storybook_fragment(self, tmp_path):
        """(F) build_prompt scene bytes contain NO storybook fragment —
        the constant is route-level only (m6 pins build_prompt scene
        bytes directly)."""
        from app.visual.descriptor import build_prompt

        settings, _engine, _factory, app, client = _build(tmp_path)
        _register_and_login(client, "m119iso")
        sink = {}
        _mount(app, sink)
        cid = _create_character(client)

        asset = _scene_at_player_location(client, cid)
        raw = build_prompt(asset["scene_descriptor"])
        assert "storybook" not in raw, (
            f"build_prompt scene bytes must not contain the storybook "
            f"fragment (route-level only); got {raw!r}")
