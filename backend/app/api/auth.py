"""M5 auth primitives (SPEC §81/104): PBKDF2 password hashing + HMAC session tokens.

Stdlib-only (hashlib/hmac/base64/json/time/os) — no new dependencies.
"""

import base64
import hashlib
import hmac
import json
import os
import re
import time

PBKDF2_ITERATIONS = 100_000
SALT_BYTES = 16
MIN_PASSWORD_LEN = 8


def hash_password(password: str) -> str:
    """Format: pbkdf2_sha256$<iterations>$<salt_b64>$<hash_b64>"""
    if len(password) < MIN_PASSWORD_LEN:
        raise ValueError("password too short")
    salt = os.urandom(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        PBKDF2_ITERATIONS,
        base64.b64encode(salt).decode(),
        base64.b64encode(digest).decode(),
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt_b64, hash_b64 = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(iterations))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def validate_registration(username: str, email: str, password: str, age_confirmed: bool):
    """Returns list of error strings; empty list = valid (R2)."""
    errors = []
    if age_confirmed is not True:
        errors.append("age_confirmed must be true (18+ required)")
    if not re.fullmatch(r"[a-zA-Z0-9_]{3,32}", username or ""):
        errors.append("username must be 3-32 chars [a-zA-Z0-9_]")
    if "@" not in email or "." not in email.split("@")[-1]:
        errors.append("invalid email")
    if len(password) < MIN_PASSWORD_LEN:
        errors.append("password must be at least 8 chars")
    return errors


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def sign_token(user_id: int, role: str, secret: str, ttl_min: int, now: int | None = None) -> str:
    """JWT-like: b64url(header).b64url(payload).b64url(hmac_sha256)"""
    issued = int(time.time() if now is None else now)
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {"sub": user_id, "role": role, "iat": issued, "exp": issued + ttl_min * 60}
    h = _b64url(json.dumps(header, separators=(",", ":")).encode())
    p = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    sig = hmac.new(secret.encode(), f"{h}.{p}".encode(), hashlib.sha256).digest()
    return f"{h}.{p}.{_b64url(sig)}"


def verify_token(token: str, secret: str, now: int | None = None) -> dict | None:
    """Returns payload dict or None (invalid signature / expired / malformed)."""
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
