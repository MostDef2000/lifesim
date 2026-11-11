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
from tests.helpers import auth_client, make_user  # noqa: E402

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


# #309: TestRegistrationControls deleted — local /auth/register is gone
# (404). The max_players cap now lives in the OIDC callback
# (oidc.py "registration_closed"); registration_enabled no longer exists.
# test_disabled_login_403 deleted — /auth/login is now the OIDC redirect
# start (GET); the disabled check is covered by test_disabled_token_rejected.


class TestDisabledAccounts:
    def test_disabled_token_rejected(self, tmp_path):
        settings = make_settings(tmp_path)
        engine = build(settings)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            user = make_user(session, "convict", "c@x.com")
        client = auth_client(create_app(settings, factory), settings, user)
        assert client.get("/auth/me").status_code == 200

        with factory() as session:
            u = session.query(User).filter_by(username="convict").one()
            u.disabled = True
            session.commit()
        # new app instance (same token cookie)
        client2 = auth_client(create_app(settings, factory), settings, user)
        r = client2.get("/auth/me")
        assert r.status_code == 403


class TestRateLimit:
    def test_auth_rate_limit_429(self, tmp_path):
        settings = make_settings(tmp_path, auth_rpm=3, rate_limit_window_sec=60)
        build(settings)
        client = TestClient(
            create_app(settings, sessionmaker(bind=create_engine_factory(settings)))
        )
        # #309: strict bucket now guards the OIDC endpoints; /auth/callback
        # with no params is a network-free 302 fail-redirect.
        statuses = []
        for i in range(5):
            r = client.get("/auth/callback", follow_redirects=False)
            statuses.append(r.status_code)
        assert statuses[:3].count(302) == 3  # within limit: normal responses
        assert statuses[3] == 429 and statuses[4] == 429
        assert client.get(
            "/auth/callback", follow_redirects=False
        ).headers.get("retry-after") is not None

    def test_xff_last_entry_single_bucket(self, tmp_path):
        # #309 ИБ-этап: behind Caddy the proxy APPENDS the real client IP
        # to any client-supplied X-Forwarded-For, so only the LAST entry is
        # trustworthy. A client rotating fake FIRST entries must all land
        # in ONE bucket (same real IP) and hit 429, not get a fresh bucket
        # per request.
        settings = make_settings(tmp_path, auth_rpm=3, rate_limit_window_sec=60)
        build(settings)
        client = TestClient(
            create_app(settings, sessionmaker(bind=create_engine_factory(settings)))
        )
        statuses = []
        for i in range(5):
            r = client.get(
                "/auth/callback",
                headers={"X-Forwarded-For": f"10.0.0.{i}, 203.0.113.7"},
                follow_redirects=False,
            )
            statuses.append(r.status_code)
        assert statuses[:3].count(302) == 3  # within limit: normal responses
        assert statuses[3] == 429 and statuses[4] == 429

    def test_xff_different_last_entries_separate_buckets(self, tmp_path):
        # #309 ИБ-этап: inverse — distinct LAST entries are distinct
        # clients and must not share a bucket (no premature 429).
        settings = make_settings(tmp_path, auth_rpm=3, rate_limit_window_sec=60)
        build(settings)
        client = TestClient(
            create_app(settings, sessionmaker(bind=create_engine_factory(settings)))
        )
        statuses = []
        for i in range(3):
            r = client.get(
                "/auth/callback",
                headers={"X-Forwarded-For": f"10.0.0.{i}, 203.0.113.7"},
                follow_redirects=False,
            )
            statuses.append(r.status_code)
        for i in range(3):
            r = client.get(
                "/auth/callback",
                headers={"X-Forwarded-For": f"10.9.0.{i}, 203.0.113.8"},
                follow_redirects=False,
            )
            statuses.append(r.status_code)
        assert all(s == 302 for s in statuses)

    def test_xff_multiple_header_lines_last_line_wins(self, tmp_path):
        # #309 ИБ-этап: a client can send SEVERAL X-Forwarded-For header
        # lines; headers.get() reads only the first line, so rotating fakes
        # in repeated lines must not restore the bypass. All lines are
        # joined and the LAST entry of the joined list (Caddy's appended
        # real IP, "203.0.113.9") must key the bucket.
        settings = make_settings(tmp_path, auth_rpm=3, rate_limit_window_sec=60)
        build(settings)
        client = TestClient(
            create_app(settings, sessionmaker(bind=create_engine_factory(settings)))
        )
        statuses = []
        for i in range(5):
            r = client.get(
                "/auth/callback",
                headers=[
                    ("X-Forwarded-For", f"10.0.0.{i}"),
                    ("X-Forwarded-For", "203.0.113.9"),
                ],
                follow_redirects=False,
            )
            statuses.append(r.status_code)
        assert statuses[:3].count(302) == 3  # within limit: normal responses
        assert statuses[3] == 429 and statuses[4] == 429

    def test_rate_limit_disabled(self, tmp_path):
        settings = make_settings(tmp_path, rate_limit_enabled=False, auth_rpm=1)
        build(settings)
        client = TestClient(
            create_app(settings, sessionmaker(bind=create_engine_factory(settings)))
        )
        statuses = [
            client.get("/auth/callback", follow_redirects=False).status_code
            for i in range(4)
        ]
        assert all(s == 302 for s in statuses)


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
