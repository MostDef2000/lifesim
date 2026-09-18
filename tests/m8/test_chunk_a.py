"""M8 Chunk A tests (SPEC 008-alpha, T6): schema, registration controls, middleware."""
import os
import sys

from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from fastapi.testclient import TestClient  # noqa: E402

from app.api.app import create_app  # noqa: E402
from app.config.config import load_config  # noqa: E402
from app.db.models import (  # noqa: E402
    AdminAuditLog,
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
        "admin": SETTINGS.admin.model_copy(update=admin_updates),
    })


def build(settings):
    engine = create_engine_factory(settings)
    with sessionmaker(bind=engine)() as session:
        bootstrap(engine, settings, seed=42)
        seed_world(session, settings, settings.world.world_id)
    return engine


class TestSchema:
    def test_admin_audit_table_and_disabled_column(self, tmp_path):
        settings = make_settings(tmp_path)
        engine = build(settings)
        from sqlalchemy import inspect

        inspector = inspect(engine)
        assert "admin_audit_log" in inspector.get_table_names()
        user_cols = {c["name"] for c in inspector.get_columns("users")}
        assert "disabled" in user_cols

        with sessionmaker(bind=engine)() as session:
            assert session.query(AdminAuditLog).count() == 0
            assert session.query(User).filter_by(disabled=True).count() == 0


class TestRegistrationControls:
    def test_registration_disabled_403(self, tmp_path):
        settings = make_settings(tmp_path, registration_enabled=False)
        build(settings)
        app = create_app(settings, sessionmaker(bind=create_engine_factory(settings)))
        client = TestClient(app)
        r = client.post("/auth/register", json={
            "username": "blocked", "email": "b@x.com",
            "password": "password123", "age_confirmed": True,
        })
        assert r.status_code == 403
        assert "registration disabled" in r.json()["detail"]

    def test_max_players_409(self, tmp_path):
        settings = make_settings(tmp_path, max_players=1)
        build(settings)
        app = create_app(settings, sessionmaker(bind=create_engine_factory(settings)))
        client = TestClient(app)
        r1 = client.post("/auth/register", json={
            "username": "first", "email": "f@x.com",
            "password": "password123", "age_confirmed": True,
        })
        assert r1.status_code == 201
        r2 = client.post("/auth/register", json={
            "username": "second", "email": "s@x.com",
            "password": "password123", "age_confirmed": True,
        })
        assert r2.status_code == 409
        assert "max players" in r2.json()["detail"]

    def test_unlimited_by_default(self, tmp_path):
        settings = make_settings(tmp_path)
        build(settings)
        app = create_app(settings, sessionmaker(bind=create_engine_factory(settings)))
        client = TestClient(app)
        for i in range(3):
            r = client.post("/auth/register", json={
                "username": f"user{i}", "email": f"u{i}@x.com",
                "password": "password123", "age_confirmed": True,
            })
            assert r.status_code == 201


class TestDisabledAccounts:
    def test_disabled_login_403(self, tmp_path):
        settings = make_settings(tmp_path)
        engine = build(settings)
        factory = sessionmaker(bind=engine)
        with factory() as session:
            session.add(User(
                username="banned", email="b@x.com",
                password_hash="$pbkdf2$fake", role="user",
                age_confirmed=True, disabled=True, created_at=0,
            ))
            session.commit()
        client = TestClient(create_app(settings, factory))
        r = client.post("/auth/login", json={"username": "banned", "password": "x"})
        assert r.status_code in (401, 403)  # bad hash -> 401; disabled check on valid creds

        # with valid password -> 403
        from app.api.auth import hash_password

        with factory() as session:
            u = session.query(User).filter_by(username="banned").one()
            u.password_hash = hash_password("password123")
            session.commit()
        r = client.post("/auth/login", json={"username": "banned", "password": "password123"})
        assert r.status_code == 403
        assert "account disabled" in r.json()["detail"]

    def test_disabled_token_rejected(self, tmp_path):
        settings = make_settings(tmp_path)
        engine = build(settings)
        factory = sessionmaker(bind=engine)
        from app.api.auth import hash_password

        with factory() as session:
            session.add(User(
                username="convict", email="c@x.com",
                password_hash=hash_password("password123"), role="user",
                age_confirmed=True, disabled=False, created_at=0,
            ))
            session.commit()
        client = TestClient(create_app(settings, factory))
        client.post("/auth/login", json={"username": "convict", "password": "password123"})
        assert client.get("/auth/me").status_code == 200

        with factory() as session:
            u = session.query(User).filter_by(username="convict").one()
            u.disabled = True
            session.commit()
        # new app instance (same token cookie)
        client2 = TestClient(create_app(settings, factory))
        client2.cookies.update(client.cookies)
        r = client2.get("/auth/me")
        assert r.status_code == 403


class TestRateLimit:
    def test_auth_rate_limit_429(self, tmp_path):
        settings = make_settings(tmp_path, auth_rpm=3, rate_limit_window_sec=60)
        build(settings)
        client = TestClient(
            create_app(settings, sessionmaker(bind=create_engine_factory(settings)))
        )
        statuses = []
        for i in range(5):
            r = client.post("/auth/login", json={"username": f"u{i}", "password": "x"})
            statuses.append(r.status_code)
        assert statuses[:3].count(401) == 3  # within limit: normal responses
        assert statuses[3] == 429 and statuses[4] == 429
        assert client.post("/auth/login", json={"username": "x", "password": "x"}
                           ).headers.get("retry-after") is not None

    def test_rate_limit_disabled(self, tmp_path):
        settings = make_settings(tmp_path, rate_limit_enabled=False, auth_rpm=1)
        build(settings)
        client = TestClient(
            create_app(settings, sessionmaker(bind=create_engine_factory(settings)))
        )
        statuses = [
            client.post("/auth/login", json={"username": f"u{i}", "password": "x"}).status_code
            for i in range(4)
        ]
        assert all(s == 401 for s in statuses)


class TestRequestID:
    def test_request_id_header_and_echo(self, tmp_path):
        settings = make_settings(tmp_path, rate_limit_enabled=False)
        build(settings)
        client = TestClient(
            create_app(settings, sessionmaker(bind=create_engine_factory(settings)))
        )
        r = client.get("/world")
        assert r.headers.get("x-request-id")

        r2 = client.get("/world", headers={"X-Request-ID": "my-trace-42"})
        assert r2.headers.get("x-request-id") == "my-trace-42"
