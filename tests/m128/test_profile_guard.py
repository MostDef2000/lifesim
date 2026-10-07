"""Issue #128 — profile null-guard pins (frontend contract).

RED-first pins for the #/profile null-character guard in app.js. route()
deliberately falls through to viewProfile when the hash is "#/profile" and
S.character is null — both in the create-form race (viewCreateCharacter sets
location.hash = "#/profile" while S.character is still null, and the queued
hashchange re-runs route()) and on direct navigation by a user with no
character. viewProfile must therefore self-guard and hand off to
viewCreateCharacter instead of reading S.character.control_mode unguarded.

Pin groups:
- "null-guard pins" must FAIL on base (unguarded read) and pass after the fix;
- "regression guards" must pass both before and after (the create-form bytes
  and the route() fall-through stay byte-identical).
"""
import subprocess


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def test_profile_null_guard_pins():
    js = _read("backend/app/web/app.js")

    # ---------- null-guard pins (RED on base) ----------
    # The guard exists exactly once and is the first statement of viewProfile,
    # before the view's own hash write and before the crash read it protects.
    guard = "if (!S.character) { viewCreateCharacter(); return; }"
    assert guard in js
    assert js.count(guard) == 1
    assert js.index("async function viewProfile() {") < js.index(guard)
    assert js.index(guard) < js.index('location.hash = "#/profile";', js.index(guard))
    assert js.index(guard) < js.index("modeSel.value = S.character.control_mode")

    # ---------- regression guards (must pass on base too) ----------
    # The control-mode read stays for the with-character path, fallback intact.
    assert 'modeSel.value = S.character.control_mode || "AUTONOMOUS";' in js
    # viewCreateCharacter stays the fallback target: it renders the form and
    # owns the same #/profile hash (set at render and on the map hand-off
    # button), so re-entering it fires no hashchange and cannot loop.
    assert js.count('location.hash = "#/profile";') == 3
    assert "S.character = r;" in js
    # route() keeps its deliberate #/profile fall-through; the viewProfile
    # guard is what makes it safe.
    assert 'if (!S.character && hash !== "#/profile") return viewCreateCharacter();' in js

    # Syntax gate: the guard must ship parse-clean.
    result = subprocess.run(
        ["node", "--check", "backend/app/web/app.js"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
