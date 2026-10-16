"""#205 — per-character outfit/nudity control (020 §Outfit / nudity control).

Contract under test:
- characters.outfit: auto|casual|formal|nude; NULL/unknown -> "auto";
  default "auto" = EXACTLY the pre-#205 prompt bytes (byte-identity is the
  hard requirement, proved against a hardcoded old-way fixture string AND
  against an outfit_mode-less descriptor).
- The outfit fragment lands in the portrait branch only, AFTER the m119
  endswith anchor; worn WorldObjects ALWAYS win in every mode (including
  nude — an explicitly worn item means dressed).
- Guardrail phrase / canonical_negative are never touched by this field
  (#119 invariant); visual_profile key set stays frozen (m119 pin).
- nude requires the OWNING user's age_confirmed (П4 18+); NPCs never render
  nude. PATCH endpoint: owner-only 403, unknown 404, unauthenticated 401,
  enum 422, nude-without-age-confirmation 422.
- Schema migration: no alembic exists in this repo — the additive column is
  created by create_all on fresh DBs and healed onto pre-#205 DBs by
  models.ensure_outfit_column (the repo's own "alpha schema self-heal"
  convention, B2-review F3). SchemaMeta version stays 0.15.0.
"""
import os
import random
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from app.api.auth import hash_password  # noqa: E402
from app.config.config import load_config  # noqa: E402
from app.db.models import (  # noqa: E402
    Character,
    User,
    bootstrap,
    create_engine_factory,
    ensure_outfit_column,
)
from app.visual.descriptor import (  # noqa: E402
    build_portrait_descriptor,
    build_prompt,
    build_visual_profile,
)

SETTINGS = None

# The hardcoded PRE-#205 portrait prompt for PORTRAIT_DESCRIPTOR below —
# built the old way (no outfit logic): guardrail phrase, face, body, style,
# casting preset, clean background. Auto mode must reproduce these EXACT
# bytes (the hard requirement of the work order). Byte-identity oracle is
# relative to the world WITH the #205b framing anchor (pre-outfit bytes,
# post-framing): the casting section carries the explicit-margin anchor
# "entire head visible with clear margin above the hair" (#205b —
# "head fully in frame" alone still cropped faces at guidance 3.5).
OLD_WAY_AUTO_PROMPT = (
    "Portrait of Anna, 30-year-old fictional adult woman character, "
    "natural, well-groomed features, healthy adult woman build, "
    "modest, casual everyday styling, "
    "Chest-up portrait, entire head visible with clear margin above the hair, "
    "neutral warm-gray studio, soft frontal beauty lighting, "
    "clean background, detailed face"
)

PORTRAIT_DESCRIPTOR = {
    "camera": "portrait",
    "characters": [{"id": "c1", "name": "Anna", "sex": "F", "age": 30}],
}


def setup_module(module):
    global SETTINGS
    SETTINGS = load_config("config/default.yaml")


def _without_outfit(descriptor):
    return {k: v for k, v in descriptor.items() if k != "outfit_mode"}


class TestAutoByteIdentity:
    def test_absent_key_equals_auto_equals_old_bytes(self):
        """HARD requirement: outfit_mode="auto" is byte-identical to the
        pre-#205 behavior — proved three ways: descriptor WITHOUT the key
        (pre-#205 shape), descriptor with outfit_mode="auto", and the
        hardcoded OLD_WAY_AUTO_PROMPT string composed exactly as the old
        code did (profile sections + clean background tail, no fragment)."""
        old_prompt = build_prompt(_without_outfit(PORTRAIT_DESCRIPTOR))
        auto_prompt = build_prompt({**PORTRAIT_DESCRIPTOR, "outfit_mode": "auto"})
        assert auto_prompt == old_prompt
        assert auto_prompt == OLD_WAY_AUTO_PROMPT

    def test_auto_leaves_guardrail_and_negative_untouched(self):
        descriptor = {**PORTRAIT_DESCRIPTOR, "outfit_mode": "auto"}
        prompt = build_prompt(descriptor)
        profile = build_visual_profile(PORTRAIT_DESCRIPTOR["characters"][0])
        # #119 invariant: the 18+ shield and canonical negative are exactly
        # the profile values — the outfit field cannot alter them.
        assert profile["guardrail"]["phrase"] in prompt
        assert profile["guardrail"]["canonical_negative"] == (
            build_visual_profile(FIXTURE)["guardrail"]["canonical_negative"]
        )

    def test_descriptor_resolver_defaults(self, session, world_id):
        """NULL outfit and unknown values read as "auto" through the DB path."""
        cid = self._add_character(
            session, world_id, "w205_null_outfit", outfit=None)
        descriptor = build_portrait_descriptor(session, world_id, cid)
        assert descriptor["outfit_mode"] == "auto"
        cid = self._add_character(
            session, world_id, "w205_bad_outfit", outfit="beachwear")
        assert build_portrait_descriptor(
            session, world_id, cid)["outfit_mode"] == "auto"

    # shared helpers ------------------------------------------------------

    def _add_character(self, session, world_id, cid, *, outfit="auto"):
        from app.db.models import Location

        location_id = (
            session.query(Location).filter_by(world_id=world_id).first().id
        )
        session.add(Character(
            id=cid, world_id=world_id, first_name="Анна", last_name="Тестова",
            birth_date="1996-05-01", age=30, sex="F", alive=True,
            location_id=location_id, created_at=0, updated_at=0,
            outfit=outfit,
        ))
        session.commit()
        return cid


FIXTURE = {
    "name": "Ivan Testov",
    "age": 63,
    "sex": "M",
    "looks": "silver-haired with a trimmed beard and steady gray eyes",
    "traits": {"sociability": 80, "discipline": 40, "risk_tolerance": 10},
}


class TestOutfitFragments:
    @pytest.mark.parametrize("mode,fragment", [
        # #205b (owner feedback 2026-10-16): the casual fragment now names
        # the garments and states "fully clothed" — "casual everyday clothes"
        # was too weak against the uncensored LoRA (topless renders).
        ("casual", ", wearing casual clothes: a plain t-shirt and jeans, fully clothed"),
        ("formal", ", wearing formal attire"),
        ("nude", ", nude"),
    ])
    def test_fragment_per_mode(self, mode, fragment):
        descriptor = {**PORTRAIT_DESCRIPTOR, "outfit_mode": mode}
        prompt = build_prompt(descriptor)
        assert prompt.endswith(fragment)
        # The fragment lands AFTER the m119 endswith anchor.
        assert prompt.startswith("Portrait of Anna, ")
        assert ", clean background, detailed face" in prompt

    def test_casual_mode_prompt_is_fully_clothed(self):
        """#205b (owner feedback 2026-10-16): the built casual-mode prompt
        contains "fully clothed" — the old "casual everyday clothes" fragment
        was too weak against the uncensored LoRA (topless casual renders)."""
        prompt = build_prompt({**PORTRAIT_DESCRIPTOR, "outfit_mode": "casual"})
        assert "fully clothed" in prompt

    def test_fragment_order_after_profile_sections(self):
        prompt = build_prompt({**PORTRAIT_DESCRIPTOR, "outfit_mode": "formal"})
        assert prompt.index("Chest-up portrait") < prompt.index(
            ", wearing formal attire")

    def test_auto_no_fragment(self):
        prompt = build_prompt({**PORTRAIT_DESCRIPTOR, "outfit_mode": "auto"})
        assert "wearing" not in prompt
        assert "nude" not in prompt

    @pytest.mark.parametrize("mode", ["auto", "casual", "formal", "nude"])
    def test_worn_items_always_win(self, mode):
        """Worn WorldObjects take precedence in EVERY mode incl. nude."""
        descriptor = {
            **PORTRAIT_DESCRIPTOR,
            "wearing": ["jacket on upper_body"],
            "outfit_mode": mode,
        }
        prompt = build_prompt(descriptor)
        assert prompt.endswith(", wearing: ['jacket on upper_body']")
        # #205b: the casual fragment now reads "...fully clothed" — a worn
        # item must still suppress it along with the other mode fragments.
        assert "fully clothed" not in prompt
        assert "wearing formal attire" not in prompt
        assert ", nude" not in prompt


class TestDescriptorAgeGate:
    """The effective-mode resolver: nude needs an age-confirmed OWNER."""

    def _add_character(self, session, world_id, cid, *, outfit="auto", user_id=None):
        from app.db.models import Location

        location_id = (
            session.query(Location).filter_by(world_id=world_id).first().id
        )
        session.add(Character(
            id=cid, world_id=world_id, first_name="Анна", last_name="Тестова",
            birth_date="1996-05-01", age=30, sex="F", alive=True,
            location_id=location_id, created_at=0, updated_at=0,
            user_id=user_id, outfit=outfit,
        ))
        session.commit()
        return cid

    def test_nude_owner_age_confirmed(self, session, world_id):
        from app.db.models import User

        user = User(
            username="w205adult", email="w205a@x.com",
            password_hash="x", age_confirmed=True, created_at=0,
        )
        session.add(user)
        session.commit()
        cid = self._add_character(
            session, world_id, "w205_nude_ok", outfit="nude", user_id=user.id)
        descriptor = build_portrait_descriptor(session, world_id, cid)
        assert descriptor["outfit_mode"] == "nude"
        assert build_prompt(descriptor).endswith(", nude")

    def test_nude_owner_not_age_confirmed_collapses_to_auto(self, session, world_id):
        from app.db.models import User

        user = User(
            username="w205minor", email="w205n@x.com",
            password_hash="x", age_confirmed=False, created_at=0,
        )
        session.add(user)
        session.commit()
        cid = self._add_character(
            session, world_id, "w205_nude_no", outfit="nude", user_id=user.id)
        descriptor = build_portrait_descriptor(session, world_id, cid)
        assert descriptor["outfit_mode"] == "auto"
        prompt = build_prompt(descriptor)
        assert ", nude" not in prompt

    def test_nude_npc_collapses_to_auto(self, session, world_id):
        cid = self._add_character(
            session, world_id, "w205_nude_npc", outfit="nude", user_id=None)
        descriptor = build_portrait_descriptor(session, world_id, cid)
        assert descriptor["outfit_mode"] == "auto"
        assert ", nude" not in build_prompt(descriptor)

    def test_visual_profile_schema_frozen(self, session, world_id):
        """m119 pin honesty: outfit NEVER leaks into visual_profile."""
        cid = self._add_character(session, world_id, "w205_profile_pin")
        descriptor = build_portrait_descriptor(session, world_id, cid)
        assert set(descriptor["visual_profile"].keys()) == {
            "face", "body", "style", "scenes", "guardrail", "lora"}

    def test_worn_items_win_through_db_path(self, session, world_id):
        from app.db.models import User, WorldObject
        from app.social.clothing import toggle_wear

        user = User(
            username="w205worn", email="w205w@x.com",
            password_hash="x", age_confirmed=True, created_at=0,
        )
        session.add(user)
        session.commit()
        cid = self._add_character(
            session, world_id, "w205_worn", outfit="nude", user_id=user.id)
        location_id = session.get(Character, cid).location_id
        session.add(WorldObject(
            world_id=world_id, location_id=location_id,
            owner_character_id=cid, object_type="jacket", quantity=1,
            condition=100, object_metadata="{}",
        ))
        session.commit()
        jacket_id = (
            session.query(WorldObject)
            .filter_by(owner_character_id=cid, object_type="jacket").one().id
        )
        toggle_wear(session, world_id, cid, jacket_id)
        session.commit()
        descriptor = build_portrait_descriptor(session, world_id, cid)
        # nude mode + worn jacket -> the worn item wins, no nude fragment.
        assert descriptor["outfit_mode"] == "nude"
        assert "wearing: ['jacket on upper_body']" in build_prompt(descriptor)
        assert ", nude" not in build_prompt(descriptor)


class TestOutfitAPI:
    """PATCH /characters/{cid}/outfit — owner-only, enum, 18+ nude gate."""

    PW = "Correct-Horse-9!"

    def _build(self, tmp_path, *, owner_age_confirmed=True):
        from sqlalchemy.orm import sessionmaker

        from app.api.app import create_app
        from app.characters.generator import generate_population
        from app.world.seed_world import seed_world

        settings = SETTINGS.model_copy(update={
            "persistence": SETTINGS.persistence.model_copy(
                update={"db_path": str(tmp_path / "w205.db")}
            ),
        })
        engine = create_engine_factory(settings)
        with sessionmaker(bind=engine)() as session:
            bootstrap(engine, settings, seed=42)
            seed_world(session, settings, settings.world.world_id)
            generate_population(
                session, settings, random.Random(42),
                settings.world.world_id, 20,
            )
            session.add(User(
                username="w205owner", email="w205@x.com",
                password_hash=hash_password(self.PW),
                role="player", age_confirmed=owner_age_confirmed, created_at=0,
            ))
            session.commit()
            from app.characters.player import create_player_character

            user = session.query(User).filter_by(username="w205owner").one()
            char = create_player_character(
                session, settings, settings.world.world_id,
                user, "Одежда Тест", "M", 30, 0,
            )
            session.commit()
            char_id = char.id
            # Fresh player character defaults to the column default.
            assert char.outfit == "auto"
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        client = TestClient(create_app(settings, factory))
        r = client.post("/auth/login", json={
            "username": "w205owner", "password": self.PW})
        assert r.status_code == 200, r.text
        return client, factory, settings.world.world_id, char_id

    @pytest.fixture()
    def world(self, tmp_path):
        return self._build(tmp_path)

    def test_patch_owner_sets_mode_and_read_model(self, world):
        client, factory, world_id, cid = world
        r = client.patch(f"/characters/{cid}/outfit",
                         json={"outfit_mode": "casual"})
        assert r.status_code == 200, r.text
        assert r.json() == {"id": cid, "outfit_mode": "casual"}
        # owner read model echoes the stored value (additive key)
        data = client.get(f"/characters/{cid}").json()
        assert data["outfit_mode"] == "casual"
        # descriptor -> prompt fragment visible through the DB path
        with factory() as session:
            prompt = build_prompt(
                build_portrait_descriptor(session, world_id, cid))
        # #205b: casual fragment names the garments and is "fully clothed".
        assert prompt.endswith(
            ", wearing casual clothes: a plain t-shirt and jeans, fully clothed")
        # back to auto
        r = client.patch(f"/characters/{cid}/outfit",
                         json={"outfit_mode": "auto"})
        assert r.json()["outfit_mode"] == "auto"

    def test_default_is_auto(self, world):
        client, _, _, cid = world
        assert client.get(f"/characters/{cid}").json()["outfit_mode"] == "auto"

    def test_unauthenticated_401(self, world):
        client, _, _, cid = world
        anon = TestClient(client.app)
        r = anon.patch(f"/characters/{cid}/outfit",
                       json={"outfit_mode": "casual"})
        assert r.status_code == 401

    def test_unknown_character_404(self, world):
        client, _, _, _ = world
        r = client.patch("/characters/plr_9999/outfit",
                         json={"outfit_mode": "casual"})
        assert r.status_code == 404

    def test_non_owner_403(self, world):
        client, factory, _, cid = world
        r = client.post("/auth/register", json={
            "username": "w205other", "email": "w205o@x.com",
            "password": self.PW, "age_confirmed": True})
        assert r.status_code == 201, r.text
        other = TestClient(client.app)
        r = other.post("/auth/login", json={
            "username": "w205other", "password": self.PW})
        assert r.status_code == 200, r.text
        r = other.patch(f"/characters/{cid}/outfit",
                        json={"outfit_mode": "casual"})
        assert r.status_code == 403
        # the stored value is untouched
        with factory() as session:
            assert session.get(Character, cid).outfit == "auto"

    def test_invalid_enum_422(self, world):
        client, _, _, cid = world
        for bad in ("beachwear", "AUTO", "", "nudity"):
            r = client.patch(f"/characters/{cid}/outfit",
                             json={"outfit_mode": bad})
            assert r.status_code == 422, (bad, r.text)

    def test_nude_requires_age_confirmed_owner(self, tmp_path):
        client, factory, world_id, cid = self._build(
            tmp_path, owner_age_confirmed=False)
        r = client.patch(f"/characters/{cid}/outfit",
                         json={"outfit_mode": "nude"})
        assert r.status_code == 422, r.text
        with factory() as session:
            assert session.get(Character, cid).outfit != "nude"
        # the read-model block stays owner-only for a registered reader
        r = client.post("/auth/register", json={
            "username": "w205reader", "email": "w205r@x.com",
            "password": self.PW, "age_confirmed": True})
        assert r.status_code == 201, r.text
        reader = TestClient(client.app)
        reader.post("/auth/login", json={
            "username": "w205reader", "password": self.PW})
        data = reader.get(f"/characters/{cid}").json()
        assert "outfit_mode" not in data  # owner-only key (m67 discipline)

    def test_by_user_payload_has_no_outfit_mode(self, world):
        """F1 (review): /characters/by-user/{id} is UNAUTHENTICATED in base
        (pre-existing IDOR, separate ticket) — outfit_mode must NOT ride it.
        The profile UI reads the value from the owner-gated
        GET /characters/{cid} cold-load (covered by
        test_patch_owner_sets_mode_and_read_model). Regression pin: the
        by-user payload stays outfit-free for anonymous AND authenticated
        callers alike."""
        client, factory, _, cid = world
        # owner sets a non-default mode, then anyone may list the row
        r = client.patch(f"/characters/{cid}/outfit",
                         json={"outfit_mode": "formal"})
        assert r.status_code == 200, r.text
        me = client.get("/auth/me").json()
        anon = TestClient(client.app)
        # anonymous AND authenticated callers alike
        for cl in (anon, client):
            rows = cl.get(f"/characters/by-user/{me['id']}").json()
            assert len(rows) == 1
            assert "outfit_mode" not in rows[0], (
                "outfit_mode leaked into the unauthenticated by-user payload")

    def test_patch_npc_character_403(self, world):
        """F4: NPCs have no owning user — a PATCH can never be authorized."""
        client, factory, world_id, _ = world
        with factory() as session:
            npc = (
                session.query(Character)
                .filter_by(world_id=world_id)
                .filter(Character.user_id.is_(None))
                .first()
            )
            npc_id = npc.id
        r = client.patch(f"/characters/{npc_id}/outfit",
                         json={"outfit_mode": "casual"})
        assert r.status_code == 403
        with factory() as session:
            assert session.get(Character, npc_id).outfit != "casual"

    def test_noop_patch_does_not_bump_updated_at(self, world):
        """F4: re-submitting the stored mode must not touch updated_at
        (the no-op branch returns early without committing). The world
        clock is bumped between the two PATCHes so a broken no-op branch
        WOULD visibly move updated_at (both _world_now calls returning the
        same 0 would make the assertion trivially true)."""
        client, factory, world_id, cid = world
        r = client.patch(f"/characters/{cid}/outfit",
                         json={"outfit_mode": "casual"})
        assert r.status_code == 200, r.text
        with factory() as session:
            after_first = session.get(Character, cid).updated_at
            # advance the game clock: a (buggy) unconditional write would
            # stamp this later timestamp on the no-op PATCH below.
            from app.db.models import WorldClock

            clock = (
                session.query(WorldClock).filter_by(world_id=world_id).one()
            )
            clock.game_timestamp = (after_first or 0) + 500
            session.commit()
        r = client.patch(f"/characters/{cid}/outfit",
                         json={"outfit_mode": "casual"})
        assert r.status_code == 200, r.text
        assert r.json() == {"id": cid, "outfit_mode": "casual"}
        with factory() as session:
            assert session.get(Character, cid).updated_at == after_first


class TestSchemaSelfHeal:
    """Migration evidence (repo convention: create_all + additive self-heal;
    no alembic exists in this repository)."""

    def test_column_default_on_fresh_bootstrap(self, tmp_path):
        settings = SETTINGS.model_copy(update={
            "persistence": SETTINGS.persistence.model_copy(
                update={"db_path": str(tmp_path / "fresh.db")}
            ),
        })
        engine = create_engine_factory(settings)
        bootstrap(engine, settings, seed=42)
        with engine.connect() as conn:
            names = [
                row[1] for row in conn.execute(
                    text("PRAGMA table_info(characters)"))
            ]
            assert "outfit" in names

    def test_heal_adds_column_to_pre_205_db(self, tmp_path):
        settings = SETTINGS.model_copy(update={
            "persistence": SETTINGS.persistence.model_copy(
                update={"db_path": str(tmp_path / "legacy.db")}
            ),
        })
        engine = create_engine_factory(settings)
        bootstrap(engine, settings, seed=42)
        from sqlalchemy.orm import Session

        from app.world.seed_world import seed_world

        with Session(engine) as session:
            seed_world(session, settings, settings.world.world_id)
            from app.db.models import Location

            location_id = (
                session.query(Location).order_by(Location.id).first().id
            )
            session.add(Character(
                id="w205_legacy", world_id=settings.world.world_id,
                first_name="Легионда", last_name="Старая",
                birth_date="1990-01-01", age=36, sex="F", alive=True,
                location_id=location_id, created_at=0, updated_at=0,
            ))
            session.commit()
        # simulate the pre-#205 schema
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE characters DROP COLUMN outfit"))
        with engine.connect() as conn:
            names = [
                row[1] for row in conn.execute(
                    text("PRAGMA table_info(characters)"))
            ]
            assert "outfit" not in names
        # heal (twice: idempotent) and verify
        ensure_outfit_column(engine)
        ensure_outfit_column(engine)
        with engine.connect() as conn:
            names = [
                row[1] for row in conn.execute(
                    text("PRAGMA table_info(characters)"))
            ]
            assert "outfit" in names
        # legacy row reads as "auto" through the resolver (NULL or 'auto').
        with Session(engine) as session:
            from app.visual.descriptor import _effective_outfit_mode

            legacy = session.get(Character, "w205_legacy")
            assert _effective_outfit_mode(session, legacy) == "auto"

    def test_schema_meta_version_unchanged(self, tmp_path):
        """SchemaMeta stays 0.15.0 (pinned in m2/m4/m8/m186 — no bump)."""
        settings = SETTINGS.model_copy(update={
            "persistence": SETTINGS.persistence.model_copy(
                update={"db_path": str(tmp_path / "meta.db")}
            ),
        })
        engine = create_engine_factory(settings)
        bootstrap(engine, settings, seed=42)
        from sqlalchemy.orm import Session

        with Session(engine) as session:
            from app.db.models import SchemaMeta

            version = (
                session.query(SchemaMeta).filter_by(key="version").one().value
            )
        assert version == "0.15.0"
