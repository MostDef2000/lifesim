"""M9 Chunk A tests (SPEC 009-web, T3): static serving + API not shadowed."""
import os
import sys

import pytest
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from fastapi.testclient import TestClient  # noqa: E402

from app.config.config import load_config  # noqa: E402
from app.db.models import bootstrap, create_engine_factory  # noqa: E402
from app.world.seed_world import seed_world  # noqa: E402

SETTINGS = None


def setup_module(module):
    global SETTINGS
    SETTINGS = load_config("config/default.yaml")


@pytest.fixture()
def client(tmp_path):
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

    return TestClient(create_app(settings, factory))


class TestStatic:
    def test_index_served(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert "ВЛ1: Рейнеке" in r.text
        assert "/static/app.js" in r.text

    def test_static_assets(self, client):
        js = client.get("/static/app.js")
        css = client.get("/static/style.css")
        assert js.status_code == 200
        assert css.status_code == 200
        assert "textContent" in js.text  # XSS-hygiene primitive present
        assert "--bg" in css.text

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
    def test_me_returns_ws_token(self, client):
        client.post("/auth/register", json={
            "username": "ann1", "email": "ann1@x.com",
            "password": "password123", "age_confirmed": True,
        })
        client.post("/auth/login", json={"username": "ann1", "password": "password123"})
        me = client.get("/auth/me").json()
        assert me["ws_token"]
        assert me["role"] == "user"
        # token verifies against the same secret
        from app.api.app import get_secret
        from app.api.auth import verify_token

        payload = verify_token(me["ws_token"], get_secret(SETTINGS))
        assert payload is not None and payload["role"] == "user"


class TestNewEndpoints:
    def test_locations_list(self, client):
        r = client.get("/locations")
        assert r.status_code == 401  # auth-gated like the rest of the API
        client.post("/auth/register", json={
            "username": "bob2", "email": "bob2@x.com",
            "password": "password123", "age_confirmed": True,
        })
        client.post("/auth/login", json={"username": "bob2", "password": "password123"})
        r = client.get("/locations")
        assert r.status_code == 200
        locs = r.json()
        assert len(locs) >= 3
        assert {"id", "type", "name"} <= set(locs[0].keys())

    def test_characters_by_user(self, client):
        client.post("/auth/register", json={
            "username": "car3", "email": "car3@x.com",
            "password": "password123", "age_confirmed": True,
        })
        client.post("/auth/login", json={"username": "car3", "password": "password123"})
        me = client.get("/auth/me").json()
        r = client.get(f"/characters/by-user/{me['id']}")
        assert r.status_code == 200
        assert r.json() == []  # no character yet

        cr = client.post("/characters", json={"name": "Nova North", "sex": "F", "age": 31})
        assert cr.status_code == 201
        rows = client.get(f"/characters/by-user/{me['id']}").json()
        assert len(rows) == 1
        assert rows[0]["alive"] is True
