"""OIDC SSO flow (Authentik): authorization-code + PKCE, JIT user provisioning.

Replaces local register/login (issue: auth replacement). Design contract:

- GET /auth/login  → create a single-use transaction (state/nonce/PKCE
  verifier), bind it to the browser via a short-lived HttpOnly cookie, 302
  to the provider's authorize endpoint.
- GET /auth/callback → consume the transaction atomically, exchange the
  code, validate the id_token (RS256 allowlist, exact issuer, aud/azp,
  exp/iat skew, nonce, sub, groups gate), JIT-provision the user by the
  exact (oidc_issuer, oidc_sub) pair, issue the app's OWN HMAC session
  cookie (app.api.auth — never an OIDC token), 302 to "/".

Failure paths never write the DB, never reflect provider error text, and
redirect to "/" with an allowlisted ?error= code. Transactions are
process-local (bounded dict, 5-min TTL) — same single-process assumption
as the admin rate-limit middleware; discovery/JWKS documents are cached
in-process with a bounded size and explicit httpx timeouts (TLS verify on,
redirects off).
"""

import base64
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from typing import Any

import httpx
import jwt

# Login transaction TTL (state/nonce/verifier) and the browser-binding
# cookie TTL (must outlive the transaction round-trip).
TRANSACTION_TTL_S = 300
BINDING_COOKIE_TTL_S = 600
# Discovery/JWKS cache lifetime; an unknown kid forces exactly one
# controlled refresh inside this window (key rotation support).
DISCOVERY_CACHE_TTL_S = 600
HTTP_TIMEOUT_S = 10.0
MAX_RESPONSE_BYTES = 1_000_000
# Bounded skew for exp/iat (PyJWT leeway).
CLOCK_SKEW_S = 60
# RS256 allowlist ONLY — the token header alg is never trusted.
ALLOWED_ALGS = ["RS256"]
# Hard cap on live login transactions — memory stays bounded even if the
# rate limiter is bypassed; at cap create() evicts the oldest entry.
MAX_TRANSACTIONS = 1000
# Historical spec-005 username rule: JIT-provisioned names must match it.
USERNAME_RE = re.compile(r"[a-zA-Z0-9_]{3,32}")

# Allowlisted error codes surfaced as ?error=<code> on the "/" redirect.
# The frontend maps these (and only these) to fixed Russian strings.
ERROR_CODES = (
    "auth_failed",
    "state_mismatch",
    "provider_error",
    "group_denied",
    "registration_closed",
    "username_taken",
)


class OidcError(Exception):
    """Login-flow failure carrying an allowlisted error code."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code if code in ERROR_CODES else "auth_failed"


def _now() -> float:
    """Clock seam — tests monkeypatch app.api.oidc._now."""
    return time.time()


def _bounded_read(response: httpx.Response) -> bytes:
    """Read a streamed response body with a hard size cap."""
    buf = bytearray()
    for chunk in response.iter_bytes(65536):
        buf += chunk
        if len(buf) > MAX_RESPONSE_BYTES:
            raise OidcError("auth_failed")
    return bytes(buf)


def _pkce_pair(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


class TransactionStore:
    """Process-local single-use login transactions.

    state → {nonce, verifier, binding, expires_at}; a parallel binding →
    state index lets a new login from the same browser invalidate its
    previous transaction. Expired entries are cleaned lazily. Single-use:
    consume() pops unconditionally, so every callback path (success or
    failure) burns exactly the one transaction it presented. All access is
    serialized by a lock (endpoints run in FastAPI's threadpool) and memory
    is bounded by MAX_TRANSACTIONS regardless of the rate limiter.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_state: dict[str, dict[str, Any]] = {}
        self._state_by_binding: dict[str, str] = {}

    def _cleanup(self, now: float) -> None:
        """Drop expired entries; caller must hold self._lock."""
        expired = [s for s, t in self._by_state.items() if t["expires_at"] <= now]
        for s in expired:
            binding = self._by_state.pop(s)["binding"]
            if self._state_by_binding.get(binding) == s:
                del self._state_by_binding[binding]

    def create(self, previous_binding: str | None) -> dict[str, Any]:
        """Mint a transaction; the previous one of THIS browser (same
        binding cookie value) is invalidated first."""
        with self._lock:
            now = _now()
            self._cleanup(now)
            if previous_binding:
                old_state = self._state_by_binding.pop(previous_binding, None)
                if old_state is not None:
                    self._by_state.pop(old_state, None)
            # Hard memory cap: at cap, evict the oldest entry (by expires_at)
            # so a flood of /auth/login requests can never grow the store.
            if len(self._by_state) >= MAX_TRANSACTIONS:
                oldest = min(
                    self._by_state, key=lambda s: self._by_state[s]["expires_at"]
                )
                evicted = self._by_state.pop(oldest)
                if self._state_by_binding.get(evicted["binding"]) == oldest:
                    del self._state_by_binding[evicted["binding"]]
            state = secrets.token_urlsafe(32)
            txn = {
                "state": state,
                "nonce": secrets.token_urlsafe(32),
                "verifier": secrets.token_urlsafe(64),  # ≥43 chars for S256 PKCE
                "binding": secrets.token_urlsafe(32),
                "expires_at": now + TRANSACTION_TTL_S,
            }
            self._by_state[state] = txn
            self._state_by_binding[txn["binding"]] = state
            return txn

    def consume(self, state: str | None, binding: str | None) -> dict[str, Any] | None:
        """Atomically pop the transaction; None unless state AND the
        browser-binding cookie value both match and it is unexpired."""
        with self._lock:
            now = _now()
            self._cleanup(now)
            if not isinstance(state, str) or not isinstance(binding, str):
                return None
            txn = self._by_state.pop(state, None)  # single-use, always cleared
            if txn is None:
                return None
            if self._state_by_binding.get(txn["binding"]) == state:
                del self._state_by_binding[txn["binding"]]
            if not hmac.compare_digest(txn["binding"], binding):
                return None
            if txn["expires_at"] <= now:
                return None
            return txn


class OidcClient:
    """Provider client: discovery (exact issuer match), authorize URL,
    code exchange, cached JWKS, id_token validation."""

    def __init__(
        self, cfg, client_secret: str, transport: httpx.BaseTransport | None = None
    ):
        self.cfg = cfg
        self.client_secret = client_secret
        # Testability seam: None → httpx default transport (production);
        # tests inject httpx.MockTransport here.
        self._transport = transport
        self._discovery: dict[str, Any] | None = None
        self._discovery_at: float = 0.0
        self._jwks: dict[str, Any] | None = None
        self._jwks_at: float = 0.0

    # ---------- discovery ----------

    def _fetch_json(self, url: str) -> dict[str, Any]:
        try:
            with httpx.Client(
                timeout=HTTP_TIMEOUT_S,
                follow_redirects=False,
                verify=True,
                transport=self._transport,
            ) as http:
                with http.stream("GET", url) as resp:
                    if resp.status_code != 200:
                        raise OidcError("auth_failed")
                    return _parse_json_object(_bounded_read(resp))
        except httpx.HTTPError:
            raise OidcError("auth_failed") from None

    def discovery(self) -> dict[str, Any]:
        now = _now()
        if self._discovery is not None and now - self._discovery_at < DISCOVERY_CACHE_TTL_S:
            return self._discovery
        issuer = self.cfg.issuer.rstrip("/")
        doc = self._fetch_json(f"{issuer}/.well-known/openid-configuration")
        # Exact issuer match: a discovery document from anywhere else must
        # never feed endpoints into the flow.
        if doc.get("issuer") != self.cfg.issuer:
            raise OidcError("auth_failed")
        for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            value = doc.get(key)
            if not isinstance(value, str) or not value.startswith(("http://", "https://")):
                raise OidcError("auth_failed")
        self._discovery = doc
        self._discovery_at = now
        return doc

    # ---------- authorize ----------

    def authorize_url(self, txn: dict[str, Any]) -> str:
        from urllib.parse import urlencode

        params = {
            "response_type": "code",
            "client_id": self.cfg.client_id,
            "redirect_uri": self.cfg.callback_url,  # config only, never headers
            "scope": " ".join(self.cfg.scopes),
            "state": txn["state"],
            "nonce": txn["nonce"],
            "code_challenge": _pkce_pair(txn["verifier"]),
            "code_challenge_method": "S256",
        }
        return f"{self.discovery()['authorization_endpoint']}?{urlencode(params)}"

    # ---------- token exchange ----------

    def exchange_code(self, code: str, verifier: str) -> dict[str, Any]:
        doc = self.discovery()
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.cfg.callback_url,
            "client_id": self.cfg.client_id,
            "client_secret": self.client_secret,
            "code_verifier": verifier,
        }
        try:
            with httpx.Client(
                timeout=HTTP_TIMEOUT_S,
                follow_redirects=False,
                verify=True,
                transport=self._transport,
            ) as http:
                with http.stream(
                    "POST",
                    doc["token_endpoint"],
                    data=form,
                    headers={"content-type": "application/x-www-form-urlencoded"},
                ) as resp:
                    if resp.status_code != 200:
                        raise OidcError("auth_failed")
                    tokens = _parse_json_object(_bounded_read(resp))
        except httpx.HTTPError:
            raise OidcError("auth_failed") from None
        if not isinstance(tokens.get("id_token"), str) or not tokens["id_token"]:
            raise OidcError("auth_failed")
        return tokens

    # ---------- JWKS ----------

    def _jwk_for_kid(self, kid: str) -> dict[str, Any] | None:
        now = _now()
        if self._jwks is None or now - self._jwks_at >= DISCOVERY_CACHE_TTL_S:
            self._refresh_jwks()
        for jwk in self._jwks.get("keys", []):
            if jwk.get("kid") == kid:
                return jwk
        return None

    def _refresh_jwks(self) -> None:
        uri = self.discovery()["jwks_uri"]
        self._jwks = self._fetch_json(uri)
        self._jwks_at = _now()

    def _signing_key(self, id_token: str):
        try:
            header = jwt.get_unverified_header(id_token)
        except jwt.PyJWTError:
            raise OidcError("auth_failed") from None
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            raise OidcError("auth_failed")
        jwk = self._jwk_for_kid(kid)
        if jwk is None:
            # Unknown kid → exactly one controlled refresh (key rotation);
            # still unresolved → reject.
            self._refresh_jwks()
            jwk = next(
                (k for k in self._jwks.get("keys", []) if k.get("kid") == kid), None
            )
        if jwk is None:
            raise OidcError("auth_failed")
        try:
            return jwt.PyJWK(jwk).key
        except (jwt.PyJWTError, ValueError, TypeError):
            raise OidcError("auth_failed") from None

    # ---------- id_token validation ----------

    def validate_id_token(self, id_token: str, nonce: str) -> dict[str, Any]:
        key = self._signing_key(id_token)
        try:
            # algorithms is a hard allowlist — the token header alg is never
            # consulted for algorithm selection (confusion attacks).
            claims = jwt.decode(
                id_token,
                key,
                algorithms=ALLOWED_ALGS,
                audience=self.cfg.client_id,
                issuer=self.cfg.issuer,  # exact match
                leeway=CLOCK_SKEW_S,
                options={"require": ["exp", "iat", "sub"]},
            )
        except jwt.PyJWTError:
            raise OidcError("auth_failed") from None
        # azp: when present it must name this client; multi-aud tokens
        # (aud list > 1) MUST carry a matching azp.
        azp = claims.get("azp")
        aud = claims.get("aud")
        if azp is not None and azp != self.cfg.client_id:
            raise OidcError("auth_failed")
        if isinstance(aud, list) and len(aud) > 1 and azp != self.cfg.client_id:
            raise OidcError("auth_failed")
        # nonce binds the id_token to THIS login transaction.
        token_nonce = claims.get("nonce")
        if not isinstance(token_nonce, str) or not hmac.compare_digest(token_nonce, nonce):
            raise OidcError("auth_failed")
        sub = claims.get("sub")
        if not isinstance(sub, str) or not sub:
            raise OidcError("auth_failed")
        return claims

    def check_groups(self, claims: dict[str, Any]) -> bool:
        """groups claim MUST be a list of strings containing exactly the
        configured required_group — missing/malformed/non-member = reject."""
        groups = claims.get("groups")
        if not isinstance(groups, list):
            return False
        if not all(isinstance(g, str) for g in groups):
            return False
        return self.cfg.required_group in groups


def _parse_json_object(data: bytes) -> dict[str, Any]:
    try:
        doc = json.loads(data)
    except (ValueError, UnicodeDecodeError):
        raise OidcError("auth_failed") from None
    if not isinstance(doc, dict):
        raise OidcError("auth_failed")
    return doc


def register_oidc_routes(
    app, settings, client_secret: str, db, transport: httpx.BaseTransport | None = None
) -> None:
    """Wire GET /auth/login + GET /auth/callback onto the FastAPI app.

    Called from create_app (API construction): the client secret is resolved
    by the caller BEFORE this point, so a missing production secret fails
    closed at boot. `db` is create_app's session dependency. `transport` is
    a testability seam (None → httpx default, production behavior). The
    transaction store is process-local — same single-process assumption as
    the in-memory rate-limit middleware.
    """
    from fastapi import Depends, Request
    from fastapi.responses import RedirectResponse
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.orm import Session

    from app.api.app import get_secret
    from app.api.auth import sign_token
    from app.db.models import User

    cfg = settings.api.oidc
    client = OidcClient(cfg, client_secret, transport=transport)
    store = TransactionStore()
    bind_cookie = f"{settings.api.cookie_name}_bind"

    def _cookie_kwargs() -> dict:
        # One attribute set for set AND delete (delete must match the set
        # attributes — Secure/SameSite/Path — for the browser to drop it).
        return {
            "path": "/",
            "secure": settings.api.cookie_secure,
            "httponly": True,
            "samesite": "lax",
        }

    def _fail(code: str) -> RedirectResponse:
        """Fail-closed redirect: allowlisted code only, no provider text,
        transaction already consumed by the caller."""
        resp = RedirectResponse(f"/?error={code}", status_code=302)
        resp.headers["cache-control"] = "no-store"
        resp.delete_cookie(bind_cookie, **_cookie_kwargs())
        return resp

    @app.get("/auth/login")
    def oidc_login(request: Request):
        # Fail-closed: discovery down / malformed document raises OidcError
        # out of authorize_url() — same allowlisted redirect as every other
        # failure path, never an unhandled 500.
        try:
            txn = store.create(request.cookies.get(bind_cookie))
            authorize = client.authorize_url(txn)
        except OidcError:
            return _fail("provider_error")
        resp = RedirectResponse(authorize, status_code=302)
        resp.headers["cache-control"] = "no-store"
        resp.set_cookie(
            bind_cookie, txn["binding"], max_age=BINDING_COOKIE_TTL_S, **_cookie_kwargs()
        )
        return resp

    @app.get("/auth/callback")
    def oidc_callback(
        request: Request,
        session: Session = Depends(db),
        state: str = "",
        code: str = "",
        error: str = "",
    ):
        # Atomic single-use consume FIRST: every path below — success or
        # failure — burns the transaction, so replays are dead on arrival.
        txn = store.consume(state or None, request.cookies.get(bind_cookie))
        if error:
            # Provider signalled a failure; the text is never reflected.
            return _fail("provider_error")
        if txn is None:
            return _fail("state_mismatch")
        if not code:
            return _fail("provider_error")
        try:
            tokens = client.exchange_code(code, txn["verifier"])
            claims = client.validate_id_token(tokens["id_token"], txn["nonce"])
        except OidcError:
            return _fail("provider_error")
        if not client.check_groups(claims):
            return _fail("group_denied")
        email = claims.get("email")
        if not isinstance(email, str) or not email:
            return _fail("auth_failed")
        sub = claims["sub"]

        # JIT provisioning: exact (issuer, sub) match ONLY — never email or
        # username linking. No DB write happened before this point.
        user = (
            session.query(User)
            .filter(User.oidc_issuer == cfg.issuer, User.oidc_sub == sub)
            .first()
        )
        if user is None:
            preferred = claims.get("preferred_username")
            username = (
                preferred
                if isinstance(preferred, str) and preferred
                else email.split("@", 1)[0]
            )
            if (
                not username
                or USERNAME_RE.fullmatch(username) is None
                or session.query(User).filter_by(username=username).first() is not None
            ):
                # Invalid (spec-005 rule) or colliding username → reject:
                # no linking, no suffixing.
                return _fail("username_taken")
            cap = settings.admin.max_players
            if cap > 0 and session.query(User).count() >= cap:
                return _fail("registration_closed")
            user = User(
                username=username,
                email=email,
                role="user",
                created_at=0,
                oidc_issuer=cfg.issuer,
                oidc_sub=sub,
            )
            session.add(user)
            try:
                session.commit()  # unique (issuer, sub)/(username/email) backstop
            except IntegrityError:
                session.rollback()
                return _fail("auth_failed")
        if user.disabled:
            return _fail("auth_failed")

        # Success: the app's OWN HMAC session cookie (never a provider token).
        resp = RedirectResponse("/", status_code=302)  # fixed target, never user-supplied
        resp.headers["cache-control"] = "no-store"
        token = sign_token(
            user.id,
            user.role,
            get_secret(settings),
            settings.api.session_ttl_min,
            user.session_generation,
        )
        resp.set_cookie(
            settings.api.cookie_name,
            token,
            max_age=settings.api.session_ttl_min * 60,
            **_cookie_kwargs(),
        )
        resp.delete_cookie(bind_cookie, **_cookie_kwargs())
        return resp
