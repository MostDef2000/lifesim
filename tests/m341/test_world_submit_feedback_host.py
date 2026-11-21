"""#341 — the World view mounts its own submission-feedback host.

Behavioral tests for the #341 World ``.task-submit-line`` in the REAL
production ``backend/app/web/app.js`` (boot included), run inside a Node
``vm`` context through the shared m307 journey harness (the m304 lineage:
same minimal DOM / fake-timer / controlled-network shim, same
``taskSubmitFeedback`` / ``refreshTaskPresentationHosts`` machinery). The
harness is used as-is — no new shim; the #341-specific DOM facts come from
an INLINE driver-side census that walks ``_parent`` chains (the shim's
selector engine has no descendant combinator).

Approved behavior under test (derived from the work order):
- On ``#/world`` a ``.task-submit-line`` is MOUNTED (count >= 1), at least
  one instance sits inside ``.world-shell``, and none sits inside
  ``.place-shell``.
- A 409 busy submission failure (``{"detail": "character is busy"}``)
  paints the authoritative ``taskSubmitFeedback`` caption (CAP_BUSY) into
  the World line — after an explicit ``refreshTaskPresentationHosts()``
  too (the painter patches every MOUNTED line).
- After navigating away from ``#/world`` the World host is UNMOUNTED: the
  captured line leaves the document, ``#world-now-task-host`` is gone and
  no line lives inside a world shell — the #304 absent-host discipline
  (no stale caption can survive navigation).
- The two hosts keep DISTINCT semantics: ``#world-now-task-host`` still
  carries the task-status caption while ``.task-submit-line`` carries the
  submission-failure caption.

Expected RED on base 131057a: the World view has no ``.task-submit-line``
at all (the class exists only on the Place context panel).
"""
import pytest
from tests.m307.journey_harness import (
    _journey_responses,
    facts,
    run_scenarios,
    unhandled,
)

# The authoritative busy caption (app.js actionSubmitErrorText, 409 +
# /busy|занят/i detail) — m304's CAP_BUSY.
CAP_BUSY = "Персонаж уже занят другим действием"
# The task-status caption of the accepted MOVE (planned) task.
CAP_PLANNED = "Задача принята. Ожидает начала"

# The driver-side census: every .task-submit-line with its ancestry verdict
# (the shim has no descendant selector, so walk _parent), plus the World
# task-status host. No backticks: the driver is spliced into a JS template
# literal by the harness.
CENSUS_JS = """
function census() {
  const out = [];
  const lines = document.querySelectorAll('.task-submit-line');
  for (const line of lines) {
    let p = line._parent, inWorld = false, inPlace = false;
    while (p) {
      if (p.classList && p.classList.contains('world-shell')) inWorld = true;
      if (p.classList && p.classList.contains('place-shell')) inPlace = true;
      p = p._parent;
    }
    out.push({ text: line.textContent, inWorld: inWorld, inPlace: inPlace });
  }
  const host = document.getElementById('world-now-task-host');
  return {
    count: lines.length,
    lines: out,
    hostText: host ? host.textContent : null,
    hostMounted: !!host,
  };
}
function isMounted(node) {
  let p = node;
  while (p) {
    if (p === document.documentElement) return true;
    p = p._parent;
  }
  return false;
}
"""


def _driver(body):
    return CENSUS_JS + body


_BUSY = "__setResponse('/actions', {status: 409, "
_BUSY += "body: {detail: 'character is busy'}}); "

SCENARIOS = [
    # ---------- 1. the World host is mounted at #/world ----------
    {
        "name": "WORLD_SUBMIT_LINE_MOUNTED",
        "initialHash": "#/world",
        "responses": _journey_responses(),
        "driver": _driver(
            "await __settle(); "
            "const c = census(); const f = __facts(); "
            "globalThis.__result = { c, f };"
        ),
    },
    # ---------- 2. a 409 busy paints CAP_BUSY into the World line ----------
    {
        "name": "WORLD_BUSY_CAPTION_PAINTED",
        "initialHash": "#/world",
        "responses": _journey_responses(),
        "driver": _driver(
            "await __settle(); "
            + _BUSY
            + "__call('doAction', 'EAT', {}); await __settle(); "
            "__call('refreshTaskPresentationHosts'); await __settle(); "
            "const c = census(); const f = __facts(); "
            "globalThis.__result = { c, f };"
        ),
    },
    # ---------- 3. navigating away unmounts the World host ----------
    {
        "name": "WORLD_HOST_UNMOUNTS_ON_NAV",
        "initialHash": "#/world",
        "responses": _journey_responses(),
        "driver": _driver(
            "await __settle(); "
            "const c1 = census(); "
            "let worldLine = null; "
            "for (const l of document.querySelectorAll('.task-submit-line')) { "
            "  let p = l._parent; "
            "  while (p) { "
            "    if (p.classList && p.classList.contains('world-shell')) "
            "      { worldLine = l; break; } "
            "    p = p._parent; "
            "  } "
            "  if (worldLine) break; "
            "} "
            "__setHash('#/location/1'); await __settle(); "
            "const c2 = census(); "
            "const hostAfter = !!document.getElementById("
            "'world-now-task-host'); "
            "const lineMounted = worldLine ? isMounted(worldLine) : null; "
            "const f = __facts(); "
            "globalThis.__result = { c1, c2, hostAfter, lineMounted, "
            "worldLineFound: !!worldLine, f };"
        ),
    },
    # ---------- 4. the two hosts keep distinct semantics ----------
    {
        "name": "WORLD_HOSTS_DISTINCT_SEMANTICS",
        "initialHash": "#/world",
        "responses": _journey_responses(),
        "driver": _driver(
            "await __settle(); "
            "__call('doAction', 'EAT', {}); await __settle(); "
            "const cAccept = census(); "
            + _BUSY
            + "__call('doAction', 'EAT', {}); await __settle(); "
            "__call('refreshTaskPresentationHosts'); await __settle(); "
            "const cBusy = census(); "
            "globalThis.__result = { cAccept, cBusy };"
        ),
    },
]


@pytest.fixture(scope="module")
def results():
    return run_scenarios(SCENARIOS)


def _in_world(c):
    return [ln for ln in c["lines"] if ln["inWorld"]]


def _in_place(c):
    return [ln for ln in c["lines"] if ln["inPlace"]]


# ---------- 1. mounted on #/world, never inside the place view ----------

def test_world_submit_line_mounted(results):
    r = facts(results, "WORLD_SUBMIT_LINE_MOUNTED")
    c, f = r["c"], r["f"]
    assert f["hasWorldShell"] is True, "the World view must be mounted"
    assert c["count"] >= 1, (
        f"#/world must mount at least one .task-submit-line, got {c['count']}"
    )
    assert len(_in_world(c)) >= 1, (
        "the World .task-submit-line must live inside .world-shell, "
        f"got {c['lines']!r}"
    )
    assert _in_place(c) == [], (
        "no .task-submit-line on #/world may sit inside .place-shell, "
        f"got {_in_place(c)!r}"
    )
    assert unhandled(results, "WORLD_SUBMIT_LINE_MOUNTED") == [], (
        "no unhandled errors may escape: "
        f"{unhandled(results, 'WORLD_SUBMIT_LINE_MOUNTED')}"
    )


# ---------- 2. the 409 busy caption lands in the World line ----------

def test_world_line_painted_with_busy_caption(results):
    name = "WORLD_BUSY_CAPTION_PAINTED"
    r = facts(results, name)
    c = r["c"]
    painted = [ln for ln in _in_world(c) if CAP_BUSY in ln["text"]]
    assert painted, (
        "the authoritative taskSubmitFeedback caption must be painted into "
        f"the World .task-submit-line, got {c['lines']!r}"
    )
    assert c["hostMounted"] is True, (
        "the World task-status host must stay mounted beside the line"
    )
    assert CAP_BUSY not in (c["hostText"] or ""), (
        "the failure caption belongs to .task-submit-line, NOT to the "
        f"task-status host, got {c['hostText']!r}"
    )
    assert unhandled(results, name) == [], (
        f"no unhandled errors may escape: {unhandled(results, name)}"
    )


# ---------- 3. navigating away unmounts the World host (#304) ----------

def test_world_host_unmounts_on_navigation(results):
    name = "WORLD_HOST_UNMOUNTS_ON_NAV"
    r = facts(results, name)
    c1, c2, f = r["c1"], r["c2"], r["f"]
    assert r["worldLineFound"] is True, (
        "the scenario must capture the World .task-submit-line before "
        "navigating"
    )
    assert len(_in_world(c1)) >= 1, (
        f"the World line must be mounted before navigation: {c1['lines']!r}"
    )
    assert f["hasPlaceShell"] is True, "the navigation must land on a place"
    assert r["lineMounted"] is False, (
        "the World .task-submit-line must leave the document on navigation "
        "(no stale host may survive), got lineMounted="
        f"{r['lineMounted']!r}"
    )
    assert r["hostAfter"] is False, (
        "#world-now-task-host must be unmounted after leaving #/world"
    )
    assert _in_world(c2) == [], (
        "no .task-submit-line may remain inside a world shell after "
        f"navigation, got {c2['lines']!r}"
    )
    assert unhandled(results, name) == [], (
        f"no unhandled errors may escape: {unhandled(results, name)}"
    )


# ---------- 4. task status vs submission feedback: two hosts ----------

def test_world_hosts_have_distinct_semantics(results):
    name = "WORLD_HOSTS_DISTINCT_SEMANTICS"
    r = facts(results, name)
    accept, busy = r["cAccept"], r["cBusy"]
    # the accepted MOVE task owns the status host…
    assert accept["hostMounted"] is True, (
        "the World task-status host must be mounted after an acceptance"
    )
    assert CAP_PLANNED in (accept["hostText"] or ""), (
        "the accepted (planned) task must own the task-status host, "
        f"got {accept['hostText']!r}"
    )
    # …and after the failure the two still disagree on purpose.
    painted = [ln for ln in _in_world(busy) if CAP_BUSY in ln["text"]]
    assert painted, (
        "the failure caption must be painted into the World "
        f".task-submit-line, got {busy['lines']!r}"
    )
    assert busy["hostMounted"] is True, (
        "the task-status host must survive the failed submission"
    )
    assert CAP_BUSY not in (busy["hostText"] or ""), (
        "the task-status host must keep the task caption, never the "
        f"submission-failure caption, got {busy['hostText']!r}"
    )
    assert CAP_PLANNED in (busy["hostText"] or ""), (
        "the task-status host must still carry the task status after the "
        f"failure, got {busy['hostText']!r}"
    )
    assert unhandled(results, name) == [], (
        f"no unhandled errors may escape: {unhandled(results, name)}"
    )
