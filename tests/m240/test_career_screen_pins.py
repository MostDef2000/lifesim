"""#75 — Career / Activity / Role-progression screen: subordinate static
route #/career.

Approved architecture (binding): ONE new static registry entry, NO backend
changes, NO new APIs, NO new topbar tab, NO fake data. Decisions recorded
for the stranger reader:

- Subordinate static route, NO tab: "#/career" sits in the `routes`
  registry (dispatched through (routes[hash] || viewWorld)()) but the
  topbar tabs array is untouched — 9 tabs stay 9. The screen highlights
  «Профиль» (its parent surface) and is reached from the profile's roles
  block link («Развитие →»).
- Data: BOTH reads are fetched FRESH every visit — the owner payload
  GET /characters/{cid} (ch.job) and the derive-on-read roles endpoint
  GET /characters/{cid}/roles. Missing hydration is NOT no-job/no-roles:
  a stale S.character must never decide the no-job state, and the roles
  list always reflects the current derivation. ONE shared failure path:
  either fetch failing (or a MALFORMED roles payload — not an object, or
  roles not an array) is a load failure with retry, NEVER rendered as
  «Работа не назначена» / «Ролей нет».
- WORK rides the EXISTING doAction (POST /actions) with a no-op stay:
  task acceptance is not a completed shift, so the screen neither yanks
  the player to the world nor fakes a refresh. The validator
  (backend/app/actions/validators.py) rejects WORK without a job (422
  "Character has no job" → doAction's honest toast) and auto-redirects
  the character to the workplace when away (needs_move) — the client does
  NOT know the workplace id, so there is NO away-note anywhere: the
  server moves the character transparently.
- Roles rendering: per role title + source + reason — the reason is WHY
  the role is derived (the honest world-behavior link, e.g. newcomer day
  count), never a progress meter. The payload is {id,title,source,reason}
  and nothing more.
- Honest NEEDS-BACKEND rows (exact copy pinned below): the roles payload
  carries NO level/progress/requirements/benefits, and no career ladder,
  rewards or role-development states exist anywhere in the backend. The
  mockup's XP/levels/benefits/rewards and its aspirational role catalog
  (Photographer etc.) are REJECTED as fake capability — nothing is
  invented.
- Roles call-site 1→2 (deliberate shift, m63/m156 updated): the roles
  fetch is no longer profile-exclusive — ONE request PER SCREEN. The
  profile card still reuses its single fetch for the summary line,
  partner chip and detail list (never re-fetches); the career screen
  fetches its own fresh copy.
- Bridges: standing «Цель →» (the #74 vocabulary) and «Дела →» — the
  tasks screen is linked, NOT duplicated here (no task journal on the
  career screen). No desire-opportunity relabeling.
- CSS: none — the screen reuses .place-shell/.panel/.muted/.place-actions
  and the profile role-list classes; zero style.css changes.

Byte-pins over backend/app/web/app.js, in the m157/m170/m176/m237/m238/
m239 house style (exact literal counts — update deliberately, never
weaken).
"""

import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
APP_JS = os.path.join(ROOT, "backend/app/web/app.js")


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _career_body(js):
    """The #75 Career screen body — viewCareer loader + renderCareer.

    The two functions are adjacent and sit BEFORE the #74 Home screen's
    viewHome (outside the m239 home-body slice viewHome→viewLocation and
    outside both m238 place-body slices — those bodies are not disturbed).
    """
    start = js.index("async function viewCareer(")
    end = js.index("async function viewHome(", start)
    return js[start:end]


def _topbar_body(js):
    """The topbar() body — the tabs array must never grow a Career tab."""
    start = js.index("function topbar(active) {")
    return js[start:js.index("\n}", start)]


# ---------- a: subordinate static route, no tab ----------

def test_career_route_registry_pins():
    js = _read(APP_JS)
    # The static registry entry exists exactly once…
    assert js.count('"#/career": viewCareer') == 1
    # …inside the routes object (dispatched through the static dispatch).
    routes_def = js.index("const routes = {")
    routes_end = js.index("};", routes_def)
    assert routes_def < js.index('"#/career": viewCareer') < routes_end
    # The registry comment records the honest row count (14→15 with #75)
    # and keeps the #74 note intact.
    assert js.count("15 rows total") == 1
    assert js.count("14 rows total") == 0
    # NO topbar tab: the tabs array never mentions the career route…
    assert js.count('["#/career"') == 0
    # …and neither the route nor the «Развитие» heading leaks into the
    # topbar body.
    assert _topbar_body(js).count("career") == 0
    assert _topbar_body(js).count("Развитие") == 0


# ---------- c: honest literals ----------

def test_career_honest_literal_pins():
    js = _read(APP_JS)
    body = _career_body(js)
    # Missing-job state (fresh-fetch null) — exactly once, in renderCareer.
    assert body.count('"Работа не назначена"') == 1
    # NEEDS-BACKEND rows, exact copy, each exactly once: the sim has no
    # career read model — nothing XP/level/benefit/reward-shaped is faked.
    assert body.count('"Уровень и прогресс недоступны"') == 1
    assert body.count('"Требования для развития ролей недоступны"') == 1
    assert body.count('"Преимущества ролей недоступны"') == 1
    assert body.count('"Карьерные возможности и награды недоступны"') == 1
    assert body.count('"Состояния развития ролей недоступны"') == 1
    # Empty roster: an honest state, career-body-scoped (the profile's own
    # «Ролей нет» literal is separate and untouched).
    assert body.count('"Ролей нет"') == 1
    # The h2 heading is the ONLY quoted «Развитие» literal in the file
    # (the profile link's «Развитие →» is a different string).
    assert js.count('"Развитие"') == 1
    assert body.count('el("h2", {}, "Развитие")') == 1
    # NO away-note anywhere: the client does not know the workplace id —
    # the validator auto-redirects transparently (needs_move), so the
    # screen must not pretend to know the character is away.
    assert body.count("отправится") == 0
    assert body.count("недоступна") == 0


# ---------- d: WORK rides the EXISTING doAction ----------

def test_career_work_uses_existing_doaction():
    js = _read(APP_JS)
    body = _career_body(js)
    # The career body posts WORK through the shared doAction helper, once.
    assert body.count('doAction("WORK"') == 1
    # The no-op stay is explicit: task acceptance is not a completed
    # shift — no navigation, no fake refresh.
    assert body.count('doAction("WORK", {}, () => {})') == 1
    # NO new /actions call site: the file-wide count is unchanged — WORK
    # reuses the existing POST /actions path (doAction + moveTo + the
    # external-trip call).
    assert js.count('api("/actions"') == 3
    # The button label exists exactly once in the career body (the m237
    # free-requests labeler keeps its own `return "Работать";` form).
    assert body.count('"Работать")') == 1


# ---------- e: data contract — fresh fetches, roles call-site 1→2 ----------

def test_career_data_contract_pins():
    js = _read(APP_JS)
    body = _career_body(js)
    # BOTH reads are fetched fresh — missing hydration is never a state.
    assert body.count('api(`/characters/${S.character.id}`)') == 1
    assert body.count('api(`/characters/${S.character.id}/roles`)') == 1
    # File-wide roles fetch: profile (m63/m156) + career (#75) = 2 — one
    # request PER SCREEN; the profile card still reuses its single fetch.
    assert js.count('api(`/characters/${S.character.id}/roles`)') == 2
    # A malformed roles payload is a load failure, not an empty roster —
    # the shape check exists exactly once, before the apply guard.
    assert body.count("!Array.isArray(data.roles)") == 1
    assert body.index("!Array.isArray(data.roles)") < body.index(
        "if (seq !== careerLoadSeq || location.hash !== viewHash")


# ---------- f: stale guards ----------

def test_career_stale_guard_pins():
    js = _read(APP_JS)
    body = _career_body(js)
    # Stale-response guards (m238/#74 pattern): one module-level sequence,
    # checked on BOTH of its paths (error + apply)…
    assert js.count("let careerLoadSeq = 0;") == 1
    assert js.count("const seq = ++careerLoadSeq;") == 1
    assert body.count("(seq !== careerLoadSeq") == 2
    # …each guard carrying the current hash, the session/character
    # presence and the host connectivity (all FOUR guards).
    assert body.count("location.hash !== viewHash") == 2
    assert body.count("!S.user || !S.character") == 2
    assert body.count("!loading.isConnected") == 2
    # renderCareer is separate from viewCareer (m238/#74 layering), and
    # the whole career body sits BEFORE the #74 Home screen's viewHome.
    assert js.count("async function viewCareer(") == 1
    assert js.count("function renderCareer(") == 1
    assert js.index("async function viewCareer(") < js.index(
        "function renderCareer(") < js.index("async function viewHome(")


# ---------- g: bridges ----------

def test_career_bridge_pins():
    js = _read(APP_JS)
    body = _career_body(js)
    # Standing desire bridge (the #74 vocabulary, same literal form)…
    assert body.count('el("a", { href: "#/desire" }, "Цель →")') == 1
    # …and the tasks bridge — the tasks screen is linked, NOT duplicated
    # (no task journal on the career screen).
    assert body.count('el("a", { href: "#/tasks" }, "Дела →")') == 1
    assert body.count("Журнал") == 0
    # File-wide #/desire hash links: 8 pre-#75 + 1 career = 9 (m170/m173/
    # m239 record the same total).
    assert js.count('href: "#/desire"') == 9
    # The career bridge is the file's only #/tasks hash link.
    assert js.count('href: "#/tasks"') == 1


# ---------- h: profile entry link ----------

def test_career_profile_link_pins():
    js = _read(APP_JS)
    # The standing link beside the profile's roles block, exactly once —
    # muted, vocabulary-consistent with the #74 housing link.
    assert js.count('href: "#/career"') == 1
    assert js.count('el("a", { href: "#/career", class: "muted" },'
                    ' "Развитие →")') == 1
    # The profile keeps its ONE roles fetch (m63/m156 pin the file-wide
    # count at 2: profile + career) — the link adds no fetch.
    assert js.count('api(`/characters/${S.character.id}/roles`)') == 2


# ---------- i: placement ----------

def test_career_placement_pins():
    js = _read(APP_JS)
    # The career body sits BEFORE the #74 Home screen's viewHome…
    assert js.index("async function viewCareer(") < js.index(
        "async function viewHome(")
    # …AFTER the #72 place section (not inside either m238 slice)…
    assert js.index("function placeTypeIcon(") < js.index(
        "async function viewCareer(")
    # …and does not leak into m239's home-body slice (viewHome→
    # viewLocation carries no career code).
    home_start = js.index("async function viewHome(")
    home_body = js[home_start:js.index("async function viewLocation(",
                                       home_start)]
    assert home_body.count("career") == 0
    assert home_body.count("Career") == 0


# ---------- chrome + hygiene ----------

def test_career_chrome_and_hygiene_pins():
    js = _read(APP_JS)
    body = _career_body(js)
    # The screen highlights «Профиль» (no Career tab exists): loader +
    # the final render.
    assert body.count('topbar("#/profile")') == 2
    # Loading + retry pair: ONE loading node, ONE retry site (the shared
    # failure path for both fetches).
    assert body.count(
        'const loading = el("div", { class: "muted" }, "Загрузка…");') == 1
    assert body.count("loadRetryNode(loading, () => viewCareer());") == 1
    # XSS hygiene holds file-wide (m146 mirror).
    assert js.count("innerHTML") == 0
    # No type-derived action labels anywhere (inventory verbs stay gone).
    for label in ("Съесть", "Выпить", "Использовать", "Выбросить",
                  "Купить"):
        assert body.count(label) == 0, label
    # The career body invents NO new api() endpoint: only the existing
    # /characters + /roles reads (WORK goes through doAction).
    for endpoint in ('api("/scene/act"', 'api("/visual/scenes"',
                     "/travel/plan", 'api("/market', 'api("/admin'):
        assert body.count(endpoint) == 0, endpoint
    # No map-marker code: the career screen is a profile-subordinate
    # panel screen, not a map surface (m149/m157 boundary).
    assert body.count("anchor--") == 0
    assert body.count("buildLivingMap") == 0
    assert body.count("mapMarker") == 0
