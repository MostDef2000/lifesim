"""M5 Chunk A tests (SPEC 005-player, T8): deps, schema 0.5.0, auth, character creation."""

import os
import sys

import pytest
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from app.api.app import create_app  # noqa: E402
from app.api.auth import (  # noqa: E402
    hash_password,
    sign_token,
    validate_registration,
    verify_password,
    verify_token,
)
from app.config.config import load_config  # noqa: E402
from app.db.models import bootstrap, create_engine_factory  # noqa: E402
from app.world.seed_world import seed_world  # noqa: E402

SETTINGS = None


def setup_module(module):
    global SETTINGS
    SETTINGS = load_config("config/default.yaml")


@pytest.fixture()
def world(tmp_path):
    """Bootstrapped world with seeded locations/jobs, no NPCs."""
    settings = SETTINGS.model_copy(update={
        "persistence": SETTINGS.persistence.model_copy(
            update={"db_path": str(tmp_path / "w.db")}
        )
    })
    engine = create_engine_factory(settings)
    with sessionmaker(bind=engine)() as session:
        bootstrap(engine, settings, seed=42)
        seed_world(session, settings, settings.world.world_id)
        session.commit()
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app(settings, factory)
    from fastapi.testclient import TestClient
    return settings, app, TestClient(app)


# ---------- Unit: password hashing (AE3''') ----------

def test_password_hash_roundtrip():
    h = hash_password("correct horse battery")
    assert h.startswith("pbkdf2_sha256$100000$")
    assert verify_password("correct horse battery", h)
    assert not verify_password("wrong password", h)


def test_password_hash_unique_salts():
    assert hash_password("same-password-1") != hash_password("same-password-1")


def test_password_too_short_rejected():
    with pytest.raises(ValueError):
        hash_password("short")


# ---------- Unit: token sign/verify/expire (AE3''') ----------

def test_token_roundtrip():
    token = sign_token(7, "user", "s3cret", ttl_min=60, now=1_000_000)
    payload = verify_token(token, "s3cret", now=1_000_000 + 3600 - 1)
    assert payload is not None
    assert payload["sub"] == 7
    assert payload["role"] == "user"


def test_token_expired():
    token = sign_token(7, "user", "s3cret", ttl_min=60, now=1_000_000)
    assert verify_token(token, "s3cret", now=1_000_000 + 3601) is None


def test_token_wrong_secret():
    token = sign_token(7, "user", "s3cret", ttl_min=60)
    assert verify_token(token, "other-secret") is None


def test_token_malformed():
    assert verify_token("garbage", "s3cret") is None
    assert verify_token("a.b.c", "s3cret") is None


# ---------- Unit: registration validation ----------

def test_validate_registration_errors():
    errs = validate_registration("ab", "nope", "short", False)
    assert any("age_confirmed" in e for e in errs)
    assert any("username" in e for e in errs)
    assert any("email" in e for e in errs)
    assert any("password" in e for e in errs)
    assert validate_registration("alice_1", "a@b.co", "longenough", True) == []


# ---------- E2E: auth flow (AE1''' part 1) ----------

def test_register_login_me_logout(world):
    settings, app, client = world

    r = client.post("/auth/register", json={
        "username": "alice", "email": "alice@example.com",
        "password": "wonderland99", "age_confirmed": True,
    })
    assert r.status_code == 201, r.text
    assert r.json()["id"] >= 1

    r = client.post("/auth/login", json={"username": "alice", "password": "wonderland99"})
    assert r.status_code == 200
    assert settings.api.cookie_name in client.cookies

    r = client.get("/auth/me")
    assert r.status_code == 200
    assert r.json()["username"] == "alice"
    assert r.json()["role"] == "user"

    r = client.post("/auth/logout")
    assert r.status_code == 200
    r = client.get("/auth/me")
    assert r.status_code == 401


def test_register_age_gate(world):
    _, _, client = world
    r = client.post("/auth/register", json={
        "username": "bob", "email": "b@x.com",
        "password": "password123", "age_confirmed": False,
    })
    assert r.status_code == 422
    assert any("age_confirmed" in d for d in r.json()["detail"])


def test_register_duplicate_username(world):
    _, _, client = world
    body = {
        "username": "carol", "email": "c@x.com",
        "password": "password123", "age_confirmed": True,
    }
    assert client.post("/auth/register", json=body).status_code == 201
    r = client.post("/auth/register", json={**body, "email": "other@x.com"})
    assert r.status_code == 409
    r = client.post("/auth/register", json={**body, "username": "carol2"})
    assert r.status_code == 409


def test_login_wrong_password(world):
    _, _, client = world
    client.post("/auth/register", json={
        "username": "dave", "email": "d@x.com", "password": "password123", "age_confirmed": True,
    })
    r = client.post("/auth/login", json={"username": "dave", "password": "wrong-pass"})
    assert r.status_code == 401


def test_bearer_token_supported(world):
    settings, app, client = world
    client.post("/auth/register", json={
        "username": "erin", "email": "e@x.com", "password": "password123", "age_confirmed": True,
    })
    r = client.post("/auth/login", json={"username": "erin", "password": "password123"})
    token = client.cookies.get(settings.api.cookie_name)
    client.cookies.clear()
    r = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["username"] == "erin"


# ---------- E2E: character creation (R3) ----------

def test_create_character_flow(world):
    _, _, client = world
    client.post("/auth/register", json={
        "username": "frank", "email": "f@x.com", "password": "password123", "age_confirmed": True,
    })
    client.post("/auth/login", json={"username": "frank", "password": "password123"})

    r = client.post("/characters", json={"name": "Frank Miller", "sex": "M", "age": 30})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["id"].startswith("plr_")
    assert body["control_mode"] == "AUTONOMOUS"

    # second character → 409 (limit 1/user)
    r = client.post("/characters", json={"name": "Frank Two", "sex": "M", "age": 30})
    assert r.status_code == 409


def test_create_character_name_taken(world):
    _, _, client = world
    client.post("/auth/register", json={
        "username": "gina", "email": "g@x.com", "password": "password123", "age_confirmed": True,
    })
    client.post("/auth/login", json={"username": "gina", "password": "password123"})
    # first user takes the name
    r = client.post("/characters", json={"name": "Frank Miller", "sex": "F", "age": 25})
    assert r.status_code == 201, r.text
    # second user (fresh registration) collides on the same name
    client.post("/auth/logout")
    client.post("/auth/register", json={
        "username": "hugo", "email": "h2@x.com", "password": "password123", "age_confirmed": True,
    })
    client.post("/auth/login", json={"username": "hugo", "password": "password123"})
    r = client.post("/characters", json={"name": "Frank Miller", "sex": "M", "age": 40})
    assert r.status_code == 409


def test_create_character_minor_rejected(world):
    _, _, client = world
    client.post("/auth/register", json={
        "username": "hank", "email": "h@x.com", "password": "password123", "age_confirmed": True,
    })
    client.post("/auth/login", json={"username": "hank", "password": "password123"})
    r = client.post("/characters", json={"name": "Kid Charlemagne", "sex": "M", "age": 17})
    assert r.status_code == 422


def test_create_character_requires_auth(world):
    _, _, client = world
    r = client.post("/characters", json={"name": "Anon Ymous", "sex": "M", "age": 30})
    assert r.status_code == 401
