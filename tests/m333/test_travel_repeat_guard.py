"""m333 travel repeat guard — source pins + behavioral proof: the World
   travel bar's Enter must ignore OS auto-repeat (a held Enter on
   ``#travel-input`` re-fires ``submitTravel()`` and issues a second
   ``POST /travel/plan``), while a plain Enter still submits exactly once.

   TWO layers (the work order asks for both):

   1. SOURCE-STRUCTURE pins (app.js read as text, m327 style) — the
      travel input's ``onkeydown`` body carries ``&& !ev.repeat`` next to
      the plain ``ev.key === "Enter"`` check; ``travel-input`` and the
      single ``/travel/plan`` call site stay intact. Expected RED on base
      131057a: no ``ev.repeat`` guard.

   2. BEHAVIORAL proof — the vm harness CAN express ``repeat``: the shim's
      ``el()`` routes ``onkeydown`` to ``addEventListener("keydown", …)``,
      so the production handler lives in ``input._listeners["keydown"]``
      and a driver can invoke it with any event shape (m305's
      ``__chatEnter`` precedent). The m310/m269 window key drivers cannot
      (window-level, and they never carry ``repeat``), so this file drives
      the element listener directly through the shared m307 journey
      harness — the REAL production handler runs, no reimplementation.

   The destination is nonsense and the stubbed server rejects it with 422
   ``unknown destination`` — the real repro condition: nothing re-renders
   the bar, the typed text stays, so a held Enter would re-submit against
   the same input value.
"""
from pathlib import Path

import pytest
from tests.m307.journey_harness import (
    _journey_responses,
    _resp,
    facts,
    run_scenarios,
    unhandled,
)

ROOT = Path(__file__).resolve().parents[2]
APP_JS = (ROOT / "backend" / "app" / "web" / "app.js").read_text(encoding="utf-8")

# The nonsense destination the driver types — no such place exists, so the
# stubbed backend rejects the plan (422) and the input text stays put.
NONSENSE = "в никуда-точно-нет-такого-места"


# ---- helpers (m270/m313 house pattern: quote-aware balanced-span extraction) ----
def _sq_string(text, i):
    """True when the ' at i opens a real single-quoted string.

    app.js comments carry possessive apostrophes (the plan's task_id) that
    must not poison the balance: a single quote only opens a string when it
    follows an opener-ish char AND closes on the same line.
    """
    if i and text[i - 1] not in "(,=:[!&|?{};> \n\t":
        return False
    close = text.find("'", i + 1)
    line_end = text.find("\n", i)
    return close != -1 and (line_end == -1 or close < line_end)


def _span(text, open_idx, open_ch, close_ch):
    """Content between open_ch at open_idx and its matching close_ch."""
    depth, quote, i, n = 0, None, open_idx, len(text)
    while i < n:
        c = text[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in "\"`" or (c == "'" and _sq_string(text, i)):
            quote = c
        elif c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return text[open_idx + 1 : i], i
        i += 1
    raise AssertionError(f"unbalanced {open_ch!r} span from {open_idx}")


def _balanced(text, marker, open_ch, close_ch):
    """Content of the first balanced open_ch..close_ch after marker."""
    i = text.index(open_ch, text.index(marker))
    return _span(text, i, open_ch, close_ch)[0]


# ---------- source pins (m327 style) ----------


def _travel_bar_body():
    """Brace-balanced body of travelBar() — the #travel-input host."""
    return _balanced(APP_JS, "function travelBar(", "{", "}")


def _keydown_body():
    """Brace-balanced body of the travel input's onkeydown arrow handler."""
    return _balanced(_travel_bar_body(), "onkeydown:", "{", "}")


def _submit_travel_body():
    """Brace-balanced body of submitTravel() — the single /travel/plan POST."""
    return _balanced(APP_JS, "function submitTravel(", "{", "}")


# ---- RED on base 131057a: held-Enter auto-repeat must not re-submit ----
def test_keydown_handler_guards_keyboard_repeat():
    body = _keydown_body()
    assert "&& !ev.repeat" in body, (
        "travel keydown handler must ignore auto-repeat events "
        f"(no && !ev.repeat guard in: {body!r})"
    )


# ---- green on base: the guard must not over-block a plain Enter ----
def test_keydown_handler_still_submits_plain_enter():
    body = _keydown_body()
    assert 'ev.key === "Enter"' in body, (
        f"travel keydown handler must still test the Enter key: {body!r}"
    )
    assert "submitTravel()" in body, (
        f"travel keydown handler must still submit on Enter: {body!r}"
    )


# ---- preservation pins: green on base, must stay green ----
def test_travel_input_id_preserved():
    assert 'id: "travel-input"' in _travel_bar_body()


def test_submit_travel_single_plan_post():
    body = _submit_travel_body()
    assert 'api("/travel/plan"' in body, (
        "submitTravel() must keep the ONE /travel/plan call site"
    )
    assert "beginActionSubmission()" in body, (
        "#304 latch acquisition drifted in submitTravel()"
    )


# ---------- behavioral: the REAL handler, driven with repeat ----------

# The nonsense destination is rejected — no re-render, input text stays.
def _travel_responses():
    return {
        **_journey_responses(),
        "/travel/plan": _resp(422, {"detail": "unknown destination"}),
    }


def _travel_key_driver(repeat):
    """Type the nonsense text into #travel-input, then fire its PRODUCTION
    keydown listener with a shaped Enter event (repeat true/false)."""
    return (
        "await __settle(); "
        "const input = document.getElementById('travel-input'); "
        "const listeners = input ? (input._listeners['keydown'] || []) : []; "
        f"if (input) input.value = {NONSENSE!r}; "
        "listeners.forEach(fn => fn({key: 'Enter', repeat: "
        + ("true" if repeat else "false")
        + ", target: input, preventDefault() {}, stopPropagation() {}})); "
        "await __settle(); "
        "const after = __facts(); "
        "globalThis.__result = { hasInput: !!input, "
        "listenerCount: listeners.length, "
        "inputValue: input ? input.value : null, after };"
    )


SCENARIOS = [
    {
        "name": "TRAVEL_REPEAT_NO_POST",
        "initialHash": "#/world",
        "responses": _travel_responses(),
        "driver": _travel_key_driver(repeat=True),
    },
    {
        "name": "TRAVEL_PLAIN_ONE_POST",
        "initialHash": "#/world",
        "responses": _travel_responses(),
        "driver": _travel_key_driver(repeat=False),
    },
]


@pytest.fixture(scope="module")
def results():
    return run_scenarios(SCENARIOS)


def _plan_posts(r):
    return [p for p in r["after"]["posts"] if p["url"] == "/travel/plan"]


def _assert_driver_really_ran(r, name):
    """Non-vacuity: the production input and its keydown listener existed —
    otherwise 'zero posts' would prove nothing."""
    assert r["hasInput"] is True, (
        f"{name}: #travel-input must be mounted on #/world"
    )
    assert r["listenerCount"] >= 1, (
        f"{name}: the production onkeydown listener must be registered, "
        f"got {r['listenerCount']}"
    )


def test_repeat_enter_does_not_submit_travel(results):
    name = "TRAVEL_REPEAT_NO_POST"
    r = facts(results, name)
    _assert_driver_really_ran(r, name)
    assert _plan_posts(r) == [], (
        "a held (auto-repeat) Enter must NOT re-submit travel — "
        f"got {len(_plan_posts(r))} POST /travel/plan: {_plan_posts(r)!r}"
    )
    assert r["inputValue"] == NONSENSE, (
        "the rejected destination text must stay in the input "
        f"(the real repro condition), got {r['inputValue']!r}"
    )
    assert unhandled(results, name) == [], (
        f"no unhandled errors may escape: {unhandled(results, name)}"
    )


def test_plain_enter_submits_travel_exactly_once(results):
    name = "TRAVEL_PLAIN_ONE_POST"
    r = facts(results, name)
    _assert_driver_really_ran(r, name)
    posts = _plan_posts(r)
    assert len(posts) == 1, (
        "a plain Enter must submit travel exactly once "
        f"(the guard must not over-block), got {len(posts)}: {posts!r}"
    )
    assert r["after"]["postCount"] >= 1, (
        f"the POST must be visible in the network census: {r['after']!r}"
    )
    assert r["inputValue"] == NONSENSE, (
        "the rejected destination text must stay in the input "
        f"(422 keeps the bar), got {r['inputValue']!r}"
    )
    assert unhandled(results, name) == [], (
        f"no unhandled errors may escape: {unhandled(results, name)}"
    )
