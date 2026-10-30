"""#72 — Location / Place screen: read-only subordinate route #/location/{id}.

Approved architecture (binding): ONE new dynamic route matched BEFORE the
static dispatch, NO backend changes, NO new APIs, NO new topbar tab, NO fake
data. The screen is a single scrolling composition (hero → actions →
selector → overview → people → events → details) rendered from EXISTING
endpoints only:

- ONE /locations fetch resolves the requested place (world-scoped catalog
  with occupants) — no /locations/{id} detail call;
- people = the occupants roster (name + kind), no invented bios;
- events = GET /world/events?since=0&limit=100 client-filtered by
  location_id, labeled «События в загруженной выборке» — never claimed to be
  complete history; the request has its own failure/retry state, independent
  of the main render;
- world context is labeled «Время мира» / «Погода в мире» (world scope,
  never local effects); unknown time stays unavailable (no zero-fallback);
- read-only actions only: «К карте» (gotoWorld() with the inline manual-focus
  mechanism — mapLink() itself untouched), «Обновить», and «Открыть диалог»
  ONLY when occupants include NPCs (kind === "npc") AND the viewed place is
  the player's CURRENT location (locId === S.character.location_id) —
  validator: chat reads NPCs at the player's CURRENT location, so a remote
  place with NPCs must not promise dialogue it cannot show (the affordance
  is honestly absent for remote places); no type-derived
  buttons, no leave button, and NO /scene/act, /visual/scenes or /travel
  calls from the place view (those target the player's CURRENT location —
  wrong-location hazard);
- hero: warm gradient + decorative type icon + real name/type + the honest
  «Фото места недоступно» note — no stock or fabricated imagery;
- states separated: loading / «Место не найдено» (unknown id) / empty roster
  / events failure / main failure with retry; stale responses are dropped
  (request generation + current hash + S.user/S.character + host isConnected
  — the #66 precedent). Review fixes: a leading-zero deep link
  (#/location/05) is normalized to the canonical hash BEFORE loading via
  history.replaceState (fires no hashchange — no re-dispatch), otherwise the
  hash guards would strand the screen on «Загрузка…»; the events request
  family owns a SEPARATE sequence (placeEventsSeq) so an events retry never
  invalidates an in-flight main load; all four guards carry the session
  checks. Malformed ids (#/location/abc) fall through to viewWorld —
  ACCEPTED as intended: a non-numeric id is not a place route and produces
  no dead state. Validator: /world and /weather are fetched into LOCALS and
  become shared state (S.world/S.weather) ONLY after the stale guard passes
  — a stale response must not mutate shared world/weather for a dropped
  screen (other screens read S.world/S.weather, so the shared write is kept
  but moved after the guard);
- entry points: «О месте» link beside the current-location summary on
  #/world, plus the compact location selector on the Place screen itself;
- CSS: scoped .place-* rules inserted AFTER the byte-pinned first media
  blocks (m146) and BEFORE the #175 conn-banner tail (m175 keeps the banner
  the last rule); NO new --w-line definition (the pinned token count stays
  4); the m162 gotoWorld() call-site count moved 4→5 honestly (#72 comment).

Byte-pins over backend/app/web/app.js + backend/app/web/style.css, in the
m157/m170/m176/m237 house style (exact literal counts — update deliberately,
never weaken).
"""

import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
APP_JS = os.path.join(ROOT, "backend/app/web/app.js")
APP_CSS = os.path.join(ROOT, "backend/app/web/style.css")


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _view_location_body(js):
    """The viewLocation loader body — the place view's fetch/guard layer."""
    start = js.index("async function viewLocation(")
    end = js.index("function renderPlace(", start)
    return js[start:end]


def _render_place_body(js):
    """The renderPlace composition body — the place view's render layer."""
    start = js.index("function renderPlace(")
    end = js.index("async function viewExternal(", start)
    return js[start:end]


# ---------- a: dynamic route match BEFORE static dispatch ----------

def test_place_route_dynamic_match_pins():
    js = _read(APP_JS)
    # The strict-numeric matcher exists exactly once…
    assert js.count("hash.match(/^#\\/location\\/(\\d+)$/)") == 1
    # …dispatches straight into the place view…
    assert js.count(
        "if (placeMatch) return viewLocation(Number(placeMatch[1]));") == 1
    # …and sits BEFORE the static dispatch line (dynamic-first contract).
    assert js.index("hash.match(/^#\\/location\\/(\\d+)$/)") < js.index(
        "(routes[hash] || viewWorld)()")
    # The static registry is untouched: no #/location entry inside it —
    # the place route is dynamic, not a registry row.
    routes_def = js.index("const routes = {")
    routes_end = js.index("};", routes_def)
    assert js[routes_def:routes_end].count('"#/location') == 0
    # No new topbar tab: the tabs array never mentions the place route.
    assert js.count('["#/location"') == 0


# ---------- b: «О месте» entry link ----------

def test_place_entry_link_pins():
    js = _read(APP_JS)
    # The link-over beside the current-location summary, exactly once.
    assert js.count('"О месте"') == 1
    assert js.count('class: "place-link"') == 1
    # href pattern: the CURRENT location's id, template-built.
    assert js.count('href: `#/location/${currentLoc.id}` }, "О месте")') == 1


# ---------- c: honest literals ----------

def test_place_honest_literal_pins():
    js = _read(APP_JS)
    # The hero's photo honesty note, exactly once.
    assert js.count('"Фото места недоступно"') == 1
    # Events are a loaded SAMPLE, never complete history…
    assert js.count('"События в загруженной выборке"') == 1
    # …with the honest empty copy.
    assert js.count('"В загруженной выборке событий этого места нет."') == 1
    # Missing place state.
    assert js.count('"Место не найдено"') == 1
    # World scope is explicit: «Время мира» renders once (template form);
    # «Погода в мире» appears twice — both branches of the same ternary
    # (readable weather vs honest «недоступна»).
    assert js.count("`Время мира: ${") == 1
    assert js.count("Погода в мире") == 2


# ---------- d: no scene/visual/travel calls from the place view ----------

def test_place_view_no_wrong_location_calls():
    js = _read(APP_JS)
    for body in (_view_location_body(js), _render_place_body(js)):
        # /scene/act and /visual/scenes target the player's CURRENT
        # location — calling them from a viewed place would be a
        # wrong-location hazard. Zero inside the place view.
        assert body.count('api("/scene/act"') == 0
        assert body.count('api("/visual/scenes"') == 0
        assert body.count("/travel/plan") == 0
    # The file-wide call counts are unchanged (m153/m154/m151 pins).
    assert js.count('api("/scene/act"') == 1
    assert js.count('api("/visual/scenes", { method: "POST",') == 1
    assert js.count("/travel/plan") == 1


# ---------- e: «Открыть диалог» gated on NPCs ----------

def test_place_dialog_gate_pins():
    js = _read(APP_JS)
    body = _render_place_body(js)
    # The dialog link exists only inside the doubly-gated ternary…
    assert body.count('"Открыть диалог"') == 1
    assert body.count('kind === "npc"') == 1
    # …gated on the location equality TOO (validator): chat reads NPCs at
    # the player's CURRENT location, so a remote place never shows the
    # affordance. The combined gate literal, exactly once file-wide.
    assert js.count("(locId === S.character.location_id && hasNpc)") == 1
    assert body.count("locId === S.character.location_id") == 1
    # …and the NPC half of the gate is the occupants check, exactly once
    # file-wide (mapNpcMarkers' own kind filter is separate).
    assert js.count('occupants.some((o) => o.kind === "npc")') == 1
    # Navigation-only anchor to the existing chat route.
    assert js.count('href: "#/chat"') == 1


# ---------- f: data contract + stale guards ----------

def test_place_data_and_stale_guard_pins():
    js = _read(APP_JS)
    # ONE /locations fetch resolves the place — no /locations/{id} detail
    # call from the place view.
    vbody = _view_location_body(js)
    assert vbody.count('api("/locations")') == 1
    assert vbody.count('api("/locations/${') == 0
    # World context comes from the existing /world + /weather endpoints.
    assert vbody.count('api("/world")') == 1
    assert vbody.count('api("/weather")') == 1
    # Validator: the reads land in LOCALS first — the shared S.world /
    # S.weather writes happen ONLY after the stale guard (a stale response
    # must not mutate shared state for a dropped screen).
    assert vbody.count("world = await api(\"/world\");") == 1
    assert vbody.count("weather = await api(\"/weather\");") == 1
    assert vbody.count("S.world = await api(") == 0
    assert vbody.count("S.weather = await api(") == 0
    assert vbody.count("S.world = world;") == 1
    assert vbody.count("S.weather = weather;") == 1
    # Ordering: fetch → guard → shared assignment (index comparison inside
    # the loader body).
    idx_fetch = vbody.index("world = await api(\"/world\");")
    idx_guard = vbody.index("if (seq !== placeLoadSeq || location.hash !== viewHash")
    idx_assign = vbody.index("S.world = world;")
    assert idx_fetch < idx_guard < idx_assign
    # Events: bounded sample fetch, client-filtered by location_id.
    assert js.count('api("/world/events?since=0&limit=100")') == 1
    assert js.count("feed.filter((ev) => ev.location_id === locId)") == 1
    # Stale-response guards (#66 precedent): one module-level sequence for
    # the main load…
    assert js.count("let placeLoadSeq = 0;") == 1
    assert js.count("const seq = ++placeLoadSeq;") == 1
    # …and a SEPARATE one for the events family (review F2: an events retry
    # must never invalidate an in-flight main load, and vice versa).
    assert js.count("let placeEventsSeq = 0;") == 1
    assert js.count("const eseq = ++placeEventsSeq;") == 1
    # …each checked on both of its own paths (error + apply); the "(seq" /
    # "(eseq" forms exclude each other's guards. Together with the current
    # hash, session/character presence (all FOUR guards — review F3) and,
    # for the events panel, host connectivity.
    assert js.count("(seq !== placeLoadSeq") == 2
    assert js.count("(eseq !== placeEventsSeq") == 2
    # #74: +2 — the Home screen's own guards (fetch-error + apply) use the
    # same shape against homeLoadSeq/viewHash; the place slices above are
    # untouched. #75: +2 — the Career screen's guards (fetch-error + apply)
    # use the same shape against careerLoadSeq/viewHash.
    assert js.count("location.hash !== viewHash") == 8
    vbody = _view_location_body(js)
    rbody = _render_place_body(js)
    assert vbody.count("!S.user || !S.character") == 2
    assert rbody.count("!S.user || !S.character") == 2
    assert js.count("!eventsPanel.isConnected") == 2
    # Review F1 regression pin: the canonical-hash normalization exists
    # exactly once, BEFORE the loading render — a leading-zero deep link
    # (#/location/05) must not strand the screen on «Загрузка…».
    assert js.count(
        'history.replaceState(null, "", "#/location/" + locId)') == 1
    assert js.count('location.hash !== "#/location/" + locId') == 1
    # The normalization sits before the first topbar render in the loader.
    assert js.index("history.replaceState(") < js.index(
        'app.replaceChildren(topbar("#/world"), loading);',
        js.index("async function viewLocation("))


# ---------- g: place view chrome + read-only actions ----------

def test_place_view_chrome_pins():
    js = _read(APP_JS)
    vbody = _view_location_body(js)
    rbody = _render_place_body(js)
    # The Place screen highlights «Мир»: topbar("#/world") on the loading
    # and missing states (2) and on the final render (1).
    assert vbody.count('topbar("#/world")') == 2
    assert rbody.count('topbar("#/world")') == 1
    # «К карте» always — via gotoWorld() with the inline manual-focus
    # mechanism (mapLink() itself is untouched; m162's call-site count
    # moved 4→5 with a #72 comment).
    assert rbody.count('"К карте"') == 1
    assert rbody.count("gotoWorld();") == 1
    assert rbody.count("S.mapFocusManual = true;") == 1
    assert rbody.count("S.mapFocusLocationId = locId;") == 1
    # Refresh re-runs the loader; the selector navigates through the hash
    # (single dispatch — never a direct view call next to a hash assignment).
    assert rbody.count('"Обновить"') == 1
    assert js.count("location.hash = `#/location/${id}`") == 1
    # XSS hygiene holds file-wide (m146 mirror).
    assert js.count("innerHTML") == 0
    # No type-derived action labels anywhere (inventory verbs stay gone).
    for label in ("Съесть", "Выпить", "Использовать", "Выбросить"):
        assert js.count(label) == 0, label


# ---------- h: CSS placement + uniqueness ----------

def test_place_css_pins():
    css = _read(APP_CSS)
    rules = (
        ".place-shell {", ".place-hero {", ".place-hero-icon {",
        ".place-hero-text {", ".place-hero-note {", ".place-actions {",
        ".place-chat-link {", ".place-selector {", ".place-roster {",
        ".place-roster-row {", ".place-events-note {", ".place-event-list {",
        ".place-event-row {", ".place-detail-row {", ".place-detail-label {",
        ".location-view .place-link {",
    )
    for rule in rules:
        assert css.count(rule) == 1, rule
    # Placement: AFTER the byte-pinned first media blocks (m146) and the
    # appearance block, BEFORE the #175 conn-banner tail (m175 keeps the
    # banner the last rule in the file).
    idx_first_small = css.index("@media (max-width: 520px) {")
    idx_appear = css.index(".appearance-shell {")
    idx_place = css.index(".place-shell {")
    idx_banner = css.index(".conn-banner {")
    assert idx_first_small < idx_appear < idx_place < idx_banner
    # Append-only tail convention: the banner block is still the last rule.
    assert css.rstrip().endswith(".conn-banner.hidden { display: none; }")
    # No new --w-line definition (m170/m173/m175/m187/m237 pin the total at 4).
    assert css.count("--w-line:") == 4
