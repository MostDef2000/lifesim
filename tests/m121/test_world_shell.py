"""Issue #121 — world shell pins (frontend contract, variant A palette).

RED-first pins for the product-facing #/world shell: the world-shell DOM tree
in app.js and the world-scoped cinematic palette in style.css. The palette is
WORLD-SCOPED (variant A): global :root token values must not change, so the
guarded :root pins below must keep passing byte-exact.

Pin groups:
- "world-shell pins" must FAIL on base (no .world-shell) and pass after the
  implementation;
- "regression guards" must pass both before and after (they re-state the
  pre-existing byte-pins from m9/m10/m14/m20/m21/m22 so this file alone
  proves the contour survived).
"""
import subprocess


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def test_world_shell_pins():
    js = _read("backend/app/web/app.js")
    css = _read("backend/app/web/style.css")

    # ---------- world-shell pins (RED on base) ----------
    # Shell tree: main.world-shell wraps the map stage and the secondary column.
    assert 'el("main", { class: "world-shell" },' in js
    assert 'class: "world-map-stage"' in js
    assert 'class: "panel world-map-panel"' in js
    assert 'class: "world-map-heading"' in js
    assert '"Осмотреть окрестности"' in js
    assert 'class: "world-character-card"' in js
    assert 'ch.job ? `Работа: ${ch.job}` : "Работа: —"' in js
    assert 'class: "world-mini-needs"' in js
    assert 'class: "world-action-pills", "aria-label": "Действия персонажа"' in js
    assert '["WORK", "Пойти на работу"]' in js
    assert '["EAT", "Поесть"]' in js
    assert '["DRINK", "Попить"]' in js
    assert '["SLEEP", "Отдохнуть / поспать"]' in js
    assert '["SOCIALIZE", "Пообщаться"]' in js
    assert 'el("button", { onclick: () => doAction(a) }, label)' in js
    # AUTONOMOUS mode hides the manual pills (backend rejects /actions with a
    # 409 in that mode) and offers the Profile hand-off instead.
    assert 'ch.control_mode === "AUTONOMOUS"' in js
    assert '"Персонаж действует сам — переключите режим в Профиле"' in js
    # Secondary column: журнал + portrait + scene + events feed.
    assert 'class: "world-secondary"' in js
    assert 'class: "panel world-tasks-card"' in js
    assert 'el("h2", {}, "Журнал"),' in js
    assert "`Активных задач: ${activeCount}" in js
    assert "taskRows.slice(0, 3)" in js
    # Feed keeps its poll/WS hook id but gains the world-events-card spelling.
    assert 'class: "panel feed world-events-card"' in js
    # The heading hand-off mirrors buildLivingMap's "Открыть Scene" scroll, so
    # the scene-view lookup and the reduced-motion-aware scrollIntoView now
    # exist twice: once in buildLivingMap (pinned byte-identical) and once here.
    assert js.count('document.getElementById("scene-view")') == 2
    assert js.count(
        'scene.scrollIntoView({ behavior: S.mapReducedMotion ? "auto" : "smooth",'
        ' block: "start" });'
    ) == 2

    # World-scoped palette (variant A): every token lives under .world-shell.
    assert ".world-shell {" in css
    assert "--w-panel" in css
    assert "--w-line" in css
    assert "--w-accent" in css
    assert "--w-warm" in css
    assert ".world-map-stage" in css
    assert ".world-character-card" in css
    assert ".world-mini-needs" in css
    assert ".world-action-pills" in css
    assert ".world-events-card" in css
    assert ".world-tasks-card" in css
    assert ".world-map-panel .map-toolbar" in css
    assert "#living-map-host" in css
    # New mobile rules are placed after the pinned first-occurrence blocks and
    # collapse the shell to a single column.
    assert ".world-shell { grid-template-columns: 1fr; }" in css

    # ---------- regression guards (must pass on base too) ----------
    # Map asset contour: the island map URL appears exactly once in app.js.
    assert js.count('"/static/static/map/world-island.jpg"') == 1
    # el()/api()/topbar byte-pins.
    assert "for (const item of child) appendKids(node, item);" in js
    assert 'if (path !== "/auth/me") route();' in js
    assert 'el("div", { class: "tabs" },' in js
    # Living map internals untouched (pinned blocks stay byte-identical).
    assert js.count('"Открыть Scene"') == 1
    assert "const capturedMarkerLeft = oldMarker && oldMarker.style.left" in js
    assert 'newBadge.classList.toggle("hidden", capturedBadgeHidden);' in js
    assert "S.mapFocusLocationId = character.location_id" in js
    assert "MAP_LOW_MARKER_BUDGET = 120" in js
    assert '["high", "balanced", "low-mobile"]' in js
    assert "mapReducedMotion" in js
    # renderWorld keeps its data-prep handoffs.
    assert "const portraitBox = portraitBlock();" in js
    assert "portraitBox," in js
    assert "const sceneBox = sceneBlock();" in js
    assert "sceneBox," in js
    assert "`/characters/${S.character.id}/tasks`" in js
    assert 'id: "feed"' in js
    # Weather topbar + time_scale contours.
    assert '"Погода: "' in js
    assert '"/weather"' in js
    assert "time_scale" in js
    # textContent-only rendering and the needBar "—" fallback stay.
    assert "el(\"span\", {}, \"—\")" in js
    assert "?? 100" not in js
    assert "innerHTML" not in js
    assert "document.write" not in js
    assert "insertAdjacentHTML" not in js
    # Global palette (variant A): :root token values are untouched.
    assert "--bg: #101418; --panel: #1a2027; --line: #2a323c;" in css
    assert "#app { max-width: 1100px;" in css
    # m21 parses the FIRST 880px block — it must stay the original .grid rule.
    grid_media = css.split("@media (max-width: 880px) {", 1)[1].split("\n}", 1)[0]
    assert ".grid { grid-template-columns: 1fr" in grid_media
    small_media = css.split("@media (max-width: 520px) {", 1)[1].split("\n}", 1)[0]
    assert ".anchor .dot" in small_media
    assert ".anchor.player" in css
    assert ".landing-cta {" in css
    assert "@media (prefers-reduced-motion: reduce)" in css

    # The world shell must ship no new fonts/assets — syntax gate only.
    result = subprocess.run(
        ["node", "--check", "backend/app/web/app.js"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
