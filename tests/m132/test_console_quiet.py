"""Issue #132 pins — console noise on a clean session (frontend contract).

RED-first pins for the two app.js changes that quiet the browser console when
a logged-out visitor loads the app:

- Change (a): route() probes /auth/me at most once per session. On a clean
  session the probe 401s and api()'s 401 branch re-routes, which used to spam
  "unauthorized" errors on every navigation. After the fix the failed probe is
  negatively cached in S.authProbed, and the flag is cleared exactly at the
  two sites where the auth state genuinely changes: successful login/register
  (viewAuth onsubmit) and logout.
- Change (b): portraitBlock() negatively caches a visual-disabled backend
  (HTTP 503 only) in S.visualDisabled so every re-render stops re-firing the
  doomed /visual/characters/:id/portrait request. 404 stays uncached (a legit
  "no canonical yet" state), and generatePortrait clears the flag after a
  successful canonical pin so a mid-session enable works.

Regression guards pin the byte-exact neighbours the change must not disturb:
api()'s 401 route-guard, the m14-pinned no-portrait comment, the world-view
portrait/scene embedding, and the canonical-portrait state flow.
"""
import os

APP_JS = os.path.join(
    os.path.dirname(__file__), "../../backend/app/web/app.js")

# Byte-exact comment pinned by tests/m14 (em-dash U+2014).
NO_PORTRAIT_COMMENT = "/* no portrait yet — keep button */"


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def test_console_quiet_pins():
    """Issue #132: auth probe once per session + visual-disabled negative cache."""
    js = _read(APP_JS)

    # ---------- change (a): auth probe once per session ----------
    # Both flags ride on the existing S init line.
    assert "authProbed: false, visualDisabled: false," in js
    # route() skip condition exists exactly once (the probe fires at most one
    # time per session; every later route() call short-circuits).
    assert js.count("!S.user && !S.authProbed") == 1
    # The flag is set exactly once (the failed probe) and cleared at exactly
    # the two auth-state transitions (login/register success + logout).
    assert js.count("S.authProbed = true") == 1
    assert js.count("S.authProbed = false") == 2

    # ---------- change (b): visual-disabled negative cache (503 only) ----------
    # The negative cache gates the canonical-portrait fetch, caches 503 only,
    # and is cleared after a successful canonical pin in generatePortrait.
    assert js.count("if (!S.visualDisabled) {") == 1
    assert js.count("S.visualDisabled = true") == 1
    assert js.count("S.visualDisabled = false") == 1
    # Positional sanity: the gate lives inside portraitBlock's IIFE, before
    # the catch that records the 503.
    assert js.index("if (!S.visualDisabled) {") < js.index(NO_PORTRAIT_COMMENT)

    # ---------- regression guards (byte-pinned neighbours stay intact) ----------
    # api()'s 401 branch keeps its /auth/me route-guard (tests/m128 neighbour).
    assert 'if (path !== "/auth/me") route();' in js
    # The no-portrait comment survives byte-exact (pinned by tests/m14:55).
    assert NO_PORTRAIT_COMMENT in js
    # World view still embeds both blocks.
    assert "const portraitBox = portraitBlock();" in js
    assert "const sceneBox = sceneBlock();" in js
    # Canonical-portrait state flow intact: probe path + pin assignment.
    assert "`/visual/characters/${S.character.id}/portrait`" in js
    assert "S.canonicalPortraitId" in js
