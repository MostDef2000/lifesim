"""Issue #124 — WS cookie auth pins (no credential in the URL).

RED-first static pins for moving /ws auth off /ws?token= (the query string
is captured verbatim by access logs) onto the httpOnly session cookie that
the same-origin client already carries on the WS upgrade request. /auth/me
stops minting the session-equivalent JWT: the browser never sees a token.

Layers:
- behavioral coverage lives in tests/m5 (AE5''' rewritten to the cookie
  handshake) and tests/m9 (ws_token absence in the /auth/me response);
- this module is the static pin layer: byte pins + the node --check gate
  (m121 subprocess style).
"""
import os
import subprocess

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
WS_PY = os.path.join(ROOT, "backend/app/api/ws.py")
APP_PY = os.path.join(ROOT, "backend/app/api/app.py")
APP_JS = os.path.join(ROOT, "backend/app/web/app.js")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def test_ws_cookie_auth_pins():
    """Issue #124: WS auth via session cookie — no credential in the URL."""
    ws = _read(WS_PY)
    app_py = _read(APP_PY)
    app_js = _read(APP_JS)

    # ---------- absence pins (RED on base) ----------
    # The query-string credential is gone everywhere; the client never
    # learns a session-equivalent token again.
    assert "token: str = Query" not in ws
    assert "ws_token" not in app_py
    assert "?token=" not in app_js
    assert "ws_token" not in app_js

    # ---------- byte pins (m131 regression guards, unchanged contract) ----------
    assert ws.count('    @app.websocket("/ws")') == 1
    # Two failure sites close 4401: cross-origin upgrade rejected, then the
    # cookie/auth check — exactly the two guards, no more.
    assert ws.count("await websocket.close(code=4401)") == 2

    # ---------- cookie auth machinery (ws.py) ----------
    assert "def _session_cookie(" in ws
    assert "_session_cookie(websocket, settings)" in ws
    assert "settings.api.cookie_name" in ws
    assert "SimpleCookie" in ws
    assert "verify_token" in ws
    assert "user.disabled" in ws

    # ---------- client handshake (app.js) ----------
    ws_url = "new WebSocket(`${proto}://${location.host}/ws`)"
    assert ws_url in app_js
    assert app_js.count(ws_url) == 1

    # ---------- syntax gate (m121 subprocess style) ----------
    result = subprocess.run(
        ["node", "--check", APP_JS],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
