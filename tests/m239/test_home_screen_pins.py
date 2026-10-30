"""#74 — Home / Property screen: subordinate static route #/home.

Approved architecture (binding): ONE new static registry entry, NO backend
changes, NO new APIs, NO new topbar tab, NO fake data. Decisions recorded
for the stranger reader:

- Subordinate static route, NO tab: "#/home" sits in the `routes` registry
  (dispatched through (routes[hash] || viewWorld)()) but the topbar tabs
  array is untouched — the mockup's 5-tab home shell / needs meters / room
  lists are REJECTED as fake capability (the sim has no housing read
  model). The screen highlights «Профиль» (its parent surface) and is
  reached from the profile's housing link and the desire bridge.
- Data: the owner payload GET /characters/{cid} is fetched FRESH — missing
  hydration is NOT a null home; when home is set, ONE /locations fetch
  resolves it from the world-scoped catalog (name/type — the same source
  the place screen reads; no /locations/{id} detail call). The home id is
  read from the FRESH character exactly once and staged locally — a stale
  S.character must never decide the no-home state.
- SLEEP rides the EXISTING doAction (POST /actions) with a no-op stay:
  task acceptance is not a completed sleep, so the screen neither yanks
  the player to the world nor fakes an energy refresh. The validator
  (backend/app/actions/validators.py) rejects SLEEP at energy>=100
  ("Energy already full") and auto-redirects home when away — doAction's
  catch maps the 422 to the honest note (moveTo's needs_move precedent),
  and the away note says up front that the character travels first.
- Honest states: energy missing stays «Энергия недоступна» (never zero);
  conditions/amenities/rooms/upgrades/housing-offers are NEEDS-BACKEND
  rows (exact copy pinned below) — the mockup's comfort meters, room
  lists and upgrade shops are fake capability and are not rendered;
  «Фото жилья недоступно» — no place-image endpoint exists.
- States separated: loading / no home («Жильё не назначено» + the #73
  bridge vocabulary «Сформулировать цель →») / DISTINCT location-read
  failure (home id set but the catalog read fails or lacks the id —
  loadRetryNode, NEVER rendered as a no-home state) / has-home render.
  Stale responses are dropped (request generation + current hash +
  S.user/S.character + host isConnected on BOTH async paths — the m238
  pattern); renderHome is separate from viewHome.
- m149 rationale revision: the lane-B rationale «the client never learns
  home_location_id» is stale — the owner payload exposes it (app.py) and
  #74 consumes it OUTSIDE the map. The MAP prohibition stands unchanged
  (houses stay ambient spans; no own-house anchor) — m149 now pins the
  map boundary directly instead of via payload ignorance.
- CSS: none — the screen reuses .place-shell/.place-hero/.place-actions
  (the #72 place vocabulary); no new --w-line token (the pinned count
  stays 4).

Byte-pins over backend/app/web/app.js, in the m157/m170/m176/m237/m238
house style (exact literal counts — update deliberately, never weaken).
"""

import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
APP_JS = os.path.join(ROOT, "backend/app/web/app.js")


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _home_body(js):
    """The #74 Home screen body — viewHome loader + renderHome composition.

    The two functions are adjacent and sit BEFORE the place view's
    viewLocation (outside both m238 place-body slices — the place body is
    not disturbed).
    """
    start = js.index("async function viewHome(")
    end = js.index("async function viewLocation(", start)
    return js[start:end]


def _topbar_body(js):
    """The topbar() body — the tabs array must never grow a Home tab."""
    start = js.index("function topbar(active) {")
    return js[start:js.index("\n}", start)]


# ---------- a: subordinate static route, no tab ----------

def test_home_route_registry_pins():
    js = _read(APP_JS)
    # The static registry entry exists exactly once…
    assert js.count('"#/home": viewHome') == 1
    # …inside the routes object (dispatched through the static dispatch).
    routes_def = js.index("const routes = {")
    routes_end = js.index("};", routes_def)
    assert routes_def < js.index('"#/home": viewHome') < routes_end
    # NO topbar tab: the tabs array never mentions the home route…
    assert js.count('["#/home"') == 0
    # …and the «Дом» heading never leaks into the topbar body.
    assert _topbar_body(js).count("Дом") == 0


# ---------- c: honest literals ----------

def test_home_honest_literal_pins():
    js = _read(APP_JS)
    body = _home_body(js)
    # Missing-home state (fresh-fetch null) — exactly once, in viewHome.
    assert body.count('"Жильё не назначено"') == 1
    # The hero's photo honesty note (no place-image endpoint).
    assert body.count('"Фото жилья недоступно"') == 1
    # NEEDS-BACKEND rows, exact copy, each exactly once: the sim has no
    # housing read model — nothing comfort/room/upgrade-shaped is faked.
    assert body.count('"Условия проживания недоступны"') == 1
    assert body.count(
        '"Данные об удобстве, оснащении и хранении недоступны"') == 1
    assert body.count('"Данные о комнатах недоступны"') == 1
    assert body.count('"Улучшения жилья пока недоступны"') == 1
    assert body.count('"Предложения нового жилья недоступны"') == 1
    # The h2 heading is the ONLY «Дом» literal in the file (m149/m157
    # mirror: the map layer stays anchor-free).
    assert js.count("Дом") == 1
    # Energy honesty: missing stays unavailable (never zero)…
    assert body.count('"Энергия недоступна"') == 1
    # …and the full-energy note renders in the panel exactly once (the
    # doAction toast mapping is the second, file-wide occurrence).
    assert body.count('"Энергия уже полная — сон сейчас не нужен"') == 1
    assert js.count('"Энергия уже полная — сон сейчас не нужен"') == 2
    # Away-from-home honesty: the validator auto-redirects — say so.
    assert body.count('"Персонаж сначала отправится домой"') == 1


# ---------- d: SLEEP rides the EXISTING doAction ----------

def test_home_sleep_uses_existing_doaction():
    js = _read(APP_JS)
    body = _home_body(js)
    # The home body posts SLEEP through the shared doAction helper, once.
    assert body.count('doAction("SLEEP"') == 1
    # NO new /actions call site: the file-wide count is unchanged — SLEEP
    # reuses the existing POST /actions path (doAction + moveTo + the
    # external-trip call).
    assert js.count('api("/actions"') == 3
    # The no-op stay is explicit: task acceptance is not a completed
    # sleep — no navigation, no fake refresh.
    assert body.count('doAction("SLEEP", {}, () => {})') == 1


# ---------- e/f: energy + away states ----------

def test_home_energy_and_away_pins():
    js = _read(APP_JS)
    body = _home_body(js)
    # The energy read is the fresh payload's real value; missing stays
    # unavailable — the honest literal exists exactly once.
    assert body.count('"Энергия недоступна"') == 1
    # The full-energy disable: the button carries a conditional disabled
    # attr and the note explains why (validator: energy>=100 is rejected).
    assert body.count("energyFull ? { disabled: \"\" } : {}") == 1
    assert body.count("energyVal >= 100") == 1
    # The away note is gated on the fresh location comparison (the
    # validator redirects home — the screen says so up front).
    assert body.count("fresh.location_id !== homeId") == 1


# ---------- g: desire bridge links ----------

def test_home_desire_bridge_pins():
    js = _read(APP_JS)
    body = _home_body(js)
    # Standing link (the #73 shop vocabulary, same literal form)…
    assert body.count('el("a", { href: "#/desire" }, "Цель →")') == 1
    # …and the no-home bridge (#73 insufficient-funds pattern: no home →
    # formulate a goal), once.
    assert body.count(
        'el("a", { href: "#/desire" }, "Сформулировать цель →")') == 1
    # File-wide #/desire hash links: 6 pre-existing + 2 from #74 = 8.
    assert js.count('href: "#/desire"') == 8


# ---------- h: data contract + stale guards ----------

def test_home_data_and_stale_guard_pins():
    js = _read(APP_JS)
    body = _home_body(js)
    # Fresh owner payload first; ONE /locations fetch only when a home is
    # set; no /locations/{id} detail call from the home view.
    assert body.count('api(`/characters/${S.character.id}`)') == 1
    assert body.count('api("/locations")') == 1
    assert body.count('api("/locations/${') == 0
    # The home id is read from the FRESH payload exactly once, file-wide
    # (m149/m157 mirror: the map layer never reads it).
    assert js.count("home_location_id") == 1
    # Stale-response guards (m238 pattern): one module-level sequence,
    # checked on BOTH of its paths (error + apply)…
    assert js.count("let homeLoadSeq = 0;") == 1
    assert js.count("const seq = ++homeLoadSeq;") == 1
    assert body.count("(seq !== homeLoadSeq") == 2
    # …each guard carrying the current hash, the session/character
    # presence and the host connectivity (all FOUR guards).
    assert body.count("location.hash !== viewHash") == 2
    assert body.count("!S.user || !S.character") == 2
    assert body.count("!loading.isConnected") == 2
    # renderHome is separate from viewHome (m238 layering).
    assert js.count("async function viewHome(") == 1
    assert js.count("function renderHome(") == 1
    assert js.index("async function viewHome(") < js.index(
        "function renderHome(") < js.index("async function viewLocation(")


# ---------- i: profile entry link ----------

def test_home_profile_link_pins():
    js = _read(APP_JS)
    # The standing link beside the profile's location row, exactly once.
    assert js.count("Моё жильё") == 1
    assert js.count('href: "#/home"') == 1
    assert js.count('el("a", { href: "#/home", class: "muted" },'
                    ' "Моё жильё →")') == 1


# ---------- h2: chrome + hygiene ----------

def test_home_chrome_and_hygiene_pins():
    js = _read(APP_JS)
    body = _home_body(js)
    # The screen highlights «Профиль» (no Home tab exists): loader + the
    # no-home/failure states + the final render.
    assert body.count('topbar("#/profile")') == 4
    # Loading + retry pair: the standard loading node and the two retry
    # sites (fetch failure, catalog-missing failure).
    assert body.count(
        'const loading = el("div", { class: "muted" }, "Загрузка…");') == 1
    assert body.count("loadRetryNode(loading, () => viewHome());") == 2
    # «Открыть место» navigates through the hash (single dispatch).
    assert body.count('href: `#/location/${homeId}` }, "Открыть место")') == 1
    # XSS hygiene holds file-wide (m146 mirror).
    assert js.count("innerHTML") == 0
    # No type-derived action labels anywhere (inventory verbs stay gone).
    for label in ("Съесть", "Выпить", "Использовать", "Выбросить"):
        assert js.count(label) == 0, label
    # The home body invents NO new api() endpoint: only the existing
    # /characters + /locations reads (SLEEP goes through doAction).
    for endpoint in ('api("/scene/act"', 'api("/visual/scenes"',
                     "/travel/plan", 'api("/market', 'api("/admin'):
        assert body.count(endpoint) == 0, endpoint
    # The map layer stays house-span-only (m149 boundary, pinned directly):
    # no home marker/anchor code anywhere.
    assert js.count('class: "anchor anchor--house"') == 1
    assert js.count("homeMarker") == 0
