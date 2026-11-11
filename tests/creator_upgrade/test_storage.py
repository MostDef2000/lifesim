"""020 wave 4 (issue #316) — storage contract: flat appearance + the
reserved `_presentation` metadata key.

Contract of record: specs/020-character-creator/spec.md (wave-4 Storage
bullet). The stored CharacterAppearance.appearance dict stays the FLAT 12-key
anatomy mapping at top level; custom clothing rides the SERVER-COMPOSED
reserved key `_presentation` = {"version": 1, "custom_clothing": ...}. The
public appearance input keeps rejecting that key (validate_appearance), and
every reader consumes the DECODED anatomy — the key can never leak into
fragments/profile/prompt bytes. Legacy flat rows decode unchanged and are
never rewritten.

Covers:
- decode_stored_appearance: flat+_presentation roundtrip (no mutation of
  the stored dict), legacy flat dict → (same dict, {}), None/non-dict/empty
  → (None, {}), malformed presentation → {}.
- `_presentation` never reaches _appearance_fragments / build_visual_profile
  output / the portrait descriptor JSON.
- validate_appearance rejects a `_presentation` key (public input).
- validate_custom_clothing bounds (None/empty/strip/500/501/non-str).
- POST /characters: outfit_mode + custom_clothing persist ATOMICALLY in the
  SAME transaction as the character (one fresh session sees both); absent
  fields → byte-identical previous behavior (outfit column default "auto",
  no `_presentation` key); custom-only (no anatomy) still writes the row;
  422 paths (bad outfit_mode / >500 custom) write nothing.
- a later portrait build reads the stored custom clothing (auto mode) and
  an explicit formal mode outranks it.
"""
import json

import pytest

from app.db.models import Character, CharacterAppearance
from app.visual.descriptor import (
    PRESENTATION_KEY,
    _appearance_fragments,
    build_portrait_descriptor,
    build_prompt,
    build_visual_profile,
    decode_stored_appearance,
    validate_appearance,
    validate_custom_clothing,
)

FLAT = {
    "face": "мягкие черты",
    "eyes": "серые",
    "hair_color": "рыжий",
    "build": "стройное",
}
PRESENTATION = {"version": 1, "custom_clothing": "a tweed jacket"}


class TestDecodeStoredAppearance:
    def test_roundtrip_flat_plus_presentation(self):
        stored = {**FLAT, PRESENTATION_KEY: dict(PRESENTATION)}
        anatomy, presentation = decode_stored_appearance(stored)
        assert anatomy == FLAT
        assert presentation == PRESENTATION
        # the stored row is never mutated
        assert stored == {**FLAT, PRESENTATION_KEY: dict(PRESENTATION)}

    def test_legacy_flat_dict_decodes_unchanged(self):
        anatomy, presentation = decode_stored_appearance(dict(FLAT))
        assert anatomy == FLAT
        assert presentation == {}

    def test_none_non_dict_and_empty_decode_to_none(self):
        assert decode_stored_appearance(None) == (None, {})
        assert decode_stored_appearance("x") == (None, {})
        assert decode_stored_appearance({}) == (None, {})

    def test_malformed_presentation_is_ignored(self):
        stored = {**FLAT, PRESENTATION_KEY: "garbage"}
        anatomy, presentation = decode_stored_appearance(stored)
        assert anatomy == FLAT
        assert presentation == {}


class TestPresentationNeverLeaks:
    def test_validate_appearance_rejects_presentation_key(self):
        # the PUBLIC appearance input keeps rejecting the reserved key —
        # `_presentation` is server-composed only.
        with pytest.raises(ValueError, match="unknown appearance field"):
            validate_appearance({PRESENTATION_KEY: dict(PRESENTATION)})

    def test_profile_output_carries_no_presentation(self):
        profile = build_visual_profile(
            {"age": 30, "sex": "F", "looks": None, "traits": {}},
            appearance={**FLAT, PRESENTATION_KEY: dict(PRESENTATION)},
        )
        assert PRESENTATION_KEY not in json.dumps(
            profile, ensure_ascii=False)

    def test_fragments_ignore_presentation_key(self):
        face, body = _appearance_fragments(
            {**FLAT, PRESENTATION_KEY: dict(PRESENTATION)})
        assert PRESENTATION_KEY not in face
        assert PRESENTATION_KEY not in body
        # and the decoded anatomy produces the SAME fragments as the bare
        # flat dict (the decode is byte-neutral for the resolver)
        assert (face, body) == _appearance_fragments(FLAT)


class TestValidateCustomClothing:
    @pytest.mark.parametrize("value,expected", [
        (None, None),
        ("", None),
        ("   ", None),
        ("  a tweed jacket  ", "a tweed jacket"),
        ("x" * 500, "x" * 500),
    ])
    def test_bounds(self, value, expected):
        assert validate_custom_clothing(value) == expected

    @pytest.mark.parametrize("value", ["x" * 501, 42, ["jacket"]])
    def test_rejections(self, value):
        with pytest.raises(ValueError):
            validate_custom_clothing(value)


def _create(client, **over):
    body = {"name": "Канон Тест", "sex": "M", "age": 30,
            "appearance": dict(FLAT)}
    body.update(over)
    r = client.post("/characters", json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


class TestAtomicPersist:
    def test_outfit_and_custom_persist_in_same_transaction(self, authed):
        settings, app, factory, client = authed("wcrstore")
        cid = _create(client, outfit_mode="formal",
                      custom_clothing="  a tweed jacket  ")
        # ONE fresh session sees BOTH the character (with outfit) and the
        # appearance row — the single commit landed them together.
        with factory() as session:
            character = session.get(Character, cid)
            assert character.outfit == "formal"
            row = session.get(CharacterAppearance, cid)
            assert row is not None
            assert row.appearance == {
                **FLAT, PRESENTATION_KEY: {
                    "version": 1, "custom_clothing": "a tweed jacket"}}

    def test_absent_fields_change_nothing(self, authed):
        settings, app, factory, client = authed("wcrstore2")
        cid = _create(client)  # no outfit_mode / custom_clothing keys
        with factory() as session:
            character = session.get(Character, cid)
            assert character.outfit == "auto"  # the column default
            row = session.get(CharacterAppearance, cid)
            assert row is not None
            assert row.appearance == FLAT  # NO _presentation key
        # and with no appearance at all: no row (pre-020 behavior)
        from tests.helpers import auth_client, make_user
        with factory() as session:
            user2 = make_user(session, "wcrstore3", "wcrstore3@x.com")
        client3 = auth_client(app, settings, user2)
        r = client3.post("/characters", json={
            "name": "Пре Тест", "sex": "F", "age": 31})
        assert r.status_code == 201, r.text
        with factory() as session:
            assert session.get(
                CharacterAppearance, r.json()["id"]) is None

    def test_custom_only_writes_presentation_only_row(self, authed):
        settings, app, factory, client = authed("wcrstore4")
        cid = _create(client, appearance=None,
                      custom_clothing="a tweed jacket")
        with factory() as session:
            row = session.get(CharacterAppearance, cid)
            assert row is not None
            assert row.appearance == {
                PRESENTATION_KEY: {
                    "version": 1, "custom_clothing": "a tweed jacket"}}
        # the portrait path still reads the custom clothing (anatomy absent
        # → trait-derived profile; no crash, no leak)
        with factory() as session:
            descriptor = build_portrait_descriptor(
                session, settings.world.world_id, cid)
        assert descriptor["custom_clothing"] == "a tweed jacket"
        assert PRESENTATION_KEY not in json.dumps(
            descriptor, ensure_ascii=False, default=str)

    def test_422_paths_write_nothing(self, authed):
        settings, app, factory, client = authed("wcrstore5")
        with factory() as session:
            before = session.query(Character).count()
        r = client.post("/characters", json={
            "name": "Плох Тест", "sex": "F", "age": 30,
            "custom_clothing": "x" * 501})
        assert r.status_code == 422, r.text
        r2 = client.post("/characters", json={
            "name": "Плох Тест 2", "sex": "F", "age": 30,
            "outfit_mode": "bogus"})
        assert r2.status_code == 422, r2.text
        with factory() as session:
            assert session.query(Character).count() == before
            assert session.query(CharacterAppearance).count() == 0


class TestPortraitReadsStoredCustom:
    def test_auto_mode_uses_stored_custom(self, authed):
        settings, app, factory, client = authed("wcrportrait")
        cid = _create(client, custom_clothing="a tweed jacket")
        with factory() as session:
            descriptor = build_portrait_descriptor(
                session, settings.world.world_id, cid)
            prompt = build_prompt(descriptor)
        assert prompt.endswith(", wearing: a tweed jacket, fully clothed")

    def test_explicit_formal_outranks_stored_custom(self, authed):
        settings, app, factory, client = authed("wcrportrait2")
        cid = _create(client, custom_clothing="a tweed jacket")
        with factory() as session:
            character = session.get(Character, cid)
            character.outfit = "formal"
            session.commit()
        with factory() as session:
            prompt = build_prompt(
                build_portrait_descriptor(session, settings.world.world_id,
                                          cid))
        assert ", wearing formal attire: a tailored blazer" in prompt
        assert "a tweed jacket" not in prompt
