"""#119 wave 3 — scene location grounding (descriptor.py).

Contract under test (backend/app/visual/descriptor.py, scene branch of
build_prompt):

- every COMMITTED location type (config/default.yaml §world.locations,
  lines 182-190) carries a deterministic environmental anchor phrase
  IMMEDIATELY AFTER the "<type> <name>" head component;
- UNKNOWN location types keep the EXACT legacy prompt bytes — no aliasing
  (the m6 "port" fixture must stay byte-identical; "port" is not "pier");
- portraits and preview modes keep the EXACT legacy bytes (the anchor fires
  only in the scene branch);
- descriptor shape, fragment ordering, weather/time/camera/reference
  fragments are unchanged; no parent-chain anchors (direct type only);
- same descriptor -> same prompt; the input dict is not mutated.

The nine anchor phrases below are FROZEN literals (architect-approved
wording) — they are intentionally independent of the implementation map so
phrase drift in the map fails these tests.

RED discipline: on base dbec014 the map does not exist and no anchor rides
the scene prompt — every anchor-position test fails on base while the
unknown-type/portrait/preview byte pins pass by design (regression guards).
"""
import copy
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from app.config.config import load_config  # noqa: E402
from app.visual.descriptor import (  # noqa: E402
    _SCENE_LOCATION_ANCHORS,
    build_prompt,
    build_visual_profile,
)

# The nine committed location types (config/default.yaml:182-190) and the
# architect-frozen anchor phrases — LITERAL, independent of the import.
FROZEN_ANCHORS = {
    "island": "outdoor island landscape, rocky coastline and surrounding sea",
    "settlement": "outdoor coastal village, wooden houses along village paths",
    "house": "wooden house interior, domestic living space",
    "shop": "small general store interior, shelves and a shop counter",
    "workshop": "workshop interior, workbenches and hand tools",
    "storage": "storage building interior, shelves and stacked crates",
    "well": "outdoor village water well, stone well rim and bucket",
    "pier": "outdoor wooden pier over sea water, mooring posts and open shoreline",
    "kitchen": "communal kitchen interior, cooking stove and preparation tables",
}

# Names from config/default.yaml:182-190 (any name works; these are the
# committed ones so the pins read like production prompts).
LOC_NAMES = {
    "island": "Reineke Island",
    "settlement": "Main Settlement",
    "house": "Residential House",
    "shop": "General Store",
    "workshop": "Workshop",
    "storage": "Community Storage",
    "well": "Water Well",
    "pier": "Main Pier",
    "kitchen": "Community Kitchen",
}

# Exact pre-change prompt bytes for the m6 "port" fixture (type "port" is
# NOT a committed type — must stay byte-identical, never aliased to "pier").
PORT_LEGACY_PROMPT = (
    "port Harbor, characters: ['Anna'], objects: ['crate'], "
    "clear weather, morning, camera: wide, "
    "reference portraits: ['visual/5.png']"
)
# A truly novel (never-committed) type must behave exactly like "port".
NOVEL_TYPE = "observatory"
NOVEL_LEGACY_PROMPT = (
    "observatory Harbor, characters: ['Anna'], objects: ['crate'], "
    "clear weather, morning, camera: wide, "
    "reference portraits: ['visual/5.png']"
)

# Portrait prompt for a descriptor that CARRIES a recognized-type location:
# the anchor must not fire in the portrait branch (frozen pre-change bytes).
PORTRAIT_LEGACY_PROMPT = (
    "Portrait of Anna, 30-year-old fictional adult woman character, "
    "natural, well-groomed features, healthy adult woman build, "
    "modest, casual everyday styling, "
    "Chest-up portrait, entire head well inside the frame with clear margin "
    "above the hair, nothing below the chest visible, "
    "neutral warm-gray studio, soft frontal beauty lighting, "
    "clean background, detailed face, "
    "wearing a fitted button-front blouse and jeans, fully clothed"
)

# Preview-mode (full_body_clothed) prompt carrying a recognized-type
# location: frozen pre-change bytes — the anchor never fires here either.
PREVIEW_LEGACY_PROMPT = (
    "Portrait of Anna, 30-year-old fictional adult woman character, "
    "natural, well-groomed features, healthy adult woman build, "
    "modest, casual everyday styling, "
    "full body character casting photograph, entire figure head to toe in "
    "frame, neutral lighting, clean background, detailed face, "
    "wearing casual clothes: a plain t-shirt and jeans, fully clothed, "
    "wide full body shot, entire figure visible head to toe, feet fully in "
    "frame, generous headroom, simple neutral background"
)


def _descriptor(loc_type, loc_name):
    """Hand-made scene descriptor (m6 fixture shape)."""
    return {
        "location": {"id": "loc_1", "name": loc_name, "type": loc_type},
        "characters": [{"id": "c1", "name": "Anna", "sex": "F", "age": 30}],
        "objects": [{"id": "o1", "type": "crate"}],
        "weather": "clear",
        "time": {"day": 3, "hour": 9, "time_of_day": "morning"},
        "camera": "wide",
        "event": None,
        "references": ["visual/5.png"],
    }


def _expected_prompt(loc_type):
    """Exact expected scene bytes for a recognized type: the anchor sits
    IMMEDIATELY AFTER the "<type> <name>" head, before the characters
    fragment; every other fragment keeps the legacy order."""
    head = f"{loc_type} {LOC_NAMES[loc_type]}"
    return (
        f"{head}, {FROZEN_ANCHORS[loc_type]}, "
        "characters: ['Anna'], objects: ['crate'], "
        "clear weather, morning, camera: wide, "
        "reference portraits: ['visual/5.png']"
    )


class TestNineAnchorsExactOutput:
    @pytest.mark.parametrize("loc_type", sorted(FROZEN_ANCHORS))
    def test_anchor_rides_immediately_after_head(self, loc_type):
        prompt = build_prompt(_descriptor(loc_type, LOC_NAMES[loc_type]))
        assert prompt == _expected_prompt(loc_type)

    def test_map_matches_frozen_phrases_exactly(self):
        """The implementation map is EXACTLY the frozen phrase set — no
        drift in keys or values, no extra entries."""
        assert _SCENE_LOCATION_ANCHORS == FROZEN_ANCHORS


class TestMapCoverage:
    def test_every_committed_location_type_has_an_entry(self):
        """Every type in config/default.yaml:182-190 is a map key."""
        settings = load_config("config/default.yaml")
        committed = {
            loc.type for loc in settings.locations.locations.values()}
        assert committed == {
            "island", "settlement", "house", "shop", "workshop",
            "storage", "well", "pier", "kitchen",
        }
        assert committed <= set(_SCENE_LOCATION_ANCHORS)


class TestUnknownTypesLegacyBytes:
    def test_port_keeps_exact_legacy_bytes(self):
        """The m6 fixture type "port" is unknown — EXACT legacy prompt bytes
        (deliberately NOT aliased to "pier")."""
        prompt = build_prompt(_descriptor("port", "Harbor"))
        assert prompt == PORT_LEGACY_PROMPT
        assert FROZEN_ANCHORS["pier"] not in prompt

    def test_truly_novel_type_keeps_exact_legacy_bytes(self):
        prompt = build_prompt(_descriptor(NOVEL_TYPE, "Harbor"))
        assert prompt == NOVEL_LEGACY_PROMPT
        for anchor in FROZEN_ANCHORS.values():
            assert anchor not in prompt


class TestDeterminismAndPurity:
    def test_same_descriptor_same_prompt(self):
        descriptor = _descriptor("pier", "Main Pier")
        assert build_prompt(descriptor) == build_prompt(descriptor)

    def test_input_descriptor_not_mutated(self):
        descriptor = _descriptor("well", "Water Well")
        snapshot = copy.deepcopy(descriptor)
        build_prompt(descriptor)
        assert descriptor == snapshot


class TestPlayerAndWornOrdering:
    def test_anchor_with_player_and_worn_fragments(self):
        descriptor = _descriptor("well", "Water Well")
        descriptor["player"] = {
            "id": "p1", "name": "Hero", "sex": "F", "age": 30,
            "looks": "", "worn": ["jacket on upper_body"],
        }
        descriptor["characters"][0]["worn"] = ["scarf on neck"]
        prompt = build_prompt(descriptor)
        anchor = FROZEN_ANCHORS["well"]
        # anchor still immediately after the head, ahead of characters
        assert prompt.startswith(f"well Water Well, {anchor}, characters:")
        # existing fragment ordering retained: head, anchor, characters,
        # objects, protagonist worn, NPC worn, weather, time, camera, refs
        positions = [
            prompt.index("well Water Well"),
            prompt.index(anchor),
            prompt.index("characters: ['Anna']"),
            prompt.index("objects: ['crate']"),
            prompt.index("the protagonist wears: jacket on upper_body"),
            prompt.index("Anna wears: scarf"),
            prompt.index("clear weather"),
            prompt.index("morning"),
            prompt.index("camera: wide"),
            prompt.index("reference portraits: ['visual/5.png']"),
        ]
        assert positions == sorted(positions)

    def test_anchor_without_player_or_worn(self):
        """No player, no worn items: anchor present, legacy fragment order
        around it unchanged (full-equality pin)."""
        prompt = build_prompt(_descriptor("shop", "General Store"))
        assert prompt == _expected_prompt("shop")
        assert "wears:" not in prompt


class TestPortraitAndPreviewLegacyBytes:
    def test_portrait_prompt_byte_identical_despite_location(self):
        """A portrait descriptor that even CARRIES a recognized-type
        location keeps the exact legacy bytes — the anchor fires only in
        the scene branch."""
        descriptor = {
            "location": {"id": "loc_1", "name": "Main Pier", "type": "pier"},
            "camera": "portrait",
            "characters": [{"id": "c1", "name": "Anna", "sex": "F", "age": 30}],
        }
        prompt = build_prompt(descriptor)
        assert prompt == PORTRAIT_LEGACY_PROMPT
        assert FROZEN_ANCHORS["pier"] not in prompt

    def test_preview_mode_prompt_byte_identical_despite_location(self):
        descriptor = {
            "location": {"id": "loc_1", "name": "Main Pier", "type": "pier"},
            "characters": [{"id": "c1", "name": "Anna", "sex": "F", "age": 30}],
            "objects": [],
            "weather": "clear",
            "time": {"day": 3, "hour": 9, "time_of_day": "morning"},
            "camera": "portrait",
            "event": None,
            "references": [],
            "outfit_mode": "casual",
            "visual_profile": build_visual_profile(
                {"age": 30, "sex": "F", "looks": None, "traits": {}}),
            "preview_mode": "full_body_clothed",
        }
        prompt = build_prompt(descriptor)
        assert prompt == PREVIEW_LEGACY_PROMPT
        assert FROZEN_ANCHORS["pier"] not in prompt
