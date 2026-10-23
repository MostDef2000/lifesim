"""#230 — create_scene player lookup must pick a LIVING character.

Contract under test (#230):
- the player lookup in POST /visual/scenes
  (session.query(Character).filter_by(world_id, user_id).order_by(Character.id).first())
  had NO alive filter: a user with a dead LEGACY character (lower id —
  rows predating the one-character-per-user limit) got the dead one as
  player_character_id, so the scene enriched with the dead character's
  canonical portrait / appearance instead of the living one.
- Fix semantics: add Character.alive.is_(True) — the first LIVING character
  enriches the scene. NO 404/422: a user whose only characters are dead
  gets a scene WITHOUT a player part, exactly like the documented
  no-character path (#86 A4). Aliveness precedent in this file:
  _owned_character_for_portrait (422 on !alive for the portrait path —
  this is the scene path: skip, don't fail).

Fixture notes:
- the API enforces one character per user (create_player_character MVP
  limit), so the dead LEGACY row is direct-inserted with a lower id
  ("plr_0000" < "plr_0001", lexicographic order_by(Character.id) picks it
  first on base) and user_id set.
- a dead character cannot get a portrait through the API
  (_owned_character_for_portrait 422s on !alive), so the dead canonical is
  a direct §70 VisualAsset row (m153 style); the living character's
  canonical goes through the real POST /visual/portraits + promote path.

RED discipline: on base 36c8a4e BOTH legs fail for the right reason — the
lookup returns the dead lower-id character, so the player part carries the
dead id and the dead canonical rides the references.
"""
import os
import random
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from app.config.config import load_config  # noqa: E402
from app.db.models import (  # noqa: E402
    Character,
    User,
    VisualAsset,
    bootstrap,
    create_engine_factory,
)
from app.world.seed_world import seed_world  # noqa: E402

SETTINGS = None
PW = "Correct-Horse-9!"


def setup_module(module):
    global SETTINGS
    SETTINGS = load_config("config/default.yaml")


def _make_settings(tmp_path):
    visual = SETTINGS.visual.model_copy(update={
        "enabled": True,
        "storage_dir": str(tmp_path / "assets"),
        "image_size": "8x8",
    })
    return SETTINGS.model_copy(update={
        "persistence": SETTINGS.persistence.model_copy(
            update={"db_path": str(tmp_path / "w230.db")}
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


def _create_character(client, name):
    r = client.post("/characters", json={"name": name, "sex": "F", "age": 30})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _build(tmp_path):
    from sqlalchemy.orm import sessionmaker

    from app.api.app import create_app
    from app.characters.generator import generate_population

    settings = _make_settings(tmp_path)
    engine = create_engine_factory(settings)
    with sessionmaker(bind=engine)() as session:
        bootstrap(engine, settings, seed=42)
        seed_world(session, settings, settings.world.world_id)
        generate_population(
            session, settings, random.Random(42),
            settings.world.world_id, 20,
        )
        session.commit()
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app(settings, factory)
    client = TestClient(app)
    _register_and_login(client, "m230player")
    return settings, engine, factory, app, client


def _make_canonical(client, cid):
    """Generate + promote one portrait for the caller's own character."""
    r = client.post(f"/visual/portraits/{cid}")
    assert r.status_code == 201, r.text
    asset_id = r.json()["asset"]["id"]
    r = client.post(f"/visual/assets/{asset_id}/canonical", json={"canonical": True})
    assert r.status_code == 200, r.text
    assert r.json()["canonical"] is True, r.text
    return asset_id


def _legacy_dead_character(engine, settings, loc_id):
    """Direct-insert the dead LEGACY character row (predates the
    one-character-per-user limit): player-owned (user_id set), alive=False,
    id lexicographically FIRST so the unfiltered lookup picks it."""
    from sqlalchemy.orm import sessionmaker

    with sessionmaker(bind=engine)() as session:
        user = session.query(User).filter_by(username="m230player").first()
        assert user is not None
        legacy = Character(
            id="plr_0000",
            world_id=settings.world.world_id,
            user_id=user.id,
            type="player",
            first_name="Мёртвый",
            last_name="Легаси",
            birth_date="1990-01-01",
            age=40,
            sex="F",
            alive=False,
            location_id=loc_id,
            created_at=0,
            updated_at=0,
        )
        session.add(legacy)
        session.commit()
        return legacy.id


def _legacy_canonical(engine, settings, cid):
    """Direct §70 canonical row for the dead legacy character (the portrait
    API refuses dead characters — 422 — so the row is inserted directly)."""
    from sqlalchemy.orm import sessionmaker

    with sessionmaker(bind=engine, expire_on_commit=False)() as session:
        asset = VisualAsset(
            world_id=settings.world.world_id, asset_type="portrait",
            character_id="plr_0000", scene_descriptor={}, prompt="p",
            storage_path="m230-dead-legacy", seed=1,
            model="flux.1-schnell", canonical=True, created_at="ts0",
        )
        session.add(asset)
        session.commit()
        return asset.id


def _set_alive(engine, cid, alive):
    """Flip aliveness directly (m219 _move_to mutation style)."""
    from sqlalchemy.orm import sessionmaker

    with sessionmaker(bind=engine, expire_on_commit=False)() as session:
        session.get(Character, cid).alive = alive
        session.commit()


def _user_location(engine, cid):
    from sqlalchemy.orm import sessionmaker

    with sessionmaker(bind=engine)() as session:
        return session.get(Character, cid).location_id


class TestSceneAliveLookup:
    def test_a_scene_enriches_from_living_not_dead_legacy(self, tmp_path):
        """(A) the bug: dead legacy character (lower id, canonical) + living
        character (canonical) → the scene player part and references come
        from the LIVING character; the dead canonical is excluded."""
        settings, engine, factory, app, client = _build(tmp_path)
        living_cid = _create_character(client, "Живая")
        loc = _user_location(engine, living_cid)
        legacy_cid = _legacy_dead_character(engine, settings, loc)
        legacy_asset_id = _legacy_canonical(engine, settings, legacy_cid)
        living_asset_id = _make_canonical(client, living_cid)

        r = client.post("/visual/scenes", json={"location_id": loc})
        assert r.status_code == 201, r.text
        d = r.json()["asset"]["scene_descriptor"]
        assert d["player"]["id"] == living_cid, (
            f"player enrichment must come from the LIVING character; "
            f"got player id {d['player']['id']!r} (dead legacy id "
            f"{legacy_cid!r} must not be picked)"
        )
        assert living_asset_id in d["references"], (
            f"living canonical must ride the references; got "
            f"{d['references']}"
        )
        assert legacy_asset_id not in d["references"], (
            f"dead legacy canonical {legacy_asset_id} must not ride the "
            f"scene references; got {d['references']}"
        )

    def test_b_only_dead_character_scene_still_succeeds(self, tmp_path):
        """(B) edge: user whose ONLY character is dead → the scene still
        succeeds (201, no 404/422), references carry no canonical of the
        dead character, and the player part is absent (the documented
        no-character path)."""
        settings, engine, factory, app, client = _build(tmp_path)
        cid = _create_character(client, "Погибшая")
        dead_asset_id = _make_canonical(client, cid)
        loc = _user_location(engine, cid)
        _set_alive(engine, cid, False)

        r = client.post("/visual/scenes", json={"location_id": loc})
        assert r.status_code == 201, r.text
        d = r.json()["asset"]["scene_descriptor"]
        assert d["player"] is None, (
            f"dead-only user must take the no-character path (player "
            f"absent); got player {d['player']!r}"
        )
        assert dead_asset_id not in d["references"], (
            f"dead character's canonical {dead_asset_id} must not ride the "
            f"scene references; got {d['references']}"
        )
