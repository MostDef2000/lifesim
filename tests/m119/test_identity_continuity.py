"""#119 wave 4 — identity continuity: regenerate chains the canonical face.

Contract under test (backend/app/api/visual_routes.py, POST
/visual/portraits/{cid}):

- PortraitIn gains regenerate: bool = False (additive; absent/false =
  byte-identical legacy behavior);
- regenerate=True bypasses the §70 canonical-reuse fast-path (a regen
  always generates: 201, reused=false);
- regenerate=True with a canonical portrait -> the transport call carries
  reference_b64 = base64 of the canonical's STORED bytes (the face chains);
  the canonical is NOT demoted (it stays pinned through generation; the
  old row is unchanged);
- explicit reference_id wins over regenerate: the canonical bytes are not
  consumed;
- regenerate=True with NO canonical row -> the legacy reference-less
  generation (the transport receives no reference);
- regenerate=True + canonical row + unreadable file -> 404 "canonical
  portrait file missing", zero writes (no VisualAsset row added), the
  canonical stays canonical (owner intent 2026-11-01: the face must
  persist or the regen must not happen);
- regenerate=True + nude=True -> ALLOWED: the face chains AND the nude
  framing applies; the backend never auto-promotes the fresh asset
  (pinning is UI-side);
- the seed formula is unchanged: seed = _deterministic_seed(world_id,
  character_id, asset_type, existing_count) — a regen only rotates the
  existing-count input, nothing else;
- plain requests (no regenerate) keep the reuse fast-path: 200
  reused=true, no transport call.

Offline only: tmp SQLite DB + FakeTransport (app.state.flux_transport_factory
seam, the tests/m234/test_reference_consume_api.py capture pattern).

RED discipline: on base 54e03204a8d09b15bb53184f9648c7baaad26862 the body
field does not exist — Pydantic ignores unknown fields, so regenerate=True
degrades to a plain request (200 reused=true where 201 + chained reference
is required).
"""
import base64
import os
import sys
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from app.config.config import load_config  # noqa: E402

SETTINGS = None
PW = "Correct-Horse-9!"
NUDE_FRAMING_PHRASE = "artistic nude figure study"


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
            update={"db_path": str(tmp_path / "w119w4.db")}
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
    a SENTINEL so a missing kwarg shows as "the call never carried the
    reference" instead of a silent None."""

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


def _make_canonical(client):
    """Plain POST (generation) + explicit promote — the backend POST never
    auto-promotes, so the pin is set the way the UI does it."""
    cid = _create_character(client)
    r = client.post(f"/visual/portraits/{cid}")
    assert r.status_code == 201, r.text
    asset_id = r.json()["asset"]["id"]
    r = client.post(f"/visual/assets/{asset_id}/canonical",
                    json={"canonical": True})
    assert r.status_code == 200, r.text
    return cid, asset_id


def _stored_asset_bytes(client, asset_id) -> bytes:
    """Storage oracle: the serving endpoint reads the SAME stored bytes the
    generation chain must consume."""
    r = client.get(f"/visual/assets/{asset_id}/file")
    assert r.status_code == 200, r.text
    return r.content


def _portrait_row_count(factory, settings) -> int:
    from app.db.models import VisualAsset

    with factory() as session:
        return (
            session.query(VisualAsset)
            .filter_by(world_id=settings.world.world_id,
                       asset_type="portrait")
            .count()
        )


class TestRegenerateChainsCanonicalFace:
    def test_a_regenerate_consumes_canonical_and_keeps_it_pinned(
            self, tmp_path):
        """(A) regenerate=True + canonical -> the transport call carries
        base64 of the canonical's STORED bytes; 201 reused=false; the
        canonical is STILL pinned after the regen (no demotion anywhere in
        the path) and the old row is unchanged (a plain repeat still
        reuses it)."""
        settings, engine, factory, app, client = _build(tmp_path)
        _register_and_login(client, "m119w4chain")
        sink = {}
        _mount(app, sink)
        cid, canonical_id = _make_canonical(client)
        canonical_b64 = base64.b64encode(
            _stored_asset_bytes(client, canonical_id)).decode()
        assert canonical_b64, "sanity: the canonical has stored bytes"
        calls_after_seed = sink.get("calls", 0)

        r = client.post(f"/visual/portraits/{cid}",
                        json={"regenerate": True})
        assert r.status_code == 201, (
            f"regenerate=True must always generate; "
            f"got {r.status_code}: {r.text}")
        body = r.json()
        assert body["reused"] is False, body
        assert body["asset"]["id"] != canonical_id, body

        assert sink.get("reference_b64") == canonical_b64, (
            "the regen transport call must carry base64 of the canonical's "
            f"stored bytes; got {sink.get('reference_b64')!r}")
        assert sink.get("calls") == calls_after_seed + 1, sink

        # no demotion: the old canonical row is unchanged and still pinned.
        r = client.get(f"/visual/assets/{canonical_id}")
        assert r.status_code == 200, r.text
        assert r.json()["canonical"] is True, (
            "the regen must NOT demote the canonical portrait")
        # the §70 fast-path still serves the SAME canonical row afterwards.
        r = client.post(f"/visual/portraits/{cid}")
        assert r.status_code == 200, r.text
        assert r.json()["reused"] is True, r.json()
        assert r.json()["asset"]["id"] == canonical_id, r.json()
        assert sink.get("calls") == calls_after_seed + 1, (
            "the plain repeat must take the reuse fast-path, no transport")

    def test_b_explicit_reference_wins_over_regenerate(self, tmp_path):
        """(B) regenerate=True + explicit reference_id -> the explicit
        reference rides; the canonical bytes are NOT consumed (the captured
        reference_b64 equals the uploaded ref's bytes, not the canonical's)."""
        settings, engine, factory, app, client = _build(tmp_path)
        _register_and_login(client, "m119w4explicit")
        original = _png_bytes()
        ref_id = _upload_reference(client, original)
        sink = {}
        _mount(app, sink)
        cid, canonical_id = _make_canonical(client)
        canonical_b64 = base64.b64encode(
            _stored_asset_bytes(client, canonical_id)).decode()

        r = client.post(f"/visual/portraits/{cid}", json={
            "regenerate": True, "reference_id": ref_id})
        assert r.status_code == 201, r.text

        expected = base64.b64encode(original).decode()
        assert sink.get("reference_b64") == expected, (
            "the explicit reference_id must win: the transport call carries "
            f"base64 of the UPLOADED ref bytes; got "
            f"{sink.get('reference_b64')!r}, expected {expected!r}")
        assert sink.get("reference_b64") != canonical_b64, (
            "the canonical bytes must NOT be consumed when an explicit "
            "reference_id is present")

    def test_c_regenerate_without_canonical_is_legacy_reference_less(
            self, tmp_path):
        """(C) regenerate=True + NO canonical row -> the legacy
        reference-less generation: the transport receives NO reference
        (byte-identical legacy body — no reference_id key was sent) and the
        answer is 201."""
        settings, engine, factory, app, client = _build(tmp_path)
        _register_and_login(client, "m119w4nocanon")
        sink = {}
        _mount(app, sink)
        cid = _create_character(client)

        r = client.post(f"/visual/portraits/{cid}",
                        json={"regenerate": True})
        assert r.status_code == 201, r.text
        assert r.json()["reused"] is False, r.json()
        assert not sink.get("reference_b64"), (
            "without a canonical row the regen must ride the legacy "
            f"reference-less payload; transport got "
            f"reference_b64={sink.get('reference_b64')!r}")

    def test_d_unreadable_canonical_file_is_404_zero_writes(self, tmp_path):
        """(D) canonical ROW exists, its FILE is gone -> 404 "canonical
        portrait file missing", the transport is never called, ZERO writes
        (no VisualAsset row added) and the canonical stays canonical
        (owner intent: the face must persist or the regen must not
        happen)."""
        settings, engine, factory, app, client = _build(tmp_path)
        _register_and_login(client, "m119w4orphan")
        sink = {}
        _mount(app, sink)
        cid, canonical_id = _make_canonical(client)
        calls_after_seed = sink.get("calls", 0)
        storage_path = client.get(f"/visual/assets/{canonical_id}").json()[
            "storage_path"]
        orphan = Path(settings.visual.storage_dir) / storage_path
        assert orphan.exists(), (
            f"the canonical asset file must exist at {orphan}")
        orphan.unlink()
        rows_before = _portrait_row_count(factory, settings)

        r = client.post(f"/visual/portraits/{cid}",
                        json={"regenerate": True})
        assert r.status_code == 404, (
            f"a regen whose canonical file vanished must be 404; "
            f"got {r.status_code}: {r.text}")
        assert r.json() == {"detail": "canonical portrait file missing"}, (
            f"the 404 must name the missing canonical file explicitly; "
            f"got {r.json()!r}")
        assert sink.get("calls") == calls_after_seed, (
            "a failed canonical read must never reach the transport")
        assert _portrait_row_count(factory, settings) == rows_before, (
            "the failed regen must write NOTHING (no VisualAsset row)")
        r = client.get(f"/visual/assets/{canonical_id}")
        assert r.json()["canonical"] is True, (
            "the canonical must survive the failed regen intact")

    def test_e_regenerate_with_style_still_chains_the_canonical(
            self, tmp_path):
        """(E) regenerate=True + style="oil" + canonical -> the realistic
        UI path (style select + regen button coexist): the canonical STILL
        chains (the transport receives the canonical bytes as
        reference_b64) AND the oil preset prefix rides the prompt; 201."""
        settings, engine, factory, app, client = _build(tmp_path)
        _register_and_login(client, "m119w4oil")
        sink = {}
        _mount(app, sink)
        cid, canonical_id = _make_canonical(client)
        canonical_b64 = base64.b64encode(
            _stored_asset_bytes(client, canonical_id)).decode()

        r = client.post(f"/visual/portraits/{cid}",
                        json={"regenerate": True, "style": "oil"})
        assert r.status_code == 201, (
            f"regenerate + style must generate; "
            f"got {r.status_code}: {r.text}")
        body = r.json()
        assert body["reused"] is False, body
        assert body["asset"]["id"] != canonical_id, body

        assert sink.get("reference_b64") == canonical_b64, (
            "the styled regen must still chain the canonical face; got "
            f"reference_b64={sink.get('reference_b64')!r}")
        from app.visual.style_presets import OIL_PORTRAIT

        assert sink.get("prompt", "").startswith("oil painting, "), (
            f"the oil preset prefix must ride the regen prompt; "
            f"got {sink.get('prompt', '')[:90]!r}")
        assert sink.get("prompt", "").startswith(
            f"{OIL_PORTRAIT.rstrip(', ')}, "), (
            f"the prompt prefix must be the OIL_PORTRAIT constant "
            f"({OIL_PORTRAIT!r}); got {sink.get('prompt', '')[:90]!r}")


class TestLegacyBehaviorUntouched:
    def test_a_plain_request_keeps_the_reuse_fast_path(self, tmp_path):
        """(A) regenerate absent or false + canonical -> the §70 reuse
        fast-path answers 200 reused=true with the canonical itself and no
        transport call (byte-identical legacy behavior)."""
        settings, engine, factory, app, client = _build(tmp_path)
        _register_and_login(client, "m119w4legacy")
        sink = {}
        _mount(app, sink)
        cid, canonical_id = _make_canonical(client)
        calls_after_seed = sink.get("calls", 0)

        for payload in (None, {"regenerate": False}):
            if payload is None:
                r = client.post(f"/visual/portraits/{cid}")
            else:
                r = client.post(f"/visual/portraits/{cid}", json=payload)
            assert r.status_code == 200, (
                f"a plain request must reuse the canonical; "
                f"got {r.status_code}: {r.text} (payload={payload!r})")
            body = r.json()
            assert body["reused"] is True, body
            assert body["asset"]["id"] == canonical_id, body
        assert sink.get("calls") == calls_after_seed, (
            "the reuse fast-path must NOT call the transport")

    def test_b_seed_formula_unchanged(self, tmp_path):
        """(B) the seed formula is untouched: seed = _deterministic_seed(
        world_id, character_id, asset_type, existing_count) — the first
        generation and the regen differ ONLY by the existing-count input."""
        settings, engine, factory, app, client = _build(tmp_path)
        from app.api.visual_routes import _deterministic_seed

        _register_and_login(client, "m119w4seed")
        sink = {}
        _mount(app, sink)
        cid = _create_character(client)
        world_id = settings.world.world_id

        r = client.post(f"/visual/portraits/{cid}")
        assert r.status_code == 201, r.text
        seed_a = r.json()["asset"]["seed"]
        assert seed_a == _deterministic_seed(world_id, cid, "portrait", 0), (
            f"the first generation seed must stay "
            f"_deterministic_seed(world, cid, 'portrait', 0); "
            f"got {seed_a}")

        canonical_id = r.json()["asset"]["id"]
        r = client.post(f"/visual/assets/{canonical_id}/canonical",
                        json={"canonical": True})
        assert r.status_code == 200, r.text

        r = client.post(f"/visual/portraits/{cid}",
                        json={"regenerate": True})
        assert r.status_code == 201, r.text
        seed_b = r.json()["asset"]["seed"]
        assert seed_b == _deterministic_seed(world_id, cid, "portrait", 1), (
            f"the regen seed must stay _deterministic_seed(world, cid, "
            f"'portrait', existing_count) — only the count rotates; "
            f"got {seed_b}")
        assert seed_b != seed_a, "sanity: the count rotation moves the seed"


class TestNudeRegenerate:
    def test_a_nude_regenerate_chains_face_backend_never_autopins(
            self, tmp_path):
        """(A) nude + regenerate is ALLOWED: the canonical face chains (the
        nude policy governs PINNING, not referencing) AND the nude framing
        applies; the backend does not auto-promote the fresh asset — the
        previous canonical stays pinned (pinning is UI-side)."""
        settings, engine, factory, app, client = _build(tmp_path)
        _register_and_login(client, "m119w4nude")
        sink = {}
        _mount(app, sink)
        cid, canonical_id = _make_canonical(client)
        canonical_b64 = base64.b64encode(
            _stored_asset_bytes(client, canonical_id)).decode()

        r = client.post(f"/visual/portraits/{cid}",
                        json={"regenerate": True, "nude": True})
        assert r.status_code == 201, (
            f"nude + regenerate must generate; got {r.status_code}: {r.text}")
        body = r.json()
        assert body["reused"] is False, body
        new_id = body["asset"]["id"]
        assert new_id != canonical_id, body

        assert sink.get("reference_b64") == canonical_b64, (
            "the nude regen must chain the canonical face; got "
            f"reference_b64={sink.get('reference_b64')!r}")
        assert NUDE_FRAMING_PHRASE in sink.get("prompt", ""), (
            f"the nude framing must ride the regen prompt; "
            f"got {sink.get('prompt')!r}")

        # backend never auto-promotes: the fresh asset is NOT canonical,
        # the previous canonical stays pinned.
        r = client.get(f"/visual/assets/{new_id}")
        assert r.json()["canonical"] is False, (
            "the backend must not auto-promote the nude regen output")
        r = client.get(f"/visual/assets/{canonical_id}")
        assert r.json()["canonical"] is True, (
            "the previous canonical must stay pinned")
