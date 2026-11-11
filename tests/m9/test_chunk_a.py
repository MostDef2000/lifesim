"""M9 Chunk A tests (SPEC 009-web, T3): static serving + API not shadowed."""
import os
import shutil
import subprocess
import sys

import pytest
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from fastapi.testclient import TestClient  # noqa: E402

from app.config.config import load_config  # noqa: E402
from app.db.models import bootstrap, create_engine_factory  # noqa: E402
from app.world.seed_world import seed_world  # noqa: E402
from tests.helpers import auth_client, make_user  # noqa: E402

SETTINGS = None


def setup_module(module):
    global SETTINGS
    SETTINGS = load_config("config/default.yaml")


@pytest.fixture()
def env(tmp_path):
    """(client, settings, factory) — factory lets tests create users."""
    settings = SETTINGS.model_copy(update={
        "persistence": SETTINGS.persistence.model_copy(
            update={"db_path": str(tmp_path / "w.db")}
        ),
        "admin": SETTINGS.admin.model_copy(update={"rate_limit_enabled": False}),
    })
    engine = create_engine_factory(settings)
    with sessionmaker(bind=engine)() as session:
        bootstrap(engine, settings, seed=42)
        seed_world(session, settings, settings.world.world_id)
        session.commit()
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    from app.api.app import create_app

    return TestClient(create_app(settings, factory)), settings, factory


@pytest.fixture()
def client(env):
    return env[0]


class TestHealth:
    def test_health_ok(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


class TestStatic:
    def test_index_served(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert "ВЛ1: Рейнеке" in r.text
        assert "живой остров" in r.text
        assert "name=\"description\"" in r.text
        assert "/static/app.js" in r.text
        assert 'content="#F5EBD9"' in r.text

    def test_static_assets(self, client):
        js = client.get("/static/app.js")
        css = client.get("/static/style.css")
        world_map = client.get("/static/static/map/world-island.jpg")
        hero = client.get("/static/static/landing/concierge-cat-hero.webp")
        assert js.status_code == 200
        assert css.status_code == 200
        assert world_map.status_code == 200
        assert hero.status_code == 200
        assert "textContent" in js.text  # XSS-hygiene primitive present
        assert "Остров живёт, даже когда тебя нет." in js.text
        # #309: the landing CTA is now the «Войти» button (→ GET /auth/login);
        # the old «Начать жизнь на Рейнеке» register-form CTA is gone.
        assert "Войти" in js.text
        assert "Кошка-консьержка" in js.text
        assert "Добро пожаловать на Рейнеке" in js.text
        assert '"/static/static/map/world-island.jpg"' in js.text
        # quoted broken URLs must not exist; quoting makes the pin unambiguous
        assert '"/static/map/world-island.jpg"' not in js.text
        assert '"/static/static/map/base.jpg"' not in js.text
        assert '"/static/map/base.jpg"' not in js.text
        # only the authenticated map card now uses the served world URL
        assert js.text.count('"/static/static/map/world-island.jpg"') == 1
        assert js.text.count('"/static/static/map/base.jpg"') == 0
        # landing hero asset (webp); the retired island map is gone
        assert '"/static/static/landing/concierge-cat-hero.webp"' in js.text
        assert '"/static/map/landing-island.jpg"' not in js.text
        assert '"/static/static/map/landing-island.jpg"' not in js.text
        # #309: local register/login forms are gone — the only auth surface
        # is the CTA href to the OIDC redirect start (GET /auth/login).
        assert '"/auth/register"' not in js.text
        assert '"/auth/login"' in js.text
        assert 'path !== "/auth/me"' in js.text
        assert "--bg" in css.text
        assert "scale(1.02)" not in css.text
        assert ".landing-shell" in css.text
        assert ".landing-scrim" in css.text
        assert "object-position" in css.text
        assert "@media (prefers-reduced-motion: reduce)" in css.text

    def test_app_js_syntax(self):
        node = shutil.which("node")
        if node is None:
            pytest.skip("node is not installed")
        app_js = os.path.abspath(os.path.join(
            os.path.dirname(__file__), "../../backend/app/web/app.js"
        ))
        subprocess.run(
            [node, "--check", app_js],
            check=True,
            capture_output=True,
            text=True,
        )

    def test_missing_static_404(self, client):
        assert client.get("/static/nope.js").status_code == 404


class TestApiNotShadowed:
    def test_api_routes_still_first(self, client):
        # /world is API JSON, not the SPA index
        r = client.get("/world")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/json")
        assert "game_timestamp" in r.json()

        # 401-protected API returns 401, not the login page
        r2 = client.get("/characters/plr_0001")
        assert r2.status_code == 401

    def test_openapi_still_json(self, client):
        r = client.get("/docs")
        assert r.status_code == 200


class TestWsToken:
    def test_me_does_not_expose_ws_token(self, env):
        # #124: /auth/me no longer mints a session-equivalent JWT for the
        # client — WS auth rides the httpOnly cookie, so the field is gone.
        client, settings, factory = env
        with factory() as session:
            user = make_user(session, "ann1", "ann1@x.com")
        client = auth_client(client.app, settings, user)
        me = client.get("/auth/me").json()
        assert "ws_token" not in me
        assert me["id"]
        assert me["username"] == "ann1"
        assert me["role"] == "user"


class TestNewEndpoints:
    def test_locations_list(self, env):
        client, settings, factory = env
        r = client.get("/locations")
        assert r.status_code == 401  # auth-gated like the rest of the API
        with factory() as session:
            user = make_user(session, "bob2", "bob2@x.com")
        client = auth_client(client.app, settings, user)
        r = client.get("/locations")
        assert r.status_code == 200
        locs = r.json()
        assert len(locs) >= 3
        assert {"id", "type", "name"} <= set(locs[0].keys())

    def test_characters_by_user(self, env):
        client, settings, factory = env
        with factory() as session:
            user = make_user(session, "car3", "car3@x.com")
        client = auth_client(client.app, settings, user)
        me = client.get("/auth/me").json()
        r = client.get(f"/characters/by-user/{me['id']}")
        assert r.status_code == 200
        assert r.json() == []  # no character yet

        cr = client.post("/characters", json={"name": "Nova North", "sex": "F", "age": 31})
        assert cr.status_code == 201
        rows = client.get(f"/characters/by-user/{me['id']}").json()
        assert len(rows) == 1
        assert rows[0]["alive"] is True
