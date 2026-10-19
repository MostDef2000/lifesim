"""020 P2 (wave 2) — canonical structured appearance: preview + persistence.

Contract of record: specs/020-character-creator/{spec,plan}.md — plan.md:28-30
(endpoint exact key set, preview writes NO sim state, resolver priorities,
m119 byte-identity regression) + plan.md:43-44 (RED-authenticity: the
no-write test must fail when a VisualAsset write is injected into the
handler — proved out-of-tree in /tmp scratch; the scratch run is quoted in
the delivery report, never a mutation of this repository).

Covers:
- preview 200: response key set EXACTLY {prompt, seed, image_base64};
  image_base64 == stub transport bytes; seed ==
  _deterministic_seed("preview", sex, age, prompt); FULL prompt-byte pin
  ("Portrait of the character, " + guardrail -> combined face -> body ->
  style -> casting -> clean-background tail -> auto outfit head);
  outfit_mode rides the prompt; determinism; transport ValueError -> 502.
- no-sim-write: VisualAsset / WorldEvent / Character / CharacterAppearance
  counts + storage-dir file count unchanged across a 200 preview (the
  handler has NO session dependency — pinned per-counter so ANY added
  write trips exactly one named assertion).
- limiter (default preview_rpm=5): 6th-in-window -> 429; a 429 does NOT
  consume quota (strong oracle: reject at t+30, then FIVE consecutive
  successes after the window expires at t+61 — a consuming bug 429s the
  5th); per-user isolation; gate 503 precedes the limiter and consumes
  nothing (full quota available after re-enable).
- validation: 422 matrix (sex/age/outfit_mode/prompt-length/appearance
  schema), unauthenticated 401, non-dict appearance rejected at the schema
  layer. Run with preview_rpm raised: validation happens AFTER the
  limiter, so 422 responses DO consume quota (pinned as-is, not weakened).
- resolver priority matrix (pure): appearance-only exact fragment bytes
  (the DELIBERATE English aspect-suffix design "{} face"/"{} hair color"
  etc.), template order pins, looks-only byte-identity, combined face =
  free-text FIRST + ", " + appearance fragments, body-key replacement,
  whitespace-only values dropped, non-str stored values ignored.
- /characters persist: cleaned values in the SAME transaction; absent /
  provided-but-empty -> no row; prompt-only -> row with appearance NULL;
  422 paths write nothing; account deletion cascades the appearance row
  (PRAGMA foreign_keys=ON regression — without the cascade line the
  delete 500s).
- byte-identity regression: a character WITHOUT an appearance row
  resolves to the hardcoded pre-020 profile (m119 style) through BOTH
  build_scene_descriptor and build_portrait_descriptor.
"""

import base64
import os
import random
import sys
import time as _real_time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from app.api import visual_routes  # noqa: E402
from app.api.app import create_app  # noqa: E402
from app.characters.generator import generate_population  # noqa: E402
from app.config.config import VisualConfig, load_config  # noqa: E402
from app.db.models import (  # noqa: E402
    Character,
    CharacterAppearance,
    CharacterTrait,
    Location,
    VisualAsset,
    WorldEvent,
    bootstrap,
    create_engine_factory,
)
from app.visual.descriptor import (  # noqa: E402
    APPEARANCE_FIELDS,
    build_portrait_descriptor,
    build_prompt,
    build_scene_descriptor,
    build_visual_profile,
)
from app.visual.transports import StubFluxTransport  # noqa: E402
from app.world.seed_world import seed_world  # noqa: E402

PW = "Correct-Horse-9!"
SETTINGS = None


def setup_module(module):
    global SETTINGS
    SETTINGS = load_config("config/default.yaml")


# ---------- preview request fixtures ----------

PREVIEW_BODY = {
    "sex": "F",
    "age": 27,
    "appearance_prompt": "выразительные глаза",
    "appearance": {"face": "мягкие черты", "hair_color": "рыжий"},
}

# The FULL deterministic prompt for PREVIEW_BODY — every section of the
# #119 pipeline in pinned order (guardrail -> face -> body -> style ->
# casting -> clean-background tail -> #205d auto pool head). The face
# section carries the 020 P2 combine rule: free text FIRST (never
# translated), then the appearance fragments — structured values render
# through the #224 build-time RU→EN value map (English aspect suffixes
# unchanged; unknown values would stay verbatim).
PREVIEW_PROMPT = (
    "Portrait of the character, 27-year-old fictional adult woman character, "
    "выразительные глаза, soft, gentle face, ginger red hair color, "
    "healthy adult woman build, modest, casual everyday styling, "
    "Chest-up portrait, entire head visible with clear margin above the hair, "
    "neutral warm-gray studio, soft frontal beauty lighting, "
    "clean background, detailed face, "
    "wearing a fitted blouse and jeans, fully clothed"
)

# m119 fixture input + the hardcoded pre-020 profile (byte-identity
# reference — mirrors tests/m119's frozen values; any drift in the
# no-appearance path trips the equality below, not just a re-derivation).
M119_FIXTURE = {
    "name": "Ivan Testov",  # interface completeness; the profile ignores it
    "age": 63,
    "sex": "M",
    "looks": "silver-haired with a trimmed beard and steady gray eyes",
    "traits": {"sociability": 80, "discipline": 40, "risk_tolerance": 10},
}

PRE020_PROFILE = {
    "face": "silver-haired with a trimmed beard and steady gray eyes",
    "body": "healthy adult man build",
    "style": "sociable, outgoing presence",
    "scenes": {
        "casting": (
            "Chest-up portrait, entire head visible with clear margin above "
            "the hair, neutral warm-gray studio, soft frontal beauty lighting"
        ),
        "scene": "natural environment lighting, candid composition",
    },
    "guardrail": {
        "phrase": "63-year-old fictional adult man character",
        "canonical_negative": (
            "child, teen, teenage, underage, minor, loli, lolicon, shota, kid, "
            "young girl, young boy, real person, real people, celebrity, "
            "politician, photograph of a real human"
        ),
    },
    "lora": {"available": False},
}


class _FakeClock:
    """`time` stand-in for the limiter tests: monotonic() is controllable,
    every other attribute delegates to the real module. Patched onto
    app.api.visual_routes (the module the limiter resolves `time` from)."""

    def __init__(self, start=1000.0):
        self.now = float(start)

    def monotonic(self):
        return self.now

    def __getattr__(self, name):
        return getattr(_real_time, name)


def _make_settings(tmp_path, *, visual_enabled=True, preview_rpm=None):
    updates = {
        "enabled": visual_enabled,
        "storage_dir": str(tmp_path / "assets"),
        "image_size": "8x8",
    }
    if preview_rpm is not None:
        updates["preview_rpm"] = preview_rpm
    visual = SETTINGS.visual.model_copy(update=updates)
    return SETTINGS.model_copy(update={
        "persistence": SETTINGS.persistence.model_copy(
            update={"db_path": str(tmp_path / "w216.db")}),
        "visual": visual,
    })


def _env(tmp_path, **kwargs):
    """Seeded world + app + stub transport (the m153 pattern). A fresh app
    per test == a fresh limiter bucket (the deque dict lives in the
    register_visual_routes closure)."""
    settings = _make_settings(tmp_path, **kwargs)
    engine = create_engine_factory(settings)
    with sessionmaker(bind=engine)() as session:
        bootstrap(engine, settings, seed=42)
        seed_world(session, settings, settings.world.world_id)
        generate_population(session, settings, random.Random(42),
                            settings.world.world_id, 20)
        session.commit()
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app(settings, factory)
    app.state.flux_transport_factory = lambda: StubFluxTransport()
    return settings, app, factory


def _login(app, username):
    client = TestClient(app)
    r = client.post("/auth/register", json={
        "username": username, "email": f"{username}@x.com",
        "password": PW, "age_confirmed": True})
    assert r.status_code == 201, r.text
    r = client.post("/auth/login", json={"username": username, "password": PW})
    assert r.status_code == 200, r.text
    return client


# ---------- preview 200 contract ----------

class TestPreviewContract:
    def test_preview_200_exact_key_set_and_full_prompt_bytes(self, tmp_path):
        settings, app, _ = _env(tmp_path)
        client = _login(app, "w216contract")
        r = client.post("/visual/preview", json=PREVIEW_BODY)
        assert r.status_code == 200, r.text
        data = r.json()
        # EXACTLY three keys — no asset id, no character id, nothing else.
        assert set(data.keys()) == {"prompt", "seed", "image_base64"}
        assert data["prompt"] == PREVIEW_PROMPT
        assert data["prompt"].startswith("Portrait of the character,")
        # appearance fragments ride the face section, English suffixes kept
        # (#224: RU→EN value mapping — deliberate pin evolution)
        assert "soft, gentle face" in data["prompt"]
        assert "ginger red hair color" in data["prompt"]
        assert data["seed"] == visual_routes._deterministic_seed(
            "preview", "F", 27, data["prompt"])
        expected_png = StubFluxTransport().generate(
            data["prompt"], data["seed"], settings.visual.image_size)
        assert base64.b64decode(data["image_base64"]) == expected_png

    def test_preview_is_deterministic(self, tmp_path):
        _, app, _ = _env(tmp_path)
        client = _login(app, "w216determinism")
        first = client.post("/visual/preview", json=PREVIEW_BODY).json()
        second = client.post("/visual/preview", json=PREVIEW_BODY).json()
        assert first == second

    @pytest.mark.parametrize("mode,tail", [
        ("casual", ", wearing casual clothes: a plain t-shirt and jeans, fully clothed"),
        ("formal", ", wearing formal attire"),
    ])
    def test_preview_outfit_mode_rides_the_prompt(self, tmp_path, mode, tail):
        _, app, _ = _env(tmp_path)
        client = _login(app, f"w216outfit_{mode}")
        r = client.post("/visual/preview", json={**PREVIEW_BODY, "outfit_mode": mode})
        assert r.status_code == 200, r.text
        assert r.json()["prompt"].endswith(tail)

    def test_preview_transport_value_error_maps_502(self, tmp_path):
        _, app, _ = _env(tmp_path)

        class BoomTransport:
            def generate(self, prompt, seed, size):
                raise ValueError("flux exploded")

        app.state.flux_transport_factory = lambda: BoomTransport()
        client = _login(app, "w216boom")
        r = client.post("/visual/preview", json=PREVIEW_BODY)
        assert r.status_code == 502, r.text
        assert "flux exploded" in r.json()["detail"]


# ---------- no-sim-write proof (RED-proven invariant) ----------

class TestPreviewNoSimWrite:
    def test_preview_writes_no_sim_state_and_no_files(self, tmp_path):
        settings, app, factory = _env(tmp_path)
        client = _login(app, "w216nowrite")

        def snapshot():
            with factory() as session:
                counts = {
                    "visual_assets": session.query(VisualAsset).count(),
                    "world_events": session.query(WorldEvent).count(),
                    "characters": session.query(Character).count(),
                    "character_appearances":
                        session.query(CharacterAppearance).count(),
                }
            storage = Path(settings.visual.storage_dir)
            counts["storage_files"] = (
                sum(1 for p in storage.rglob("*") if p.is_file())
                if storage.exists() else 0)
            return counts

        before = snapshot()
        r = client.post("/visual/preview", json=PREVIEW_BODY)
        assert r.status_code == 200, r.text
        after = snapshot()
        # per-counter assertions: ANY added write trips exactly one named
        # line below (the RED proof injects a VisualAsset write and this
        # test fails on "preview wrote to visual_assets").
        for key, value in before.items():
            assert after[key] == value, f"preview wrote to {key}"


# ---------- per-user sliding-window limiter ----------

class TestPreviewLimiter:
    def test_sixth_request_in_window_gets_429(self, tmp_path):
        # default preview_rpm == 5 (no override on this env)
        _, app, _ = _env(tmp_path)
        client = _login(app, "w216limiter")
        for i in range(5):
            r = client.post("/visual/preview", json=PREVIEW_BODY)
            assert r.status_code == 200, (i, r.text)
        r = client.post("/visual/preview", json=PREVIEW_BODY)
        assert r.status_code == 429, r.text
        assert "preview rate limit exceeded" in r.json()["detail"]

    def test_rejected_request_does_not_consume_quota(self, tmp_path, monkeypatch):
        clock = _FakeClock()
        monkeypatch.setattr(visual_routes, "time", clock)
        _, app, _ = _env(tmp_path)
        client = _login(app, "w216noc")
        for _ in range(5):
            assert client.post("/visual/preview", json=PREVIEW_BODY).status_code == 200
        clock.now += 30  # mid-window: quota spent, rejection must NOT append
        assert client.post("/visual/preview", json=PREVIEW_BODY).status_code == 429
        clock.now += 31  # t+61: the five t=0 entries age out of the window
        # STRONG oracle: exactly rpm consecutive successes after expiry.
        # Had the 429'd request consumed a slot (appended at t+30), that
        # entry would still be live at t+61 and the 5th request here 429s.
        for i in range(5):
            r = client.post("/visual/preview", json=PREVIEW_BODY)
            assert r.status_code == 200, (i, r.text)

    def test_quota_is_per_user(self, tmp_path):
        _, app, _ = _env(tmp_path)
        user_a = _login(app, "w216usera")
        user_b = _login(app, "w216userb")
        for _ in range(5):
            assert user_a.post("/visual/preview", json=PREVIEW_BODY).status_code == 200
        assert user_a.post("/visual/preview", json=PREVIEW_BODY).status_code == 429
        # A's exhausted bucket must not touch B (keyed by user id)
        assert user_b.post("/visual/preview", json=PREVIEW_BODY).status_code == 200


# ---------- auth + gate ordering ----------

class TestPreviewGateAndAuth:
    def test_disabled_gate_503_before_limiter_and_consumes_nothing(self, tmp_path):
        settings, app, _ = _env(tmp_path, visual_enabled=False)
        client = _login(app, "w216gate")
        for _ in range(6):  # > rpm: proves the limiter never ran
            r = client.post("/visual/preview", json=PREVIEW_BODY)
            assert r.status_code == 503, r.text
        # flip the SAME visual object the gate closure reads; the full
        # default quota (5) must still be available — a 503 that consumed
        # would 429 the 5th request below.
        settings.visual.enabled = True
        for i in range(5):
            r = client.post("/visual/preview", json=PREVIEW_BODY)
            assert r.status_code == 200, (i, r.text)
        assert client.post("/visual/preview", json=PREVIEW_BODY).status_code == 429

    def test_unauthenticated_401(self, tmp_path):
        _, app, _ = _env(tmp_path)
        anon = TestClient(app)
        r = anon.post("/visual/preview", json=PREVIEW_BODY)
        assert r.status_code == 401


# ---------- validation matrix ----------

class TestPreviewValidation:
    # preview_rpm raised: validation failures ride AFTER the limiter and
    # DO consume quota (frozen order); one env must serve the whole matrix.
    @pytest.fixture()
    def client(self, tmp_path):
        _, app, _ = _env(tmp_path, preview_rpm=60)
        return _login(app, "w216valid")

    @pytest.mark.parametrize("body,detail", [
        ({"sex": "X", "age": 27}, "sex must be M|F"),
        ({"sex": "M", "age": 17}, "age must be 18-99"),
        ({"sex": "M", "age": 100}, "age must be 18-99"),
        ({"sex": "F", "age": 27, "outfit_mode": "beachwear"},
         "outfit_mode must be auto|casual|formal|nude"),
        ({"sex": "F", "age": 27, "appearance_prompt": "а" * 501},
         "appearance_prompt ≤500"),
        ({"sex": "F", "age": 27, "appearance": {"third_eye": "x"}},
         "unknown appearance field"),
        ({"sex": "F", "age": 27, "appearance": {"face": 42}},
         "must be a string"),
        ({"sex": "F", "age": 27, "appearance": {"face": "а" * 121}},
         "must be ≤120 chars"),
        ({"sex": "F", "age": 27,
          "appearance": {**{k: "значение" for k in sorted(APPEARANCE_FIELDS)},
                         "third_eye": "x"}},
         "at most 12 fields"),
    ])
    def test_422_matrix(self, client, body, detail):
        r = client.post("/visual/preview", json=body)
        assert r.status_code == 422, r.text
        assert detail in r.json()["detail"]

    def test_non_dict_appearance_rejected_at_schema_layer(self, client):
        # PreviewIn.appearance is typed dict | None — a string never
        # reaches validate_appearance; FastAPI answers 422 (detail shape
        # is pydantic's error list, not the handler message).
        r = client.post("/visual/preview",
                        json={"sex": "F", "age": 27, "appearance": "не объект"})
        assert r.status_code == 422, r.text

    def test_preview_rpm_default_and_floor(self):
        # config contract: default 5, ge=1 (VisualConfig is importable and
        # validated directly — no app needed).
        assert SETTINGS.visual.preview_rpm == 5
        base = SETTINGS.visual.model_dump()
        with pytest.raises(ValidationError):
            VisualConfig(**{**base, "preview_rpm": 0})
        assert VisualConfig(**{**base, "preview_rpm": 1}).preview_rpm == 1


# ---------- resolver priority matrix (pure function) ----------

class TestResolverPriority:
    def test_appearance_only_face_fragments_exact_bytes(self):
        profile = build_visual_profile(
            {"age": 27, "sex": "F", "looks": None, "traits": {}},
            appearance={"face": "мягкие черты", "hair_color": "рыжий"})
        # the DELIBERATE English aspect-suffix design, pinned verbatim
        # (#224: RU→EN value mapping — deliberate pin evolution)
        assert profile["face"] == "soft, gentle face, ginger red hair color"
        # no body keys -> the derived phrase survives untouched
        assert profile["body"] == "healthy adult woman build"

    def test_face_template_order_and_features_verbatim(self):
        profile = build_visual_profile(
            {"age": 30, "sex": "M", "looks": None, "traits": {}},
            appearance={"face": "A", "eyes": "B", "nose": "C", "lips": "D",
                        "hair": "E", "hair_color": "F", "skin": "G",
                        "features": "H"})
        assert profile["face"] == (
            "A face, B eyes, C nose, D lips, E hair, F hair color, G skin, H")

    def test_body_keys_replace_derived_phrase(self):
        profile = build_visual_profile(
            {"age": 30, "sex": "M", "looks": None, "traits": {}},
            appearance={"height": "185", "weight": "80",
                        "build": "athletic", "chest": "broad"})
        assert profile["body"] == "height 185, weight 80, athletic build, broad chest"
        # face: no looks, no face keys -> the neutral fallback applies
        assert profile["face"] == "natural, well-groomed features"

    def test_looks_only_is_byte_identical_pre020(self):
        assert build_visual_profile(M119_FIXTURE) == PRE020_PROFILE
        assert build_visual_profile(M119_FIXTURE, appearance=None) == PRE020_PROFILE
        assert build_visual_profile(M119_FIXTURE, appearance={}) == PRE020_PROFILE

    def test_combined_face_free_text_first(self):
        profile = build_visual_profile(
            {**M119_FIXTURE, "looks": "выразительные глаза"},
            appearance={"face": "мягкие черты", "hair_color": "рыжий"})
        # exact combined bytes: looks FIRST (free text, never translated
        # — #224 maps structured values only), ", ", appearance fragments
        # (#224: RU→EN value mapping — deliberate pin evolution)
        assert profile["face"] == (
            "выразительные глаза, soft, gentle face, ginger red hair color")

    def test_whitespace_only_values_are_dropped(self):
        profile = build_visual_profile(
            M119_FIXTURE,
            appearance={"face": "   ", "build": "", "features": "\t"})
        assert profile == PRE020_PROFILE

    def test_non_str_stored_values_are_ignored(self):
        profile = build_visual_profile(
            M119_FIXTURE,
            appearance={"face": 42, "build": None, "eyes": ["x"], "skin": True})
        assert profile == PRE020_PROFILE


# ---------- POST /characters persistence ----------

class TestCharactersPersistence:
    def test_appearance_row_written_cleaned_in_same_transaction(self, tmp_path):
        _, app, factory = _env(tmp_path)
        client = _login(app, "w216persist")
        r = client.post("/characters", json={
            "name": "Канон Тест", "sex": "M", "age": 30,
            "looks": "свободный текст",
            "appearance": {"face": "  мягкие черты  ", "eyes": "серые",
                           "build": "   "},
            "appearance_prompt": "  свободный текст  "})
        assert r.status_code == 201, r.text
        cid = r.json()["id"]
        # one fresh session sees BOTH rows: create_player_character only
        # flushes; the single commit lands character + appearance together.
        with factory() as session:
            assert session.get(Character, cid) is not None
            row = session.get(CharacterAppearance, cid)
            assert row is not None
            # cleaned values: stripped, whitespace-only dropped
            assert row.appearance == {"face": "мягкие черты", "eyes": "серые"}
            assert row.appearance_prompt == "свободный текст"

    def test_absent_appearance_writes_no_row(self, tmp_path):
        _, app, factory = _env(tmp_path)
        client = _login(app, "w216absent")
        r = client.post("/characters", json={
            "name": "Пре Тест", "sex": "M", "age": 30})
        assert r.status_code == 201, r.text
        with factory() as session:
            assert session.get(Character, r.json()["id"]) is not None
            assert session.get(CharacterAppearance, r.json()["id"]) is None

    def test_provided_but_empty_writes_no_row(self, tmp_path):
        _, app, factory = _env(tmp_path)
        client = _login(app, "w216empty")
        r = client.post("/characters", json={
            "name": "Пусто Тест", "sex": "M", "age": 30,
            "appearance": {}, "appearance_prompt": ""})
        assert r.status_code == 201, r.text
        # whitespace-only fields clean to None == absent (second user:
        # one character per user is the pre-existing MVP limit)
        other = _login(app, "w216empty2")
        r2 = other.post("/characters", json={
            "name": "Второй Пусто", "sex": "F", "age": 31,
            "appearance": {"face": "   "}})
        assert r2.status_code == 201, r2.text
        with factory() as session:
            assert session.get(CharacterAppearance, r.json()["id"]) is None
            assert session.get(CharacterAppearance, r2.json()["id"]) is None
            assert session.query(CharacterAppearance).count() == 0

    def test_prompt_only_row_has_null_appearance(self, tmp_path):
        _, app, factory = _env(tmp_path)
        client = _login(app, "w216promptonly")
        r = client.post("/characters", json={
            "name": "Промпт Тест", "sex": "F", "age": 28,
            "appearance_prompt": "только свободный текст"})
        assert r.status_code == 201, r.text
        with factory() as session:
            row = session.get(CharacterAppearance, r.json()["id"])
            assert row is not None
            assert row.appearance is None
            assert row.appearance_prompt == "только свободный текст"

    @pytest.mark.parametrize("appearance,detail", [
        ({"third_eye": "x"}, "unknown appearance field"),
        ({"face": 42}, "must be a string"),
        ({"face": "а" * 121}, "must be ≤120 chars"),
        ({**{k: "значение" for k in sorted(APPEARANCE_FIELDS)},
          "third_eye": "x"}, "at most 12 fields"),
    ])
    def test_422_appearance_paths_write_nothing(self, tmp_path, appearance, detail):
        _, app, factory = _env(tmp_path)
        client = _login(app, "w216bad")
        with factory() as session:
            chars_before = session.query(Character).count()
        r = client.post("/characters", json={
            "name": "Бэд Тест", "sex": "M", "age": 30, "appearance": appearance})
        assert r.status_code == 422, r.text
        assert detail in r.json()["detail"]
        with factory() as session:
            # validation runs BEFORE create_player_character: nothing landed
            assert session.query(Character).count() == chars_before
            assert session.query(CharacterAppearance).count() == 0

    def test_non_dict_appearance_rejected_at_schema_layer(self, tmp_path):
        _, app, factory = _env(tmp_path)
        client = _login(app, "w216nondict")
        r = client.post("/characters", json={
            "name": "Строка Тест", "sex": "M", "age": 30,
            "appearance": "не объект"})
        assert r.status_code == 422, r.text
        with factory() as session:
            assert session.query(CharacterAppearance).count() == 0

    def test_appearance_prompt_over_500_422(self, tmp_path):
        _, app, factory = _env(tmp_path)
        client = _login(app, "w216longprompt")
        with factory() as session:
            chars_before = session.query(Character).count()
        r = client.post("/characters", json={
            "name": "Длинный Тест", "sex": "M", "age": 30,
            "appearance_prompt": "а" * 501})
        assert r.status_code == 422, r.text
        assert "appearance_prompt ≤500" in r.json()["detail"]
        with factory() as session:
            assert session.query(Character).count() == chars_before

    def test_account_deletion_cascades_appearance_row(self, tmp_path):
        _, app, factory = _env(tmp_path)
        client = _login(app, "w216del")
        r = client.post("/characters", json={
            "name": "Каскад Тест", "sex": "F", "age": 28,
            "appearance": {"face": "мягкие черты"},
            "appearance_prompt": "текст"})
        assert r.status_code == 201, r.text
        cid = r.json()["id"]
        with factory() as session:
            assert session.get(CharacterAppearance, cid) is not None
        # PRAGMA foreign_keys=ON regression: the cascade MUST delete the
        # appearance row or the character delete below IntegrityErrors (500).
        r = client.post("/auth/account/delete", json={"confirm": "w216del"})
        assert r.status_code == 200, r.text
        with factory() as session:
            assert session.get(Character, cid) is None
            assert session.get(CharacterAppearance, cid) is None


# ---------- byte-identity regression (m119 style, both descriptor paths) ----------

class TestByteIdentityRegression:
    def _add_character(self, session, world_id, cid):
        location_id = (
            session.query(Location).filter_by(world_id=world_id).first().id)
        session.add(Character(
            id=cid, world_id=world_id, first_name="Иван", last_name="Тестов",
            birth_date="1963-05-01", age=63, sex="M", alive=True,
            location_id=location_id, created_at=0, updated_at=0,
            looks=M119_FIXTURE["looks"]))
        for key, value in M119_FIXTURE["traits"].items():
            session.add(CharacterTrait(
                character_id=cid, trait_key=key, value=value))
        session.commit()
        return location_id

    def test_portrait_descriptor_without_row_is_pre020_bytes(self, session, world_id):
        cid = "w216_noapp_p"
        self._add_character(session, world_id, cid)
        descriptor = build_portrait_descriptor(session, world_id, cid)
        assert descriptor["visual_profile"] == PRE020_PROFILE
        prompt = build_prompt(descriptor)
        assert prompt.startswith(
            "Portrait of Иван Тестов, 63-year-old fictional adult man character, ")
        assert PRE020_PROFILE["face"] in prompt

    def test_scene_descriptor_without_row_is_pre020_bytes(self, session, world_id):
        location_id = self._add_character(session, world_id, "w216_noapp_s")
        descriptor = build_scene_descriptor(
            session, world_id, location_id, player_character_id="w216_noapp_s")
        assert descriptor["visual_profile"] == PRE020_PROFILE

    def test_prompt_only_row_still_resolves_pre020_bytes(self, session, world_id):
        # a row whose structured dict is NULL must not change anything:
        # the free text already rides characters.looks at creation.
        cid = "w216_promptonly"
        self._add_character(session, world_id, cid)
        session.add(CharacterAppearance(
            character_id=cid, appearance=None, appearance_prompt="что-то"))
        session.commit()
        descriptor = build_portrait_descriptor(session, world_id, cid)
        assert descriptor["visual_profile"] == PRE020_PROFILE

    def test_stored_appearance_row_beats_trait_derivation(self, session, world_id):
        cid = "w216_withapp"
        self._add_character(session, world_id, cid)
        session.add(CharacterAppearance(
            character_id=cid, appearance={"build": "athletic"},
            appearance_prompt=None))
        session.commit()
        descriptor = build_portrait_descriptor(session, world_id, cid)
        assert descriptor["visual_profile"]["body"] == "athletic build"
        # face path untouched (no face keys) — the looks derivation survives
        assert descriptor["visual_profile"]["face"] == PRE020_PROFILE["face"]
