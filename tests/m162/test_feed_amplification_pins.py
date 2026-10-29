"""#161 — client feed amplification pins (byte-pin style per m-suite).

Production evidence (orchestrator, Playwright on prod): the client poll feed
amplifies traffic — full viewWorld() GET bursts are sent ×2 on entry, and over
a session parallel poll timers leak until the server rate limit (global_rpm
600) trips → 429 → toasts/retry → more navigation → more timers.

Root causes (all in backend/app/web/app.js):

1. Double viewWorld on entry — EXPLICIT DOUBLE-DISPATCH, not a hash quirk:
   several handlers assign `location.hash = "#/world";` AND immediately call
   route() (register, create-character) or viewWorld() (external-trip depart,
   external back, mapLink) in the same tick. The assignment queues a
   hashchange event; the direct call renders viewWorld() right away; when the
   queued hashchange fires, route() renders viewWorld() AGAIN — the second
   full burst. The earlier round-1 theory (same-fragment re-assignment of an
   already-current hash) is behaviorally INERT: per the HTML spec, assigning
   location.hash its current value fires NO hashchange event, so the round-1
   guard `if (location.hash !== "#/world") location.hash = …` eliminated zero
   events and has been REVERTED to the plain assignment (harmless).
   Fix: gotoWorld() — assign the hash (event → single route()) and only call
   viewWorld() directly when the hash is already current (no event will
   fire). The five double-dispatch sites now go through it (or, for the
   external-back button, an equivalent inline check).
2. Leaked poll timers: startFeed() assigned S.pollTimer = setInterval(...)
   with NO guard. viewWorld() calls startFeed() on every render (route, WS
   CONTROL_CHANGED refresh, onOwnArrival, retry buttons, external-trip back
   button) — each call overwrote S.pollTimer while the previous interval kept
   running forever. Timers accumulated with navigation history → request storm.
3. WS close race: stopFeed() nulled S.ws, THEN closed the socket. The old
   socket's async onclose ran later, set S.ws = null again (stomping the
   reference to a newer socket) and scheduled a duplicate connectWs — parallel
   WebSockets delivering the same events → multiplied handling → multiplied
   viewWorld() refreshes.

Fix pins (this file):

1. test_startfeed_idempotent — connectWs(); first, then the guard
   `if (S.pollTimer) return;` (exactly once in the file); a single
   `S.pollTimer = setInterval` creation site (== 1, tightened from <= 1).
2. test_stopfeed_detaches_socket_handlers — the old socket's handlers are
   detached (w.onclose/w.onmessage/w.onopen nulled, in that order) BEFORE
   w.close(); the old onclose null-stomp byte sequence
   `S.ws = null;\n    S.wsTries += 1;` is gone; the round-1 guarded onclose
   cleanup `if (S.ws === ws) S.ws = null;` is pinned == 1 (it was added in
   round 1 but never pinned). `S.ws = null;` occurs at most 2× (stopFeed
   detach + the guarded onclose cleanup).
3. test_no_double_dispatch_navigation (replaces round-1's hash-guard pins) —
   a python line-scan proves NO `route();` call sits on the same line as or
   within the 2 lines after a `location.hash = "#/world";` assignment (== 0);
   POST-EDIT TRUE COUNTS (verified by grep after the round-2 edit):
   `location.hash = "#/world";` == 3 (viewWorld's plain unconditional
   assignment, gotoWorld's else branch, and the substring inside route()'s
   empty-hash normalization — the work order's draft estimate of 4 forgot the
   route() normalization line and counted the since-removed register/create
   sites); `function gotoWorld()` == 1 with body comparison
   `if ("#/world" === location.hash) viewWorld();` == 1 (literal operand
   first — see note in test 3); `gotoWorld();` call sites == 4 (register,
   create-character, viewExternal depart, mapLink — the work order's draft
   text wavered between 3 and 4; the true count is 4); route()'s
   empty-hash normalization line == 1; the round-1 guard byte string == 0
   (revert proof).
4. test_pollevents_inflight_guard — S.pollBusy appears exactly 3× (guard
   return, set-true, finally-false); the initializer field is `pollBusy:
   false` (no "S." prefix inside the object literal), pinned via the
   `pollBusy` total == 4; the early-return form appears exactly once.
5. test_regression_byte_pins_of_b3_survive — a representative subset of the
   B3/resilience byte pins (m158 test_ui_transient_failure_catch_pins) must
   survive the #161 edits untouched. NOTE: the round-1 work order said pin
   `S.sceneActDraft == 5`, but the true byte count of `S.sceneActDraft` is 4
   (pre- AND post-#161-fix) — the m158 oracle pins `sceneActDraft == 5`
   without the prefix (4× S.sceneActDraft + 1× `sceneActDraft: null` init).
   #175 later added the scene-act oninput capture: the counts are now
   sceneActDraft == 6 AND S.sceneActDraft == 5. #175 also raised the
   loadRetryNode call sites 6 → 8 (viewExternal + viewAdmin) and rewrote the
   pollEvents silent catch (failure counting + connection banner) — the old
   byte literal is pinned == 0, the new finally/counter shape is pinned.

No conftest changes; no backend/API/config changes; XSS hygiene untouched
(innerHTML stays == 0).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
APP_JS = os.path.join(ROOT, "backend/app/web/app.js")

JS = ""


def setup_module(module):
    global JS
    with open(APP_JS, "r", encoding="utf-8") as f:
        JS = f.read()


def _double_dispatch_hits(js_lines):
    """#161: explicit double-dispatch scan — `route();` calls on the same
    line as, or within the 2 lines after, a `location.hash = "#/world";`
    assignment (the queued hashchange routes again on top of the direct
    call). Returns (line_no, text) pairs."""
    hits = []
    for i, line in enumerate(js_lines):
        if 'location.hash = "#/world";' not in line:
            continue
        window = [(i, line)]
        window += [(j, js_lines[j]) for j in range(i + 1, min(i + 3, len(js_lines)))]
        for j, text in window:
            if "route();" in text:
                hits.append((j + 1, text.strip()))
    return hits


# ---------- 1. startFeed must never stack a second poll loop ----------

def test_startfeed_idempotent():
    # #161: startFeed() runs on every viewWorld() render (route, WS
    # CONTROL_CHANGED refresh, onOwnArrival, retry buttons, external-trip
    # back button). Without the guard every call overwrote S.pollTimer and
    # the previous interval kept running forever.
    assert JS.count("function startFeed() {\n  connectWs();") == 1
    # connectWs() stays unguarded here — it self-guards via
    # `if (!S.user || S.ws) return;` (pinned in test 2's file-wide counts).
    assert JS.count("if (S.pollTimer) return;") == 1
    # Single creation site: the interval must be created exactly once.
    # (Round 2: tightened from <= 1 to == 1.)
    assert JS.count("S.pollTimer = setInterval") == 1


# ---------- 2. stopFeed must detach handlers before closing ----------

def test_stopfeed_detaches_socket_handlers():
    # Detach BEFORE close, in this order, so the old socket's async onclose
    # can never stomp the state of a newer socket created meanwhile.
    assert JS.count(
        "w.onclose = null; w.onmessage = null; w.onopen = null;") == 1
    # Order proof: onclose is detached first.
    assert JS.index("w.onclose = null") < JS.index("w.onmessage = null")
    assert JS.count("try { w.close(); }") == 1
    # Old stomp pattern (null S.ws, then bump tries right after) is gone —
    # this is what let a stale onclose null out a NEWER socket's reference
    # and spawn duplicate parallel WebSockets.
    assert JS.count("S.ws = null;\n    S.wsTries += 1;") == 0
    # #161 (round 2): pin the guarded onclose cleanup that round 1 added but
    # never pinned — a stale socket may only clear S.ws while it is still
    # the current one.
    assert JS.count("if (S.ws === ws) S.ws = null;") == 1
    # Whole-file occurrences: stopFeed's detach + the guarded onclose cleanup.
    assert JS.count("S.ws = null;") <= 2


# ---------- 3. no explicit double-dispatch navigation (round 2) ----------

def test_no_double_dispatch_navigation():
    """The ×2 entry burst came from handlers that set the hash AND directly
    called route()/viewWorld() — the queued hashchange then routed again.
    (The round-1 theory — same-fragment re-assignment — fires no hashchange
    per the HTML spec and was behaviorally inert; its guard is reverted.)
    """
    # No `route();` on the same line as or within the 2 lines after a
    # `location.hash = "#/world";` assignment.
    assert _double_dispatch_hits(JS.split("\n")) == []
    # POST-EDIT TRUE COUNT (grep-verified): 4 — +#69 viewPeople Find-on-map;
        # original count 3 — viewWorld's plain
    # unconditional assignment, gotoWorld's else branch, and the substring
    # inside route()'s empty-hash normalization.
    assert JS.count('location.hash = "#/world";') == 4
    # The gotoWorld helper exists exactly once…
    assert JS.count("function gotoWorld()") == 1
    # …with the same-fragment guard in its body. NOTE: the literal operand
    # comes first on purpose — `location.hash === "#/world"` as a substring
    # is whole-file-pinned == 1 by m147/m153/m154/m156/m157 (the pollEvents
    # re-render gate); this body must not add a 2nd match.
    assert JS.count('if ("#/world" === location.hash) viewWorld();') == 1
    # Five call sites: register success, create-character success,
    # viewExternal depart, mapLink "Показать на карте", and — since #72 —
    # the Place screen's «К карте» (same single-dispatch contract; the
    # focus fields are set inline, mapLink() itself is untouched).
    assert JS.count("gotoWorld();") == 5
    # route() normalizes an EMPTY hash exactly once, without a second render:
    # set the hash and return — the queued hashchange re-enters route() with
    # a concrete view.
    assert JS.count(
        'if (!location.hash) { location.hash = "#/world"; return; }') == 1
    # The round-1 guard is reverted (revert proof; passes on base too).
    assert JS.count(
        'if (location.hash !== "#/world") location.hash = "#/world";') == 0


# ---------- 4. pollEvents must not double-fire while in flight ----------

def test_pollevents_inflight_guard():
    # Overlapping timers/renders must never run two poll bursts concurrently.
    assert JS.count("if (S.pollBusy) return;") == 1
    # Exactly 3× with the S. prefix: the guard return, the set-true and the
    # finally-false. The S initializer field is `pollBusy: false` (no "S."
    # prefix inside the object literal) — pinned via the 4× total below.
    assert JS.count("S.pollBusy") == 3
    assert JS.count("pollBusy") == 4
    # The finally must release the flag on the same statement chain as the
    # catch. #175 rewrote the formerly silent catch (it now counts consecutive
    # failures and drives the connection banner) — the old byte literal is
    # gone on purpose; the finally chain and the new failure-counter shape
    # are pinned instead.
    assert JS.count("} catch (e) { /* silent */ }"
                    " finally { S.pollBusy = false; }") == 0
    assert JS.count("finally { S.pollBusy = false; }") == 1
    assert JS.count("S.pollFails += 1;") == 1
    assert JS.count("S.pollFails = 0;") == 1


# ---------- 5. B3/resilience byte pins survive the #161 edits ----------

def test_regression_byte_pins_of_b3_survive():
    """Representative subset of m158's B3/resilience pins (retry affordance,
    §77-78 XSS hygiene, §61 refusal surfacing, chat-draft pattern) — the #161
    fix must not disturb them."""
    # #170: viewDesire joins the #93 retry pattern → 5 alpha-critical sites.
    # #173: viewShop joins the #93 retry pattern → 6 alpha-critical sites.
    # #175: viewExternal + viewAdmin drop their toast-only catches → 8
    # (#67: viewProfile adds its own retry via route()).
    # #72: viewLocation joins the #93 retry pattern → 11 sites.
    assert JS.count("loadRetryNode(loading, () => view") == 11  # +1 (#65 viewAppearance)
    # #170 F2: the desire interpret-preview failure path reuses the shared
    # hint constant (m158 keeps the const def + profile-save literal).
    # #72: +1 — the Place screen's events-failure line reuses the hint.
    assert JS.count("RETRY_HINT") == 5
    assert JS.count("innerHTML") == 0
    assert JS.count("AUTONOMOUS_HINT") == 8
    assert JS.count("isAutonomousRefusal") == 6
    # m158 oracle pins `sceneActDraft == 6` (5× S.sceneActDraft + 1× init
    # field); the bare-S count itself is 5 — #175 added the oninput capture
    # (draft saved on every keystroke, not only on submit). See module
    # docstring for the pre-#175 counts.
    assert JS.count("sceneActDraft") == 6
    assert JS.count("S.sceneActDraft") == 5
