"""020 wave 4 (issue #316) — clothing precedence + prompt byte contracts.

Contract of record: specs/020-character-creator/spec.md (wave-4 precedence
bullet): nude_framing > worn > explicit enum (casual/formal/nude) >
auto+custom_clothing > pool. Scenes ignore custom_clothing.

Covers (pure build_prompt unless stated):
- precedence matrix: worn beats formal/casual/custom; explicit enum beats
  auto+custom; nude beats custom; auto+custom replaces the pool.
- nude_framing path byte-unchanged: the API-level nude flag replaces EVERY
  clothing fragment; a custom_clothing key on the descriptor is ignored
  (byte-identical to the same descriptor without it).
- formal = the NEW concrete-garment bytes (superseded product decision —
  the m205/m216/m236 formal pins were bumped honestly).
- auto WITHOUT custom is byte-identical to the absent-key path and to the
  pinned pool-head bytes (the m205 hard requirement, re-proven here).
- scene prompts ignore custom_clothing (route-built scene descriptor for a
  character carrying stored `_presentation` custom clothing).
- preview-route wiring: custom_clothing rides the preview prompt in auto
  mode; an explicit outfit_mode outranks it; >500 chars → 422.
"""

from app.visual.descriptor import (
    _AUTO_OUTFIT_POOL,
    _FORMAL_OUTFIT_FRAGMENT,
    build_prompt,
)
from app.visual.style_presets import NUDE_FRAMING

DESCRIPTOR = {
    "camera": "portrait",
    "characters": [{"id": "c1", "name": "Anna", "sex": "F", "age": 30}],
}

AUTO_TAIL = f", {_AUTO_OUTFIT_POOL[0]}"
CUSTOM = "a tweed jacket"


class TestPrecedenceMatrix:
    def test_worn_beats_everything(self):
        prompt = build_prompt({**DESCRIPTOR, "outfit_mode": "formal",
                               "wearing": ["jacket on upper_body"],
                               "custom_clothing": CUSTOM})
        assert prompt.endswith(", wearing: ['jacket on upper_body']")
        assert "formal attire" not in prompt
        assert CUSTOM not in prompt

    def test_explicit_casual_beats_custom(self):
        prompt = build_prompt({**DESCRIPTOR, "outfit_mode": "casual",
                               "custom_clothing": CUSTOM})
        assert prompt.endswith(
            ", wearing casual clothes: a plain t-shirt and jeans, "
            "fully clothed")
        assert CUSTOM not in prompt

    def test_explicit_formal_beats_custom(self):
        prompt = build_prompt({**DESCRIPTOR, "outfit_mode": "formal",
                               "custom_clothing": CUSTOM})
        assert prompt.endswith(_FORMAL_OUTFIT_FRAGMENT)
        assert CUSTOM not in prompt

    def test_explicit_nude_beats_custom(self):
        prompt = build_prompt({**DESCRIPTOR, "outfit_mode": "nude",
                               "custom_clothing": CUSTOM})
        assert prompt.endswith(", nude, completely unclothed, wearing nothing")
        assert CUSTOM not in prompt

    def test_auto_plus_custom_replaces_pool(self):
        prompt = build_prompt({**DESCRIPTOR, "outfit_mode": "auto",
                               "custom_clothing": CUSTOM})
        assert prompt.endswith(f", wearing: {CUSTOM}, fully clothed")
        assert _AUTO_OUTFIT_POOL[0] not in prompt

    def test_absent_key_equals_auto_equals_pool_head(self):
        # the m205 hard requirement, re-proven on this fixture: absent
        # outfit_mode == "auto" == the pinned pool-head bytes
        absent = build_prompt({**DESCRIPTOR})
        auto = build_prompt({**DESCRIPTOR, "outfit_mode": "auto"})
        assert auto == absent
        assert auto.endswith(AUTO_TAIL)


class TestNudeFramingByteUnchanged:
    def test_custom_clothing_ignored_on_nude_framing_path(self):
        base = build_prompt({**DESCRIPTOR, "nude_framing": True})
        with_custom = build_prompt(
            {**DESCRIPTOR, "nude_framing": True, "custom_clothing": CUSTOM})
        assert with_custom == base
        assert with_custom.endswith(f", {NUDE_FRAMING}")
        assert CUSTOM not in with_custom
        # the nude_framing path also replaces the outfit chain
        assert "wearing" not in with_custom


class TestFormalBytes:
    def test_formal_new_bytes(self):
        prompt = build_prompt({**DESCRIPTOR, "outfit_mode": "formal"})
        assert prompt.endswith(_FORMAL_OUTFIT_FRAGMENT)
        assert prompt == (
            build_prompt({**DESCRIPTOR, "outfit_mode": "formal"}))


class TestSceneIgnoresCustom:
    def test_scene_prompt_has_no_custom_fragment(self, authed):
        settings, app, factory, client = authed("wcrscene")
        r = client.post("/characters", json={
            "name": "Сцена Тест", "sex": "F", "age": 30,
            "custom_clothing": "a tweed jacket"})
        assert r.status_code == 201, r.text
        cid = r.json()["id"]
        from app.db.models import Location
        from app.visual.descriptor import build_scene_descriptor
        with factory() as session:
            location_id = (
                session.query(Location)
                .filter_by(world_id=settings.world.world_id).first().id)
            descriptor = build_scene_descriptor(
                session, settings.world.world_id, location_id,
                player_character_id=cid)
            prompt = build_prompt(descriptor)
        assert "a tweed jacket" not in prompt
        assert "custom_clothing" not in prompt


class TestPreviewCustomClothing:
    BODY = {"sex": "F", "age": 27}

    def test_custom_rides_auto_preview_prompt(self, authed):
        settings, app, factory, client = authed("wcrpvcustom")
        r = client.post("/visual/preview", json={
            **self.BODY, "custom_clothing": "  a denim jacket  "})
        assert r.status_code == 200, r.text
        prompt = r.json()["prompt"]
        assert prompt.endswith(", wearing: a denim jacket, fully clothed")
        assert _AUTO_OUTFIT_POOL[0] not in prompt

    def test_preview_absent_custom_is_byte_identical_auto(self, authed):
        settings, app, factory, client = authed("wcrpvplain")
        plain = client.post("/visual/preview", json=dict(self.BODY)).json()
        auto = client.post("/visual/preview", json={
            **self.BODY, "outfit_mode": "auto"}).json()
        assert plain["prompt"] == auto["prompt"]
        assert plain["prompt"].endswith(AUTO_TAIL)

    def test_preview_explicit_formal_outranks_custom(self, authed):
        settings, app, factory, client = authed("wcrpvformal")
        r = client.post("/visual/preview", json={
            **self.BODY, "outfit_mode": "formal",
            "custom_clothing": "a denim jacket"})
        assert r.status_code == 200, r.text
        prompt = r.json()["prompt"]
        assert prompt.endswith(_FORMAL_OUTFIT_FRAGMENT)
        assert "a denim jacket" not in prompt

    def test_preview_custom_too_long_422(self, authed):
        settings, app, factory, client = authed("wcrpvlong")
        r = client.post("/visual/preview", json={
            **self.BODY, "custom_clothing": "x" * 501})
        assert r.status_code == 422, r.text
