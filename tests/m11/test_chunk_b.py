"""M11 Chunk B tests (SPEC 011-fire, T6): fire API + audit."""
import os
import sys

import pytest
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from fastapi.testclient import TestClient  # noqa: E402

from app.api.auth import hash_password  # noqa: E402
from app.config.config import load_config  # noqa: E402
from app.db.models import (  # noqa: E402
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


def make_settings(tmp_path, **fire_updates):
    return SETTINGS.model_copy(update={
        "persistence": SETTINGS.persistence.model_copy(
            update={"db_path": str(tmp_path / "w.db")}
        ),
        "fire": SETTINGS.fire.model_copy(update=fire_updates),
    })


@pytest.fixture()
def env(tmp_path):
    settings = make_settings(tmp_path, burnout_days=2)
    engine = create_engine_factory(settings)
    with sessionmaker(bind=engine)() as session:
        bootstrap(engine, settings, seed=42)
        seed_world(session, settings, settings.world.world_id)
        for username, role in [
            ("boss", "admin"), ("mod", "moderator"), ("pleb", "player"),
        ]:
            session.add(User(
                username=username, email=f"{username}@x.com",
                password_hash=hash_password("password123"),
                role=role, age_confirmed=True, created_at=0,
            ))
        session.commit()
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    from app.api.app import create_app

    def client_for(username):
        c = TestClient(create_app(settings, factory))
        c.post("/auth/login", json={
            "username": username, "password": "password123"})
        return c

    return settings, factory, client_for


class TestIgniteAPI:
    def test_role_gating(self, env):
        settings, factory, client_for = env
        pleb, mod = client_for("pleb"), client_for("mod")
        assert pleb.post(
            "/admin/fire/ignite", json={"object_id": 1}).status_code == 403
        assert mod.post(
            "/admin/fire/ignite", json={"object_id": 1}).status_code == 200

    def test_ignite_and_active(self, env):
        settings, factory, client_for = env
        boss = client_for("boss")
        with factory() as session:
            obj = (
                session.query(WorldObject)
                .filter_by(world_id=settings.world.world_id)
                .first()
            )
            obj.flammability = 0.9
            object_id = obj.id
            session.commit()
        r = boss.post("/admin/fire/ignite", json={"object_id": object_id})
        assert r.status_code == 200
        active = boss.get("/fire/active").json()
        assert active[0]["object_id"] == object_id
        assert active[0]["days_burning"] == 0
        # unknown object
        assert boss.post(
            "/admin/fire/ignite", json={"object_id": 999999}
        ).status_code == 404

    def test_ignite_audited(self, env):
        settings, factory, client_for = env
        boss = client_for("boss")
        with factory() as session:
            obj = (
                session.query(WorldObject)
                .filter_by(world_id=settings.world.world_id)
                .first()
            )
            object_id = obj.id
            session.commit()
        boss.post("/admin/fire/ignite", json={"object_id": object_id})
        audit = boss.get("/admin/audit").json()
        assert audit[0]["action"] == "fire_ignite"

    def test_unauthenticated_401(self, env):
        settings, factory, client_for = env
        from app.api.app import create_app

        fresh = TestClient(create_app(settings, factory))
        assert fresh.get("/fire/active").status_code == 401


class TestStructuralPins:
    def test_event_types_30(self):
        from app.events.events import EventType

        assert len(EventType) == 30

    def test_fire_events_exist(self, env):
        settings, factory, client_for = env
        boss = client_for("boss")
        with factory() as session:
            obj = (
                session.query(WorldObject)
                .filter_by(world_id=settings.world.world_id)
                .first()
            )
            object_id = obj.id
            session.commit()
        boss.post("/admin/fire/ignite", json={"object_id": object_id})
        with factory() as session:
            types = {
                r[0] for r in session.query(WorldEvent.event_type).all()
            }
        assert "OBJECT_BURNING" in types
