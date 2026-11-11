"""Shared auth helpers for OIDC SSO tests (issue #309).

Local register/login is gone — the app authenticates through the OIDC
provider flow (app.api.oidc). Gameplay fixtures must NEVER go through the
provider: create the User row directly and sign the app's OWN session
cookie (v2 token with the user's session_generation).

Precedent: tests/integration/helpers.py (plain functions, no fixtures).
"""
import itertools

from fastapi.testclient import TestClient

from app.api.app import get_secret
from app.api.auth import sign_token
from app.db.models import User

# Module-level counter: oidc_sub must be unique per (issuer, sub) pair
# (uq_users_oidc_sub) even when the same username is created twice.
_counter = itertools.count()


def make_user(session, username, email, role="user", **kw):
    """Create a User row directly (no provider, no password).

    commit is REQUIRED: the app opens its own session from the same engine,
    so an uncommitted row is invisible to the API.
    """
    user = User(
        username=username,
        email=email,
        password_hash=None,
        role=role,
        oidc_issuer="https://test",
        oidc_sub=f"sub-{username}-{next(_counter)}",
        created_at=0,
        **kw,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def session_cookie(settings, user):
    """A valid v2 session token for `user`, signed with the app's secret.

    get_secret(settings) guarantees the secret matches the app's own
    resolution (api.secret_env env var or the dev fallback).
    """
    return sign_token(
        user.id,
        user.role,
        get_secret(settings),
        settings.api.session_ttl_min,
        user.session_generation,
    )


def auth_client(app, settings, user):
    """TestClient with the session cookie pre-set for `user`."""
    client = TestClient(app)
    client.cookies.set(settings.api.cookie_name, session_cookie(settings, user))
    return client
