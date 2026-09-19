"""M14 Chunk A tests (SPEC 014-player-ux, T2): looks/biography at creation."""
import os
import sys

import pytest
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from fastapi.testclient import TestClient  # noqa: E402

from app.config.config import load_config  # noqa: E402
from app.db.models import (  # noqa: E402
    Character,
    bootstrap,
    create_engine_factory,
)
from app.world.seed_world import seed_world  # noqa: E402

SETTINGS = None


def setup_module(module):
    global SETTINGS
    SETTINGS = load_config("config/default.yaml")


def make_settings(tmp_path):
    return SETTINGS.model_copy(update={
        "persistence": SETTINGS.persistence.model_copy(
            update={"db_path": str(tmp_path / "w.db")}
        ),
    })


def build(settings, population=False):
    engine = create_engine_factory(settings)
    with sessionmaker(bind=engine)() as session:
        bootstrap(engine, settings, seed=42)
        seed_world(session, settings, settings.world.world_id)
        if population:
            import random as _r

            from app.characters.generator import generate_population

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
    from app.api.app import create_app

    client = TestClient(create_app(settings, factory))
    r = client.post("/auth/register", json={
        "username": "bio14", "email": "b@x.com",
        "password": "password123", "age_confirmed": True,
    })
    assert r.status_code in (200, 201), r.text
    client.post("/auth/login", json={
        "username": "bio14", "password": "password123"})
    return settings, factory, client


class TestBioLooks:
    def test_create_with_bio_looks(self, env):
        settings, factory, client = env
        r = client.post("/characters", json={
            "name": "Био Тест", "sex": "F", "age": 30,
            "looks": "Рыжая, веснушки",
            "biography": "Родилась во Владивостоке, переехала на Рейнеке.",
        })
        assert r.status_code == 201, r.text
        assert r.json()["looks"] == "Рыжая, веснушки"
        with factory() as session:
            from app.db.models import User

            user = session.query(User).filter_by(username="bio14").one()
            char = session.query(Character).filter_by(
                user_id=user.id).one()
            assert char.looks == "Рыжая, веснушки"
            assert "Владивостоке" in char.biography
            # by-user endpoint exposes both
            rows = client.get(
                f"/characters/by-user/{user.id}").json()
            assert rows[0]["looks"] == "Рыжая, веснушки"
            assert "Рейнеке" in rows[0]["biography"]

    def test_optional_empty(self, env):
        settings, factory, client = env
        r = client.post("/characters", json={
            "name": "Молчун Тест", "sex": "M", "age": 40})
        assert r.status_code == 201, r.text
        assert r.json()["looks"] is None
        assert r.json()["biography"] is None

    def test_limits_422(self, env):
        settings, factory, client = env
        r = client.post("/characters", json={
            "name": "Длинный Тест", "sex": "M", "age": 30,
            "looks": "x" * 501,
        })
        assert r.status_code == 422
        r2 = client.post("/characters", json={
            "name": "Длинный2 Тест", "sex": "M", "age": 30,
            "biography": "y" * 2001,
        })
        assert r2.status_code == 422

    def test_whitespace_stripped_to_null(self, env):
        settings, factory, client = env
        r = client.post("/characters", json={
            "name": "Пробел Тест", "sex": "M", "age": 30,
            "looks": "   ", "biography": "\t",
        })
        assert r.status_code == 201, r.text
        assert r.json()["looks"] is None
        assert r.json()["biography"] is None

    def test_keystone_vectors_unchanged(self, tmp_path):
        # AE2: nullable columns — default seed leaves them NULL; no events
        settings = make_settings(tmp_path)
        engine = build(settings, population=True)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            npcs = session.query(Character).filter(
                Character.user_id.is_(None)).all()
            assert len(npcs) > 0
            assert all(c.looks is None for c in npcs)
            assert all(c.biography is None for c in npcs)
