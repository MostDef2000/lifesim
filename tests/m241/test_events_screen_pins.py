"""#76 — Events / Invitations / Notifications screen: subordinate static
route #/events.

Approved architecture (binding): ONE new static registry entry, ONE world
entry link, NO backend changes, NO new APIs, NO new topbar tab, NO fake
data. Decisions recorded for the stranger reader:

- Subordinate static route, NO tab: "#/events" sits in the `routes`
  registry (dispatched through (routes[hash] || viewWorld)()) but the
  topbar tabs array is untouched — 9 tabs stay 9. The screen is
  world-scoped and highlights «Мир» (its parent surface); it is reached
  from the world screen's feed link («События мира →»).
- Data honesty — the bounded sample: GET /world/events?since=0&limit=100
  returns an id-ASCENDING array — the FIRST 100 historical events, NOT
  the latest. The screen carries the PERSISTENT disclosure (exact copy
  pinned below), always visible including in the empty state. The fetch
  is a fresh ONE-SHOT per visit/retry: no poller start, no WS calls, no
  shared-feed reuse, no cursor writes — the world screen keeps its own
  live feed; this screen is a bounded historical read.
- Raw facts only: event_type is rendered as the raw string; actor_id is
  the raw character-id string (or «вы» for the player's own actions) and
  the actor line is omitted entirely when the event has no actor — never
  a fabricated person. Place: top-level numeric location_id first, else
  a DIRECT numeric payload.location_id (POWER_OUTAGE carries one) —
  strict numeric validation (typeof + Number.isSafeInteger + > 0);
  CHARACTER_MOVED's payload.from/to_location fields are NOT parsed (no
  from/to guessing, no string coercion) — an unusable place is no place
  affordance at all.
- Place affordance REUSES the existing mapLink() («Показать на карте» +
  gotoWorld inside — the #92 focus mechanism); the events body never
  calls gotoWorld directly and never redefines the helper. The place
  link navigates through the hash (single dispatch, #72 vocabulary).
- World entry link OUTSIDE #feed: both updaters (REST fallback + WS)
  replaceChildren the whole #feed contents on every update — a link
  inside the feed node would vanish on first arrival. The feed node
  itself stays byte-identical; a wrapper div carries the link.
- Honest NEEDS-BACKEND rows (exact copy pinned below): NO invitations/
  RSVP/notifications/importance/lifecycle backend exists, and dialogue
  emits no WorldEvent; messages are a letter inbox (different feature).
  The mockup's filter tabs, RSVP buttons, images/avatars and «Новое»
  badges are REJECTED as fake capability — nothing is invented.
- States separated: loading / DISTINCT failure (fetch failure or a
  MALFORMED payload — non-array — with retry, NEVER rendered as
  «Событий пока нет») / empty («Событий пока нет» under the disclosure) /
  rows render. Stale responses are dropped (request generation + current
  hash + S.user/S.character + host isConnected on BOTH async paths — the
  m238/#74/#75 pattern); renderEvents is separate from viewEvents.
- CSS: none — the screen reuses .place-shell/.panel/.muted/
  .place-actions; rows are page-scrolling panel rows (the .feed class's
  260px scroll container is NOT used here; the id "feed" stays unique to
  the world screen); zero style.css changes.

Byte-pins over backend/app/web/app.js, in the m157/m170/m176/m237/m238/
m239/m240 house style (exact literal counts — update deliberately, never
weaken).
"""

import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
APP_JS = os.path.join(ROOT, "backend/app/web/app.js")


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _events_body(js):
    """The #76 Events screen body — viewEvents loader + renderEvents.

    The two functions are adjacent and sit AFTER the #72 place section's
    placeTypeIcon and BEFORE the #75 Career screen's viewCareer (outside
    the m240 career slice viewCareer→viewHome, the m239 home slice
    viewHome→viewLocation and both m238 place slices).
    """
    start = js.index("async function viewEvents(")
    end = js.index("async function viewCareer(", start)
    return js[start:end]


def _topbar_body(js):
    """The topbar() body — the tabs array must never grow an Events tab."""
    start = js.index("function topbar(active) {")
    return js[start:js.index("\n}", start)]


# ---------- a: subordinate static route ----------

def test_events_route_registry_pins():
    js = _read(APP_JS)
    # The static registry entry exists exactly once — pinned WITH the
    # trailing comma so a renamed/broken handler (viewEventsX) fails the
    # count instead of matching as a substring prefix.
    assert js.count('"#/events": viewEvents,') == 1
    # …inside the routes object (dispatched through the static dispatch).
    routes_def = js.index("const routes = {")
    routes_end = js.index("};", routes_def)
    assert routes_def < js.index('"#/events": viewEvents,') < routes_end
    # The registry comment records the honest row count (15→16 with #76)
    # and keeps the #74/#75 notes intact.
    assert js.count("16 rows total") == 1
    assert js.count("15 rows total") == 0
    assert js.count("14 rows total") == 0


# ---------- b: no topbar tab ----------

def test_events_no_topbar_tab_pins():
    js = _read(APP_JS)
    # The tabs array never mentions the events route…
    assert js.count('["#/events"') == 0
    # …and neither the route nor the «События мира» heading leaks into
    # the topbar body.
    assert _topbar_body(js).count("events") == 0
    assert _topbar_body(js).count("События мира") == 0


# ---------- c: one bounded fetch, no poller ----------

def test_events_fetch_pins():
    js = _read(APP_JS)
    body = _events_body(js)
    # The same bounded-sample literal the place screen uses — file-wide
    # 1→2 (one request PER SCREEN; the events screen owns its fetch).
    assert js.count('api("/world/events?since=0&limit=100")') == 2
    assert body.count('api("/world/events?since=0&limit=100")') == 1
    # A malformed payload (non-array) is a load failure, not an empty
    # sample — the shape check exists exactly once, before the apply guard.
    assert body.count("!Array.isArray(data)") == 1
    assert body.index("!Array.isArray(data)") < body.index(
        "if (seq !== eventsLoadSeq || location.hash !== viewHash")
    # NO poller/WS/shared-feed integration: the events body never starts
    # the feed poller, never touches the shared feed state, never writes
    # a cursor.
    assert body.count("startFeed") == 0
    assert body.count("S.feed") == 0
    assert body.count("pollTimer") == 0
    assert body.count("sendWsCursor") == 0


# ---------- d: persistent disclosure ----------

def test_events_disclosure_pin():
    js = _read(APP_JS)
    body = _events_body(js)
    # The bounded-sample disclosure, exact copy, exactly once file-wide —
    # always visible (it renders above rows AND the empty state).
    assert js.count(
        '"События мира: до 100 записей с начала истории. '
        'Это не полная и не обновляемая автоматически лента."') == 1
    assert body.count("disclosure") >= 1
    assert body.index("disclosure") < body.index('"Событий пока нет"')


# ---------- e: honest NEEDS-BACKEND rows ----------

def test_events_needs_backend_pins():
    js = _read(APP_JS)
    body = _events_body(js)
    # Exact copy, each exactly once in the events body: no invitations/
    # RSVP/importance/lifecycle/notifications backend exists.
    assert body.count('"Приглашения недоступны"') == 1
    assert body.count('"Принятие, отклонение и перенос приглашений недоступны"') == 1
    assert body.count('"Оценка важности и категории событий недоступны"') == 1
    assert body.count(
        "'Состояния событий «предстоящее», «активное», «истёкшее» недоступны'") == 1
    assert body.count('"Персональные уведомления недоступны"') == 1
    assert body.count(
        "'Статусы прочтения и отметки «Новое» недоступны'") == 1
    # The mockup's fake capability stays out: no filter tabs, no RSVP
    # buttons, no images/avatars, no «Новое» badges (the straight-quoted
    # badge literal form — the honest row's guillemet form is separate).
    for label in ("Приглашение от", "Я иду", "Maybe Later", '"Новое"',
                  "Фильтр", "filter"):
        assert body.count(label) == 0, label
    # No desire bridge, no task duplication on this screen.
    assert body.count('href: "#/desire"') == 0
    assert body.count("Журнал") == 0


# ---------- f: mapLink reuse ----------

def test_events_maplink_reuse_pins():
    js = _read(APP_JS)
    body = _events_body(js)
    # The map-focus helper is defined exactly once (m157/m170 boundary)…
    assert js.count("function mapLink(") == 1
    # …and the events body REUSES it (never redefines, never calls
    # gotoWorld directly — the focus mechanism lives inside mapLink).
    assert body.count("mapLink(locId)") == 1
    assert body.count("gotoWorld(") == 0
    # The place link navigates through the hash (#72 vocabulary).
    assert body.count('href: `#/location/${locId}` }, "Место →")') == 1


# ---------- g: stale guards ----------

def test_events_stale_guard_pins():
    js = _read(APP_JS)
    body = _events_body(js)
    # Stale-response guards (m238/#74/#75 pattern): one module-level
    # sequence, checked on BOTH of its paths (error + apply)…
    assert js.count("let eventsLoadSeq = 0;") == 1
    assert js.count("const seq = ++eventsLoadSeq;") == 1
    assert body.count("(seq !== eventsLoadSeq") == 2
    # …each guard carrying the current hash, the session/character
    # presence and the host connectivity (all FOUR guards).
    assert body.count("location.hash !== viewHash") == 2
    assert body.count("!S.user || !S.character") == 2
    assert body.count("!loading.isConnected") == 2
    # renderEvents is separate from viewEvents (m238/#74/#75 layering).
    assert js.count("async function viewEvents(") == 1
    assert js.count("function renderEvents(") == 1
    assert js.index("async function viewEvents(") < js.index(
        "function renderEvents(") < js.index("async function viewCareer(")


# ---------- h: file-wide guard total ----------

def test_events_hash_guard_total_pin():
    js = _read(APP_JS)
    # 8 pre-#76 (place ×2 + home ×2 + career ×2 + … the m238 total) + 2
    # events guards (fetch-error + apply) = 10 (m238 records the same).
    # #77: +2 — the external-trip screen's guards (fetch-error + apply)
    # against externalLoadSeq join the same house shape → 12 file-wide.
    assert js.count("location.hash !== viewHash") == 12


# ---------- i: placement ----------

def test_events_placement_pins():
    js = _read(APP_JS)
    # The events body sits BEFORE the #75 Career screen's viewCareer…
    assert js.index("async function viewEvents(") < js.index(
        "async function viewCareer(")
    # …AFTER the #72 place section's placeTypeIcon (not inside either
    # m238 slice)…
    assert js.index("function placeTypeIcon(") < js.index(
        "async function viewEvents(")
    # …and does not leak into the m240 career slice (viewCareer→viewHome)
    # or the m239 home slice (viewHome→viewLocation).
    career_start = js.index("async function viewCareer(")
    career_body = js[career_start:js.index("async function viewHome(",
                                           career_start)]
    assert career_body.count("viewEvents") == 0
    assert career_body.count("eventsLoadSeq") == 0
    home_start = js.index("async function viewHome(")
    home_body = js[home_start:js.index("async function viewLocation(",
                                       home_start)]
    assert home_body.count("viewEvents") == 0
    assert home_body.count("eventsLoadSeq") == 0


# ---------- j: world entry link outside #feed ----------

def test_events_world_link_pins():
    js = _read(APP_JS)
    # The standing link exists exactly once…
    assert js.count('"События мира →"') == 1
    assert js.count('el("a", { href: "#/events" }, "События мира →")') == 1
    # …and the feed node itself stays byte-identical (same class/id/h2).
    assert js.count(
        'const feedNode = el("div", { class: "panel feed world-events-card",'
        ' id: "feed" },') == 1
    assert js.count('id: "feed"') == 1
    # Structurally OUTSIDE #feed: the link sits in the wrapper AFTER the
    # feedNode declaration (both updaters replaceChildren #feed — a link
    # inside would vanish on first update).
    wrap_start = js.index('const feedBlock = el("div", {}, feedNode,')
    link_idx = js.index('el("a", { href: "#/events" }, "События мира →")')
    assert wrap_start < link_idx
    assert js.index("const feedNode =") < wrap_start
    # The updaters still target #feed by id (untouched).
    assert js.count('document.getElementById("feed")') == 2


# ---------- l: empty state + chrome + hygiene ----------

def test_events_empty_and_chrome_pins():
    js = _read(APP_JS)
    body = _events_body(js)
    # Empty roster: an honest state under the disclosure, events-body-
    # scoped (the world screen's feed has its own empty copy).
    assert body.count('"Событий пока нет"') == 1
    # The h2 heading is the ONLY quoted «События мира» literal (distinct
    # from the world screen's «События»).
    assert js.count('"События мира"') == 1
    assert body.count('el("h2", {}, "События мира")') == 1
    # The screen highlights «Мир» (world-scoped): loader + the final render.
    assert body.count('topbar("#/world")') == 2
    # Loading + retry pair: ONE loading node, ONE retry site.
    assert body.count(
        'const loading = el("div", { class: "muted" }, "Загрузка…");') == 1
    assert body.count("loadRetryNode(loading, () => viewEvents());") == 1
    # XSS hygiene holds file-wide (m146 mirror).
    assert js.count("innerHTML") == 0
    # Raw-fact rendering: the timestamp ternary and the actor ternary
    # exist exactly once each (no fabricated people, no zero-fallbacks).
    assert body.count('typeof ev.game_timestamp === "number"') == 1
    assert body.count('ev.actor_id === S.character.id ? "вы" : ev.actor_id') == 1
    # Strict place validation: top-level numeric check + direct numeric
    # payload.location_id check (no from/to parsing, no string coercion).
    assert body.count("Number.isSafeInteger(ev.location_id)") == 1
    assert body.count("Number.isSafeInteger(p.location_id)") == 1
    assert body.count("from_location") == 0
    assert body.count("to_location") == 0
    # The events body invents NO new api() endpoint: only the existing
    # bounded /world/events read.
    for endpoint in ('api("/scene/act"', 'api("/visual/scenes"',
                     "/travel/plan", 'api("/market', 'api("/admin',
                     'api("/characters'):
        assert body.count(endpoint) == 0, endpoint
    # Page-scrolling panel rows: the .feed scroll-container class and the
    # world feed's id stay out of this screen.
    assert body.count('class: "panel feed') == 0
    assert body.count('id: "feed"') == 0
    # No map-marker code: the events screen is a panel screen, not a map
    # surface (m149/m157 boundary).
    assert body.count("anchor--") == 0
    assert body.count("buildLivingMap") == 0
    assert body.count("mapMarker") == 0
