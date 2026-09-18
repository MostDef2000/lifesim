"""M9 Chunk C tests (SPEC 009-web, T10): admin tab smoke + external form data."""
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
        "admin": SETTINGS.admin.model_copy(update={"rate_limit_enabled": False}),
    })


@pytest.fixture()
def admin_env(tmp_path):
    settings = make_settings(tmp_path)
    engine = create_engine_factory(settings)
    with sessionmaker(bind=engine)() as session:
        bootstrap(engine, settings, seed=42)
        seed_world(session, settings, settings.world.world_id)
        session.add(User(
            username="root", email="root@x.com",
            password_hash=hash_password("password123"),
            role="admin", age_confirmed=True, created_at=0,
        ))
        session.commit()
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    from app.api.app import create_app

    client = TestClient(create_app(settings, factory))
    client.post("/auth/login", json={"username": "root", "password": "password123"})
    return client


class TestAdminScreen:
    """AE6'''''': the admin tab's data source works for admin role."""

    def test_overview_fields_for_admin(self, admin_env):
        client = admin_env
        data = client.get("/admin/overview").json()
        for key in ["users", "players", "npcs", "active_tasks",
                    "world_clock", "schema_version", "ws_connections"]:
            assert key in data, key
        assert data["users"] == 1

    def test_pause_resume_roundtrip(self, admin_env):
        client = admin_env
        assert client.post("/admin/world/pause").status_code == 200
        ov = client.get("/admin/overview").json()
        assert ov["world_clock"]["is_paused"] is True
        assert client.post("/admin/world/resume").status_code == 200

    def test_audit_empty_then_actions(self, admin_env):
        client = admin_env
        assert client.get("/admin/audit").json() == []
        client.post("/admin/world/pause")
        audit = client.get("/admin/audit").json()
        assert audit[0]["action"] == "pause_world"


class TestExternalFormData:
    """R10: the external travel form source (GET /external) works."""

    def test_catalog_shape(self, tmp_path):
        settings = make_settings(tmp_path)
        engine = create_engine_factory(settings)
        with sessionmaker(bind=engine)() as session:
            bootstrap(engine, settings, seed=42)
            seed_world(session, settings, settings.world.world_id)
            session.commit()
        from app.api.app import create_app
        from app.world.seed_external import seed_external_world

        client = TestClient(create_app(settings, sessionmaker(bind=engine)))
        client.post("/auth/register", json={
            "username": "trav", "email": "t@x.com",
            "password": "password123", "age_confirmed": True,
        })
        with sessionmaker(bind=engine)() as session:
            # players must exist before seeding contacts
            import random as _r

            from app.characters.generator import generate_population

            generate_population(
                session, settings, _r.Random(42),
                settings.world.world_id, 20,
            )
            seed_external_world(session, settings, settings.world.world_id, _r.Random(7))
            session.commit()
        client.post("/auth/login", json={"username": "trav", "password": "password123"})
        catalog = client.get("/external").json()
        assert len(catalog) >= 3
        svc = catalog[0]["services"][0]
        assert {"id", "service_type"} <= set(svc.keys())
