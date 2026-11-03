"""#258 — client-side WS boot state machine byte-pins (m162/m176 house style).

The client half of #258: a fresh page session waits for the WS hello
(snapshot tail) before trusting the socket. The hello handler settles the
page-lifetime feed-init state ("pending" -> "done"), starts the stream at
tail-100, marks the boot replay boundary at the TAIL, and immediately
declares the cursor. A 5s hello deadline (armHelloDeadline) retires a
hello-less socket and permanently selects the REST fallback ("rest") for the
page session. The REST events fetch is suppressed while init is pending and
can never lower a cursor the WS already advanced.

Byte-pinned here (read backend/app/web/app.js as text, JS.count/JS.index):
the S-init feedInit fields, the hello-handler block, the armHelloDeadline
callback, the pollEventsFeed pending gate, the REST monotonic cursor guard,
and the onclose connect-failure fallback. The #179 sendWsCursor census and
the #177/#178 bootCursor/liveness literals are owned by
tests/m179/test_ws_cursor_client_pins.py and tests/m176 — not re-pinned here.
"""

import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
APP_JS = os.path.join(ROOT, "backend/app/web/app.js")

JS = ""

# The #258 hello-handler block in ws.onmessage — byte-exact. It settles the
# page-lifetime init, starts the stream 100 below the tail, marks the boot
# replay boundary at the TAIL, clears the deadline, and declares the cursor.
HELLO_HANDLER_BLOCK = (
    '      if (data.type === "hello" && S.feedInit === "pending"\n'
    '          && Number.isSafeInteger(data.cursor) && data.cursor >= 0) {\n'
    "        S.cursor = Math.max(0, data.cursor - 100);\n"
    "        S.bootCursor = data.cursor;  // the TAIL, not the window start\n"
    "        S.feedFetched = true;\n"
    '        S.feedInit = "done";\n'
    "        if (S.helloDeadline) { clearTimeout(S.helloDeadline); S.helloDeadline = null; }\n"
    "        sendWsCursor();\n"
    "      }"
)

# The onclose connect-failure fallback — a socket that died before hello
# permanently selects the REST fallback for the page session.
ONCLOSE_FALLBACK_BLOCK = (
    '    if (S.feedInit === "pending") {\n'
    "      // #258: connect failure before hello — the REST fallback is\n"
    "      // permanent for this page session (never reset by navigation).\n"
    '      S.feedInit = "rest";\n'
    "    }"
)


def setup_module(module):
    global JS
    with open(APP_JS, "r", encoding="utf-8") as f:
        JS = f.read()


# ---------- the S initializer carries the page-lifetime feed-init state ----------

def test_feed_init_s_init_pin():
    # The #258 feed-init fields exist exactly once, as a single line...
    assert JS.count('feedInit: "pending", helloDeadline: null,') == 1
    # ...positioned BEFORE shopConfirm (the #175/#177 init tail keeps
    # bootCursor as the LAST property, pinned byte-exact by m173/m175/m176).
    assert JS.index('feedInit: "pending", helloDeadline: null,') < JS.index(
        "shopConfirm: null"
    )


# ---------- the hello handler settles the init and declares the cursor ----------

def test_hello_handler_block_pin():
    # The whole hello-handler block exists exactly once, byte-exact.
    assert JS.count(HELLO_HANDLER_BLOCK) == 1
    # Key lines individually (defense in depth against a partial edit).
    assert JS.count(
        'if (data.type === "hello" && S.feedInit === "pending"') == 1
    assert JS.count("S.cursor = Math.max(0, data.cursor - 100);") == 1
    assert JS.count(
        "S.bootCursor = data.cursor;  // the TAIL, not the window start") == 1
    assert JS.count('S.feedInit = "done";') == 1
    # The handler sits inside ws.onmessage (before the events frame).
    onmessage = JS.index("ws.onmessage = (msg) => {")
    events_frame = JS.index('if (data.type === "events" && data.events) {')
    hello = JS.index(HELLO_HANDLER_BLOCK)
    assert onmessage < hello < events_frame


# ---------- the 5s hello deadline is socket-scoped ----------

def test_arm_hello_deadline_pin():
    # Defined exactly once.
    assert JS.count("function armHelloDeadline(ws) {") == 1
    # The callback re-checks socket identity + init state exactly once — a
    # stale timer can never close/clear a replacement socket.
    assert JS.count(
        'if (S.feedInit !== "pending" || S.ws !== ws) return;') == 1
    # The deadline expiry permanently selects the REST fallback, exactly once.
    assert JS.count(
        'S.feedInit = "rest";  // permanent for this page session') == 1
    # The identity re-check and the "rest" settle both live inside the
    # armHelloDeadline callback.
    fn = JS.index("function armHelloDeadline(ws) {")
    fn_end = JS.index("\n}", fn)
    assert fn < JS.index(
        'if (S.feedInit !== "pending" || S.ws !== ws) return;') < fn_end
    assert fn < JS.index(
        'S.feedInit = "rest";  // permanent for this page session') < fn_end


# ---------- the REST events fetch is suppressed while init is pending ----------

def test_poll_events_feed_pending_gate_pin():
    # The pending gate exists exactly once...
    assert JS.count('if (S.feedInit === "pending") return;') == 1
    # ...and sits BEFORE the WS-liveness OPEN guard (the pending gate must
    # short-circuit before the socket-liveness check).
    poll_def = JS.index("async function pollEventsFeed() {")
    pending_gate = JS.index('if (S.feedInit === "pending") return;')
    open_guard = JS.index("if (S.ws && S.ws.readyState === 1) return;")
    assert poll_def < pending_gate < open_guard


# ---------- the REST cursor can never lower a WS-advanced cursor ----------

def test_rest_monotonic_cursor_guard_pin():
    # The monotonic guard exists exactly once (a WS batch may advance
    # S.cursor while the REST fetch is in flight).
    assert JS.count(
        "S.cursor = Math.max(S.cursor, events[events.length - 1].id);") == 1


# ---------- onclose connect-failure selects the REST fallback ----------

def test_onclose_connect_failure_fallback_pin():
    # The onclose fallback block exists exactly once, byte-exact...
    assert JS.count(ONCLOSE_FALLBACK_BLOCK) == 1
    # ...and sits inside the ws.onclose handler.
    oc_start = JS.index("ws.onclose = () => {")
    oc_end = JS.index("\n  };", oc_start)
    assert oc_start < JS.index(ONCLOSE_FALLBACK_BLOCK) < oc_end
