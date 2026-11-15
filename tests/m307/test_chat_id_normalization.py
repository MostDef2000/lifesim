"""#338 (part D) — chat id-normalization regression: NUMERIC ids must not die.

The #307 fixtures use STRING character ids (the production shape). app.js now
normalizes every id comparison with ``String()`` (six sites: the poll-hook
presence verdict, the two viewChat target-resolution checks, the renderChat
selection restore, the refreshRel other_id match, and the send-time roster
revalidation). This file proves that defensive normalization works for a
NUMERIC id — a shape production never emits, but one the app must not silently
die on — and that the harness's SELECT ``.value`` string coercion is real.

The numeric roster is built AFTER the string fixtures: the destination
(location 2) carries an NPC whose ``id`` is an ``int`` (2, not "2"). The
character is already at location 2 (``CHAR1_AT_2``), so the chat roster is
exactly that one numeric NPC.

Every assertion is on an observable fact (the fetch POST ledger, the rendered
DOM, the ``S`` state). The shared ``journey_harness`` vm context is used as-is
— no new shim.
"""

import pytest

from tests.m307.journey_harness import (
    CATALOG,
    CHAR1_AT_2,
    LOC2,
    NPC_FAR,
    _journey_responses,
    _resp,
    facts,
    run_scenarios,
    unhandled,
)

# ---------- the numeric roster (built AFTER the string fixtures) ----------

# The destination (2) with an NPC whose id is an INT — a shape production never
# emits (production character ids are strings) but the app must survive.
NUMERIC_LOC = {**LOC2, "occupants": [{**NPC_FAR, "id": 2}]}
NUMERIC_CATALOG = [NUMERIC_LOC if loc["id"] == 2 else loc for loc in CATALOG]

# Non-vacuity guard (runs at import, BEFORE any scenario executes): the
# occupant id really is an int, not a string. Without this the whole file would
# silently test the string shape and prove nothing.
if not isinstance(NUMERIC_LOC["occupants"][0]["id"], int):
    raise AssertionError(
        "the numeric roster's occupant id must be an int, "
        f"got {type(NUMERIC_LOC['occupants'][0]['id']).__name__}")

# The numeric relationship (test 3): other_id is an INT. The app reads
# other_id / affection / trust (refreshRel + renderRelationshipScale).
NUMERIC_REL = {"other_id": 2, "affection": 60, "trust": 55}


def _numeric_responses(relationships=None):
    """The canonical-journey endpoint map with the numeric roster at loc 2.

    The character is already at location 2 (CHAR1_AT_2), so the chat roster is
    the single numeric NPC. ``relationships`` overrides the (default empty)
    /characters/1/relationships read for the relationship test.
    """
    d = _journey_responses(char=CHAR1_AT_2, catalog=NUMERIC_CATALOG)
    d["/locations/2"] = _resp(200, NUMERIC_LOC)
    if relationships is not None:
        d["/characters/1/relationships"] = _resp(200, relationships)
    return d


# The exact dialogue POST ledger a successful send must produce (npc_id is the
# STRING "2" — the select value is always a string; the message content is the
# typed text).
EXPECTED_POSTS = [
    ("/dialogue/start", {"npc_id": "2"}),
    ("/dialogue/30703/message", {"content": "Привет, Фар"}),
]


def _posts(final):
    return [(p["url"], p["body"]) for p in final["posts"]]


SCENARIOS = [
    {
        # Test 1 (core): numeric roster, a numeric select assignment + change
        # event, then a send.
        "name": "NUMERIC_ROSTER_SEND",
        "initialHash": "#/chat",
        "responses": _numeric_responses(),
        "driver": (
            "await __settle(); "
            "let sel = document.getElementById('chat-npc-select'); "
            "const initial = { value: sel.value, type: typeof sel.value }; "
            "sel.value = 2; "
            "(sel._listeners['change'] || [])"
            ".forEach(fn => fn({type:'change', target: sel})); "
            "await __settle(); "
            "sel = document.getElementById('chat-npc-select'); "
            "const after = { value: sel.value, type: typeof sel.value }; "
            "__type('chat-msg-input', 'Привет, Фар'); "
            "__clickButton('→'); "
            "await __settle(); "
            "const final = __facts(); "
            "globalThis.__result = { initial, after, final };"
        ),
    },
    {
        # Test 2: numeric roster, the app's auto-selection (no change event),
        # send directly. Isolates the send-time roster membership from the
        # change handler.
        "name": "NUMERIC_ROSTER_SEND_NO_CHANGE",
        "initialHash": "#/chat",
        "responses": _numeric_responses(),
        "driver": (
            "await __settle(); "
            "__type('chat-msg-input', 'Привет, Фар'); "
            "__clickButton('→'); "
            "await __settle(); "
            "const final = __facts(); "
            "globalThis.__result = { final };"
        ),
    },
    {
        # Test 3: numeric relationship other_id — the rendered relationship
        # must resolve (not the «пока незнакомы» fallback).
        "name": "NUMERIC_RELATIONSHIP",
        "initialHash": "#/chat",
        "responses": _numeric_responses(relationships=[NUMERIC_REL]),
        "driver": (
            "await __settle(); "
            "const relWord = document.querySelector('.rel-word'); "
            "const relBox = document.querySelector('.rel-box'); "
            "const rel = { "
            "  word: relWord ? relWord.textContent : null, "
            "  aria: relBox ? relBox.getAttribute('aria-label') : null, "
            "}; "
            "const final = __facts(); "
            "globalThis.__result = { rel, final };"
        ),
    },
    {
        # Test 4: shim-only coercion checks (no app involvement) — fresh
        # elements, the app's boot is irrelevant.
        "name": "SHIM_COERCION",
        "initialHash": "#/world",
        "responses": _journey_responses(),
        "driver": (
            "const sel = document.createElement('select'); "
            "sel.value = 2; "
            "const selNum = { value: sel.value, type: typeof sel.value }; "
            "sel.setAttribute('value', 101); "
            "const selAttr = { value: sel.value, type: typeof sel.value }; "
            "sel.value = ''; "
            "const selEmpty = { value: sel.value, type: typeof sel.value }; "
            "const inp = document.createElement('input'); "
            "inp.value = 'Привет, Фар'; "
            "const inpStr = { value: inp.value, type: typeof inp.value }; "
            "globalThis.__result = { selNum, selAttr, selEmpty, inpStr };"
        ),
    },
]


@pytest.fixture(scope="module")
def results():
    return run_scenarios(SCENARIOS)


def _f(results, name, key):
    return facts(results, name)[key]


def _uh(results, name):
    uh = unhandled(results, name)
    assert uh == [], f"{name}: no unhandled errors may escape: {uh}"


# ---------- 1. numeric roster + change + send (core) ----------

def test_numeric_roster_change_and_send(results):
    r = facts(results, "NUMERIC_ROSTER_SEND")
    initial, after, final = r["initial"], r["after"], r["final"]
    # The regression's primary observable: the exact dialogue POST ledger. A
    # numeric roster must not silently drop the send (zero POSTs) — the
    # send-time revalidation and the /dialogue/start npc_id must both survive
    # the int id.
    assert _posts(final) == EXPECTED_POSTS, (
        f"the numeric roster must produce the exact dialogue POSTs, "
        f"got {_posts(final)}")
    # The presence verdict is valid (the numeric NPC is co-located), the
    # session bound at the ACK, the composer consumed.
    assert final["chatPresenceText"] == "NPC рядом", (
        f"the numeric NPC must be co-located, got {final['chatPresenceText']!r}")
    assert final["chatSessionId"] == "30703"
    assert final["chatInputValue"] == ""
    assert final["chatDraft"] == ""
    assert "Привет, Фар" in (final["chatLogText"] or ""), (
        "the sent message must appear in the chat log")
    # The app state reflects the STRING id (the select value is always a
    # string; S.chatNpcId is keyed off it).
    assert final["chatNpcSelectValue"] == "2"
    assert final["chatNpcId"] == "2"
    # Harness coercion proof: the initial select value is a STRING "2".
    assert initial["value"] == "2", f"initial select value, got {initial!r}"
    assert initial["type"] == "string", f"initial select typeof, got {initial!r}"
    # After the numeric assignment + change, the live select's raw .value is a
    # STRING "2" (the selection survives the re-render).
    assert after["value"] == "2", f"after select value, got {after!r}"
    assert after["type"] == "string", f"after select typeof, got {after!r}"
    _uh(results, "NUMERIC_ROSTER_SEND")


# ---------- 2. numeric roster, auto-selection, send WITHOUT a change ----------

def test_numeric_roster_send_without_change(results):
    final = _f(results, "NUMERIC_ROSTER_SEND_NO_CHANGE", "final")
    # The app auto-selected the single numeric NPC on entry; a direct send
    # (no change event) must still produce the exact dialogue POSTs. This
    # isolates the send-time roster membership from the change handler.
    assert _posts(final) == EXPECTED_POSTS, (
        f"the auto-selected numeric NPC must send, got {_posts(final)}")
    # The select value is the STRING "2" (the restore coerces it); the POST
    # body's npc_id is that string. S.chatNpcId itself keeps the auto-selected
    # int (no change event ran to re-key it off the select) — the app must
    # survive that, which the POST ledger above proves.
    assert final["chatNpcSelectValue"] == "2"
    assert final["chatPresenceText"] == "NPC рядом"
    assert final["chatSessionId"] == "30703"
    _uh(results, "NUMERIC_ROSTER_SEND_NO_CHANGE")


# ---------- 3. numeric relationship other_id ----------

def test_numeric_relationship_other_id(results):
    r = facts(results, "NUMERIC_RELATIONSHIP")
    rel = r["rel"]
    # The rendered relationship must NOT be the «пока незнакомы» fallback —
    # the numeric other_id must match the selected NPC.
    assert "пока незнакомы" not in (rel["aria"] or ""), (
        f"the numeric other_id must resolve the relationship, "
        f"got aria {rel['aria']!r}")
    # Positive: affection 60 → the «близкие» band (not the neutral fallback
    # that a missing rel would render).
    assert rel["word"] == "близкие", (
        f"affection 60 must render the близкие band, got {rel!r}")
    assert "близкие" in (rel["aria"] or ""), (
        f"the aria must carry the близкие band, got {rel['aria']!r}")
    _uh(results, "NUMERIC_RELATIONSHIP")


# ---------- 4. shim-only coercion checks (no app involvement) ----------

def test_shim_select_coercion(results):
    r = facts(results, "SHIM_COERCION")
    selNum, selAttr, selEmpty, inpStr = (
        r["selNum"], r["selAttr"], r["selEmpty"], r["inpStr"])
    # A raw numeric assignment to a select's .value is coerced to the string
    # "2".
    assert selNum["value"] == "2" and selNum["type"] == "string", (
        f"select numeric assignment must coerce to string, got {selNum!r}")
    # setAttribute("value", 101) on a select is coerced to the string "101".
    assert selAttr["value"] == "101" and selAttr["type"] == "string", (
        f"select setAttribute value must coerce to string, got {selAttr!r}")
    # Assigning "" stays "".
    assert selEmpty["value"] == "" and selEmpty["type"] == "string", (
        f"select empty assignment must stay '', got {selEmpty!r}")
    # A text input keeps the exact typed string (no coercion).
    assert inpStr["value"] == "Привет, Фар" and inpStr["type"] == "string", (
        f"text input must keep the exact string, got {inpStr!r}")
