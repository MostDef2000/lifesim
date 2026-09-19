"""M12 Chunk B tests (SPEC 012-market, T5): construction, destruction, API."""
import json
import os
import sys

import pytest
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from fastapi.testclient import TestClient  # noqa: E402

from app.api.auth import hash_password  # noqa: E402
from app.config.config import load_config  # noqa: E402
from app.db.models import (  # noqa: E402
    Account,
    Character,
    CharacterTask,
    User,
    WorldEvent,
    WorldObject,
    bootstrap,
    create_engine_factory,
)
from app.world.seed_world import seed_world  # noqa: E402

SETTINGS = None


def setup_module(module):
    global SETTINGS
    SETTINGS = load_config("config/default.yaml")


def make_settings(tmp_path, **kw):
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


@pytest.fixture()
def env(tmp_path):
    settings = make_settings(tmp_path)
    engine = build(settings)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        session.add(User(
            username="mkt", email="m@x.com",
            password_hash=hash_password("password123"),
            role="player", age_confirmed=True, created_at=0,
        ))
        session.commit()
    from app.api.app import create_app

    client = TestClient(create_app(settings, factory))
    client.post("/auth/login", json={
        "username": "mkt", "password": "password123"})
    return settings, factory, client


class TestConstruction:
    def test_construct_task_creates_object(self, tmp_path):
        from app.actions.lifecycle import complete_task

        settings = make_settings(tmp_path)
        engine = build(settings)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            char = session.query(Character).filter_by(
                world_id=settings.world.world_id).first()
            # resources: 2 wood_piles owned by char
            from app.inventory import create_object

            for _ in range(2):
                create_object(
                    session, settings.world.world_id, "wood_pile",
                    location_id=char.location_id, quantity=1,
                    owner_character_id=char.id,
                )
            task = CharacterTask(
                character_id=char.id, priority=5, task_type="CONSTRUCT",
                status="active", source="player",
                parameters=json.dumps({
                    "object_type": "shed",
                    "required_items": {"wood_pile": 2},
                }),
                created_at=0, started_at=0, ends_at=2880,
            )
            session.add(task)
            session.flush()
            complete_task(session, settings.world.world_id, char, task,
                          2880, settings)
            session.commit()
            shed = session.query(WorldObject).filter_by(
                world_id=settings.world.world_id, object_type="shed",
                owner_character_id=char.id).first()
            assert shed is not None
            assert shed.quantity == 1
            ev = session.query(WorldEvent).filter_by(
                event_type="CONSTRUCTED").one()
            assert json.loads(ev.payload)["object_id"] == shed.id
            # resources consumed
            left = session.query(WorldObject).filter_by(
                owner_character_id=char.id, object_type="wood_pile").count()
            assert left == 0

    def test_missing_resources_fails(self, tmp_path):
        from app.actions.lifecycle import complete_task

        settings = make_settings(tmp_path)
        engine = build(settings)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            char = session.query(Character).filter_by(
                world_id=settings.world.world_id).first()
            task = CharacterTask(
                character_id=char.id, priority=5, task_type="CONSTRUCT",
                status="active", source="player",
                parameters=json.dumps({
                    "object_type": "shed",
                    "required_items": {"wood_pile": 2},
                }),
                created_at=0, started_at=0, ends_at=2880,
            )
            session.add(task)
            session.flush()
            complete_task(session, settings.world.world_id, char, task,
                          2880, settings)
            session.commit()
            assert session.get(CharacterTask, task.id).status == "failed"
            assert session.query(WorldObject).filter_by(
                object_type="shed").count() == 0


class TestDestruction:
    def test_condition_zero_destroys(self, tmp_path):
        from app.simulation.fire import run_fire_phase

        settings = make_settings(tmp_path)
        engine = build(settings)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            obj = (
                session.query(WorldObject)
                .filter_by(world_id=settings.world.world_id)
                .first()
            )
            obj.condition = 0
            object_id = obj.id
            session.commit()
            run_fire_phase(session, settings.world.world_id, 1, settings)
            destroyed = session.get(WorldObject, object_id)
            assert destroyed.quantity == 0
            ev = session.query(WorldEvent).filter_by(
                event_type="OBJECT_DESTROYED").one()
            assert json.loads(ev.payload)["object_id"] == object_id


class TestMarketAPI:
    def test_offers_flow(self, env):
        settings, factory, client = env
        with factory() as session:
            # player character via registration flow is absent; create one
            from app.characters.player import create_player_character

            user = session.query(User).filter_by(username="mkt").one()
            char = create_player_character(
                session, settings, settings.world.world_id,
                user, "Рынок Тест", "M", 30, 0,
            )
            acc = session.query(Account).filter_by(
                owner_type="character", owner_id=char.id).first()
            if acc is None:
                session.add(Account(
                    id=f"acc_{char.id}", world_id=settings.world.world_id,
                    owner_type="character", owner_id=char.id, balance=300,
                    created_at=0,
                ))
            else:
                acc.balance = 300
            from app.inventory import create_object

            item = create_object(
                session, settings.world.world_id, "cloth",
                location_id=char.location_id, quantity=1,
                owner_character_id=char.id,
            )
            session.commit()
            object_id = item.id

        r = client.post(
            "/market/offers", json={"object_id": object_id, "price": 50})
        assert r.status_code == 200, r.text
        offers = client.get("/market/offers").json()
        assert any(o["object_id"] == object_id for o in offers)

    def test_build_endpoint(self, env):
        settings, factory, client = env
        with factory() as session:
            from app.characters.player import create_player_character

            user = session.query(User).filter_by(username="mkt").one()
            create_player_character(
                session, settings, settings.world.world_id,
                user, "Строитель Тест", "M", 30, 0,
            )
            session.commit()
        r = client.post("/build", json={"object_type": "shed"})
        assert r.status_code == 200, r.text
        assert r.json()["ends_at"] >= 2880
        # unknown blueprint
        r2 = client.post("/build", json={"object_type": "castle"})
        assert r2.status_code == 422

    def test_requires_auth(self, env):
        settings, factory, _ = env
        from app.api.app import create_app

        fresh = TestClient(create_app(settings, factory))
        assert fresh.get("/market/offers").status_code == 401
        assert fresh.post("/build", json={}).status_code == 401
