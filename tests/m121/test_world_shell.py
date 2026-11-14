"""Issue #121 — world shell pins (frontend contract, variant A palette).

RED-first pins for the product-facing #/world shell: the world-shell DOM tree
in app.js and the world-scoped palette in style.css. The palette is
WORLD-SCOPED (variant A): global :root token values must not change, so the
guarded :root pins below must keep passing byte-exact.

#289 recomposed the shell to canon v3 «Панель слева + плавающая навигация»:
the warm palette (VISUAL-BIBLE §14) replaces the old cinematic dark one, the
Living Map is full-bleed (.world-stage), the left context panel (.world-panel)
+ floating nav pill (.world-pill) + below-fold blocks (.world-below) replace
the old two-column grid, and the world screen renders the pill INSTEAD of the
topbar. The world-shell pins below track the new canon; the regression guards
still prove the pre-existing contour survived.

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

    # ---------- world-shell pins (canon v3, #289) ----------
    # Shell tree: main.world-shell is a fixed full-viewport scroll container.
    # The Living Map fills the first viewport (.world-stage, full-bleed); the
    # left context panel (.world-panel), the floating nav pill (.world-pill),
    # and the below-fold blocks (.world-below) float/scroll over it.
    assert 'el("main", { class: "world-shell" },' in js
    # Full-bleed map stage (was .world-map-stage > .panel.world-map-panel); the
    # no-coords fallback list keeps working beside the context panel.
    assert 'class: "world-stage"' in js
    assert 'class: "world-map-fallback"' in js
    # «Осмотреть окрестности» now floats bottom-right over the map and zooms
    # the Living Map to region LOD (was a heading button in the map panel).
    assert '"Осмотреть окрестности"' in js
    assert 'class: "world-inspect"' in js
    assert 'setMapLod("region")' in js
    # Left context panel (was .world-character-card): name/money, location·job,
    # time/weather chip, needs, island events.
    assert 'class: "world-panel"' in js
    assert 'class: "world-who-name"' in js
    assert 'class: "world-money"' in js
    assert 'class: "world-chip"' in js
    # #295: the job title is localized through the JS mirror of the backend
    # _OCCUPATION_TITLES_RU (codeLabel raw-fallback for unknown titles).
    assert (
        '`${currentLocationLabel} · ${ch.job ? codeLabel(JOB_TITLES_RU, ch.job) : "работы нет"}`'
        in js
    )
    assert 'class: "world-mini-needs"' in js
    # Below-fold blocks (was .world-secondary): actions, travel, place,
    # journal, portrait, scene.
    assert 'class: "world-below"' in js
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
    assert 'class: "panel world-tasks-card"' in js
    assert 'el("h2", {}, "Журнал"),' in js
    assert "`Активных задач: ${activeCount}" in js
    assert "taskRows.slice(0, 3)" in js
    # Feed keeps its poll/WS hook id; the node now carries the world-feed
    # spelling and an honest empty state (canon §43.1 empty).
    assert 'class: "world-feed"' in js
    assert 'class: "world-feed-empty"' in js
    assert "Сейчас на острове тихо" in js
    # The scene-view lookup and the reduced-motion-aware scrollIntoView exist
    # twice: once in buildLivingMap (pinned byte-identical) and once in
    # backToMap (the world re-render hand-off). #304: a third lookup —
    # refreshTaskPresentationHosts patches the scene-result host in place.
    assert js.count('document.getElementById("scene-view")') == 3
    assert js.count(
        'scene.scrollIntoView({ behavior: S.mapReducedMotion ? "auto" : "smooth",'
        ' block: "start" });'
    ) == 2

    # ---------- #289 new canon: floating nav pill (9 sections) ----------
    # The world screen renders the pill INSTEAD of the topbar; 9 sections,
    # same hash routes (and order) as the topbar tabs.
    assert 'el("nav", { class: "world-pill", "aria-label": "Разделы" },' in js
    pill_start = js.index("const PILL_TABS = [")
    pill_block = js[pill_start:js.index("];", pill_start)]
    for route in ('"#/world"', '"#/chat"', '"#/people"', '"#/inventory"',
                  '"#/shop"', '"#/tasks"', '"#/desire"', '"#/profile"',
                  '"#/appearance"'):
        assert route in pill_block, route

    # ---------- #289 new canon: critical events ----------
    # Criticality is derived client-side from the event type (the backend model
    # carries no criticality field); a payload.critical === true hook is
    # honored for future backend use. Critical rows get the .ev-critical
    # terracotta callout; the map marker gets the .critical tint.
    assert "const CRITICAL_EVENT_TYPES = new Set([" in js
    assert "function isCriticalEvent(ev)" in js
    assert "CRITICAL_EVENT_TYPES.has(ev.event_type)" in js
    assert "ev.payload.critical === true" in js
    assert 'class: `ev${critical ? " ev-critical" : ""}`' in js
    assert ".world-panel .ev.ev-critical { border-left-color: var(--w-critical); }" in css

    # World-scoped warm palette (canon §14): every token lives under
    # .world-shell; global :root values stay untouched (variant A). The shell
    # is a fixed full-viewport scroll container (no grid collapse).
    assert ".world-shell {" in css
    assert "--w-bg: #F3ECDF;" in css
    assert "--w-panel: #FFF9EF;" in css
    assert "--w-line: rgba(48, 39, 31, 0.12);" in css
    assert "--w-text: #30271F;" in css
    assert "--w-dim: #69594C;" in css
    assert "--w-accent: #86551E;" in css
    assert "--w-warm: rgba(134, 85, 30, 0.12);" in css
    assert "--w-success: #42613E;" in css
    assert "--w-warning: #805316;" in css
    assert "--w-critical: #A13D32;" in css
    assert "position: fixed;" in css
    assert "inset: 0;" in css
    assert ".world-stage" in css
    assert ".world-panel" in css
    assert ".world-pill" in css
    assert ".world-inspect" in css
    assert ".world-below" in css
    assert ".world-mini-needs" in css
    assert ".world-action-pills" in css
    assert ".world-feed" in css
    assert ".world-tasks-card" in css
    assert ".world-shell .map-toolbar" in css
    assert "#living-map-host" in css
    assert ".world-map-fallback" in css
    assert ".world-events-link" in css

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
    # #289: the scene block is now the LAST child of .world-below (was a middle
    # child of .world-secondary), so the render-site spelling is "sceneBox)));".
    assert "sceneBox)));" in js
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
