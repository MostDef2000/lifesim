"""#72 — Location / Place screen: subordinate route #/location/{id}.

Approved architecture (binding): ONE new dynamic route matched BEFORE the
static dispatch, NO backend changes, NO new topbar tab, NO fake data. The
screen renders from EXISTING endpoints only:

- ONE /locations fetch resolves the requested place (world-scoped catalog
  with occupants) — no /locations/{id} detail call;
- #303: the screen is a game-shell composition — topbar (back «← Остров»
  with the #92 manual-focus + #161 gotoWorld mechanism + the place title)
  over a .place-grid: a static .place-stage placeholder (honest «Фото места
  недоступно» note, no imagery) on the left and the .place-context panel on
  the right. The old hero band, the «К карте»/«Обновить» button row, the
  location selector, the .place-overview/.place-people panels and the
  always-visible details section are REMOVED;
- #303: the context panel is state-aware — isCurrent =
  locId === S.character.location_id. Current: badge «Вы здесь», the
  «Действия здесь» block (the shared ACTION_VOCABULARY through doAction —
  a button only POSTs a request) and the free-text scene-act bar (the
  existing submitSceneAct path, result re-read into the Place host).
  Remote: badge «Другое место», the player's own location stated («Вы
  сейчас: …») and the ONE move CTA «Пойти сюда» (the existing moveTo());
  NO actions, NO scene-act, NO dialogue on a remote place;
- #303: «Рядом» = the NPC slice of the occupants (kind === "npc"), name
  rows only (the API gives exactly {id,name,kind} — nothing invented);
  «Поговорить» per NPC is offered for the CURRENT place only (the existing
  placeDialogueNpcs gate: viewChat reads NPCs at the player's CURRENT
  location, so a remote place must not promise dialogue it cannot show);
  it sets gameUiContext.npcId and navigates #/chat (revalidated in
  viewChat);
- #303: the current-task line reads the SAME tasks source as the World
  HUD's «Сейчас» (fetched in viewLocation, own catch, is_owner gate):
  active[0] else planned[0] → status·type labels, «нет активной задачи»,
  or an explicit «Данные о делах недоступны»;
- #303: «О месте» is a native <details class="place-about"> (collapsed
  reference level): ID/Тип/Координаты/Входит в/Людей сейчас + the full
  roster (no affordances) + the events listing. ID/coordinates must NOT
  appear in the primary (always-visible) view;
- events = GET /world/events?since=0&limit=100 client-filtered by
  location_id, labeled «События в загруженной выборке» — never claimed to
  be complete history; the request has its own failure/retry state,
  independent of the main render;
- world context is labeled «Время мира» / «Погода в мире» (world scope,
  never local effects); unknown time stays unavailable (no zero-fallback);
  weather disabled/failed stays «недоступна»;
- #303: the scene-act call is allowed from the Place view ONLY inside the
  current-place branch (isCurrent) — the old «no /scene/act inside the
  Place slices» pin inverts to a branch-confinement pin (a strengthening:
  the single /scene/act call site stays submitSceneAct, and the remote
  branch must carry no scene-act machinery at all);
- states separated: loading / «Место не найдено» (unknown id) / empty
  roster / events failure / main failure with retry; stale responses are
  dropped (request generation + current hash + S.user/S.character + host
  isConnected — the #66 precedent). Review fixes: a leading-zero deep link
  (#/location/05) is normalized to the canonical hash BEFORE loading via
  history.replaceState (fires no hashchange — no re-dispatch), otherwise
  the hash guards would strand the screen on «Загрузка…»; the events
  request family owns a SEPARATE sequence (placeEventsSeq) so an events
  retry never invalidates an in-flight main load; all four guards carry
  the session checks. Malformed ids (#/location/abc) fall through to
  viewWorld — ACCEPTED as intended: a non-numeric id is not a place route
  and produces no dead state. Validator: /world and /weather are fetched
  into LOCALS and become shared state (S.world/S.weather) ONLY after the
  stale guard passes — a stale response must not mutate shared
  world/weather for a dropped screen (other screens read S.world/S.weather,
  so the shared write is kept but moved after the guard);
- entry points: «О месте» link beside the current-location summary on
  #/world (the #303 details summary reuses the same label);
- CSS: the #72 .place-* rules (now shared with the #74 Home hero) stay
  scoped; the #303 game-shell rules (.place-game, .place-topbar,
  .place-grid, .place-stage, .place-context, .place-badge, .place-cta,
  .place-nearby-row, .place-task, .place-scene-act, .place-about, …) are
  inserted AFTER the #72 block and BEFORE the #175 conn-banner tail (m175
  keeps the banner the last rule); NO new --w-line definition (the pinned
  token count stays 4).

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
    # #303: 1→2 — the «О месте» <details> summary reuses the same label;
    # the entry link itself stays pinned by the two literals below.
    assert js.count('"О месте"') == 2
    assert js.count('class: "place-link"') == 1
    # href pattern: the CURRENT location's id, template-built.
    assert js.count('href: `#/location/${currentLoc.id}` }, "О месте")') == 1


# ---------- c: honest literals ----------

def test_place_honest_literal_pins():
    js = _read(APP_JS)
    # The stage's photo honesty note, exactly once.
    # #303: re-anchored — the note moved from the hero band to the static
    # .place-stage placeholder (same wording, same honesty).
    # #312: the static photo placeholder is superseded by the text-first
    # stage composition (the old «Фото места недоступно» note is gone).
    assert js.count('"Фото места недоступно"') == 0
    # #312: the stage's «Показать сцену» affordance is current-place-only
    # (present in the isCurrent branch, absent from the remote branch).
    rbody = _render_place_body(js)
    remote = rbody[rbody.index("if (!isCurrent) {"):
                 rbody.index("if (isCurrent) {")]
    current = rbody[rbody.index("if (isCurrent) {"):]
    assert current.count('"Показать сцену"') == 1
    assert remote.count('"Показать сцену"') == 0
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


# ---------- d: scene-act confined to the current-place branch ----------

def test_place_view_no_wrong_location_calls():
    js = _read(APP_JS)
    vbody = _view_location_body(js)
    rbody = _render_place_body(js)
    # The loader never touches scene/visual/travel (unchanged).
    assert vbody.count('api("/scene/act"') == 0
    assert vbody.count('api("/visual/scenes"') == 0
    assert vbody.count("/travel/plan") == 0
    # #303: the old «no /scene/act inside the Place slices» pin INVERTS —
    # the free-text action is now part of the Place contract, but ONLY in
    # the current-place branch. This is a strengthening, not a weakening:
    # the single /scene/act call site stays submitSceneAct (outside the
    # slices), and the remote branch must carry NO scene-act machinery at
    # all (a remote place must not offer an action that targets the
    # player's CURRENT location — the original wrong-location hazard).
    assert rbody.count('api("/scene/act"') == 0
    assert rbody.count('api("/visual/scenes"') == 0
    assert rbody.count("/travel/plan") == 0
    # The two state branches exist exactly once each…
    assert rbody.count("if (!isCurrent) {") == 1
    assert rbody.count("if (isCurrent) {") == 1
    # …and the remote branch (everything between the two) is scene-act-free…
    remote = rbody[rbody.index("if (!isCurrent) {"):
                   rbody.index("if (isCurrent) {")]
    assert remote.count("sceneActBar(") == 0
    assert remote.count("place-scene-act") == 0
    assert remote.count("submitPlaceSceneAct") == 0
    assert remote.count('api("/scene/act"') == 0
    # …while the current branch carries the bar wired to the shared
    # submission path, exactly once.
    current = rbody[rbody.index("if (isCurrent) {"):]
    assert current.count("sceneActBar(submitPlaceSceneAct)") == 1
    assert current.count("place-scene-act") == 2  # bar + result host
    # The file-wide call counts are unchanged (m153/m154/m151 pins).
    assert js.count('api("/scene/act"') == 1
    assert js.count('api("/visual/scenes", { method: "POST",') == 1
    assert js.count("/travel/plan") == 1


# ---------- e: «Поговорить» gated on NPCs + current location ----------

def test_place_dialog_gate_pins():
    js = _read(APP_JS)
    rbody = _render_place_body(js)
    # #303: the per-NPC dialogue affordance is the «Поговорить» button in
    # the «Рядом» row (the old «Открыть диалог» anchor is removed).
    assert rbody.count('"Поговорить"') == 1
    # The NPC half of the gate: the nearby slice filters kind === "npc",
    # exactly once in the render body (mapNpcMarkers' own kind filter at
    # the world map is separate).
    assert rbody.count('kind === "npc"') == 1
    assert js.count('occupants.filter((o) => o.kind === "npc")') == 1
    # #303: the location half of the gate moved to the module-level
    # placeDialogueNpcs helper (reordered comparison, outside the body —
    # the same validator: chat reads NPCs at the player's CURRENT
    # location, so a remote place never shows the affordance).
    assert js.count("if (locId !== S.character.location_id) return [];") == 1
    assert js.count('occupants.filter((o) => "npc" === o.kind)') == 1
    # The body's own current-location comparison (isCurrent), exactly once.
    assert rbody.count("locId === S.character.location_id") == 1
    # #303: the affordance is a button (not an anchor) — it carries the
    # confirmed occupant id into the session UI context and navigates
    # #/chat (revalidated in viewChat). The place body is the only
    # place-screen site of both.
    assert rbody.count("gameUiContext.npcId = o.id;") == 1
    assert rbody.count('location.hash = "#/chat"') == 1


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
    # #303: the current-task line reads the SAME tasks source as the World
    # HUD's «Сейчас» — one bounded read per place load, OWN catch (a tasks
    # failure is an honest «Данные о делах недоступны» line, never a dead
    # screen), gated on is_owner (undefined counts as owner).
    assert vbody.count("let tasksData = null;") == 1
    assert vbody.count("if (S.character.is_owner !== false) {") == 1
    assert vbody.count(
        "tasksData = await api(`/characters/${ctxCharId}/tasks`)") == 1
    assert vbody.count("catch (e) { tasksData = null; }") == 1
    # Events: bounded sample fetch, client-filtered by location_id.
    # #76: 1→2 — the Events screen fetches the same bounded sample for
    # itself (one request PER SCREEN; the place screen's filter is its own).
    assert js.count('api("/world/events?since=0&limit=100")') == 2
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
    # use the same shape against careerLoadSeq/viewHash. #76: +2 — the
    # Events screen's guards (fetch-error + apply) against eventsLoadSeq.
    # #77: +2 — the external-trip screen's guards (fetch-error + apply)
    # against externalLoadSeq (the redesigned #/external keeps the house
    # stale-guard shape).
    # #79: +1 — the settings screen's sync stale-guard (viewSettings) joins
    # the same house shape → 13 file-wide.
    assert js.count("location.hash !== viewHash") == 13
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


# ---------- g: place view chrome + state-aware actions ----------

def test_place_view_chrome_pins():
    js = _read(APP_JS)
    vbody = _view_location_body(js)
    rbody = _render_place_body(js)
    # The Place screen highlights «Мир»: topbar("#/world") on the loading
    # and missing states (2) and on the final render (1).
    assert vbody.count('topbar("#/world")') == 2
    assert rbody.count('topbar("#/world")') == 1
    # #303: the back affordance moved from the «К карте» button row to the
    # .place-topbar «← Остров» button — the SAME manual-focus mechanism
    # (mapLink() itself untouched; m162's call-site count moved 4→5 with a
    # #72 comment) and the same single-dispatch gotoWorld() navigation.
    assert rbody.count('"← Остров"') == 1
    assert rbody.count("gotoWorld();") == 1
    assert rbody.count("S.mapFocusManual = true;") == 1
    assert rbody.count("S.mapFocusLocationId = locId;") == 1
    # #303: the «Обновить» button is removed — the refresh affordance is
    # the transient-failure retry (loadRetryNode re-runs the loader),
    # exactly once in the loader.
    assert rbody.count('"Обновить"') == 0
    assert vbody.count("loadRetryNode(loading, () => viewLocation(locId))") == 1
    # #303: the location selector is removed — place navigation stays
    # hash-driven from the map / «О месте» link (single dispatch, m161),
    # and the render body never calls the loader directly (no direct view
    # call next to a hash assignment).
    assert js.count("location.hash = `#/location/${id}`") == 0
    assert rbody.count("viewLocation(") == 0
    # XSS hygiene holds file-wide (m146 mirror).
    assert js.count("innerHTML") == 0
    # No type-derived action labels anywhere (inventory verbs stay gone).
    for label in ("Съесть", "Выпить", "Использовать", "Выбросить"):
        assert js.count(label) == 0, label


# ---------- h: CSS placement + uniqueness ----------

def test_place_css_pins():
    css = _read(APP_CSS)
    # The #72 .place-* rules (the hero trio is now shared with the #74
    # Home screen) stay unique…
    rules = (
        ".place-shell {", ".place-hero {", ".place-hero-icon {",
        ".place-hero-text {", ".place-hero-note {", ".place-actions {",
        ".place-roster {", ".place-roster-row {", ".place-events-note {",
        ".place-event-list {", ".place-event-row {", ".place-detail-row {",
        ".place-detail-label {", ".location-view .place-link {",
    )
    for rule in rules:
        assert css.count(rule) == 1, rule
    # #303: the old-UI rules are REMOVED with the hero-band/selector
    # composition (the .place-chat-link and .place-selector selectors are
    # gone from the stylesheet).
    assert css.count(".place-chat-link {") == 0
    assert css.count(".place-selector {") == 0
    # #303: the game-shell rules are unique (the two .place-grid /
    # .place-stage duplicates are the mobile media-query overrides at the
    # block tail — pinned by count, not by absence).
    game_rules = (
        ".place-game {", ".place-topbar {", ".place-topbar-title {",
        # #312: .place-stage-icon is removed with the static photo placeholder.
        ".place-stage-note {", ".place-context {",
        ".place-context-name {", ".place-badge {", ".place-badge-here {",
        ".place-cta {", ".place-nearby-row {", ".place-act-hint {",
        ".place-task {", ".place-task-label {", ".place-scene-act {",
        ".place-scene-act-result {", ".place-about {", ".place-about-body {",
    )
    for rule in game_rules:
        assert css.count(rule) == 1, rule
    assert css.count(".place-grid {") == 2   # base + mobile override
    assert css.count(".place-stage {") == 2  # base + mobile override
    # Placement: AFTER the byte-pinned first media blocks (m146) and the
    # appearance block, BEFORE the #175 conn-banner tail (m175 keeps the
    # banner the last rule in the file). The #303 block sits after the
    # #72 block and before the banner too.
    idx_first_small = css.index("@media (max-width: 520px) {")
    idx_appear = css.index(".appearance-shell {")
    idx_place = css.index(".place-shell {")
    idx_game = css.index(".place-game {")
    idx_banner = css.index(".conn-banner {")
    assert idx_first_small < idx_appear < idx_place < idx_game < idx_banner
    # Append-only tail convention: the banner block is still the last rule.
    assert css.rstrip().endswith(".conn-banner.hidden { display: none; }")
    # No new --w-line definition (m170/m173/m175/m187/m237 pin the total at 4).
    assert css.count("--w-line:") == 4
