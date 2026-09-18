"""M8 Chunk B tests (SPEC 008-alpha, T10): admin API — overview, world, users, audit."""
import os
import sys

import pytest
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from fastapi.testclient import TestClient  # noqa: E402

from app.api.app import create_app  # noqa: E402
from app.api.auth import hash_password  # noqa: E402
from app.config.config import load_config  # noqa: E402
from app.db.models import (  # noqa: E402
    CharacterTask,
    User,
    bootstrap,
    create_engine_factory,
)
from app.world.seed_world import seed_world  # noqa: E402

SETTINGS = None


def setup_module(module):
    global SETTINGS
    SETTINGS = load_config("config/default.yaml")


def make_settings(tmp_path, **admin_updates):
    return SETTINGS.model_copy(update={
        "persistence": SETTINGS.persistence.model_copy(
            update={"db_path": str(tmp_path / "w.db")}
        ),
        "admin": SETTINGS.admin.model_copy(
            update={"rate_limit_enabled": False, **admin_updates}
        ),
    })


@pytest.fixture()
def world(tmp_path):
    """App + world + users of three roles + one player with a task."""
    settings = make_settings(tmp_path)
    engine = create_engine_factory(settings)

    with sessionmaker(bind=engine)() as session:
        bootstrap(engine, settings, seed=42)
        seed_world(session, settings, settings.world.world_id)
        import random

        from app.characters.generator import generate_population as gen
        gen(session, settings, random.Random(42), settings.world.world_id, 20)
        for username, role in [("boss", "admin"), ("helper", "moderator"),
                               ("pleb", "user")]:
            session.add(User(
                username=username, email=f"{username}@x.com",
                password_hash=hash_password("password123"),
                role=role, age_confirmed=True, created_at=0,
            ))
        session.commit()

    factory = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app(settings, factory)

    def client_for(username):
        c = TestClient(create_app(settings, factory))
        r = c.post("/auth/login", json={"username": username, "password": "password123"})
        assert r.status_code == 200, r.text
        return c

    # player + active task for cancel/teleport tests
    pc = client_for("pleb")
    r = pc.post("/characters", json={"name": "Victim", "sex": "M", "age": 30})
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    pc.post(f"/characters/{cid}/control", json={"mode": "DIRECT"})
    r = pc.post("/actions", json={
        "character_id": cid, "action_type": "WORK", "params": {},
    })
    assert r.status_code == 201, r.text
    task_id = r.json()["task_id"]

    return settings, factory, app, client_for, cid, task_id


class TestGuards:
    def test_non_admin_403(self, world):
        settings, factory, app, client_for, cid, task_id = world
        pleb = client_for("pleb")
        for method, url in [
            ("GET", "/admin/overview"), ("POST", "/admin/world/pause"),
            ("GET", "/admin/audit"),
        ]:
            r = pleb.request(method, url)
            assert r.status_code == 403, (url, r.status_code)

    def test_moderator_read_only(self, world):
        settings, factory, app, client_for, cid, task_id = world
        helper = client_for("helper")
        assert helper.get("/admin/overview").status_code == 200
        assert helper.get("/admin/audit").status_code == 200
        r = helper.post("/admin/world/pause")
        assert r.status_code == 403  # actions are admin-only

    def test_admin_full_access(self, world):
        _, _, _, client_for, *_ = world
        boss = client_for("boss")
        assert boss.get("/admin/overview").status_code == 200


class TestWorldActions:
    def test_pause_resume_timescale(self, world):
        settings, factory, app, client_for, *_ = world
        boss = client_for("boss")
        assert boss.post("/admin/world/pause").json() == {"is_paused": True}
        r = boss.post("/admin/world/timescale", json={"time_scale": 2.5})
        assert r.status_code == 200
        assert boss.post("/admin/world/resume").json() == {"is_paused": False}

        with factory() as session:
            from app.db.models import WorldClock

            clock = session.get(WorldClock, settings.world.world_id)
            assert clock.time_scale == 2.5 and not clock.is_paused

        # audit trail
        actions = [a["action"] for a in boss.get("/admin/audit").json()]
        assert {"pause_world", "resume_world", "set_timescale"} <= set(actions)

    def test_timescale_invalid(self, world):
        _, _, _, client_for, *_ = world
        boss = client_for("boss")
        r = boss.post("/admin/world/timescale", json={"time_scale": -1})
        assert r.status_code == 422


class TestCharacterActions:
    def test_teleport(self, world):
        settings, factory, app, client_for, cid, _ = world
        from sqlalchemy import select

        from app.db.models import Character, Location

        boss = client_for("boss")
        with factory() as session:
            loc_id = session.scalars(select(Location.id).order_by(Location.id)).first()
        r = boss.post(f"/admin/characters/{cid}/teleport", json={"location_id": loc_id})
        assert r.status_code == 200, r.text
        with factory() as session:
            assert session.get(Character, cid).location_id == loc_id

    def test_teleport_unknown_location(self, world):
        _, _, _, client_for, cid, _ = world
        boss = client_for("boss")
        r = boss.post(f"/admin/characters/{cid}/teleport", json={"location_id": 999999})
        assert r.status_code == 422

    def test_cancel_task(self, world):
        settings, factory, app, client_for, cid, task_id = world
        boss = client_for("boss")
        r = boss.post(f"/admin/tasks/{task_id}/cancel")
        assert r.status_code == 200
        assert r.json()["status"] == "cancelled"
        with factory() as session:
            task = session.get(CharacterTask, int(task_id))
            assert task.status == "cancelled"
        # terminal -> 409 on repeat
        assert boss.post(f"/admin/tasks/{task_id}/cancel").status_code == 409

    def test_cancel_missing_task_404(self, world):
        _, _, _, client_for, *_ = world
        boss = client_for("boss")
        assert boss.post("/admin/tasks/999999/cancel").status_code == 404


class TestUserModeration:
    def test_disable_enable(self, world):
        settings, factory, app, client_for, cid, task_id = world
        boss = client_for("boss")
        with factory() as session:
            pleb_id = session.query(User).filter_by(username="pleb").one().id

        # login while account is healthy (token persists in cookie jar)
        pleb = client_for("pleb")
        assert pleb.get("/auth/me").status_code == 200

        r = boss.post(f"/admin/users/{pleb_id}/disable")
        assert r.status_code == 200

        # banned: existing token rejected, fresh login blocked, live tasks cancelled
        assert pleb.get("/auth/me").status_code == 403
        r = pleb.post("/auth/login", json={"username": "pleb", "password": "password123"})
        assert r.status_code == 403
        with factory() as session:
            assert session.get(CharacterTask, int(task_id)).status == "cancelled"

        r = boss.post(f"/admin/users/{pleb_id}/enable")
        assert r.status_code == 200
        # after re-enable, login works
        pleb2 = client_for("pleb")
        assert pleb2.get("/auth/me").status_code == 200

    def test_cannot_disable_self(self, world):
        settings, factory, app, client_for, *_ = world
        boss = client_for("boss")
        with factory() as session:
            boss_id = session.query(User).filter_by(username="boss").one().id
        assert boss.post(f"/admin/users/{boss_id}/disable").status_code == 422

    def test_missing_user_404(self, world):
        _, _, _, client_for, *_ = world
        boss = client_for("boss")
        assert boss.post("/admin/users/999999/disable").status_code == 404


class TestOverview:
    def test_fields(self, world):
        settings, factory, app, client_for, cid, _ = world
        boss = client_for("boss")
        data = boss.get("/admin/overview").json()
        assert data["users"] >= 3
        assert data["players"] == 1
        assert data["npcs"] == 20
        assert data["active_tasks"] == 1
        assert data["world_clock"]["is_paused"] is False
        assert data["schema_version"] == "0.8.0"
        assert "uptime_sec" in data and "ws_connections" in data


class TestInvariant:
    def test_admin_integrity(self, world):
        settings, factory, app, client_for, cid, task_id = world
        boss = client_for("boss")
        boss.post("/admin/world/pause")

        from app.simulation.invariants import run_invariant_checks

        with factory() as session:
            results = run_invariant_checks(session, settings.world.world_id, settings)
        admin = next(r for r in results if r["name"] == "admin_integrity")
        assert admin["ok"], admin["details"]
