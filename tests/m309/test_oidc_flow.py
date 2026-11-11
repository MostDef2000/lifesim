"""#309 — OIDC SSO flow, end-to-end with a mocked provider transport.

Covers the NEW code (backend/app/api/oidc.py + the #309 seams in app.py)
with REAL signed JWTs (RSA keys generated at runtime) and REAL PKCE math —
never the helper-signed-cookie path. Every provider interaction goes through
httpx.MockTransport; GET /auth/login and /auth/callback are always called with
follow_redirects=False.

Coverage areas (work order 1-9):
  1. Discovery: exact issuer match, trailing slash, malformed/non-200/oversized,
     cache hit within TTL + refetch after TTL.
  2. Login start: 302 to authorize endpoint with code/client_id/redirect_uri/
     scopes/state/nonce/PKCE S256; bind cookie; second login invalidates first.
  3. Callback happy path: full exchange, token form (code+verifier+client_id+
     redirect_uri+client_secret), JWKS fetch, id_token validation, groups gate,
     JIT User(role="user") by (issuer, sub), v2 session cookie, single-use state.
  4. Token/id_token failure paths: each → allowlisted fail-redirect.
  5. JIT edge cases: username collision, same (issuer, sub) reuse,
     MAX_TRANSACTIONS cap + oldest eviction.
  6. Legacy-session rejection (create_app): v1 token → 401, wrong generation → 401.
  7. WS close-code 4401 (create_app): absent + legacy cookie, client-observable.
  8. Removed credential routes (create_app): register 404, POST login 405,
     GET login 302.
  9. Boot fail-closed (create_app): enabled+no-oidc raises, require_secret+
     missing env raises, disabled+oidc creates fine.
"""
import base64
import hashlib
import hmac
import json
import os
import sys
import time
from types import SimpleNamespace
from urllib.parse import parse_qsl, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.websockets import WebSocketDisconnect

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

import app.api.oidc as oidc_mod  # noqa: E402
from app.api.app import create_app, get_secret  # noqa: E402
from app.api.auth import verify_token  # noqa: E402
from app.api.oidc import (  # noqa: E402
    DISCOVERY_CACHE_TTL_S,
    MAX_TRANSACTIONS,
    OidcClient,
    OidcError,
    TransactionStore,
)
from app.config.config import ApiConfig, OidcConfig, load_config  # noqa: E402
from app.db.models import Base, User, bootstrap, create_engine_factory  # noqa: E402
from tests.helpers import auth_client, make_user  # noqa: E402

# ---------- provider identity (test-only, no real endpoints) ----------
ISSUER = "https://idp.example.test"
CLIENT_ID = "lifesim-client"
CALLBACK = "https://app.example.test/auth/callback"
REQUIRED_GROUP = "lifesim"
CLIENT_SECRET = "test-client-secret"

COOKIE = "vl1_session"
BIND = "vl1_session_bind"


# ---------- runtime key material (never committed) ----------

def make_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _b64u(n: int, length: int | None = None) -> str:
    data = n.to_bytes((n.bit_length() + 7) // 8, "big")
    if length is not None:
        data = data.rjust(length, b"\0")
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def rsa_jwk(priv, kid: str) -> dict:
    pub = priv.public_key().public_numbers()
    ksize = (pub.n.bit_length() + 7) // 8
    return {
        "kty": "RSA", "kid": kid, "use": "sig", "alg": "RS256",
        "n": _b64u(pub.n, ksize), "e": _b64u(pub.e),
    }


def jwks_doc(keys) -> dict:
    return {"keys": [rsa_jwk(priv, kid) for kid, priv in keys]}


def discovery_doc(issuer: str) -> dict:
    base = issuer.rstrip("/")
    return {
        "issuer": issuer,
        "authorization_endpoint": f"{base}/authorize",
        "token_endpoint": f"{base}/token",
        "jwks_uri": f"{base}/jwks",
    }


def make_id_token(
    key, kid, *, nonce, sub="sub-1", email="user@example.com",
    preferred_username="user1", groups=None, iss=ISSUER, aud=CLIENT_ID,
    azp=None, exp_delta=3600, iat_delta=0, now=None, drop=None,
    algorithm="RS256", signing_key=None,
) -> str:
    now = int(time.time()) if now is None else now
    claims = {
        "iss": iss, "aud": aud, "sub": sub,
        "exp": now + exp_delta, "iat": now + iat_delta,
        "nonce": nonce, "email": email,
        "preferred_username": preferred_username,
        "groups": [REQUIRED_GROUP] if groups is None else groups,
    }
    if azp is not None:
        claims["azp"] = azp
    if drop:
        for k in drop:
            claims.pop(k, None)
    return jwt.encode(
        claims, signing_key if signing_key is not None else key,
        algorithm=algorithm, headers={"kid": kid},
    )


def make_v1_token(user_id: int, role: str, secret: str, ttl_min: int) -> str:
    """Legacy v1 session token: valid HMAC, but NO `v`/`gen` claims."""
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {"sub": user_id, "role": role, "iat": now, "exp": now + ttl_min * 60}
    h = base64.urlsafe_b64encode(
        json.dumps(header, separators=(",", ":")).encode()).rstrip(b"=").decode()
    p = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode()).rstrip(b"=").decode()
    sig = hmac.new(secret.encode(), f"{h}.{p}".encode(), hashlib.sha256).digest()
    return f"{h}.{p}.{base64.urlsafe_b64encode(sig).rstrip(b'=').decode()}"


def pkce_challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def parse_query(location: str) -> dict:
    return dict(parse_qsl(urlsplit(location).query))


# ---------- in-memory OIDC provider behind httpx.MockTransport ----------

class Provider:
    """Routes discovery / JWKS / token endpoints; records every request."""

    def __init__(self, keys):
        self.keys = keys
        self.discovery = discovery_doc(ISSUER)
        self.discovery_status = 200
        self.discovery_body = None  # raw bytes override (malformed/oversized)
        self.jwks_docs = [jwks_doc(keys)]  # one doc per JWKS hit (rotation)
        self.token_status = 200
        self.token_body = None  # must be set by the test before a callback
        self.discovery_hits = 0
        self.jwks_hits = 0
        self.token_hits = 0
        self.last_token_form = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        # Route discovery by request-URL suffix (NOT off self.discovery["issuer"]):
        # the client fetches off cfg.issuer, so a mismatched doc issuer must still
        # reach this handler and be rejected by the issuer check itself, not a 404.
        if request.method == "GET" and url.endswith("/.well-known/openid-configuration"):
            self.discovery_hits += 1
            if self.discovery_status != 200:
                return httpx.Response(self.discovery_status, text="unavailable")
            if self.discovery_body is not None:
                return httpx.Response(200, content=self.discovery_body)
            return httpx.Response(200, json=self.discovery)
        if request.method == "GET" and url == self.discovery["jwks_uri"]:
            self.jwks_hits += 1
            idx = min(self.jwks_hits - 1, len(self.jwks_docs) - 1)
            return httpx.Response(200, json=self.jwks_docs[idx])
        if request.method == "POST" and url == self.discovery["token_endpoint"]:
            self.token_hits += 1
            self.last_token_form = dict(parse_qsl(request.content.decode("utf-8")))
            if self.token_status != 200:
                return httpx.Response(self.token_status, json={"error": "invalid_grant"})
            if self.token_body is None:
                return httpx.Response(400, json={"error": "no_token_body_configured"})
            return httpx.Response(200, json=self.token_body)
        return httpx.Response(404, text="not found")


def make_cfg(issuer: str = ISSUER) -> OidcConfig:
    return OidcConfig(
        issuer=issuer, client_id=CLIENT_ID,
        callback_url=CALLBACK, required_group=REQUIRED_GROUP,
    )


def make_client(cfg, provider, secret: str = CLIENT_SECRET) -> OidcClient:
    return OidcClient(cfg, secret, transport=httpx.MockTransport(provider.handler))


# ---------- flow fixtures ----------

@pytest.fixture
def oidc_env(default_settings, tmp_path):
    """Minimal FastAPI app + register_oidc_routes with a MockTransport provider.

    create_app() does NOT pass a transport, so flow tests build the app here
    and inject the provider directly (the documented testability seam).
    """
    key1, key2 = make_key(), make_key()
    keys = [("kid1", key1), ("kid2", key2)]
    provider = Provider(keys)
    settings = default_settings.model_copy(update={
        "api": ApiConfig(
            enabled=True,
            cookie_secure=False,  # plain-http TestClient round-trips the cookies
            oidc=make_cfg(),
        ),
    })
    engine = create_engine(
        f"sqlite:///{tmp_path / 'flow.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def db():
        s = factory()
        try:
            yield s
        finally:
            s.close()

    app = FastAPI()
    from app.api.oidc import register_oidc_routes

    register_oidc_routes(
        app, settings, CLIENT_SECRET, db,
        transport=httpx.MockTransport(provider.handler),
    )
    client = TestClient(app)
    yield SimpleNamespace(
        app=app, client=client, settings=settings, provider=provider,
        factory=factory, engine=engine, keys=keys,
    )
    engine.dispose()


@pytest.fixture
def full_env(tmp_path):
    """create_app() with the real default.yaml (oidc set, api.enabled=false).

    Used for the seams only create_app wires: /auth/me, /ws, removed routes,
    and the boot fail-closed checks.
    """
    settings = load_config("config/default.yaml")
    settings = settings.model_copy(update={
        "persistence": settings.persistence.model_copy(
            update={"db_path": str(tmp_path / "w.db")}),
    })
    engine = create_engine_factory(settings)
    bootstrap(engine, settings, seed=42)  # creates its own session internally
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app(settings, factory)
    yield SimpleNamespace(settings=settings, app=app, factory=factory, engine=engine)
    engine.dispose()


# ---------- flow helpers ----------

def start_login(client) -> dict:
    r = client.get("/auth/login", follow_redirects=False)
    assert r.status_code == 302
    return parse_query(r.headers["location"])


def do_callback(client, state: str, code: str = "c1") -> httpx.Response:
    return client.get(
        f"/auth/callback?state={state}&code={code}", follow_redirects=False)


def assert_fail_redirect(r: httpx.Response, code: str) -> None:
    assert r.status_code == 302
    assert r.headers["location"] == f"/?error={code}"
    assert r.headers.get("cache-control") == "no-store"


def happy_callback(env, *, sub="sub-alice", email="alice@example.com",
                   username="alice", groups=None) -> httpx.Response:
    """Login + a fully valid provider token → the success redirect."""
    kid, key = env.keys[0]
    params = start_login(env.client)
    token = make_id_token(
        key, kid, nonce=params["nonce"], sub=sub, email=email,
        preferred_username=username, groups=groups if groups is not None else [REQUIRED_GROUP],
    )
    env.provider.token_body = {"id_token": token, "access_token": "at", "token_type": "Bearer"}
    return do_callback(env.client, params["state"])


# =====================================================================
# 1. Discovery
# =====================================================================

class TestDiscovery:
    def test_exact_issuer_match_ok(self):
        provider = Provider([( "kid1", make_key())])
        client = make_client(make_cfg(), provider)
        doc = client.discovery()
        assert doc["issuer"] == ISSUER
        assert provider.discovery_hits == 1

    def test_issuer_mismatch_auth_failed(self):
        provider = Provider([("kid1", make_key())])
        provider.discovery = discovery_doc("https://evil.example")
        client = make_client(make_cfg(), provider)
        with pytest.raises(OidcError) as e:
            client.discovery()
        assert e.value.code == "auth_failed"

    def test_trailing_slash_match_ok(self):
        # cfg.issuer WITH trailing slash; doc.issuer must match exactly.
        provider = Provider([("kid1", make_key())])
        provider.discovery = discovery_doc(ISSUER + "/")
        client = make_client(make_cfg(issuer=ISSUER + "/"), provider)
        assert client.discovery()["issuer"] == ISSUER + "/"

    def test_trailing_slash_mismatch_auth_failed(self):
        # cfg.issuer WITHOUT slash, doc.issuer WITH slash → not exact → reject.
        provider = Provider([("kid1", make_key())])
        provider.discovery = discovery_doc(ISSUER + "/")
        client = make_client(make_cfg(), provider)
        with pytest.raises(OidcError) as e:
            client.discovery()
        assert e.value.code == "auth_failed"

    def test_non_200_auth_failed(self):
        provider = Provider([("kid1", make_key())])
        provider.discovery_status = 500
        client = make_client(make_cfg(), provider)
        with pytest.raises(OidcError) as e:
            client.discovery()
        assert e.value.code == "auth_failed"

    def test_malformed_json_auth_failed(self):
        provider = Provider([("kid1", make_key())])
        provider.discovery_body = b"this is not json"
        client = make_client(make_cfg(), provider)
        with pytest.raises(OidcError) as e:
            client.discovery()
        assert e.value.code == "auth_failed"

    def test_non_object_json_auth_failed(self):
        provider = Provider([("kid1", make_key())])
        provider.discovery_body = b"[1, 2, 3]"
        client = make_client(make_cfg(), provider)
        with pytest.raises(OidcError) as e:
            client.discovery()
        assert e.value.code == "auth_failed"

    def test_oversized_discovery_auth_failed(self):
        provider = Provider([("kid1", make_key())])
        provider.discovery_body = b"x" * (oidc_mod.MAX_RESPONSE_BYTES + 1)
        client = make_client(make_cfg(), provider)
        with pytest.raises(OidcError) as e:
            client.discovery()
        assert e.value.code == "auth_failed"

    def test_missing_endpoint_field_auth_failed(self):
        provider = Provider([("kid1", make_key())])
        provider.discovery = {
            "issuer": ISSUER,
            "authorization_endpoint": f"{ISSUER}/authorize",
            # token_endpoint + jwks_uri missing
        }
        client = make_client(make_cfg(), provider)
        with pytest.raises(OidcError) as e:
            client.discovery()
        assert e.value.code == "auth_failed"

    def test_cache_hit_within_ttl_and_refetch_after(self, monkeypatch):
        provider = Provider([("kid1", make_key())])
        client = make_client(make_cfg(), provider)
        t = [1000.0]
        monkeypatch.setattr(oidc_mod, "_now", lambda: t[0])
        client.discovery()
        assert provider.discovery_hits == 1
        t[0] = 1000.0 + DISCOVERY_CACHE_TTL_S - 1  # still within TTL
        client.discovery()
        assert provider.discovery_hits == 1  # cache hit, no refetch
        t[0] = 1000.0 + DISCOVERY_CACHE_TTL_S + 1  # past TTL
        client.discovery()
        assert provider.discovery_hits == 2  # refetched


# =====================================================================
# 2. Login start (GET /auth/login)
# =====================================================================

class TestLoginStart:
    def test_redirect_to_authorize_with_pkce(self, oidc_env):
        env = oidc_env
        r = env.client.get("/auth/login", follow_redirects=False)
        assert r.status_code == 302
        loc = r.headers["location"]
        # the redirect target is the provider's authorization_endpoint
        assert loc.startswith(f"{ISSUER}/authorize?")
        params = parse_query(loc)
        assert params["response_type"] == "code"
        assert params["client_id"] == CLIENT_ID
        assert params["redirect_uri"] == CALLBACK
        assert params["scope"] == "openid profile email groups"
        assert params["code_challenge_method"] == "S256"
        assert params["state"]
        assert params["nonce"]
        assert params["code_challenge"]

    def test_bind_cookie_set(self, oidc_env):
        env = oidc_env
        r = env.client.get("/auth/login", follow_redirects=False)
        assert r.status_code == 302
        assert r.cookies.get(BIND)  # Set-Cookie carries the binding value
        assert env.client.cookies.get(BIND)  # and the jar stores it

    def test_pkce_challenge_matches_verifier_at_token_request(self, oidc_env):
        # The challenge is verified against the verifier the token request
        # actually carries (real PKCE math, not a re-run of the server fn).
        env = oidc_env
        kid, key = env.keys[0]
        params = start_login(env.client)
        challenge = params["code_challenge"]
        token = make_id_token(key, kid, nonce=params["nonce"], sub="sub-pkce",
                              email="pkce@example.com", preferred_username="pkce")
        env.provider.token_body = {"id_token": token}
        r = do_callback(env.client, params["state"], code="pkce-code")
        assert r.status_code == 302 and r.headers["location"] == "/"
        form = env.provider.last_token_form
        assert form is not None
        assert pkce_challenge(form["code_verifier"]) == challenge

    def test_second_login_invalidates_first(self, oidc_env):
        env = oidc_env
        p1 = start_login(env.client)
        p2 = start_login(env.client)  # same browser (same bind cookie)
        assert p1["state"] != p2["state"]
        # the first transaction is dead: its state no longer consumes
        assert_fail_redirect(do_callback(env.client, p1["state"]), "state_mismatch")


# =====================================================================
# 3. Callback happy path
# =====================================================================

class TestCallbackHappyPath:
    def test_full_exchange(self, oidc_env):
        env = oidc_env
        kid, key = env.keys[0]
        params = start_login(env.client)
        token = make_id_token(
            key, kid, nonce=params["nonce"], sub="sub-alice",
            email="alice@example.com", preferred_username="alice",
        )
        env.provider.token_body = {
            "id_token": token, "access_token": "at", "token_type": "Bearer"}
        r = do_callback(env.client, params["state"], code="auth-code-1")

        # success redirect to the fixed target
        assert r.status_code == 302
        assert r.headers["location"] == "/"
        assert r.headers.get("cache-control") == "no-store"

        # the token request carried the full PKCE + client-credentials form
        form = env.provider.last_token_form
        assert form["grant_type"] == "authorization_code"
        assert form["code"] == "auth-code-1"
        assert form["redirect_uri"] == CALLBACK
        assert form["client_id"] == CLIENT_ID
        assert form["client_secret"] == CLIENT_SECRET
        assert pkce_challenge(form["code_verifier"]) == params["code_challenge"]

        # JWKS was fetched from discovery's jwks_uri
        assert env.provider.jwks_hits >= 1

        # JIT user keyed by the exact (issuer, sub)
        with env.factory() as s:
            user = s.query(User).filter_by(
                oidc_issuer=ISSUER, oidc_sub="sub-alice").one()
            uid, gen = user.id, user.session_generation
            assert user.role == "user"
            assert user.username == "alice"
            assert user.email == "alice@example.com"

        # the app's OWN v2 session cookie (never a provider token)
        sess = env.client.cookies.get(COOKIE)
        assert sess
        payload = verify_token(sess, get_secret(env.settings))
        assert payload is not None
        assert payload["v"] == 2
        assert payload["sub"] == uid
        assert payload["gen"] == gen

        # the browser-binding cookie is cleared on success
        assert env.client.cookies.get(BIND) is None

    def test_second_callback_same_state_fails(self, oidc_env):
        env = oidc_env
        kid, key = env.keys[0]
        r = env.client.get("/auth/login", follow_redirects=False)
        assert r.status_code == 302
        params = parse_query(r.headers["location"])
        bind = r.cookies.get(BIND)  # the binding value minted at login
        assert bind
        token = make_id_token(key, kid, nonce=params["nonce"], sub="sub-once",
                              email="once@example.com", preferred_username="once")
        env.provider.token_body = {"id_token": token}
        first = do_callback(env.client, params["state"], code="c-a")
        assert first.status_code == 302 and first.headers["location"] == "/"
        # the success response deleted the bind cookie; re-set it so the ONLY
        # possible failure cause on replay is the consumed single-use txn
        env.client.cookies.set(BIND, bind)
        second = do_callback(env.client, params["state"], code="c-a")
        assert_fail_redirect(second, "state_mismatch")


# =====================================================================
# 4. Token / id_token failure paths
# =====================================================================

class TestTokenIdTokenFailures:
    def test_bogus_state_mismatch(self, oidc_env):
        assert_fail_redirect(
            do_callback(oidc_env.client, "not-a-real-state"), "state_mismatch")

    def test_expired_txn_state_mismatch(self, oidc_env, monkeypatch):
        env = oidc_env
        t = [1000.0]
        monkeypatch.setattr(oidc_mod, "_now", lambda: t[0])
        params = start_login(env.client)  # txn expires at 1000 + 300
        t[0] = 1000.0 + oidc_mod.TRANSACTION_TTL_S + 1
        assert_fail_redirect(do_callback(env.client, params["state"]), "state_mismatch")

    @pytest.mark.parametrize("defect", [
        "wrong_iss", "wrong_aud", "wrong_azp", "alg_hs256", "bad_signature",
        "expired", "nonce_mismatch", "kid_absent", "sub_absent",
    ])
    def test_id_token_defect_provider_error(self, oidc_env, defect):
        env = oidc_env
        kid, key = env.keys[0]
        kid2, key2 = env.keys[1]
        params = start_login(env.client)
        nonce = params["nonce"]
        base = dict(sub="sub-1", email="u@example.com",
                    preferred_username="user1", groups=[REQUIRED_GROUP])
        if defect == "kid_absent":
            # JWKS carries a DIFFERENT kid than the token's header
            env.provider.jwks_docs = [jwks_doc([("kidX", key)])]
            token = make_id_token(key, kid, nonce=nonce, **base)
        elif defect == "wrong_iss":
            token = make_id_token(key, kid, nonce=nonce, iss="https://evil.example", **base)
        elif defect == "wrong_aud":
            token = make_id_token(key, kid, nonce=nonce, aud="other-client", **base)
        elif defect == "wrong_azp":
            token = make_id_token(key, kid, nonce=nonce, azp="other-client", **base)
        elif defect == "alg_hs256":
            # ≥32-byte HMAC secret (RFC 7518 §3.2) to avoid InsecureKeyLengthWarning
            token = make_id_token(key, kid, nonce=nonce, algorithm="HS256",
                                  signing_key="hmac-secret-for-tests-0123456789", **base)
        elif defect == "bad_signature":
            # signed with key2 but presenting kid1 → JWKS key1 can't verify
            token = make_id_token(key2, kid, nonce=nonce, **base)
        elif defect == "expired":
            token = make_id_token(key, kid, nonce=nonce, exp_delta=-120, **base)
        elif defect == "nonce_mismatch":
            token = make_id_token(key, kid, nonce="wrong-nonce", **base)
        elif defect == "sub_absent":
            token = make_id_token(key, kid, nonce=nonce, drop=["sub"], **base)
        else:
            raise AssertionError(f"unknown defect {defect}")
        env.provider.token_body = {"id_token": token}
        assert_fail_redirect(do_callback(env.client, params["state"]), "provider_error")

    def test_token_endpoint_non_200_provider_error(self, oidc_env):
        env = oidc_env
        params = start_login(env.client)
        env.provider.token_status = 400
        assert_fail_redirect(do_callback(env.client, params["state"]), "provider_error")

    def test_token_endpoint_error_payload_provider_error(self, oidc_env):
        env = oidc_env
        params = start_login(env.client)
        env.provider.token_body = {"error": "invalid_grant"}  # no id_token
        assert_fail_redirect(do_callback(env.client, params["state"]), "provider_error")

    def test_groups_missing_required_group_denied(self, oidc_env):
        env = oidc_env
        r = happy_callback(env, sub="sub-nogroup", email="ng@example.com",
                           username="nogroup", groups=["some-other-group"])
        assert_fail_redirect(r, "group_denied")

    def test_groups_malformed_denied(self, oidc_env):
        env = oidc_env
        r = happy_callback(env, sub="sub-badgroups", email="bg@example.com",
                           username="badgroups", groups="not-a-list")
        assert_fail_redirect(r, "group_denied")

    def test_invalid_preferred_username_taken(self, oidc_env):
        env = oidc_env
        # "ab" violates USERNAME_RE [a-zA-Z0-9_]{3,32}
        r = happy_callback(env, sub="sub-short", email="short@example.com",
                           username="ab")
        assert_fail_redirect(r, "username_taken")

    def test_missing_email_auth_failed(self, oidc_env):
        env = oidc_env
        kid, key = env.keys[0]
        params = start_login(env.client)
        token = make_id_token(key, kid, nonce=params["nonce"], sub="sub-noemail",
                              email="", preferred_username="noemail")
        env.provider.token_body = {"id_token": token}
        assert_fail_redirect(do_callback(env.client, params["state"]), "auth_failed")

    def test_kid_rotation_success(self, oidc_env):
        env = oidc_env
        kid1, key1 = env.keys[0]
        kid2, key2 = env.keys[1]
        # first JWKS fetch → only kid1; the controlled refresh → kid1 + kid2
        env.provider.jwks_docs = [
            jwks_doc([(kid1, key1)]),
            jwks_doc([(kid1, key1), (kid2, key2)]),
        ]
        params = start_login(env.client)
        token = make_id_token(key2, kid2, nonce=params["nonce"], sub="sub-rot",
                              email="rot@example.com", preferred_username="rot")
        env.provider.token_body = {"id_token": token}
        r = do_callback(env.client, params["state"])
        assert r.status_code == 302 and r.headers["location"] == "/"
        assert env.provider.jwks_hits == 2  # initial + exactly one controlled refresh
        with env.factory() as s:
            assert s.query(User).filter_by(oidc_sub="sub-rot").count() == 1


# =====================================================================
# 5. JIT edge cases + TransactionStore bounds
# =====================================================================

class TestJitEdgeCases:
    def test_username_collision_taken(self, oidc_env):
        env = oidc_env
        with env.factory() as s:
            s.add(User(
                username="alice", email="other@example.com", password_hash=None,
                role="user", oidc_issuer="https://other", oidc_sub="sub-other",
                created_at=0,
            ))
            s.commit()
        # same username, DIFFERENT (issuer, sub) → no linking, reject
        r = happy_callback(env, sub="sub-alice2", email="alice2@example.com",
                           username="alice")
        assert_fail_redirect(r, "username_taken")
        with env.factory() as s:
            assert s.query(User).filter_by(oidc_sub="sub-alice2").count() == 0

    def test_same_issuer_sub_reused(self, oidc_env):
        env = oidc_env
        r1 = happy_callback(env, sub="sub-bob", email="bob@example.com", username="bob")
        assert r1.status_code == 302 and r1.headers["location"] == "/"
        with env.factory() as s:
            u1 = s.query(User).filter_by(oidc_sub="sub-bob").one()
            uid1, gen1 = u1.id, u1.session_generation
        # second login, same (issuer, sub) → SAME row, no duplicate
        r2 = happy_callback(env, sub="sub-bob", email="bob@example.com", username="bob")
        assert r2.status_code == 302 and r2.headers["location"] == "/"
        with env.factory() as s:
            rows = s.query(User).filter_by(oidc_sub="sub-bob").all()
            assert len(rows) == 1
            assert rows[0].id == uid1
            assert rows[0].session_generation == gen1  # preserved, not rotated
        sess = env.client.cookies.get(COOKIE)
        payload = verify_token(sess, get_secret(env.settings))
        assert payload["sub"] == uid1

    def test_max_transactions_cap_and_eviction(self, monkeypatch):
        t = [1000.0]
        monkeypatch.setattr(oidc_mod, "_now", lambda: t[0])
        store = TransactionStore()
        pairs = []
        for _ in range(MAX_TRANSACTIONS + 1):
            txn = store.create(None)
            pairs.append((txn["state"], txn["binding"]))
        # memory stays bounded at the cap
        assert len(store._by_state) == MAX_TRANSACTIONS
        # the oldest (first minted) was evicted → its state no longer consumes
        old_state, old_binding = pairs[0]
        assert store.consume(old_state, old_binding) is None
        # a recent one is still alive
        new_state, new_binding = pairs[-1]
        assert store.consume(new_state, new_binding) is not None

    def test_transaction_single_use(self, monkeypatch):
        t = [1000.0]
        monkeypatch.setattr(oidc_mod, "_now", lambda: t[0])
        store = TransactionStore()
        txn = store.create(None)
        assert store.consume(txn["state"], txn["binding"]) is not None
        assert store.consume(txn["state"], txn["binding"]) is None  # burned

    def test_new_login_invalidates_previous_binding(self, monkeypatch):
        t = [1000.0]
        monkeypatch.setattr(oidc_mod, "_now", lambda: t[0])
        store = TransactionStore()
        txn1 = store.create(None)
        txn2 = store.create(txn1["binding"])  # same browser re-login
        assert store.consume(txn1["state"], txn1["binding"]) is None  # invalidated
        assert store.consume(txn2["state"], txn2["binding"]) is not None  # alive


# =====================================================================
# 6. Legacy-session rejection (create_app)
# =====================================================================

class TestLegacySessionRejection:
    def test_v1_token_rejected(self, full_env):
        env = full_env
        with env.factory() as s:
            user = make_user(s, "legacy", "legacy@example.com")
            uid = user.id
        secret = get_secret(env.settings)
        v1 = make_v1_token(uid, "user", secret, env.settings.api.session_ttl_min)
        client = TestClient(env.app)
        client.cookies.set(env.settings.api.cookie_name, v1)
        assert client.get("/auth/me").status_code == 401

    def test_wrong_generation_rejected(self, full_env):
        env = full_env
        with env.factory() as s:
            user = make_user(s, "genuser", "gen@example.com")
        client = auth_client(env.app, env.settings, user)
        assert client.get("/auth/me").status_code == 200
        with env.factory() as s:
            u = s.query(User).filter_by(username="genuser").one()
            u.session_generation = "rotated-" + u.session_generation
            s.commit()
        # same (now stale) cookie → generation mismatch → 401
        assert client.get("/auth/me").status_code == 401


# =====================================================================
# 7. WS close-code 4401 (create_app)
# =====================================================================

class TestWsCloseCode:
    def test_ws_4401_absent_cookie(self, full_env):
        env = full_env
        client = TestClient(env.app)
        with pytest.raises(WebSocketDisconnect) as excinfo:
            with client.websocket_connect("/ws") as ws:
                ws.receive_json()
        assert excinfo.value.code == 4401

    def test_ws_4401_legacy_cookie(self, full_env):
        env = full_env
        with env.factory() as s:
            user = make_user(s, "wsuser", "ws@example.com")
            uid = user.id
        secret = get_secret(env.settings)
        v1 = make_v1_token(uid, "user", secret, env.settings.api.session_ttl_min)
        client = TestClient(env.app)
        client.cookies.set(env.settings.api.cookie_name, v1)
        with pytest.raises(WebSocketDisconnect) as excinfo:
            with client.websocket_connect("/ws") as ws:
                ws.receive_json()
        assert excinfo.value.code == 4401


# =====================================================================
# 8. Removed credential routes (create_app)
# =====================================================================

class TestRemovedCredentialRoutes:
    def test_register_404(self, full_env):
        client = TestClient(full_env.app)
        r = client.post(
            "/auth/register",
            json={"username": "x", "email": "x@x.com", "password": "p"})
        assert r.status_code == 404

    def test_login_post_405(self, full_env):
        client = TestClient(full_env.app)
        r = client.post("/auth/login", data={"username": "x", "password": "p"})
        assert r.status_code == 405

    def test_login_get_302(self, full_env):
        # GET /auth/login is the OIDC redirect start. The default.yaml provider
        # (localhost:9000) is unreachable in the sandbox → discovery fails →
        # the fail-closed 302 (never 404/405, never an unhandled 500).
        client = TestClient(full_env.app)
        r = client.get("/auth/login", follow_redirects=False)
        assert r.status_code == 302


# =====================================================================
# 9. Boot fail-closed (create_app)
# =====================================================================

class TestBootFailClosed:
    def _settings(self, tmp_path, **api_updates):
        base = load_config("config/default.yaml")
        api = ApiConfig(**api_updates)
        return base.model_copy(update={
            "persistence": base.persistence.model_copy(
                update={"db_path": str(tmp_path / "w.db")}),
            "api": api,
        })

    def test_enabled_without_oidc_raises(self, tmp_path):
        settings = self._settings(tmp_path, enabled=True, oidc=None)
        factory = sessionmaker(bind=create_engine_factory(settings))
        with pytest.raises(RuntimeError):
            create_app(settings, factory)

    def test_require_secret_missing_env_raises(self, tmp_path, monkeypatch):
        monkeypatch.delenv("LIFESIM_OIDC_CLIENT_SECRET", raising=False)
        settings = self._settings(
            tmp_path, enabled=True, require_secret=True, oidc=make_cfg())
        factory = sessionmaker(bind=create_engine_factory(settings))
        with pytest.raises(RuntimeError):
            create_app(settings, factory)

    def test_disabled_with_oidc_creates(self, tmp_path):
        settings = self._settings(tmp_path, enabled=False, oidc=make_cfg())
        factory = sessionmaker(bind=create_engine_factory(settings))
        app = create_app(settings, factory)
        assert app is not None
