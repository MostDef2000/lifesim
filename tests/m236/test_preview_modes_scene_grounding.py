"""W2 bounded child PR — preview modes + reference_id + scene prompt grounding.

Contract under test (backend/app/api/visual_routes.py, descriptor.py,
style_presets.py):

- POST /visual/preview gains OPTIONAL fields: mode (str|None = None) and
  reference_id (int|None = None). Omitted mode (or mode="portrait") is
  BYTE-IDENTICAL legacy behavior: same prompt bytes, same seed, same
  response key set {prompt, seed, image_base64}, and the transport call
  carries nothing new (the capture transport sees identical kwargs).
- mode="full_body_clothed" -> the outfit chain renders the CASUAL garments
  (a nude outfit_mode is deliberately overridden — the label says clothed)
  and the prompt ends with FULL_BODY_FRAMING.
- mode="full_body_outfit" -> the draft's outfit_mode rides unchanged
  (auto/casual/formal as chosen) + the same full-body framing suffix.
- mode="base_body" -> a neutral proportion-study framing REPLACES the whole
  clothing chain: no "wearing" fragment, no nude fragment, no outfit chain
  — regardless of outfit_mode (18+ gated by the endpoint's existing
  18-99 age rule; underage -> 422 before any transport call).
- unknown mode -> 422, no transport call.
- reference_id on preview: the SAME owner-checked stage-1 resolution the
  portrait/scene routes use (missing or foreign -> 404, no transport); an
  owned reference rides the transport as reference_b64 = base64(file
  bytes) and composes with any mode. The preview stays WRITE-FREE: no
  VisualAsset row, no canonical promotion, no file in the storage dir, no
  WorldEvent — reference resolution is a READ-ONLY session use.
- failure semantics: a transport ValueError -> 502 on the preview AND on
  the portrait route (the portrait mapping was never pinned before) with
  ZERO writes (asset/event/reference counts and storage file count
  unchanged).
- scene prompt grounding (descriptor.py): when the requesting player's
  committed state has worn WorldObjects, the scene prompt carries
  "the protagonist wears: X, Y"; when an NPC has worn items their entry
  carries "worn" and the prompt carries "NAME wears: X, Y" (id order).
  Descriptors without worn data build BYTE-IDENTICAL prompts (m119/m154
  pins stay untouched).

RED discipline: on base d563e7ca the preview route ignores unknown body
fields (pydantic extra=ignore), so the NEW-BEHAVIOR tests below fail
against the legacy 200 (wrong prompt bytes / missing reference_b64 / no
404) — mechanically confirmed: 9 failed, 7 passed on base (stash of the
tracked diff, m236 present, 2026-10-26). The scene-grounding unit tests
fail because build_prompt/build_scene_descriptor do not read the worn
fragments yet. Two tests in TestFailureSemantics pin PRE-EXISTING route
behavior (explicit-portrait byte identity, portrait 502 mapping) and pass
on base — they are labeled as such in their docstrings. The explicit
invariant guards (mode-omitted legacy identity, underage 422, no-write
proofs) pass on base BY DESIGN — they pin the invariants this change
must not break; each is marked as such in its docstring.

Offline only: the API runs against a tmp SQLite DB + capture/stub
transports (no network, no ComfyUI).
"""
import base64
import os
import random
import sys
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from app.api import visual_routes  # noqa: E402
from app.api.app import create_app  # noqa: E402
from app.characters.generator import generate_population  # noqa: E402
from app.config.config import load_config  # noqa: E402
from app.db.models import (  # noqa: E402
    Character,
    VisualAsset,
    VisualReference,
    WorldEvent,
    WorldObject,
    bootstrap,
    create_engine_factory,
)
from app.social.clothing import toggle_wear  # noqa: E402
from app.visual.descriptor import (  # noqa: E402
    build_prompt,
    build_scene_descriptor,
)
from app.visual.transports import StubFluxTransport  # noqa: E402
from app.world.seed_world import seed_world  # noqa: E402

PW = "Correct-Horse-9!"
SETTINGS = None


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
            update={"db_path": str(tmp_path / "w236.db")}
        ),
        "visual": visual,
    })


class CaptureTransport:
    """Records every generate() call (kwargs included), delegates to the
    stub so the response stays a real PNG."""

    def __init__(self):
        self.calls = []

    def generate(self, prompt, seed, size, negative=None, reference_b64=None):
        self.calls.append({
            "prompt": prompt, "seed": seed, "size": size,
            "negative": negative, "reference_b64": reference_b64,
        })
        return StubFluxTransport().generate(prompt, seed, size)


class BoomTransport:
    def generate(self, prompt, seed, size):
        raise ValueError("flux exploded")


def _env(tmp_path, *, transport=None):
    """Seeded world + app + transport factory (the m216 pattern)."""
    settings = _make_settings(tmp_path)
    engine = create_engine_factory(settings)
    with sessionmaker(bind=engine)() as session:
        bootstrap(engine, settings, seed=42)
        seed_world(session, settings, settings.world.world_id)
        generate_population(session, settings, random.Random(42),
                            settings.world.world_id, 20)
        session.commit()
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app(settings, factory)
    app.state.flux_transport_factory = (lambda: transport()) if transport \
        else (lambda: StubFluxTransport())
    return settings, app, factory, engine


def _login(app, username):
    client = TestClient(app)
    r = client.post("/auth/register", json={
        "username": username, "email": f"{username}@x.com",
        "password": PW, "age_confirmed": True})
    assert r.status_code == 201, r.text
    r = client.post("/auth/login", json={"username": username, "password": PW})
    assert r.status_code == 200, r.text
    return client


def _png_bytes():
    buf = BytesIO()
    Image.new("RGB", (8, 8), (17, 90, 140)).save(buf, format="PNG")
    return buf.getvalue()


def _upload_reference(client, filename="w236.png"):
    r = client.post(
        "/visual/references",
        files={"file": (filename, _png_bytes(), "image/png")},
        data={"label": "w236"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _write_counts(settings, engine):
    """Per-counter write snapshot (the m216 no-write pattern) + ref count."""
    with sessionmaker(bind=engine)() as session:
        counts = {
            "visual_assets": session.query(VisualAsset).count(),
            "world_events": session.query(WorldEvent).count(),
            "visual_references": session.query(VisualReference).count(),
            "canonical_assets": session.query(VisualAsset)
                .filter_by(canonical=True).count(),
        }
    storage = Path(settings.visual.storage_dir)
    counts["storage_files"] = (
        sum(1 for p in storage.rglob("*") if p.is_file())
        if storage.exists() else 0)
    return counts


PREVIEW_BODY = {
    "sex": "F",
    "age": 27,
    "appearance_prompt": "выразительные глаза",
    "appearance": {"face": "мягкие черты", "hair_color": "рыжий"},
}


# ---------- B: preview modes ----------

class TestPreviewModes:
    def test_mode_omitted_legacy_bytes_and_transport_call(self, tmp_path):
        """Invariant guard (green on base): the mode-omitted payload keeps
        the m216 legacy contract — exact key set, deterministic seed, stub
        PNG, and a transport call with NO mode-related payload (the mode
        never leaks toward the transport)."""
        settings, app, _, _ = _env(tmp_path)
        # hold one shared capture instance
        shared = CaptureTransport()
        app.state.flux_transport_factory = lambda: shared
        client = _login(app, "w236legacy")
        r = client.post("/visual/preview", json=PREVIEW_BODY)
        assert r.status_code == 200, r.text
        data = r.json()
        assert set(data.keys()) == {"prompt", "seed", "image_base64"}
        assert data["seed"] == visual_routes._deterministic_seed(
            "preview", "F", 27, data["prompt"])
        expected_png = StubFluxTransport().generate(
            data["prompt"], data["seed"], settings.visual.image_size)
        assert base64.b64decode(data["image_base64"]) == expected_png
        assert len(shared.calls) == 1
        call = shared.calls[0]
        assert call["prompt"] == data["prompt"]
        # the legacy call shape: nothing mode/reference-shaped rides along
        assert call["reference_b64"] is None

    def test_mode_portrait_explicit_matches_omitted_bytes(self, tmp_path):
        """Invariant guard (green on base): mode="portrait" is an explicit
        legacy alias — byte-identical prompt AND seed to the omitted form."""
        _, app, _, _ = _env(tmp_path)
        client = _login(app, "w236portraitalias")
        omitted = client.post("/visual/preview", json=PREVIEW_BODY).json()
        explicit = client.post(
            "/visual/preview", json={**PREVIEW_BODY, "mode": "portrait"}).json()
        assert (omitted["prompt"], omitted["seed"]) == \
            (explicit["prompt"], explicit["seed"])

    def test_full_body_clothed_forces_casual_and_framing(self, tmp_path):
        """New behavior (RED on base): full_body_clothed renders the casual
        garments even when outfit_mode=nude was sent, and appends the
        full-body framing after the outfit chain."""
        from app.visual.style_presets import FULL_BODY_FRAMING

        _, app, _, _ = _env(tmp_path)
        client = _login(app, "w236fbc")
        r = client.post("/visual/preview", json={
            **PREVIEW_BODY, "outfit_mode": "nude",
            "mode": "full_body_clothed"})
        assert r.status_code == 200, r.text
        prompt = r.json()["prompt"]
        assert prompt.endswith(f", {FULL_BODY_FRAMING}")
        assert "wearing casual clothes: a plain t-shirt and jeans" in prompt
        assert "fully clothed" in prompt
        # the safety override: no nude fragment leaks into a "clothed" mode
        assert ", nude" not in prompt
        assert "completely unclothed" not in prompt

    def test_full_body_outfit_keeps_draft_outfit_mode(self, tmp_path):
        """New behavior (RED on base): full_body_outfit uses the outfit
        chosen in the creator's Одежда control (formal here) + framing."""
        from app.visual.style_presets import FULL_BODY_FRAMING

        _, app, _, _ = _env(tmp_path)
        client = _login(app, "w236fbo")
        r = client.post("/visual/preview", json={
            **PREVIEW_BODY, "outfit_mode": "formal",
            "mode": "full_body_outfit"})
        assert r.status_code == 200, r.text
        prompt = r.json()["prompt"]
        assert prompt.endswith(f", {FULL_BODY_FRAMING}")
        assert ", wearing formal attire" in prompt
        # auto (omitted outfit_mode) also composes: pool head + framing
        r2 = client.post("/visual/preview", json={
            **PREVIEW_BODY, "mode": "full_body_outfit"})
        assert r2.status_code == 200, r2.text
        assert "wearing a fitted button-front blouse and jeans" in r2.json()["prompt"]
        assert r2.json()["prompt"].endswith(f", {FULL_BODY_FRAMING}")

    def test_base_body_replaces_clothing_chain_neutrally(self, tmp_path):
        """New behavior (RED on base): base_body swaps the whole clothing
        chain for the neutral proportion-study framing — no wearing
        fragment, no nude fragment, regardless of outfit_mode."""
        from app.visual.style_presets import BASE_BODY_FRAMING

        _, app, _, _ = _env(tmp_path)
        client = _login(app, "w236basebody")
        r = client.post("/visual/preview", json={
            **PREVIEW_BODY, "outfit_mode": "nude", "mode": "base_body"})
        assert r.status_code == 200, r.text
        prompt = r.json()["prompt"]
        assert BASE_BODY_FRAMING in prompt
        # neutral: no clothing chain, no nudity wording at all
        assert "wearing" not in prompt
        assert "nude" not in prompt
        assert "unclothed" not in prompt
        assert ", fully clothed" not in prompt

    def test_base_body_underage_422_no_transport(self, tmp_path):
        """Invariant guard (green on base): the endpoint's 18-99 age gate
        IS the base_body 18+ gate — a 16-year-old request 422s before any
        transport call, in any mode."""
        calls = []

        class Counting:
            def generate(self, prompt, seed, size):
                calls.append(1)
                return StubFluxTransport().generate(prompt, seed, size)

        _, app, _, _ = _env(tmp_path, transport=lambda: Counting())
        client = _login(app, "w236underage")
        r = client.post("/visual/preview", json={
            **PREVIEW_BODY, "age": 16, "mode": "base_body"})
        assert r.status_code == 422, r.text
        assert not calls, "transport must not run for an underage request"

    def test_unknown_mode_422_no_transport(self, tmp_path):
        """New behavior (RED on base): an unknown mode is rejected before
        generation (today it is silently ignored -> 200 legacy)."""
        calls = []

        class Counting:
            def generate(self, prompt, seed, size):
                calls.append(1)
                return StubFluxTransport().generate(prompt, seed, size)

        _, app, _, _ = _env(tmp_path, transport=lambda: Counting())
        client = _login(app, "w236badmode")
        r = client.post("/visual/preview", json={
            **PREVIEW_BODY, "mode": "hologram"})
        assert r.status_code == 422, r.text
        assert not calls


# ---------- B: preview reference_id ----------

class TestPreviewReference:
    def test_owned_reference_rides_the_transport(self, tmp_path):
        """New behavior (RED on base): an owned stage-1 reference resolves
        to reference_b64 = base64(stored bytes) on the transport call and
        composes with a body mode."""
        _, app, _, _ = _env(tmp_path)
        shared = CaptureTransport()
        app.state.flux_transport_factory = lambda: shared
        client = _login(app, "w236refok")
        ref_id = _upload_reference(client)
        r = client.post("/visual/preview", json={
            **PREVIEW_BODY, "reference_id": ref_id,
            "mode": "full_body_clothed"})
        assert r.status_code == 200, r.text
        assert len(shared.calls) == 1
        assert shared.calls[0]["reference_b64"] is not None
        assert base64.b64decode(shared.calls[0]["reference_b64"]) == _png_bytes()

    def test_foreign_or_missing_reference_404_no_transport(self, tmp_path):
        """New behavior (RED on base): foreign/missing reference_id ->
        the same 404 the portrait route answers, no transport call."""
        _, app, _, _ = _env(tmp_path)
        shared = CaptureTransport()
        app.state.flux_transport_factory = lambda: shared
        owner = _login(app, "w236refowner")
        ref_id = _upload_reference(owner)
        other = _login(app, "w236refother")
        for bad in (ref_id, 999999):
            r = other.post("/visual/preview", json={
                **PREVIEW_BODY, "reference_id": bad})
            assert r.status_code == 404, (bad, r.text)
        assert not shared.calls


# ---------- D: failure semantics + write-free invariant ----------

class TestFailureSemantics:
    def test_preview_transport_value_error_502_zero_writes(self, tmp_path):
        """Invariant guard (green on base): a transport ValueError maps to
        502 and writes NOTHING (no asset, no event, no reference, no file)."""
        settings, app, _, engine = _env(tmp_path, transport=lambda: BoomTransport())
        client = _login(app, "w236boompreview")
        before = _write_counts(settings, engine)
        r = client.post("/visual/preview", json={
            **PREVIEW_BODY, "mode": "full_body_clothed"})
        assert r.status_code == 502, r.text
        assert "flux exploded" in r.json()["detail"]
        assert _write_counts(settings, engine) == before

    def test_portrait_transport_value_error_502_zero_writes(self, tmp_path):
        """New pin (green on base): the PORTRAIT route's ValueError -> 502
        mapping was never pinned; pin it with the zero-write proof."""
        settings, app, _, engine = _env(tmp_path, transport=lambda: BoomTransport())
        client = _login(app, "w236boomportrait")
        # create the player character so the portrait route has an owner
        r = client.post("/characters", json={
            "name": "Бум Портретов", "sex": "F", "age": 30})
        assert r.status_code == 201, r.text
        cid = r.json()["id"]
        before = _write_counts(settings, engine)
        r = client.post(f"/visual/portraits/{cid}")
        assert r.status_code == 502, r.text
        assert "flux exploded" in r.json()["detail"]
        assert _write_counts(settings, engine) == before

    def test_preview_never_inserts_assets_or_promotes_canonical(self, tmp_path):
        """Invariant guard (green on base): successful previews in EVERY
        mode write nothing — no VisualAsset row, no canonical promotion,
        no storage file, no WorldEvent."""
        settings, app, _, engine = _env(tmp_path)
        client = _login(app, "w236nowrite")
        before = _write_counts(settings, engine)
        for mode in ("portrait", "full_body_clothed",
                     "full_body_outfit", "base_body"):
            r = client.post("/visual/preview", json={
                **PREVIEW_BODY, "mode": mode})
            assert r.status_code == 200, (mode, r.text)
        after = _write_counts(settings, engine)
        assert after == before
        assert after["visual_assets"] == 0
        assert after["canonical_assets"] == 0
        assert after["storage_files"] == 0


# ---------- A: scene prompt grounding ----------

class TestSceneGrounding:
    def test_scene_prompt_player_worn_fragment(self, tmp_path):
        """New behavior (RED on base): the scene prompt carries the
        protagonist's worn outfit from committed state."""
        _, app, _, engine = _env(tmp_path)
        client = _login(app, "w236sceneworn")
        r = client.post("/characters", json={
            "name": "Носит Куртку", "sex": "F", "age": 29})
        assert r.status_code == 201, r.text
        cid = r.json()["id"]
        with sessionmaker(bind=engine)() as session:
            char = session.get(Character, cid)
            session.add(WorldObject(
                world_id=char.world_id, location_id=char.location_id,
                owner_character_id=cid, object_type="jacket", quantity=1,
                condition=100, object_metadata="{}"))
            session.commit()
            jacket_id = (
                session.query(WorldObject)
                .filter_by(owner_character_id=cid, object_type="jacket")
                .one().id)
            toggle_wear(session, char.world_id, cid, jacket_id)
            session.commit()
            descriptor = build_scene_descriptor(
                session, char.world_id, char.location_id,
                player_character_id=cid)
        prompt = build_prompt(descriptor)
        # the player fragment joins the RAW player-part object names
        # (pre-A4 pinned "worn" shape — no slot suffix)
        assert "the protagonist wears: jacket" in prompt

    def test_scene_descriptor_npc_worn_entry_and_prompt(self, tmp_path):
        """New behavior (RED on base): an NPC with worn items gets a
        "worn" entry (id-ordered) and the prompt carries "NAME wears: ...";
        the player's own entry never duplicates the protagonist fragment."""
        _, app, _, engine = _env(tmp_path)
        client = _login(app, "w236scenenvpc")
        r = client.post("/characters", json={
            "name": "Игрок Сцен", "sex": "M", "age": 33})
        assert r.status_code == 201, r.text
        player_id = r.json()["id"]
        with sessionmaker(bind=engine)() as session:
            player = session.get(Character, player_id)
            world_id = player.world_id
            npc = (
                session.query(Character)
                .filter(Character.world_id == world_id,
                        Character.user_id.is_(None),
                        Character.alive.is_(True))
                .order_by(Character.id).first()
            )
            assert npc is not None
            # scene grounding is location-scoped: stage the NPC into the
            # player's scene so both land in one descriptor
            npc.location_id = player.location_id
            session.add(WorldObject(
                world_id=world_id, location_id=npc.location_id,
                owner_character_id=npc.id, object_type="boots", quantity=1,
                condition=100, object_metadata="{}"))
            session.commit()
            boots_id = (
                session.query(WorldObject)
                .filter_by(owner_character_id=npc.id, object_type="boots")
                .one().id)
            toggle_wear(session, world_id, npc.id, boots_id)
            session.commit()
            descriptor = build_scene_descriptor(
                session, world_id, npc.location_id,
                player_character_id=player_id)
        npc_entry = next(c for c in descriptor["characters"] if c["id"] == npc.id)
        assert npc_entry["worn"] == ["boots on feet"]
        player_entry = next(
            c for c in descriptor["characters"] if c["id"] == player_id)
        assert "worn" not in player_entry
        prompt = build_prompt(descriptor)
        assert f"{npc_entry['name']} wears: boots on feet" in prompt
        # player wears nothing -> no protagonist fragment, single mention
        assert "the protagonist wears" not in prompt
        assert prompt.count("boots on feet") == 1

    def test_scene_without_worn_prompt_bytes_unchanged(self, tmp_path):
        """Invariant guard (green on base): a scene where nobody wears
        anything builds the exact pre-W2 prompt (no fragment, no key)."""
        _, app, _, engine = _env(tmp_path)
        client = _login(app, "w236scenplain")
        r = client.post("/characters", json={
            "name": "Босый Игрок", "sex": "F", "age": 25})
        assert r.status_code == 201, r.text
        cid = r.json()["id"]
        with sessionmaker(bind=engine)() as session:
            char = session.get(Character, cid)
            descriptor = build_scene_descriptor(
                session, char.world_id, char.location_id,
                player_character_id=cid)
        prompt = build_prompt(descriptor)
        assert "wears:" not in prompt
        assert "worn" not in str(descriptor["characters"])
        # player part still serializes an (empty) worn list, pre-A4 shape
        assert descriptor["player"]["worn"] == []

    def test_unit_scene_prompt_worn_fragments_deterministic(self):
        """New behavior (RED on base): pure build_prompt contract — player
        fragment first, NPC fragments in entry order, hand-made descriptor
        without worn keys stays byte-identical."""
        base_descriptor = {
            "location": {"id": "l1", "name": "Пирс", "type": "pier"},
            "characters": [
                {"id": "c1", "name": "Anna", "sex": "F", "age": 30},
                {"id": "c2", "name": "Boris", "sex": "M", "age": 41},
            ],
            "objects": [{"id": "o1", "type": "crate"}],
            "weather": "clear",
            "time": {"day": 0, "hour": 12, "time_of_day": "day"},
            "camera": "wide",
            "event": None,
            "player": None,
            "references": [],
        }
        legacy_prompt = build_prompt(base_descriptor)
        assert "wears:" not in legacy_prompt

        with_player = {
            **base_descriptor,
            "player": {"id": "p1", "name": "Hero", "sex": "F", "age": 30,
                       "looks": "", "worn": ["jacket on upper_body",
                                             "boots on feet"]},
        }
        prompt = build_prompt(with_player)
        assert "the protagonist wears: jacket on upper_body, boots on feet" in prompt
        # deterministic
        assert build_prompt(with_player) == prompt

        with_npc = {
            **base_descriptor,
            "characters": [
                {"id": "c1", "name": "Anna", "sex": "F", "age": 30,
                 "worn": ["scarf on neck"]},
                {"id": "c2", "name": "Boris", "sex": "M", "age": 41},
            ],
        }
        prompt_npc = build_prompt(with_npc)
        assert "Anna wears: scarf on neck" in prompt_npc
        assert "Boris wears" not in prompt_npc
