"""#179 — client-side WS cursor handshake byte-pins (m162/m176 house style).

The client half of #179: after the first successful REST events fetch has
defined S.cursor (feedFetched), the client declares that cursor to the server
right after the WS opens — sendWsCursor(), one handshake per connection,
re-armed in ws.onclose so every new connection re-declares its position.
Server side: /ws honors the declared cursor as the poll START (see
test_ws_cursor_handshake.py).

The #177/#178 defense-in-depth literals (bootCursor replay gate, the
CONTROL_CHANGED liveness gate) are re-asserted here byte-identically —
tests/m176 is the owning pin file and stays untouched; the two key literals
are duplicated below per the work order.
"""

import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
APP_JS = os.path.join(ROOT, "backend/app/web/app.js")
WS_PY = os.path.join(ROOT, "backend/app/api/ws.py")

JS = ""
WS_PY_TEXT = ""

HELPER_DEF = (
    "// #179: tell the server where our event stream should start — right after\n"
    "// hello it replays from 0 unless the client declares its cursor. One\n"
    "// handshake per connection: re-armed in ws.onclose, armed once the first\n"
    "// REST events fetch has defined S.cursor (boot boundary).\n"
    "function sendWsCursor() {"
)

ONOPEN_LINE = "ws.onopen = () => { S.wsTries = 0; sendWsCursor(); };"

# Mirrored verbatim from tests/m176 (frozen): the #178 CONTROL_CHANGED
# liveness gate must survive this lane byte-identically.
CONTROL_CHANGED_GATE_BLOCK = (
    '          if (fresh.some((e) => e.event_type === "CONTROL_CHANGED"\n'
    "              && e.id > S.bootCursor\n"
    '              && S.world && typeof e.game_timestamp === "number"\n'
    "              && e.game_timestamp >= S.world.game_timestamp - 15)\n"
    '            && location.hash === "#/world") {\n'
    "            viewWorld();\n"
    "          }"
)


def setup_module(module):
    global JS, WS_PY_TEXT
    with open(APP_JS, "r", encoding="utf-8") as f:
        JS = f.read()
    with open(WS_PY, "r", encoding="utf-8") as f:
        WS_PY_TEXT = f.read()


# ---------- the handshake helper ----------

def test_send_ws_cursor_helper_pins():
    # single definition, with the #179 rationale comment attached
    assert JS.count("function sendWsCursor()") == 1
    assert JS.count(HELPER_DEF) == 1
    # the whole handshake is this one declared frame
    assert JS.count('{ type: "cursor", id: S.cursor }') == 1
    # helper sits before its callers (immediately before connectWs)
    helper = JS.index("function sendWsCursor() {")
    connect_ws = JS.index("function connectWs() {")
    assert helper < connect_ws
    # guard shape: armed only after the first REST fetch, once per connection,
    # and only while the socket is actually open
    assert JS.count("if (!S.feedFetched || S.wsCursorSent) return;") == 1
    assert JS.count("if (!S.ws || S.ws.readyState !== 1) return;") == 1


# ---------- exactly two call sites ----------

def test_handshake_call_sites():
    # one in ws.onopen, one at the end of the pollEventsFeed success path
    assert JS.count("sendWsCursor();") == 2
    assert JS.count(ONOPEN_LINE) == 1
    # the pollEventsFeed tail runs on EVERY successful fetch completion:
    # it sits after the #177 boot capture, at the end of the function body
    # (before connectWs), and the two statements are adjacent
    poll_def = JS.index("async function pollEventsFeed() {")
    capture = JS.index("if (S.bootCursor === Infinity) {")
    feed_fetched = JS.index("S.feedFetched = true;")
    connect_ws = JS.index("function connectWs() {")
    assert poll_def < capture < feed_fetched < connect_ws
    assert JS.count("S.feedFetched = true;") == 1
    assert JS.count("S.feedFetched = true;\n  sendWsCursor();") == 1


# ---------- state + once-per-connection re-arm ----------

def test_ws_cursor_state_and_rearm_pins():
    # S-init fields exist exactly once. Placement note: they ride BEFORE
    # shopConfirm/chatDraft so the #175/#177 init tail (pinned byte-exact by
    # m173/m175/m176 — m175/m176 frozen) keeps bootCursor as the LAST property.
    assert JS.count("wsCursorSent: false") == 1
    assert JS.count("feedFetched: false") == 1
    # the re-arm occurs EXACTLY twice, once per close path:
    #   (a) stopFeed() — the #161 detach (w.onclose = null BEFORE close) means
    #       onclose never fires for detached sockets, so the flag re-arms there;
    #   (b) ws.onclose — regular remote closes / reconnect churn.
    assert JS.count("S.wsCursorSent = false;") == 2
    # (a) stopFeed: inside the `if (S.ws)` branch, strictly BETWEEN the #161
    # detach line and the close call (detach order itself untouched).
    sf_start = JS.index("function stopFeed() {")
    sf_branch = JS.index("if (S.ws) {", sf_start)
    sf_end = JS.index("\n  }\n", sf_branch)
    stopfeed_body = JS[sf_branch:sf_end]
    assert "S.wsCursorSent = false;" in stopfeed_body
    detach = JS.index("w.onclose = null;", sf_branch)
    rearm_sf = JS.index("S.wsCursorSent = false;", sf_branch)
    close_call = JS.index("w.close()", sf_branch)
    assert detach < rearm_sf < close_call
    # (b) onclose: inside the onclose body (m175 slices this block for the
    # banner negatives — the added line touches none of those literals)...
    oc_start = JS.index("ws.onclose = () => {")
    oc_end = JS.index("\n  };", oc_start)
    assert "S.wsCursorSent = false;" in JS[oc_start:oc_end]
    # ...and precedes the reconnect scheduling inside onclose
    rearm_oc = JS.index("S.wsCursorSent = false;", oc_start)
    assert rearm_oc < JS.index("setTimeout(connectWs", oc_start)


# ---------- server-side #179 pins (backend/app/api/ws.py) ----------

def test_179_ws_py_handshake_pins():
    # F2: the handshake clamp must catch OverflowError (JSON 1e400 -> inf) —
    # garbage input clamps to 0, it must never kill the connection.
    assert WS_PY_TEXT.count("except (TypeError, ValueError, OverflowError):") == 1
    # the handshake window constant is declared exactly once (tests patch it)
    assert WS_PY_TEXT.count("WS_CURSOR_HANDSHAKE_TIMEOUT_S = None") == 1


# ---------- #177/#178 defense-in-depth literals unchanged ----------

def test_178_defense_in_depth_literals_unchanged():
    # bootCursor census identical to m176: 1 init field + 5 S. sites
    assert JS.count("bootCursor") == 6
    assert JS.count("S.bootCursor") == 5
    assert JS.count("bootCursor: Infinity") == 1
    # the CONTROL_CHANGED liveness gate keeps its exact bytes (m176 owns it)
    assert JS.count(CONTROL_CHANGED_GATE_BLOCK) == 1
    assert JS.count('e.event_type === "CONTROL_CHANGED"') == 1
    assert JS.count('location.hash === "#/world"') == 1
