"""M18 tests (SPEC 018): clothing /wear + portrait wearing (§28, AE4)."""
import os
import sys

from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from app.api.auth import hash_password  # noqa: E402
from app.config.config import load_config  # noqa: E402
from app.db.models import (  # noqa: E402
    Character,
    User,
    WorldObject,
    bootstrap,
    create_engine_factory,
)
from app.world.seed_world import seed_world  # noqa: E402

SETTINGS = None


def setup_module(module):
    global SETTINGS
    SETTINGS = load_config("config/default.yaml")


def make_settings(tmp_path):
    from pathlib import Path

    tmp_path = Path(tmp_path)
    return SETTINGS.model_copy(update={
        "persistence": SETTINGS.persistence.model_copy(
            update={"db_path": str(tmp_path / "w.db")}
        ),
    })


def build(settings):
    import random as _r

    from app.characters.generator import generate_population

    engine = create_engine_factory(settings)
    with sessionmaker(bind=engine)() as session:
        bootstrap(engine, settings, seed=42)
        seed_world(session, settings, settings.world.world_id)
        generate_population(
            session, settings, _r.Random(42),
            settings.world.world_id, 20,
        )
        session.commit()
    return engine


class TestClothing:
    def _player(self, session, settings, username="odezhda"):
        from app.characters.player import create_player_character

        user = session.query(User).filter_by(username=username).one()
        return create_player_character(
            session, settings, settings.world.world_id,
            user, "Одежда Тест", "M", 30, 0,
        )

    def test_wear_toggle_and_portrait(self, tmp_path):
        settings = make_settings(tmp_path)
        engine = build(settings)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            session.add(User(
                username="odezhda", email="o@x.com",
                password_hash=hash_password("password123"),
                role="player", age_confirmed=True, created_at=0,
            ))
            session.commit()
            char = self._player(session, settings)
            session.commit()
            npc = (
                session.query(Character)
                .filter_by(world_id=settings.world.world_id)
                .filter(Character.user_id.is_(None))
                .first()
            )
            session.add(WorldObject(
                world_id=settings.world.world_id,
                location_id=char.location_id,
                owner_character_id=char.id,
                object_type="jacket", quantity=1,
                condition=100, object_metadata="{}",
            ))
            session.commit()
            jacket_id = (
                session.query(WorldObject)
                .filter_by(owner_character_id=char.id,
                           object_type="jacket").one().id
            )
            npc_id = npc.id
        from app.api.app import create_app

        client = TestClient(create_app(settings, factory))
        client.post("/auth/login", json={
            "username": "odezhda", "password": "password123"})

        # baseline portrait: byte-identical, no wearing fragment
        from app.visual.descriptor import (
            build_portrait_descriptor,
            build_prompt,
        )
        with factory() as session:
            base = build_prompt(build_portrait_descriptor(
                session, settings.world.world_id, npc_id))
        assert "wearing" not in base

        # wear on
        r = client.post("/wear", json={"object_id": jacket_id})
        assert r.status_code == 200, r.text
        assert r.json() == {"object_id": jacket_id,
                            "slot": "upper_body", "worn": True}
        with factory() as session:
            prompt = build_prompt(build_portrait_descriptor(
                session, settings.world.world_id, char.id))
        assert "wearing: ['jacket on upper_body']" in prompt
        with factory() as session:
            obj = session.get(WorldObject, jacket_id)
            meta = __import__("json").loads(obj.object_metadata)
            assert meta == {"slot": "upper_body", "worn": True}

        # wear off
        r = client.post("/wear", json={"object_id": jacket_id})
        assert r.json()["worn"] is False
        with factory() as session:
            prompt_off = build_prompt(build_portrait_descriptor(
                session, settings.world.world_id, char.id))
        assert "wearing" not in prompt_off

    def test_foreign_and_non_wearable(self, tmp_path):
        settings = make_settings(tmp_path)
        engine = build(settings)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            session.add(User(
                username="odezhda", email="o@x.com",
                password_hash=hash_password("password123"),
                role="player", age_confirmed=True, created_at=0,
            ))
            session.commit()
            char = self._player(session, settings)
            session.commit()
            npc = (
                session.query(Character)
                .filter_by(world_id=settings.world.world_id)
                .filter(Character.user_id.is_(None))
                .first()
            )
            session.add(WorldObject(
                world_id=settings.world.world_id,
                location_id=npc.location_id,
                owner_character_id=npc.id,
                object_type="hat", quantity=1,
                condition=100, object_metadata="{}",
            ))
            session.add(WorldObject(
                world_id=settings.world.world_id,
                location_id=char.location_id,
                owner_character_id=char.id,
                object_type="food_bread", quantity=2,
                condition=100, object_metadata="{}",
            ))
            session.commit()
            hat_id = (
                session.query(WorldObject)
                .filter_by(owner_character_id=npc.id,
                           object_type="hat").one().id
            )
            bread_id = (
                session.query(WorldObject)
                .filter_by(owner_character_id=char.id,
                           object_type="food_bread").one().id
            )
        from app.api.app import create_app

        client = TestClient(create_app(settings, factory))
        client.post("/auth/login", json={
            "username": "odezhda", "password": "password123"})
        assert client.post(
            "/wear", json={"object_id": hat_id}).status_code == 404
        assert client.post(
            "/wear", json={"object_id": bread_id}).status_code == 422
