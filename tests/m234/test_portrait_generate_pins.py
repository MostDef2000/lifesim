"""020 P3 stage 2 — review holes T3/T4 + the nude-negative pin (portrait API).

Review holes pinned here (peer review of the stage-2 implementation):
- T3 canonical-reuse bypass: the §70 canonical fast-path in create_portrait
  must serve PLAIN repeat POSTs only — a customized request (style/nude/
  reference) renders DIFFERENT output than the stored canonical and must
  always generate. Untested: the interplay of promote-canonical -> styled
  POST -> plain repeat was never exercised through the transport sink.
- T4 vanished reference file: _reference_b64_or_404 must answer the SAME
  uniform stage-1 404 when the reference ROW exists but its FILE is gone
  from disk (the stage-1 orphan case, m233 test_m) — and the transport is
  never called. Existing pins only cover missing/foreign ROWS.
- Nude-negative (fix had no pin): with nude=True the composed negative sent
  to the transport keeps the canonical guardrail floor ONLY (the
  "bare midriff, exposed stomach, navel" composition phrase contradicts the
  NUDE_FRAMING positive and must be dropped); non-nude negatives stay
  byte-identical to compose_negative() (canonical + composition).

Offline only: tmp SQLite DB + FakeTransport (app.state.flux_transport_factory
seam, the tests/m234/test_reference_consume_api.py capture pattern).

RED discipline: on base dd7d11d (stage-1) the portrait route takes no body
(T3: styled POST is a plain reuse, 200/no sink call; T4: the reference_id is
ignored -> 201 with the transport called; nude: no framing path at all).
"""
import os
import sys
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from app.config.config import load_config  # noqa: E402

SETTINGS = None
PW = "Correct-Horse-9!"
OIL_PREFIX = "oil painting, "

# Hand-copied literals from backend/app/visual/descriptor.py — pinned as
# bytes so a constant change forces a conscious test update (each test
# cross-checks its literal against the production constant).
_CANONICAL_NEGATIVE = (
    "child, teen, teenage, underage, minor, loli, lolicon, shota, kid, "
    "young girl, young boy, real person, real people, celebrity, politician, "
    "photograph of a real human"
)
_COMPOSITION_NEGATIVE = "bare midriff, exposed stomach, navel"
# Stable distinguishing substring of the canonical guardrail floor (the
# canonical part of the composed negative; survives when the composition
# phrase is dropped for nude renders).
_FLOOR_MARKER = "photograph of a real human"


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
            update={"db_path": str(tmp_path / "w234pins.db")}
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
    """API-test fake (app.state.flux_transport_factory seam): counts calls
    and captures the positive prompt and the composed negative."""

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


class TestCanonicalReuseBypass:
    def test_a_styled_post_generates_plain_repeat_reuses_canonical(
            self, tmp_path):
        """(T3) promote a plain portrait to canonical, then: a style="oil"
        POST must NOT reuse it (201, NEW asset, transport sink invoked with
        the oil-prefixed prompt), while a plain repeat POST takes the
        canonical reuse path (200 reused=True, transport NOT invoked)."""
        settings, engine, factory, app, client = _build(tmp_path)
        _register_and_login(client, "m234t3")
        sink = {}
        _mount(app, sink)
        cid = _create_character(client)

        # plain POST #1: no canonical yet -> generation
        r = client.post(f"/visual/portraits/{cid}")
        assert r.status_code == 201, r.text
        canonical_id = r.json()["asset"]["id"]
        assert sink.get("calls") == 1, sorted(sink)

        # promote to canonical: the §70 reuse source
        r = client.post(f"/visual/assets/{canonical_id}/canonical",
                        json={"canonical": True})
        assert r.status_code == 200, r.text

        # customized (style="oil") request: a preset render DIFFERS from the
        # stored canonical -> must always generate, never reuse.
        r = client.post(f"/visual/portraits/{cid}", json={"style": "oil"})
        assert r.status_code == 201, (
            f'style="oil" must generate a fresh asset even when a canonical '
            f'exists; got {r.status_code}: {r.text}')
        body = r.json()
        assert body["reused"] is False, body
        assert body["asset"]["id"] != canonical_id, (
            "the styled request must create a NEW asset, not return the "
            "canonical one")
        assert sink.get("calls") == 2, (
            f"the transport must be invoked for the styled request; "
            f"sink={sorted(sink)} calls={sink.get('calls')}")
        assert sink.get("prompt", "").startswith(OIL_PREFIX), (
            f"the generation that ran must be the OIL render; "
            f"got {sink.get('prompt', '')[:90]!r}")

        # plain repeat: the canonical reuse fast-path, no transport call.
        r = client.post(f"/visual/portraits/{cid}")
        assert r.status_code == 200, (
            f"a plain repeat POST must reuse the canonical portrait; "
            f"got {r.status_code}: {r.text}")
        body = r.json()
        assert body["reused"] is True, body
        assert body["asset"]["id"] == canonical_id, (
            f"the reused asset must be the canonical one {canonical_id}; "
            f"got {body['asset']['id']}")
        assert sink.get("calls") == 2, (
            f"the canonical reuse path must NOT call the transport; "
            f"calls={sink.get('calls')}")


class TestVanishedReferenceFile:
    def test_a_vanished_reference_file_is_uniform_404_no_transport(
            self, tmp_path):
        """(T4) stage-1 row exists, FILE deleted from disk -> portrait POST
        with that reference_id answers the SAME uniform 404 body as the
        stage-1 file endpoint (dynamic oracle, m233 test_m orphan case) and
        the transport is never called."""
        settings, engine, factory, app, client = _build(tmp_path)
        _register_and_login(client, "m234t4")
        ref_id = _upload_reference(client)
        sink = {}
        _mount(app, sink)
        cid = _create_character(client)

        # stage-1 storage fixture: the row survives, the file does not.
        orphan = Path(settings.visual.references_dir) / f"{ref_id}.png"
        assert orphan.exists(), (
            f"the stage-1 upload must store {ref_id}.png under "
            f"references_dir; got {sorted(Path(settings.visual.references_dir).iterdir())}")
        orphan.unlink()

        # dynamic oracle: the uniform stage-1 404 for a vanished file.
        stage1 = client.get(f"/visual/references/{ref_id}/file")
        assert stage1.status_code == 404, stage1.text

        r = client.post(f"/visual/portraits/{cid}",
                        json={"reference_id": ref_id})
        assert r.status_code == 404, (
            f"a reference whose file vanished must be 404; "
            f"got {r.status_code}: {r.text}")
        assert r.json() == stage1.json(), (
            f"the 404 body must match the stage-1 uniform shape "
            f"{stage1.json()!r}; got {r.json()!r}")
        assert "calls" not in sink, (
            "a vanished reference file must never reach the transport")


class TestNudeNegativeComposition:
    def test_a_nude_negative_drops_composition_keeps_guardrail_floor(
            self, tmp_path):
        """(nude-negative pin) nude=True -> the negative sent to the
        transport keeps the canonical guardrail floor and does NOT contain
        the composition phrase "bare midriff" (byte pin: the canonical
        negative alone)."""
        settings, engine, factory, app, client = _build(tmp_path)
        _register_and_login(client, "m234nudeneg")
        sink = {}
        _mount(app, sink)
        cid = _create_character(client)

        from app.visual.descriptor import _PROFILE_CANONICAL_NEGATIVE

        assert _CANONICAL_NEGATIVE == _PROFILE_CANONICAL_NEGATIVE, (
            "the test literal drifted from the production constant — "
            "update the pin consciously")

        r = client.post(f"/visual/portraits/{cid}", json={"nude": True})
        assert r.status_code == 201, r.text
        negative = sink.get("negative")
        assert negative is not None, sorted(sink)
        assert _FLOOR_MARKER in negative, (
            f"the nude negative must keep the canonical guardrail floor "
            f"(marker {_FLOOR_MARKER!r}); got {negative!r}")
        assert "bare midriff" not in negative, (
            f"the nude negative must NOT carry the composition phrase "
            f'"bare midriff" (it contradicts the NUDE_FRAMING positive); '
            f"got {negative!r}")
        assert negative == _CANONICAL_NEGATIVE, (
            f"the nude negative must be exactly the canonical guardrail "
            f"floor; got {negative!r}")

    def test_b_clothed_negative_keeps_the_composition_phrase(self, tmp_path):
        """(nude-negative pin, byte-behavior unchanged) nude=False -> the
        negative stays byte-identical to compose_negative(): canonical +
        "bare midriff, exposed stomach, navel"."""
        settings, engine, factory, app, client = _build(tmp_path)
        _register_and_login(client, "m234clothedneg")
        sink = {}
        _mount(app, sink)
        cid = _create_character(client)

        from app.visual.descriptor import (
            _COMPOSITION_NEGATIVE,
            _PROFILE_CANONICAL_NEGATIVE,
        )

        assert _CANONICAL_NEGATIVE == _PROFILE_CANONICAL_NEGATIVE, (
            "the test literal drifted from the production constant — "
            "update the pin consciously")
        assert _COMPOSITION_NEGATIVE == "bare midriff, exposed stomach, navel"

        r = client.post(f"/visual/portraits/{cid}", json={"nude": False})
        assert r.status_code == 201, r.text
        negative = sink.get("negative")
        assert negative is not None, sorted(sink)
        assert "bare midriff" in negative, (
            f"the clothed negative must keep the composition phrase; "
            f"got {negative!r}")
        assert negative == f"{_CANONICAL_NEGATIVE}, {_COMPOSITION_NEGATIVE}", (
            f"the clothed negative must stay byte-identical to "
            f"compose_negative(); got {negative!r}")
