"""#77 — Vladivostok / external-trip destination screen: in-place redesign
of the EXISTING #/external route.

Approved architecture (binding): NO new route, NO new tab, NO backend
changes, NO new APIs, NO fake data. Decisions recorded for the stranger
reader:

- In-place expansion: "#/external" already exists in the `routes` registry
  (m175 pins the entry, the function and the single hash assignment) — the
  redesign keeps all three byte-stable and the registry at 16 rows. The
  world screen's «Поездка во Владивосток» button stays the entry point.
- Entry normalization kills the double-fetch: the old code assigned the
  hash AND fetched, so a world-button click fired hashchange → route() →
  viewExternal again — two catalog fetches per entry. Now: hash not
  current → assign + return (the re-dispatch loads); hash current (direct
  entry / reload) → load immediately. Exactly ONE `location.hash =
  "#/external";` assignment survives (m175:63).
- House stale guards (m238/#74/#75/#76 pattern): `let externalLoadSeq = 0;`
  + seq/hash/session/isConnected on BOTH catch and apply paths; ONE
  loading node + ONE loadRetryNode pair (m175 counts unchanged);
  renderExternal separate from viewExternal. PLACEMENT TRAP: the seq
  declaration sits AFTER the viewExternal definition — m238's renderPlace
  body slice runs to `async function viewExternal(`, so anything between
  renderPlace and viewExternal would enter that pinned slice (TDZ is not
  a concern: the top-level declaration executes before any call).
- Catalog honesty: EVERY returned venue renders (never a hard-coded set)
  with real fields only — raw ext_type, description or the honest
  «Описание места недоступно», per-service raw types with «Цена услуги:»/
  «Длительность услуги:», and «Восстановление здоровья:» ONLY when
  heal_amount is a number (treatment). A non-array catalog or a venue
  without a services array is MALFORMED → failure-with-retry, never a
  silent empty screen; a valid empty array renders «Места и услуги не
  найдены» with submit disabled.
- Structured quantity REPLACES the raw-JSON cart: the backend contract
  (validators.py TRAVEL_EXTERNAL) accepts exactly one item_type per
  purchase service with an integer qty 1–10 — a number input (min 1 /
  max 10 / step 1) with explicit Number.isSafeInteger + range validation
  says the same thing without asking the player to write JSON. Non-
  integer/out-of-range is rejected with an honest error, NEVER silently
  clamped; the row shows only for a purchase service with a usable
  item_type; the quantity resets to 1 on every service change; a purchase
  service WITHOUT a usable item_type disables submit with
  «Конфигурация покупки недоступна». The old JSON.parse cart is gone —
  the file-wide JSON.parse count drops 2→1 (only the WS message parse
  remains; no test pinned the old count — reconciled here).
- Automatic round-trip honesty: TRAVEL_EXTERNAL is ONE task whose
  completion handler logs departed + returned — there is NO player-chosen
  return, so there is NO fake Return button; the return note says the
  trip ends with the arrival back on Рейнеке. 480 min / 50 ₽ are CONFIG
  values the catalog contract does NOT expose — the totals note says the
  full cost/duration are unavailable here instead of hard-coding them.
- Submit safety: the button disables during the POST (no double
  submission); on success the gotoWorld() navigation fires ONLY when the
  hash and character are still the submitted ones (no yank after
  navigation away — the task exists server-side either way). The m147
  AUTONOMOUS catch handling is preserved BYTE-EXACT (EXTERNAL_CATCH_BLOCK
  pinned ==1 at its exact indentation — the submit button is therefore
  built inline in the composition at the natural 12-space depth, and the
  disable reaches the button via ev.currentTarget + the "external-submit"
  id lookup). The pinned block's err write is render-scoped by node
  detachment: a stale write lands on a detached node and is invisible —
  the explicit submittedHash/submittedCharId guard guards the SUCCESS
  navigation (the yank hazard).
- Navigation: «К острову →» is a plain hash link (no new gotoWorld call
  site — the file-wide count stays 5). NO mapLink and NO #/location links
  in the external body: external venues have no island coordinates.
- Honest NEEDS-BACKEND rows (exact copy pinned below): no city-level
  description, no free-requests linkage, no city movement, no player-
  chosen return, no service-availability states, no places beyond the
  catalog. The mockup's tabs, venue-category strip, hero image, Plan-Trip
  composer and marketing copy are REJECTED as fake capability.
- CSS: none — the screen reuses .place-shell/.panel/.muted/
  .place-actions/.err-text; zero style.css changes.

Byte-pins over backend/app/web/app.js, in the m157/m170/m176/m237/m238/
m239/m240/m241 house style (exact literal counts — update deliberately,
never weaken).
"""

import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
APP_JS = os.path.join(ROOT, "backend/app/web/app.js")


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _external_body(js):
    """The #77 external-trip screen body — viewExternal + renderExternal.

    Sits between the #72 place section (renderPlace → viewExternal is the
    m238-pinned slice boundary) and the event-feed section. The seq
    declaration lives INSIDE this slice, AFTER the viewExternal definition
    (placement trap — see docstring).
    """
    start = js.index("async function viewExternal(")
    end = js.index("/* ---------- event feed", start)
    return js[start:end]


# ---------- a: entry normalization ----------

def test_external_entry_normalization_pins():
    js = _read(APP_JS)
    body = _external_body(js)
    # The entry guard exists exactly once — a world-button click assigns
    # the hash and returns; the re-dispatch does the single fetch.
    assert body.count('if (location.hash !== "#/external")') == 1
    # Exactly ONE hash assignment survives (m175:63, unchanged).
    assert js.count('location.hash = "#/external";') == 1
    # The guard sits BEFORE the loading render (the early return must not
    # flash a screen).
    assert body.index('if (location.hash !== "#/external")') < body.index(
        'const loading = el("div", { class: "muted" }, "Загрузка…");')


# ---------- b: stale guards + placement trap ----------

def test_external_stale_guard_pins():
    js = _read(APP_JS)
    body = _external_body(js)
    # One module-level sequence, checked on BOTH of its paths (error +
    # apply)…
    assert js.count("let externalLoadSeq = 0;") == 1
    assert body.count("const seq = ++externalLoadSeq;") == 1
    assert body.count("(seq !== externalLoadSeq") == 2
    # …each guard carrying the current hash, the session/character
    # presence and the host connectivity (all FOUR guards).
    assert body.count("location.hash !== viewHash") == 2
    assert body.count("!S.user || !S.character") == 2
    assert body.count("!loading.isConnected") == 2
    # PLACEMENT TRAP: the declaration sits AFTER the viewExternal
    # definition — anything between renderPlace and viewExternal would
    # enter m238's pinned renderPlace body slice.
    assert js.index("let externalLoadSeq = 0;") > js.index(
        "async function viewExternal(")
    # renderExternal is separate from viewExternal and defined after it.
    assert js.count("async function viewExternal()") == 1
    assert js.count("function renderExternal(") == 1
    assert js.index("async function viewExternal(") < js.index(
        "function renderExternal(")
    # viewExternal calls renderExternal exactly once (apply path only).
    assert body.count("renderExternal(catalog);") == 1


# ---------- c: file-wide guard total ----------

def test_external_hash_guard_total_pin():
    js = _read(APP_JS)
    # 10 pre-#77 (place ×2 + home ×2 + career ×2 + events ×2 + … the m238
    # total) + 2 external guards (fetch-error + apply) = 12 (m238 records
    # the same total).
    assert js.count("location.hash !== viewHash") == 12


# ---------- d: heading ----------

def test_external_heading_pins():
    js = _read(APP_JS)
    body = _external_body(js)
    # The heading literal stays at 2 file-wide: the world screen's button
    # + this screen's h2 (no third copy invented).
    assert js.count("Поездка во Владивосток") == 2
    assert body.count('el("h2", {}, "Поездка во Владивосток")') == 1


# ---------- e: framing / return / totals honesty lines ----------

def test_external_honesty_line_pins():
    js = _read(APP_JS)
    body = _external_body(js)
    # Framing: off-map destination, port transit — always visible.
    assert body.count(
        '"Внешняя локация — вне карты острова. Проезд через порт острова."') == 1
    # Return honesty: the trip is an AUTOMATIC ROUND-TRIP — no fake
    # Return button, no player-chosen return.
    assert body.count(
        '"Возврат автоматический — задача поездки завершается возвращением на Рейнеке."') == 1
    # Totals honesty: 480 min / 50 ₽ are CONFIG values the catalog does
    # not expose — never hard-coded.
    assert body.count(
        '"Полная стоимость и длительность поездки здесь недоступны."') == 1
    assert body.count("480") == 0
    assert body.count("50 ₽") == 0


# ---------- f: honest NEEDS-BACKEND rows ----------

def test_external_needs_backend_pins():
    js = _read(APP_JS)
    body = _external_body(js)
    # Exact copy, each exactly once in the external body.
    assert body.count('"Общее описание города недоступно"') == 1
    assert body.count('"Связь поездки со свободными запросами недоступна"') == 1
    assert body.count('"Пребывание и перемещения по городу недоступны"') == 1
    assert body.count('"Возвращение по выбору игрока недоступно"') == 1
    assert body.count('"Данные о временной недоступности услуг недоступны"') == 1
    assert body.count(
        '"Места вне каталога, включая рестораны, ночные заведения '
        'и достопримечательности, недоступны"') == 1
    # The mockup's fake capability stays out: no tabs, no venue-category
    # strip, no hero image, no Plan-Trip composer, no marketing copy.
    for label in ("Plan Trip", "Shopping Centers", "Nightlife",
                  "Restaurants", "hero"):
        assert body.count(label) == 0, label


# ---------- g: catalog rendering literals ----------

def test_external_catalog_pins():
    js = _read(APP_JS)
    body = _external_body(js)
    # The venues section heading (quoted element form — the empty state's
    # «Места и услуги не найдены» is a different literal).
    assert body.count('el("h3", {}, "Места и услуги")') == 1
    # Description honesty: falsy description → the unavailable note.
    assert body.count('"Описание места недоступно"') == 1
    # Per-service real fields (raw types + price + duration), each literal
    # exactly once (template-string forms — bare substrings, body-scoped);
    # the heal note rides the typeof gate.
    assert body.count("Цена услуги: ") == 1
    assert body.count("Длительность услуги: ") == 1
    assert body.count("Восстановление здоровья: до ") == 1
    assert body.count('typeof svc.heal_amount === "number"') == 1
    # EVERY returned venue renders — the map iterates the catalog, no
    # hard-coded venue set.
    assert body.count("catalog.map((loc) =>") == 1


# ---------- h: structured quantity (JSON cart gone) ----------

def test_external_quantity_pins():
    js = _read(APP_JS)
    body = _external_body(js)
    # The number input exists exactly once with the 1–10 contract bounds…
    assert body.count('type: "number"') == 1
    assert body.count('min: "1"') == 1
    assert body.count('max: "10"') == 1
    assert body.count('step: "1"') == 1
    # …with EXPLICIT integer validation (never a silent clamp)…
    assert body.count("Number.isSafeInteger(qty)") == 1
    assert body.count('"Количество: целое число от 1 до 10"') == 1
    # …and the purchase-shape gate: only a purchase service with a usable
    # item_type shows the row; a broken one disables submit with the
    # honest explanation.
    assert body.count('"Конфигурация покупки недоступна"') == 1
    # Three sites: the usable check, the broken-purchase check, and the
    # submit-payload gate (purchase → {item_type: qty}, else {}).
    assert body.count('svc.type === "purchase"') == 3
    # The raw-JSON cart is GONE: no JSON.parse in the external body; the
    # file-wide count dropped 2→1 (only the WS message parse remains —
    # no test pinned the old count; reconciled in the docstring).
    assert body.count("JSON.parse") == 0
    assert js.count("JSON.parse") == 1


# ---------- i: navigation ----------

def test_external_navigation_pin():
    js = _read(APP_JS)
    body = _external_body(js)
    # The plain hash link replaces the oldHash back-button…
    assert body.count('el("a", { href: "#/world" }, "К острову →")') == 1
    # …and adds NO new gotoWorld call site (the file-wide count is
    # unchanged — the submit's guarded success navigation is the only one
    # in this body).
    assert js.count("gotoWorld();") == 5
    assert body.count("gotoWorld();") == 1


# ---------- j: empty state ----------

def test_external_empty_state_pin():
    js = _read(APP_JS)
    body = _external_body(js)
    # A valid empty array renders the honest empty note (submit disabled
    # via the sync gate) — a MALFORMED catalog never reaches this render.
    assert body.count('"Места и услуги не найдены"') == 1


# ---------- k: TRAVEL_EXTERNAL + m147 preservation ----------

def test_external_submit_and_m147_pins():
    js = _read(APP_JS)
    body = _external_body(js)
    # The submit posts the EXISTING action through the EXISTING /actions
    # path — no new call site (doAction + moveTo + this submit = 3).
    assert js.count('action_type: "TRAVEL_EXTERNAL"') == 1
    assert js.count('api("/actions"') == 3
    # The m147 AUTONOMOUS catch handling is preserved: the helper is used
    # twice in this body (err mapping + toast gate) — the file-wide count
    # stays 6 (1 decl + 5 catch usages; m147 pins the exact block).
    assert body.count("isAutonomousRefusal") == 2
    assert js.count("isAutonomousRefusal") == 6
    # The port toast literal survives exactly once.
    assert js.count('toast("Сначала дойдите до порта", true);') == 1
    # Submit safety (validator F1/F2): an in-flight latch respected by BOTH
    # the handler and the selector sync (the disabled attribute alone is
    # bypassable — syncQtyRow re-enables on every change; the backend does
    # NOT reject concurrent trips), and the success navigation guarded on
    # hash + null-safe character + connectivity of the ORIGINATING render
    # (external → away → external during a pending POST must not yank the
    # freshly opened screen).
    assert body.count("let submitting = false;") == 1
    assert body.count("if (submitting) return;") == 1
    assert body.count("submitting = true;") == 1
    # The release is pinned WITH its 12-space indent — the bare substring
    # would also match inside the `let submitting = false;` declaration.
    assert body.count("\n            submitting = false;") == 1
    assert body.count("submitting || broken || services.length === 0") == 1
    assert body.count("const btn = ev.currentTarget;") == 1
    assert body.count("btn.disabled = true;") == 1
    assert body.count("const submittedHash = location.hash;") == 1
    assert body.count("const submittedCharId = S.character ? S.character.id : null;") == 1
    assert body.count('const screenNode = ev.currentTarget.closest("main");') == 1
    assert body.count("location.hash === submittedHash") == 1
    assert body.count("S.character && S.character.id === submittedCharId") == 1
    assert body.count("screenNode.isConnected") == 1


# ---------- l: renderExternal wiring ----------

def test_external_render_wiring_pins():
    js = _read(APP_JS)
    body = _external_body(js)
    # Defined once, called once (the apply path; the retry path
    # re-dispatches viewExternal instead).
    assert js.count("function renderExternal(") == 1
    assert body.count("renderExternal(catalog);") == 1


# ---------- m: no map affordance for external venues ----------

def test_external_no_map_pins():
    js = _read(APP_JS)
    body = _external_body(js)
    # External venues have no island coordinates: no mapLink reuse, no
    # place links, no map-marker code.
    assert body.count("mapLink") == 0
    assert body.count('"#/location/') == 0
    assert body.count("anchor--") == 0
    assert body.count("buildLivingMap") == 0
    assert body.count("mapMarker") == 0


# ---------- chrome + hygiene ----------

def test_external_chrome_and_hygiene_pins():
    js = _read(APP_JS)
    body = _external_body(js)
    # The screen highlights «Мир» (world-scoped): loader + the final render.
    assert body.count('topbar("#/world")') == 2
    # Loading + retry pair: ONE loading node, ONE retry site (m175 counts
    # unchanged at 15/15).
    assert body.count(
        'const loading = el("div", { class: "muted" }, "Загрузка…");') == 1
    assert body.count("loadRetryNode(loading, () => viewExternal());") == 1
    # Malformed defense: the shape check exists once, before the apply
    # guard — a non-array catalog or a venue without a services array is
    # a retry, never a silent empty screen.
    assert body.count("!Array.isArray(data)") == 1
    assert body.count("!Array.isArray(loc.services)") == 1
    assert body.index("!Array.isArray(data)") < body.index(
        "if (seq !== externalLoadSeq || location.hash !== viewHash")
    # The purpose selector keeps its 3 client-vocabulary options.
    assert body.count('el("option", { value: "heal" }, "лечение")') == 1
    assert body.count('el("option", { value: "shop" }, "покупки")') == 1
    assert body.count('el("option", { value: "visit" }, "визит")') == 1
    # XSS hygiene holds file-wide (m146 mirror).
    assert js.count("innerHTML") == 0
    # The external body invents NO new api() endpoint: only the existing
    # /external read (the trip posts through the existing /actions site).
    for endpoint in ('api("/scene/act"', 'api("/visual/scenes"',
                     "/travel/plan", 'api("/market', 'api("/admin',
                     'api("/world/events'):
        assert body.count(endpoint) == 0, endpoint
