"""Session-token primitives (HMAC-signed, app's OWN cookie only).

OIDC SSO note: these functions sign/verify the application session cookie
exclusively. OIDC id_tokens are validated by app.api.oidc with PyJWT —
never feed a provider token into verify_token.
"""

import base64
import hashlib
import hmac
import json
import time

# Session-token format marker: v2 payloads carry `v: 2` + `gen` (the user's
# session_generation); legacy v1 tokens (no marker) are rejected by
# current_user so wiped/legacy sessions cannot authenticate.
SESSION_TOKEN_VERSION = 2


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def sign_token(
    user_id: int,
    role: str,
    secret: str,
    ttl_min: int,
    generation: str,
    now: int | None = None,
) -> str:
    """JWT-like: b64url(header).b64url(payload).b64url(hmac_sha256)"""
    issued = int(time.time() if now is None else now)
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": user_id,
        "role": role,
        "v": SESSION_TOKEN_VERSION,
        "gen": generation,
        "iat": issued,
        "exp": issued + ttl_min * 60,
    }
    h = _b64url(json.dumps(header, separators=(",", ":")).encode())
    p = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    sig = hmac.new(secret.encode(), f"{h}.{p}".encode(), hashlib.sha256).digest()
    return f"{h}.{p}.{_b64url(sig)}"


def verify_token(token: str, secret: str, now: int | None = None) -> dict | None:
    """Returns payload dict or None (invalid signature / expired / malformed).

    Signature + expiry only — the caller (current_user / WS) owns the
    format-marker, generation and DB-row checks.
    """
    try:
        h, p, sig = token.split(".")
        expected = hmac.new(secret.encode(), f"{h}.{p}".encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _b64url_decode(sig)):
            return None
        payload = json.loads(_b64url_decode(p))
        if float(payload.get("exp", 0)) < (time.time() if now is None else now):
            return None
        if not isinstance(payload.get("sub"), int):
            return None
        return payload
    except (ValueError, TypeError, json.JSONDecodeError):
        return None
