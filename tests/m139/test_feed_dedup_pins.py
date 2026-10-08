"""Issue #139 pins — event feed dedup (WS + REST channel coordination).

RED-first pins for the two app.js changes that stop every event arriving
twice in S.feed now that the WebSocket is live on prod (#131/#137) while the
5 s REST poll keeps running:

- Change (a): pollEvents() delegates the REST events fetch to
  pollEventsFeed(), which early-returns while the WS is the live channel
  (readyState === 1). The interval keeps running and the A2 world-state
  refresh (clock/weather/map/tasks) still runs on EVERY tick — only the
  events fetch is skipped while the socket is the live channel; REST
  polling is the self-healing fallback for when the socket is down.
- Change (b): id-dedup at BOTH push sites. WS can re-deliver around a
  reconnect and the REST cursor path can re-fetch on a missed ack, so each
  site builds a Set of already-buffered ids, pushes/renders only fresh
  events, and skips the DOM re-render when nothing is fresh.

Regression guards pin the untouched invariants: the m124 byte-exact
WebSocket handshake line, the #132 console-quiet flags (S.authProbed /
S.visualDisabled set/clear counts), and the single S.feed declaration.
"""
import os
import subprocess

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
APP_JS = os.path.join(ROOT, "backend/app/web/app.js")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def test_feed_dedup_pins():
    """Issue #139: WS/REST channel coordination — no duplicate feed rows."""
    js = _read(APP_JS)

    # ---------- change (a): channel coordination in pollEvents ----------
    early = "if (S.ws && S.ws.readyState === 1) return;"
    assert js.count(early) == 1
    assert "async function pollEvents() {" in js
    # pollEvents delegates the events fetch; the gate lives in the delegated
    # fetch, ahead of the REST fetch it short-circuits.
    assert js.count("await pollEventsFeed();") == 1
    assert js.index("async function pollEvents() {") < js.index("await pollEventsFeed();")
    assert js.index("async function pollEvents() {") < js.index(early)
    assert js.index(early) < js.index("const data = await api(`/world/events?since=")
    # The documented fallback contract rides on a #139 comment marker, and the
    # A2 refresh is NOT skipped while WS is live (no frozen clock/weather):
    # the gate must live in the delegated fetch, AFTER the call site.
    assert "#139: WS is the live channel while connected" in js
    assert js.index("await pollEventsFeed();") < js.index(early)

    # ---------- change (b): id-dedup at both push sites ----------
    # REST poll site and WS onmessage site each build the seen-set.
    assert js.count("new Set(S.feed.map(e => e.id))") == 2

    # ---------- regression guards (counts unchanged vs base) ----------
    # m124: byte-exact WebSocket handshake line survives untouched.
    assert (
        js.count("  const ws = new WebSocket(`${proto}://${location.host}/ws`);") == 1
    )
    # #132 console-quiet flags: set/clear counts unchanged.
    assert js.count("S.authProbed = true") == 1
    assert js.count("S.authProbed = false") == 2
    assert js.count("S.visualDisabled = true") == 1
    assert js.count("S.visualDisabled = false") == 1
    # S.feed is declared exactly once in the S init line.
    assert js.count("feed: [],") == 1

    # ---------- syntax gate (m121 subprocess style) ----------
    result = subprocess.run(
        ["node", "--check", APP_JS],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
