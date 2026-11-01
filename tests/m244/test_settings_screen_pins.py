"""#79 — Settings screen: subordinate static route #/settings.

Approved architecture (binding), decisions recorded for the stranger
reader:

- Subordinate static route, NO tab: "#/settings" sits in the `routes`
  registry (17 rows total — the count comment is pinned, the stale
  16/15 counts rejected) but the topbar tabs array is UNTOUCHED — the
  nine ordinary tabs stay nine (the exact tabs literal is pinned). The
  screen highlights «Профиль» (its parent surface) and is reached from
  the profile's settings link («Настройки →»).
- ONE preset source of truth: the settings preset control and the map
  toolbar's quality buttons both land in setMapQuality(); the choice
  persists via persistSettings(). The preset NEVER touches the motion
  toggle (independent controls — acceptance 3).
- ONE motion state: the map toolbar toggle (setMapReducedMotion) and
  the settings toggle both land in setReducedMotionPref(); the
  EFFECTIVE flag is OS prefers-reduced-motion OR the user pref
  (recomputed, never a second state); the OS sample is read live by
  osReducedMotion() and NEVER persisted.
- Honest rows: image-generation quality (server-side, no quality
  param), sound (no sound engine), cross-device sync (nothing) are
  stated as unavailable — no disabled dropdowns, no fake controls.
- Persistence: vl1.settings.v1 in localStorage, defensive read
  (try/catch around getItem AND JSON.parse, per-field enum/range
  validation), best-effort write on every change, sessionOnly flag on
  read- OR write-denial. Boot applies the stored settings BEFORE the
  first render (loadSettings(); applyStoredSettings(); before route()),
  and a matchMedia change listener recomputes the effective flag
  mid-session.
- UI scale: 80–120% step 10, validated (never clamped — early-return
  form), applied as zoom on documentElement (100 = no inline style).
- Guide: the creator-guide pattern (#78) — scrollIntoView + focus, NO
  hash assignment, scroll honours the effective-motion policy (the
  same ternary literal as the creator guide).
- CSS: the .settings-shell block is inserted BEFORE the #175
  conn-banner tail (the m175 append-only tail pin keeps the banner the
  last rule); warm tokens are mirrored locally and NO --w-line
  definition is added (file-wide count stays 4); NO new
  @media (prefers-reduced-motion: reduce) block (count stays 3). The
  settings-reduced-motion class hook (toggled on documentElement) is
  CONSUMED by exactly two class-scoped freeze rules OUTSIDE any media
  query: the landing-shell freeze (the same !important trio as the OS
  media block, for users who reduce motion in settings without an
  OS-level preference) and the typing-dots mirror. The map freeze is
  NOT duplicated — .map-card.reduced-motion (same effective flag)
  already covers the map surfaces.

Byte-pins over backend/app/web/app.js + backend/app/web/style.css, in
the m157/m170/m176/m237/m238/m239/m240/m241/m242/m243 house style
(exact literal counts — update deliberately, never weaken; anchors are
commas / line-ends / indentation, NEVER bare identifier prefixes).
"""

import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
APP_JS = os.path.join(ROOT, "backend/app/web/app.js")
APP_CSS = os.path.join(ROOT, "backend/app/web/style.css")


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _routes_slice(js):
    """The static `routes` registry object."""
    start = js.index("const routes = {")
    return js[start:js.index("};", start)]


def _topbar_body(js):
    """The topbar() body — the tabs array must never grow a Settings tab."""
    start = js.index("function topbar(active) {")
    return js[start:js.index("\n}", start)]


def _settings_block(js):
    """The whole #79 block: SETTINGS_DEFAULTS through renderSettings."""
    start = js.index("/* ---------- #79: Settings screen (#/settings)")
    return js[start:js.index("/* ---------- profile (§77;", start)]


def _render_settings_body(js):
    """The renderSettings() body — the screen's DOM composition."""
    start = js.index("function renderSettings() {")
    return js[start:js.index("/* ---------- profile (§77;", start)]


def _set_map_quality_body(js):
    start = js.index("function setMapQuality(quality) {")
    return js[start:js.index("function setMapReducedMotion(", start)]


def _set_map_reduced_motion_body(js):
    start = js.index("function setMapReducedMotion(enabled) {")
    return js[start:js.index("function renderWorld(", start)]


def _set_reduced_motion_pref_body(js):
    start = js.index("function setReducedMotionPref(enabled) {")
    return js[start:js.index("function setUiScale(", start)]


def _set_ui_scale_body(js):
    start = js.index("function setUiScale(pct) {")
    return js[start:js.index("function resetSettings()", start)]


def _reset_settings_body(js):
    start = js.index("function resetSettings() {")
    return js[start:js.index("/* #79: stale-render guard", start)]


def _load_settings_body(js):
    start = js.index("function loadSettings() {")
    return js[start:js.index("function persistSettings()", start)]


def _persist_settings_body(js):
    start = js.index("function persistSettings() {")
    return js[start:js.index("function applyReducedMotionClass()", start)]


def _boot_tail(js):
    """From the boot comment to EOF — the settings boot calls live here."""
    return js[js.index("/* ---------- boot ---------- */"):]


def _settings_css(css):
    """The #79 .settings-shell block, up to the #175 conn-banner tail."""
    start = css.index("/* ---------- #79: Settings screen (#/settings)")
    return css[start:css.index("/* #175: connection banner", start)]


# ---------- 1: subordinate static route, no tab ----------

def test_settings_route_registry_pins():
    js = _read(APP_JS)
    # The static registry entry exists exactly once…
    assert js.count('"#/settings": viewSettings') == 1
    # …inside the routes object (dispatched through the static dispatch).
    routes = _routes_slice(js)
    assert routes.count('"#/settings": viewSettings') == 1
    # The registry comment records the honest row count (16→17 with #79)
    # and keeps the #74/#75/#76 notes intact; the stale counts are
    # rejected.
    assert js.count("17 rows total") == 1
    assert js.count("16 rows total") == 0
    assert js.count("15 rows total") == 0
    assert js.count(
        '// "#/events" entry and #79\'s static "#/settings" entry — '
        '17 rows total;') == 1
    # The #79 registry comment block is present, exactly once.
    assert js.count(
        "// #79: the Settings screen is a subordinate STATIC route — "
        "no topbar") == 1
    # NO topbar tab: the tabs array never mentions the settings route…
    assert js.count('["#/settings"') == 0
    # …and neither the route nor a «Настройки» label leaks into the
    # topbar body.
    topbar = _topbar_body(js)
    assert topbar.count("settings") == 0
    assert topbar.count("Settings") == 0
    assert topbar.count("#/settings") == 0
    assert topbar.count("Настройки") == 0
    # The nine-tab invariant is UNTOUCHED: the exact tabs literal (nine
    # ordinary tabs, the admin tab pushed conditionally below it) is
    # pinned as a full multi-line block.
    assert js.count(
        'const tabs = [["#/world", "Мир"], ["#/chat", "Чат"], '
        '["#/people", "Люди"],\n'
        '    ["#/inventory", "Инвентарь"], ["#/shop", "Рынок"], '
        '["#/tasks", "Дела"],\n'
        '    ["#/desire", "Цель"], ["#/profile", "Профиль"], '
        '["#/appearance", "Образ"]];') == 1


# ---------- 2: profile entry link + topbar mount ----------

def test_settings_entry_link_pins():
    js = _read(APP_JS)
    # The standing link beside the profile's roles block, exactly once —
    # muted, vocabulary-consistent with the #75 career link.
    assert js.count('el("a", { href: "#/settings", class: "muted" }, '
                    '"Настройки →")') == 1
    # The profile link is the file's only #/settings hash link.
    assert js.count('href: "#/settings"') == 1
    # viewSettings mounts the topbar highlighting «Профиль» (no Settings
    # tab exists) — exactly once, in the final render.
    body = _render_settings_body(js)
    assert body.count('app.replaceChildren(topbar("#/profile"),') == 1
    # The screen's shell is the single .settings-shell main.
    assert js.count('el("main", { class: "settings-shell" },') == 1


# ---------- 3: section order ----------

def test_settings_section_order_pins():
    js = _read(APP_JS)
    body = _render_settings_body(js)
    # The five section headings, each exactly once…
    for heading in ('el("h2", {}, "Графика")',
                    'el("h2", {}, "Интерфейс")',
                    'el("h2", {}, "Специальные возможности")',
                    'el("h2", {}, "Звук")',
                    'el("h2", {}, "Сохранение")'):
        assert body.count(heading) == 1, heading
    # …in that index order (the mockup's order, minus the rejected
    # sections).
    assert body.index('el("h2", {}, "Графика")') < \
        body.index('el("h2", {}, "Интерфейс")') < \
        body.index('el("h2", {}, "Специальные возможности")') < \
        body.index('el("h2", {}, "Звук")') < \
        body.index('el("h2", {}, "Сохранение")')
    # NO «Gameplay»/«Язык»/«Language» section anywhere in the settings
    # body — those mockup sections are rejected (no such capability).
    assert body.count("Gameplay") == 0
    assert body.count("gameplay") == 0
    assert body.count("Язык") == 0
    assert body.count("Language") == 0
    # Exactly five focusable sections — the guide targets, nothing else.
    assert body.count('tabindex: "-1"') == 5
    # The composition: the five sections stacked in the same order
    # inside .settings-shell (single page, NO nav-rail).
    assert body.count(
        "secGraphics,\n"
        "      secInterface,\n"
        "      secMotion,\n"
        "      secSound,\n"
        "      secStorage));") == 1


# ---------- 4: preset control — ONE setter ----------

def test_settings_preset_control_pins():
    js = _read(APP_JS)
    body = _render_settings_body(js)
    # The fieldset with its legend, each exactly once.
    assert body.count(
        'el("fieldset", { class: "settings-preset-group" },') == 1
    assert body.count('el("legend", {}, "Пресет графики")') == 1
    # Three radios, ONE name (the .map builds all three from the single
    # name literal).
    assert body.count('type: "radio", name: "settings-preset", value,') == 1
    # The three options with their consequence lines, verbatim, each
    # exactly once.
    assert body.count(
        '["high", "High", '
        '"Полная яркость ночных огней и погодных эффектов карты"]') == 1
    assert body.count(
        '["balanced", "Balanced", '
        '"Приглушённые огни и погода — рекомендуемый баланс нагрузки"]') == 1
    assert body.count(
        '["low-mobile", "Low-Mobile",\n'
        '        "Без фоновых анимаций; меньше маркеров, огней и событий '
        '— для слабых устройств"],') == 1
    # The «рекомендуется» marker is ONLY on balanced: one mark span,
    # gated by the single balanced conditional.
    assert body.count(
        'el("span", { class: "settings-preset-mark" }, "рекомендуется")') == 1
    assert body.count('value === "balanced"') == 1
    # The truth note, exactly once.
    assert body.count(
        '"Пресет влияет только на карту и анимации — на события мира '
        'он не влияет."') == 1
    # The preset onchange routes to setMapQuality — ONE setter shared
    # with the map toolbar's quality buttons (file-wide: toolbar +
    # settings = 2 call sites, 3 references with the definition).
    assert body.count('onchange: () => setMapQuality(value)') == 1
    assert js.count("setMapQuality(value)") == 2
    assert js.count("setMapQuality(") == 3
    # setMapQuality's body sets the preset on the settings object (ONE
    # preset source of truth) and persists it…
    smq = _set_map_quality_body(js)
    assert smq.count("S.settings.preset = quality;") == 1
    assert js.count("S.settings.preset = quality;") == 1
    assert smq.count("persistSettings();") == 1
    # …and the preset path contains NO motion writes (independence —
    # acceptance 3).
    assert smq.count("reducedMotion") == 0
    assert smq.count("ReducedMotion") == 0
    assert smq.count("setReducedMotionPref") == 0


# ---------- 5: honest rows ----------

def test_settings_honest_rows_pins():
    js = _read(APP_JS)
    body = _render_settings_body(js)
    block = _settings_block(js)
    # NEEDS-BACKEND rows, exact copy, each exactly once: the sim has no
    # image-quality param, no sound engine, no cross-device sync.
    assert body.count(
        '"Качество генерации изображений — недоступно для настройки"') == 1
    assert body.count(
        '"Генерация изображений идёт на сервере без параметра '
        'качества"') == 1
    assert body.count('"Звук — недоступен"') == 1
    assert body.count('"В игре пока нет звукового движка"') == 1
    assert body.count(
        '"Синхронизация между устройствами — недоступна"') == 1
    # The Сохранение section's honest lines, exact copy.
    assert body.count(
        'el("div", {}, "Настройки сохраняются автоматически в этом '
        'браузере")') == 1
    assert body.count(
        '"Это не сохранение игры — прогресс хранится на сервере"') == 1
    # The sessionOnly disclosure (storage-denied honesty), exactly once.
    assert body.count(
        '"Применено только в этой сессии — браузер не дал сохранить '
        'настройки"') == 1
    assert body.count(
        'st.sessionOnly ? el("div", { class: "muted settings-note" },') == 1
    # NO disabled dropdown for image quality: the whole #79 block has no
    # `disabled` attribute and no <select> at all — the honest rows are
    # plain text, not greyed-out controls.
    assert block.count("disabled") == 0
    assert block.count('el("select"') == 0


# ---------- 6: UI scale ----------

def test_settings_ui_scale_pins():
    js = _read(APP_JS)
    body = _render_settings_body(js)
    # The range input: 80–120, step 10, exactly once.
    assert body.count(
        'id: "settings-ui-scale", type: "range", min: "80", max: "120", '
        'step: "10",') == 1
    # The label is bound to the input (for attribute), exactly once.
    assert body.count(
        'el("label", { class: "settings-field", for: "settings-ui-scale" '
        '},') == 1
    # The live label template: the initial value from the settings
    # object…
    assert body.count("Масштаб интерфейса: ${st.uiScale}%") == 1
    # …and the oninput update from the (validated) applied value.
    assert body.count("Масштаб интерфейса: ${S.settings.uiScale}%") == 1
    assert body.count("value: String(st.uiScale),") == 1
    assert body.count("setUiScale(Number(ev.target.value));") == 1
    assert body.count(
        "scaleText.textContent = `Масштаб интерфейса: "
        "${S.settings.uiScale}%`;") == 1
    assert body.count('"Применяется ко всему интерфейсу сразу"') == 1
    # setUiScale validation: 80..120 step 10, integer, early-return form
    # (anything else is ignored, NEVER clamped).
    sus = _set_ui_scale_body(js)
    assert js.count(
        'if (typeof pct !== "number" || !Number.isInteger(pct)\n'
        '    || pct < 80 || pct > 120 || pct % 10 !== 0) return;') == 1
    assert sus.count("Math.min") == 0
    assert sus.count("Math.max") == 0
    # setUiScale applies + persists (file-wide: definition + the ONE
    # oninput call site).
    assert sus.count("applyUiScale();") == 1
    assert sus.count("persistSettings();") == 1
    assert js.count("setUiScale(") == 2
    # applyUiScale: zoom on the root element; 100 means no inline style
    # at all (removeProperty), each exactly once.
    assert js.count(
        'document.documentElement.style.zoom = '
        'String(S.settings.uiScale / 100);') == 1
    assert js.count(
        'document.documentElement.style.removeProperty("zoom");') == 1


# ---------- 7: motion — ONE toggle, ONE state ----------

def test_settings_motion_pins():
    js = _read(APP_JS)
    body = _render_settings_body(js)
    # Exactly ONE motion toggle in the settings body (the mockup's
    # «Animation and Transitions» is merged into it — two toggles would
    # be fake granularity): one checkbox, one label.
    assert body.count('type: "checkbox"') == 1
    assert body.count('el("span", {}, "Уменьшенное движение")') == 1
    # The toggle reflects the USER pref and routes to the shared pref
    # setter.
    assert body.count(
        '...(st.reducedMotion ? { checked: "checked" } : {}),') == 1
    assert body.count(
        "onchange: (ev) => setReducedMotionPref(ev.target.checked),") == 1
    # The description note, exactly once.
    assert body.count(
        '"Отключает плавную прокрутку и фоновые анимации"') == 1
    # The OS status line is CONDITIONAL on osReducedMotion() — it
    # explains why motion can stay reduced when the toggle is off.
    assert body.count(
        'osReducedMotion() ? el("p", { class: "muted settings-note" },') == 1
    assert body.count(
        '"Также включено системными настройками устройства"') == 1
    # setMapReducedMotion DELEGATES to setReducedMotionPref — its body
    # has no direct S.mapReducedMotion assignment (one motion state).
    smrm = _set_map_reduced_motion_body(js)
    assert smrm.count("setReducedMotionPref(enabled);") == 1
    assert smrm.count("S.mapReducedMotion =") == 0
    # setReducedMotionPref: sets the USER pref, recomputes the EFFECTIVE
    # flag (OS OR user), re-applies the class, persists, rerenders.
    srmp = _set_reduced_motion_pref_body(js)
    assert srmp.count("S.settings.reducedMotion = !!enabled;") == 1
    assert srmp.count(
        "S.mapReducedMotion = osReducedMotion() || !!enabled;") == 1
    assert srmp.count("applyReducedMotionClass();") == 1
    assert srmp.count("persistSettings();") == 1
    assert srmp.count("rerenderLivingMap();") == 1
    # File-wide call-site census: setReducedMotionPref has 4 references
    # (1 comment + 1 definition + 2 call sites: the map toolbar
    # delegation and the settings toggle); setMapReducedMotion has 2
    # (definition + the map toolbar's onchange).
    assert js.count("setReducedMotionPref(") == 4
    assert js.count("setMapReducedMotion(") == 2
    # The effective flag is written in exactly THREE places:
    # applyStoredSettings (boot), setReducedMotionPref (user change),
    # the boot matchMedia listener (OS change).
    assert js.count("S.mapReducedMotion =") == 3
    # osReducedMotion: 5 references (1 comment + 1 definition + 3 call
    # sites: applyStoredSettings, setReducedMotionPref, the settings
    # status line). The OS sample is read live, never stored.
    assert js.count("osReducedMotion()") == 5
    assert js.count("function osReducedMotion() {") == 1
    # The S-object init still samples the OS at startup (the pre-#79
    # line, untouched).
    assert js.count(
        'mapReducedMotion: window.matchMedia('
        '"(prefers-reduced-motion: reduce)").matches,') == 1


# ---------- 8: persistence ----------

def test_settings_persistence_pins():
    js = _read(APP_JS)
    ls = _load_settings_body(js)
    ps = _persist_settings_body(js)
    boot = _boot_tail(js)
    # The storage key: ONE read site (loadSettings getItem) + ONE write
    # site (persistSettings setItem) = 2 references to the same key.
    assert js.count('"vl1.settings.v1"') == 2
    # Defensive read: try/catch around getItem AND around the JSON
    # decode, each exactly once.
    assert ls.count(
        'try { raw = window.localStorage.getItem("vl1.settings.v1"); }') == 1
    assert ls.count(
        "catch (e) { S.settings.sessionOnly = true; return; }") == 1
    assert ls.count(
        "try { data = JSON.parse(raw); } catch (e) { data = null; }") == 1
    # Per-field enum/range validation literals, each exactly once — any
    # violation falls back to THAT field's default.
    assert ls.count(
        '["high", "balanced", "low-mobile"].includes(data.preset)') == 1
    assert ls.count('typeof data.reducedMotion === "boolean"') == 1
    assert ls.count(
        'typeof data.uiScale === "number" && '
        'Number.isInteger(data.uiScale)') == 1
    assert ls.count(
        "data.uiScale >= 80 && data.uiScale <= 120 && "
        "data.uiScale % 10 === 0") == 1
    # The canonical defaults object, exactly once (referenced 3 times
    # file-wide: definition + loadSettings + resetSettings).
    assert js.count(
        "const SETTINGS_DEFAULTS = { preset: \"balanced\", "
        "reducedMotion: false, uiScale: 100 };") == 1
    assert js.count("SETTINGS_DEFAULTS") == 3
    # sessionOnly: set on read-denial AND write-denial (2 sites), 4
    # references file-wide (2 sets + the persistSettings guard + the
    # resetSettings carry-over).
    assert js.count("S.settings.sessionOnly = true") == 2
    assert js.count("S.settings.sessionOnly") == 4
    assert ps.count("if (S.settings.sessionOnly) return;") == 1
    assert ps.count("catch (e) { S.settings.sessionOnly = true; }") == 1
    # Boot: loadSettings(); applyStoredSettings(); BEFORE route() — the
    # stored preset replaces the hardcoded default before the first
    # render. loadSettings is called exactly once file-wide (boot);
    # applyStoredSettings twice (boot + resetSettings).
    assert js.count("loadSettings();") == 1
    assert js.count("applyStoredSettings();") == 2
    assert boot.count("loadSettings();") == 1
    assert boot.count("applyStoredSettings();") == 1
    assert boot.count("route();") == 1
    assert boot.index("loadSettings();") < \
        boot.index("applyStoredSettings();") < boot.index("route();")
    # The matchMedia change listener: the OS motion setting can change
    # mid-session — the listener recomputes the effective flag,
    # re-applies the class, rerenders, and re-renders the open settings
    # screen.
    assert js.count(
        'const motionQuery = window.matchMedia('
        '"(prefers-reduced-motion: reduce)")') == 1
    assert boot.count('motionQuery.addEventListener("change", () => {') == 1
    assert js.count(
        "S.mapReducedMotion = motionQuery.matches\n"
        "    || !!(S.settings && S.settings.reducedMotion);") == 1
    # File-wide matchMedia samples: the S-object init, osReducedMotion,
    # and the boot listener = 3.
    assert js.count(
        'window.matchMedia("(prefers-reduced-motion: reduce)")') == 3


# ---------- 9: motion CSS hook ----------

def test_settings_motion_css_hook_pins():
    js = _read(APP_JS)
    css = _read(APP_CSS)
    # The documentElement class toggle: exactly ONE application site
    # (applyReducedMotionClass)…
    assert js.count(
        'document.documentElement.classList.toggle('
        '"settings-reduced-motion",') == 1
    # …one definition, three call sites (applyStoredSettings,
    # setReducedMotionPref, the boot matchMedia listener).
    assert js.count("function applyReducedMotionClass() {") == 1
    assert js.count("applyReducedMotionClass();") == 3
    # The class hook is CONSUMED by exactly TWO class-scoped freeze
    # rules (the m244 dead-hook finding was closed): 4 references
    # file-wide — 3 selector lines in the landing-shell freeze + 1
    # typing-dots mirror rule.
    assert css.count(".settings-reduced-motion") == 4
    # (a) The landing-shell class freeze: the same !important trio as
    # the OS media block, for users who reduce motion in settings
    # without an OS-level preference. The full rule (3 selector lines
    # + 3 declarations) is present exactly once…
    assert css.count(
        ".settings-reduced-motion .landing-shell *,\n"
        ".settings-reduced-motion .landing-shell *::before,\n"
        ".settings-reduced-motion .landing-shell *::after {\n"
        "  scroll-behavior: auto !important;\n"
        "  transition: none !important;\n"
        "  animation: none !important;\n"
        "}") == 1
    # …and each selector line is pinned individually (comma/EOL
    # anchored — the `*,` line is NOT a substring of the `*::before,`
    # line).
    assert css.count(".settings-reduced-motion .landing-shell *,") == 1
    assert css.count(
        ".settings-reduced-motion .landing-shell *::before,") == 1
    assert css.count(
        ".settings-reduced-motion .landing-shell *::after {") == 1
    # The decision comment records that the map freeze is NOT
    # duplicated here: .map-card.reduced-motion (same effective flag)
    # already covers the map surfaces.
    assert css.count(
        "/* #79: the settings toggle's class hook — same freeze as the "
        "OS media\n"
        "   query, for users who reduce motion in settings without an "
        "OS-level\n"
        "   preference. The map freeze is NOT duplicated here: "
        ".map-card.reduced-\n"
        "   motion (same effective flag) already covers the map "
        "surfaces. */") == 1
    # (b) The typing-dots mirror rule, present exactly once (the OS
    # media block keeps its own rule — the class rule is the
    # settings-toggle mirror).
    assert css.count(
        ".settings-reduced-motion .typing-dots i { animation: none; "
        "opacity: 1; }") == 1
    assert css.count(
        "/* #79: settings-toggle mirror of the typing-dots freeze. */") == 1
    # (c) NO new @media (prefers-reduced-motion: reduce) block was
    # added — the count stays 3 (the pre-#79 OS blocks); the class
    # rules are OUTSIDE any media query.
    assert css.count("@media (prefers-reduced-motion: reduce)") == 3
    # Token discipline: NO --w-line definition inside the .settings-shell
    # block (the :root fallback carries the identical value); the
    # file-wide count stays 4.
    block = _settings_css(css)
    assert block.count("--w-line:") == 0
    assert css.count("--w-line:") == 4


# ---------- 10: section guide — navigation only ----------

def test_settings_guide_pins():
    js = _read(APP_JS)
    body = _render_settings_body(js)
    # The guide link class, exactly once (the guideEntry definition).
    assert body.count('class: "settings-guide-link"') == 1
    # The scroll honours the effective-motion policy — the SAME ternary
    # literal as the creator guide (#78): file-wide 2 (creator +
    # settings), settings body 1.
    assert body.count(
        'section.scrollIntoView({ behavior: S.mapReducedMotion '
        '? "auto" : "smooth", block: "start" });') == 1
    assert js.count(
        'section.scrollIntoView({ behavior: S.mapReducedMotion '
        '? "auto" : "smooth", block: "start" });') == 2
    # Focus after the scroll (preventScroll — the scroll already
    # positioned the section): file-wide 2, settings body 1.
    assert body.count("section.focus({ preventScroll: true });") == 1
    assert js.count("section.focus({ preventScroll: true });") == 2
    # NO hash assignment anywhere in the renderSettings body — the guide
    # is navigation-only (the m120/m162 hash counts stay put).
    assert body.count("location.hash") == 0
    # Five numbered entries, section order, each exactly once.
    for entry in ('guideEntry(secGraphics, "1. Графика")',
                  'guideEntry(secInterface, "2. Интерфейс")',
                  'guideEntry(secMotion, "3. Специальные возможности")',
                  'guideEntry(secSound, "4. Звук")',
                  'guideEntry(secStorage, "5. Сохранение")'):
        assert body.count(entry) == 1, entry
    # The guide label and row, each exactly once.
    assert body.count(
        'el("div", { class: "muted settings-eyebrow" }, '
        '"Разделы настроек")') == 1
    assert body.count('el("div", { class: "settings-guide-row" },') == 1


# ---------- 11: reset ----------

def test_settings_reset_pins():
    js = _read(APP_JS)
    body = _render_settings_body(js)
    # The «Сбросить» button, exactly once, calling resetSettings.
    assert body.count(
        'el("button", { type: "button", onclick: () => resetSettings() }, '
        '"Сбросить")') == 1
    # File-wide: definition + the ONE button call site.
    assert js.count("resetSettings()") == 2
    # resetSettings restores SETTINGS_DEFAULTS (sessionOnly survives —
    # the storage capability did not change) and re-applies everything.
    rs = _reset_settings_body(js)
    assert rs.count(
        "S.settings = { ...SETTINGS_DEFAULTS, "
        "sessionOnly: S.settings.sessionOnly };") == 1
    assert rs.count("applyStoredSettings();") == 1
    assert rs.count("persistSettings();") == 1
    assert rs.count("rerenderLivingMap();") == 1
    # …and re-renders the open settings screen if it is current.
    assert rs.count('if (location.hash === "#/settings") viewSettings();') == 1


# ---------- 12: structure + hygiene ----------

def test_settings_structure_and_hygiene_pins():
    js = _read(APP_JS)
    css = _read(APP_CSS)
    block = _settings_block(js)
    # The #79 functions: one definition each, viewSettings BEFORE
    # renderSettings (the m238–m242 subordinate-family layering).
    assert js.count("function viewSettings() {") == 1
    assert js.count("function renderSettings() {") == 1
    assert js.index("function viewSettings() {") < \
        js.index("function renderSettings() {")
    # viewSettings references: registry + 2 call sites (resetSettings,
    # boot listener) + the definition = 4; renderSettings: the ONE call
    # site + the definition = 2.
    assert js.count("viewSettings") == 4
    assert js.count("renderSettings") == 2
    # The session/character guard (the profile-subordinate family),
    # exactly once file-wide.
    assert js.count(
        "if (!S.user || !S.character) { viewCreateCharacter(); return; }") == 1
    # The stale-render guard: the subordinate-family counter, one
    # module-level sequence, checked on its single path.
    assert js.count("let settingsLoadSeq = 0;") == 1
    assert js.count("const seq = ++settingsLoadSeq;") == 1
    assert js.count(
        "if (seq !== settingsLoadSeq || location.hash !== viewHash) "
        "return;") == 1
    # The hero: the h1 and the eyebrow, each exactly once (the file-wide
    # «ВЛ1: Рейнеке» count is 6 — header comment, auth, footer, creator
    # eyebrow, topbar h1, settings hero — so the settings site is pinned
    # by its full literal).
    assert js.count('el("h1", {}, "Настройки")') == 1
    assert block.count(
        'el("div", { class: "muted settings-eyebrow" }, '
        '"ВЛ1: Рейнеке")') == 1
    assert block.count(
        '"Графика, масштаб интерфейса и движение — всё применяется '
        'сразу."') == 1
    assert block.count(
        'el("section", { class: "panel settings-hero" },') == 1
    # Hygiene (m146 mirror): the #79 block builds DOM via el() only.
    assert block.count("innerHTML") == 0
    # CSS: the .settings-shell block exists exactly once, inserted
    # BEFORE the #175 conn-banner tail (the m175 append-only tail pin
    # keeps the banner the last rule).
    assert css.count(".settings-shell {") == 1
    assert css.index(".settings-shell {") < css.index(".conn-banner {")
    assert css.rstrip().endswith(".conn-banner.hidden { display: none; }")
    # The shell's warm token mirror (local, no --w-line definition).
    block_css = _settings_css(css)
    assert block_css.count("--w-bg: #0a1016;") == 1
    # The shell stacks panels by grid gap — the flat sibling margin is
    # zeroed (creator-shell precedent).
    assert block_css.count(
        ".settings-shell .panel + .panel { margin-top: 0; }") == 1
    # The hero: the main rule (padding 22px 24px) + the 640px override
    # (padding 16px) — the selector appears exactly twice.
    assert block_css.count(
        ".settings-shell .settings-hero {\n  padding: 22px 24px;") == 1
    assert block_css.count(
        ".settings-shell .settings-hero { padding: 16px; }") == 1
    assert block_css.count(".settings-shell .settings-hero {") == 2
    # Scope discipline: every occurrence of the new classes is
    # shell-scoped (no global selectors invented).
    for cls in (".settings-preset-mark", ".settings-guide-link",
                ".settings-honest", ".settings-toggle", ".settings-actions",
                ".settings-field", ".settings-note", ".settings-hero",
                ".settings-eyebrow", ".settings-intro", ".settings-guide",
                ".settings-preset-group", ".settings-preset-option",
                ".settings-preset-name", ".settings-preset-note"):
        assert css.count(cls) == css.count(".settings-shell " + cls), cls
