"""020 P3 stage 2 — reference consumption + style presets + nude flag (API).

Contract under test (backend/app/api/visual_routes.py):
- POST /visual/portraits/{cid} accepts OPTIONAL JSON fields:
  reference_id (int|None = None), style (""|"oil"|"watercolor" = ""),
  nude (bool = False). The body itself stays optional (existing no-body
  POSTs — m6/m193/m205/m230 — must keep answering 201).
- reference_id missing or owned by another user -> 404 with the SAME uniform
  JSON error shape the stage-1 file endpoint answers (dynamic oracle: the
  404 body of GET /visual/references/{missing}/file) and NO transport call.
- reference_id of a non-int type -> 422 (int|None schema).
- style not in {"", "oil", "watercolor"} -> 422 (before any generation).
- style="oil" -> the positive prompt passed to the transport starts with
  "oil painting, "; style="watercolor" -> starts with "watercolor painting, ";
  default portrait -> no preset prefix.
- nude=True -> positive contains "artistic nude figure study" and NO
  clothing terms ("wearing", "dress", "coat", "overalls"); nude=False ->
  unchanged behavior (the clothed auto-outfit fragment stays).
- POST /visual/scenes: optional reference_id with the same 404 semantics;
  the positive prompt ALWAYS contains the storybook suffix constant
  "semi-realistic digital painting, warm palette, storybook realism"
  (server-side; no user-facing style param on scenes this wave).
- Reference bytes flow: a real stage-1 upload + FakeTransport -> the
  captured reference_b64 equals base64 of the stored file bytes.
- Auth unchanged: unauthenticated -> 401.

Preset strings live in backend/app/visual/style_presets.py
(STORYBOOK_SCENE, OIL_PORTRAIT, WATERCOLOR_PORTRAIT, NUDE_FRAMING) —
imported INSIDE the tests so a not-yet-landed module isolates as a per-test
failure instead of a collection error.

RED discipline: on base 0a2a25e the routes take no request body, so the new
fields are silently ignored (201 where 422/404 is required, legacy prompt
shape) and app.visual.style_presets does not exist. Offline only: the API
runs against a tmp SQLite DB + FakeTransport (no network, no ComfyUI).
"""
import base64
import os
import sys
from io import BytesIO

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from app.config.config import load_config  # noqa: E402

SETTINGS = None
PW = "Correct-Horse-9!"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

OIL_PREFIX = "oil painting, "
WATERCOLOR_PREFIX = "watercolor painting, "
NUDE_FRAMING_PHRASE = "artistic nude figure study"
STORYBOOK_PHRASE = "semi-realistic digital painting, warm palette, storybook realism"
CLOTHING_TERMS = ("wearing", "dress", "coat", "overalls")


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
            update={"db_path": str(tmp_path / "w234.db")}
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


def _png_bytes():
    """Real 8x8 PNG, built at runtime — never a committed binary fixture."""
    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (8, 8), (17, 90, 140)).save(buf, format="PNG")
    return buf.getvalue()


def _upload_reference(client, data=None):
    data = _png_bytes() if data is None else data
    r = client.post("/visual/references", files={
        "file": ("ref.png", data, "image/png")})
    assert r.status_code == 201, r.text
    return r.json()["id"]


class _FakeTransport:
    """API-test fake (app.state.flux_transport_factory seam): captures the
    positive prompt and the optional reference_b64. The reference default is
    a SENTINEL so a RED run shows "the call never carried the reference"
    instead of a silent None."""

    SENTINEL = "<reference-not-passed>"

    def __init__(self, sink):
        self._sink = sink

    def generate(self, prompt, seed, size, negative="",
                 reference_b64=SENTINEL):
        self._sink["calls"] = self._sink.get("calls", 0) + 1
        self._sink["prompt"] = prompt
        self._sink["negative"] = negative
        self._sink["reference_b64"] = reference_b64
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


class TestPortraitStylePresets:
    def test_a_oil_prefixes_the_positive_prompt(self, tmp_path):
        """(A) style="oil" -> the transport prompt starts with the OIL
        preset prefix (literal bytes + the style_presets constant)."""
        settings, engine, factory, app, client = _build(tmp_path)
        _register_and_login(client, "m234oil")
        sink = {}
        _mount(app, sink)
        cid = _create_character(client)

        r = client.post(f"/visual/portraits/{cid}", json={"style": "oil"})
        assert r.status_code == 201, r.text
        prompt = _prompt_of(sink)
        assert prompt.startswith(OIL_PREFIX), (
            f'style="oil" must prefix the positive prompt with '
            f'{OIL_PREFIX!r}; got {prompt[:90]!r}')
        from app.visual.style_presets import OIL_PORTRAIT

        assert prompt.startswith(f"{OIL_PORTRAIT.rstrip(', ')}, "), (
            f"the prompt prefix must be the OIL_PORTRAIT constant "
            f"({OIL_PORTRAIT!r}); got {prompt[:90]!r}")

    def test_b_watercolor_prefixes_the_positive_prompt(self, tmp_path):
        """(B) style="watercolor" -> the WATERCOLOR preset prefix."""
        settings, engine, factory, app, client = _build(tmp_path)
        _register_and_login(client, "m234water")
        sink = {}
        _mount(app, sink)
        cid = _create_character(client)

        r = client.post(f"/visual/portraits/{cid}",
                        json={"style": "watercolor"})
        assert r.status_code == 201, r.text
        prompt = _prompt_of(sink)
        assert prompt.startswith(WATERCOLOR_PREFIX), (
            f'style="watercolor" must prefix the positive prompt with '
            f'{WATERCOLOR_PREFIX!r}; got {prompt[:90]!r}')
        from app.visual.style_presets import WATERCOLOR_PORTRAIT

        assert prompt.startswith(
            f"{WATERCOLOR_PORTRAIT.rstrip(', ')}, "), (
            f"the prompt prefix must be the WATERCOLOR_PORTRAIT constant "
            f"({WATERCOLOR_PORTRAIT!r}); got {prompt[:90]!r}")

    def test_c_default_portrait_has_no_preset_prefix(self, tmp_path):
        """(C) default (style="" or absent) -> no preset prefix, the plain
        portrait prompt starts with "Portrait of "."""
        settings, engine, factory, app, client = _build(tmp_path)
        _register_and_login(client, "m234plain")
        sink = {}
        _mount(app, sink)
        cid = _create_character(client)

        r = client.post(f"/visual/portraits/{cid}", json={"style": ""})
        assert r.status_code == 201, r.text
        prompt = _prompt_of(sink)
        assert prompt.startswith("Portrait of "), (
            f"default portrait prompt must keep the legacy head; "
            f"got {prompt[:90]!r}")
        assert not prompt.startswith(OIL_PREFIX), prompt[:90]
        assert not prompt.startswith(WATERCOLOR_PREFIX), prompt[:90]

    def test_d_unknown_style_is_422_before_generation(self, tmp_path):
        """(D) style outside {"", "oil", "watercolor"} -> 422, no transport."""
        settings, engine, factory, app, client = _build(tmp_path)
        _register_and_login(client, "m234badstyle")
        sink = {}
        _mount(app, sink)
        cid = _create_character(client)

        r = client.post(f"/visual/portraits/{cid}", json={"style": "gouache"})
        assert r.status_code == 422, (
            f'style="gouache" must be 422; got {r.status_code}: {r.text}')
        assert "calls" not in sink, (
            "an invalid style must be rejected before any generation")

    def test_e_portrait_post_without_body_still_works(self, tmp_path):
        """(E) backward-compat pin: the body is OPTIONAL — the historical
        no-body POST /visual/portraits/{cid} keeps answering 201."""
        settings, engine, factory, app, client = _build(tmp_path)
        _register_and_login(client, "m234nobody")
        sink = {}
        _mount(app, sink)
        cid = _create_character(client)

        r = client.post(f"/visual/portraits/{cid}")
        assert r.status_code == 201, (
            f"no-body portrait POST must keep working; "
            f"got {r.status_code}: {r.text}")
        assert "calls" in sink


class TestNudeFlag:
    def test_a_nude_true_framing_replaces_the_outfit(self, tmp_path):
        """(A) nude=True -> the intentional fine-art nude framing phrase and
        NO clothing terms anywhere in the positive prompt."""
        settings, engine, factory, app, client = _build(tmp_path)
        _register_and_login(client, "m234nude")
        sink = {}
        _mount(app, sink)
        cid = _create_character(client)

        r = client.post(f"/visual/portraits/{cid}", json={"nude": True})
        assert r.status_code == 201, r.text
        prompt = _prompt_of(sink)
        assert NUDE_FRAMING_PHRASE in prompt, (
            f"nude=True must frame the prompt with "
            f"{NUDE_FRAMING_PHRASE!r}; got {prompt!r}")
        for term in CLOTHING_TERMS:
            assert term not in prompt, (
                f"nude=True prompt must not carry the clothing term "
                f"{term!r}: {prompt!r}")
        from app.visual.style_presets import NUDE_FRAMING

        assert NUDE_FRAMING.rstrip(", ") in prompt, (
            f"the prompt must carry the NUDE_FRAMING constant "
            f"({NUDE_FRAMING!r}); got {prompt!r}")

    def test_b_nude_false_keeps_the_clothed_prompt(self, tmp_path):
        """(B) nude=False -> unchanged behavior: no nude framing, the
        clothed auto-outfit fragment stays."""
        settings, engine, factory, app, client = _build(tmp_path)
        _register_and_login(client, "m234clothed")
        sink = {}
        _mount(app, sink)
        cid = _create_character(client)

        r = client.post(f"/visual/portraits/{cid}", json={"nude": False})
        assert r.status_code == 201, r.text
        prompt = _prompt_of(sink)
        assert prompt.startswith("Portrait of "), prompt[:90]
        assert NUDE_FRAMING_PHRASE not in prompt, (
            f"nude=False must not add the nude framing: {prompt!r}")
        assert "fully clothed" in prompt, (
            f"nude=False must keep the default clothed outfit fragment: "
            f"{prompt!r}")


class TestReferenceConsumption:
    def test_a_missing_reference_is_404_uniform_shape_no_generation(
            self, tmp_path):
        """(A) reference_id that does not exist -> the SAME 404 JSON shape
        as the stage-1 file endpoint, and the transport is never called."""
        settings, engine, factory, app, client = _build(tmp_path)
        _register_and_login(client, "m234miss")
        sink = {}
        _mount(app, sink)
        cid = _create_character(client)

        # dynamic oracle: the uniform stage-1 error shape
        stage1 = client.get("/visual/references/999999/file")
        assert stage1.status_code == 404, stage1.text

        r = client.post(f"/visual/portraits/{cid}",
                        json={"reference_id": 999999})
        assert r.status_code == 404, (
            f"a missing reference_id must be 404; "
            f"got {r.status_code}: {r.text}")
        assert r.json() == stage1.json(), (
            f"the 404 body must match the stage-1 uniform shape "
            f"{stage1.json()!r}; got {r.json()!r}")
        assert "calls" not in sink, (
            "a rejected reference must never reach the transport")

    def test_b_foreign_reference_is_404_uniform_shape_no_generation(
            self, tmp_path):
        """(B) reference_id owned by ANOTHER user -> the same 404 (no
        existence leak) + no transport call."""
        settings, engine, factory, app, client = _build(tmp_path)
        _register_and_login(client, "m234own")
        foreign_ref = _upload_reference(client)
        sink = {}
        _mount(app, sink)

        other = TestClient(app)
        _register_and_login(other, "m234intruder")
        other_cid = _create_character(other)
        stage1 = other.get(f"/visual/references/{foreign_ref}/file")
        assert stage1.status_code == 404, stage1.text

        r = other.post(f"/visual/portraits/{other_cid}",
                       json={"reference_id": foreign_ref})
        assert r.status_code == 404, (
            f"a foreign reference_id must be 404; "
            f"got {r.status_code}: {r.text}")
        assert r.json() == stage1.json(), (
            f"the 404 body must match the stage-1 uniform shape "
            f"{stage1.json()!r}; got {r.json()!r}")
        assert "calls" not in sink, (
            "a foreign reference must never reach the transport")
        # the owner's row and file survive the foreign attempt
        r = client.get(f"/visual/references/{foreign_ref}/file")
        assert r.status_code == 200, r.text

    def test_c_reference_bytes_flow_to_the_transport(self, tmp_path):
        """(C) a real stage-1 upload consumed by generation: the captured
        reference_b64 equals base64 of the STORED file bytes (the stage-1
        serving endpoint is the storage oracle)."""
        settings, engine, factory, app, client = _build(tmp_path)
        _register_and_login(client, "m234flow")
        original = _png_bytes()
        ref_id = _upload_reference(client, original)
        sink = {}
        _mount(app, sink)
        cid = _create_character(client)

        r = client.post(f"/visual/portraits/{cid}",
                        json={"reference_id": ref_id})
        assert r.status_code == 201, r.text

        stored = client.get(f"/visual/references/{ref_id}/file")
        assert stored.status_code == 200, stored.text
        assert stored.content == original, "stage-1 storage changed"
        expected = base64.b64encode(stored.content).decode()
        assert sink.get("reference_b64") == expected, (
            "the transport call must carry base64 of the stored reference "
            f"bytes; got {sink.get('reference_b64')!r}, expected {expected!r}")

    def test_d_non_int_reference_id_is_422(self, tmp_path):
        """(D) reference_id is int|None: a non-int value is a 422."""
        settings, engine, factory, app, client = _build(tmp_path)
        _register_and_login(client, "m234refint")
        sink = {}
        _mount(app, sink)
        cid = _create_character(client)

        r = client.post(f"/visual/portraits/{cid}",
                        json={"reference_id": "not-an-int"})
        assert r.status_code == 422, (
            f"reference_id must be int|None; got {r.status_code}: {r.text}")
        assert "calls" not in sink, (
            "a malformed reference_id must be rejected before generation")


class TestScenes:
    def test_a_scene_prompt_always_carries_the_storybook_suffix(
            self, tmp_path):
        """(A) POST /visual/scenes: the positive prompt ALWAYS contains the
        server-side storybook suffix (no user-facing style param)."""
        settings, engine, factory, app, client = _build(tmp_path)
        _register_and_login(client, "m234scene")
        sink = {}
        _mount(app, sink)
        cid = _create_character(client)
        loc = client.get(f"/characters/{cid}").json()["location_id"]

        r = client.post("/visual/scenes", json={"location_id": loc})
        assert r.status_code == 201, r.text
        prompt = _prompt_of(sink)
        assert STORYBOOK_PHRASE in prompt, (
            f"the scene prompt must carry the storybook suffix "
            f"{STORYBOOK_PHRASE!r}; got {prompt!r}")
        from app.visual.style_presets import STORYBOOK_SCENE

        assert STORYBOOK_SCENE.rstrip(", ") in prompt, (
            f"the scene prompt must carry the STORYBOOK_SCENE constant "
            f"({STORYBOOK_SCENE!r}); got {prompt!r}")

    def test_b_scene_missing_reference_is_404_uniform_shape(
            self, tmp_path):
        """(B) scenes accept reference_id with the same 404 semantics as
        portraits (uniform stage-1 shape, no transport call)."""
        settings, engine, factory, app, client = _build(tmp_path)
        _register_and_login(client, "m234sceneref")
        sink = {}
        _mount(app, sink)
        cid = _create_character(client)
        loc = client.get(f"/characters/{cid}").json()["location_id"]

        stage1 = client.get("/visual/references/999999/file")
        assert stage1.status_code == 404, stage1.text

        r = client.post("/visual/scenes", json={
            "location_id": loc, "reference_id": 999999})
        assert r.status_code == 404, (
            f"a missing reference_id must be 404; "
            f"got {r.status_code}: {r.text}")
        assert r.json() == stage1.json(), (
            f"the 404 body must match the stage-1 uniform shape "
            f"{stage1.json()!r}; got {r.json()!r}")
        assert "calls" not in sink, (
            "a rejected reference must never reach the transport")

    def test_c_scene_consumes_the_reference_bytes(self, tmp_path):
        """(C) a real stage-1 upload consumed by scene generation: the
        captured reference_b64 equals base64 of the stored file bytes."""
        settings, engine, factory, app, client = _build(tmp_path)
        _register_and_login(client, "m234sceneflow")
        original = _png_bytes()
        ref_id = _upload_reference(client, original)
        sink = {}
        _mount(app, sink)
        cid = _create_character(client)
        loc = client.get(f"/characters/{cid}").json()["location_id"]

        r = client.post("/visual/scenes", json={
            "location_id": loc, "reference_id": ref_id})
        assert r.status_code == 201, r.text
        expected = base64.b64encode(original).decode()
        assert sink.get("reference_b64") == expected, (
            "scene generation must carry base64 of the stored reference "
            f"bytes; got {sink.get('reference_b64')!r}, expected {expected!r}")


class TestAuthUnchanged:
    def test_a_unauthenticated_portrait_and_scene_are_401(self, tmp_path):
        """(A) auth rules unchanged: no token -> 401, even with the new
        fields in the body."""
        settings, engine, factory, app, client = _build(tmp_path)
        _register_and_login(client, "m234auth")
        ref_id = _upload_reference(client)
        anon = TestClient(app)

        r = anon.post("/visual/portraits/some_cid", json={
            "reference_id": ref_id, "style": "oil", "nude": True})
        assert r.status_code == 401, (
            f"unauthenticated portrait POST must be 401; got {r.status_code}")
        r = anon.post("/visual/scenes", json={
            "location_id": 1, "reference_id": ref_id})
        assert r.status_code == 401, (
            f"unauthenticated scene POST must be 401; got {r.status_code}")
