"""#177 — WS backlog replay must not reload the world view (byte-pin style).

Production evidence (owner report on prod): after a page reload the world
view visibly reloaded 5-7 times. Root cause: on every WS connect the server
replays the ENTIRE event backlog from cursor 0 (ws.py starts at cursor=0,
batch 100/1s), the client's S.cursor starts at 0 after a reload, so every
replayed batch looks "fresh" — and the ws.onmessage #144 block ran a FULL
viewWorld() re-render for every batch containing a CONTROL_CHANGED. The same
replay re-fired onOwnArrival() for historical own-MOVE events (map churn).

Fix (all in backend/app/web/app.js, liveness gate = #177):

1. S initializer grows bootCursor: Infinity as the LAST property — the
   replay gate: Infinity = "no live events until proven".
2. pollEventsFeed(): the boot cursor is captured (once-only) from the REST
   RESPONSE events — not from `fresh`/S.feed. The race this prevents: the
   first WS batch (same ids, ws.py replays from cursor 0) can be appended
   before the REST response is processed, emptying the dedup set; a
   feed-based capture would then never run and the liveness gates would
   reject ALL events (replay AND live) for the whole session. A fresh world
   with zero events at page load pins the boundary to zero instead
   (everything from id 1 on is live).
3. ws.onmessage: the CONTROL_CHANGED world-view refresh now reacts only to
   LIVE events — id beyond S.bootCursor AND logged within 15 game-minutes
   of the current world time (covers backlogs larger than the REST fetch
   window). Byte-pin constraints: `e.event_type === "CONTROL_CHANGED"` and
   `location.hash === "#/world"` each occur exactly once file-wide.
4. ws.onmessage: the onOwnArrival hook carries the same liveness gate, so
   historical own-MOVE replay cannot churn the map. The REST fallback path
   keeps its ungated hook (it cannot fire while the socket is live — the
   early-return guard below is pinned intact).

Pin tests (this file, following the m162 house style): exact literal counts,
never weakened; the OLD unconditional refresh shape is pinned == 0 so the
backlog-reload bug cannot silently return.
"""

import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
APP_JS = os.path.join(ROOT, "backend/app/web/app.js")

JS = ""

# (a) the replay gate field, appended as the LAST S property (#175 tail + #177).
BOOT_CURSOR_INIT_TAIL = 'chatDraft: "",\n  bootCursor: Infinity };'

# (b) the once-only boot cursor capture inside pollEventsFeed — captured
# from the REST RESPONSE `events` (4-space indent, inside `if (events.length)`,
# strictly AFTER the whole `if (fresh.length)` block).
BOOT_CAPTURE_BLOCK = (
    "    // #177: the replay boundary is captured from the REST RESPONSE, not\n"
    "    // from `fresh` — the first WS batch (same ids, server replays from\n"
    "    // cursor 0) can win the race and empty the dedup set, but the response\n"
    "    // still defines where replay ends. `events` is bounded by the server\n"
    "    // limit (100), so the spread is safe.\n"
    "    if (S.bootCursor === Infinity) {\n"
    "      S.bootCursor = Math.max(...events.map((e) => e.id));\n"
    "    }"
)

# (b2) the zero-events fallback — a fresh world with no events at page load
# still pins the boundary (zero = everything from id 1 on is live).
FALLBACK_BLOCK = (
    "  } else if (S.cursor === 0) {\n"
    "    // #177: fresh world with no events yet — everything from id 1 on is\n"
    "    // live; the boundary is zero.\n"
    "    S.bootCursor = 0;\n"
    "  }"
)

# (c) the amended CONTROL_CHANGED refresh: live-only (id gate + timestamp
# window), still hash-gated to #/world (10-space if-line, 14-space continuations).
CONTROL_CHANGED_GATE_BLOCK = (
    '          if (fresh.some((e) => e.event_type === "CONTROL_CHANGED"\n'
    "              && e.id > S.bootCursor\n"
    '              && S.world && typeof e.game_timestamp === "number"\n'
    "              && e.game_timestamp >= S.world.game_timestamp - 15)\n"
    '            && location.hash === "#/world") {\n'
    "            viewWorld();\n"
    "          }"
)

# (d) the OLD unconditional shape — refresh fired for ANY CONTROL_CHANGED,
# including backlog replay. Must stay gone.
OLD_UNCONDITIONAL_BLOCK = (
    'e.event_type === "CONTROL_CHANGED")\n'
    '            && location.hash === "#/world") {\n'
    "            viewWorld();"
)

# (e) the onOwnArrival WS hook carries the same liveness gate (single line).
OWN_ARRIVAL_GATE_BLOCK = (
    "          if (fresh.some((e) => isOwnMoveEvent(e)\n"
    "              && e.id > S.bootCursor\n"
    '              && S.world && typeof e.game_timestamp === "number"\n'
    "              && e.game_timestamp >= S.world.game_timestamp - 15)) onOwnArrival();"
)

# (f) the early-return WS guard in pollEventsFeed — must not be weakened.
POLL_EARLY_RETURN = "if (S.ws && S.ws.readyState === 1) return;"


def setup_module(module):
    global JS
    with open(APP_JS, "r", encoding="utf-8") as f:
        JS = f.read()


# ---------- (a) the S initializer carries the replay gate ----------

def test_boot_cursor_init_pin():
    # The gate field exists exactly once, as the LAST property (after the
    # #175 chatDraft tail — same single-line tail convention).
    assert JS.count("bootCursor: Infinity") == 1
    assert JS.count(BOOT_CURSOR_INIT_TAIL) == 1
    # Whole-file occurrence census (round-2 recount): 1 init field (no S.
    # prefix) + 5 S. sites (REST capture guard, REST capture assign,
    # zero-events fallback assign, CONTROL_CHANGED gate, own-arrival gate)
    # => 6 total, 5 S.-prefixed. There is no separate fallback guard — the
    # fallback is `} else if (S.cursor === 0) {` + a bare assign.
    assert JS.count("bootCursor") == 6
    assert JS.count("S.bootCursor") == 5


# ---------- (b) the boot cursor is captured from the first REST fill ----------

def test_boot_capture_in_poll_events_feed():
    # The capture block exists exactly once, INSIDE pollEventsFeed.
    assert JS.count(BOOT_CAPTURE_BLOCK) == 1
    poll_def = JS.index("async function pollEventsFeed() {")
    connect_ws = JS.index("function connectWs() {")
    capture = JS.index(BOOT_CAPTURE_BLOCK)
    assert poll_def < capture < connect_ws
    # ...and AFTER the dedup push (the response is the source of truth).
    push_rest = JS.index("S.feed.push(...fresh)", poll_def)
    assert push_rest < capture
    # Anti-regression (the round-2 race fix): the capture sits OUTSIDE the
    # `if (fresh.length)` block — its guard must come strictly after the
    # LAST line of the fresh block (the ungated own-arrival hook). If the
    # capture were moved back inside `if (fresh.length)`, the first WS
    # batch (same ids, ws.py replays from cursor 0) could win the race,
    # empty the dedup set, and the boundary would never be captured — the
    # liveness gates would then reject ALL events (replay AND live) for
    # the whole session.
    fresh_tail = JS.index(
        "      if (fresh.some((e) => isOwnMoveEvent(e))) onOwnArrival();")
    guard = JS.index("if (S.bootCursor === Infinity) {")
    assert fresh_tail < guard
    # Once-only semantics: the guard form occurs exactly once file-wide.
    assert JS.count("if (S.bootCursor === Infinity) {") == 1
    # The capture reads the RESPONSE events, not the buffer...
    assert JS.count(
        "S.bootCursor = Math.max(...events.map((e) => e.id));") == 1
    # ...and the old feed-based form is gone (it raced with the first WS batch).
    assert JS.count("Math.max(...S.feed.map((e) => e.id))") == 0
    # Fresh-world fallback: zero events at page load => boundary is zero.
    assert JS.count(FALLBACK_BLOCK) == 1
    assert JS.count("} else if (S.cursor === 0) {") == 1
    assert JS.count("S.bootCursor = 0;") == 1


# ---------- (c)+(d) the world-view refresh is live-only ----------

def test_control_changed_liveness_gate():
    # The amended block exists exactly once (m147 pins the same bytes).
    assert JS.count(CONTROL_CHANGED_GATE_BLOCK) == 1
    block = JS[JS.index(CONTROL_CHANGED_GATE_BLOCK):]
    # All three gate clauses ride inside the condition.
    assert "e.id > S.bootCursor" in block
    assert 'typeof e.game_timestamp === "number"' in block
    assert "e.game_timestamp >= S.world.game_timestamp - 15" in block
    assert 'location.hash === "#/world"' in block
    # Byte-pin constraints (m147/m175 mirror): each singleton literal stays
    # exactly once file-wide — no second refresh site, no second hash gate.
    assert JS.count('e.event_type === "CONTROL_CHANGED"') == 1
    assert JS.count('location.hash === "#/world"') == 1
    # The OLD unconditional shape (no id gate) is gone — the backlog-reload
    # bug cannot return silently.
    assert JS.count(OLD_UNCONDITIONAL_BLOCK) == 0


# ---------- (e) own-MOVE replay cannot churn the map ----------

def test_own_arrival_liveness_gate():
    # The WS hook is gated identically to the CONTROL_CHANGED refresh.
    assert JS.count(OWN_ARRIVAL_GATE_BLOCK) == 1
    connect_ws = JS.index("function connectWs() {")
    cc_gate = JS.index(CONTROL_CHANGED_GATE_BLOCK)
    own_gate = JS.index(OWN_ARRIVAL_GATE_BLOCK)
    assert connect_ws < cc_gate < own_gate
    # The REST fallback keeps its ungated hook on purpose: it early-returns
    # while the socket is live (see (f)), so its "fresh" set can only be
    # REST-fallback deliveries (exactly 1 site — the WS one was rewritten).
    assert JS.count(
        "if (fresh.some((e) => isOwnMoveEvent(e))) onOwnArrival();") == 1


# ---------- (f) the WS-liveness early return stays intact ----------

def test_poll_events_feed_ws_guard_intact():
    # The REST fallback must keep short-circuiting while the WS is the live
    # channel (m139 pins it too; duplicated here so #177 cannot weaken it).
    assert JS.count(POLL_EARLY_RETURN) == 1
    poll_def = JS.index("async function pollEventsFeed() {")
    rest_fetch = JS.index("const data = await api(`/world/events?since=")
    assert poll_def < JS.index(POLL_EARLY_RETURN, poll_def) < rest_fetch
