"""Issue #114 pins — security headers in the Caddy template.

RED-first pins for the deploy/Caddyfile template change that ships the
security-header target state from issue #114 (HSTS / XFO / nosniff /
Referrer-Policy / CSP). The live rollout is phased and starts CSP in
Report-Only (sysadmin handoff); this PR syncs the repo template only.

The CSP is pinned as a full literal: script-src stays 'self' (no
unsafe-inline/unsafe-eval), style-src keeps 'unsafe-inline' (documented:
18 setAttribute("style") call sites force it — a future el() CSSOM
refactor drops it), img-src allows data:/blob: (favicon + portraits),
connect-src allows ws:/wss: (same-host WebSocket), and the hardening
directives (frame-ancestors / base-uri / form-action / object-src) hold.

Regression guards pin the untouched template invariants: the single
reverse_proxy 127.0.0.1:8000 line, the example.com placeholder (domain
substituted at deploy), and the encode gzip line (see deploy/README.md).
"""
import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
CADDYFILE = os.path.join(ROOT, "deploy", "Caddyfile")

CSP = (
    'Content-Security-Policy "default-src \'self\'; script-src \'self\'; '
    "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
    "font-src 'self' data:; connect-src 'self' ws: wss:; "
    "object-src 'none'; frame-ancestors 'none'; base-uri 'self'; "
    'form-action \'self\'"'
)


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def test_caddy_header_template_pins():
    """Issue #114: security headers in the Caddy template (HSTS/XFO/nosniff/Referrer-Policy/CSP target state)."""  # noqa: E501
    caddy = _read(CADDYFILE)

    # ---------- each security header line exactly once ----------
    assert caddy.count("-Server") == 1
    assert caddy.count('Strict-Transport-Security "max-age=63072000"') == 1
    assert caddy.count('X-Frame-Options "DENY"') == 1
    assert caddy.count('X-Content-Type-Options "nosniff"') == 1
    assert caddy.count('Referrer-Policy "strict-origin-when-cross-origin"') == 1
    # CSP pinned as the full literal.
    assert caddy.count(CSP) == 1

    # ---------- CSP target state ----------
    assert "script-src 'self'" in CSP
    # no unsafe-inline/unsafe-eval in script-src (style-src 'unsafe-inline'
    # is the documented exception: 18 setAttribute("style") call sites).
    assert "script-src 'self' 'unsafe" not in CSP
    assert "unsafe-eval" not in CSP
    assert "style-src 'self' 'unsafe-inline'" in CSP
    assert "img-src 'self' data: blob:" in CSP
    assert "connect-src 'self' ws: wss:" in CSP
    assert "frame-ancestors 'none'" in CSP
    assert "base-uri 'self'" in CSP
    assert "form-action 'self'" in CSP
    assert "object-src 'none'" in CSP

    # ---------- template invariants unchanged ----------
    assert caddy.count("reverse_proxy 127.0.0.1:8000") == 1
    # placeholder domain: substituted at deploy time (documented)
    assert "example.com {" in caddy
    assert "encode gzip" in caddy
