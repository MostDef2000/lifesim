"""#224 — build-time RU→EN value mapping for structured appearance values.

Contract (spec 020 P2 + issue #224): the RU creator UI stores structured
appearance values verbatim in Russian; the Flux model ignores Russian in
prompts (prod-probe evidence: «рыжий»/«серые» rendered brown/ombré). The
fix maps KNOWN normalized values through
descriptor._APPEARANCE_VALUE_MAP at BUILD time only, in the
fragment-building path (_appearance_fragments):

- mapped value → the EN phrase renders in the existing template slot;
- UNKNOWN value → byte-identical verbatim pass-through (deterministic);
- free-text looks / appearance_prompt are NEVER translated (user's words);
- storage (character_appearances.appearance) and validate_appearance see
  the RU values unchanged — the map never feeds back into storage.

Grammar rule (documented on the constant): an EN value must compose
grammatically with its template suffix — adjective for "{} face"
("soft, gentle" + " face"), plain phrase for the suffix-less "{}" features
slot; slots without a clean compose (numeric height/weight) stay unmapped.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from app.visual.descriptor import (  # noqa: E402
    _character_appearance,
    build_prompt,
    build_visual_profile,
    validate_appearance,
)


class TestProbeVocabularyMapping:
    """The exact probe vocabulary from issue #224 (prod probe returned
    brown/ombré hair + brown eyes for рыжий/серые) — each value must render
    as the mapped EN phrase, byte-exact, in its template slot."""

    def test_probe_vocabulary_maps_to_exact_fragment_bytes(self):
        profile = build_visual_profile(
            {"age": 27, "sex": "F", "looks": None, "traits": {}},
            appearance={
                "face": "мягкие черты", "eyes": "серые", "hair": "до плеч",
                "hair_color": "рыжий", "build": "атлетичное",
            })
        # #224: RU→EN value mapping — exact new fragment bytes
        assert profile["face"] == (
            "soft, gentle face, grey eyes, shoulder-length hair, "
            "ginger red hair color")
        assert profile["body"] == "athletic build"

    def test_full_prompt_rides_mapped_values(self):
        """End-to-end through build_prompt: no RU value bytes survive into
        the portrait prompt for the mapped probe vocabulary."""
        profile = build_visual_profile(
            {"age": 27, "sex": "F", "looks": None, "traits": {}},
            appearance={"face": "мягкие черты", "hair_color": "рыжий",
                        "eyes": "серые", "skin": "светлая"})
        prompt = build_prompt({
            "camera": "portrait",
            "characters": [{"name": "Тест", "age": 27, "sex": "F"}],
            "visual_profile": profile,
        })
        assert "мягкие" not in prompt
        assert "рыжий" not in prompt
        assert "серые" not in prompt
        assert "светлая" not in prompt
        assert "soft, gentle face, grey eyes, " in prompt
        assert "ginger red hair color, fair skin" in prompt

    def test_case_and_whitespace_normalize_before_lookup(self):
        """Lookup key is value.strip().lower(): «Рыжий », «РЫЖИЙ» map too."""
        profile = build_visual_profile(
            {"age": 27, "sex": "F", "looks": None, "traits": {}},
            appearance={"hair_color": "  Рыжий  ", "build": "Атлетичное"})
        assert profile["face"] == "ginger red hair color"
        assert profile["body"] == "athletic build"


class TestUnknownValuePassThrough:
    def test_unknown_values_are_byte_identical_verbatim(self):
        """Unmapped values (the documented contract) render exactly as
        before #224 — every template slot probed with an unknown RU value
        keeps its byte-identical pass-through assertion."""
        profile = build_visual_profile(
            {"age": 30, "sex": "M", "looks": None, "traits": {}},
            appearance={
                "face": "сдержанные черты", "eyes": "неоново-бирюзовые",
                "nose": "с горбинкой", "lips": "особенные",
                "hair": "каре", "hair_color": "неоново-бирюзовый",
                "skin": "фарфоровая с румянцем", "features": "родинка",
                "height": "170", "weight": "60 кг",
                "build": "жилистое", "chest": "86 см",
            })
        assert profile["face"] == (
            "сдержанные черты face, неоново-бирюзовые eyes, с горбинкой nose, "
            "особенные lips, каре hair, неоново-бирюзовый hair color, "
            "фарфоровая с румянцем skin, родинка")
        assert profile["body"] == (
            "height 170, weight 60 кг, жилистое build, 86 см chest")

    def test_known_and_unknown_mix_in_one_call(self):
        """A mapped value and an unknown value in the SAME call: the known
        one maps, the unknown one passes through — per-value, not
        per-call behavior."""
        profile = build_visual_profile(
            {"age": 27, "sex": "F", "looks": None, "traits": {}},
            appearance={"face": "мягкие черты",
                        "hair_color": "неоново-бирюзовый"})
        assert profile["face"] == (
            "soft, gentle face, неоново-бирюзовый hair color")


class TestBuildTimeOnlyIsolation:
    def test_validate_appearance_returns_ru_unchanged(self):
        """The validation/storage contract is untouched: cleaned values are
        the stripped RU strings, never the EN phrases."""
        assert validate_appearance({
            "face": "  мягкие черты  ", "hair_color": "рыжий",
            "eyes": "серые"}) == {
                "face": "мягкие черты", "hair_color": "рыжий",
                "eyes": "серые"}

    def test_character_appearance_reads_back_ru_verbatim(self, session, world_id):
        """Storage isolation through the read path used by the resolvers:
        _character_appearance returns the stored RU dict unchanged while
        build_visual_profile on the SAME dict renders EN — the mapping
        exists only at build time."""
        from app.db.models import Character, CharacterAppearance, Location

        cid = "w224_iso"
        location_id = (
            session.query(Location).filter_by(world_id=world_id).first().id)
        session.add(Character(
            id=cid, world_id=world_id, first_name="Иван", last_name="Тестов",
            birth_date="1990-01-01", age=30, sex="M", alive=True,
            location_id=location_id, created_at=0, updated_at=0,
            looks=None))
        stored = {"face": "мягкие черты", "hair_color": "рыжий",
                  "eyes": "серые"}
        session.add(CharacterAppearance(
            character_id=cid, appearance=dict(stored),
            appearance_prompt=None))
        session.commit()

        assert _character_appearance(session, cid) == stored
        # same dict through the build path: EN renders, storage untouched
        profile = build_visual_profile(
            {"age": 30, "sex": "M", "looks": None, "traits": {}},
            appearance=_character_appearance(session, cid))
        assert profile["face"] == (
            "soft, gentle face, grey eyes, ginger red hair color")
        assert _character_appearance(session, cid) == stored

    def test_free_text_looks_is_never_translated(self):
        """Free text (looks / appearance_prompt) is the user's own words —
        the #224 map must never touch it, even when the text contains
        values that ARE mapped for structured fields."""
        profile = build_visual_profile(
            {"age": 27, "sex": "F", "looks": "рыжий, мягкие черты, до плеч",
             "traits": {}},
            appearance={"hair_color": "рыжий"})
        # looks verbatim FIRST; only the structured fragment is mapped
        assert profile["face"] == (
            "рыжий, мягкие черты, до плеч, ginger red hair color")

    def test_map_covers_all_twelve_fields_and_is_bounded(self):
        """The constant is a per-field dict over EXACTLY the canonical
        field set — neither unknown keys (dead weight) nor missing fields
        (drift) survive this equality."""
        from app.visual.descriptor import _APPEARANCE_VALUE_MAP, APPEARANCE_FIELDS

        assert set(_APPEARANCE_VALUE_MAP) == set(APPEARANCE_FIELDS)
        for field, values in _APPEARANCE_VALUE_MAP.items():
            assert isinstance(values, dict) and values, field
            for ru, en in values.items():
                assert ru == ru.strip().lower(), (field, ru)
                assert en == en.strip() and en, (field, en)


@pytest.mark.parametrize("field,value,expected_fragment", [
    # the probe vocabulary, field by field (grammar compose per slot)
    ("face", "мягкие черты", "soft, gentle face"),
    ("eyes", "серые", "grey eyes"),
    ("hair", "до плеч", "shoulder-length hair"),
    ("hair_color", "рыжий", "ginger red hair color"),
    ("build", "атлетичное", "athletic build"),
    ("skin", "светлая", "fair skin"),
    ("nose", "прямой", "straight nose"),
    ("lips", "полные", "full lips"),
    ("chest", "большая", "full chest"),
    ("features", "веснушки", "freckles"),
])
def test_probe_vocabulary_per_field(field, value, expected_fragment):
    """Each mapped value composes grammatically with its template slot
    (see the grammar rule on _APPEARANCE_VALUE_MAP)."""
    profile = build_visual_profile(
        {"age": 27, "sex": "F", "looks": None, "traits": {}},
        appearance={field: value})
    fragment = profile["face"] if field in (
        "face", "eyes", "nose", "lips", "hair", "hair_color", "skin",
        "features") else profile["body"]
    assert fragment == expected_fragment, (field, value)
