"""019 Phase 1 (T1-T4): canonical visual profile resolver + prompt integration.

Contract: specs/019-flux-baseline-quality — frozen profile schema, fixed
portrait section order, guardrail from the real character age, sex label as
man/woman. Pure-function tests plus one fixture character row; no network,
no LLM (R5).
"""
from types import SimpleNamespace

from app.db.models import Character, CharacterTrait, Location
from app.visual.descriptor import (
    _AUTO_OUTFIT_POOL,
    build_portrait_descriptor,
    build_prompt,
    build_scene_descriptor,
    build_visual_profile,
)

FIXTURE_CHARACTER = {
    "name": "Ivan Testov",  # interface completeness; the profile ignores it
    "age": 63,
    "sex": "M",
    "looks": "silver-haired with a trimmed beard and steady gray eyes",
    "traits": {"sociability": 80, "discipline": 40, "risk_tolerance": 10},
    "worn": ["tweed jacket"],
}

LOOKS = FIXTURE_CHARACTER["looks"]
CASTING_PRESET = (
    # #227: RU→EN of the prod-probe anchors — "well inside the frame" +
    # nothing-below-the-chest added around the #205b margin phrase.
    "Chest-up portrait, entire head well inside the frame with clear margin "
    "above the hair, nothing below the chest visible, "
    "neutral warm-gray studio, soft frontal beauty lighting"
)
SCENE_PRESET = "natural environment lighting, candid composition"
CANONICAL_NEGATIVE = (
    "child, teen, teenage, underage, minor, loli, lolicon, shota, kid, "
    "young girl, young boy, real person, real people, celebrity, politician, "
    "photograph of a real human"
)


class TestResolverPure:
    def test_deterministic(self):
        first = build_visual_profile(FIXTURE_CHARACTER)
        second = build_visual_profile(FIXTURE_CHARACTER)
        assert first == second
        # Same state via attribute access (duck-typed input) -> same profile.
        as_object = SimpleNamespace(**FIXTURE_CHARACTER)
        assert build_visual_profile(as_object) == first

    def test_frozen_schema_exact(self):
        profile = build_visual_profile(FIXTURE_CHARACTER)
        assert set(profile.keys()) == {"face", "body", "style", "scenes", "guardrail", "lora"}
        assert set(profile["scenes"].keys()) == {"casting", "scene"}
        assert set(profile["guardrail"].keys()) == {"phrase", "canonical_negative"}
        assert set(profile["lora"].keys()) == {"available"}
        assert profile["lora"] == {"available": False}
        for key in ("face", "body", "style"):
            assert isinstance(profile[key], str) and profile[key]
        assert profile["scenes"]["casting"] == CASTING_PRESET
        assert profile["scenes"]["scene"] == SCENE_PRESET
        assert profile["guardrail"]["canonical_negative"] == CANONICAL_NEGATIVE

    def test_guardrail_uses_real_age(self):
        profile = build_visual_profile(FIXTURE_CHARACTER)
        assert profile["guardrail"]["phrase"] == (
            "63-year-old fictional adult man character"
        )
        younger = build_visual_profile({**FIXTURE_CHARACTER, "age": 25})
        assert younger["guardrail"]["phrase"] == (
            "25-year-old fictional adult man character"
        )

    def test_sex_label_mapping(self):
        man = build_visual_profile(FIXTURE_CHARACTER)
        woman = build_visual_profile({**FIXTURE_CHARACTER, "sex": "F"})
        empty = build_visual_profile({**FIXTURE_CHARACTER, "sex": ""})
        none_sex = build_visual_profile({**FIXTURE_CHARACTER, "sex": None})
        lower = build_visual_profile({**FIXTURE_CHARACTER, "sex": "m"})
        assert man["guardrail"]["phrase"].endswith("fictional adult man character")
        assert woman["guardrail"]["phrase"].endswith("fictional adult woman character")
        # Unknown sex collapses to the neutral adult label (019 T1 formula).
        assert empty["guardrail"]["phrase"] == "63-year-old fictional adult character"
        assert none_sex["guardrail"]["phrase"] == empty["guardrail"]["phrase"]
        assert lower["guardrail"]["phrase"] == man["guardrail"]["phrase"]
        assert man["body"] == "healthy adult man build"
        assert woman["body"] == "healthy adult woman build"
        assert empty["body"] == "healthy adult build"

    def test_face_body_from_looks(self):
        profile = build_visual_profile(FIXTURE_CHARACTER)
        assert profile["face"] == LOOKS
        assert profile["body"] == "healthy adult man build"
        fallback = build_visual_profile({**FIXTURE_CHARACTER, "looks": None})
        empty_looks = build_visual_profile({**FIXTURE_CHARACTER, "looks": ""})
        assert fallback["face"] == "natural, well-groomed features"
        assert fallback["face"] == empty_looks["face"]

    def test_style_trait_mapping(self):
        default = build_visual_profile({**FIXTURE_CHARACTER, "traits": {}})
        assert default["style"] == "modest, casual everyday styling"
        neutral = build_visual_profile({
            **FIXTURE_CHARACTER,
            "traits": {"sociability": 50, "discipline": 50, "risk_tolerance": 50},
        })
        assert neutral["style"] == default["style"]
        no_traits = build_visual_profile({**FIXTURE_CHARACTER, "traits": None})
        assert no_traits["style"] == default["style"]
        # Boundary: 67 counts as high, 66 does not.
        at_threshold = build_visual_profile({**FIXTURE_CHARACTER, "traits": {"sociability": 67}})
        below = build_visual_profile({**FIXTURE_CHARACTER, "traits": {"sociability": 66}})
        assert at_threshold["style"] == "sociable, outgoing presence"
        assert below["style"] == default["style"]
        assert build_visual_profile({
            **FIXTURE_CHARACTER, "traits": {"discipline": 90},
        })["style"] == "neat, put-together styling"
        assert build_visual_profile({
            **FIXTURE_CHARACTER, "traits": {"risk_tolerance": 75},
        })["style"] == "bold, adventurous styling"
        both = build_visual_profile({
            **FIXTURE_CHARACTER,
            "traits": {"sociability": 80, "discipline": 80, "risk_tolerance": 5},
        })
        assert both["style"] == (
            "sociable, outgoing presence; neat, put-together styling"
        )

    def test_trait_pairs_input(self):
        pairs = build_visual_profile({
            **FIXTURE_CHARACTER,
            "traits": [("sociability", 80), ("discipline", 40)],
        })
        mapping = build_visual_profile({
            **FIXTURE_CHARACTER, "traits": {"sociability": 80, "discipline": 40},
        })
        assert pairs == mapping


class TestPortraitPrompt:
    def test_prompt_fallback_without_profile(self):
        """Hand-made (pre-019) descriptors still build a total prompt."""
        descriptor = {
            "camera": "portrait",
            "characters": [{"id": "c1", "name": "Anna", "sex": "F", "age": 30}],
        }
        prompt = build_prompt(descriptor)
        assert prompt.startswith("Portrait of Anna, 30-year-old fictional adult woman character, ")
        assert "natural, well-groomed features" in prompt
        assert "healthy adult woman build" in prompt
        assert "modest, casual everyday styling" in prompt
        assert CASTING_PRESET in prompt
        # #205d: the absent-outfit_mode (auto) descriptor now ends with the
        # auto pool head (concrete garments beat generic phrasing against
        # the LoRA prior) appended after the clean-background tail.
        assert prompt.endswith(
            ", clean background, detailed face"
            f", {_AUTO_OUTFIT_POOL[0]}"
        )

    def test_prompt_deterministic_with_profile(self):
        descriptor = {
            "camera": "portrait",
            "characters": [{"id": "c1", "name": "Ivan", "sex": "M", "age": 63}],
            "visual_profile": build_visual_profile(FIXTURE_CHARACTER),
        }
        assert build_prompt(descriptor) == build_prompt(descriptor)
        prompt = build_prompt(descriptor)
        # Fixed section order: guardrail phrase -> face -> body -> style ->
        # casting preset (019 T2).
        positions = [
            prompt.index("63-year-old fictional adult man character"),
            prompt.index(LOOKS),
            prompt.index("healthy adult man build"),
            prompt.index("sociable, outgoing presence"),
            prompt.index(CASTING_PRESET),
        ]
        assert positions == sorted(positions)

    def test_prompt_keeps_framing_anchor(self):
        """W204b/#205b/#227: the casting preset's framing anchors survive
        into the prompt.

        Owner feedback 2026-10-16: flux at guidance 3.5 crops the head unless
        the prompt itself anchors composition — "head fully in frame" alone
        still cropped faces at guidance 3.5 (#205b). The pinned anchor is the
        #227 wording: "entire head well inside the frame with clear margin
        above the hair" + "nothing below the chest visible" (prod probe:
        crown clipped flush, crop crept to the waist), strengthened from the
        #205b phrase "entire head visible with clear margin above the hair".
        """
        descriptor = {
            "camera": "portrait",
            "characters": [{"id": "c1", "name": "Ivan", "sex": "M", "age": 63}],
            "visual_profile": build_visual_profile(FIXTURE_CHARACTER),
        }
        # #205b: explicit-margin anchor (stronger than "head fully in frame").
        # #227: framing strengthened — head INSIDE the frame + explicit
        # nothing-below-the-chest clause.
        assert (
            "entire head well inside the frame with clear margin above the hair"
            in build_prompt(descriptor)
        )
        assert "nothing below the chest visible" in build_prompt(descriptor)


class TestDescriptorIntegration:
    def _add_character(self, session, world_id, cid, *, sex, age, looks, traits):
        location_id = (
            session.query(Location).filter_by(world_id=world_id).first().id
        )
        session.add(Character(
            id=cid, world_id=world_id, first_name="Иван", last_name="Тестов",
            birth_date="1963-05-01", age=age, sex=sex, alive=True,
            location_id=location_id, created_at=0, updated_at=0, looks=looks,
        ))
        for key, value in traits.items():
            session.add(CharacterTrait(
                character_id=cid, trait_key=key, value=value))
        session.commit()
        return location_id

    def test_portrait_descriptor_carries_profile(self, session, world_id):
        cid = "w119_char"
        self._add_character(
            session, world_id, cid,
            sex="M", age=63, looks=LOOKS,
            traits={"sociability": 80, "discipline": 40, "risk_tolerance": 10},
        )
        descriptor = build_portrait_descriptor(session, world_id, cid)
        profile = descriptor["visual_profile"]
        assert profile == build_visual_profile({
            "name": "Иван Тестов", "age": 63, "sex": "M", "looks": LOOKS,
            "traits": {"sociability": 80, "discipline": 40, "risk_tolerance": 10},
            "worn": [],
        })
        # Identity facts stay raw in the canonical descriptor data.
        assert descriptor["characters"][0]["sex"] == "M"
        # Same state -> same descriptor bytes (R1 through the DB path).
        assert build_portrait_descriptor(session, world_id, cid) == descriptor
        prompt = build_prompt(descriptor)
        assert prompt.startswith(
            "Portrait of Иван Тестов, 63-year-old fictional adult man character, ")
        assert LOOKS in prompt
        assert "healthy adult man build" in prompt
        assert "sociable, outgoing presence" in prompt
        assert CASTING_PRESET in prompt
        # #205d: DB-path auto mode ends with the auto pool head appended
        # after the clean-background tail.
        assert prompt.endswith(
            ", clean background, detailed face"
            f", {_AUTO_OUTFIT_POOL[0]}"
        )

    def test_scene_descriptor_carries_player_profile(self, session, world_id):
        location_id = self._add_character(
            session, world_id, "w119_player",
            sex="F", age=30, looks="quick dark eyes and a ready smile",
            traits={"sociability": 75},
        )
        descriptor = build_scene_descriptor(
            session, world_id, location_id, player_character_id="w119_player")
        profile = descriptor["visual_profile"]
        assert profile["guardrail"]["phrase"] == (
            "30-year-old fictional adult woman character"
        )
        assert profile["style"] == "sociable, outgoing presence"
        # The pinned player-part key set is untouched (profile is top-level).
        assert set(descriptor["player"].keys()) == {"id", "name", "sex", "age", "looks", "worn"}
        prompt = build_prompt(descriptor)
        assert "natural environment lighting, candid composition" in prompt
        assert "sociable, outgoing presence" in prompt
        # Profile fragments land BEFORE the environment/time composition.
        assert prompt.index(SCENE_PRESET) < prompt.index("clear weather")
        # Existing environment composition survives (T4e).
        assert "clear weather" in prompt
        assert descriptor["time"]["time_of_day"] in prompt
        assert "camera: wide" in prompt

    def test_scene_without_player_unchanged(self, session, world_id):
        location_id = (
            session.query(Location).filter_by(world_id=world_id).first().id
        )
        descriptor = build_scene_descriptor(session, world_id, location_id)
        assert "visual_profile" not in descriptor
        prompt = build_prompt(descriptor)
        assert SCENE_PRESET not in prompt
        assert "sociable, outgoing presence" not in prompt
        assert "clear weather" in prompt
