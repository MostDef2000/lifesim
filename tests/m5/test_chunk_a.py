"""M5 Chunk A tests (SPEC 005-player, T8): deps, schema 0.5.0, auth, character creation.

Issue #309: local register/login is gone — the app authenticates through the
OIDC provider flow. Gameplay fixtures create the User row directly
(tests.helpers.make_user) and sign the app's own v2 session cookie
(tests.helpers.auth_client).
"""

import os
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from app.api.app import create_app  # noqa: E402
from app.api.auth import sign_token, verify_token  # noqa: E402
from app.config.config import load_config  # noqa: E402
from app.db.models import bootstrap, create_engine_factory  # noqa: E402
from app.world.seed_world import seed_world  # noqa: E402
from tests.helpers import auth_client, make_user  # noqa: E402

SETTINGS = None


def setup_module(module):
    global SETTINGS
    SETTINGS = load_config("config/default.yaml")


@pytest.fixture()
def world(tmp_path):
    """Bootstrapped world with seeded locations/jobs, no NPCs.

    Returns (settings, app, client, factory): `client` is UNauthenticated;
    tests that need a session create a user via make_user(factory session)
    and an auth_client.
    """
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
    return settings, app, TestClient(app), factory


def _user(factory, username, email, **kw):
    with factory() as session:
        return make_user(session, username, email, **kw)


# ---------- Unit: token sign/verify/expire (AE3''') ----------

def test_token_roundtrip():
    token = sign_token(7, "user", "s3cret", 60, "gen-1", now=1_000_000)
    payload = verify_token(token, "s3cret", now=1_000_000 + 3600 - 1)
    assert payload is not None
    assert payload["sub"] == 7
    assert payload["role"] == "user"
    # #309: v2 tokens carry the format marker + the user's generation
    assert payload["v"] == 2
    assert payload["gen"] == "gen-1"


def test_token_expired():
    token = sign_token(7, "user", "s3cret", 60, "gen-1", now=1_000_000)
    assert verify_token(token, "s3cret", now=1_000_000 + 3601) is None


def test_token_wrong_secret():
    token = sign_token(7, "user", "s3cret", 60, "gen-1")
    assert verify_token(token, "other-secret") is None


def test_token_malformed():
    assert verify_token("garbage", "s3cret") is None
    assert verify_token("a.b.c", "s3cret") is None


# ---------- E2E: auth flow (AE1''' part 1, #309: OIDC session cookie) ----------

def _parse_set_cookie(header):
    """Split a Set-Cookie header into (name, value, {attr: value-or-True})."""
    parts = [p.strip() for p in header.split(";")]
    name, _, value = parts[0].partition("=")
    attrs = {}
    for p in parts[1:]:
        if "=" in p:
            k, _, v = p.partition("=")
            attrs[k.strip().lower()] = v.strip()
        else:
            attrs[p.strip().lower()] = True
    return name, value, attrs


def test_session_me_logout(world):
    settings, app, _, factory = world
    user = _user(factory, "alice", "alice@example.com")
    client = auth_client(app, settings, user)

    r = client.get("/auth/me")
    assert r.status_code == 200
    assert r.json()["username"] == "alice"
    assert r.json()["role"] == "user"

    r = client.post("/auth/logout")
    assert r.status_code == 200

    # #309: logout is STATELESS — the token is a signed HMAC with no
    # server-side session table, so there is nothing to revoke. The only
    # observable effect is the cookie-clearing Set-Cookie headers. (The old
    # "same client → 401 after logout" assertion only passed because the
    # login response's Set-Cookie had populated the httpx jar with path=/;
    # a directly-set jar entry is not overwritten by delete_cookie, so that
    # assertion was a harness artifact, not app behavior. Dropped.)
    cleared = {}
    for header in r.headers.get_list("set-cookie"):
        name, value, attrs = _parse_set_cookie(header)
        cleared[name] = (value, attrs)

    # Both the session cookie and the OIDC browser-binding cookie are cleared.
    assert set(cleared) == {settings.api.cookie_name, f"{settings.api.cookie_name}_bind"}
    for name, (value, attrs) in cleared.items():
        assert value == '""', f"{name} not emptied: {value!r}"
        assert attrs.get("max-age") == "0", f"{name} missing Max-Age=0: {attrs}"
        assert attrs.get("path") == "/", f"{name} missing Path=/: {attrs}"
        assert attrs.get("httponly") is True, f"{name} missing HttpOnly: {attrs}"
        assert attrs.get("samesite") == "lax", f"{name} missing SameSite=lax: {attrs}"

    # A cookie-less client is unauthenticated (the token only authenticates
    # when it is actually presented).
    anon = TestClient(app)
    assert anon.get("/auth/me").status_code == 401


def test_bearer_token_supported(world):
    settings, app, _, factory = world
    user = _user(factory, "erin", "e@x.com")
    client = auth_client(app, settings, user)
    token = client.cookies.get(settings.api.cookie_name)
    client.cookies.clear()
    r = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["username"] == "erin"


def test_local_auth_routes_removed(world):
    """#309: local registration/login are gone — OIDC SSO only.

    POST /auth/register: route removed entirely → 404.
    POST /auth/login: the path now serves GET (OIDC redirect start) → 405.
    """
    _, _, client, _ = world
    r = client.post("/auth/register", json={
        "username": "alice", "email": "alice@example.com",
        "password": "wonderland99", "age_confirmed": True,
    })
    assert r.status_code == 404
    r = client.post("/auth/login", json={"username": "alice", "password": "wonderland99"})
    assert r.status_code == 405


# ---------- E2E: character creation (R3) ----------

def test_create_character_flow(world):
    settings, app, _, factory = world
    user = _user(factory, "frank", "f@x.com")
    client = auth_client(app, settings, user)

    r = client.post("/characters", json={"name": "Frank Miller", "sex": "M", "age": 30})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["id"].startswith("plr_")
    assert body["control_mode"] == "AUTONOMOUS"

    # second character → 409 (limit 1/user)
    r = client.post("/characters", json={"name": "Frank Two", "sex": "M", "age": 30})
    assert r.status_code == 409


def test_create_character_name_taken(world):
    settings, app, _, factory = world
    gina = _user(factory, "gina", "g@x.com")
    client = auth_client(app, settings, gina)
    # first user takes the name
    r = client.post("/characters", json={"name": "Frank Miller", "sex": "F", "age": 25})
    assert r.status_code == 201, r.text
    # second user collides on the same name
    hugo = _user(factory, "hugo", "h2@x.com")
    client2 = auth_client(app, settings, hugo)
    r = client2.post("/characters", json={"name": "Frank Miller", "sex": "M", "age": 40})
    assert r.status_code == 409


def test_create_character_minor_rejected(world):
    settings, app, _, factory = world
    user = _user(factory, "hank", "h@x.com")
    client = auth_client(app, settings, user)
    r = client.post("/characters", json={"name": "Kid Charlemagne", "sex": "M", "age": 17})
    assert r.status_code == 422


def test_create_character_requires_auth(world):
    _, _, client, _ = world
    r = client.post("/characters", json={"name": "Anon Ymous", "sex": "M", "age": 30})
    assert r.status_code == 401
