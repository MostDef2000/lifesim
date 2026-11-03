"""#262 — own-character completion events must refresh the #/world needs panel.

Production evidence (owner report on prod): after the player's own task
completed, the needs panel showed hunger 0 while the server had 74.7. Root
cause: the WS live-refresh gate in ws.onmessage reacted only to
CONTROL_CHANGED (+#82 own-MOVE arrival); TASK_COMPLETED / ITEM_CONSUMED for
the OWN character only advanced S.cursor and the feed — the world view (needs
panel) stayed stale until a manual reload.

Fix (backend/app/web/app.js, WS events handler): the #177 CONTROL_CHANGED
refresh gate grew an OR-branch — events authored by the OWN character
(actor_id === S.character.id) of types TASK_COMPLETED / ITEM_CONSUMED /
CHARACTER_MOVED also trigger viewWorld(). Key semantics (pinned here):

- The own-actor branch gates on ID freshness (e.id > S.bootCursor) ONLY —
  NO game_timestamp window. Backdated DIRECT completions (#261) log
  game_timestamps in the past; a 15-minute liveness window would swallow
  them and the panel would stay stale (the exact #262 bug).
- The CONTROL_CHANGED predicate keeps its #177 bytes (id gate AND timestamp
  window) inside the OR; the preserved #82 arrival predicate (liveOwnArrival)
  keeps its timestamp window too.
- liveOwnArrival is computed once per WS frame; when it fires, the general
  refresh is suppressed (!liveOwnArrival) and onOwnArrival() still runs —
  max one full render per frame, arrival state/toast preserved.
- Singleton literals stay singletons: one `location.hash === "#/world"`,
  one `e.event_type === "CONTROL_CHANGED"` file-wide.
- isOwnMoveEvent keeps checking payload.character_id (own-MOVE semantics);
  the completion branch uses actor_id — the two filters are distinct.

Pin style: m176 house rules — exact literals, never weakened; the OLD
unconditional refresh shape stays pinned == 0.
"""

import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
APP_JS = os.path.join(ROOT, "backend/app/web/app.js")

JS = ""

# The combined refresh gate: CONTROL_CHANGED keeps its #177 liveness clauses
# byte-identically inside the OR; the #262 own-actor branch rides on ID
# freshness only (10-space if-line, 14/16/18-space continuations).
COMBINED_GATE_BLOCK = (
    "          if (fresh.some((e) =>\n"
    '              (e.event_type === "CONTROL_CHANGED"\n'
    "                && e.id > S.bootCursor\n"
    '                && S.world && typeof e.game_timestamp === "number"\n'
    "                && e.game_timestamp >= S.world.game_timestamp - 15)\n"
    "              || (S.character && e.actor_id === S.character.id\n"
    "                && e.id > S.bootCursor\n"
    '                && ["TASK_COMPLETED", "ITEM_CONSUMED", "CHARACTER_MOVED"]\n'
    "                  .includes(e.event_type)))\n"
    '            && location.hash === "#/world"\n'
    "            && !liveOwnArrival) {\n"
    "            viewWorld();\n"
    "          }"
)

# The preserved #82 arrival predicate — computed once per frame, shared with
# the refresh gate (suppression + single-render semantics).
LIVE_OWN_ARRIVAL_DECL = (
    "          const liveOwnArrival = fresh.some((e) => isOwnMoveEvent(e)\n"
    "              && e.id > S.bootCursor\n"
    '              && S.world && typeof e.game_timestamp === "number"\n'
    "              && e.game_timestamp >= S.world.game_timestamp - 15);"
)

# The arrival hook: still fires even when the general refresh is suppressed.
ARRIVAL_HOOK = "          if (liveOwnArrival) onOwnArrival();"

# The own-MOVE helper is untouched (payload.character_id semantics).
OWN_MOVE_HELPER = (
    "function isOwnMoveEvent(e) {\n"
    '  return e.event_type === "CHARACTER_MOVED"\n'
    "    && !!e.payload && e.payload.character_id === S.character.id;\n"
    "}"
)

# (d, mirrored from m176) the OLD unconditional shape — refresh fired for ANY
# CONTROL_CHANGED with no liveness gate. Must stay gone.
OLD_UNCONDITIONAL_BLOCK = (
    'e.event_type === "CONTROL_CHANGED")\n'
    '            && location.hash === "#/world") {\n'
    "            viewWorld();"
)


def setup_module(module):
    global JS
    with open(APP_JS, "r", encoding="utf-8") as f:
        JS = f.read()


# ---------- the combined predicate + own-actor filter ----------

def test_combined_predicate_and_own_actor_filter():
    # The combined gate exists exactly once, inside connectWs, after the
    # fresh-dedup (only LIVE deliveries react).
    assert JS.count(COMBINED_GATE_BLOCK) == 1
    connect_ws = JS.index("function connectWs() {")
    fresh_dedup = JS.index("const fresh = data.events.filter", connect_ws)
    gate = JS.index(COMBINED_GATE_BLOCK)
    assert connect_ws < fresh_dedup < gate
    # The own-actor filter: exactly one site, own character only.
    assert JS.count("S.character && e.actor_id === S.character.id") == 1
    # Exactly the three completion event types, consumed via .includes.
    assert JS.count(
        '["TASK_COMPLETED", "ITEM_CONSUMED", "CHARACTER_MOVED"]') == 1
    assert JS.count(".includes(e.event_type)") == 1


# ---------- the timestamp window does NOT gate the own-actor branch ----------

def test_own_actor_branch_is_id_freshness_only():
    # Slice the own-actor OR-branch out of the real file: from its `|| (` head
    # to the closing `.includes(...)))`.
    branch_start = JS.index("|| (S.character && e.actor_id")
    branch_end = JS.index(".includes(e.event_type)))", branch_start) \
        + len(".includes(e.event_type)))")
    branch = JS[branch_start:branch_end]
    # The own-actor branch carries the id gate...
    assert "e.id > S.bootCursor" in branch
    # ...and NO game_timestamp window: backdated DIRECT completions (#261)
    # must refresh (a 15-minute window would silently drop them).
    assert "game_timestamp" not in branch
    # The restriction lives exactly twice in the WS handler: once inside the
    # CONTROL_CHANGED OR-clause, once in the preserved arrival predicate.
    assert JS.count('typeof e.game_timestamp === "number"') == 2
    assert JS.count(
        "e.game_timestamp >= S.world.game_timestamp - 15") == 2
    arrival = JS.index(LIVE_OWN_ARRIVAL_DECL)
    gate = JS.index(COMBINED_GATE_BLOCK)
    region = JS[arrival:gate + len(COMBINED_GATE_BLOCK)]
    assert region.count('typeof e.game_timestamp === "number"') == 2
    # ...and the arrival predicate keeps its full window (byte-preserved).
    assert LIVE_OWN_ARRIVAL_DECL in JS


# ---------- singleton literals stay singletons ----------

def test_singleton_literals():
    # One hash gate, one CONTROL_CHANGED literal file-wide (m176/m179/m147
    # mirror this constraint) — no second refresh site may appear.
    assert JS.count('location.hash === "#/world"') == 1
    assert JS.count('e.event_type === "CONTROL_CHANGED"') == 1


# ---------- arrival suppresses the general refresh, hook still fires ----------

def test_arrival_suppresses_refresh_but_still_fires():
    # The refresh condition carries the suppression clause...
    assert JS.count("&& !liveOwnArrival) {") == 1
    # ...the hook still runs exactly once, AFTER the refresh block...
    assert JS.count(ARRIVAL_HOOK) == 1
    gate = JS.index(COMBINED_GATE_BLOCK)
    hook = JS.index(ARRIVAL_HOOK)
    assert gate + len(COMBINED_GATE_BLOCK) < hook
    # ...the declaration precedes both consumers (computed once per frame)...
    assert JS.index(LIVE_OWN_ARRIVAL_DECL) < gate
    # ...and the hook count is unchanged: def + REST fallback + WS hook.
    assert JS.count("onOwnArrival();") == 2
    assert JS.count(
        "if (fresh.some((e) => isOwnMoveEvent(e))) onOwnArrival();") == 1


# ---------- the old unconditional shape stays gone ----------

def test_old_unconditional_shape_absent():
    assert JS.count(OLD_UNCONDITIONAL_BLOCK) == 0


# ---------- the own-MOVE helper is untouched ----------

def test_own_move_helper_untouched():
    # isOwnMoveEvent keeps its payload.character_id filter — the #262
    # completion branch uses actor_id instead, a distinct filter.
    assert JS.count(OWN_MOVE_HELPER) == 1
