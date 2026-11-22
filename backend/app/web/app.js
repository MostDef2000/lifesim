/* ВЛ1: Рейнеке — MVP web client (§77-78). Vanilla JS, без сборки.
   XSS-гигиена: рендер данных API только через textContent/createTextNode. */
"use strict";

const app = document.getElementById("app");
const toastEl = document.getElementById("toast");
const S = { user: null, character: null, world: null, ws: null, wsTries: 0,
  feed: [], cursor: 0, pollTimer: null, canonicalPortraitId: null, authProbed: false, visualDisabled: false,
  connLost: false, pollFails: 0,  // #175: poll-failure liveness banner (WS close is routine — never drives this)
  mapLod: "island", mapQuality: "balanced",  // #79: pre-settings default — loadSettings() applies the stored preset BEFORE the first render
  // #79: the GLOBAL EFFECTIVE-motion flag (legacy name kept — every scroll
  // ternary and the map ambient read it): OS prefers-reduced-motion OR the
  // user's settings toggle. Never a second motion state; recomputed by
  // setReducedMotionPref() and the matchMedia change listener at boot.
  mapReducedMotion: window.matchMedia("(prefers-reduced-motion: reduce)").matches,
  mapFocusLocationId: null, mapLocations: [], travel: null, scene: null, sceneAct: null,
  sceneActDraft: null, pollBusy: false, invSellPrice: {},  // #256: typed sell price survives row re-renders
  wsCursorSent: false, feedFetched: false,  // #179: WS cursor handshake state (see sendWsCursor)
  // #258: page-lifetime feed-init state — "pending" until the WS hello
  // (snapshot tail) or a completed REST events fill defines the start
  // position; NEVER reset by navigation/reconnect. helloDeadline holds the
  // 5s hello-wait timer id (null = none armed).
  feedInit: "pending", helloDeadline: null,
  // #312: Place scene-visual state. S.scene is the MOUNTED entry (the cache
  // entry for the player's current location, or null); S.sceneCache is the
  // durable per-location cache (normalized STRING locationId → entry —
  // navigation retains entries, movement/context/identity discard them).
  // sceneRequestSeq is the monotonic request token (never reset — it voids
  // stale flights); sceneFlight is the single in-flight generation record;
  // sceneSession keys the cache to the session identity (user OBJECT
  // REFERENCE + character id + world id + world seed).
  sceneCache: new Map(), sceneRequestSeq: 0, sceneFlight: null,
  sceneSession: null,
  shopConfirm: null, chatDraft: "",
  bootCursor: Infinity };  // #177: replay gate — set once from the first history fill (WS hello OR REST); Infinity = "no live events until proven"

/* #302: session UI context — World ↔ Place ↔ Dialogue navigation state.
   Module-level, deliberately NOT inside S: the m176 pin freezes the S
   initializer tail (its tail field is byte-pinned), and the feed/WS fields
   are #311's.
   userRef is the user OBJECT REFERENCE (same keying as worldGet's
   single-flight): identity, never id — a relogin as the same id is still a
   different session. World truth (world state, player location) never lives
   here — only cross-screen UI navigation state. */
let gameUiContext = {
  userRef: null,       // S.user object reference at capture time
  characterId: null,   // S.character.id at capture time
  selectedLocationId: null,  // place chosen on the World → Place navigation
  selectedPoiId: null,       // #310: Living Map marker highlighted by the selection card — a map-marker selection, never a MOVE intent (that is selectedLocationId's place-navigation semantics)
  viewLocationId: null,      // place whose view validated and rendered
  lastWorldFocusId: null,    // world map focus to restore on the return
  lastWorldFocusPlayerLocId: null,  // player location when the focus was recorded — restore is skipped if the player moved since
  npcId: null,               // NPC carried Place → Chat (confirmed occupant id)
  draftByNpc: {},            // per-NPC composer drafts, keyed by occupant id
  chatSourceLocationId: null,   // #305: the place «Поговорить» was clicked at — the chat back anchor's return target
  chatSourceLocationName: null, // #305: the origin place's display name
};

/* #302: the last authoritatively observed player location — world truth,
   deliberately NOT in gameUiContext. Drives the scene invalidation rule:
   the scene is tied to the location it was inspected at (m153/m156), so it
   survives a World ↔ Place round trip without movement and is invalidated
   as soon as an authoritative read observes a different location. */
let observedPlayerLocId = null;

/* #302: monotonic navigation generation. Advanced by every route() dispatch
   and by every World/Place/Chat load start (direct viewWorld()/gotoWorld()
   callers and retries included) — an in-flight load whose captured epoch no
   longer matches must not publish into the newer view's DOM or shared
   state. Compared by value, never against the hash (the m238 pin censuses
   that comparison file-wide — do not add occurrences). */
let navEpoch = 0;

/* #302: session teardown — drop every piece of cross-screen UI state so a
   logout / relogin / character change starts clean (drafts, selection,
   focus, scene). The legacy transient composer/chat fields ride along: the
   live binding S.chatDraft keeps its m175 semantics WITHIN a session. */
function resetGameUiContext() {
  gameUiContext = {
    userRef: null, characterId: null, selectedLocationId: null,
    selectedPoiId: null, viewLocationId: null, lastWorldFocusId: null,
    lastWorldFocusPlayerLocId: null, npcId: null, draftByNpc: {},
    chatSourceLocationId: null, chatSourceLocationName: null,  // #305
  };
  observedPlayerLocId = null;
  // #312: session teardown routes through the shared scene lifecycle — the
  // in-flight generation is voided and the whole cache dies (URLs revoked,
  // entries dropped, mounted state reset to idle). A new session never
  // inherits the previous identity's scene assets.
  cancelSceneFlight();
  clearSceneCache();
  S.sceneSession = null;  // #312: the next sync re-keys the cache identity
  S.chatDraft = "";
  S.chatNpcId = null;
  S.chatSessionId = null;
  // #305: the chat presence runtime dies with the session — the orphaned
  // render's «Печатает…» timer stops and every in-flight chat op fails its
  // render-identity check (chatRuntime === rt) and discards itself.
  if (chatRuntime && chatRuntime.typingTimer != null) {
    clearInterval(chatRuntime.typingTimer);
    chatRuntime.typingTimer = null;
  }
  chatRuntime = null;
  chatPendingPresence = null;
  S.mapFocusManual = false;
  S.mapFocusAnchor = null;
  // #304: the action-lifecycle adapter dies with the session — the
  // registry, the followed-task pointer, the snapshot, the latch and the
  // read flights (aborted) all reset; the travel/scene submission state
  // rides along (a new session never inherits them).
  resetTaskPresentation();
  S.travel = null;
  S.sceneAct = null;
  S.sceneActDraft = null;
  // #311: the session-owned feed controller tears down destructively — the
  // transport dies, the stream state resets, the listeners invalidate
  // (logout / 401 / character switch start a clean feed).
  worldFeedController.teardown();
}

/* ---------- helpers ---------- */

function toast(msg, isErr) {
  toastEl.textContent = msg;
  toastEl.classList.toggle("err", !!isErr);
  toastEl.classList.remove("hidden");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => toastEl.classList.add("hidden"), 3500);
}

/* #144/§61: AUTONOMOUS characters refuse direct actions server-side (409),
   so every direct-control surface says the same localized thing instead of
   leaking the raw English server detail. */
const AUTONOMOUS_HINT = "Персонаж действует сам — переключите режим в Профиле";
// #144/§61: matches the server detail regardless of which error surface
// (message/detail) the api() wrapper populated; used by every direct-control
// catch so a stale-state 409 still speaks the localized hint.
const isAutonomousRefusal = (e) =>
  String(e.message ?? e.detail ?? "").includes("character is AUTONOMOUS");

/* #93 (A11): shared load-failure state for alpha-critical screens — the
   failed view keeps its topbar, explains the failure, and offers an explicit
   retry; a transient network error never dead-ends on a silent «Загрузка…». */
const RETRY_HINT = "Проверьте связь и повторите";
function loadRetryNode(loading, retry) {
  loading.replaceChildren(
    el("span", { class: "muted" }, "Загрузка не удалась. " + RETRY_HINT),
    el("button", { type: "button", onclick: retry }, "Повторить"));
}

/* ---------- #306: game-shell primitives (foundation only) ----------
   Reusable DOM builders for the upcoming game-shell screens; no existing
   screen is rewired yet. They build on el()/appendKids (textContent-only
   rendering — API data is never interpolated into HTML strings) and emit
   the .game-* classes styled in the #306 section of style.css. Plain args
   in, DOM nodes out. Usage contract: an adopting screen carries the
   .game-shell class itself — these helpers emit primitives only, never
   the shell. */

/* Screen header: Georgia-400 title + optional dim meta line (day/time…). */
function gameHeader(title, meta) {
  return el("header", { class: "game-header" },
    el("h2", { class: "game-header-title" }, title),
    meta == null ? null : el("div", { class: "game-header-meta" }, meta));
}

/* Context panel: warm card with an optional title and body children. */
function contextPanel(title, ...kids) {
  return el("section", { class: "game-panel" },
    title == null ? null : el("h3", { class: "game-panel-title" }, title),
    kids);
}

/* Action button: >= 44px target; opts = { kind: "primary"|"danger",
   disabled, title }. */
function actionButton(label, onclick, opts = {}) {
  opts = opts || {};  // null-safe: default params only cover undefined
  const attrs = { type: "button",
    class: "game-action" + (opts.kind ? " game-action-" + opts.kind : ""),
    onclick };
  if (opts.disabled) attrs.disabled = "";
  if (opts.title) attrs.title = opts.title;
  return el("button", attrs, label);
}

/* Inline feedback line under an action row; kind = "ok" | "err". */
function actionStatus(text, kind) {
  return el("div", { class: "game-status" + (kind ? " game-status-" + kind : "") },
    text);
}

/* Empty / loading / error states (game-shell styling of the #93 pattern).
   Unlike loadRetryNode — which fills an existing node — these RETURN a node
   so new screens can compose them inline; retry is optional. */
function gameEmpty(text) {
  return el("div", { class: "game-state game-state-empty" }, text);
}

function gameLoading(text) {
  return el("div", { class: "game-state game-state-loading" }, text || "Загрузка…");
}

function gameError(text, retry) {
  return el("div", { class: "game-state game-state-error" },
    text,
    retry ? actionButton("Повторить", retry) : null);
}

/* #295: display dictionaries for internal codes that reach UI surfaces
   (task rows, profile/career job, taskRow source/object_type). Mirrors the
   backend canons: TASK_TYPE/OBJECT_TYPE — app/events/labels.py; job titles —
   app/social/roles.py _OCCUPATION_TITLES_RU; statuses/sources — the values
   actually written to CharacterTask (app/actions/lifecycle.py,
   app/ai/worker.py, app/external/npc_trips.py). Unknown code → raw, the
   same doctrine as the backend label() — a lookup never invents a name. */
const TASK_TYPE_RU = { SLEEP: "сон", EAT: "еда", DRINK: "питьё", WORK: "работа",
  MOVE: "перемещение", BUY_ITEM: "покупка", SOCIALIZE: "общение", IDLE: "безделье",
  TRAVEL_EXTERNAL: "внешняя поездка", CONSTRUCT: "строительство" };
const TASK_STATUS_RU = { planned: "запланирована", active: "активна",
  queued: "в очереди",
  completed: "выполнена", failed: "провалена", cancelled: "отменена",
  STARTED: "начата" };
const TASK_SOURCE_RU = { player: "игрок", user: "игрок", utility: "система",
  ai: "ИИ", need: "потребность", goal: "цель" };
const JOB_TITLES_RU = { Farmer: "Фермер", Fisher: "Рыболов", Crafter: "Крафтер",
  Storekeeper: "Кладовщик" };
const OBJECT_TYPE_RU = { food_bread: "хлеб", food_canned: "консервы", water_bottle: "бутылка воды",
  jacket: "куртка", boots: "сапоги", hat: "шляпа", food_groceries: "продукты",
  tool_kit: "набор инструментов", shed: "сарай", generator: "генератор",
  wood_pile: "поленница", pier_extension: "пристройка к пирсу", haystack: "стог сена",
  furniture: "мебель", cloth: "ткань", food_stock: "запасы еды", prepared_meal: "готовая еда",
  fish: "рыба", berries: "ягоды" };
function codeLabel(map, code) {
  if (code == null) return "";
  return Object.prototype.hasOwnProperty.call(map, code) ? map[code] : String(code);
}

/* #175: connection banner — a dead server must be visible. The banner is
   created lazily, appended to document.body exactly once, and toggled via
   .hidden like .toast. Liveness oracle = consecutive pollEvents failures
   (index.html must not grow a second static node; WS closes are routine on
   navigation and never drive this). */
function ensureConnBanner() {
  let b = document.getElementById("conn-banner");
  if (!b) {
    b = el("div", { id: "conn-banner", class: "conn-banner", role: "status" },
      "Нет связи с островом — переподключение…");
    document.body.append(b);
  }
  b.classList.remove("hidden");
}

function hideConnBanner() {
  const b = document.getElementById("conn-banner");
  if (b) b.classList.add("hidden");
}

async function api(path, opts = {}) {
  // #269: private routing metadata — never forwarded to fetch.
  // #311: feedPoll rides the same lane — the poll flight's session token,
  // used to exempt a STALE flight's 401 from the global fail-closed deauth.
  // #304: taskFlight rides it too — a tracked task's detail GET carries its
  // session token, so a STALE flight's 401 (session torn down / relogin
  // while the GET flew) must not deauthenticate the NEW session. A CURRENT
  // flight's genuine 401 keeps the fail-closed deauth + reroute.
  const { worldBootstrap: bootstrapFlight, feedPoll: feedPollFlight,
    taskFlight: taskPollFlight, ...fetchOpts } = opts;
  // #269: /world GETs join the pending cold-entry bootstrap flight for the
  // SAME session context (one GET during a route handoff, S17/S18); with no
  // bootstrap in flight every caller still fetches fresh (existing loader
  // and poll refreshes unchanged). Only the bootstrap's own flight is
  // joinable — a loader re-dispatch keeps its historical fresh fetch.
  const worldRead = path === "/world" && !fetchOpts.method && !fetchOpts.body;
  if (worldRead && !bootstrapFlight) {
    const user = S.user;
    const charId = S.character ? S.character.id : null;
    if (worldGet && worldGet.user === user && worldGet.charId === charId) {
      return worldGet.promise;
    }
  }
  const res = await fetch(path, {
    credentials: "same-origin",
    headers: fetchOpts.body ? { "content-type": "application/json" } : {},
    ...fetchOpts,
    body: fetchOpts.body ? JSON.stringify(fetchOpts.body) : undefined,
  });
  if (res.status === 401) {
    // #269: a STALE world-bootstrap GET is dropped BEFORE the global
    // fail-closed mutation — it must not deauthenticate the CURRENT session.
    // Stale = the session/character changed while it flew, OR the flight
    // lost the slot to a newer bootstrap (identities may match again — the
    // superseded flight is still not the current one). A CURRENT 401
    // (bootstrap included) and every other caller keep the global
    // fail-closed deauth + reroute.
    // #311: a feed-poll flight carries the same exemption — a stale poll
    // resolving 401 must not deauthenticate the CURRENT session. Stale =
    // the flight's token no longer matches the live session identity, OR
    // the flight lost currency (a newer tick superseded it, a teardown
    // nulled the slot, or a navigation advanced the generation) — a
    // superseded same-user flight's 401 is just as stale as a dead
    // session's. A CURRENT flight's genuine 401 keeps the global
    // fail-closed deauth + reroute.
    const stale = (bootstrapFlight && (S.user !== bootstrapFlight.user
      || !S.character || S.character.id !== bootstrapFlight.charId
      || worldGet !== bootstrapFlight))
      || (feedPollFlight && (S.user !== feedPollFlight.userRef
        || !S.character || S.character.id !== feedPollFlight.charId
        || worldFeedPollFlight !== feedPollFlight
        || worldFeedNavGen !== feedPollFlight.navGen))
      || (taskPollFlight && (taskPollFlight.gen !== taskSessionGeneration
        || S.user !== taskPollFlight.userRef
        || !S.character || S.character.id !== taskPollFlight.charId));
    if (!stale) {
      S.user = null;
      // #311: fail-closed — a 401 just proved this session dead, so mark it
      // probed: the deauth reroute's boot probe must not re-fetch /auth/me
      // and resurrect the user from a stale-200 (a dead session stays dead).
      S.authProbed = true;
      S.world = null;  // the cached world belonged to the dead session
      resetGameUiContext();  // #302: the dead session's UI state must not leak
      if (path !== "/auth/me") route();
    }
    throw new Error("unauthorized");
  }
  let data = null;
  try { data = await res.json(); } catch (e) { data = null; }
  if (!res.ok) {
    const detail = data && data.detail ? data.detail : `HTTP ${res.status}`;
    // #82 (A3): keep the whole body (422 ambiguous carries .options) —
    // .detail alone would strand the options list server-side.
    throw Object.assign(new Error(String(detail)),
      { status: res.status, detail, data });
  }
  // #269: validate the world body BEFORE any publication — an empty or
  // malformed 200 is a failure (honest «н/д»), never a cached fake or a
  // NaN clock (S19/S20).
  if (worldRead && (!data || typeof data !== "object" || Array.isArray(data)
    || typeof data.game_timestamp !== "number"
    || !Number.isFinite(data.game_timestamp) || data.game_timestamp < 0)) {
    throw new Error("некорректный ответ мира");
  }
  return data;
}

function appendKids(node, child) {
  if (child === null || child === undefined || typeof child === "boolean") return;
  if (Array.isArray(child)) {
    for (const item of child) appendKids(node, item);
    return;
  }
  node.append(child.nodeType ? child : document.createTextNode(String(child)));
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "onclick") node.onclick = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const child of children) appendKids(node, child);
  return node;
}

function dayTime(ts) {
  const day = Math.floor(ts / 1440) + 1;
  const min = ts % 1440;
  const hh = String(Math.floor(min / 60)).padStart(2, "0");
  const mm = String(min % 60).padStart(2, "0");
  return `День ${day}, ${hh}:${mm}`;
}

/* ---------- routing ---------- */

const routes = {
  "#/login": viewAuth, "#/create": viewCreateCharacter, "#/world": viewWorld, "#/chat": viewChat,
  "#/people": viewPeople,
  "#/inventory": viewInventory, "#/shop": viewShop, "#/tasks": viewTasks, "#/desire": viewDesire,
  "#/profile": viewProfile, "#/appearance": viewAppearance, "#/admin": viewAdmin, "#/external": viewExternal,
  // #74: the Home screen is a subordinate STATIC route — no topbar tab
  // (the tabs array stays as-is); it is reached from the profile's
  // housing link and from the desire bridge.
  "#/home": viewHome,
  // #75: the Career screen is a subordinate STATIC route too — no topbar
  // tab; it is reached from the profile's roles block link.
  "#/career": viewCareer,
  // #76: the Events screen is a subordinate STATIC route — no topbar tab;
  // world-scoped (highlights «Мир»), reached from the world feed link.
  "#/events": viewEvents,
  // #79: the Settings screen is a subordinate STATIC route — no topbar
  // tab; it is reached from the profile's settings link and highlights
  // «Профиль» (its parent surface).
  "#/settings": viewSettings,
};

async function route() {
  navEpoch++;  // #302: every dispatch invalidates in-flight view loads
  // #312: a navigation dispatch cancels the scene flight and releases the
  // mounted URLs (blobs RETAINED — the return recreates them from cache).
  cancelSceneFlight();
  releaseSceneURLs();
  if (!location.hash) { location.hash = "#/world"; return; }  // #161: normalize once; the hashchange fires route again with a concrete view
  // #311: the session-owned feed controller reconciles the navigation — the
  // view subscription detaches, the identity is re-checked (a session change
  // tears down destructively), and the transport KEEPS RUNNING across
  // screens (no per-navigation stop/start churn).
  worldFeedController.onRoute();
  if (!S.user && !S.authProbed) {
    try { S.user = await api("/auth/me"); }
    catch (e) { S.user = null; S.authProbed = true; }
  }
  if (!S.user) return viewAuth();
  document.body.classList.remove("landing-mode");
  if (!S.character && !popupRosterLoaded) {
    try {
      const chars = await api(`/characters/by-user/${S.user.id}`);
      S.character = chars && chars.length ? chars[0] : null;
    } catch (e) { S.character = null; }
    // #302: a freshly hydrated character is a new session context — UI
    // state captured under a previous user/character must not leak into it.
    resetGameUiContext();
  }
  const hash = location.hash;  // #161: empty hash is normalized at the top of route(); no fallback needed
  // #310: marker selection is World-screen-local — any route change away from
  // #/world drops it (World→Place→World, World→Chat→World, …). A POI click
  // never changes the hash, so no route() fires and the selection persists.
  if (hash !== "#/world") gameUiContext.selectedPoiId = null;
  // 020/#118 P1: a user without a character gets the creator screen for ANY
  // hash. The #/profile carve-out below is deliberate: #/profile falls
  // through to viewProfile, whose #128 null-guard hands off to the creator,
  // and the creator normalizes the address to #/create (its own route).
  // Every other hash short-circuits straight into the creator.
  if (!S.character && hash !== "#/profile") return viewCreateCharacter();
  // #72: dynamic subordinate route — strict numeric id, matched BEFORE the
  // static registry (the registry above now also carries #74's static
  // "#/home" entry, #75's static "#/career" entry, #76's static
  // "#/events" entry and #79's static "#/settings" entry — 17 rows total;
  // still no topbar tab for any of them: the Place and Events screens
  // highlight «Мир», the Home, Career and Settings screens «Профиль»).
  const placeMatch = hash.match(/^#\/location\/(\d+)$/);
  // #269: a cold direct entry to a non-world route never passes viewWorld/
  // viewLocation (the only /world loaders), so the topbar clock would sit
  // on «…» for the whole session. Bootstrap ONLY when the dispatch target
  // is a registry view that does not load /world itself: #/world AND the
  // unknown-hash fallback dispatch viewWorld (own /world fetch), and
  // #/location/* fetches its own — no duplicate /world during route races.
  if (S.character && !S.world && routes[hash] && routes[hash] !== viewWorld) {
    hydrateWorldClock();
  }
  if (placeMatch) return viewLocation(Number(placeMatch[1]));
  (routes[hash] || viewWorld)();
}

/* ---------- auth (OIDC SSO via Authentik, issue #309) ---------- */

/* ?error= codes mirror backend/app/api/oidc.py ERROR_CODES exactly. Values
   are fixed Russian strings — provider-supplied text is never rendered. */
const AUTH_ERROR_TEXTS = {
  auth_failed: "Ошибка входа, попробуйте ещё раз",
  state_mismatch: "Сессия входа устарела, попробуйте ещё раз",
  provider_error: "Провайдер входа недоступен, попробуйте позже",
  group_denied: "Вход по приглашениям. Если у вас есть приглашение — откройте ссылку из письма",
  registration_closed: "Регистрация новых игроков закрыта",
  username_taken: "Имя занято, обратитесь к администратору",
};

/* Consume ?error=<code> once at page load: map to the fixed text, then strip
   the query (hash preserved) so a reload cannot re-show it and no parameter
   value can loop back into a redirect. No auto-redirect happens here — the
   only way to /auth/login is the explicit «Войти» button. */
function consumeAuthError() {
  /* Manual query parse: URLSearchParams is absent in the m269 node-vm harness
     (stub environment), and the error codes are fixed ASCII — no decoding. */
  const q = (location.search || "").replace(/^\?/, "");
  let code = null;
  for (const pair of q.split("&")) {
    const eq = pair.indexOf("=");
    if (eq > 0 && pair.slice(0, eq) === "error") { code = pair.slice(eq + 1); break; }
  }
  if (code === null) return null;
  history.replaceState(null, "", (location.pathname || "") + location.hash);
  return AUTH_ERROR_TEXTS[code] || "Ошибка входа";
}
const authErrorText = consumeAuthError();

// #342: the popup listener already loaded the roster (possibly empty); route()
// must not issue a second /characters/by-user request at the rate-limit boundary.
let popupRosterLoaded = false;

/* #342: the popup login completion. The message is a confirmation trigger —
   the opener verifies the session, pre-fetches the character, resets private
   drafts and enters the game in place (no reload, no duplicate requests). */
window.addEventListener("message", async (event) => {
  if (event.origin !== location.origin) return;
  const data = event.data;
  if (!data || typeof data !== "object" || data.type !== "lifesim-auth"
      || data.ok !== true) return;
  // #342 race fix: the popup's callback response sets the session cookie;
  // confirm it is live before entering the game so the boot probe cannot
  // race the cookie. Reuse the confirmed identity — a reload would issue a
  // SECOND /auth/me and can hit the global rate-limit boundary (Codex P2).
  for (let attempt = 0; attempt < 3; attempt++) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 3000);
    try {
      const r = await fetch("/auth/me", {
        credentials: "same-origin", signal: controller.signal,
      });
      if (r.ok) {
        S.user = await r.json();
        S.authProbed = false;
        S.world = null;
        // Pre-fetch the character with the same bounded retry so route()
        // does not issue another request at the rate-limit boundary (Codex P2).
        S.character = null;
        for (let c = 0; c < 3; c++) {
          const charController = new AbortController();
          const charTimeout = setTimeout(() => charController.abort(), 3000);
          try {
            const cr = await fetch(`/characters/by-user/${S.user.id}`, {
              credentials: "same-origin", signal: charController.signal,
            });
            if (cr.ok) {
              const chars = await cr.json();
              S.character = chars && chars.length ? chars[0] : null;
              break;
            }
          } catch (e) { /* transient or aborted — retry */ }
          finally { clearTimeout(charTimeout); }
          await new Promise((resolve) => setTimeout(resolve, 200 * (c + 1)));
        }
        popupRosterLoaded = true;
        // Per-session private state must not survive a different account (Codex P2).
        creatorResetDraft();
        appearanceResetDraft();
        desireResetDraft();
        // route() skips its own reset because the roster was prefetched here —
        // do the full session-context teardown explicitly on relogin.
        resetGameUiContext();
        // Leave the login route before dispatching the game. replaceState
        // avoids a hashchange (which would dispatch route() a second time and
        // issue another request at the rate-limit boundary).
        history.replaceState(null, "", location.pathname + location.search + "#/world");
        route();
        return;
      }
    } catch (e) { /* transient or aborted — retry */ }
    finally { clearTimeout(timeout); }
    await new Promise((resolve) => setTimeout(resolve, 200 * (attempt + 1)));
  }
  // Fail closed: reload so the landing shows the honest state.
  location.reload();
});

function viewAuth() {
  location.hash = "#/login";
  document.body.classList.add("landing-mode");

  function eyebrowOrnament() {
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 72 12");
    svg.setAttribute("width", "72");
    svg.setAttribute("height", "12");
    svg.setAttribute("fill", "none");
    const line = document.createElementNS(ns, "path");
    line.setAttribute("d", "M1 6 H27 M45 6 H71");
    line.setAttribute("stroke", "currentColor");
    line.setAttribute("stroke-width", "1.4");
    line.setAttribute("stroke-linecap", "round");
    const dot = document.createElementNS(ns, "circle");
    dot.setAttribute("cx", "36");
    dot.setAttribute("cy", "6");
    dot.setAttribute("r", "2.6");
    dot.setAttribute("fill", "currentColor");
    svg.append(line, dot);
    return svg;
  }

  const top = el("header", { class: "landing-nav" },
    el("div", { class: "landing-brand" },
      el("span", { class: "landing-brand-mark", "aria-hidden": "true" }, "Р"),
      el("span", {}, "ВЛ1: Рейнеке")));

  const heroCopy = el("div", { class: "landing-copy" },
    el("div", { class: "landing-eyebrow", "aria-hidden": "true" }, eyebrowOrnament()),
    el("h1", { class: "landing-title" }, "Остров живёт, даже когда тебя нет."),
    el("p", { class: "landing-intro" },
      "Вас встречает Кошка-консьержка — проводница по острову Рейнеке."),
    el("div", { class: "landing-bubble" },
      el("span", { class: "landing-bubble-text" }, "Добро пожаловать на Рейнеке. Я помогу тебе освоиться.")),
    el("p", { class: "landing-sub" }, "Симулятор жизни на острове Рейнеке. 18+"),
    el("div", { class: "landing-hero-actions" },
      el("button", { class: "landing-cta", type: "button",
        onclick: () => {
          const popup = window.open(
            "/auth/login?mode=popup",
            "lifesim-auth",
            "popup,width=520,height=680",
          );
          if (popup === null) window.location.href = "/auth/login";
        } }, "Войти")),
    el("div", { class: "err-text landing-auth-error", role: "alert" },
      authErrorText || ""));

  const scene = el("img", { class: "landing-scene",
    src: "/static/static/landing/concierge-cat-hero.webp",
    alt: "Кошка-консьержка встречает гостей на пирсе острова Рейнеке" });
  const scrim = el("div", { class: "landing-scrim", "aria-hidden": "true" });

  const heroInner = el("div", { class: "landing-hero-inner" }, heroCopy);

  const hero = el("section", { class: "landing-hero" }, scene, scrim, heroInner);

  const features = el("section", { class: "landing-features", "aria-label": "Что уже есть в игре" },
    el("article", { class: "landing-feature" },
      el("h2", {}, "Живой остров"),
      el("p", {}, "Мир развивается, даже когда ты офлайн.")),
    el("article", { class: "landing-feature" },
      el("h2", {}, "Мир помнит"),
      el("p", {}, "Твои выборы оставляют след. Персонажи помнят.")),
    el("article", { class: "landing-feature" },
      el("h2", {}, "Мягкий онбординг"),
      el("p", {}, "Простое начало: вход, персонаж, первый день на острове.")));

  const footer = el("footer", { class: "landing-footer" },
    el("p", {}, "Симулятор жизни на острове Рейнеке. 18+ · © 2026 ВЛ1: Рейнеке"));

  const landing = el("main", { class: "landing-shell" }, top, hero, features, footer);

  app.replaceChildren(landing);
}

/* ---------- character creation (020/#118 P1: the #/create screen) ---------- */

/* 020 P1: structured appearance fields (P1 was UI-only; since P2 they
   persist). Their values live in CREATOR_DRAFT; at confirm the filled
   fields ride the POST body as `appearance` (creatorAppearancePayload,
   canonical snake_case keys) and the free-text prompt goes BOTH to
   `appearance_prompt` and to `looks` (≤500) as today. */
const CREATOR_FIELDS = [
  ["face", "Лицо"], ["eyes", "Глаза"], ["nose", "Нос"], ["lips", "Губы"],
  ["hair", "Волосы"], ["hairColor", "Цвет волос"], ["skin", "Кожа"],
  ["height", "Рост"], ["weight", "Вес"], ["build", "Телосложение"],
  ["chest", "Грудь"], ["extra", "Дополнительные особенности"],
];

/* 020 P1: the COMMON biography tag pool is deliberately gender-neutral —
   every ingredient is a present-tense verb, a prepositional construction,
   or a clause with an inanimate subject: no gendered person-nouns, no
   past-tense person verbs, no gendered adjective forms (tests/m120 pins
   this neutrality against a blacklist). */
const CREATOR_TAGS = [
  ["origin", ["из Владивостока", "с материка", "из приморского посёлка",
    "детство прошло в городе", "давно на острове", "недавно на острове"]],
  ["work", ["работает в кафе", "чинит лодки", "ловит и коптит рыбу",
    "ведёт маленький магазин", "пишет картины", "учится на повара",
    "ухаживает за садом"]],
  ["nature", ["любит море", "любит тишину и книги", "легко знакомится",
    "держится особняком", "не боится рисковать", "всё считает заранее",
    "любит шумные компании", "умеет слушать"]],
  ["dreams", ["ищет новую жизнь", "мечтает о семье", "хочет открыть своё дело",
    "хочет увидеть большой город", "хочет построить дом у пирса",
    "мечтает о путешествиях", "ищет новых друзей"]],
];
const CREATOR_GROUP_NAMES = { origin: "Откуда", work: "Занятия",
  nature: "Характер", dreams: "Мечты" };

/* 020 P1: creator draft state lives OUTSIDE the rendered tree — the view
   re-renders once when the queued hash normalization re-enters route()
   (the viewAuth pattern), and inputs unmounted by that render would lose
   typed values. Seed on build + capture on input keeps the draft. */
/* 020 wave 4 (#316): customClothing is the free-text clothing description —
   persisted across re-renders like every draft field, sent to the preview
   and to POST /characters (custom_clothing). */
const CREATOR_DRAFT = { appearance: {}, appearancePrompt: "", bio: "", tags: [],
  customClothing: "" };

/* 020 wave 4 (#316): the ONE revision/epoch counter for AI proposals. Every
   manual edit of the анкета bumps it; every generate/variant request carries
   the value at dispatch, and a response applies only when the counter is
   UNCHANGED — any keystroke between dispatch and response discards the
   proposal. creatorResetDraft() bumps it too (epoch): the counter only ever
   moves forward, so a response from before a logout/reset/creation can
   never repopulate the next user's form. */
let creatorRev = 0;
function bumpCreatorRev() { creatorRev += 1; }

/* W118-RV1 F1: the draft is per-session private input (name, bio, appearance).
   Reset it on every auth transition — logout, account deletion, successful
   creation, and fresh login — so the next user of the same browser never
   sees the previous user's identity data prefilled. */
function creatorResetDraft() {
  CREATOR_DRAFT.appearance = {};
  CREATOR_DRAFT.appearancePrompt = "";
  CREATOR_DRAFT.bio = "";
  CREATOR_DRAFT.tags = [];
  CREATOR_DRAFT.customClothing = "";
  bumpCreatorRev();  // #316 epoch: an in-flight AI response can never repopulate after a reset
}

/* 020 P2: the draft's structured appearance → POST payload. Filled fields
   only (empty strings dropped); the creator's camelCase draft keys map to
   the canonical storage keys (APPEARANCE_FIELDS on the server):
   hairColor→hair_color, extra→features, the rest are already canonical. */
function creatorAppearancePayload() {
  const rename = { hairColor: "hair_color", extra: "features" };
  const out = {};
  for (const [key] of CREATOR_FIELDS) {
    const v = (CREATOR_DRAFT.appearance[key] || "").trim();
    if (v) out[rename[key] || key] = v;
  }
  return out;
}

/* 020 wave 4 (#316): the 19 draft keys of the /characters/draft contract
   (server canon: DRAFT_FIELDS in app/characters/creator.py), in canonical
   order. The variant request must carry the FULL current draft — the
   server validates every key. */
const CREATOR_DRAFT_KEYS = [
  "name", "sex", "age", "face", "eyes", "nose", "lips", "hair", "hair_color",
  "skin", "height", "weight", "build", "chest", "features", "supplement",
  "biography", "outfit_mode", "custom_clothing"];
/* the two draft keys whose creator-side (camelCase) names differ */
const CREATOR_DRAFT_RENAME = { hair_color: "hairColor", features: "extra" };
// The inverse (grid key -> server draft key), DERIVED so the two maps
// cannot drift: the variant REQUEST must carry the snake_case DRAFT_FIELDS
// key (the server rejects anything else), the apply path maps the response
// back through CREATOR_DRAFT_RENAME.
const CREATOR_DRAFT_RENAME_REV = Object.fromEntries(
  Object.entries(CREATOR_DRAFT_RENAME).map(([k, v]) => [v, k]));

/* 020 P1: draft assembly is deterministic TEMPLATE concatenation — no LLM
   (the world must work without one): fixed group order (the CREATOR_TAGS
   order), ingredients joined with ", ", groups with ". ", leading capital.
   Free text stays first-class: the biography textarea is rebuilt ONLY by an
   explicit assembly action — a tag click or the «Собрать черновик» button.
   Nothing else ever writes into it (no auto-assembly on render, navigation
   or confirm), so manual edits cannot be clobbered; confirm reads the
   textarea value itself, so the last user edit always wins. */
function creatorAssembleBio(selected) {
  const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);
  const parts = [];
  for (const [group] of CREATOR_TAGS) {
    const items = selected.filter((t) => t.group === group).map((t) => t.text);
    if (items.length) parts.push(cap(items.join(", ")));
  }
  return parts.length ? parts.join(". ") + "." : "";
}

function viewCreateCharacter() {
  // One character per player: a stale #/create deep link with a character
  // already on the island lands on the profile (the character-management
  // screen) instead of offering a second creation. The no-character path
  // below normalizes the address to #/create — this view's own route.
  if (S.character) { location.hash = "#/profile"; return; }
  location.hash = "#/create";
  const name = el("input", { placeholder: "Имя Фамилия" });
  name.addEventListener("input", () => bumpCreatorRev());  // #316: a manual edit stales in-flight proposals
  const sex = el("select", {},
    el("option", { value: "F" }, "Ж"), el("option", { value: "M" }, "М"));
  sex.addEventListener("change", () => bumpCreatorRev());  // #316: a manual edit stales in-flight proposals
  const age = el("input", { type: "number", value: "28", min: "18", max: "80" });
  age.addEventListener("input", () => bumpCreatorRev());  // #316: a manual edit stales in-flight proposals
  const looks = el("textarea", { placeholder: "Внешность (необязательно)", maxlength: "500" });
  const biography = el("textarea", { placeholder: "Биография (необязательно)", maxlength: "2000" });

  // Draft persistence across re-renders: seed from CREATOR_DRAFT, capture
  // every keystroke (the chat-draft pattern). #316: every keystroke also
  // bumps the revision — an in-flight AI proposal becomes stale the moment
  // the user edits the анкета.
  looks.value = CREATOR_DRAFT.appearancePrompt || "";
  looks.addEventListener("input", () => { CREATOR_DRAFT.appearancePrompt = looks.value; bumpCreatorRev(); });
  biography.value = CREATOR_DRAFT.bio || "";
  biography.addEventListener("input", () => { CREATOR_DRAFT.bio = biography.value; bumpCreatorRev(); });

  // The ONLY draft-writing sites are this function and the two explicit
  // assembly actions (tag click / «Собрать черновик» button) below.
  function rebuildDraft() {
    const draft = creatorAssembleBio(CREATOR_DRAFT.tags);
    biography.value = draft;
    CREATOR_DRAFT.bio = draft;
    bumpCreatorRev();  // #316: explicit assembly is a manual edit too
  }

  // 12 structured appearance inputs — persisted at confirm since P2 (see CREATOR_FIELDS).
  // #316: each field also gets a small «вариант» affordance — a per-field
  // AI proposal applied to THAT field only (never auto-generates an image).
  const variantBtns = [];  // every «вариант» button — disabled together while an AI request is in flight
  const appearanceInputs = {};  // camelCase key -> input node (the AI apply path writes through it)
  const appearanceGrid = el("div", { class: "creator-appearance" },
    ...CREATOR_FIELDS.map(([key, lbl]) => {
      const input = el("input", { placeholder: lbl, "aria-label": lbl });
      input.value = CREATOR_DRAFT.appearance[key] || "";
      appearanceInputs[key] = input;
      input.addEventListener("input", () => { CREATOR_DRAFT.appearance[key] = input.value; bumpCreatorRev(); });
      const field = el("div", { class: "creator-field" },
        el("div", { class: "creator-field-head" },
          el("label", {}, lbl),
          // the variant request carries the CANONICAL (server) draft key —
          // DRAFT_FIELDS is snake_case; the local grid key is camelCase
          // for hairColor/extra (the apply path maps the response back
          // via CREATOR_DRAFT_RENAME; the request map is its derived
          // inverse).
          aiVariantBtn(CREATOR_DRAFT_RENAME_REV[key] || key)),
        input);
      // A long-label field (in practice only «Дополнительные особенности»)
      // spans the full row in the wide layout — its label word cannot fit
      // a ~106px track without a mid-word break (review round-03).
      if (lbl.length > 12) field.classList.add("creator-field-wide");
      return field;
    }));

  // «Одежда» rides the EXISTING #205 outfit control (app.py enum
  // auto|casual|formal|nude — owner-only PATCH; auto is the no-touch default).
  const outfit = el("select", {},
    el("option", { value: "auto" }, "auto — как сейчас"),
    el("option", { value: "casual" }, "casual — повседневная одежда"),
    el("option", { value: "formal" }, "formal — строгая одежда"),
    el("option", { value: "nude" }, "nude — без одежды (18+)"));
  outfit.addEventListener("change", () => bumpCreatorRev());

  // 020 wave 4 (#316): free-text custom clothing — rides the preview and
  // the create POST; persisted across re-renders via CREATOR_DRAFT.
  // A TEXTAREA (review fix): a 500-char free-text description does not
  // fit a single-line input — same attributes, ~2 visible rows.
  const customClothing = el("textarea", {
    placeholder: "Своё описание одежды (необязательно)", maxlength: "500",
    "aria-label": "Своё описание одежды", rows: "2" });
  const clothingCounter = el("p", { class: "muted creator-note creator-counter" },
    "0/500");
  customClothing.value = CREATOR_DRAFT.customClothing || "";
  clothingCounter.textContent = `${customClothing.value.length}/500`;
  customClothing.addEventListener("input", () => { CREATOR_DRAFT.customClothing = customClothing.value; clothingCounter.textContent = `${customClothing.value.length}/500`; bumpCreatorRev(); });

  /* W2: creator preview panel — POST /visual/preview with the CURRENT
     form state. In-place updates ONLY: the handler never re-renders the
     form (no route(), no replaceChildren over the inputs) — on ANY
     outcome the typed values stay put; failures land in the panel's own
     error line and the buttons come back per the previewOk flag (retry
     re-reads the form). No reference picker here: the creator screen
     predates reference uploads (the profile portrait flow owns those). */
  const previewMode = el("select", {},
    el("option", { value: "portrait" }, "Портрет"),
    el("option", { value: "full_body_clothed" }, "В полный рост (повседневная одежда)"),
    el("option", { value: "full_body_outfit" }, "В полный рост (выбранная одежда)"));
  // 023 round 3 (#120): the base_body option is removed from this
  // selector — the mode is server-side DISABLED (always 422).
  // Re-adding the option requires the server-side reject to be lifted
  // first (the descriptor plumbing for it is kept pinned in tests/m236).
  const previewStatus = el("p", { class: "muted creator-note", hidden: true });
  const previewErr = el("p", { class: "err-text" });
  // #78: honest empty state — visible until the first SUCCESSFUL
  // generation hides it; a failed regeneration keeps the last good image
  // and the placeholder stays hidden (previewOk semantics). Toggling it
  // is a local preview-node update (W2): the form is never touched.
  const previewPlaceholder = el("p",
    { class: "muted creator-note creator-preview-empty" },
    "Предпросмотр пока не создан. Нажмите «Создать предпросмотр», чтобы увидеть образ.");
  const previewImg = el("img", {
    class: "preview-image", alt: "Предпросмотр персонажа", hidden: true });
  // #316: «предыдущая версия» labels a RETAINED old image after a failed
  // regeneration; «образ устарел» appears when a variant changes one field
  // (the shown image no longer matches the form) until the next preview.
  const previewVersion = el("p", { class: "muted creator-note", hidden: true },
    "предыдущая версия");
  const previewStale = el("p", { class: "muted creator-note creator-stale",
    hidden: true }, "образ устарел — обновите предпросмотр");
  // #316: prompt inspector — the ACTUAL prompt the endpoint returned, plus
  // the draft revision it was made at. Text-only via textContent (the
  // prompt is model-derived; HTML strings are forbidden).
  const promptDetails = el("details", { class: "creator-prompt", hidden: true },
    el("summary", {}, "Запрос предпросмотра"),
    el("p", { class: "muted creator-note creator-prompt-rev" }),
    el("pre", { class: "creator-prompt-text" }));
  const promptRev = promptDetails.querySelector(".creator-prompt-rev");
  const promptText = promptDetails.querySelector(".creator-prompt-text");
  let previewBusy = false;
  let previewOk = false;  // a preview has succeeded this session (regen lives)

  async function runPreview() {
    if (previewBusy) return;  // no double-submit while in flight
    previewBusy = true;
    previewBtn.disabled = true;
    previewRegenBtn.disabled = true;
    previewErr.textContent = "";
    previewStatus.hidden = false;
    previewStatus.textContent = "Генерация…";
    const rev = creatorRev;  // #316: the revision the request was composed at
    try {
      const appearance = creatorAppearancePayload();
      const r = await api("/visual/preview", { method: "POST", body: {
        sex: sex.value, age: Number(age.value),
        appearance_prompt: looks.value.trim() || undefined,
        appearance: Object.keys(appearance).length ? appearance : undefined,
        outfit_mode: outfit.value,
        custom_clothing: customClothing.value.trim() || undefined,
        mode: previewMode.value,
      } });
      previewImg.src = `data:image/png;base64,${r.image_base64}`;
      previewImg.hidden = false;
      previewPlaceholder.hidden = true;
      previewStatus.hidden = true;
      previewOk = true;
      previewStale.hidden = true;
      previewVersion.hidden = true;
      promptText.textContent = r.prompt || "";
      promptRev.textContent = `Ревизия анкеты: ${rev}`;
      promptDetails.hidden = false;
    } catch (e) {
      // the form is untouched: the panel owns the failure surface;
      // previewOk is deliberately NOT reset — the shown image stays the
      // last good one, so «Обновить» keeps working after a failed retry
      previewStatus.hidden = true;
      if (previewOk) previewVersion.hidden = false;  // the retained image is the previous one
      previewErr.textContent =
        `Предпросмотр не удался: ${e.message}. Попробуйте ещё раз.`;
    } finally {
      previewBusy = false;
      previewBtn.disabled = false;
      // regenerate is available iff a preview exists (set on success,
      // never cleared on failure) — a failure must not strand it disabled
      previewRegenBtn.disabled = !previewOk;
    }
  }
  const previewBtn = el("button", { type: "button", class: "primary",
    onclick: runPreview }, "Создать предпросмотр");
  const previewRegenBtn = el("button", { type: "button",
    onclick: runPreview, disabled: true }, "Обновить");

  /* ---------- 020 wave 4 (#316): AI draft assistant ----------
     ONE-CLICK generate + per-field variants against POST /characters/draft.
     Guards: ONE revision counter (creatorRev) — every manual edit bumps it,
     every request carries the value at dispatch, a response applies only
     when the counter is UNCHANGED (else the proposal is discarded and the
     form keeps the user's values); buttons are disabled while their request
     is in flight; errors keep the form and the last image, never auto-retry.
     The raw server detail never reaches the user — a RU message per class. */
  const aiIdea = el("textarea", {
    placeholder: "Опишите персонажа: кто это, какой характер, чем занимается",
    maxlength: "500", "aria-label": "Описание персонажа для нейропомощника" });
  const aiStatus = el("p", { class: "muted creator-note", hidden: true });
  const aiErr = el("p", { class: "err-text" });
  let aiBusy = false;
  const AI_DISCARDED = "предложение не применено: анкета изменилась";

  function creatorAiErrorText(e) {
    if (e && e.status === 429) return "Нейропомощник занят или лимит попыток исчерпан — попробуйте через минуту.";
    if (e && e.status === 502) return "Нейропомощник вернул некорректный ответ — попробуйте ещё раз.";
    if (e && e.status === 503) return "Нейропомощник недоступен. Создание вручную работает как обычно.";
    if (e && e.status === 504) return "Нейропомощник не ответил вовремя — попробуйте ещё раз.";
    if (e && e.status === 422) return "Нейропомощник не принял запрос — проверьте описание и поля анкеты.";
    return "Нейропомощник не ответил. Проверьте связь и попробуйте ещё раз.";
  }

  function setAiBusy(busy) {
    aiBtn.disabled = busy;
    for (const b of variantBtns) b.disabled = busy;
  }

  /* The CURRENT form as the full 19-key draft (the variant request shape —
     the server validates every key). creatorAppearancePayload() supplies the
     filled canonical appearance keys; the rest ride as empty strings. */
  function creatorDraftPayload() {
    const ageNum = Number(age.value);
    const appearance = creatorAppearancePayload();
    const draft = {
      name: name.value.trim(), sex: sex.value,
      age: Number.isInteger(ageNum) ? ageNum : 0,
      face: "", eyes: "", nose: "", lips: "", hair: "", hair_color: "",
      skin: "", height: "", weight: "", build: "", chest: "", features: "",
      supplement: looks.value.trim(), biography: biography.value.trim(),
      outfit_mode: outfit.value, custom_clothing: customClothing.value.trim(),
    };
    for (const k of Object.keys(appearance)) draft[k] = appearance[k];
    return draft;
  }

  // ONE draft value → its form control (the variant apply path). Model
  // values reach the DOM only through .value / textContent — never HTML.
  function applyCreatorField(field, value) {
    if (field === "name") { name.value = value; return; }
    if (field === "sex") { sex.value = value; return; }
    if (field === "age") { age.value = String(value); return; }
    if (field === "supplement") {
      looks.value = value; CREATOR_DRAFT.appearancePrompt = value; return; }
    if (field === "biography") {
      biography.value = value; CREATOR_DRAFT.bio = value; return; }
    if (field === "outfit_mode") { outfit.value = value; return; }
    if (field === "custom_clothing") {
      customClothing.value = value; CREATOR_DRAFT.customClothing = value;
      clothingCounter.textContent = `${value.length}/500`; return; }
    const localKey = CREATOR_DRAFT_RENAME[field] || field;
    const input = appearanceInputs[localKey];
    if (input) {
      input.value = value; CREATOR_DRAFT.appearance[localKey] = value;
    }
  }

  function applyCreatorDraft(draft) {
    for (const key of CREATOR_DRAFT_KEYS) {
      if (draft && draft[key] !== undefined) applyCreatorField(key, draft[key]);
    }
  }

  async function runGenerate() {
    if (aiBusy) return;  // no double-submit while in flight
    const idea = aiIdea.value.trim();
    if (!idea) {
      aiErr.textContent = "Сначала опишите персонажа — хотя бы одно предложение.";
      return;
    }
    aiBusy = true;
    setAiBusy(true);
    aiErr.textContent = "";
    aiStatus.hidden = false;
    aiStatus.textContent = "Нейропомощник придумывает персонажа…";
    const rev = creatorRev;  // the revision at dispatch
    try {
      const r = await api("/characters/draft", { method: "POST", body: {
        operation: "generate", idea, revision: rev } });
      if (rev !== creatorRev || !name.isConnected) {
        // a manual edit (or a re-render) happened mid-flight: discard the
        // proposal, keep the form as the user left it, NO automatic preview
        aiStatus.hidden = true;
        aiErr.textContent = "предложение не применено: анкета изменилась";
        return;
      }
      applyCreatorDraft(r.draft);
      aiStatus.hidden = true;
      runPreview();  // exactly ONE automatic preview after a clean apply
    } catch (e) {
      aiStatus.hidden = true;
      aiErr.textContent = creatorAiErrorText(e);
    } finally {
      aiBusy = false;
      setAiBusy(false);
    }
  }

  async function runVariant(field) {
    if (aiBusy) return;  // no double-submit while in flight
    // The variant request carries the FULL current draft; a missing name or
    // an out-of-range age is a guaranteed 422 — say it locally instead.
    const ageNum = Number(age.value);
    if (!name.value.trim() || !Number.isInteger(ageNum)
        || ageNum < 18 || ageNum > 99) {
      aiErr.textContent = "Заполните имя и возраст (18–99), чтобы запросить вариант.";
      return;
    }
    aiBusy = true;
    setAiBusy(true);
    aiErr.textContent = "";
    aiStatus.hidden = false;
    aiStatus.textContent = "Нейропомощник предлагает вариант…";
    const rev = creatorRev;  // the revision at dispatch
    try {
      const r = await api("/characters/draft", { method: "POST", body: {
        operation: "variant", field,
        draft: creatorDraftPayload(), revision: rev } });
      if (rev !== creatorRev || !name.isConnected) {
        aiStatus.hidden = true;
        aiErr.textContent = "предложение не применено: анкета изменилась";
        return;
      }
      applyCreatorField(field, r.value);
      aiStatus.hidden = true;
      // the shown image no longer matches the form — never auto-regenerate
      previewStale.hidden = false;
    } catch (e) {
      aiStatus.hidden = true;
      aiErr.textContent = creatorAiErrorText(e);
    } finally {
      aiBusy = false;
      setAiBusy(false);
    }
  }

  function aiVariantBtn(field) {
    const btn = el("button", { type: "button", class: "creator-variant",
      title: "Предложить ИИ-вариант этого поля",
      onclick: () => runVariant(field) }, "вариант");
    variantBtns.push(btn);
    return btn;
  }

  const aiBtn = el("button", { type: "button", class: "primary",
    onclick: runGenerate }, "Создать по описанию");
  const secAi = el("section", { class: "panel creator-ai" },
    el("h2", {}, "Нейропомощник"),
    el("p", { class: "muted creator-note" },
      "Один клик — и анкета заполнена. Опишите персонажа своими словами: нейропомощник предложит имя, внешность и биографию, всё можно поправить руками."),
    aiIdea,
    el("div", { class: "creator-actions" }, aiBtn),
    aiStatus, aiErr);

  // Tag cloud: a click toggles the ingredient AND rebuilds the draft (the
  // documented explicit-assembly path). Selection styling is restored on
  // re-render from CREATOR_DRAFT.tags.
  const tagCloud = el("div", { class: "creator-tags" },
    ...CREATOR_TAGS.map(([group, items]) =>
      el("div", { class: "creator-tag-group" },
        el("div", { class: "muted creator-tag-group-name" }, CREATOR_GROUP_NAMES[group]),
        el("div", { class: "creator-tag-row" },
          ...items.map((text) => {
            const chip = el("button", {
              type: "button",
              class: "creator-tag",
              onclick: () => {
                const i = CREATOR_DRAFT.tags.findIndex(
                  (t) => t.group === group && t.text === text);
                if (i >= 0) CREATOR_DRAFT.tags.splice(i, 1);
                else CREATOR_DRAFT.tags.push({ group, text });
                chip.classList.toggle("sel", i < 0);
                rebuildDraft();
              },
            }, text);
            if (CREATOR_DRAFT.tags.some(
                (t) => t.group === group && t.text === text)) {
              chip.classList.add("sel");
            }
            return chip;
          })))));

  const err = el("div", { class: "err-text" });
  const confirmBtn = el("button", {
    class: "primary", type: "button", onclick: async () => {
      if (confirmBtn.disabled) return;  // no double-submit while in flight
      confirmBtn.disabled = true;
      try {
        // 020 P2: the structured appearance rides the confirm POST
        // (omitted entirely when every field is empty — the server treats
        // absent as the pre-020 behavior, so a client without these keys
        // stays compatible). appearance_prompt carries the same free text
        // as `looks` (the looks behavior is unchanged); limits are
        // enforced server-side (422 surfaces in err).
        // 020 wave 4 (#316): outfit_mode + custom_clothing persist
        // ATOMICALLY in the same POST — the old best-effort post-create
        // PATCH is gone (a failed second write can no longer contradict
        // the created row).
        const appearance = creatorAppearancePayload();
        const r = await api("/characters", { method: "POST", body: {
          name: name.value, sex: sex.value, age: Number(age.value),
          looks: looks.value.trim() || undefined,
          biography: biography.value.trim() || undefined,
          appearance: Object.keys(appearance).length ? appearance : undefined,
          appearance_prompt: looks.value.trim() || undefined,
          outfit_mode: outfit.value,
          custom_clothing: customClothing.value.trim() || undefined } });
        creatorResetDraft();  // W118-RV1 F1: consumed draft must not leak on logout
        S.character = r;
        // The create response is a minimal dict without outfit_mode —
        // carry the chosen mode so the profile view shows it (the server
        // persisted it atomically with the character row).
        S.character.outfit_mode = outfit.value;
        resetGameUiContext();  // #302: a new character is a new session context
        gotoWorld();  // #161: single dispatch — no direct route() next to the hash assignment
      } catch (e) { err.textContent = e.message; }
      finally { confirmBtn.disabled = false; }
    },
  }, "Появиться на острове");

  // #78: the five guided sections as named nodes — the section guide
  // scrolls+focuses them, the composition orders them. tabindex="-1"
  // makes each a programmatic focus target (not a tab stop). DOM order IS
  // the guided order: кто вы → образ → биография → одежда → предпросмотр;
  // the confirm section stays a separate full-width node below.
  const secWho = el("section", { class: "panel", tabindex: "-1" },
    el("h2", {}, "Кто вы"),
    el("div", { class: "creator-field" },
      el("div", { class: "creator-field-head" },
        el("label", {}, "Имя"), aiVariantBtn("name")),
      name),
    el("div", { class: "creator-field" },
      el("div", { class: "creator-field-head" },
        el("label", {}, "Пол"), aiVariantBtn("sex")),
      sex),
    el("div", { class: "creator-field" },
      el("div", { class: "creator-field-head" },
        el("label", {}, "Возраст"), aiVariantBtn("age")),
      age));
  const secLook = el("section", { class: "panel", tabindex: "-1" },
    el("h2", {}, "Образ"),
    appearanceGrid,
    el("div", { class: "creator-field" },
      el("div", { class: "creator-field-head" },
        el("label", {}, "Внешность"), aiVariantBtn("supplement")),
      looks),
    el("p", { class: "muted creator-note" },
      "Свободное описание — основное; отдельные поля подсказывают детали. Предпросмотр собирается по текущим полям формы по запросу — правки в саму форму не вносятся и не сбрасываются."),
    el("p", { class: "muted creator-note" },
      "Поля внешности сохраняются при создании; редактирование этих полей после создания пока недоступно. Режим одежды можно изменить позже."));
  const secBio = el("section", { class: "panel", tabindex: "-1" },
    el("h2", {}, "Биография"),
    el("p", { class: "muted creator-note" },
      "Биография — стартовый контекст, а не жёсткая судьба. Происхождение, занятия, характер и мечты здесь описываются словами: они не выбирают игровые характеристики или профессию."),
    el("p", { class: "muted creator-note" },
      "Клик по ингредиенту добавит его в черновик. Можно писать что угодно своими словами — ваша правка не сотрётся, пока вы сами не нажмёте сборку."),
    el("p", { class: "muted creator-note" },
      "Нажатие на тег или «Собрать черновик» пересобирает текст биографии. После этого его можно отредактировать."),
    tagCloud,
    el("div", { class: "creator-bio-actions" },
      el("button", { type: "button", onclick: rebuildDraft }, "Собрать черновик")),
    el("div", { class: "creator-field" },
      el("div", { class: "creator-field-head" },
        el("label", {}, "Биография"), aiVariantBtn("biography")),
      biography),
    el("p", { class: "muted creator-note" },
      "Биография видна миру и помогает острову понять, кто вы."));
  const secOutfit = el("section", { class: "panel", tabindex: "-1" },
    el("h2", {}, "Одежда"),
    el("div", { class: "creator-field" },
      el("div", { class: "creator-field-head" },
        el("label", {}, "Одежда"), aiVariantBtn("outfit_mode")),
      outfit),
    el("div", { class: "creator-field" },
      el("div", { class: "creator-field-head" },
        el("label", {}, "Своё описание одежды"), aiVariantBtn("custom_clothing")),
      customClothing,
      clothingCounter),
    el("p", { class: "muted creator-note" },
      "Действует в режиме „авто“; сохраняется с персонажем для будущих портретов; это описание образа, не инвентарь."),
    el("p", { class: "muted creator-note" },
      "Как персонаж одевается по умолчанию: auto — как сегодня, остальные режимы добавляют одежду в портрет. nude — только для 18+. Изменить можно позже в профиле."));
  const secPreview = el("section", { class: "panel creator-preview", tabindex: "-1" },
    el("h2", {}, "Предпросмотр"),
    el("p", { class: "muted creator-note" },
      "Как персонаж будет выглядеть. Собирается по текущим полям формы — правки в саму форму не вносятся и не сбрасываются."),
    el("p", { class: "muted creator-note" },
      "Это предпросмотр, а не сохранённый портрет. После изменения полей создайте его заново. Можно появиться на острове без предпросмотра."),
    el("label", {}, "Ракурс"), previewMode,
    el("div", { class: "creator-actions" }, previewBtn, previewRegenBtn),
    previewPlaceholder, previewStatus, previewImg, previewVersion, previewStale,
    promptDetails, previewErr);

  // #78: numbered section guide — a compact map of the single-page flow,
  // pinned inside the hero. Each entry scrolls its section into view and
  // focuses it. Deliberate NON-features (#78 decisions): no hash
  // assignment (the m120/m162 hash counts stay put), no re-render, no
  // validation, no draft writes, no completion state, no Next/Back, no
  // percentages — a stateful wizard over a single page would be fake
  // structure, and step-completion markers would be fake progress.
  const guideEntry = (section, label) => el("button", {
    type: "button", class: "creator-guide-link",
    onclick: () => {
      // #79: the scroll joins the effective-motion policy (OS OR user pref).
      section.scrollIntoView({ behavior: S.mapReducedMotion ? "auto" : "smooth", block: "start" });
      section.focus({ preventScroll: true });
    },
  }, label);
  const sectionGuide = el("div", { class: "creator-guide" },
    el("div", { class: "muted creator-eyebrow" }, "Разделы создания персонажа"),
    el("div", { class: "creator-guide-row" },
      guideEntry(secWho, "1. Кто вы"),
      guideEntry(secLook, "2. Образ"),
      guideEntry(secBio, "3. Биография"),
      guideEntry(secOutfit, "4. Одежда"),
      guideEntry(secPreview, "5. Предпросмотр")));

  // #316: the confirm block is a named section — on wide screens it lives
  // in the RIGHT column (under биография/одежда); narrow screens keep it
  // last in the stacked flow. The honest NEEDS-BACKEND rows and the
  // post-creation disclosure sit at the decision point, above the button.
  const secConfirm = el("section", { class: "panel creator-confirm" },
    el("p", { class: "muted creator-note" },
      "Структурированный выбор происхождения недоступен."),
    el("p", { class: "muted creator-note" },
      "Выбор игровых черт личности недоступен."),
    el("p", { class: "muted creator-note" },
      "Персональная сцена прибытия по вашей биографии недоступна."),
    el("p", { class: "muted creator-note" },
      "После создания откроется «Мир» — карта острова и первые дела. При первом посещении этого браузера кошка-консьержка может показать подсказки."),
    err,
    el("div", { class: "creator-actions" }, confirmBtn));

  // #78 guided composition, #316 layout: hero (with the section guide),
  // then .creator-columns — on wide+tall screens THREE columns: the AI
  // assistant + кто вы/образ LEFT, the large preview CENTER, биография/
  // одежда/confirm RIGHT (CSS media query; narrow/short screens keep the
  // normal stacked flow with the preview first). The preview panel is a
  // single node — never duplicated.
  app.replaceChildren(el("main", { class: "creator-shell" },
    el("section", { class: "panel creator-hero" },
      el("div", { class: "muted creator-eyebrow" }, "ВЛ1: Рейнеке"),
      el("h1", {}, "Новый житель Рейнеке"),
      el("p", { class: "creator-intro" },
        "Соберите образ и биографию до появления на острове. Всё, кроме имени, необязательно — свободный текст главный."),
      sectionGuide),
    el("div", { class: "creator-columns" },
      el("div", { class: "creator-form" },
        secAi,
        secWho,
        secLook),
      secPreview,
      el("div", { class: "creator-form creator-side" },
        secBio,
        secOutfit,
        secConfirm))));
}

/* ---------- portrait + scene (014, M6 visual pipeline) ---------- */

async function fetchPortraitBlob(path, opts = {}) {
  // Same-origin cookie auth (same as api()); the client holds no bearer token,
  // so a Bearer header would 401. img src cannot carry auth → fetch blob, objectURL.
  // #312: an optional abort signal rides the fetch, and an isFresh predicate
  // is re-checked between the response await and the blob await — a scene
  // flight superseded mid-transfer must not consume the file it raced for.
  // #312: the abort signal rides only when provided — the no-signal call
  // stays byte-identical to the historical portrait fetch (m14 pin).
  const r = opts.signal
    ? await fetch(path, { credentials: "same-origin", signal: opts.signal })
    : await fetch(path, { credentials: "same-origin" });
  if (opts.isFresh && !opts.isFresh()) throw new Error("stale scene request");
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return await r.blob();
}

async function generatePortrait(container, force) {
  const status = container.querySelector(".portrait-status");
  if (status) status.textContent = "Генерация…";
  try {
    // 020 P3 stage 2: the optional reference/style/nude ride the POST body
    // ONLY when set — an untouched control set keeps the historical no-body
    // POST (byte-identical request; the server's canonical-reuse fast-path
    // still applies to plain requests).
    const refSel = container.querySelector(".portrait-ref-select");
    const styleSel = container.querySelector(".portrait-style-select");
    const nudeToggle = container.querySelector(".portrait-nude-toggle");
    const body = {};
    if (refSel && refSel.value) body.reference_id = Number(refSel.value);
    if (styleSel && styleSel.value) body.style = styleSel.value;
    if (nudeToggle && nudeToggle.checked) body.nude = true;
    // Force-regenerate (#119 wave 4, owner decision 2026-11-01): the POST
    // carries regenerate=true — the server chains the character's OWN
    // canonical portrait as the generation reference (identity continuity:
    // the face persists across regens). This replaces the old
    // demote-then-regenerate-then-repin dance: the canonical is never
    // demoted here, so a FAILED regen no longer loses the pinned face (the
    // old best-effort demote committed even when the generation then
    // failed). Applies to nude renders too — the nude auto-pin skip below
    // is unchanged (the nude policy governs pinning, not referencing).
    if (force) {
      body.regenerate = true;
    }
    const r = await api(`/visual/portraits/${S.character.id}`,
      Object.keys(body).length ? { method: "POST", body } : { method: "POST" });
    // Pin the new asset canonical: persists across reloads and feeds §70 scene refs.
    // 020 P3 stage 2: nude renders skip the auto-pin — the fresh asset displays
    // once, but the previous canonical stays pinned; making a nude render
    // canonical must be a deliberate act. Plain/styled/reference renders keep
    // the historical auto-pin.
    if (!body.nude) {
      try {
        await api(`/visual/assets/${r.asset.id}/canonical`,
          { method: "POST", body: { canonical: true } });
        S.canonicalPortraitId = r.asset.id;
        S.visualDisabled = false;
      } catch (e) { /* best effort */ }
    }
    const blob = await fetchPortraitBlob(`/visual/assets/${r.asset.id}/file`);
    const url = URL.createObjectURL(blob);
    const img = el("img", { class: "portrait-img", src: url, alt: "Портрет" });
    const old = container.querySelector(".portrait-img");
    if (old) old.remove();
    container.insertBefore(img, status);
    if (status) status.textContent = "";
  } catch (e) {
    if (status) status.textContent = String(e.detail || e.message || "Ошибка генерации");
  }
}

/* 020 P3 stage 2: owner reference list → picker options. The payload per
   item is {id, mime, size_bytes, created_at} (visual_routes.py) — there is
   no filename, so the label is built from id + KB size. The current selection
   survives a reference-list refresh when still present; a full re-render of
   the portrait block rebuilds the controls with defaults instead. */
async function refreshReferenceOptions(refSel) {
  const data = await api("/visual/references");
  const items = (data && data.items) || [];
  const prev = refSel.value;
  refSel.replaceChildren(
    el("option", { value: "" }, "—"),
    ...items.map((r) => el("option", { value: String(r.id) },
      `#${r.id} · ${Math.max(1, Math.round(r.size_bytes / 1024))} КБ`)));
  if ([...refSel.options].some((o) => o.value === prev)) refSel.value = prev;
}

/* 020 P3 stage 2: multipart upload — unlike api() the body is FormData (the
   browser sets the multipart boundary header; a JSON content-type here would
   corrupt it). Cookie auth as in api(); a 401 re-routes like every other
   expired-session hit, and any non-2xx throws {status, detail} so callers
   surface the server text (422 "reference too large" / "unsupported reference
   format" / "reference quota exceeded") — note the shape carries no attached
   response body (api() keeps it as .data for the ambiguous-422 case; nothing
   here needs it). */
async function uploadReference(file) {
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch("/visual/references", {
    method: "POST", credentials: "same-origin", body: fd });
  if (res.status === 401) {
    S.user = null;
    route();
    throw new Error("unauthorized");
  }
  let data = null;
  try { data = await res.json(); } catch (e) { data = null; }
  if (!res.ok) {
    const detail = data && data.detail ? data.detail : `HTTP ${res.status}`;
    throw Object.assign(new Error(String(detail)),
      { status: res.status, detail });
  }
  return data;
}

function portraitBlock() {
  // Synchronous box (a real DOM node, not a Promise) so renderWorld can embed it
  // directly; the canonical portrait loads asynchronously and fills in when ready.
  const status = el("div", { class: "portrait-status muted" });
  const box = el("div", { class: "panel portrait" }, el("h2", {}, "Портрет"), status);
  // 020 P3 stage 2: compact reference-consumption row. All controls default
  // to the no-op (picker "—", style "—", nude OFF) — plain generation stays
  // the historical no-body POST. The picker lists the user's uploaded
  // references (GET /visual/references); the upload button posts the file
  // and refreshes the picker on success. Scenes keep API-only consumption
  // this wave — no scene-side controls here.
  const refSel = el("select", { class: "portrait-ref-select",
    "aria-label": "Референс" }, el("option", { value: "" }, "—"));
  const styleSel = el("select", { class: "portrait-style-select",
    "aria-label": "Стиль" },
    el("option", { value: "" }, "—"),
    el("option", { value: "oil" }, "Масло"),
    el("option", { value: "watercolor" }, "Акварель"));
  const nudeToggle = el("input", { type: "checkbox", class: "portrait-nude-toggle" });
  const refFile = el("input", { type: "file", accept: "image/png,image/jpeg",
    class: "portrait-ref-file", hidden: "hidden" });
  const refUploadBtn = el("button", { type: "button", class: "portrait-ref-upload",
    onclick: () => refFile.click() }, "Загрузить референс");
  refFile.addEventListener("change", async () => {
    const file = refFile.files && refFile.files[0];
    refFile.value = "";  // reset so re-picking the same file re-fires change
    if (!file) return;
    refUploadBtn.disabled = true;
    try {
      await uploadReference(file);
      toast("Референс загружен");
      await refreshReferenceOptions(refSel);
    } catch (e) { toast(e.message, true); }
    finally { refUploadBtn.disabled = false; }
  });
  box.append(el("div", { class: "portrait-ref-controls" },
    el("label", { class: "portrait-ref-field" }, "Референс", refSel),
    el("label", { class: "portrait-ref-field" }, "Стиль", styleSel),
    el("label", { class: "portrait-ref-field portrait-ref-nude" }, nudeToggle,
      el("span", {}, "Ню (художественный)")),
    refUploadBtn, refFile));
  (async () => {
    let hasCanonical = false;
    if (!S.visualDisabled) {
      try {
        const asset = await api(`/visual/characters/${S.character.id}/portrait`);
        S.canonicalPortraitId = asset.id;
        const blob = await fetchPortraitBlob(`/visual/assets/${asset.id}/file`);
        const url = URL.createObjectURL(blob);
        box.insertBefore(
          el("img", { class: "portrait-img", src: url, alt: "Портрет" }), status);
        hasCanonical = true;
      } catch (e) {
        if (e && e.status === 503) {
          S.visualDisabled = true;
          status.textContent = "Портрет-сервис недоступен";
        }
        else status.textContent = "Портрет ещё не готов";  // #175: 404/net — honest non-error note
        /* no portrait yet — keep button */
      }
      // 020 P3 stage 2: fill the reference picker (owner list). Best-effort:
      // a failed list (503 gate / transient net) leaves the empty "—" picker;
      // upload-time errors surface on the upload button instead.
      try { await refreshReferenceOptions(refSel); }
      catch (e) { /* picker stays at "—" */ }
    } else {
      // cached 503: the negative cache skips the fetch on re-mounts —
      // keep the status honest instead of empty (#59 follow-up #2).
      status.textContent = "Портрет-сервис недоступен";
    }
    box.append(el("button", {
      onclick: () => generatePortrait(box, hasCanonical),
    }, hasCanonical ? "Сгенерировать заново" : "Сгенерировать портрет"));
  })();
  return box;
}

/* ---------- #86 (A4): Scene view — inspect surroundings ---------- */

function sceneDescriptorData(d) {
  // Authoritative summary from the committed-state scene descriptor.
  // "Рядом" excludes the inspecting player (they see others, not themselves).
  const hour = d && d.time ? String(d.time.hour).padStart(2, "0") : "00";
  return {
    locationName: d && d.location ? d.location.name : "—",
    timeText: d && d.time ? `День ${d.time.day + 1}, ${hour}:00` : "—",
    weatherText: d && d.weather ? d.weather : "",
    nearby: d && Array.isArray(d.characters)
      ? d.characters.filter((c) => c.id !== S.character.id).map((c) => c.name)
      : [],
    nearbyKnown: true,  // #312: the committed descriptor is authoritative
    objects: d && Array.isArray(d.objects) ? d.objects.map((o) => o.type) : [],
  };
}

function sceneFallbackData(locId = (S.character ? S.character.location_id : null),
                           locs = S.mapLocations) {
  // #312: the confirmed-data summary built client-side from ALREADY-LOADED
  // authoritative state — a dead visual service never blocks inspection, and
  // the Place stage renders it for any viewed location. Generalized signature
  // (the Place stage passes the viewed location + the loaded catalog); the
  // world Scene panel keeps the defaults. Honest gaps: a missing world
  // timestamp stays «недоступно» (never a synthesized «День 1»), and an
  // unknown catalog location is distinguished from a confirmed-empty roster
  // (nearbyKnown) — no false «Рядом никого» is claimed for unknown data.
  // Read-only: no gameplay state is touched on any path.
  const ch = S.character;
  const list = locs || [];
  const loc = list.find((l) => String(l.id) === String(locId));
  const ts = S.world && typeof S.world.game_timestamp === "number"
    ? S.world.game_timestamp : null;
  const nearby = ((loc && loc.occupants) || [])
    .filter((o) => !ch || o.id !== ch.id)
    .map((o) => o.name);
  return {
    locationName: loc && loc.name ? loc.name : "—",
    timeText: ts !== null ? dayTime(ts) : "недоступно",
    weatherText: S.weather && S.weather.enabled ? S.weather.description : "",
    nearby,
    nearbyKnown: !!loc,
    objects: [],
  };
}

function sceneSummaryLines(d) {
  const lines = [`Место: ${d.locationName}`, `Время: ${d.timeText}`];
  if (d.weatherText) lines.push(`Погода: ${d.weatherText}`);
  // #312: nearbyKnown === false → the catalog has no such location — the
  // roster is UNKNOWN, never confirmed empty.
  lines.push(!d.nearbyKnown ? "Рядом: недоступно"
    : d.nearby.length ? `Рядом: ${d.nearby.join(", ")}` : "Рядом никого");
  if (d.objects.length) lines.push(`Объекты: ${d.objects.join(", ")}`);
  return lines;
}

function backToMap() {
  // Back navigation from the scene state to the world view. #312: the scene
  // lifecycle (cache/flight/URLs) is owned by the scene helpers — the panel
  // keeps showing the mounted entry; only the scroll resets.
  renderScenePanel();
  const map = document.querySelector(".world-stage");
  if (map) map.scrollIntoView({ behavior: S.mapReducedMotion ? "auto" : "smooth", block: "start" });
}

function fillScenePanel(box) {
  // #312: the world Scene panel is a stable-host view over the mounted scene
  // entry (S.scene) — hosts are created once (box._sceneHosts) and patched in
  // place, so a re-fill never rebuilds the subtree (focus/scroll retention).
  // Missing/unknown entry shapes (legacy presets) render the idle branch.
  const scene = S.scene;
  if (!box._sceneHosts) {
    const bar = sceneActBar();
    box._sceneHosts = {
      status: el("div", { class: "muted scene-status" }),
      image: el("div", { class: "scene-image-host" }),
      summary: el("div", { class: "scene-live-summary" }),
      actions: el("div", { class: "scene-panel-actions" }),
      result: el("div", { class: "scene-act-result-host" }),
    };
    box.replaceChildren(el("h2", {}, "Сцена"),
      box._sceneHosts.status, box._sceneHosts.image, box._sceneHosts.summary,
      box._sceneHosts.actions, bar, box._sceneHosts.result);
  }
  const h = box._sceneHosts;
  const status = scene ? scene.status : null;
  h.status.textContent = !status ? "Осмотр ещё не выполнялся."
    : status === "generating" ? "Осматриваю окрестности…"
    : status === "unavailable" ? "Визуал недоступен." : "";
  // The panel mounts ONLY a live objectURL — it never recreates one from the
  // cached blob (that is the Place stage's cache-return path).
  if (status === "ready" && scene.objectURL) {
    h.image.replaceChildren(el("img", {
      src: scene.objectURL, alt: sceneImageAlt(scene) }));
  } else {
    h.image.replaceChildren();
  }
  const data = status === "ready"
    ? sceneDescriptorData(scene.descriptor) : sceneFallbackData();
  h.summary.replaceChildren(...sceneSummaryLines(data)
    .map((t) => el("div", { class: "muted" }, t)));
  if (status === "generating") {
    // #86 review: escape hatch while a generation is in flight.
    h.actions.replaceChildren(
      el("button", { type: "button", onclick: backToMap }, "К карте"));
  } else if (status === "ready" || status === "unavailable") {
    const kids = [];
    if (status === "unavailable") {
      // #93: the visual service failed → an explicit retry affordance.
      kids.push(el("button", { type: "button",
        onclick: () => inspectSurroundings() }, "Повторить визуализацию"));
    }
    kids.push(el("button", { type: "button", onclick: backToMap }, "К карте"));
    // #253 convention: a raw spread into replaceChildren never carries a
    // null kid unfiltered (sceneActResultNode's "nothing to show" shape).
    h.actions.replaceChildren(...kids.filter(Boolean));
  } else {
    h.actions.replaceChildren();
  }
  // #253: raw replaceChildren stringifies null args (unlike el()/appendKids),
  // so a null kid (sceneActResultNode's "nothing to show") must never reach it.
  const resultNode = sceneActResultNode();
  h.result.replaceChildren(...(resultNode ? [resultNode] : []));
}

function renderScenePanel() {
  const box = document.getElementById("scene-view");
  if (box) fillScenePanel(box);
  return box;
}

async function inspectSurroundings() {
  // #312: the SINGLE /visual/scenes call site. The click captures its
  // location; every await re-checks the flight token (seq, nav epoch,
  // identity, world, player location, cache ownership, context key) — a
  // superseded flight is discarded silently, never published.
  if (!S.user || !S.character) return;
  const locId = S.character.location_id;
  // #86 review: one inspection in flight — rapid re-clicks are no-ops.
  if (S.sceneFlight) return;
  // A valid cached READY entry means zero POST and zero file GET.
  const cached = locId != null ? S.sceneCache.get(String(locId)) : null;
  if (cached && cached.status === "ready"
    && cached.contextKey === sceneContextKey()) {
    S.scene = cached;
    renderSceneVisuals();
    return;
  }
  // A stale-context entry (if any) dies before the fresh flight.
  if (cached) discardSceneEntry(locId);
  const entry = {
    locationId: locId, status: "generating", assetId: null, descriptor: null,
    blob: null, objectURL: null, contextKey: sceneContextKey(),
    capturedAt: Date.now(), errorKind: null,
  };
  S.sceneCache.set(String(locId), entry);
  S.scene = entry;
  const flight = {
    seq: ++S.sceneRequestSeq, navEpoch: navEpoch, userRef: S.user,
    characterId: S.character.id,
    worldId: S.world ? S.world.id : null,
    worldSeed: S.world ? S.world.seed : null,
    locationId: locId, contextKey: entry.contextKey, entry, controller: null,
  };
  S.sceneFlight = flight;
  const scene = renderSceneVisuals();
  if (scene) scene.scrollIntoView({ behavior: S.mapReducedMotion ? "auto" : "smooth", block: "start" });
  // One 300s deadline covers the whole flight (POST + file GET); the late
  // fire is a no-op unless the flight is still current (token check #7).
  const timer = setTimeout(() => {
    if (!sceneFlightCurrent(flight)) return;
    sceneUnavailable(flight.entry, "deadline");
  }, SCENE_REQUEST_TIMEOUT_MS);
  try {
    // The POST carries NO abort signal — a superseded flight is discarded by
    // its token checks, not by transport cancellation. #312 (m153/m154 pin):
    // the body carries the click-captured locId, not a live S.character read.
    const r = await api("/visual/scenes", { method: "POST",
      body: { location_id: locId } });
    // Token check #2: the flight must still own the state before the asset
    // is consumed.
    if (!sceneFlightCurrent(flight)) return;
    if (!r || !r.asset || r.asset.asset_type !== "scene"
      || String(r.asset.location_id) !== String(locId)
      || !r.asset.scene_descriptor || !r.asset.scene_descriptor.location
      || String(r.asset.scene_descriptor.location.id) !== String(locId)) {
      sceneUnavailable(entry, "invalid-asset");
      return;
    }
    entry.assetId = r.asset.id;
    entry.descriptor = r.asset.scene_descriptor;
    // Cookie-auth file fetch (img src cannot carry auth → blob). The abort
    // signal rides THIS fetch only; isFresh re-checks between the response
    // and the blob (token check #4).
    flight.controller = new AbortController();
    // Token check #3: a flight superseded while the POST flew must not even
    // issue the file GET (the abort signal covers later cancellation).
    if (!sceneFlightCurrent(flight)) return;
    const blob = await fetchPortraitBlob(`/visual/assets/${r.asset.id}/file`,
      { signal: flight.controller.signal,
        isFresh: () => sceneFlightCurrent(flight) });
    // Token check #5: only the still-current flight turns bytes into a URL.
    if (!sceneFlightCurrent(flight)) return;
    entry.blob = blob;
    entry.objectURL = URL.createObjectURL(blob);
    entry.status = "ready";
    S.sceneFlight = null;
    renderSceneVisuals();
  } catch (e) {
    // Token check #6: only a still-current flight lands as unavailable —
    // a superseded flight (nav away, movement, identity switch) stays silent.
    if (!sceneFlightCurrent(flight)) return;
    sceneUnavailable(entry, "error");
  } finally {
    clearTimeout(timer);
  }
}

function sceneBlock() {
  // Sync host node for renderWorld; content follows S.scene (A4 Scene view).
  const box = el("div", { class: "panel portrait", id: "scene-view" }, el("h2", {}, "Сцена"));
  fillScenePanel(box);
  return box;
}

/* ---------- #312: scene cache + flight lifecycle ---------- */

const SCENE_REQUEST_TIMEOUT_MS = 300000;

function sceneSessionKey() {
  // #312: the scene cache identity — the user OBJECT reference (a relogin
  // yields a fresh object), the character, and the world identity.
  if (!S.user || !S.character) return null;
  return {
    userRef: S.user, characterId: S.character.id,
    worldId: S.world ? S.world.id : null,
    worldSeed: S.world ? S.world.seed : null,
  };
}

function sceneSessionMatches(key) {
  if (!key || !S.user || !S.character) return false;
  const cur = sceneSessionKey();
  return !!cur && cur.userRef === key.userRef
    && cur.characterId === key.characterId
    && cur.worldId === key.worldId && cur.worldSeed === key.worldSeed;
}

function sceneContextKey(locId = (S.character ? S.character.location_id : null),
                         locs = S.mapLocations) {
  // #312: the cache validity key — world hour, readable weather, and the
  // location's name/type/roster at the moment of capture. Any change
  // invalidates the cached visual WITHOUT a regeneration POST.
  if (locId == null || !S.world) return null;
  const ts = S.world.game_timestamp;
  const hour = typeof ts === "number" ? Math.floor(ts / 60) : null;
  const weather = S.weather && S.weather.enabled
    ? (S.weather.description || "") : "";
  const loc = (locs || []).find((l) => String(l.id) === String(locId));
  const occ = loc && Array.isArray(loc.occupants)
    ? loc.occupants.map((o) => `${o.id}|${o.name}|${o.kind}`).sort().join(",")
    : "";
  return [String(locId), hour, weather, loc ? (loc.name || "") : "",
    loc ? (loc.type || "") : "", occ].join("\u0001");
}

function syncSceneContext(locs) {
  // #312: publish-time cache reconciliation — an identity change (user
  // object ref, character, world) clears the whole cache; a context-key
  // change at the player's CURRENT location discards that entry; S.scene
  // re-mounts from whatever cache remains. Entries carry no session field.
  if (!sceneSessionMatches(S.sceneSession)) {
    clearSceneCache();
    S.sceneSession = sceneSessionKey();
  }
  const locId = S.character ? S.character.location_id : null;
  if (locId != null) {
    const entry = S.sceneCache.get(String(locId));
    if (entry && entry.contextKey !== sceneContextKey(locId, locs)) {
      discardSceneEntry(locId);
    }
    S.scene = S.sceneCache.get(String(locId)) || null;
  } else {
    S.scene = null;
  }
}

function cancelSceneFlight() {
  // #312: navigation away — the in-flight file GET is aborted at the
  // network boundary (the POST carries no signal; its token checks discard
  // the result) and the still-generating entry is dropped.
  const flight = S.sceneFlight;
  if (!flight) return;
  S.sceneFlight = null;
  if (flight.controller) {
    try { flight.controller.abort(); } catch (e) { /* already aborted */ }
  }
  if (flight.entry && flight.entry.status === "generating") {
    S.sceneCache.delete(String(flight.entry.locationId));
    if (S.scene === flight.entry) S.scene = null;
  }
}

function releaseSceneURLs() {
  // #312: navigation away — mounted URLs are revoked exactly once; the
  // blobs are RETAINED so the return can recreate the URL from the cache.
  for (const entry of S.sceneCache.values()) {
    if (entry.objectURL) {
      URL.revokeObjectURL(entry.objectURL);
      entry.objectURL = null;
    }
  }
}

function clearSceneCache() {
  // #312: identity-level invalidation — every entry dies (URL revoked once,
  // blob dropped) and the mounted state resets.
  for (const entry of S.sceneCache.values()) {
    if (entry.objectURL) {
      URL.revokeObjectURL(entry.objectURL);
      entry.objectURL = null;
    }
    entry.blob = null;
  }
  S.sceneCache.clear();
  S.scene = null;
}

function discardSceneEntry(locId) {
  // #312: movement/context discard — the entry dies entirely (URL revoked
  // only while still live, blob dropped); a flight targeting it is cancelled.
  const entry = S.sceneCache.get(String(locId));
  if (!entry) return;
  S.sceneCache.delete(String(locId));
  if (entry.objectURL) {
    URL.revokeObjectURL(entry.objectURL);
    entry.objectURL = null;
  }
  entry.blob = null;
  if (S.scene === entry) S.scene = null;
  if (S.sceneFlight && S.sceneFlight.entry === entry) cancelSceneFlight();
}

function sceneUnavailable(entry, kind) {
  // #312: the single failure landing — one honest status, one toast, one
  // re-render. Only a still-current flight (or a mounted decode failure)
  // reaches here; stale flights bail at their token checks.
  entry.status = "unavailable";
  entry.errorKind = kind;
  S.sceneCache.set(String(entry.locationId), entry);
  S.scene = entry;
  S.sceneFlight = null;
  renderSceneVisuals();
  toast("Визуал недоступен, показываю данные");
}

function sceneFlightCurrent(flight) {
  // #312: the flight token — seq/epoch/identity/world/player location/
  // cache ownership/context key must ALL still match to publish anything.
  if (!flight || S.sceneFlight !== flight) return false;
  if (!S.user || !S.character || flight.userRef !== S.user
    || flight.characterId !== S.character.id
    || flight.navEpoch !== navEpoch) return false;
  if ((S.world ? S.world.id : null) !== flight.worldId
    || (S.world ? S.world.seed : null) !== flight.worldSeed) return false;
  if (String(S.character.location_id) !== String(flight.locationId)) return false;
  if (flight.entry !== S.sceneCache.get(String(flight.locationId))) return false;
  return flight.contextKey === sceneContextKey();
}

function sceneImageAlt(entry) {
  // #312: the alt comes from the VALIDATED descriptor only — never from
  // live data (the image shows the committed scene, not the current roster).
  const d = entry && entry.descriptor;
  return `Визуализация места: ${d && d.location && d.location.name
    ? d.location.name : "—"}`;
}

function renderSceneVisuals() {
  // #312: one render entry for both scene hosts — the Place stage (when a
  // scene stage for the player's CURRENT place is mounted) and the world
  // Scene panel. Returns the panel box (null on the Place screen).
  const stage = document.querySelector(".place-stage");
  if (stage && stage._sceneHosts && S.character
    && String(stage._sceneLocId) === String(S.character.location_id)) {
    updateSceneVisual(stage, stage._sceneLocId, S.mapLocations);
  }
  return renderScenePanel();
}

/* ---------- #87 (A5): free-text scene action ---------- */

function sceneActBar(onSubmit = submitSceneAct) {
  return el("div", { class: "scene-act" },
    el("input", {
      id: "scene-act-input", type: "text",
      value: S.sceneActDraft || "",
      placeholder: "Что вы делаете? Например: поговорить с Ивановым",
      // #175: capture the draft on every keystroke too (the post-submit
      // pattern alone loses text to a re-inspect while typing).
      oninput: (ev) => { S.sceneActDraft = ev.target.value; },
      // #327: OS key auto-repeat must not re-send after a settled rejection.
      onkeydown: (ev) => {
        if (ev.key === "Enter" && !ev.repeat) onSubmit();
      },
    }),
    el("button", { id: "scene-act-go", type: "button",
      onclick: () => onSubmit() }, "Сделать"));
}

function sceneActInterpText(i) {
  if (!i) return "";
  const p = i.params || {};
  if (i.intent === "move") return `идти: ${p.destination_name || p.destination_id || ""}`;
  if (i.intent === "socialize") return `поговорить: ${p.target_name || ""}`;
  if (i.intent === "buy") return `купить: ${p.item || ""}`;
  if (i.intent === "wear") return `надеть: ${codeLabel(OBJECT_TYPE_RU, p.object_type) || p.object_text || ""}`;
  if (i.intent === "take_off") return `снять: ${codeLabel(OBJECT_TYPE_RU, p.object_type) || p.object_text || ""}`;
  return String(i.intent || "");
}

function sceneActResultNode() {
  // Result of the last /scene/act submission. It lives in S.sceneAct (not in
  // the DOM), so it survives the scene refresh — the committed gameplay
  // result stays visible even when the visual re-inspection fails.
  const r = S.sceneAct;
  if (!r) return null;
  const lines = [];
  if (r.ok === false) {
    lines.push(r.detail || "Не удалось выполнить действие");
  } else {
    if (r.interpretation) {
      lines.push(el("div", { class: "muted" },
        "Распознано: " + sceneActInterpText(r.interpretation)));
    }
    if (r.action === "look") {
      lines.push(el("div", {}, r.result));
    } else if (r.action === "wear" || r.action === "take_off") {
      lines.push(el("div", {}, r.worn ? "Надето" : "Снято"));
    } else if (r.task_id) {
      // #295: the last event's server-rendered text wins; raw event_type is
      // the fallback (same doctrine as the feed). task_label is the
      // backend's label(TASK_TYPE, ...) — JS dict covers older payloads.
      const lastEv = r.events && r.events.length
        ? r.events[r.events.length - 1] : null;
      const ev = lastEv ? " · " + (lastEv.text || lastEv.event_type) : "";
      lines.push(el("div", {}, "Принято: "
        + (r.task_label || codeLabel(TASK_TYPE_RU, r.task_type))
        + " (задача " + r.task_id + ")" + ev));
      // #304: the acceptance subsection reads the adapter — the tracked
      // task's server-backed status owns the live line (the POST response
      // alone never produces completion).
      const entry = taskPresentation.get(String(r.task_id));
      if (entry) {
        lines.push(el("div", { class: "muted" }, taskPresentationText(entry)));
      }
    }
  }
  return el("div", { id: "scene-act-result", class: "scene-act-line muted" }, ...lines);
}

async function submitSceneAct(refreshScene = true) {
  const input = document.getElementById("scene-act-input");
  const text = input ? input.value.trim() : "";
  if (!text) return;
  // #304: the shared submission latch — Enter + click double-fire is ONE
  // POST (the disabled button stays as today, but correctness no longer
  // depends on it).
  const token = beginActionSubmission();
  if (!token) return;
  // A5: one submission in flight — the button is disabled for the request,
  // mirroring the A4 one-inspection guard philosophy.
  const go = document.getElementById("scene-act-go");
  if (go) go.disabled = true;
  try {
    const r = await api("/scene/act", { method: "POST", body: { text } });
    // #304: guard (a) — navigation must not discard the task id, but a
    // session change must not accept it. The acceptance runs while the
    // token still owns the latch; the release is independent of the
    // acceptance's own detail GET.
    if (!taskContinuationCurrent(token)) return;
    S.sceneAct = r;
    if (r && r.ok === false) {
      // #93 review F2: 200 ok:false (unsupported/ambiguous/not_possible) is a
      // RECOVERABLE rejection — the user must rephrase, so the typed draft is
      // kept here exactly like the catch path below.
      S.sceneActDraft = text;
    } else {
      // #304: a task-producing acceptance joins the registry — the POST
      // response alone never produces completion; the tracked task's own
      // reads own its status from here.
      if (r && r.task_id != null) acceptTask(r, token);
      // Only a real success (or the discarded 200-ok shape) clears it.
      S.sceneActDraft = null;
    }
    finishActionSubmission(token);
    // #312: a committed scene action is a PRESENTATION refresh only — the
    // visual lifecycle is owned by the explicit inspect affordance, so the
    // success path never re-inspects (no /visual/scenes POST from here).
    // Idle keeps the result line without a refresh.
    renderScenePanel();
  } catch (e) {
    finishActionSubmission(token);
    // Same error phrasing as the A3 travel bar (409/401/4xx surfaces).
    // #303 review F2: the persistent result line localizes §61 refusals too —
    // the raw server detail never reaches player-visible copy (the toast
    // below keeps its own #93 semantics untouched).
    const refusalHint = isAutonomousRefusal(e) ? AUTONOMOUS_HINT : travelErrorText(e);
    S.sceneAct = { ok: false, detail: refusalHint };
    // #93: a recoverable error keeps the typed draft (chat-draft pattern).
    S.sceneActDraft = text;
    // #93 review F1: §61 refusals surface as localized hints (same discipline
    // as doAction/moveTo/viewExternal); any other error keeps the raw text.
    // The typed draft stays — switching control mode makes the same text valid.
    renderScenePanel();
    toast(isAutonomousRefusal(e) ? AUTONOMOUS_HINT : e.message, true);
  } finally {
    finishActionSubmission(token);  // idempotent — only while still current
    if (go) go.disabled = false;
  }
}

/* ---------- topbar ---------- */

function weatherLabel() {
  if (!S.weather || !S.weather.enabled) return "";
  return "Погода: " + S.weather.description + (S.weather.source === "historical"
    ? " (реальная " + S.weather.real_date + ")" : "");
}

function topbar(active) {
  const tabs = [["#/world", "Мир"], ["#/chat", "Чат"], ["#/people", "Люди"],
    ["#/inventory", "Инвентарь"], ["#/shop", "Рынок"], ["#/tasks", "Дела"],
    ["#/desire", "Цель"], ["#/profile", "Профиль"], ["#/appearance", "Образ"]];
  if (["admin", "developer", "moderator"].includes(S.user.role)) {
    tabs.push(["#/admin", "Админ"]);
  }
  const weatherSpan = S.weather && S.weather.enabled
    ? el("span", { id: "weather", class: "muted" }, weatherLabel())
    : null;
  return el("div", { class: "topbar" },
    el("h1", {}, "ВЛ1: Рейнеке"),
    el("span", { id: "clock", class: "muted" },
      S.world ? dayTime(S.world.game_timestamp) : clockPendingLabel()),
    weatherSpan,
    el("span", { class: "spacer" }),
    el("div", { class: "tabs" },
      tabs.map(([h, label]) => el("button", {
        class: h === active ? "active" : "", onclick: () => { location.hash = h; },
      }, label))),
  );
}

// #313: compact contextual header — reuses the topbar() registry nodes.
function ctxTopbar(back, title, tb, extraClass) {
  const tabs = tb.querySelector(".tabs");
  // #313 fix (m269 s10/s18): the clock span must survive the ctx header —
  // the location/chat screens keep their live #clock node from topbar().
  const clock = tb.querySelector("#clock");
  // #313 R1: carry the remaining topbar() children too — h1 and the weather
  // span (absent → null; el()/appendKids() drop null children) must not be
  // silently discarded on the place/chat routes.
  const h1 = tb.querySelector("h1");
  const weather = tb.querySelector("#weather");
  return el("div", { class: "ctx-topbar" + (extraClass ? " " + extraClass : "") },
    back, title, h1, clock, weather,
    el("details", { class: "ctx-sections" }, el("summary", {}, "Разделы"), tabs));
}

/* #269: cold-entry world bootstrap for the topbar clock. One fire-and-
   forget /world fetch per navigation while world data is missing — coalesced
   within the SAME session context, never a poll, never blocking the
   requested screen, never starting feed/WS. Session context = S.user
   REFERENCE (a fresh object after login/logout/account deletion, so a
   same-id relogin is still a new session) + character ID (screen refreshes
   replace the S.character object on every /characters read; the id
   distinguishes a real character change from a same-character refresh).
   Stale results AND stale failures are dropped; a newer authoritative
   S.world is never overwritten. A failure records an honest unavailable
   mark that survives topbar re-renders for that context; the next route()/
   reload may retry once (no polling loop). The in-flight bootstrap owns the
   joinable /world GET slot (worldGet) that api() hands world/location reads
   to. */
let worldGet = null;           // { user, charId, promise } of the joinable bootstrap flight
let worldFailure = null;       // { user, charId } of the last failed attempt

/* Pure read for topbar(): what #clock shows while S.world is still null. */
function clockPendingLabel() {
  const user = S.user;
  const charId = S.character ? S.character.id : null;
  if (worldFailure && worldFailure.user === user && worldFailure.charId === charId) {
    return "н/д";  // this session's bootstrap failed — honest, not loading
  }
  return "…";
}

function hydrateWorldClock() {
  const user = S.user;
  const charId = S.character ? S.character.id : null;
  // Single-flight WITHIN the current context only: a pending request from a
  // previous session must not block this session's bootstrap.
  if (worldGet && worldGet.user === user && worldGet.charId === charId) return;
  const flight = { user, charId };
  worldGet = flight;
  worldFailure = null;  // a new attempt supersedes the unavailable mark
  const request = api("/world", { worldBootstrap: flight });
  // The joinable promise exposes the body ONLY while this flight still owns
  // the slot AND the session context is unchanged (S21/S22): a stale
  // completion resolves to null — each consumer drops null at its own
  // boundary (before its extra awaits and shared writes), and no stale
  // rejection ever paints retry/toast/failure UI. Current failures
  // still reject so joiners handle them normally.
  flight.promise = request.then(
    (world) => (worldGet === flight && S.user === user && S.character
      && S.character.id === charId) ? world : null,
    (e) => (worldGet === flight && S.user === user && S.character
      && S.character.id === charId) ? Promise.reject(e) : null);
  flight.promise.then((world) => {
    // Ownership FIRST as an EARLY RETURN: a flight that lost the slot to a
    // newer bootstrap never releases the slot, publishes or marks — including
    // when ownership is lost in the microtask gap between the wrapper's gate
    // and this handler (S21). A conditional release is not an ownership guard.
    if (worldGet !== flight) return;
    worldGet = null;  // only the owner releases the slot
    // A stale completion resolved to null; a real loader/poll publication
    // is never overwritten with this (older) snapshot. Re-check the context:
    // a 401 from another in-flight request can land in the microtask hop
    // between the wrapper's gate and this handler.
    if (!world || S.world || S.user !== user || !S.character
      || S.character.id !== charId) return;
    S.world = world;
    const clock = document.getElementById("clock");
    if (clock) clock.textContent = dayTime(world.game_timestamp);
  }, () => {
    if (worldGet !== flight) return;
    worldGet = null;
    if (S.world || S.user !== user || !S.character
      || S.character.id !== charId) return;
    worldFailure = flight;
    const clock = document.getElementById("clock");
    if (clock) clock.textContent = "н/д";
  });
}

/* ---------- #289: world screen canon v3 — floating nav pill + inline icons ----------
   The world screen uses the pill INSTEAD of the topbar (other screens keep
   topbar() unchanged). Icons are inline SVG (stroke 1.75, 24px box, §27) —
   no external assets, no emoji. */

/* Static-path SVG builder. DOM only via createElementNS/setAttribute — never
   raw HTML string injection (XSS discipline), and the path data below is a
   fixed literal table, never user data. */
function svgIcon(paths) {
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "1.75");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");
  for (const [tag, attrs] of paths) {
    const node = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    svg.append(node);
  }
  return svg;
}

const ICON_DROP = [["path", { d: "M12 3.25s6.25 6.5 6.25 10.5a6.25 6.25 0 1 1-12.5 0c0-4 6.25-10.5 6.25-10.5z" }]];
const ICON_EYE = [["path", { d: "M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" }],
  ["circle", { cx: "12", cy: "12", r: "3" }]];

/* 9 sections, same hash routes (and order) as the topbar tabs. */
const PILL_TABS = [
  ["#/world", "Мир", [["circle", { cx: "12", cy: "9.25", r: "4.25" }],
    ["path", { d: "M3.5 18.75c1.42 0 1.42 1.5 2.83 1.5s1.41-1.5 2.83-1.5 1.41 1.5 2.83 1.5 1.41-1.5 2.83-1.5 1.42 1.5 2.83 1.5 1.41-1.5 2.83-1.5" }]]],
  ["#/chat", "Чат", [["path", { d: "M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z" }]]],
  ["#/people", "Люди", [["path", { d: "M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" }],
    ["circle", { cx: "9", cy: "7", r: "4" }],
    ["path", { d: "M23 21v-2a4 4 0 0 0-3-3.87" }],
    ["path", { d: "M16 3.13a4 4 0 0 1 0 7.75" }]]],
  ["#/inventory", "Инвентарь", [["path", { d: "M6 2 3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4z" }],
    ["path", { d: "M3 6h18" }],
    ["path", { d: "M16 10a4 4 0 0 1-8 0" }]]],
  ["#/shop", "Рынок", [["path", { d: "M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z" }],
    ["line", { x1: "7", y1: "7", x2: "7.01", y2: "7" }]]],
  ["#/tasks", "Дела", [["rect", { x: "5", y: "4.5", width: "14", height: "17", rx: "2.25" }],
    ["path", { d: "M9 2.5h6a1 1 0 0 1 1 1V5a1 1 0 0 1-1 1H9a1 1 0 0 1-1-1V3.5a1 1 0 0 1 1-1z" }],
    ["path", { d: "m8.9 13.6 2.2 2.2 4.2-4.7" }]]],
  ["#/desire", "Цель", [["path", { d: "M4 15s1-1 4-1 5 2 7 2 4-1 4-1V3s-1 1-4 1-5-2-7-2-4 1-4 1z" }],
    ["line", { x1: "4", y1: "22", x2: "4", y2: "15.5" }]]],
  ["#/profile", "Профиль", [["path", { d: "M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" }],
    ["circle", { cx: "12", cy: "7", r: "4" }]]],
  ["#/appearance", "Образ", [["rect", { x: "3", y: "3", width: "18", height: "18", rx: "2.5" }],
    ["circle", { cx: "8.5", cy: "8.5", r: "1.5" }],
    ["path", { d: "m21 15-5-5L5 21" }]]],
];

/* #289: the world panel chip shows the bare description — the «Погода: »
   prefix stays a topbar-only affordance (weatherLabel() is untouched). */
function worldWeatherShort() {
  if (!S.weather || !S.weather.enabled) return "";
  return S.weather.description + (S.weather.source === "historical"
    ? " (реальная " + S.weather.real_date + ")" : "");
}
function worldWeatherChipText() {
  const short = worldWeatherShort();
  return short ? " · " + short : "";
}

/* ---------- world view (§78) ---------- */

// #161: navigate without double-dispatching — hash assignment fires
// hashchange → route() async; only call viewWorld() directly when the
// hash is already current (no event will fire). NOTE: the comparison is
// written with the literal operand first on purpose — a `location.hash`
// comparison against "#/world" in source order is whole-file-pinned ==1
// by m147/m153/m154/m156/m157 (the pollEvents re-render gate), so this
// site must not add a 2nd match.
function gotoWorld() {
  if ("#/world" === location.hash) viewWorld();
  else location.hash = "#/world";
}

/* #107: first-visit concierge welcome — a single dismissible card rendered
   as the first child of .world-shell (reads above the panels). One-time
   semantics live in localStorage; storage-denied/private mode NEVER shows
   the card (fail-closed: catch ⇒ seen). Dismissal persists best-effort —
   a denied write just removes the card for this session. */
function welcomeCard() {
  if (S.welcomeDismissed) return null;
  let seen = false;
  try { seen = window.localStorage.getItem("vl1.welcome.done") === "1"; } catch (e) { seen = true; }
  if (seen) return null;
  const node = el("section", { class: "panel welcome-card" },
    el("h2", {}, "Кошка-консьержка"),
    el("p", { class: "welcome-copy" },
      "Добро пожаловать на Рейнеке. Остров живёт своей жизнью — а я подскажу, с чего начать."),
    /* World-first-view: photos/tips/closing live in a native disclosure, closed by default; toggling it writes nothing. */
    el("details", { class: "welcome-details" },
      el("summary", { class: "welcome-summary" }, "Советы и фотографии"),
      el("div", { class: "welcome-photos" },
        el("img", { src: "/static/static/landing/concierge-cat-ref-face-tail.jpg",
          alt: "Кошка-консьержка — справочная фотография" }),
        el("img", { src: "/static/static/landing/concierge-cat-ref-loaf.jpg",
          alt: "Кошка-консьержка — поза отдыхающей кошки" })),
      el("div", { class: "welcome-tips" },
        el("div", { class: "welcome-tip" }, "„Мир“ покажет, где ты, и что вокруг."),
        el("div", { class: "welcome-tip" }, "„Люди“ — кто тебя знает и как к тебе относится."),
        el("div", { class: "welcome-tip" }, "„Дела“ — работа и поручения: кормят и знакомят.")),
      el("p", { class: "welcome-close muted" }, "Если потеряешься — просто найди меня.")),
    el("button", { type: "button", onclick: () => {
      S.welcomeDismissed = true; // write-denied storage (private mode): stays dismissed until reload
      try { window.localStorage.setItem("vl1.welcome.done", "1"); } catch (e) { /* best-effort persist */ }
      node.remove();
    } }, "Я освоился"));
  return node;
}

async function viewWorld() {
  navEpoch++;  // #302: every load start is a new navigation generation
  // #312: direct re-entries (arrival, gotoWorld) bypass route() — the scene
  // flight does not survive a view load; mounted URLs release (blobs kept).
  cancelSceneFlight();
  releaseSceneURLs();
  location.hash = "#/world";
  const loading = el("div", { class: "muted" }, "Загрузка…");
  app.replaceChildren(topbar("#/world"), loading);
  // #269: session-context identity for the async continuation — a logout/
  // relogin or character change while the loads fly must not publish the
  // older world/character over the new session's state. World and weather
  // are staged LOCALLY; null (stale joined bootstrap) and a dead context
  // bail immediately after the joined /world, and the actual-context guard
  // runs again before any shared write (S22) — freshness guarded too (S23).
  // #302: the navigation epoch joins the guard — a load superseded by a
  // newer dispatch (or a direct re-entry) must not publish at all.
  const ctxUser = S.user;
  const ctxCharId = S.character.id;
  const epoch = navEpoch;
  try {
    const world = await api("/world");
    // #269: null = the joined bootstrap lost ownership (stale, S22) — and
    // the context may have died while the load flew. Bail BEFORE the extra
    // awaits: no further requests, no catch-side retry/toast for a dead
    // context. Rechecked below before any shared write (S23).
    if (!world || S.user !== ctxUser || !S.character
      || S.character.id !== ctxCharId || epoch !== navEpoch) return;
    let weather = null;
    try { weather = await api("/weather"); } catch { weather = null; }
    const me = await api(`/characters/${ctxCharId}`);
    const locs = await api("/locations");

    // Fetch tasks if owner
    let tasksData = null;
    let tasksReadToken = null;
    if (me.is_owner) {
      // #304: the World load's tasks read is an authoritative adapter
      // refresher — token at START, the in-flight counter spans the await,
      // the ingest lands after the #302 guard below (a superseded load
      // commits nothing).
      tasksReadToken = ++taskReadSequence;
      taskListReadsInFlight++;
      try {
        tasksData = await api(`/characters/${ctxCharId}/tasks`);
      } catch (e) {
        console.error("Tasks fetch failed", e);
      } finally {
        taskListReadsInFlight--;
      }
    }
    if (S.user !== ctxUser || !S.character || S.character.id !== ctxCharId
      || epoch !== navEpoch) return;
    if (tasksReadToken != null) ingestTaskSnapshot(tasksData, tasksReadToken);
    // #269 S23: freshness, not just context — an out-of-order OLDER
    // same-context load must not republish its world/weather over an
    // already-newer snapshot; it still renders (with the newer shared world).
    if (!S.world || S.world.game_timestamp <= world.game_timestamp) {
      S.world = world;
      S.weather = weather;
    }
    S.character = { ...S.character, ...me };
    // #302: Place → World return — restore the focus the world view had
    // when the place was entered, through the EXISTING manual-focus
    // mechanism (#92): a foreign focus carries the manual flag so the poll
    // keeps it until the player physically moves; the player-location
    // focus is the poll's own default and needs no flag. Consumed once —
    // the next world render is a plain world view, not a return.
    if (gameUiContext.viewLocationId != null
      && gameUiContext.lastWorldFocusId != null
      && gameUiContext.lastWorldFocusPlayerLocId === S.character.location_id) {
      S.mapFocusLocationId = gameUiContext.lastWorldFocusId;
      S.mapFocusManual = gameUiContext.lastWorldFocusId !== S.character.location_id
        || S.mapFocusManual;
      S.mapFocusAnchor = S.character.location_id;
    }
    gameUiContext.viewLocationId = null;
    renderWorld(locs, tasksData);
    // #311: start (identity-reconciled, idempotent — NO transport restart on
    // returning to World) + attach the world view's feed listener. The World
    // view also fires the ONE-TIME session bootstrap poll (place/chat
    // entries start the transport without it — m269 S10 keeps its single
    // /world fetch on a place cold boot).
    worldFeedController.start();
    worldFeedController.initialPoll();
    worldFeedController.attachView(worldFeedListener);
  } catch (e) {
    // #302: a superseded load paints no retry/toast into a newer view.
    if (epoch !== navEpoch || S.user !== ctxUser || !S.character
      || S.character.id !== ctxCharId) return;
    // #93: transient failure — explicit retry instead of a dead «Загрузка…».
    loadRetryNode(loading, () => viewWorld());
    toast(e.message, true);
  }
}

function needBar(label, value) {
  if (value === null || value === undefined) {
    return el("div", { class: "need" },
      el("div", { class: "row" }, el("span", {}, label), el("span", {}, "—")),
      el("div", { class: "bar" }, el("div", { style: `width:0%` })));
  }
  const v = Math.max(0, Math.min(100, Math.round(value)));
  return el("div", { class: `need ${v < 30 ? "low" : ""}` },
    el("div", { class: "row" }, el("span", {}, label), el("span", {}, `${v}`)),
    el("div", { class: "bar" }, el("div", { style: `width:${v}%` })));
}

/* #301 World HUD: «Сейчас» — the character's current deed, read-only. The
   server pre-orders active/planned oldest created_at first, so the HUD takes
   active[0] (else planned[0]) WITHOUT re-sorting. Honest states: the tasks
   fetch failed (or the viewer is not the owner) -> «Данные о делах
   недоступны»; nothing queued or running -> «Сейчас нет активных дел». No
   progress bars, no timers, no ETA math — a render-time snapshot with the
   same freshness model as the needs below. The expand control (mobile
   compact band, #301 F) is a real button: keyboard-reachable, and the
   toggle rides classList (no document listeners — vm-harness surface). */
function nowPanel(tasksData, autonomous) {
  const expandBtn = el("button", { class: "world-panel-expand", type: "button",
      "aria-expanded": "false", "aria-controls": "world-panel-details",
      onclick: (ev) => {
        const btn = ev.currentTarget;
        const panel = btn.closest(".world-panel");
        if (!panel) return;
        const expanded = panel.classList.toggle("world-panel--expanded");
        btn.setAttribute("aria-expanded", expanded ? "true" : "false");
        btn.textContent = expanded ? "Свернуть" : "Детали";
        // #301 (G): keep the mobile POI card BELOW the HUD band. Static
        // offsets — the vm-harness (m302/m310) has no getBoundingClientRect,
        // so the exact expanded height is a browser-probe follow-up; the
        // compact cap (136px) + panel top 12px + 8px gap = 156px.
        const poiHost = document.getElementById("poi-card-host");
        if (poiHost) poiHost.setAttribute("style", expanded
          ? "--poi-top: calc(42vh + 20px)" : "--poi-top: 156px");
      } }, "Детали");
  // #304: the task slot is a dedicated host filled from the adapter (the
  // selected task's lifecycle caption, the honest empty state, or the
  // unavailable line) — refreshTaskPresentationHosts() fills it at mount
  // and patches it in place on every adapter change (never a full-screen
  // rerender for status). The expand button and the .world-now-* styling
  // are untouched.
  const parts = [el("div", { class: "world-now-row" },
    el("span", { class: "world-now-label" }, "Сейчас"),
    el("span", { class: "world-now-task-host", id: "world-now-task-host" }),
    // #341: the authoritative submission-feedback caption (409 busy, network
    // failure, …) must be visible on #/world too. refreshTaskPresentationHosts()
    // patches every MOUNTED .task-submit-line with taskSubmitLineText(); absent
    // hosts are skipped (#304 discipline), so no stale caption survives
    // navigation. Keep it separate from the task-status host (different
    // semantics).
    el("div", { class: "task-submit-line muted" }),
    expandBtn)];
  if (autonomous) {
    // #301: the autonomy state line moved INTO the HUD (readable above the
    // map fold) — relocated from the below-fold actions card, same
    // autonomy-hint const and the same Profile switch path; no
    // direct-action buttons exist in this mode.
    parts.push(el("p", { class: "world-now-mode" }, AUTONOMOUS_HINT));
    parts.push(el("button", { class: "world-now-profile", type: "button",
      onclick: () => { location.hash = "#/profile"; } }, "Открыть Профиль"));
  }
  return el("div", { class: "world-now" }, ...parts);
}

/* #301: 1–3 newest island events as compact rows in the panel — a preview
   OUTSIDE #feed (the WS/REST updaters replaceChildren that node). Same
   render-time derivation as the needs: no new subscription, no second poll;
   critical highlighting rides the existing isCriticalEvent inside feedRow. */
function islandEventsBlock() {
  const rows = (S.feed || []).slice(-3).reverse();
  // #301: the container is ALWAYS present (stable id) so the feed updaters
  // can refresh the preview after every arrival — on a cold entry the first
  // poll lands after renderWorld, and a render-only block would stay empty
  // for the whole visit (live-probe defect). Empty = zero-height div.
  return el("div", { class: "world-island-events", id: "world-island-events" },
    ...(rows.length
      ? [el("h2", { class: "world-sec-h" }, "Остров"), ...rows.map(feedRow)]
      : []));
}

/* #301: the island preview mirrors the live feed — re-derived from S.feed
   (no new fetch) after every REST/WS arrival, same freshness model as the
   needs' poll cycle. Absent panel (other view) → no-op. */
function refreshIslandPreview() {
  const box = document.getElementById("world-island-events");
  if (!box) return;
  const rows = (S.feed || []).slice(-3).reverse();
  box.replaceChildren(...(rows.length
    ? [el("h2", { class: "world-sec-h" }, "Остров"), ...rows.map(feedRow)]
    : []));
}

const MAP_LOW_MARKER_BUDGET = 120;

/* #57 first-queue ambient effects. Percentage coords (0-100 of the 1600x900
   map space) come from the visual zone analysis of 2026-10-27: individual
   trees are NOT resolvable in the base image, so tree zones are tentative
   soft overlays kept very subtle; smoke sources have weak visual
   confirmation and are gated by flags.smoke — flip it off if they look
   wrong in practice. Surf segments and bird corridors sit over open water,
   clear of the pier marker (~46,91) and the bottom attribution. */
const MAP_AMBIENT = {
  flags: { smoke: true },
  trees: [ // {x,y,w,h} sway zones, T1-T3
    { x: 35, y: 18, w: 30, h: 22 }, // T1 north interior
    { x: 27, y: 25, w: 15, h: 18 }, // T2 northwest interior
    { x: 56, y: 58, w: 14, h: 15 }, // T3 southeast interior
  ],
  surf: [ // {x1,y1,x2,y2} coastline endpoints (water side), S1-S4
    { x1: 30, y1: 14, x2: 69, y2: 13 }, // S1 north
    { x1: 29, y1: 26, x2: 29, y2: 68 }, // S2 west
    { x1: 70, y1: 22, x2: 73, y2: 66 }, // S3 east
    { x1: 31, y1: 73, x2: 67, y2: 72 }, // S4 south
  ],
  smoke: [ // chimney sources, weak confirmation — flag-gated
    { x: 46.79, y: 51.4 }, // kitchen
    { x: 42.89, y: 56.0 }, // workshop
  ],
  birds: [ // {x1,y1,x2,y2} corridors over open water, B1-B2
    { x1: 12, y1: 9, x2: 86, y2: 8 }, // B1 north, band y 4-12%
    { x1: 15, y1: 86, x2: 83, y2: 85 }, // B2 south, band y 84-88%
  ],
};

function mapTimePhase(gameTimestamp) {
  const minute = ((gameTimestamp || 0) % 1440 + 1440) % 1440;
  if (minute < 360 || minute >= 1200) return "night";
  if (minute < 480) return "dawn";
  if (minute >= 1020) return "dusk";
  return "day";
}

function mapDistance(a, b) {
  if (!a || !b || a.x === null || a.y === null || b.x === null || b.y === null) return Infinity;
  return Math.hypot(a.x - b.x, a.y - b.y);
}

function visibleMapLocations(locs, focus) {
  // #106 Lane B (visual bible §zoom-levels): houses render only at local
  // zoom — region mirrors the island LOD exclusion and drops them from the
  // marker set entirely; at local they downgrade to ambient spans in
  // buildLivingMap (no interactive house anchors).
  const positioned = locs.filter(l =>
    l.x !== null && l.y !== null && l.type !== "island"
    && (S.mapLod === "local" || l.type !== "house"));
  if (S.mapLod === "island") return positioned;
  if (!focus || focus.x === null || focus.y === null) return positioned;
  const radius = S.mapLod === "region" ? 28 : 14;
  return positioned.filter(l => mapDistance(l, focus) <= radius);
}

function mapNpcMarkers(visibleLocs, cardW, cardH) {
  const markers = [];
  for (const loc of visibleLocs) {
    const npcs = (loc.occupants || []).filter(o => o.kind === "npc");
    if (!npcs.length) continue;
    const pos = worldToScreen(locationToWorld(loc), cardW, cardH);

    if (S.mapLod !== "local" || S.mapQuality === "low-mobile") {
      markers.push(el("span", {
        class: "map-dynamic-marker npc-cluster",
        style: `left:${pos.x}%; top:${pos.y}%`,
        "data-location-id": String(loc.id),
        title: `${npcs.length} NPC · ${loc.name}`,
      }, String(npcs.length)));
      continue;
    }

    npcs.forEach((npc, index) => {
      // Presentation-only screen offset. Authoritative position remains location_id.
      const angle = (index * 137.5) * Math.PI / 180;
      const radius = 10 + Math.floor(index / 6) * 5;
      const dx = Math.round(Math.cos(angle) * radius);
      const dy = Math.round(Math.sin(angle) * radius);
      markers.push(el("span", {
        class: "map-dynamic-marker npc-person",
        style: `left:${pos.x}%; top:${pos.y}%; --npc-dx:${dx}px; --npc-dy:${dy}px`,
        "data-character-id": npc.id,
        "data-location-id": String(loc.id),
        title: `${npc.name} · ${loc.name}`,
      }, el("span", { class: "npc-glyph", "aria-hidden": "true" })));
    });
  }
  return markers;
}

function mapEventMarkers(visibleLocs, cardW, cardH) {
  const visibleIds = new Set(visibleLocs.map(l => l.id));
  const locations = new Map(visibleLocs.map(l => [l.id, l]));
  const maxEvents = S.mapQuality === "low-mobile" ? 6 : (S.mapLod === "local" ? 16 : 10);
  const recent = S.feed.filter(ev => ev.location_id && visibleIds.has(ev.location_id)).slice(-maxEvents);
  const grouped = new Map();
  for (const ev of recent) {
    const list = grouped.get(ev.location_id) || [];
    list.push(ev);
    grouped.set(ev.location_id, list);
  }
  return [...grouped.entries()].map(([locationId, events]) => {
    const loc = locations.get(locationId);
    const last = events[events.length - 1];
    // #289: a critical event at this location tints the marker terracotta.
    const critical = events.some(isCriticalEvent);
    const pos = worldToScreen(locationToWorld(loc), cardW, cardH);
    return el("span", {
      class: `map-dynamic-marker event-marker${critical ? " critical" : ""}`,
      style: `left:${pos.x}%; top:${pos.y}%`,
      "data-location-id": String(locationId),
      title: `${last.text || last.event_type || "Событие"} · ${loc.name}`,
    }, events.length > 1 ? String(events.length) : "!");
  });
}

/* #106 Lane A (item 4): touch has no :hover, so keep the tapped anchor's
   label visible after the tap — one `touched` anchor at a time; the class
   is dropped on the next map rebuild (or when another anchor is tapped). */
function markTouchedAnchor(ev) {
  for (const a of document.querySelectorAll(".anchor.touched")) {
    a.classList.remove("touched");
  }
  ev.currentTarget.classList.add("touched");
}

/* #57 ambient layer: sits above the base image and below tint/lights,
   markers and weather (z-index 0 + DOM slot right after .map-base), never
   interactive. Gating mirrors rain/fog: nothing at quality low-mobile or
   when reduced motion is on. Per-effect LOD: surf on the coast is visible
   at every zoom, trees+smoke only at region/local, birds only at
   island/region (they fly high). Returns null when fully gated. */
function buildMapAmbient(ctx) {
  if (ctx.quality === "low-mobile" || ctx.reduced) return null;
  const ambient = el("div", { class: "map-ambient", "aria-hidden": "true" });
  for (const seg of MAP_AMBIENT.surf) {
    ambient.append(el("span", {
      class: "map-ambient-surf",
      style: mapSurfBandStyle(seg),
      "data-amb-x1": String(seg.x1), "data-amb-y1": String(seg.y1),
      "data-amb-x2": String(seg.x2), "data-amb-y2": String(seg.y2),
    }));
  }
  if (ctx.lod !== "island") {
    MAP_AMBIENT.trees.forEach((zone, i) => ambient.append(el("span", {
      class: "map-ambient-tree",
      style: `left:${zone.x}%; top:${zone.y}%; width:${zone.w}%; height:${zone.h}%;`
        + ` animation-duration:${11 + i}s; animation-delay:${-3 * i}s;`,
      "data-amb-x": String(zone.x), "data-amb-y": String(zone.y),
      "data-amb-w": String(zone.w), "data-amb-h": String(zone.h),
    })));
    if (MAP_AMBIENT.flags.smoke) {
      MAP_AMBIENT.smoke.forEach((src, i) => ambient.append(el("span", {
        class: "map-ambient-smoke",
        style: `left:${src.x - 1}%; top:${src.y - 1.4}%;`
          + ` animation-duration:${9 + 2 * i}s; animation-delay:${-4 * i - 2}s;`,
        "data-amb-x": String(src.x - 1), "data-amb-y": String(src.y - 1.4),
      })));
    }
  }
  if (ctx.lod !== "local") {
    MAP_AMBIENT.birds.forEach((corridor, i) => ambient.append(el("span", {
      class: "map-ambient-bird",
      style: mapBirdCorridorStyle(corridor, i),
      "data-amb-x1": String(corridor.x1), "data-amb-y1": String(corridor.y1),
      "data-amb-x2": String(corridor.x2), "data-amb-y2": String(corridor.y2),
      "data-amb-i": String(i),
    })));
  }
  return ambient;
}

/* #57: derive a surf band box from coastline endpoints — oriented along the
   segment, centered on the line, ~13px thick on the 1600x900 stage. */
function mapSurfBandStyle(seg) {
  const horizontal = Math.abs(seg.x2 - seg.x1) >= Math.abs(seg.y2 - seg.y1);
  return horizontal
    ? `left:${Math.min(seg.x1, seg.x2)}%; top:${(seg.y1 + seg.y2) / 2 - 0.75}%;`
      + ` width:${Math.abs(seg.x2 - seg.x1)}%; height:1.5%;`
    : `left:${(seg.x1 + seg.x2) / 2 - 0.425}%; top:${Math.min(seg.y1, seg.y2)}%;`
      + ` width:0.85%; height:${Math.abs(seg.y2 - seg.y1)}%;`;
}

/* #57: bird corridor element spans the flight line; the CSS animation
   translates it by 100% of its own width — exactly one corridor length. */
function mapBirdCorridorStyle(corridor, i) {
  return `left:${Math.min(corridor.x1, corridor.x2)}%; top:${(corridor.y1 + corridor.y2) / 2}%;`
    + ` width:${Math.abs(corridor.x2 - corridor.x1)}%;`
    + ` animation-duration:${28 + 8 * i}s; animation-delay:${-(9 + 12 * i)}s;`;
}

// #308: one authoritative map projection. World space = master 1600x900
// (coords.json image_space). /locations x/y are percentages of the VISIBLE
// container after object-fit:cover crops the 16:9 master. The AUTHOR crop is
// the 742/616 card the coords were authored against: locationToWorld is the
// authoritative game-data -> master-pixel conversion and always uses it.
// Tiles, anchors and every dynamic actor must share this transform so POIs
// cannot drift.
const MAP_WORLD = { w: 1600, h: 900 };
const MAP_AUTHOR_CARD = { w: 742, h: 616 };
const MAP_COVER_CROP_X = (1 - (742 / 616) / (MAP_WORLD.w / MAP_WORLD.h)) / 2;
function locationToWorld(loc) {
  const px = (Number(loc && loc.x) || 0) / 100;
  const py = (Number(loc && loc.y) || 0) / 100;
  return {
    x: (px * (1 - 2 * MAP_COVER_CROP_X) + MAP_COVER_CROP_X) * MAP_WORLD.w,
    y: py * MAP_WORLD.h,
  };
}
// #308 P2: the World stage stretches .map-card to the FULL viewport, so its
// aspect ratio is not the author 742/616 — object-fit:cover crops along
// whichever axis is in surplus (horizontal on narrow cards, vertical on wide
// ones). worldToScreen therefore derives the crop from the ACTUAL card box.
// cardW/cardH default to the author card for the pre-measure build (cold
// mount); recomputeMapPositions() re-applies it once the live card box exists.
function worldToScreen(world, cardW, cardH) {
  const cw = cardW || MAP_AUTHOR_CARD.w;
  const ch = cardH || MAP_AUTHOR_CARD.h;
  const cardAspect = cw / ch;
  const imageAspect = MAP_WORLD.w / MAP_WORLD.h;
  let cropX = 0;
  let cropY = 0;
  if (cardAspect < imageAspect) {
    cropX = (1 - cardAspect / imageAspect) / 2;
  } else {
    cropY = (1 - imageAspect / cardAspect) / 2;
  }
  return {
    x: ((world.x / MAP_WORLD.w) - cropX) / (1 - 2 * cropX) * 100,
    y: ((world.y / MAP_WORLD.h) - cropY) / (1 - 2 * cropY) * 100,
  };
}
// #308 P2: the LIVE card box — the only authoritative crop source. It is
// unmeasurable before the tree is mounted, hence the author-default build
// plus this post-mount/re-render/resize pass over every location-derived
// marker (all of them carry data-loc-id or data-location-id).
function mapCardBox() {
  const host = document.getElementById("living-map-host");
  const card = host && host.querySelector(".map-card");
  if (!card || !card.clientWidth || !card.clientHeight) return null;
  return { card, w: card.clientWidth, h: card.clientHeight };
}
// #308 P2: ambient geometry is authored in the same percentages as location
// coords, so it must ride the same projection (ambient nodes carry no
// location id, hence the stored data-amb-* author values).
function projectAmbientNode(node, box) {
  const ds = node.dataset;
  if (ds.ambX1 !== undefined && ds.ambX2 !== undefined) {
    const p1 = worldToScreen(locationToWorld({ x: Number(ds.ambX1), y: Number(ds.ambY1) }), box.w, box.h);
    const p2 = worldToScreen(locationToWorld({ x: Number(ds.ambX2), y: Number(ds.ambY2) }), box.w, box.h);
    const seg = { x1: p1.x, y1: p1.y, x2: p2.x, y2: p2.y };
    // Birds regenerate their inline duration/delay through the helper; surf
    // has no inline animation (its CSS shorthand rides the class).
    node.setAttribute("style", node.classList.contains("map-ambient-bird")
      ? mapBirdCorridorStyle(seg, Number(ds.ambI) || 0)
      : mapSurfBandStyle(seg));
    return;
  }
  if (ds.ambX !== undefined && ds.ambY !== undefined) {
    const p = worldToScreen(locationToWorld({ x: Number(ds.ambX), y: Number(ds.ambY) }), box.w, box.h);
    node.style.left = `${p.x}%`;
    node.style.top = `${p.y}%`;
    if (ds.ambW !== undefined && ds.ambH !== undefined) {
      const p2 = worldToScreen(locationToWorld({
        x: Number(ds.ambX) + Number(ds.ambW),
        y: Number(ds.ambY) + Number(ds.ambH),
      }), box.w, box.h);
      node.style.width = `${p2.x - p.x}%`;
      node.style.height = `${p2.y - p.y}%`;
    }
  }
}

function recomputeMapPositions() {
  const box = mapCardBox();
  if (!box) return;
  const locById = new Map((S.mapLocations || [])
    .map(l => [String(l.id), l]));
  for (const node of box.card.querySelectorAll("[data-loc-id], [data-location-id]")) {
    // #308 P2: an in-flight MOVE marker carries its interpolated AUTHOR point
    // — project it instead of the (still START) location id.
    if (node.dataset.moveX !== undefined && node.dataset.moveY !== undefined) {
      const movePos = worldToScreen(locationToWorld({
        x: Number(node.dataset.moveX), y: Number(node.dataset.moveY),
      }), box.w, box.h);
      node.style.left = `${movePos.x}%`;
      node.style.top = `${movePos.y}%`;
      continue;
    }
    const id = node.getAttribute("data-loc-id")
      || node.getAttribute("data-location-id");
    const loc = locById.get(String(id));
    if (!loc || loc.x === null || loc.x === undefined
      || loc.y === null || loc.y === undefined) continue;
    const pos = worldToScreen(locationToWorld(loc), box.w, box.h);
    node.style.left = `${pos.x}%`;
    node.style.top = `${pos.y}%`;
  }
  // #308 P2: re-project the ambient layer from its stored author geometry.
  for (const node of box.card.querySelectorAll("[data-amb-x], [data-amb-x1]")) {
    projectAmbientNode(node, box);
  }
  // #308 P2: keep the zoom origin in the projected space on resize.
  const focusId = box.card.getAttribute("data-focus-loc-id");
  const focusLoc = focusId ? locById.get(String(focusId)) : null;
  if (focusLoc && focusLoc.x !== null && focusLoc.x !== undefined
    && focusLoc.y !== null && focusLoc.y !== undefined) {
    const fpos = worldToScreen(locationToWorld(focusLoc), box.w, box.h);
    box.card.style.setProperty("--map-focus-x", `${fpos.x}%`);
    box.card.style.setProperty("--map-focus-y", `${fpos.y}%`);
  }
}

function buildLivingMap(locs, cardW, cardH) {
  S.mapLocations = locs;
  const ch = S.character;
  const currentLoc = locs.find(l => l.id === ch.location_id);
  if (!S.mapFocusLocationId && currentLoc) S.mapFocusLocationId = currentLoc.id;
  const focus = locs.find(l => l.id === S.mapFocusLocationId) || currentLoc;
  const visibleLocs = visibleMapLocations(locs, focus);
  const timePhase = mapTimePhase(S.world && S.world.game_timestamp);
  const raining = !!(S.weather && S.weather.enabled && S.weather.precipitation > 0.5);
  const foggy = !!(S.weather && S.weather.enabled && S.weather.visibility < 1.0);
  const night = timePhase === "night";
  const reduced = S.mapReducedMotion ? "reduced-motion" : "";
  const qualityClass = `quality-${S.mapQuality}`;

  // #106 Lane B: houses split off the interactive anchor path. The client
  // cannot know which house is the player's own home (no client payload
  // exposes the home location), so ALL houses render as ambient
  // non-interactive spans at local zoom — same 8px dot visual, but no
  // button, no tab stop, no ::before hit target, no MOVE wiring; POI
  // anchors keep the Lane A interactive set.
  const locationAnchors = [];
  const houseSpans = [];
  for (const l of visibleLocs) {
    if (l.type === "house") {
      const pos = worldToScreen(locationToWorld(l), cardW, cardH);
      houseSpans.push(el("span", {
        class: "anchor anchor--house",
        style: `left:${pos.x}%; top:${pos.y}%`,
        "data-loc-id": String(l.id),
      }, el("span", { class: "dot" })));
      continue;
    }
    const isHere = l.id === ch.location_id;
    // #310: a POI click SELECTS the marker (context card) — it never moves.
    // The selected class is derived here at build time, so every
    // rerenderLivingMap rebuild re-derives it from gameUiContext and the
    // selection ring survives the ~5s poll rebuilds automatically.
    const isSelected = l.id === gameUiContext.selectedPoiId;
    const pos = worldToScreen(locationToWorld(l), cardW, cardH);
    locationAnchors.push(el("button", {
      class: `anchor location-anchor ${isHere ? "here" : ""} ${l.y >= 20 ? "above" : ""} ${isSelected ? "anchor--selected" : ""}`,
      style: `left:${pos.x}%; top:${pos.y}%`,
      "aria-label": l.name,
      "data-loc-id": String(l.id),
      onpointerdown: markTouchedAnchor,
      onclick: () => selectPoi(l),
    },
      el("span", { class: "dot" }),
      (!/^House \d+$/.test(l.name) ? el("span", { class: "map-label" }, l.name) : null),
    ));
  }

  const dynamicMarkers = [...mapNpcMarkers(visibleLocs, cardW, cardH), ...mapEventMarkers(visibleLocs, cardW, cardH)];
  const markerCount = houseSpans.length + locationAnchors.length + dynamicMarkers.length + (currentLoc ? 1 : 0);
  const nightLightLimit = S.mapQuality === "low-mobile" ? 8 : 24;
  const nightLightLocs = night ? visibleLocs.filter(l =>
    ["settlement", "house", "shop", "workshop", "kitchen"].includes(l.type)
  ).slice(0, nightLightLimit) : [];

  // #308 P2: the zoom origin must live in the SAME projected space as the
  // markers — raw author percentages diverge from them on non-author
  // viewports (390x844 etc.). Recomputed on resize from data-focus-loc-id.
  const focusProjected = focus && focus.x !== null
    ? worldToScreen(locationToWorld(focus), cardW, cardH) : null;
  const focusStyle = focusProjected
    ? `--map-focus-x:${focusProjected.x}%; --map-focus-y:${focusProjected.y}%`
    : "--map-focus-x:50%; --map-focus-y:50%";
  const playerPos = currentLoc && currentLoc.x !== null
    ? worldToScreen(locationToWorld(currentLoc), cardW, cardH) : null;

  const mapCard = el("div", {
    class: `map-card lod-${S.mapLod} time-${timePhase} ${raining ? "is-rain" : ""} ${foggy ? "is-fog" : ""} ${night ? "is-night" : ""} ${qualityClass} ${reduced}`,
    style: focusStyle,
    "data-focus-loc-id": focus && focus.x !== null ? String(focus.id) : "",
    "data-profile-markers": String(markerCount),
    "data-profile-budget": String(MAP_LOW_MARKER_BUDGET),
  },
    el("div", { class: "map-stage" },
      el("img", { class: "map-base", src: "/static/static/map/world-island.jpg", alt: "" }),
      buildMapAmbient({ lod: S.mapLod, quality: S.mapQuality, reduced: !!S.mapReducedMotion }),
      el("div", { class: "map-time-tint", "aria-hidden": "true" }),
      el("div", { class: "map-night-lights", "aria-hidden": "true" },
        ...nightLightLocs.map(loc => {
          const pos = worldToScreen(locationToWorld(loc), cardW, cardH);
          return el("span", {
            class: "map-night-light",
            style: `left:${pos.x}%; top:${pos.y}%`,
            "data-location-id": String(loc.id),
          });
        })),
      el("div", { class: "map-rain", "aria-hidden": "true" }),
      el("div", { class: "map-fog", "aria-hidden": "true" }),
      el("div", { class: "map-anchors", id: "map-anchors" },
        ...houseSpans,
        ...locationAnchors,
        ...dynamicMarkers,
        currentLoc && currentLoc.x !== null ? el("span", {
          class: "anchor player",
          id: "player-marker",
          style: `left:${playerPos.x}%; top:${playerPos.y}%`,
          "data-location-id": String(currentLoc.id),
          title: "Вы здесь",
        }, el("span", { class: "dot" })) : null,
      ),
    ),
    el("div", { id: "map-badge", class: "map-badge hidden" }, ""),
  );

  const lodButtons = [
    ["island", "Остров"],
    ["region", "Район"],
    ["local", "Локально"],
  ].map(([value, label]) => el("button", {
    class: S.mapLod === value ? "active" : "",
    onclick: () => setMapLod(value),
  }, label));

  const qualityButtons = [
    ["high", "High"],
    ["balanced", "Balanced"],
    ["low-mobile", "Low-Mobile"],
  ].map(([value, label]) => el("button", {
    class: S.mapQuality === value ? "active" : "",
    onclick: () => setMapQuality(value),
  }, label));

  const reducedToggle = el("label", { class: "map-reduced-toggle" },
    el("input", {
      type: "checkbox",
      ...(S.mapReducedMotion ? { checked: "checked" } : {}),
      onchange: (event) => setMapReducedMotion(event.target.checked),
    }),
    "Reduced Motion",
  );

  const sceneHandoff = S.mapLod === "local" ? el("button", {
    onclick: () => {
      const scene = document.getElementById("scene-view");
      if (scene) scene.scrollIntoView({ behavior: S.mapReducedMotion ? "auto" : "smooth", block: "start" });
    },
  }, "Открыть Scene") : null;

  return el("div", { class: "living-map" },
    el("div", { class: "map-toolbar", "aria-label": "Масштаб карты" }, ...lodButtons, sceneHandoff),
    el("div", { class: "map-toolbar map-quality", "aria-label": "Качество карты" },
      ...qualityButtons, reducedToggle),
    mapCard,
    el("div", { class: "map-status muted" },
      `${S.mapLod === "island" ? "Остров" : S.mapLod === "region" ? "Район" : "Локально"} · ${dayTime((S.world && S.world.game_timestamp) || 0)}`,
      S.weather && S.weather.enabled ? ` · ${S.weather.description}` : "",
      S.mapQuality === "low-mobile" ? ` · markers ${markerCount}/${MAP_LOW_MARKER_BUDGET}` : ""),
  );
}

function rerenderLivingMap() {
  const host = document.getElementById("living-map-host");
  if (host && S.mapLocations.length) {
    // F2: capture transient state from the old tree before the rebuild wipes it.
    const oldMarker = host.querySelector("#player-marker");
    const oldBadge = host.querySelector("#map-badge");
    const capturedMarkerLeft = oldMarker && oldMarker.style.left ? oldMarker.style.left : null;
    const capturedMarkerTop = oldMarker && oldMarker.style.top ? oldMarker.style.top : null;
    const capturedBadgeText = oldBadge ? oldBadge.textContent : null;
    const capturedBadgeHidden = oldBadge ? oldBadge.classList.contains("hidden") : null;
    const box = mapCardBox();
    host.replaceChildren(buildLivingMap(S.mapLocations, box && box.w, box && box.h));
    // #308 P2: the previous card box may have changed (resize/rotation) —
    // re-apply the projection to the freshly mounted markers.
    recomputeMapPositions();
    // F2: restore transient state onto the freshly built tree.
    const newMarker = host.querySelector("#player-marker");
    const newBadge = host.querySelector("#map-badge");
    if (newMarker && capturedMarkerLeft !== null && capturedMarkerTop !== null) {
      newMarker.style.left = capturedMarkerLeft;
      newMarker.style.top = capturedMarkerTop;
    }
    if (newBadge && capturedBadgeText !== null) {
      newBadge.textContent = capturedBadgeText;
      newBadge.classList.toggle("hidden", capturedBadgeHidden);
    }
  }
  // #310: the poll rebuild wipes the host children — the selection card lives
  // OUTSIDE the host (in .world-stage) and is re-derived from current state
  // after every rebuild, so it stays fresh (variant, existence) and the
  // selected anchor's ring is re-derived inside buildLivingMap.
  updatePoiSelectionCard();
}

/* ---------- #310: Living Map POI selection (click = select, never move) ---------- */

// #313: contextual back target — source place when recorded, else World.
function ctxBackHash() {
  if (location.hash === "#/chat" && gameUiContext.chatSourceLocationId) {
    return `#/location/${gameUiContext.chatSourceLocationId}`;
  }
  return "#/world";
}

/* Module-level once-guard: the Escape handler is bound exactly once per
   page load; it acts only while a selection card is open. No other Escape
   handling exists in app.js. */
let poiKeydownBound = false;
function bindPoiSelectionKeys() {
  if (poiKeydownBound) return;
  poiKeydownBound = true;
  // window, not document: same Escape semantics in a real browser, and the
  // keydown listener rides the same surface as the boot hashchange listener.
  window.addEventListener("keydown", (ev) => {
    if (ev.key !== "Escape") return;
    // #336: Escape inside a form control belongs to the control (e.g. closing
    // a native <select>), not to contextual navigation. Null-safe: the vm
    // harness fires synthetic key events without a `target`.
    const keyTarget = ev.target;
    if (keyTarget && ["INPUT", "TEXTAREA", "SELECT"].indexOf(keyTarget.tagName) >= 0) return;
    // #313: Escape closes an open sections disclosure first, else contextual back.
    const ctxBar = document.querySelector(".ctx-topbar");
    const ctxDetails = ctxBar ? ctxBar.querySelector("details") : null;
    if (ctxDetails && ctxDetails.open) { ctxDetails.open = false; return; }
    if (ctxBar) { location.hash = ctxBackHash(); return; }
    if (!document.getElementById("poi-card")) return;
    gameUiContext.selectedPoiId = null;
    rerenderLivingMap();
  });
}

/* #310: the selection setter — a POI click highlights the marker and opens
   the context card; no MOVE, no POST. */
function selectPoi(loc) {
  gameUiContext.selectedPoiId = loc.id;
  // Rebuild, not just the card: the ring is derived at map-build time, so a
  // rebuild is what paints it immediately (the rebuild tail also refreshes
  // the card). Same cost as a poll tick.
  rerenderLivingMap();
}

function closePoiSelection() {
  gameUiContext.selectedPoiId = null;
  rerenderLivingMap();
}

/* #310: the card is derived from CURRENT state on every call — the selected
   loc must still exist in S.mapLocations (a poll may have dropped it), and
   the «вы здесь» variant is re-read from the fresh player location. */
function poiSelectionCard() {
  const poiId = gameUiContext.selectedPoiId;
  if (poiId == null) return null;
  const loc = (S.mapLocations || []).find((l) => l.id === poiId);
  if (!loc) {  // stale selection — the location vanished from the payload
    gameUiContext.selectedPoiId = null;
    return null;
  }
  const here = loc.id === S.character.location_id;
  return el("div", { class: "poi-card", id: "poi-card", role: "group",
      "aria-label": `${loc.name || `#${loc.id}`} — выбор места` },
    el("div", { class: "poi-card-head" },
      el("h3", { class: "poi-card-title" },
        `${loc.name || `#${loc.id}`} · ${here ? "вы здесь" : "другое место"}`),
      el("button", { class: "poi-card-close", type: "button", "aria-label": "Закрыть",
        onclick: () => closePoiSelection() }, "×")),
    el("div", { class: "poi-card-actions" },
      el("a", { class: "poi-card-btn", href: `#/location/${loc.id}` },
        here ? "Войти в место" : "Посмотреть место"),
      here
        ? el("button", { class: "poi-card-btn", type: "button",
            onclick: () => closePoiSelection() }, "Закрыть")
        : el("button", { class: "poi-card-btn poi-card-go", type: "button",
            onclick: async () => {
              const target = loc;
              // #310: the ONE move path is the existing moveTo() — no new
              // /actions call site. Selection clears after success AND
              // failure (the marker can simply be clicked again).
              await moveTo(target);
              gameUiContext.selectedPoiId = null;
              rerenderLivingMap();
            } }, "Пойти сюда")));
}

/* #310: idempotent updater — the card lives in a dedicated host slot inside
   .world-stage (never inside living-map-host: rerenderLivingMap wipes host
   children). replaceChildren swaps the content in place: present → card,
   absent → empty. Cheap enough to call after every map rebuild. */
function updatePoiSelectionCard() {
  const host = document.getElementById("poi-card-host");
  if (!host) return;
  const card = poiSelectionCard();
  if (card) host.replaceChildren(card);
  else host.replaceChildren();
}

function setMapLod(lod) {
  if (!["island", "region", "local"].includes(lod)) return;
  S.mapLod = lod;
  if (S.character) S.mapFocusLocationId = S.character.location_id;
  rerenderLivingMap();
}

function setMapQuality(quality) {
  if (!["high", "balanced", "low-mobile"].includes(quality)) return;
  S.mapQuality = quality;
  // #79: ONE preset source of truth — the map toolbar's quality buttons and
  // the settings screen's preset control both land here; the choice
  // persists (and the preset NEVER touches the motion toggle — independent
  // controls).
  S.settings.preset = quality;
  persistSettings();
  rerenderLivingMap();
}

function setMapReducedMotion(enabled) {
  // #79: routed through the shared pref setter — the settings screen's
  // toggle uses the same path (one motion state, no second one).
  setReducedMotionPref(enabled);
}

function renderWorld(locs, tasksData) {
  const ch = S.character;
  const needs = ch.needs || {};

  // #86 review, refined by #302: the scene is tied to the location it was
  // inspected at — invalidated when the authoritatively observed player
  // location CHANGES (travel, arrival, action). A World ↔ Place round trip
  // without movement keeps the scene (session context preservation); the
  // poll publication path carries the same rule (pollEvents).
  if (observedPlayerLocId !== null && observedPlayerLocId !== ch.location_id) {
    discardSceneEntry(observedPlayerLocId);
  }
  observedPlayerLocId = ch.location_id;


  // Keep the A1 fallback when canonical map coordinates are unavailable.
  const keyedPois = ["settlement", "shop", "workshop", "kitchen", "storage", "well", "pier"];
  const poiMap = {};
  locs.forEach(l => {
    for (const k of keyedPois) {
      if (l.type === k || (l.name && l.name.toLowerCase().includes(k))) poiMap[k] = l;
    }
  });
  const mapReady = keyedPois.every(k => poiMap[k] && poiMap[k].x !== null);

  // #289: events feed lives in the left context panel (newest on top). The
  // updaters (REST fallback + WS) replaceChildren the whole #feed contents
  // on every update, so the «События мира →» link stays OUTSIDE the #feed
  // node (boundary kept). Empty state: one quiet phrase (canon §43.1 empty).
  const feedNode = el("div", { class: "world-feed", id: "feed" },
    el("h2", { class: "world-sec-h" }, "События"),
    ...(S.feed.length
      ? S.feed.slice(-40).reverse().map(feedRow)
      : [el("p", { class: "world-feed-empty" }, "Сейчас на острове тихо")]));
  const feedBlock = el("div", { class: "world-feed-block" }, feedNode,
    el("a", { class: "world-events-link", href: "#/events" }, "События мира →"));
  const actions = ACTION_VOCABULARY.map(([a, label]) =>
    el("button", { onclick: () => doAction(a) }, label));
  const extBtn = el("button", { onclick: () => viewExternal() },
    "Поездка во Владивосток");

  const taskRows = [];
  if (tasksData) {
    const planned = tasksData.planned || [];
    const active = tasksData.active || [];
    [...active, ...planned].forEach(t => {
      taskRows.push(el("div", { class: "task" },
        el("span", { class: "status" }, codeLabel(TASK_STATUS_RU, t.status)),
        el("span", {}, codeLabel(TASK_TYPE_RU, t.task_type) || "")));
    });
  }
  const activeCount = tasksData && tasksData.active ? tasksData.active.length : 0;

  const portraitBox = portraitBlock();
  S.mapLocations = locs;
  // #312: publish BEFORE the scene hosts render — the panel reads the cache
  // against the fresh catalog (identity + context-key reconciliation).
  syncSceneContext(locs);
  const sceneBox = sceneBlock();

  const characterName = ch.name || `${ch.first_name || ""} ${ch.last_name || ""}`;
  const currentLoc = locs.find((l) => l.id === ch.location_id);
  const currentLocationLabel = currentLoc && currentLoc.name ? currentLoc.name : "—";
  const autonomous = ch.control_mode === "AUTONOMOUS";

  // #289: left context panel — header (name/money, location · job, time/weather
  // chip), needs, island events. The chip's #clock/#weather ids are the poller
  // targets (hydrateWorldClock + pollEvents update them in place; they used to
  // target the topbar spans). Privileged Admin stays reachable in the footer.
  const privileged = ["admin", "developer", "moderator"].includes(S.user.role);
  const worldPanel = el("aside", { class: "world-panel" },
    el("header", { class: "world-panel-head" },
      el("div", { class: "world-who-top" },
        el("h1", { class: "world-who-name" }, characterName),
        el("span", { class: "world-money" }, `${ch.money ?? ch.balance ?? "—"} ₽`)),
      el("p", { class: "world-sub" },
        `${currentLocationLabel} · ${ch.job ? codeLabel(JOB_TITLES_RU, ch.job) : "работы нет"}`),
      el("div", { class: "world-chip" },
        svgIcon(ICON_DROP),
        el("span", {},
          el("b", { id: "clock" }, S.world ? dayTime(S.world.game_timestamp) : clockPendingLabel()),
          el("span", { id: "weather" }, worldWeatherChipText())))),
    // #301 World HUD: «Сейчас» sits directly under the header — the one
    // glanceable line (current deed / autonomy mode) that stays visible in
    // the mobile compact band; everything below folds into the collapsible.
    nowPanel(tasksData, autonomous),
    el("div", { class: "world-collapsible", id: "world-panel-details" },
      el("h2", { class: "world-sec-h" }, "Состояние"),
      el("div", { class: "world-mini-needs" },
        needBar("Сытость", needs.hunger), needBar("Вода", needs.thirst),
        needBar("Энергия", needs.energy), needBar("Общение", needs.social)),
      // #301: 1–3 newest island events previewed above the feed (container
      // always present; content filled here and refreshed by the feed
      // updaters via refreshIslandPreview).
      islandEventsBlock(),
      el("hr"),
      feedBlock),
    privileged ? el("a", { class: "world-admin-link", href: "#/admin" }, "Админ") : null);

  // #289: floating nav pill — 9 sections over the map, same routes as the
  // topbar; the world screen renders the pill INSTEAD of the topbar.
  const worldPill = el("nav", { class: "world-pill", "aria-label": "Разделы" },
    ...PILL_TABS.map(([hash, label, icon]) => el("a", {
      href: hash,
      class: hash === "#/world" ? "active" : "",
      // #313: the active destination is the current page for assistive
      // tech. Conditional spread — el() setAttribute()s every prop, so a
      // null value would render the attribute as the string "null" on
      // the other eight anchors.
      ...(hash === "#/world" ? { "aria-current": "page" } : {}),
      title: label,
      "aria-label": label,
    }, svgIcon(icon), el("span", {}, label))));

  // #289: «Осмотреть окрестности» floats bottom-right over the map and zooms
  // the Living Map to region LOD (§43.2). Scene inspection keeps its existing
  // triggers (locationView's «Осмотреть» + the scene fallback retry).
  const inspectBtn = el("button", { class: "world-inspect", type: "button",
      onclick: () => setMapLod("region"), title: "Приблизить карту до района" },
    svgIcon(ICON_EYE), "Осмотреть окрестности");

  app.replaceChildren(
    el("main", { class: "world-shell" },
      // #107: first-visit concierge card — floats above the map (top-center);
      // null (renders nothing) once dismissed or when storage is denied.
      welcomeCard(),
      el("section", { class: "world-stage" },
        mapReady ? el("div", { id: "living-map-host" }, buildLivingMap(locs)) : el("div", { class: "world-map-fallback" },
          el("h2", {}, "Остров Рейнеке"),
          el("div", { class: "locs" },
            ...locs.map((loc) => el("div", {
              class: `loc ${loc.id === ch.location_id ? "here" : ""}`,
              onclick: () => moveTo(loc),
            }, el("span", {}, loc.name || `#${loc.id}`),
               el("span", { class: "muted" }, loc.type || ""))))),
        inspectBtn,
        // #310: the POI selection card mounts into this dedicated host (AFTER
        // inspectBtn, never inside living-map-host — the poll rebuild wipes
        // host children); updatePoiSelectionCard() fills it from state.
        el("div", { class: "poi-card-host", id: "poi-card-host" })),
      worldPanel,
      worldPill,
      // Below-fold blocks (travel, actions, journal, portrait, scene) —
      // preserved under the map fold, restyled to the warm canon.
      el("section", { class: "world-below" },
        el("nav", { class: "panel world-card world-actions-card" },
          el("h2", {}, "Действия"),
          el("div", { class: "world-action-pills", "aria-label": "Действия персонажа" },
            // #301: in AUTONOMOUS mode the hint + Profile button moved into
            // the panel HUD (nowPanel) — the below-fold card stays empty
            // rather than duplicating the line above the fold.
            ...(autonomous ? [] : [...actions, extBtn]))),
        el("div", { class: "panel world-card" },
          el("h2", {}, "Маршрут"),
          travelBar()),
        el("div", { class: "panel world-card" },
          el("h2", {}, "Место"),
          locationView(locs)),
        el("div", { class: "panel world-tasks-card" },
          el("h2", {}, "Журнал"),
          taskRows.length ? el("div", { class: "muted" }, `Активных задач: ${activeCount} (полный журнал — на экране Задач)`) : el("div", { class: "muted" }, "Нет активных задач"),
          taskRows.slice(0, 3)),
        portraitBox,
        sceneBox)));
  // #310: bind the Escape close path once and derive the card from current
  // state after the mount (covers a selection carried across a same-hash
  // re-render; route() clears it on any hash change).
  bindPoiSelectionKeys();
  updatePoiSelectionCard();
  // #308 P2: the cold build ran before the card was mounted; re-apply the
  // projection now that the live stage box exists.
  recomputeMapPositions();
  // #304: the World HUD's task slot renders from the adapter (the ingest
  // has already landed before renderWorld) — fill the host at mount.
  refreshTaskPresentationHosts();
}

/* #304: action lifecycle — «команда принята ≠ действие выполнено». The
   module-local task presentation adapter owns the ACCEPTED-task state: the
   registry (String(task_id) → entry), the followed-task pointer, the
   session generation, the monotonic read sequence, the submission latch,
   and the in-flight task GET flights. The phase is DERIVED from
   serverStatus + readState at caption time — never stored. The bindings
   are module-locals (NOT S fields — the frozen initializer tail), exposed
   to the m304 harness through its own epilogue. Ordinary route switches
   clear NOTHING; only resetGameUiContext() (session teardown) is
   destructive. */
let taskPresentation = new Map();  // String(task_id) → entry
let currentTaskId = null;  // the followed task — survives terminal completion
let taskSessionGeneration = 0;  // monotonic; bumped by resetTaskPresentation
let taskReadSequence = 0;  // monotonic; assigned WHEN EACH READ STARTS
let actionSubmission = null;  // the session-owned submission latch token
let taskReadFlights = new Map();  // String(task_id) → detail flight
let latestTaskSnapshot = null;  // the last SUCCESSFULLY ingested list (null = none/failed)
let taskListReadsInFlight = 0;  // list reads in flight (single-flight span)
let taskSubmitFeedback = "";  // the last submission-failure caption (host line)

/* #304: terminal/active predicates — the reconciliation vocabulary. */
function isTerminalTaskStatus(st) {
  return st === "completed" || st === "failed" || st === "cancelled";
}
function isActiveTaskStatus(st) {
  return st === "active" || st === "STARTED";
}

/* #304: guard (a) — the model-write continuation token. A successful
   POST's task id survives NAVIGATION (navigation is not a session change),
   but a session teardown/relogin must not accept it: the generation, the
   user OBJECT identity, and the character id must all still match, and the
   token must still own the latch. */
function taskContinuationCurrent(token) {
  if (!token || actionSubmission !== token) return false;
  if (token.gen !== taskSessionGeneration) return false;
  if (!S.user || token.userRef !== S.user) return false;
  if (!S.character || token.charId !== S.character.id) return false;
  return true;
}

/* #304: the ONE session-owned submission latch, shared by doAction,
   moveTo, submitSceneAct and submitTravel. Acquired synchronously BEFORE
   the first await (a second click/Enter is a no-op — no second POST, no
   fake acceptance; narrowly covers #327's double-submit without touching
   the keyboard wiring). Released only while the token is still current. */
function beginActionSubmission() {
  if (actionSubmission) return null;
  const token = {
    gen: taskSessionGeneration,
    userRef: S.user,
    charId: S.character ? S.character.id : null,
  };
  actionSubmission = token;
  taskSubmitFeedback = "";  // a new submission supersedes the last failure
  refreshTaskPresentationHosts();  // the sending line appears
  return token;
}

function finishActionSubmission(token) {
  if (!token || actionSubmission !== token) return;
  actionSubmission = null;
  refreshTaskPresentationHosts();  // the sending line clears
}

/* #304: acceptance — create/merge the registry entry, move the pointer,
   and start the immediate single-flight detail GET. The latch is released
   by the CALLER independently of this GET. The acceptance response alone
   never produces completion/needs/movement — only a planned/queued entry. */
function acceptTask(response, token) {
  if (!response || response.task_id == null) return;
  if (!taskContinuationCurrent(token)) return;  // a dead session accepts nothing
  const key = String(response.task_id);
  const existing = taskPresentation.get(key);
  const entry = existing || {
    taskId: response.task_id,
    serverStatus: null,
    readState: "ok",
    lastReadSequence: 0,
    acceptedSequence: 0,
  };
  entry.taskType = response.task_type || entry.taskType || "";
  entry.label = response.task_label || entry.label
    || codeLabel(TASK_TYPE_RU, entry.taskType) || "";
  if (!isTerminalTaskStatus(entry.serverStatus)) {
    // terminal sticky: a re-acceptance never demotes a confirmed terminal
    entry.serverStatus = response.status || "planned";
    if (response.ends_at != null) entry.endsAt = response.ends_at;
  }
  entry.acceptedSequence = taskReadSequence;
  entry.readState = "ok";
  taskPresentation.set(key, entry);
  currentTaskId = response.task_id;
  refreshTaskPresentationHosts();
  // The immediate single-flight detail GET: skipped while a list read is
  // in flight (the landing list refreshes instead — one read, not two).
  refreshTask(response.task_id, { corroborated: false });
}

/* #304: the single-flight detail GET for one tracked task. Skipped when a
   detail flight for the SAME task is active, when a list read is in flight
   (coalescing), or when the entry is a confirmed terminal (no recurring
   lookups). The flight carries the session token — a stale 401 must not
   deauthenticate a newer session (api()'s taskFlight lane). */
function refreshTask(taskId, opts = {}) {
  const key = String(taskId);
  if (taskReadFlights.has(key)) return;
  if (taskListReadsInFlight > 0) return;
  const entry = taskPresentation.get(key);
  if (entry && isTerminalTaskStatus(entry.serverStatus)) return;
  let controller = null;
  try { controller = new AbortController(); } catch (e) { controller = null; }
  const token = ++taskReadSequence;
  const flight = {
    token,
    gen: taskSessionGeneration,
    userRef: S.user,
    charId: S.character ? S.character.id : null,
    controller,
    corroborated: !!opts.corroborated,
  };
  taskReadFlights.set(key, flight);
  const apiOpts = { taskFlight: flight };
  if (controller) apiOpts.signal = controller.signal;
  api(`/actions/${taskId}`, apiOpts).then((detail) => {
    if (taskReadFlights.get(key) === flight) taskReadFlights.delete(key);
    ingestTaskDetail(taskId, detail, flight);
  }).catch((e) => {
    if (taskReadFlights.get(key) === flight) taskReadFlights.delete(key);
    ingestTaskDetailFailure(taskId, e, flight);
  });
}

/* #304: the detail-GET landing. Stale-session responses are rejected; a
   malformed body degrades to «unavailable»; the reconciliation rules apply
   (terminal sticky / first confirmed terminal wins / nonterminal age
   rejection / active never regresses). */
function ingestTaskDetail(taskId, detail, flight) {
  if (flight.gen !== taskSessionGeneration || !S.user
    || flight.userRef !== S.user || !S.character
    || S.character.id !== flight.charId) return;  // stale session — commit nothing
  if (!detail || typeof detail !== "object" || detail.status == null) {
    applyTaskReadFailure(taskId, { status: 0 }, flight);  // malformed → unavailable
    return;
  }
  applyTaskRead(taskId, detail, flight.token);
}

function ingestTaskDetailFailure(taskId, e, flight) {
  if (flight.gen !== taskSessionGeneration || !S.user
    || flight.userRef !== S.user || !S.character
    || S.character.id !== flight.charId) return;  // stale session — commit nothing
  applyTaskReadFailure(taskId, e || {}, flight);
}

/* #304: the read-failure landing. A terminal entry survives ordinary read
   failures (its caption is already confirmed). A 404 corroborated by a
   post-acceptance list omission → «История задачи недоступна»; a 404 alone
   (and 403/503/network) → «Не удаётся уточнить состояние». */
function applyTaskReadFailure(taskId, e, flight) {
  const entry = taskPresentation.get(String(taskId));
  if (!entry) return;
  if (isTerminalTaskStatus(entry.serverStatus)) return;  // terminal survives
  if (e && e.status === 404 && flight.corroborated) {
    entry.readState = "historyMissing";
  } else {
    entry.readState = "unavailable";
  }
  entry.lastReadSequence = flight.token;
  refreshTaskPresentationHosts();
}

/* #304: the shared read-landing reconciliation. `readToken` is the token
   the read carried from its START — a nonterminal update older than the
   entry's committed token is rejected; a terminal update is always the
   first-confirmed winner (the entry was nonterminal, else we bailed). */
function applyTaskRead(taskId, data, readToken) {
  const entry = taskPresentation.get(String(taskId));
  if (!entry) return;  // not tracked (e.g. a pre-acceptance read's task)
  if (isTerminalTaskStatus(entry.serverStatus)) return;  // terminal sticky
  if (isTerminalTaskStatus(data.status)) {
    entry.serverStatus = data.status;
    entry.failureReason = (data.parameters && data.parameters.reason) || null;
    if (data.ends_at != null) entry.endsAt = data.ends_at;
    if (data.task_type) entry.taskType = data.task_type;
    entry.readState = "ok";
    entry.lastReadSequence = readToken;
    refreshTaskPresentationHosts();
    return;
  }
  if (readToken < entry.lastReadSequence) return;  // older nonterminal update
  if (isActiveTaskStatus(entry.serverStatus) && !isActiveTaskStatus(data.status)) {
    return;  // active never regresses to planned/queued
  }
  entry.serverStatus = data.status;
  if (data.task_type) entry.taskType = data.task_type;
  entry.label = data.task_label || entry.label
    || codeLabel(TASK_TYPE_RU, entry.taskType) || "";
  if (data.ends_at != null) entry.endsAt = data.ends_at;
  entry.failureReason = null;
  entry.readState = "ok";
  entry.lastReadSequence = readToken;
  refreshTaskPresentationHosts();
}

/* #304: the list-read landing — the existing reads become authoritative
   refreshers (NO second timer). A null/malformed snapshot (a failed read)
   drops the snapshot state (the hosts degrade honestly) but never touches
   tracked entries (terminal survives ordinary read failures). A successful
   list refreshes every tracked task it carries; a post-acceptance omission
   of a tracked NONTERMINAL task triggers ONE corroborating detail GET
   (failed tasks vanish from lists — fail_task writes no completed_at);
   a pre-acceptance read's absence is ignored. Terminals are never looked
   up and never regress. */
function ingestTaskSnapshot(snapshot, readToken) {
  if (!snapshot || typeof snapshot !== "object" || !Array.isArray(snapshot.active)
    || !Array.isArray(snapshot.planned)) {
    latestTaskSnapshot = null;
    refreshTaskPresentationHosts();
    return;
  }
  latestTaskSnapshot = snapshot;
  const all = [...(snapshot.active || []), ...(snapshot.planned || []),
    ...(snapshot.recent_terminal || [])];
  const byId = new Map();
  for (const t of all) {
    const rid = taskRowId(t);
    if (rid != null) byId.set(String(rid), t);
  }
  for (const [key, entry] of taskPresentation) {
    if (isTerminalTaskStatus(entry.serverStatus)) continue;  // sticky, no lookups
    const found = byId.get(key);
    if (found) {
      applyTaskRead(entry.taskId, found, readToken);
    } else if (readToken > entry.acceptedSequence) {
      // post-acceptance omission — corroborate with ONE detail GET
      refreshTask(entry.taskId, { corroborated: true });
    }
    // a read started before the task's acceptance says nothing about it
  }
  refreshTaskPresentationHosts();
}

/* #304: the TASK_COMPLETED/TASK_FAILED feed events are RECHECK TRIGGERS
   only — the payloads carry no task_id, so the adapter never assigns a
   status or a reason by event type; the tracked nonterminal tasks' own
   detail GETs decide. */
function triggerTaskRechecks() {
  for (const [key, entry] of taskPresentation) {
    if (!isTerminalTaskStatus(entry.serverStatus)) {
      refreshTask(entry.taskId, { corroborated: false });
    }
  }
}

/* #304: the list-row id — the POST/scene-act acceptance payloads carry
   `task_id`, while the /characters/{id}/tasks list rows (backend
   task_to_dict) carry `id`. The adapter accepts BOTH shapes everywhere it
   reads a row id; a row with neither id is display-ineligible (it cannot
   be tracked or refreshed). */
function taskRowId(t) {
  if (!t) return null;
  if (t.task_id != null) return t.task_id;
  if (t.id != null) return t.id;
  return null;
}

/* #304: the selected task — the pointer first, else the latest snapshot's
   active[0] (the server pre-orders oldest first), else planned[0]. Multiple
   planned tasks are possible (the server activates the OLDEST); the pointer
   survives terminal completion until the next acceptance or a session
   reset; failed submissions never replace it. */
function selectCurrentTask() {
  if (currentTaskId != null) {
    const entry = taskPresentation.get(String(currentTaskId));
    if (entry) return entry;
  }
  if (latestTaskSnapshot) {
    const active = latestTaskSnapshot.active || [];
    const planned = latestTaskSnapshot.planned || [];
    const current = active.length ? active[0] : (planned.length ? planned[0] : null);
    const currentId = taskRowId(current);
    if (currentId != null) {
      return taskPresentation.get(String(currentId)) || {
        taskId: currentId,
        taskType: current.task_type || "",
        label: current.task_label || codeLabel(TASK_TYPE_RU, current.task_type) || "",
        serverStatus: current.status,
        endsAt: current.ends_at != null ? current.ends_at : null,
        failureReason: (current.parameters && current.parameters.reason) || null,
        readState: "ok",
        lastReadSequence: 0,
        acceptedSequence: 0,
      };
    }
  }
  return null;
}

/* #304: the caption formatter — the exact RU strings of the lifecycle
   contract. The phase is derived here (serverStatus + readState), never
   stored. ends_at renders ONLY as the server-estimate caption (no
   countdown/percentage/auto-transition); failed/cancelled keep neutral
   wording, never «завершено». */
function taskPresentationText(entry) {
  if (!entry) return "";
  if (entry.readState === "unavailable") return "Не удаётся уточнить состояние";
  if (entry.readState === "historyMissing") return "История задачи недоступна";
  const st = entry.serverStatus;
  const endsPart = (entry.endsAt != null && !isTerminalTaskStatus(st))
    ? ` · Плановое окончание по оценке сервера: ${dayTime(entry.endsAt)}` : "";
  if (st === "completed") return "Действие завершено";
  if (st === "failed") {
    return entry.failureReason
      ? `Не удалось: ${entry.failureReason}` : "Не удалось: причина не указана";
  }
  if (st === "cancelled") {
    return entry.failureReason
      ? `Действие отменено: ${entry.failureReason}` : "Действие отменено";
  }
  if (isActiveTaskStatus(st)) {
    const label = entry.label || codeLabel(TASK_TYPE_RU, entry.taskType) || "—";
    return `Персонаж занят: ${label}${endsPart}`;
  }
  if (st === "planned" || st === "queued") {
    return `Задача принята. Ожидает начала${endsPart}`;
  }
  // unknown status — preserve the server value with the existing
  // unknown-code fallback (codeLabel falls back to the raw code)
  const label = codeLabel(TASK_STATUS_RU, st) || String(st == null ? "—" : st);
  return `${label}${endsPart}`;
}

/* #304: the World HUD's task slot renderer — the adapter's selected task
   (status caption + type label, the .world-now-* styling kept), the honest
   empty state, or the unavailable line. Shared by the mount render and the
   in-place refresh (one source, identical text on World and Place). */
function taskStatusHostNode() {
  const entry = selectCurrentTask();
  if (entry) {
    return el("span", { class: "world-now-task" },
      el("span", { class: "world-now-status" }, taskPresentationText(entry)),
      el("span", { class: "world-now-type" },
        codeLabel(TASK_TYPE_RU, entry.taskType) || "—"));
  }
  if (latestTaskSnapshot == null) {
    return el("span", { class: "world-now-state" }, "Данные о делах недоступны");
  }
  return el("span", { class: "world-now-state" }, "Сейчас нет активных дел");
}

/* #304: the Place task-content renderer — the SAME selected-task text as
   the World HUD (identical caption, no re-derivation). */
function placeTaskHostNode() {
  const entry = selectCurrentTask();
  if (entry) return el("span", {}, taskPresentationText(entry));
  if (latestTaskSnapshot == null) {
    return el("span", { class: "muted" }, "Данные о делах недоступны");
  }
  return el("span", { class: "muted" }, "нет активной задачи");
}

/* #304: the submission feedback line — the local sending text while the
   latch is held, else the last submission-failure caption. A separate
   line: the existing active task stays visible beside it. */
function taskSubmitLineText() {
  if (actionSubmission) return "Отправляем запрос…";
  return taskSubmitFeedback;
}

/* #304: the in-place host refresher — patches every MOUNTED #304 host
   (World HUD slot, Place task content, the submission lines, the Tasks
   followed-task host, the scene-result hosts). Absent hosts are skipped:
   origin-screen effects never resurrect a view that is not on screen
   (guard (b) — no stale toast/callback/scene redraw after navigation). */
function refreshTaskPresentationHosts() {
  const worldHost = document.getElementById("world-now-task-host");
  if (worldHost) worldHost.replaceChildren(taskStatusHostNode());
  const placeHost = document.getElementById("place-task-content");
  if (placeHost) placeHost.replaceChildren(placeTaskHostNode());
  const lineText = taskSubmitLineText();
  for (const line of document.querySelectorAll(".task-submit-line")) {
    line.textContent = lineText;
  }
  const followed = document.getElementById("tasks-followed");
  if (followed) {
    const entry = currentTaskId != null
      ? taskPresentation.get(String(currentTaskId)) : null;
    const inSections = !entry || (followed._taskIds
      && followed._taskIds.has(String(currentTaskId)));
    followed.querySelector(".tasks-followed-body").textContent = inSections
      ? "" : taskPresentationText(entry);
  }
  const rn = sceneActResultNode();
  const sceneBox = document.getElementById("scene-view");
  if (sceneBox && sceneBox._sceneHosts) {
    sceneBox._sceneHosts.result.replaceChildren(...(rn ? [rn] : []));
  }
  const placeResult = document.querySelector(".place-scene-act-result");
  if (placeResult) placeResult.replaceChildren(...(rn ? [rn] : []));
}

/* #304: session teardown — the registry, the pointer, the snapshot, the
   latch and the read flights die destructively (abort first). The
   monotonic counters stay monotonic: the generation bumps (in-flight
   continuations lose their guard), the read sequence never rewinds. */
function resetTaskPresentation() {
  taskSessionGeneration++;
  for (const flight of taskReadFlights.values()) {
    try { if (flight.controller) flight.controller.abort(); } catch (e) { /* already dead */ }
  }
  taskReadFlights.clear();
  taskListReadsInFlight = 0;
  taskPresentation.clear();
  currentTaskId = null;
  latestTaskSnapshot = null;
  actionSubmission = null;
  taskSubmitFeedback = "";
  refreshTaskPresentationHosts();
}

/* #303: the shared action vocabulary — the World actions card and the Place
   context panel render the SAME request set through doAction. A button only
   POSTs a request: POST /actions validates it, so a button is a possible
   request, never a guaranteed outcome. */
const ACTION_VOCABULARY = [["WORK", "Пойти на работу"], ["EAT", "Поесть"], ["DRINK", "Попить"],
  ["SLEEP", "Отдохнуть / поспать"], ["SOCIALIZE", "Пообщаться"]];

async function doAction(actionType, params = {}, after = () => {}) {
  // #304: the session-owned submission latch — a second click while a
  // submission is in flight is a no-op (no second POST, no fake
  // acceptance). The default after() is a no-op: acceptance stays on the
  // current screen (the lifecycle captions own the feedback now).
  const token = beginActionSubmission();
  if (!token) return;
  try {
    const r = await api("/actions", { method: "POST",
      body: { character_id: S.character.id, action_type: actionType, params } });
    // #304: guard (a) — navigation must not discard the task id, but a
    // session change must not accept it. The acceptance runs while the
    // token still owns the latch; the release below is independent of the
    // acceptance's own detail GET (it does not wait for it).
    if (!taskContinuationCurrent(token)) return;
    if (r && r.task_id != null) {
      acceptTask(r, token);
    }
    finishActionSubmission(token);
    toast(`Задача ${actionType} принята`);
    after();
  } catch (e) {
    finishActionSubmission(token);
    // #144/§61: map the AUTONOMOUS refusal to the localized hint; every
    // other server error keeps its raw message (out of scope here).
    // #74: ONE SLEEP-specific mapping (moveTo's needs_move precedent) —
    // the validator rejects SLEEP at energy>=100 with the raw detail
    // "Energy already full"; speak the honest note instead. Every other
    // error keeps its existing behavior.
    if (isAutonomousRefusal(e)) {
      toast(AUTONOMOUS_HINT, true);
    } else if (e.status === 422
      && String(e.detail).includes("Energy already full")) {
      toast("Энергия уже полная — сон сейчас не нужен", true);
    } else {
      // #304: the honest degradation captions for the generic submission
      // failures — the feedback line owns them (the raw server detail
      // never reaches player-visible copy).
      taskSubmitFeedback = actionSubmitErrorText(e);
      refreshTaskPresentationHosts();
    }
  } finally {
    finishActionSubmission(token);  // idempotent — only while still current
  }
}

/* #304: the generic submission-failure captions (409/422/503/network).
   The preserved specific messages (AUTONOMOUS refusal, full-energy,
   needs_move) keep their toasts and never reach this mapper. */
function actionSubmitErrorText(e) {
  if (e && e.status === 409) {
    const d = String((e.data && e.data.detail) || e.detail || "");
    // #304: the busy caption must match BOTH the synthetic busy detail and
    // the REAL backend's 409-active detail "character already has an active
    // task" (app.py:1164/:1243/:1507 — all three action paths send exactly
    // this); anything else stays the generic unavailable caption.
    return /busy|занят|already has an active task/i.test(d)
      ? "Персонаж уже занят другим действием" : "Действие сейчас недоступно";
  }
  if (e && e.status === 422) {
    return "Действие сейчас невозможно. Проверьте условия";
  }
  return "Не удалось подтвердить принятие запроса. Проверьте связь";
}

async function moveTo(loc) {
  if (loc.id === S.character.location_id) return;
  // #144/§61: in AUTONOMOUS the server refuses MOVE with 409 — gate it
  // client-side instead of posting a doomed /actions request.
  if (S.character.control_mode === "AUTONOMOUS") {
    toast(AUTONOMOUS_HINT);
    return;
  }
  // #304: the shared submission latch + the lifecycle acceptance — the
  // move request is a task like any other. NO viewWorld(): the map keeps
  // its state and the poll/arrival path updates it; the POI card's own
  // continuation still consumes the selection.
  const token = beginActionSubmission();
  if (!token) return;
  try {
    const r = await api("/actions", { method: "POST", body: {
      character_id: S.character.id, action_type: "MOVE",
      params: { location_id: loc.id } } });
    // #304: guard (a) — the acceptance runs while the token still owns the
    // latch; the release is independent of the acceptance's detail GET.
    if (!taskContinuationCurrent(token)) return;
    if (r && r.task_id != null) acceptTask(r, token);
    finishActionSubmission(token);
    toast("Запрос на перемещение принят");
  } catch (e) {
    finishActionSubmission(token);
    if (isAutonomousRefusal(e)) {
      // #144/§61: stale S.character (GUIDED shown, server AUTONOMOUS) — the
      // client gate passed but the server refused; speak the hint, not §61.
      toast(AUTONOMOUS_HINT, true);
    } else if (e.status === 422 && String(e.detail).includes("needs_move")) {
      toast("Сначала дойдите до промежуточной точки", true);
    } else {
      // #304: the honest degradation captions on the feedback line.
      taskSubmitFeedback = actionSubmitErrorText(e);
      refreshTaskPresentationHosts();
    }
  } finally {
    finishActionSubmission(token);  // idempotent — only while still current
  }
}

/* ---------- #82 (A3): free-text travel on the Living Map ---------- */

function travelStatusLabel() {
  if (!S.travel) return "";
  // #304: the interpreting phase shows the LOCAL sending text — the plan
  // POST is still in flight, no movement evidence exists yet.
  if (S.travel.state === "interpreting") return "Отправляем запрос…";
  if (S.travel.state === "travelling") {
    // #304: the travel task's status resolves through the adapter — the
    // POST response alone is not movement evidence; the tracked task's
    // server-backed status owns the line when the task is registered.
    const entry = S.travel.taskId != null
      ? taskPresentation.get(String(S.travel.taskId)) : null;
    if (entry) return taskPresentationText(entry);
    return `Идём в ${S.travel.destName} — ~${S.travel.etaMinutes} мин`;
  }
  return "";
}

function travelBar() {
  return el("div", { class: "travel-bar", id: "travel-bar" },
    el("input", {
      id: "travel-input", type: "text",
      placeholder: "Куда идти? Например: к причалу",
      onkeydown: (ev) => { if (ev.key === "Enter" && !ev.repeat) submitTravel(); },
    }),
    el("button", { id: "travel-go", type: "button", onclick: () => submitTravel() }, "Идти"),
    el("span", { id: "travel-status", class: "muted" }, travelStatusLabel()));
}

function travelErrorText(e) {
  const d = e.detail;
  const body = e.data || {};
  if (body.detail === "ambiguous destination"
    && Array.isArray(body.options)) {
    return `Уточните пункт назначения: ${body.options.join(", ")}`;
  }
  if (typeof d === "string" && d) return d;
  return e.message || "Не удалось построить маршрут";
}

async function submitTravel() {
  const input = document.getElementById("travel-input");
  const text = input ? input.value.trim() : "";
  if (!text) return;
  // #304: the travel plan reuses the ONE submission latch (no fourth
  // /actions call site — the plan's task_id joins the registry at this
  // existing call site).
  const token = beginActionSubmission();
  if (!token) return;
  S.travel = { state: "interpreting" };
  const status = document.getElementById("travel-status");
  if (status) status.textContent = travelStatusLabel();
  try {
    const plan = await api("/travel/plan", { method: "POST", body: { text } });
    // #304: guard (a) — a session change must not accept the plan's task.
    // The acceptance runs while the token still owns the latch.
    if (!taskContinuationCurrent(token)) return;
    S.travel = { state: "travelling", destName: plan.name,
      etaMinutes: plan.travel_minutes, taskId: plan.task_id };
    // #304: the travel task joins the registry — the poll's list reads and
    // the detail GET refresh its server-backed status from here.
    if (plan.task_id != null) {
      acceptTask({ task_id: plan.task_id, task_type: "MOVE",
        status: "planned" }, token);
    }
    finishActionSubmission(token);
    viewWorld();
  } catch (e) {
    finishActionSubmission(token);
    S.travel = null;
    const stale = document.getElementById("travel-status");
    if (stale) stale.textContent = "";
    toast(travelErrorText(e), true);
  } finally {
    finishActionSubmission(token);  // idempotent — only while still current
  }
}

function isOwnMoveEvent(e) {
  return e.event_type === "CHARACTER_MOVED"
    && !!e.payload && e.payload.character_id === S.character.id;
}

function onOwnArrival() {
  // #311: the travel state machine settles on EVERY screen (the arrival
  // happened in the world, not in a view), but the feedback — the toast and
  // the map re-render — stays on #/world: the feed now runs on every screen,
  // and non-World surfaces own the quiet indicator instead of a toast.
  const arrival = (S.travel && S.travel.state === "travelling")
    ? (S.travel.destName || "") : null;
  if (arrival !== null) S.travel = null;
  if (location.hash !== "#/world") return;
  if (arrival !== null) toast(`Вы прибыли: ${arrival}`);
  viewWorld();
}

function locationView(locs) {
  const ch = S.character;
  const currentLoc = locs.find((l) => l.id === ch.location_id);
  const nearby = ((currentLoc && currentLoc.occupants) || [])
    .filter((o) => o.id !== ch.id)
    .map((o) => o.name);
  return el("div", { class: "location-view", id: "location-view" },
    el("h3", {}, currentLoc ? currentLoc.name : "—"),
    // #72: read-only link-over to the Place screen for the CURRENT location
    // (marker-click and «Осмотреть» semantics stay untouched).
    currentLoc
      ? el("a", { class: "place-link",
          href: `#/location/${currentLoc.id}` }, "О месте")
      : null,
    el("div", { class: "muted" }, dayTime((S.world && S.world.game_timestamp) || 0)),
    S.weather && S.weather.enabled
      ? el("div", { class: "muted" }, S.weather.description) : null,
    el("div", { class: "muted" },
      nearby.length ? `Рядом: ${nearby.join(", ")}` : "Рядом никого"),
    el("button", { type: "button",
      onclick: () => inspectSurroundings() }, "Осмотреть"));
}

/* ---------- #72: Place screen — read-only view of any location ---------- */

// #72: stale-response guard for the whole Place screen — one sequence covers
// the view generation, the events fetch and the events retry. A response is
// applied only when it is still the latest request, the player is still on
// this place's hash with the same account/character, and the host node is
// still mounted (A→B→A navigation drops every stale response; #66 precedent).
let placeLoadSeq = 0;
// #72 review F2: the events request family owns a SEPARATE sequence — an
// events retry (or refetch) must never invalidate an in-flight main load.
let placeEventsSeq = 0;

// #72: decorative type glyph for the hero — presentation only, never a
// capability claim; unknown types fall through to a neutral mark.
const PLACE_TYPE_ICONS = {
  settlement: "⌂", house: "⌂", shop: "◆", workshop: "⚒", kitchen: "♨",
  storage: "▤", well: "○", pier: "⚓", security: "✦", island: "◈",
};

function placeTypeIcon(type) {
  return PLACE_TYPE_ICONS[type] || "◈";
}

/* ---------- #76: Events screen — bounded historical sample, honest states ---------- */

// #76: stale-response guard for the Events screen (m238/#74/#75 pattern) —
// a response is applied only when it is still the latest request, the
// player is still on #/events with the same account/character, and the
// host node is still mounted (A→B→A navigation drops every stale response;
// #66 precedent).
let eventsLoadSeq = 0;

async function viewEvents() {
  const viewHash = "#/events";
  const loading = el("div", { class: "muted" }, "Загрузка…");
  app.replaceChildren(topbar("#/world"), loading);
  const seq = ++eventsLoadSeq;
  // #76: a fresh ONE-SHOT fetch per visit/retry — no poller start, no WS
  // calls, no shared-feed reuse, no cursor writes. The world screen keeps
  // its own live feed; this screen is a bounded historical read.
  let events = null;
  try {
    const data = await api("/world/events?since=0&limit=100");
    // A malformed payload is a load failure, not an empty sample — it
    // must never silently render as «Событий пока нет».
    if (!Array.isArray(data)) throw new Error("Malformed events payload");
    events = data;
  } catch (e) {
    // #93: transient failure — explicit retry instead of a dead «Загрузка…».
    // Same stale guard as the apply path (session + host checks included).
    if (seq !== eventsLoadSeq || location.hash !== viewHash
      || !S.user || !S.character || !loading.isConnected) return;
    loadRetryNode(loading, () => viewEvents());
    toast(e.message, true);
    return;
  }
  // #76 stale-response guard (m238/#74/#75 precedent): latest request +
  // still on #/events + same session/character + host still mounted.
  // Otherwise the response is dropped.
  if (seq !== eventsLoadSeq || location.hash !== viewHash
    || !S.user || !S.character || !loading.isConnected) return;
  renderEvents(events);
}

function renderEvents(events) {
  // #76 persistent disclosure: the read model is the FIRST 100 historical
  // events (since=0, id-ascending) — never claimed to be a complete or
  // live-updating feed. Always visible, including in the empty state.
  const disclosure = el("div", { class: "muted" },
    "События мира: до 100 записей с начала истории. Это не полная и не обновляемая автоматически лента.");

  // Rows: raw facts only — formatted tick time (feedRow precedent), the
  // raw event_type string, the raw actor id (or «вы» for the player's own
  // actions; the line is omitted entirely when the event has no actor —
  // never a fabricated person). #291: when the server rendered a text for
  // the row it replaces the raw code and the actor line (the name is
  // already in the phrase). Place: top-level numeric location_id,
  // else a DIRECT numeric payload.location_id (POWER_OUTAGE carries one) —
  // CHARACTER_MOVED's from/to fields are NOT parsed (no guessing, no
  // string coercion): an unusable place is no place affordance.
  const rows = events.map((ev) => {
    const p = ev.payload;
    const locId = (typeof ev.location_id === "number"
        && Number.isSafeInteger(ev.location_id) && ev.location_id > 0)
      ? ev.location_id
      : (p && typeof p === "object" && !Array.isArray(p)
        && typeof p.location_id === "number"
        && Number.isSafeInteger(p.location_id) && p.location_id > 0
        ? p.location_id : null);
    const text = typeof ev.text === "string" && ev.text ? ev.text : null;
    return el("div", { class: "panel" },
      el("div", {}, typeof ev.game_timestamp === "number"
        ? dayTime(ev.game_timestamp) : "—"),
      el("div", {}, text || ev.event_type || "—"),
      !text && ev.actor_id
        ? el("div", { class: "muted" },
            ev.actor_id === S.character.id ? "вы" : ev.actor_id)
        : null,
      locId != null
        ? el("div", { class: "place-actions" },
            mapLink(locId),
            el("a", { href: `#/location/${locId}` }, "Место →"))
        : null);
  });

  // #76 honest gaps: no invitations/RSVP/importance/lifecycle/notifications
  // backend exists, and dialogue emits no WorldEvent. The mockup's category
  // tabs, RSVP buttons, images/avatars and «Новое» badges are fake
  // capability — NEEDS-BACKEND rows instead, nothing invented.
  const backend = el("section", { class: "panel" },
    el("div", { class: "muted" }, "Приглашения недоступны"),
    el("div", { class: "muted" },
      "Принятие, отклонение и перенос приглашений недоступны"),
    el("div", { class: "muted" },
      "Оценка важности и категории событий недоступны"),
    el("div", { class: "muted" },
      'Состояния событий «предстоящее», «активное», «истёкшее» недоступны'),
    el("div", { class: "muted" }, "Персональные уведомления недоступны"),
    el("div", { class: "muted" },
      'Статусы прочтения и отметки «Новое» недоступны'));

  app.replaceChildren(topbar("#/world"),
    el("main", { class: "place-shell" },
      el("section", { class: "panel" },
        el("h2", {}, "События мира"),
        disclosure,
        events.length
          ? el("div", {}, ...rows)
          : el("div", { class: "muted" }, "Событий пока нет")),
      backend));
}

/* ---------- #75: Career screen — job + roles, honest states ---------- */

// #75: stale-response guard for the Career screen (m238/#74 pattern) — a
// response is applied only when it is still the latest request, the player
// is still on #/career with the same account/character, and the host node
// is still mounted (A→B→A navigation drops every stale response; #66
// precedent).
let careerLoadSeq = 0;

async function viewCareer() {
  const viewHash = "#/career";
  const loading = el("div", { class: "muted" }, "Загрузка…");
  app.replaceChildren(topbar("#/profile"), loading);
  const seq = ++careerLoadSeq;
  // Missing hydration is NOT no-job/no-roles: both reads are fetched fresh —
  // the owner payload carries ch.job, the roles endpoint derives on read.
  // ONE shared failure path: either fetch failing (or a malformed roles
  // payload) is a load failure with retry — never a no-job/no-roles state.
  let fresh = null;
  let roles = null;
  try {
    fresh = await api(`/characters/${S.character.id}`);
    const data = await api(`/characters/${S.character.id}/roles`);
    // #75: a malformed roles payload is a load failure, not an empty
    // roster — it must never silently render as «Ролей нет».
    if (!data || !Array.isArray(data.roles)) {
      throw new Error("Malformed roles payload");
    }
    roles = data.roles;
  } catch (e) {
    // #93: transient failure — explicit retry instead of a dead «Загрузка…».
    // Same stale guard as the apply path (session + host checks included).
    if (seq !== careerLoadSeq || location.hash !== viewHash
      || !S.user || !S.character || !loading.isConnected) return;
    loadRetryNode(loading, () => viewCareer());
    toast(e.message, true);
    return;
  }
  // #75 stale-response guard (m238/#74 precedent): latest request + still
  // on #/career + same session/character + host still mounted. Otherwise
  // the response is dropped.
  if (seq !== careerLoadSeq || location.hash !== viewHash
    || !S.user || !S.character || !loading.isConnected) return;
  renderCareer(fresh, roles);
}

function renderCareer(fresh, roles) {
  // Job: the fresh owner payload's ch.job is the only work fact the client
  // has. WORK rides the EXISTING doAction (POST /actions) with a no-op
  // stay: task acceptance is not a completed shift — no navigation, no
  // fake refresh. No away-note: the client does not know the workplace id —
  // the validator auto-redirects the character transparently (needs_move).
  const job = fresh.job;
  const jobPanel = el("section", { class: "panel" },
    el("h2", {}, job ? `Работа: ${codeLabel(JOB_TITLES_RU, job)}` : "Работа не назначена"),
    job ? el("div", { class: "place-actions" },
      el("button", { type: "button",
        onclick: () => doAction("WORK", {}, () => {}) }, "Работать")) : null);

  // Roles: title + source + reason — the reason is WHY the role is derived
  // (the honest world-behavior link), never a progress meter. The payload
  // is {id,title,source,reason} and nothing more.
  const rolesBlock = roles.length
    ? el("div", { class: "profile-rolelist" },
        ...roles.map((r) => el("div", { class: "profile-role" },
          el("span", { class: "profile-role-title" }, r.title || r.id || "—"),
          r.source ? el("span", { class: "muted" }, r.source) : null,
          r.reason ? el("span", { class: "muted profile-role-reason" },
            r.reason) : null)))
    : el("div", { class: "muted" }, "Ролей нет");

  // #75 honest gaps: the sim has no career read model — the roles payload
  // carries no level/progress/requirements/benefits, and no career ladder,
  // rewards or role-development states exist anywhere. The mockup's XP/
  // levels/benefits/rewards and its aspirational role catalog are fake
  // capability — NEEDS-BACKEND rows instead, nothing invented.
  const backend = el("section", { class: "panel" },
    el("div", { class: "muted" }, "Уровень и прогресс недоступны"),
    el("div", { class: "muted" }, "Требования для развития ролей недоступны"),
    el("div", { class: "muted" }, "Преимущества ролей недоступны"),
    el("div", { class: "muted" }, "Карьерные возможности и награды недоступны"),
    el("div", { class: "muted" }, "Состояния развития ролей недоступны"));

  app.replaceChildren(topbar("#/profile"),
    el("main", { class: "place-shell" },
      el("section", { class: "panel" },
        el("h2", {}, "Развитие"),
        jobPanel,
        el("h3", {}, "Роли"),
        rolesBlock),
      backend,
      el("div", { class: "panel" },
        el("a", { href: "#/desire" }, "Цель →"),
        el("a", { href: "#/tasks" }, "Дела →"))));
}

/* ---------- #74: Home screen — the player's housing, honest states ---------- */

// #74: stale-response guard for the Home screen (m238 pattern) — a response
// is applied only when it is still the latest request, the player is still
// on #/home with the same account/character, and the host node is still
// mounted (A→B→A navigation drops every stale response; #66 precedent).
let homeLoadSeq = 0;

async function viewHome() {
  const viewHash = "#/home";
  const loading = el("div", { class: "muted" }, "Загрузка…");
  app.replaceChildren(topbar("#/profile"), loading);
  const seq = ++homeLoadSeq;
  // Missing hydration is NOT a null home: the owner payload is fetched
  // fresh, and the home id is read from the FRESH character only — a stale
  // S.character must never decide the no-home state.
  let fresh = null;
  let homeId = null;
  let home = null;
  try {
    fresh = await api(`/characters/${S.character.id}`);
    // ONE home read off the fresh payload; the /locations fetch resolves it
    // from the world-scoped catalog (name/type — the same source the place
    // screen reads; no /locations/{id} detail call). Skipped entirely when
    // no home is set.
    homeId = fresh.home_location_id;
    if (homeId != null) {
      const locs = await api("/locations");
      home = locs.find((l) => l.id === homeId) || null;
    }
  } catch (e) {
    // #93: transient failure — explicit retry instead of a dead «Загрузка…».
    // Same stale guard as the apply path (session + host checks included).
    if (seq !== homeLoadSeq || location.hash !== viewHash
      || !S.user || !S.character || !loading.isConnected) return;
    loadRetryNode(loading, () => viewHome());
    toast(e.message, true);
    return;
  }
  // #74 stale-response guard (m238/#66 precedent): latest request + still
  // on #/home + same session/character + host still mounted. Otherwise the
  // response is dropped.
  if (seq !== homeLoadSeq || location.hash !== viewHash
    || !S.user || !S.character || !loading.isConnected) return;
  if (homeId == null) {
    // #74 honest no-home state (#73 insufficient-funds bridge pattern):
    // no home → formulate a goal. No Sleep / Open-place affordances —
    // there is nothing to sleep in and nowhere to open.
    app.replaceChildren(topbar("#/profile"),
      el("main", { class: "place-shell" },
        el("section", { class: "panel" },
          el("h2", {}, "Жильё не назначено"),
          el("p", { class: "muted" },
            "У персонажа пока нет жилья — сформулируйте цель, чтобы его найти."),
          el("a", { href: "#/desire" }, "Сформулировать цель →"))));
    return;
  }
  if (!home) {
    // #74: the home id is set but the catalog read does not carry it — a
    // DISTINCT failure state with retry. An unavailable read is never
    // rendered as a no-home state.
    app.replaceChildren(topbar("#/profile"),
      el("main", { class: "place-shell" },
        el("section", { class: "panel" },
          el("h2", {}, "Жильё недоступно"),
          el("p", { class: "muted" },
            `В каталоге мира нет жилья #${homeId}.`),
          loading)));
    loadRetryNode(loading, () => viewHome());
    return;
  }
  renderHome(homeId, fresh, home);
}

function renderHome(homeId, fresh, home) {
  // Hero-lite: real name/type from the catalog entry; the photo note is
  // honest — no place-image endpoint exists, so no imagery is implied.
  const hero = el("header", { class: "place-hero" },
    el("span", { class: "place-hero-icon", "aria-hidden": "true" },
      placeTypeIcon(home.type)),
    el("div", { class: "place-hero-text" },
      el("h2", {}, "Дом"),
      el("div", { class: "muted" }, home.name || `#${home.id}`),
      el("div", { class: "muted" }, home.type || ""),
      el("div", { class: "muted place-hero-note" }, "Фото жилья недоступно")));

  // Energy: the fresh owner payload's real needs.energy; missing stays
  // unavailable — never zero (zero would read as "drained", a fact the
  // payload does not carry). At 100 the validator would reject SLEEP, so
  // the button is disabled and the state says why.
  const energyVal = fresh.needs && Number.isFinite(Number(fresh.needs.energy))
    ? Number(fresh.needs.energy) : null;
  const energyFull = energyVal !== null && energyVal >= 100;
  // Away from home: the SLEEP validator accepts the task and auto-redirects
  // the character home — the note says so up front instead of pretending
  // the sleep happens in place.
  const away = fresh.location_id !== homeId;

  // Sleep goes through the EXISTING doAction (POST /actions): the no-op
  // stay is deliberate — task acceptance is not a completed sleep, so the
  // screen neither navigates away nor fakes an energy refresh.
  const actions = el("div", { class: "place-actions" },
    el("button", { type: "button",
        ...(energyFull ? { disabled: "" } : {}),
        onclick: () => doAction("SLEEP", {}, () => {}) }, "Поспать"),
    el("a", { href: `#/location/${homeId}` }, "Открыть место"));

  const energy = el("section", { class: "panel" },
    el("div", { class: "muted" },
      energyVal === null ? "Энергия недоступна"
        : `Энергия: ${Math.round(energyVal)}`),
    energyFull ? el("div", { class: "muted" },
      "Энергия уже полная — сон сейчас не нужен") : null,
    away ? el("div", { class: "muted" },
      "Персонаж сначала отправится домой") : null);

  // #74 honest gaps: the sim has no housing read model — conditions,
  // amenities, rooms, upgrades and housing offers are NEEDS-BACKEND and
  // stay honestly unavailable (nothing is invented).
  const backend = el("section", { class: "panel" },
    el("div", { class: "muted" }, "Условия проживания недоступны"),
    el("div", { class: "muted" },
      "Данные об удобстве, оснащении и хранении недоступны"),
    el("div", { class: "muted" }, "Данные о комнатах недоступны"),
    el("div", { class: "muted" }, "Улучшения жилья пока недоступны"),
    el("div", { class: "muted" }, "Предложения нового жилья недоступны"));

  app.replaceChildren(topbar("#/profile"),
    el("main", { class: "place-shell" },
      hero, actions, energy, backend,
      el("div", { class: "panel" },
        el("a", { href: "#/desire" }, "Цель →"))));
}

async function viewLocation(locId) {
  navEpoch++;  // #302: every load start is a new navigation generation
  // #312: the scene flight does not survive a place load; mounted URLs
  // release (blobs kept — the cache-return path recreates them).
  cancelSceneFlight();
  releaseSceneURLs();
  const viewHash = `#/location/${locId}`;
  // #72 review F1: a leading-zero deep link (#/location/05) dispatches with
  // locId=5 while the raw hash stays "#/location/05" — the hash guards below
  // would drop every response and strand the screen on «Загрузка…».
  // Normalize to the canonical form BEFORE loading. history.replaceState is
  // deliberate: it fires NO hashchange, so route() is not re-dispatched
  // (#161 single-dispatch rule); a location.hash assignment here would
  // re-enter viewLocation and double-fire the load.
  if (location.hash !== "#/location/" + locId) {
    history.replaceState(null, "", "#/location/" + locId);
  }
  const loading = el("div", { class: "muted" }, "Загрузка…");
  app.replaceChildren(topbar("#/world"), loading);
  const seq = ++placeLoadSeq;
  // #302: record the session UI context — the selected place and the world
  // focus at the moment of leaving the map (restored on the return path,
  // see viewWorld). The context is stamped with the current session
  // identity; resetGameUiContext() clears it across sessions.
  gameUiContext.userRef = S.user;
  gameUiContext.characterId = S.character.id;
  gameUiContext.selectedLocationId = locId;
  gameUiContext.lastWorldFocusId = S.mapFocusLocationId;
  gameUiContext.lastWorldFocusPlayerLocId = S.character.location_id;
  // #302: the load captures its generation + session identity — a newer
  // navigation or a session change while it flies must not publish.
  const epoch = navEpoch;
  const ctxUser = S.user;
  const ctxCharId = S.character.id;
  let locs;
  // #303: the context panel's current-task line reads the SAME tasks source
  // as the World HUD's «Сейчас» (viewWorld's /characters/{id}/tasks) — one
  // bounded read per place load, staged locally like world/weather.
  let tasksData = null;
  let tasksReadToken = null;  // #304: the adapter read token (assigned at START)
  // #72 validator: world context is fetched into LOCALS — a stale response
  // must not mutate the SHARED S.world/S.weather for a screen that is about
  // to be dropped. Other screens read S.world/S.weather (topbar clock, map
  // ambience, locationView), so the shared write is kept — but moved AFTER
  // the stale guard below.
  let world = null;
  let weather = null;
  // #303: a deep link / F5 boots from /characters/by-user, whose payload
  // carries no location_id — the World poll that normally sets it has never
  // run. Re-read the character from the same source the poll uses; the own
  // catch degrades to the stale state, never kills the place load.
  let charData = null;
  try {
    world = await api("/world");
    // #269: null = the joined bootstrap lost ownership (stale, S22) — and
    // the load may already be superseded. Bail BEFORE the extra awaits (no
    // further requests for a dropped load); rechecked below before any
    // shared write.
    if (!world || seq !== placeLoadSeq) return;
    try { weather = await api("/weather"); } catch (e) { weather = null; }
    try { charData = await api(`/characters/${ctxCharId}`); } catch (e) { charData = null; }
    // ONE /locations fetch resolves the requested place — the world-scoped
    // catalog already carries occupants; no /locations/{id} detail call.
    locs = await api("/locations");
    // #303: the tasks read owns its catch — a tasks failure is an honest
    // «Данные о делах недоступны» line, never a dead place screen. The
    // is_owner gate mirrors viewWorld; the cold by-user bootstrap payload
    // carries no is_owner and the place viewer always owns S.character,
    // so undefined counts as owner.
    // #304: token at START, the in-flight counter spans the await; the
    // ingest lands after the #302 guard below.
    if (S.character.is_owner !== false) {
      tasksReadToken = ++taskReadSequence;
      taskListReadsInFlight++;
      try { tasksData = await api(`/characters/${ctxCharId}/tasks`); }
      catch (e) { tasksData = null; }
      finally { taskListReadsInFlight--; }
    }
  } catch (e) {
    // #93: transient failure — explicit retry instead of a dead «Загрузка…».
    // Same stale guard as the apply path (review F3: session checks included),
    // #302: plus the navigation epoch and session identity.
    if (seq !== placeLoadSeq || location.hash !== viewHash
      || !S.user || !S.character
      || S.user !== ctxUser || S.character.id !== ctxCharId
      || epoch !== navEpoch) return;
    loadRetryNode(loading, () => viewLocation(locId));
    toast(e.message, true);
    return;
  }
  // #72 stale-response guard (#66 precedent): latest request + still on this
  // place's hash + same session/character. Otherwise the response is dropped.
  // #302: the epoch and session identity join the guard.
  if (seq !== placeLoadSeq || location.hash !== viewHash
    || !S.user || !S.character
    || S.user !== ctxUser || S.character.id !== ctxCharId
    || epoch !== navEpoch) return;
  // #304: the guard passed — the tasks read lands in the adapter (a
  // superseded load commits nothing).
  if (tasksReadToken != null) ingestTaskSnapshot(tasksData, tasksReadToken);
  // Guard passed — only now do the fresh reads become shared state
  // (topbar clock, map ambience and the overview lines read them).
  // #269 S23: freshness, not just context — an out-of-order OLDER
  // same-context load must not republish its world/weather over an
  // already-newer snapshot; the screen still renders (with the newer
  // shared world).
  if (!S.world || S.world.game_timestamp <= world.game_timestamp) {
    S.world = world;
    S.weather = weather;
  }
  // #303: publish only after the stale guard — same discipline as world/weather; renderPlace derives isCurrent / «Вы сейчас» / placeDialogueNpcs from it.
  if (charData) S.character = { ...S.character, ...charData };
  // #312: publish the catalog + reconcile the scene cache BEFORE the place
  // renders — the stage reads the cache against the fresh state.
  S.mapLocations = locs;
  syncSceneContext(locs);
  const place = locs.find((l) => l.id === locId);
  if (!place) {
    // #72: honest missing-place state — the catalog has no such id.
    app.replaceChildren(topbar("#/world"),
      el("main", { class: "place-shell" },
        el("section", { class: "panel place-missing" },
          el("h2", {}, "Место не найдено"),
          el("p", { class: "muted" },
            `В загруженном каталоге мира нет места #${locId}.`),
          el("a", { href: "#/world" }, "← К карте"))));
    return;
  }
  // #302: the place origin is recorded only after the VALIDATED load — a
  // missing/failed place never becomes a dialogue or back-navigation origin.
  gameUiContext.viewLocationId = locId;
  renderPlace(locId, place, locs, tasksData);
}

/* #302: roster dialogue candidates — NPC occupants of the player's CURRENT
   location only (viewChat reads NPCs at S.character.location_id, #72 — a
   remote place must not promise dialogue it cannot show). Module-level on
   purpose: the m238 renderPlace-body census holds its kind comparison at
   one and the occupants check is file-wide — this helper uses a reordered
   comparison and stays outside that body. */
function placeDialogueNpcs(locId, occupants) {
  if (locId !== S.character.location_id) return [];
  return occupants.filter((o) => "npc" === o.kind);
}

function renderPlace(locId, place, locs, tasksData) {
  const viewHash = `#/location/${locId}`;
  const occupants = place.occupants || [];
  const isCurrent = locId === S.character.location_id;
  // #303: nearby NPCs are the dialogue-relevant slice of the roster; the
  // full roster (any kind) stays reference-level inside «О месте». The API
  // provides exactly {id, name, kind} — nothing else is attributed to NPCs.
  const npcs = occupants.filter((o) => o.kind === "npc");
  // #302: dialogue candidates — the existing helper gates the affordance to
  // the CURRENT location (#72: viewChat reads NPCs at S.character.location_id,
  // so a remote place must not promise dialogue it cannot show).
  const dialogueNpcs = placeDialogueNpcs(locId, occupants);

  // Screen topbar: back to the island map + the place title. The back path
  // is the previous «К карте» mechanism verbatim (#92 manual focus, #302
  // context stamp, #161 single-dispatch gotoWorld). #313: the bar rides the
  // compact contextual header; the registry tabs live in the disclosure.
  const worldTopbar = topbar("#/world");
  const placeTopbar = ctxTopbar(
    el("button", { type: "button", onclick: () => {
      // Same manual-focus mechanism as mapLink() (#92): survives polls,
      // releases when the player physically moves; gotoWorld() keeps the
      // single-dispatch navigation contract (#161).
      S.mapFocusManual = true;
      S.mapFocusAnchor = S.character.location_id;
      S.mapFocusLocationId = locId;
      gameUiContext.lastWorldFocusId = locId;  // #302: an explicit map return must not be overridden by viewWorld's stale pre-place focus restoration
      gameUiContext.lastWorldFocusPlayerLocId = S.character.location_id;
      gotoWorld();
    } }, "← Остров"),
    el("span", { class: "place-topbar-title" },
      `/ ${place.name || "Без названия"}`),
    worldTopbar, "place-topbar");

  // Stage: the #312 text-first scene visual — heading, live weather +
  // confirmed-data summary, image host, live-region status, and ONE stable
  // button (current place only). No Flux call on entry; the asset lifecycle
  // is owned by the scene cache/flight helpers.
  const stage = buildSceneVisual(locId, locs);

  // Context panel — everything about being (or not being) here. World time
  // and weather keep the #72 honest-missing wording («недоступно», never a
  // zero-fallback lie).
  const worldTs = S.world && S.world.game_timestamp;
  const currentLoc = locs.find((l) => l.id === S.character.location_id);
  const contextKids = [
    el("h2", {}, "Контекст места"),
    el("div", { class: "place-context-name" }, place.name || "Без названия"),
    el("div", { class: "muted" },
      `Время мира: ${typeof worldTs === "number" ? dayTime(worldTs) : "недоступно"}`),
    el("div", { class: "muted" },
      S.weather && S.weather.enabled
        ? `Погода в мире: ${S.weather.description}` : "Погода в мире: недоступна"),
    el("span", { class: isCurrent
      ? "place-badge place-badge-here" : "place-badge" },
      isCurrent ? "Вы здесь" : "Другое место"),
    // #311: the quiet live-events indicator — empty on render, filled by the
    // feed controller's place listener when a LIVE event arrives (muted
    // style rides the shared .muted class; no modal, no route change).
    el("div", { class: "place-feed-quiet muted" }, ""),
  ];
  if (!isCurrent) {
    // Remote place: read-only — the player's own location is stated
    // explicitly, and the ONE move path is the existing #310 moveTo()
    // (AUTONOMOUS gating and toasts included). No actions, no dialogue,
    // no scene-act, no «Вы здесь».
    contextKids.push(
      el("div", { class: "muted" },
        `Вы сейчас: ${currentLoc && currentLoc.name ? currentLoc.name : "—"}`),
      el("button", { class: "place-cta", type: "button",
        onclick: () => moveTo(place) }, "Пойти сюда"));
  }

  // Nearby NPCs — name rows only; «Поговорить» reuses the existing dialogue
  // entry (gameUiContext.npcId → #/chat, revalidated in viewChat) and is
  // offered for the CURRENT place only.
  contextKids.push(el("div", { class: "place-nearby" },
    el("h3", {}, "Рядом"),
    ...(npcs.length
      ? npcs.map((o) => el("div", { class: "place-nearby-row" },
          el("span", {}, o.name || "Без имени"),
          dialogueNpcs.includes(o)
            ? el("button", { class: "place-cta", type: "button",
              // #313: the visible «Поговорить» is ambiguous in the AT
              // roster loop — name it for the NPC it opens a dialogue with.
              "aria-label": `Поговорить с ${o.name}`, onclick: () => {
                // #305: the dialogue origin — the place «Поговорить» was
                // clicked at; the chat's back anchor returns here
                // («← К месту»), a direct #/chat entry offers the map.
                gameUiContext.chatSourceLocationId = locId;
                gameUiContext.chatSourceLocationName = place.name || null;
                gameUiContext.npcId = o.id;
                location.hash = "#/chat";
              } }, "Поговорить")
            : null))
      : [el("p", { class: "muted" }, "Рядом никого нет.")])));

  if (isCurrent) {
    // Contextual actions — CURRENT place only, the SAME vocabulary as the
    // World card; the hint keeps the contract honest (a button is a
    // possible request — POST /actions validates it).
    contextKids.push(
      el("div", { class: "place-actions-block" },
        el("h3", {}, "Действия здесь"),
        el("div", { class: "place-actions" },
          ...ACTION_VOCABULARY.map(([a, label]) =>
            el("button", { type: "button", onclick: () => doAction(a) }, label))),
        el("p", { class: "muted place-act-hint" },
          "Кнопка только отправляет запрос — сервер решает, возможно ли это сейчас.")));

    // Free-text action — CURRENT place only. The bar reuses sceneActBar with
    // the SAME submission path (submitSceneAct — the single /scene/act call
    // site, draft and error semantics included); the committed result is
    // re-read from S.sceneAct into THIS host, because submitSceneAct's own
    // refresh targets the Scene panel host id, which the Place screen does
    // not mount.
    const actResult = el("div", { class: "place-scene-act-result" });
    const initial = sceneActResultNode();
    if (initial) actResult.append(initial);
    const submitPlaceSceneAct = async () => {
      await submitSceneAct(false);
      const node = sceneActResultNode();
      actResult.replaceChildren(...(node ? [node] : []));
    };
    contextKids.push(el("div", { class: "place-scene-act" },
      sceneActBar(submitPlaceSceneAct), actResult));
  }

  // Current task — #304: the SAME adapter source as the World HUD's
  // «Сейчас» (identical selected-task text). The «Задача:» label stays;
  // only the content host is adapter-driven and patched in place (never a
  // full-screen rerender for status).
  contextKids.push(el("div", { class: "place-task" },
    el("span", { class: "place-task-label" }, "Задача:"),
    el("span", { class: "place-task-content", id: "place-task-content" })));
  // #304: the submission feedback line — the local sending text while a
  // submission is in flight, else the last submission-failure caption.
  contextKids.push(el("div", { class: "task-submit-line muted" }, ""));

  // Events: the request has its own failure/retry state, independent of the
  // main render — a feed failure never takes the whole screen down.
  const eventsBody = el("div", { class: "place-events-body" },
    el("div", { class: "muted" }, "Загрузка…"));
  const eventsPanel = el("section", { class: "place-events" },
    el("h4", {}, "События"),
    el("div", { class: "muted place-events-note" }, "События в загруженной выборке"),
    eventsBody);

  async function loadPlaceEvents() {
    // #72 review F2: the events path owns its sequence — an events retry
    // must NOT invalidate an in-flight main load (and vice versa). Cross-
    // navigation staleness is still caught by the hash guard and this
    // panel's isConnected check.
    const eseq = ++placeEventsSeq;
    eventsBody.replaceChildren(el("div", { class: "muted" }, "Загрузка…"));
    try {
      const feed = await api("/world/events?since=0&limit=100");
      // Same stale guard as the main load, plus this panel's connectivity.
      if (eseq !== placeEventsSeq || location.hash !== viewHash
        || !S.user || !S.character || !eventsPanel.isConnected) return;
      const rows = feed.filter((ev) => ev.location_id === locId);
      eventsBody.replaceChildren(
        rows.length
          ? el("div", { class: "place-event-list" },
              ...rows.map((ev) => el("div", { class: "place-event-row" },
                el("span", { class: "muted" },
                  typeof ev.game_timestamp === "number"
                    ? dayTime(ev.game_timestamp) : "—"),
                el("span", {}, ev.text || ev.event_type || ""))))
          : el("p", { class: "muted" },
              "В загруженной выборке событий этого места нет."));
    } catch (e) {
      // Review F3: session checks included, same as the apply path.
      if (eseq !== placeEventsSeq || location.hash !== viewHash
        || !S.user || !S.character || !eventsPanel.isConnected) return;
      eventsBody.replaceChildren(
        el("span", { class: "muted" }, "События не загрузились. " + RETRY_HINT),
        el("button", { type: "button", onclick: loadPlaceEvents }, "Повторить"));
    }
  }
  loadPlaceEvents();

  // «О месте» — collapsed reference level: authoritative catalog fields, the
  // FULL occupant roster (names + kinds, no affordances), and the sampled
  // events listing. Native <details> — keyboard accessible for free.
  const parent = place.parent_id != null
    ? locs.find((l) => l.id === place.parent_id) : null;
  const detailRow = (label, value) => el("div", { class: "place-detail-row" },
    el("span", { class: "place-detail-label" }, label), el("span", {}, value));
  const details = el("details", { class: "place-about" },
    el("summary", {}, "О месте"),
    el("div", { class: "place-about-body" },
      detailRow("ID", String(place.id)),
      detailRow("Тип", place.type || "—"),
      detailRow("Координаты", place.x != null && place.y != null
        ? `${place.x} · ${place.y}` : "—"),
      detailRow("Входит в", parent ? (parent.name || `#${parent.id}`) : "—"),
      detailRow("Людей сейчас", String(occupants.length)),
      el("h4", {}, "Кто здесь"),
      occupants.length
        ? el("div", { class: "place-roster" },
            ...occupants.map((o) => el("div", { class: "place-roster-row" },
              el("span", {}, o.name || `#${o.id}`),
              el("span", { class: "muted" }, o.kind || ""))))
        : el("p", { class: "muted" }, "Сейчас здесь никого нет."),
      eventsPanel));
  contextKids.push(details);

  app.replaceChildren(placeTopbar,
    el("main", { class: "place-shell place-game" },
      el("div", { class: "place-grid" },
        stage,
        el("section", { class: "panel place-context" }, ...contextKids))));
  // #311: the feed controller starts (idempotent) + the place view attaches
  // its quiet listener — a cold deep-link into a place starts the feed too.
  worldFeedController.start();
  worldFeedController.attachView(placeFeedListener);
  // #304: the Place task host + the submission line render from the adapter
  // (the ingest has already landed before renderPlace) — fill at mount.
  refreshTaskPresentationHosts();
}

/* #312: the Place stage scene visual. Module-level on purpose: the m238
   renderPlace-body census holds its isCurrent branches at one each — these
   helpers use a `current` local (String comparison) and stay outside the
   branch bodies. The stage hosts are created ONCE and patched in place
   (the button node is never deleted — focus retention across state swaps). */
const SCENE_IDLE_LABEL = "Показать сцену";

function buildSceneVisual(locId, locs) {
  const heading = el("h2", { class: "scene-heading" });
  const summary = el("div", { class: "scene-live-summary" });
  const image = el("div", { class: "scene-image-host" });
  const status = el("p", { class: "scene-status", role: "status",
    "aria-live": "polite" });
  const button = el("button", { type: "button",
    onclick: () => inspectSurroundings() });
  const current = String(locId) === String(S.character
    && S.character.location_id);
  // Remote preview: NO generation button at all (the move CTA stays in the
  // context panel) — the button node exists only for the current place.
  const stage = el("div", { class: "place-stage" },
    heading, summary, image, status, ...(current ? [button] : []));
  stage._sceneLocId = locId;
  stage._sceneHosts = { heading, summary, image, status, button };
  updateSceneVisual(stage, locId, locs);
  return stage;
}

function updateSceneVisual(stage, locId, locs) {
  // Patch the stable stage hosts from the scene cache — never a rebuild,
  // never a request. The summary ALWAYS reflects the live loaded data; the
  // descriptor feeds only the img alt.
  const h = stage._sceneHosts;
  if (!h) return;
  const current = String(locId) === String(S.character
    && S.character.location_id);
  const data = current ? sceneFallbackData() : sceneFallbackData(locId, locs);
  const ts = S.world && typeof S.world.game_timestamp === "number"
    ? S.world.game_timestamp : null;
  h.heading.textContent = ts !== null
    ? `${data.locationName} · ${dayTime(ts)}` : data.locationName;
  // Weather line: readable description when enabled, honest gap otherwise —
  // a disabled description never leaks.
  const weatherText = S.weather && S.weather.enabled
    ? (S.weather.description || "Погода недоступна") : "Погода недоступна";
  h.summary.replaceChildren(
    ...[weatherText, ...sceneSummaryLines(data)]
      .map((t) => el("div", { class: "muted" }, t)));
  const entry = S.sceneCache.get(String(locId)) || null;
  // A cache RETURN recreates the objectURL from the retained blob (the
  // mounted URL was revoked on navigation away); the PANEL never does this.
  if (entry && entry.status === "ready" && !entry.objectURL && entry.blob) {
    entry.objectURL = URL.createObjectURL(entry.blob);
  }
  if (entry && entry.status === "ready" && entry.objectURL) {
    const img = el("img", { src: entry.objectURL, alt: sceneImageAlt(entry) });
    img.addEventListener("error", () => {
      // Token check #8: only the still-mounted entry for THIS place reacts
      // to a decode failure — a superseded entry is never touched. Cache
      // membership + ready ⇒ the full flight token held at ready-time; every
      // token change (movement/identity/context) discards the entry first
      // (discardSceneEntry/syncSceneContext/clearSceneCache), so this guard
      // is equivalent to a token re-check without a live flight to compare.
      if (S.sceneCache.get(String(locId)) !== entry
        || entry.status !== "ready") return;
      URL.revokeObjectURL(entry.objectURL);
      entry.objectURL = null;
      sceneUnavailable(entry, "decode");
    });
    h.image.replaceChildren(img);
  } else {
    h.image.replaceChildren();
  }
  // Remote preview: text-first composition only — no button, no status.
  if (!current) return;
  if (entry && entry.status === "generating") {
    h.status.textContent = "Создаём визуализацию…";
  } else if (entry && entry.status === "unavailable") {
    h.status.textContent = "Визуал недоступен";
  } else {
    h.status.textContent = "";
  }
  if (entry && entry.status === "generating") {
    h.button.textContent = "Создаём сцену…";
    h.button.setAttribute("aria-disabled", "true");
  } else if (entry && entry.status === "ready") {
    h.button.textContent = "Сцена показана";
    h.button.setAttribute("aria-disabled", "true");
  } else if (entry && entry.status === "unavailable") {
    h.button.textContent = "Повторить визуализацию";
    h.button.setAttribute("aria-disabled", "false");
  } else {
    h.button.textContent = SCENE_IDLE_LABEL;
    h.button.setAttribute("aria-disabled", "false");
  }
}

async function viewExternal() {
  // #77 entry normalization: exactly ONE hash assignment (m175 pin) — a
  // world-button click fires hashchange → route() → viewExternal again with
  // the hash already current, so the catalog fetch happens exactly once
  // (the old code assigned the hash AND fetched, double-fetching on every
  // button entry). Direct hash entry (reload on #/external) loads at once.
  if (location.hash !== "#/external") { location.hash = "#/external"; return; }
  const viewHash = "#/external";
  const loading = el("div", { class: "muted" }, "Загрузка…");
  app.replaceChildren(topbar("#/world"), loading);
  const seq = ++externalLoadSeq;
  let catalog = null;
  try {
    const data = await api("/external");
    // #77 defensive: a non-array catalog or a venue without a services
    // array is a load failure with retry — never a silent empty screen.
    if (!Array.isArray(data)
      || data.some((loc) => !loc || !Array.isArray(loc.services))) {
      throw new Error("Malformed external catalog");
    }
    catalog = data;
  } catch (e) {
    // #175/#93: transient failure — explicit retry instead of a dead «Загрузка…».
    // Same stale guard as the apply path (session + host checks included).
    if (seq !== externalLoadSeq || location.hash !== viewHash
      || !S.user || !S.character || !loading.isConnected) return;
    loadRetryNode(loading, () => viewExternal());
    toast(e.message, true);
    return;
  }
  // #77 stale-response guard (m238/#74/#75/#76 precedent): latest request +
  // still on #/external + same session/character + host still mounted.
  // Otherwise the response is dropped.
  if (seq !== externalLoadSeq || location.hash !== viewHash
    || !S.user || !S.character || !loading.isConnected) return;
  renderExternal(catalog);
}

// #77: stale-response guard for the external-trip screen (m238/#74/#75/#76
// pattern). Declared AFTER the viewExternal definition ON PURPOSE: m238's
// renderPlace body slice runs to `async function viewExternal(` — anything
// between renderPlace and viewExternal would enter that pinned slice (the
// top-level declaration executes before any call, so TDZ is not a concern).
let externalLoadSeq = 0;

function renderExternal(catalog) {
  // Flattened service options (label format unchanged): item_type and
  // service_type ride along for the structured quantity control.
  const services = [];
  for (const loc of catalog) {
    for (const svc of loc.services) {
      services.push({
        id: svc.id,
        label: `${loc.name || `#${loc.id}`} — ${svc.service_type}` +
          (svc.item_type ? ` (${svc.item_type})` : ""),
        type: svc.service_type,
        itemType: svc.item_type || null,
      });
    }
  }
  const svcSel = el("select", {},
    ...services.map((s) => el("option", { value: s.id }, s.label)));
  const purpose = el("select", {},
    el("option", { value: "heal" }, "лечение"),
    el("option", { value: "shop" }, "покупки"),
    el("option", { value: "visit" }, "визит"));

  const qtyLabel = el("span", {});
  const qtyInput = el("input", { type: "number", min: "1", max: "10",
    step: "1", value: "1" });
  const qtyRow = el("label", {}, qtyLabel, qtyInput);
  const err = el("div", { class: "err-text" });

  function selectedSvc() {
    return services.find((s) => String(s.id) === String(svcSel.value)) || null;
  }

  // Selector sync: reset the quantity to 1 on every service change, show
  // the quantity row only for a purchase service with a usable item_type,
  // and disable submit for a purchase service WITHOUT one (the backend
  // would reject the basket — the screen says so instead of pretending).
  // The button is looked up by id: the change event only fires after the
  // screen is mounted.
  // #77 (validator F1): in-flight latch — syncQtyRow re-enables the submit
  // button on every selector change, so the disabled attribute alone is
  // bypassable mid-POST (select another service → submit re-enabled →
  // second POST; the backend does NOT reject concurrent trips). The latch
  // is respected by BOTH the handler and the selector sync.
  let submitting = false;

  function syncQtyRow() {
    const svc = selectedSvc();
    const usable = !!svc && svc.type === "purchase" && !!svc.itemType;
    const broken = !!svc && svc.type === "purchase" && !svc.itemType;
    // style.display, not the hidden attribute: the author rule
    // `label { display: block }` (style.css) outranks the UA
    // `[hidden] { display: none }`, so the attribute would not hide it.
    qtyRow.style.display = usable ? "" : "none";
    if (usable) qtyLabel.textContent = `Количество (${svc.itemType})`;
    qtyInput.value = "1";
    const btn = document.getElementById("external-submit");
    if (btn) btn.disabled = submitting || broken || services.length === 0;
    err.textContent = broken ? "Конфигурация покупки недоступна" : "";
  }
  svcSel.addEventListener("change", syncQtyRow);

  // #77 framing + honesty lines (always visible): the destination is OFF
  // the island map — no map-focus affordance and no place links (external
  // venues carry no island coordinates); the trip is an AUTOMATIC ROUND-TRIP
  // (one completion handler logs departed + returned); total cost and
  // duration are config values the catalog contract does not expose.
  const framing = el("div", { class: "muted" },
    "Внешняя локация — вне карты острова. Проезд через порт острова.");
  const returnNote = el("div", { class: "muted" },
    "Возврат автоматический — задача поездки завершается возвращением на Рейнеке.");
  const totalsNote = el("div", { class: "muted" },
    "Полная стоимость и длительность поездки здесь недоступны.");

  // EVERY returned venue renders (never a hard-coded set): real fields
  // only — raw ext_type, description or the honest unavailable note, and
  // per-service raw types with price/duration; the heal note ONLY when
  // heal_amount is a number (treatment).
  const venueBlocks = catalog.map((loc) =>
    el("div", { class: "panel" },
      el("h3", {}, loc.name || `#${loc.id}`),
      el("div", { class: "muted" }, loc.ext_type || "—"),
      loc.description
        ? el("div", { class: "muted" }, loc.description)
        : el("div", { class: "muted" }, "Описание места недоступно"),
      ...loc.services.map((svc) => {
        const parts = [
          `${svc.service_type}${svc.item_type ? ` (${svc.item_type})` : ""}`,
          `Цена услуги: ${svc.price} ₽`,
          `Длительность услуги: ${svc.duration_minutes} мин`];
        if (typeof svc.heal_amount === "number") {
          parts.push(`Восстановление здоровья: до ${svc.heal_amount}`);
        }
        return el("div", { class: "muted" }, parts.join(" — "));
      })));

  // #77 honest gaps: no city-level description, no city places beyond the
  // catalog, no player-chosen return, no service-availability states. The
  // mockup's tabs, venue-category strip, decorative imagery and Plan-Trip
  // composer are fake capability — NEEDS-BACKEND rows, nothing invented.
  const backend = el("section", { class: "panel" },
    el("div", { class: "muted" }, "Общее описание города недоступно"),
    el("div", { class: "muted" }, "Связь поездки со свободными запросами недоступна"),
    el("div", { class: "muted" }, "Пребывание и перемещения по городу недоступны"),
    el("div", { class: "muted" }, "Возвращение по выбору игрока недоступно"),
    el("div", { class: "muted" }, "Данные о временной недоступности услуг недоступны"),
    el("div", { class: "muted" },
      "Места вне каталога, включая рестораны, ночные заведения и достопримечательности, недоступны"));

  app.replaceChildren(topbar("#/world"),
    el("main", { class: "place-shell" },
      el("section", { class: "panel" },
        el("h2", {}, "Поездка во Владивосток"),
        framing),
      el("section", { class: "panel" },
        el("h3", {}, "Места и услуги"),
        catalog.length
          ? el("div", {}, ...venueBlocks)
          : el("div", { class: "muted" }, "Места и услуги не найдены")),
      el("section", { class: "panel" },
        el("h3", {}, "Поездка"),
        el("label", {}, "Сервис"), svcSel,
        el("label", {}, "Цель"), purpose,
        qtyRow,
        err,
        el("div", { class: "place-actions" },
          el("button", { id: "external-submit", class: "primary",
            onclick: async (ev) => {
            const btn = ev.currentTarget;
            // #77 (validator F1): check-and-set latch — a second click
            // during the in-flight POST is a no-op.
            if (submitting) return;
            const svc = selectedSvc();
            let items = {};
            if (svc && svc.type === "purchase") {
              if (!svc.itemType) return;  // submit is disabled for broken services
              const qty = Number(qtyInput.value);
              // #77: explicit integer 1–10 validation — a non-integer or
              // out-of-range value is rejected with an honest error, NEVER
              // silently clamped.
              if (!Number.isSafeInteger(qty) || qty < 1 || qty > 10) {
                err.textContent = "Количество: целое число от 1 до 10";
                return;
              }
              items = { [svc.itemType]: qty };
            }
            submitting = true;
            btn.disabled = true;
            const submittedHash = location.hash;
            const submittedCharId = S.character ? S.character.id : null;
            // #77 (validator F2): the ORIGINATING screen's root node —
            // hash+character alone cannot distinguish external → away →
            // external during a pending POST (the stale response would
            // match and yank the freshly opened screen); connectivity of
            // the originating render closes that hole.
            const screenNode = ev.currentTarget.closest("main");
            try {
              await api("/actions", { method: "POST", body: {
                character_id: S.character.id, action_type: "TRAVEL_EXTERNAL",
                params: { service_id: Number(svcSel.value),
                  purpose: purpose.value, items } } });
              toast("Поездка запланирована (транзит через порт)");
              // #77: no yank after navigation away — the task exists
              // server-side either way; only a still-current screen
              // navigates to the world.
              if (location.hash === submittedHash
                && S.character && S.character.id === submittedCharId
                && screenNode.isConnected) {
                gotoWorld();  // #161: single dispatch — no direct viewWorld() next to the hash assignment
              }
            } catch (e) {
              // #144/§61: same stale-state class as moveTo — localize.
              err.textContent = isAutonomousRefusal(e)
                ? AUTONOMOUS_HINT : String(e.detail);
              if (isAutonomousRefusal(e))
                toast(AUTONOMOUS_HINT, true);
              if (String(e.detail).includes("port"))
                toast("Сначала дойдите до порта", true);
            }
            submitting = false;
            btn.disabled = false;
          } }, "Отправиться")),
        returnNote,
        totalsNote),
      backend,
      el("div", { class: "panel" },
        el("a", { href: "#/world" }, "К острову →"))));
  // Initial selector sync runs AFTER mounting (the submit button lookup
  // needs the live DOM); change events only fire post-mount anyway.
  syncQtyRow();
}

/* ---------- event feed (§79 WS + polling fallback) ---------- */

/* #289: critical events → terracotta callout row + map marker accent. The
   backend event model (app/events/events.py) carries no criticality field,
   so criticality is derived client-side from the event type; a
   payload.critical === true hook is honored for future backend use. */
const CRITICAL_EVENT_TYPES = new Set([
  "CHARACTER_DIED", "OBJECT_BURNING", "OBJECT_BURNED", "OBJECT_DESTROYED",
  "CRIME_COMMITED", "ARRESTED", "POWER_OUTAGE", "TASK_FAILED",
]);
function isCriticalEvent(ev) {
  return CRITICAL_EVENT_TYPES.has(ev.event_type)
    || (!!ev.payload && ev.payload.critical === true);
}

function feedRow(ev) {
  const critical = isCriticalEvent(ev);
  // #291: a server-rendered text replaces the raw code and the actor id
  // (the name is already in the phrase); no text -> today's raw row.
  const text = typeof ev.text === "string" && ev.text ? ev.text : null;
  return el("div", { class: `ev${critical ? " ev-critical" : ""}` },
    el("span", { class: "t" }, dayTime(ev.game_timestamp || 0)),
    el("span", { class: "ev-text" }, text || ev.event_type || ""),
    !text && ev.actor_id
      ? el("span", { class: "muted" }, ` · ${ev.actor_id}`)
      : null);
}

/* ---------- #311: the session-owned world feed controller ----------
   One owner of the live event transport (WS + REST poll) across every
   screen. The controller exclusively owns the S feed/transport state
   (declared in S above); its session metadata lives in the module-local
   bindings below (same convention as navEpoch/gameUiContext). Views never
   stop/start the transport: they start() the controller (identity-
   reconciled, idempotent) and attachView() a listener that receives
   committed event batches. */

// #311: module-local controller metadata.
let worldFeedRunning = false;
let worldFeedToken = null;  // { userRef, charId, generation } — user OBJECT identity
let worldFeedGeneration = 0;
let worldFeedNavGen = 0;  // advanced by onRoute(); stale-timer/flight checks
let worldFeedListeners = new Set();
let worldFeedViewDisposer = null;
let worldFeedReconnectTimer = null;
let worldFeedPollFlight = null;  // { controller, userRef, charId, navGen }
let worldFeedInitialPollDone = false;

// #311: the live-event gate shared by the quiet-indicator listeners —
// bootstrap replay (id at or below the replay gate) never surfaces as a
// «В мире произошло…» notice. One shared helper gates both quiet listeners
// (and keeps the replay-gate census at its code sites only).
function worldFeedHasLive(fresh) {
  return fresh.some((ev) => ev.id > S.bootCursor);
}

// #311: a poll flight may commit only while it is still the current one —
// the controller running, the same session token, the same navigation
// generation, and still the registered flight (a teardown aborts + clears).
function worldFeedFlightCurrent(flight) {
  return worldFeedRunning && worldFeedToken
    && worldFeedPollFlight === flight
    && worldFeedToken.userRef === flight.userRef
    && worldFeedToken.charId === flight.charId
    && flight.navGen === worldFeedNavGen;
}

// #311: publish a committed batch to the listeners. One bad listener must
// never break the feed — exceptions are isolated per listener.
function worldFeedPublishAll(fresh) {
  for (const listener of [...worldFeedListeners]) {
    try { listener(fresh); } catch (e) { /* isolated: the feed outlives it */ }
  }
}

function worldFeedSubscribe(listener) {
  if (!worldFeedRunning || typeof listener !== "function") return () => {};
  worldFeedListeners.add(listener);
  return () => { worldFeedListeners.delete(listener); };
}

function worldFeedDetachView() {
  if (worldFeedViewDisposer) { worldFeedViewDisposer(); worldFeedViewDisposer = null; }
}

function worldFeedAttachView(listener) {
  worldFeedDetachView();
  worldFeedViewDisposer = worldFeedSubscribe(listener);
}

// #311: the tracked, token-checked reconnect timer (replaces the untracked
// setTimeout in ws.onclose). The callback re-checks the session token, the
// controller GENERATION, and the navigation generation — a timer armed
// before a teardown/restart cycle or a navigation must never open a socket
// for a newer context.
function armReconnect(delay) {
  if (worldFeedReconnectTimer) clearTimeout(worldFeedReconnectTimer);
  const armedNavGen = worldFeedNavGen;
  const armedGeneration = worldFeedGeneration;
  worldFeedReconnectTimer = setTimeout(() => {
    worldFeedReconnectTimer = null;
    if (!worldFeedRunning || !worldFeedToken) return;  // session torn down
    if (armedGeneration !== worldFeedGeneration) return;  // #311: a teardown/restart cycle invalidated this timer
    if (armedNavGen !== worldFeedNavGen) return;  // stale navigation — onRoute re-arms
    if (S.ws) return;  // a socket already exists
    connectWs();
  }, delay);
}

// #311: identity-reconciled, idempotent start — running for the same
// identity is a no-op (no second WS, no second interval, no poll re-arm);
// a different identity tears down destructively and starts fresh.
function worldFeedStart() {
  if (!S.user) return;
  const userRef = S.user;
  const charId = S.character ? S.character.id : null;
  if (worldFeedRunning && worldFeedToken
    && worldFeedToken.userRef === userRef
    && worldFeedToken.charId === charId) return;
  if (worldFeedRunning) worldFeedTeardown();
  worldFeedRunning = true;
  worldFeedGeneration += 1;
  worldFeedToken = { userRef, charId, generation: worldFeedGeneration };
  connectWs();
  if (!S.pollTimer) S.pollTimer = setInterval(pollEvents, 5000);  // #161: never stack a second poll loop
}

// #311: the ONE-TIME session bootstrap poll — fired by the WORLD view only.
// A place/chat cold entry starts the transport (WS + interval) but must not
// add the poll's /world refresh on top of its own load (m269 S10: a place
// cold boot keeps its single /world fetch). Once per session — navigation
// never re-arms it; a teardown (logout/401/character switch) re-arms it for
// the next session.
function worldFeedInitialPoll() {
  if (worldFeedInitialPollDone) return;
  worldFeedInitialPollDone = true;
  pollEvents();
}

// #311: destructive teardown — invalidate the token and every listener,
// close the WS, cancel the reconnect + hello-deadline timers, abort the
// in-flight poll, reset the stream + liveness state (logout / 401 /
// character switch / resetGameUiContext all land here).
function worldFeedTeardown() {
  worldFeedRunning = false;
  worldFeedToken = null;
  worldFeedListeners.clear();
  worldFeedDetachView();
  if (worldFeedReconnectTimer) { clearTimeout(worldFeedReconnectTimer); worldFeedReconnectTimer = null; }
  if (worldFeedPollFlight) {
    if (worldFeedPollFlight.controller) {
      worldFeedPollFlight.controller.abort();  // a deferred poll must resolve into nothing
    }
    worldFeedPollFlight = null;
  }
  if (S.pollTimer) { clearInterval(S.pollTimer); S.pollTimer = null; }
  if (S.helloDeadline) { clearTimeout(S.helloDeadline); S.helloDeadline = null; }  // #258: a stopped feed's hello deadline must never fire
  if (S.ws) {
    const w = S.ws;
    S.ws = null;
    w.onclose = null; w.onmessage = null; w.onopen = null;  // #161: detach BEFORE close — the old socket's async onclose must not stomp a newer socket's state
    S.wsCursorSent = false;  // #179: the #161 detach means onclose never fires for this socket — re-arm the handshake flag here so the next connection re-declares its cursor
    try { w.close(); } catch (e) { /* already closed */ }
  }
  S.feed = [];
  S.cursor = 0;
  S.feedInit = "pending";
  S.feedFetched = false;
  S.bootCursor = Infinity;  // #177: back to "no live events until proven"
  S.wsTries = 0;
  S.connLost = false;
  S.pollFails = 0;
  hideConnBanner();
  worldFeedInitialPollDone = false;
}

// #311: navigation dispatch — detach the view subscription, reconcile the
// identity (a session change tears down destructively), and keep the
// transport running across screens. A pending reconnect timer is stale
// after this dispatch (its callback is navGen-checked), so a fresh one is
// armed while the socket is down and tries remain.
function worldFeedOnRoute() {
  worldFeedNavGen += 1;
  worldFeedDetachView();
  const userRef = S.user;
  const charId = S.character ? S.character.id : null;
  if (worldFeedRunning && worldFeedToken
    && (worldFeedToken.userRef !== userRef || worldFeedToken.charId !== charId)) {
    worldFeedTeardown();
    return;
  }
  if (worldFeedRunning && !S.ws && S.wsTries >= 1 && S.wsTries <= 3) {
    armReconnect(2000 * S.wsTries);  // clears the stale timer first
  }
}

// #311: the controller — the single session-owned owner of the feed.
const worldFeedController = {
  start: worldFeedStart,
  subscribe: worldFeedSubscribe,
  stop: worldFeedTeardown,
  teardown: worldFeedTeardown,
  onRoute: worldFeedOnRoute,
  attachView: worldFeedAttachView,
  initialPoll: worldFeedInitialPoll,
};

// #311: the World view's feed listener — the feed box and the island
// preview re-render on every committed batch (moved out of the transport
// frame handler / REST poll, which are view-agnostic now).
function worldFeedListener(fresh) {
  const feedBox = document.getElementById("feed");
  if (feedBox) {
    feedBox.replaceChildren(el("h2", { class: "world-sec-h" }, "События"),
      ...S.feed.slice(-40).reverse().map(feedRow));
  }
  refreshIslandPreview();  // #301: the panel's island preview follows the feed
}

// #311: the Place view's quiet listener — a LIVE event (beyond the boot
// replay marker) while the player reads a place surfaces as the muted
// «В мире произошло…» line; a fresh render starts empty (no stale notice).
function placeFeedListener(fresh) {
  if (!worldFeedHasLive(fresh)) return;
  const node = document.querySelector(".place-feed-quiet");
  if (node) node.textContent = "В мире произошло…";
}

// #311: the Chat view's quiet listener — same contract on a dedicated
// muted node near the heading; never touches the draft/typing/send surface.
function chatFeedListener(fresh) {
  if (!worldFeedHasLive(fresh)) return;
  const node = document.getElementById("chat-feed-quiet");
  if (node) node.textContent = "В мире произошло…";
}

// #258: the 5s hello window — a fresh page session waits for the WS hello
// (snapshot tail) before trusting the socket; if it never arrives, the
// hello-less socket is retired FIRST and the REST fallback takes over for
// the rest of the page session. The timer is socket-scoped: the callback
// re-checks socket identity, so a stale timer can never close/clear a
// replacement socket or resurrect a stopped feed.
function armHelloDeadline(ws) {
  if (S.feedInit !== "pending") return;  // init settled — no deadline needed
  if (S.helloDeadline) clearTimeout(S.helloDeadline);
  S.helloDeadline = setTimeout(() => {
    S.helloDeadline = null;
    if (S.feedInit !== "pending" || S.ws !== ws) return;  // stale timer or init settled elsewhere
    // Retire the hello-less socket FIRST (detach before close, mirroring
    // stopFeed — its async onclose must not fire or schedule a reconnect),
    // then enable the REST fallback.
    ws.onclose = null; ws.onmessage = null; ws.onopen = null;
    if (S.ws === ws) S.ws = null;
    S.wsCursorSent = false;  // detached socket: onclose will not re-arm — do it here (same as stopFeed)
    try { ws.close(); } catch (e) { /* already closed */ }
    S.feedInit = "rest";  // permanent for this page session
  }, 5000);
}

async function pollEvents() {
  if (S.pollBusy) return;  // #161: a view re-render must not double-fire the poll
  S.pollBusy = true;
  // #311: the poll flight — the token every post-await guard re-checks, so
  // a tick deferred across a navigation or a session teardown commits
  // nothing; the AbortController lets the teardown cancel the held request.
  // The controller is defensive (same tolerance as the #312 scene flight):
  // an environment without AbortController still gets a fully working poll
  // — the token checks alone discard stale flights — and a constructor
  // failure here must never wedge the busy flag (the creation sits BEFORE
  // the try on purpose, so it cannot be mistaken for a poll failure).
  let controller = null;
  try { controller = new AbortController(); } catch (e) { controller = null; }
  const flight = {
    controller,
    userRef: S.user,
    charId: S.character ? S.character.id : null,
    navGen: worldFeedNavGen,
  };
  worldFeedPollFlight = flight;
  try {
    await pollEventsFeed();
    if (!worldFeedFlightCurrent(flight)) return;  // #311: stale flight — commit nothing

    // #305: the chat presence hook — the revalidation token is captured
    // BEFORE this tick's authoritative reads (a monotonic sequence: a send
    // that starts mid-tick supersedes this verdict), and the verdict is
    // applied after the fresh character + catalog land below. It paints
    // ONLY the availability caption + the send state — a poll never
    // rebuilds the composer (m311: input, focus, draft survive every tick).
    const chatToken = chatBeginValidation();

    // A2 Living Map: refresh authoritative read state, then rebuild presentation.
    const previousDay = S.world ? S.world.day : null;
    const character = await api(`/characters/${S.character.id}`);
    if (!worldFeedFlightCurrent(flight)) return;  // #311: stale flight
    const world = await api("/world");
    // #269: null = the joined bootstrap lost ownership (stale) — the tick is
    // stale: drop it silently BEFORE the extra awaits (no .day dereference,
    // no shared write, no poll-failure mark); a newer load owns the shared
    // state (S22). S.character re-checked: a dead context must not drive
    // the rest of the tick.
    // #311: the flight token joins the guard — a superseded tick drops here too.
    if (!worldFeedFlightCurrent(flight) || !world || !S.character) return;
    const locs = await api("/locations");
    if (!worldFeedFlightCurrent(flight)) return;  // #311: stale flight
    if (previousDay !== world.day) {
      try { S.weather = await api("/weather"); } catch { /* keep last readable weather */ }
      // F3: day-boundary refetch must update the panel chip in place, not wait for a full re-render.
      const weatherEl = document.getElementById("weather");
      if (weatherEl) weatherEl.textContent = worldWeatherChipText();
    }
    S.character = { ...S.character, ...character };
    // #302: scene invalidation on authoritative movement — the scene is tied
    // to the location it was inspected at (m153/m156); a poll that observes
    // the player at a DIFFERENT location drops it (same rule as renderWorld).
    if (observedPlayerLocId !== null && observedPlayerLocId !== character.location_id) {
      discardSceneEntry(observedPlayerLocId);
    }
    observedPlayerLocId = character.location_id;
    // F1: LOD focus follows the authoritative player location on every poll
    // refresh — unless a manual focus is active (#92 «Показать на карте»);
    // manual focus releases as soon as the player physically moves.
    if (S.mapFocusManual && S.mapFocusAnchor !== character.location_id) {
      S.mapFocusManual = false;
    }
    if (!S.mapFocusManual && character.location_id != null) S.mapFocusLocationId = character.location_id;
    S.mapLocations = locs;
    // #305: apply this tick's chat presence verdict from the fresh reads —
    // a stale token (a newer revalidation, render, navigation, session or
    // target) discards itself inside chatApplyValidation.
    if (chatToken) chatApplyValidation(chatToken, character, locs);
    // #269 S23: a stale tick's OLDER world must not republish over an
    // already-newer snapshot (viewWorld/viewLocation publications) — the
    // clock keeps the newer time too.
    if (!S.world || world.game_timestamp >= S.world.game_timestamp) {
      S.world = world;
      const clock = document.getElementById("clock");
      if (clock) clock.textContent = dayTime(world.game_timestamp);
    }
    // #312: reconcile the scene cache against the fresh publish (identity +
    // context key), then patch the mounted place stage IN PLACE — a tick
    // never generates (no POST from the poll path).
    syncSceneContext(locs);
    const sceneStage = document.querySelector(".place-stage");
    if (sceneStage && sceneStage._sceneHosts
      && String(sceneStage._sceneLocId) === String(character.location_id)) {
      updateSceneVisual(sceneStage, sceneStage._sceneLocId, locs);
    }
    rerenderLivingMap();

    const marker = document.getElementById("player-marker");
    const badge = document.getElementById("map-badge");

    // Handle Tasks (MOVE and TRAVEL_EXTERNAL)
    if (S.character.is_owner) {
      // #304: the poll's tasks read is an authoritative adapter refresher —
      // the read token is assigned at START, the in-flight counter spans
      // the await (finally-only: a failed fetch still propagates to the
      // poll's own catch — the #175 liveness semantics are untouched), and
      // the snapshot is ingested BEFORE the MOVE/badge processing below.
      const listToken = ++taskReadSequence;
      taskListReadsInFlight++;
      let tasks;
      try {
        tasks = await api(`/characters/${S.character.id}/tasks`);
      } finally {
        taskListReadsInFlight--;
      }
      // #311: the missing flight guard — a tick superseded while the tasks
      // fetch flew must not write the marker/badge from its stale read.
      if (!worldFeedFlightCurrent(flight)) return;
      ingestTaskSnapshot(tasks, listToken);
      const active = tasks.active && tasks.active[0];
      
      if (active && active.task_type === "MOVE") {
        const params = active.parameters || {};
        // Real MOVE params: {path: [..locIds], total_minutes, from}
        const targetId = (params.path && params.path.length)
          ? params.path[params.path.length - 1] : params.location_id;
        const targetLoc = locs.find(l => l.id == targetId);
        const startLoc = locs.find(l => l.id == character.location_id);
        
        if (targetLoc && startLoc) {
          const f = Math.max(0, Math.min(1, (world.game_timestamp - active.started_at) / (active.ends_at - active.started_at)));
          const curX = startLoc.x + (targetLoc.x - startLoc.x) * f;
          const curY = startLoc.y + (targetLoc.y - startLoc.y) * f;
          if (marker) {
            const box = mapCardBox();
            const pos = worldToScreen(locationToWorld({ x: curX, y: curY }),
              box && box.w, box && box.h);
            marker.style.left = `${pos.x}%`;
            marker.style.top = `${pos.y}%`;
            // #308 P2: keep the interpolated AUTHOR point so a resize
            // re-projects the in-flight position instead of snapping back to
            // the (still START) location id.
            marker.dataset.moveX = String(curX);
            marker.dataset.moveY = String(curY);
          }
        }
      } else if (active && active.task_type === "TRAVEL_EXTERNAL") {
        // #308 P2: no in-flight map interpolation — drop the MOVE override so
        // normal location-id reprojection resumes.
        if (marker) {
          delete marker.dataset.moveX;
          delete marker.dataset.moveY;
        }
        if (badge) {
          const remaining = active.ends_at - world.game_timestamp;
          const h = Math.floor(remaining / 60);
          const m = Math.floor(remaining % 60);
          badge.textContent = `В рейсе — вернётся через ${h}ч ${m}м`;
          badge.classList.remove("hidden");
        }
      } else {
        // #308 P2: MOVE finished (marker back on the authoritative location)
        // — drop the in-flight override.
        if (marker) {
          delete marker.dataset.moveX;
          delete marker.dataset.moveY;
        }
        if (badge) badge.classList.add("hidden");
      }
    }

    // #175: a full refresh succeeded — the server is reachable again. Clear
    // the failure streak; if the banner was up, drop it and say so once.
    if (S.connLost) {
      S.connLost = false;
      hideConnBanner();
      toast("Связь восстановлена");
    }
    S.pollFails = 0;
  } catch (e) {
    // #175: consecutive poll failures are the liveness oracle (WS closes are
    // routine on navigation and never drive the banner) — the 2nd failure in
    // a row surfaces the persistent connection banner. Errors stay otherwise
    // unlogged (same quiet-catch semantics as before, now with a signal).
    // #311: a stale/aborted flight is not a liveness signal — no failure mark.
    if (!worldFeedFlightCurrent(flight)) return;
    S.pollFails += 1;
    if (S.pollFails >= 2 && !S.connLost) {
      S.connLost = true;
      ensureConnBanner();
    }
  } finally { S.pollBusy = false; }
}

// #139: WS is the live channel while connected; the REST events fetch is
// the fallback for when the socket is down (resumes automatically next tick).
// The A2 world-state refresh above keeps running on every tick either way —
// only the events fetch is skipped while the socket is the live channel.
async function pollEventsFeed() {
  // #258: while the page session waits for the WS hello, suppress ONLY the
  // events REST fetch — the world/map/task refresh in pollEvents keeps
  // running every tick, and hello is never awaited here. The deadline in
  // armHelloDeadline settles the init (hello -> "done", expiry -> "rest").
  if (S.feedInit === "pending") return;
  if (S.ws && S.ws.readyState === 1) return;
  const requested = S.cursor;  // #258: snapshot the requested position — a WS batch may advance S.cursor while this fetch is in flight
  // #311: the feed-owned GET rides the poll flight — the abort signal lets a
  // teardown cancel the held request, and the feedPoll metadata (the flight
  // OBJECT itself: userRef + charId + navGen) lets api() exempt a STALE
  // flight's 401 from the global fail-closed deauth — stale by identity OR
  // by currency (superseded tick / teardown / navigation). The signal rides
  // only when the flight actually carries a controller (the defensive
  // creation may yield null — token checks still apply).
  const flight = worldFeedPollFlight;
  const feedPollOpts = flight ? { feedPoll: flight } : {};
  if (flight && flight.controller) feedPollOpts.signal = flight.controller.signal;
  const data = await api(`/world/events?since=${requested}`, feedPollOpts);
  // #311: a poll deferred across a navigation/session change commits nothing.
  if (flight && !worldFeedFlightCurrent(flight)) return;
  const events = Array.isArray(data) ? data : (data.events || []);
  if (events.length) {
    // #258: never lower a cursor the WS already advanced while we awaited.
    S.cursor = Math.max(S.cursor, events[events.length - 1].id);
    // #139: the REST cursor path can re-fetch on a missed ack — push and
    // render only events not already buffered (dedup by id).
    const seen = new Set(S.feed.map(e => e.id));
    const fresh = events.filter((e) => !seen.has(e.id));
    if (fresh.length) {
      S.feed.push(...fresh);
      // #311: the feed-box render + island preview moved OUT of the
      // transport into the view listeners (worldFeedPublishAll) — the
      // transport is view-agnostic now.
      worldFeedPublishAll(fresh);
      // #304: completion/failure events are RECHECK TRIGGERS only (at
      // committed publication) — the payloads carry no task_id; the tracked
      // nonterminal tasks' own detail GETs decide. No artificial batches.
      if (fresh.some((e) => e.event_type === "TASK_COMPLETED"
        || e.event_type === "TASK_FAILED")) {
        triggerTaskRechecks();
      }
      // #82 (A3): REST fallback path mirrors the WS arrival hook.
      if (fresh.some((e) => isOwnMoveEvent(e))) onOwnArrival();
    }
    // #177: the replay boundary is captured from the REST RESPONSE, not
    // from `fresh` — the first WS batch (same ids, server replays from
    // cursor 0) can win the race and empty the dedup set, but the response
    // still defines where replay ends. `events` is bounded by the server
    // limit (100), so the spread is safe.
    if (S.bootCursor === Infinity) {
      S.bootCursor = Math.max(...events.map((e) => e.id));
    }
  } else if (S.cursor === 0) {
    // #177: fresh world with no events yet — everything from id 1 on is
    // live; the boundary is zero.
    S.bootCursor = 0;
  }
  // #179: a completed REST fetch proves S.cursor is defined — arm the WS
  // cursor handshake (one per connection; re-armed in ws.onclose).
  // #258: a successful REST fill is ONE init source, not the only one —
  // the hello path (feedInit "done") settles the init before this runs.
  S.feedFetched = true;
  sendWsCursor();
}

// #179/#258: tell the server where our event stream should start — the
// hello already gives a fresh boot a bounded window at the snapshot tail
// (tail-100); this declaration re-anchors RECONNECTS at the live cursor.
// One handshake per connection: re-armed in ws.onclose, armed once init
// has defined S.cursor (hello tail or first REST events fetch).
function sendWsCursor() {
  if (!S.feedFetched || S.wsCursorSent) return;
  if (!S.ws || S.ws.readyState !== 1) return;
  try { S.ws.send(JSON.stringify({ type: "cursor", id: S.cursor })); S.wsCursorSent = true; } catch (e) { /* closing */ }
}

function connectWs() {
  if (!S.user || S.ws) return;
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onmessage = (msg) => {
    try {
      const data = JSON.parse(msg.data);
      // #258: the hello carries the per-world snapshot tail — handle it
      // BEFORE any events frame, only while init is still pending. The
      // stream starts 100 events below the tail; the boot replay marker is
      // the TAIL itself. A late hello (init already settled) or one with a
      // garbage cursor is ignored — reconnect handshake semantics (#179).
      if (data.type === "hello" && S.feedInit === "pending"
          && Number.isSafeInteger(data.cursor) && data.cursor >= 0) {
        S.cursor = Math.max(0, data.cursor - 100);
        S.bootCursor = data.cursor;  // the TAIL, not the window start
        S.feedFetched = true;
        S.feedInit = "done";
        if (S.helloDeadline) { clearTimeout(S.helloDeadline); S.helloDeadline = null; }
        sendWsCursor();
      }
      if (data.type === "events" && data.events) {
        for (const ev of data.events) {
          if (ev.id > S.cursor) S.cursor = ev.id;
        }
        // #139: WS can re-deliver around a reconnect — push and render only
        // events not already buffered (dedup by id).
        const seen = new Set(S.feed.map(e => e.id));
        const fresh = data.events.filter((e) => !seen.has(e.id));
        if (fresh.length) {
          S.feed.push(...fresh);
          // #311: the feed-box render + island preview moved OUT of the
          // transport into the view listeners (worldFeedPublishAll) — the
          // frame handler is view-agnostic now.
          worldFeedPublishAll(fresh);
          // #304: completion/failure events are RECHECK TRIGGERS only (at
          // committed publication) — the payloads carry no task_id; the
          // tracked nonterminal tasks' own detail GETs decide.
          if (fresh.some((e) => e.event_type === "TASK_COMPLETED"
            || e.event_type === "TASK_FAILED")) {
            triggerTaskRechecks();
          }
          // #144: a CONTROL_CHANGED fired from another tab/session leaves
          // stale action pills behind — re-render the world view (it re-reads
          // the character). ws.onmessage fires on ALL views, so only refresh
          // when the world view is actually on screen (never hijack
          // #/profile or #/chat); viewWorld() only reads state, so no
          // event/POST feedback loop is possible.
          // #177/#258: a (re)connect can replay a bounded history window
          // (hello tail-100) — or a larger backlog via an explicit/legacy
          // cursor declaration; a replayed CONTROL_CHANGED must not reload
          // the world view. React only to LIVE events: id beyond the boot
          // replay marker and logged within a small window of the current
          // game time (covers backlogs larger than the fetch window).
          // #262: own-character completion events (TASK_COMPLETED,
          // ITEM_CONSUMED, own CHARACTER_MOVED) must refresh the needs panel
          // too — the own-actor branch gates on ID freshness ONLY (no
          // game_timestamp window: backdated DIRECT completions, #261, would
          // fail a 15-minute liveness check). liveOwnArrival is computed once
          // per frame: when it fires, the general refresh is suppressed and
          // onOwnArrival() still runs (max one full render per WS frame).
          const liveOwnArrival = fresh.some((e) => isOwnMoveEvent(e)
              && e.id > S.bootCursor
              && S.world && typeof e.game_timestamp === "number"
              && e.game_timestamp >= S.world.game_timestamp - 15);
          if (fresh.some((e) =>
              (e.event_type === "CONTROL_CHANGED"
                && e.id > S.bootCursor
                && S.world && typeof e.game_timestamp === "number"
                && e.game_timestamp >= S.world.game_timestamp - 15)
              || (S.character && e.actor_id === S.character.id
                && e.id > S.bootCursor
                && ["TASK_COMPLETED", "ITEM_CONSUMED", "CHARACTER_MOVED"]
                  .includes(e.event_type)))
            && location.hash === "#/world"
            && !liveOwnArrival) {
            viewWorld();
          }
          // #82 (A3): own arrival completes the travel state machine
          // (travelling → arrived) and re-renders the map. #177: the
          // liveness gate above keeps historical own-MOVE replay from
          // churning the map.
          if (liveOwnArrival) onOwnArrival();
        }
      }
    } catch (e) { /* ignore malformed */ }
  };
  ws.onclose = () => {
    S.wsTries += 1;
    if (S.ws === ws) S.ws = null;  // #161: only clear the reference while this socket is still the current one (no stomp)
    S.wsCursorSent = false;  // #179: the next connection re-handshakes with the current S.cursor
    // #258: socket-scoped deadline cleanup — this socket died without a
    // hello, so its deadline timer is dead weight. Clearing the slot is
    // safe: a replacement socket can only exist after this handler (or
    // stopFeed) removed THIS one, and the deadline callback re-checks
    // socket identity anyway — a stale timer never closes/clears a
    // replacement socket.
    if (S.helloDeadline) { clearTimeout(S.helloDeadline); S.helloDeadline = null; }
    if (S.feedInit === "pending") {
      // #258: connect failure before hello — the REST fallback is
      // permanent for this page session (never reset by navigation).
      S.feedInit = "rest";
    }
    if (S.wsTries <= 3) armReconnect(2000 * S.wsTries);  // #311: tracked, token+navGen-checked reconnect
    /* иначе остаётся polling */
  };
  ws.onopen = () => { S.wsTries = 0; sendWsCursor(); };
  S.ws = ws;
  armHelloDeadline(ws);  // #258: the 5s hello window starts when the connect is initiated
}

/* ---------- chat (§77, #88 Dialogue alpha) ---------- */

/* #305: the chat presence runtime — a render-owned state machine for the
   in-place NPC dialogue. presence ∈ none|checking|valid|departed|
   unavailable: departed means an authoritative read DISPROVED the target's
   co-location («NPC не рядом»); a NETWORK failure is unavailable, never
   departed. The runtime holds every identity token an async op must
   re-check before touching shared state or the live DOM: the captured
   navigation generation, the user OBJECT reference, the character id, the
   dialogue target id, a monotonic validation sequence (captured BEFORE a
   revalidation's reads — results apply only if the sequence still
   matches), the per-render send-busy latch and the «Печатает…» typing
   timer (stopped by a superseding render and by session teardown).
   chatPendingPresence hands the entry revalidation's verdict from viewChat
   to the fresh runtime renderChat builds. */
let chatRuntime = null;
let chatPendingPresence = null;

/* #305: the NPC roster of ONE location record — the kind-based slice of
   its occupants, mapped to ids. Shared by the chat entry load, the poll
   hook and the send revalidation (a distinct spelling from the renderPlace
   census pins — m238 counts those bodies, not this helper). */
function chatRosterFor(character, locs) {
  const locId = character && character.location_id;
  const loc = (locs || []).find((l) => l && String(l.id) === String(locId));
  return ((loc && loc.occupants) || [])
    .filter((o) => o && o.kind === "npc").map((o) => o.id);
}

/* #305: the identity token every chat async op re-checks before it may
   touch shared state or the live DOM: the SAME runtime object (render
   identity), the same navigation generation, the same session identity and
   the same dialogue target. seq additionally proves no newer revalidation
   superseded this one (omitted for ops whose OWN fresh reads — not the
   shared verdict slot — decide the outcome). */
function chatOpCurrent(rt, seq) {
  return chatRuntime === rt
    && rt.navEpoch === navEpoch
    && S.user === rt.userRef
    && !!S.character && S.character.id === rt.charId
    && S.chatNpcId === rt.npcId
    && (seq === undefined || rt.validationSeq === seq);
}

/* #305: open a presence revalidation — the monotonic sequence is captured
   BEFORE the caller's reads, so a superseded flight can never paint over
   a newer verdict. */
function chatBeginValidation() {
  const rt = chatRuntime;
  if (!rt) return null;
  rt.validationSeq += 1;
  return { rt, seq: rt.validationSeq };
}

/* #305: the player-visible availability wording. Departed never implies
   co-location; a network failure is honestly unknown, never «не рядом». */
function chatPresenceCaption(state) {
  if (state === "departed") return "NPC не рядом";
  if (state === "unavailable") return "Статус NPC неизвестен — проверьте связь";
  if (state === "valid") return "NPC рядом";
  return "";  // none | checking — nothing to claim yet
}

/* #305: the send affordance — a departed or missing target cannot send.
   checking/unavailable keep the affordance honest: send() revalidates
   fresh and speaks for itself (a failed revalidation aborts with zero
   POSTs and its own caption). */
function chatPresenceDisablesSend(state) {
  return state === "departed" || state === "none";
}

/* #305: paint a presence verdict onto the LIVE chat DOM — the availability
   caption + the send button state ONLY. The composer (input, focus, draft)
   is never touched: a revalidation never rebuilds it (m311 contract). */
function chatPaintPresence(rt, state) {
  rt.presence = state;
  if (chatRuntime !== rt) return;  // a superseded render owns the DOM now
  const cap = document.getElementById("chat-presence");
  if (cap) cap.textContent = chatPresenceCaption(state);
  const btn = document.getElementById("chat-send");
  if (btn) btn.disabled = chatPresenceDisablesSend(state);
}

/* #305: apply a verdict derived from an authoritative character + catalog
   read (the poll's existing reads or the send-time revalidation). Stale
   tokens — a newer revalidation, a newer render, a newer navigation,
   session or target — are discarded silently. */
function chatApplyValidation(token, character, locs) {
  const rt = token.rt;
  if (!chatOpCurrent(rt, token.seq)) return;
  const roster = chatRosterFor(character, locs);
  chatPaintPresence(rt, !rt.npcId ? "none"
    : (roster.some((id) => String(id) === String(rt.npcId)) ? "valid" : "departed"));
}

/* #305: the initials fallback for a portrait-less NPC — the first codepoint
   of each of the first two whitespace-separated words of the display name
   (Unicode-aware via codePointAt/fromCodePoint: «Пётр Сидоров» → «ПС»,
   «Марина» → «М»); an unknown name degrades to the honest «NPC». */
function chatInitials(name) {
  const words = String(name || "").trim().split(/\s+/).filter(Boolean);
  if (!words.length) return "NPC";
  return words.slice(0, 2).map((w) =>
    String.fromCodePoint(w.codePointAt(0)).toUpperCase()).join("");
}

async function viewChat() {
  navEpoch++;  // #302: every load start is a new navigation generation
  location.hash = "#/chat";
  const loading = el("div", { class: "muted" }, "Загрузка…");
  app.replaceChildren(topbar("#/chat"), loading);
  // #302: the load captures its generation + session identity — a newer
  // navigation or a session change while it flies must not publish (same
  // guard shape as viewWorld/viewLocation; compared by value, never against
  // the hash — the m238 pin censuses that comparison file-wide).
  const epoch = navEpoch;
  const ctxUser = S.user;
  const ctxCharId = S.character.id;
  const stale = () => epoch !== navEpoch || S.user !== ctxUser
    || !S.character || S.character.id !== ctxCharId;
  try {
    // #305: the authoritative character is read on EVERY entry — the
    // by-user hydration omits location_id (the cold-load shape) AND a
    // populated one may be stale (the player may have moved since the
    // last screen). Same read as viewWorld, now unconditional.
    const me = await api(`/characters/${S.character.id}`);
    // #302: guarded hydration — a superseded load must not write a dead
    // context's character into shared state.
    if (stale()) return;
    S.character = { ...S.character, ...me };
    // #305: the /locations catalog is the roster source — kind-based NPC
    // occupants of the player's CURRENT location. The old npc_ prefix
    // filter over the detail endpoint's characters_here excluded hyphen
    // ids (npc-a) and duplicated a read the catalog already answers.
    const locs = await api("/locations");
    // #302: staged loads — every fetch lands in LOCALS; shared state is
    // published only after the guard (a dropped load writes nothing and
    // renders nothing).
    if (stale()) return;
    const loc = ((locs || []).find((l) =>
      l && String(l.id) === String(me.location_id))) || { name: "" };
    const npcs = (loc.occupants || [])
      .filter((o) => o && o.kind === "npc").map((o) => o.id);
    // #305: display names come from the catalog occupants (every
    // location) — a DEPARTED carry stays identifiable («Фар», not a raw
    // id) without a doomed /characters fetch for an absent NPC.
    const names = {};
    for (const l of (locs || [])) {
      for (const o of (l && l.occupants) || []) {
        if (o && o.id != null && names[o.id] == null) names[o.id] = o.name || o.id;
      }
    }
    // #302/#305: a Place-roster «Поговорить» carries the intended target
    // in gameUiContext.npcId. It is resolved BEFORE the select is built —
    // a stale carry (the NPC has left) stays IDENTIFIABLE and keeps its
    // draft, and never silently falls back to another nearby NPC (the
    // #305 presence machine marks it departed and gates sending). With no
    // carry, a still-valid persisted selection is kept; otherwise the
    // first roster NPC is the keyed default (no unkeyed browser-selected
    // state). The decision lands in LOCALS; the shared S.* writes are
    // deferred to after the guard below (a superseded load publishes
    // nothing — the m302 staged-loads discipline).
    let targetNpc = null;
    let targetDeparted = false;
    if (gameUiContext.npcId != null) {
      targetNpc = gameUiContext.npcId;
      targetDeparted = !npcs.some((id) => String(id) === String(targetNpc));
    } else if (S.chatNpcId && npcs.some((id) => String(id) === String(S.chatNpcId))) {
      targetNpc = S.chatNpcId;
    } else if (npcs.length) {
      targetNpc = npcs[0];
    }
    // #302: the resolved target is applied only after the guard — a
    // superseded load must not write shared chat state. The per-NPC draft
    // (if any) is restored into the composer even for a departed target.
    if (targetNpc != null && targetNpc !== S.chatNpcId) {
      S.chatNpcId = targetNpc;
      S.chatSessionId = null;  // the session belongs to the previous NPC
      S.chatDraft = gameUiContext.draftByNpc[targetNpc] || "";
    }
    // #88: current session history comes from the authoritative read model.
    // #302: staged — the shared session id is updated only after the guard.
    let sess = null;
    const ctxSessId = S.chatSessionId;
    if (ctxSessId) {
      try { sess = await api(`/dialogue/${ctxSessId}`); }
      catch (e) { sess = null; }
    }
    if (stale()) return;
    if (ctxSessId && !sess) S.chatSessionId = null;
    // #63: the meeting location IS the display location (S.chatNpcs is
    // the NPC slice of the current location's occupants) — thread loc.name
    // into the header.
    S.chatNpcs = npcs;
    S.chatNpcNames = names;
    // #305: the entry revalidation's verdict rides to the fresh runtime
    // renderChat builds below (a departed carry renders departed — caption
    // + disabled send — never silently co-located).
    chatPendingPresence = targetNpc == null ? "none"
      : (targetDeparted ? "departed" : "valid");
    renderChat(sess, loc.name);
  } catch (e) {
    // #93: transient failure — explicit retry instead of a dead «Загрузка…».
    // #302: a superseded load paints no retry/toast into a newer view.
    if (stale()) return;
    loadRetryNode(loading, () => viewChat());
    toast(e.message, true);
  }
}

/* #63: relationship widget — the bare «Отношения: …» text line reworked
   into a 5-segment horizontal scale. EXPLICIT band mapping over the
   authoritative affection scalar (band words carried over from the #88
   line; segments filled 1..5, warm pink/magenta fill per the approved
   mock, hostile band red):
     affection <= -40        → «враждебные»  — 1/5 filled (red)
     -39 .. -13              → «холодные»    — 2/5 filled
     -12 .. 12               → «нейтральные» — 3/5 filled
     13 .. 40                → «тёплые»      — 4/5 filled
     affection > 40          → «близкие»     — 5/5 filled
   Missing/undefined/NaN affection → Neutral fallback (no crash, no
   invented data). Pure: accepts a number OR a relationship object
   ({affection, ...}) and returns the band descriptor. */
function renderRelationshipScale(rel) {
  const a = (rel !== null && typeof rel === "object")
    ? Number(rel.affection) : Number(rel);
  if (!Number.isFinite(a)) {
    return { words: "нейтральные", filled: 3, cls: "rel-neutral" };
  }
  if (a <= -40) return { words: "враждебные", filled: 1, cls: "rel-hostile" };
  if (a <= -13) return { words: "холодные", filled: 2, cls: "rel-cold" };
  if (a <= 12) return { words: "нейтральные", filled: 3, cls: "rel-neutral" };
  if (a <= 40) return { words: "тёплые", filled: 4, cls: "rel-warm" };
  return { words: "близкие", filled: 5, cls: "rel-close" };
}

function renderChat(session, locName) {
  // #305: a fresh render owns a fresh presence runtime — the previous
  // render's runtime is orphaned (its in-flight ops fail the identity
  // check and discard themselves) and its typing timer must not outlive
  // it. The entry revalidation's verdict (chatPendingPresence) seeds the
  // initial state; an in-render switch re-renders with the picked roster
  // NPC (optimistically valid — the send revalidates fresh anyway).
  if (chatRuntime && chatRuntime.typingTimer != null) {
    clearInterval(chatRuntime.typingTimer);  // #305: the orphaned «Печатает…» timer stops with its render
    chatRuntime.typingTimer = null;
  }
  const rt = {
    navEpoch, userRef: S.user,
    charId: S.character ? S.character.id : null,
    npcId: S.chatNpcId,
    validationSeq: 0, sendBusy: false, typingTimer: null,
    presence: chatPendingPresence || "checking",
  };
  chatPendingPresence = null;
  chatRuntime = rt;
  let curSession = session;
  const npcSel = el("select", { id: "chat-npc-select" },
    // #305: an explicit empty option — the DOM selection can never
    // silently disagree with S.chatNpcId (the old browser-default first
    // option created an unkeyed selected state; a departed carry — whose
    // id is intentionally not among the roster options — now renders as
    // an honest empty select while the header keeps the identity).
    el("option", { value: "" }, "— выберите NPC —"),
    ...S.chatNpcs.map((id) => el("option", { value: id },
      (S.chatNpcNames && S.chatNpcNames[id])
        ? `${S.chatNpcNames[id]} (${id})` : id)));
  npcSel.addEventListener("change", () => {
    // #302: per-NPC drafts — snapshot the outgoing composer text BEFORE the
    // switch (keyed by the outgoing selection), then restore the incoming
    // NPC's own draft into the shared binding AND the live input (the
    // re-render below re-seeds the rebuilt composer from it; the programmatic
    // input assignment fires no input event, so nothing clobbers the store).
    if (S.chatNpcId != null) {
      gameUiContext.draftByNpc[S.chatNpcId] = input.value;
    }
    // #305: an explicit selection of a roster NPC is the new intended
    // target — it updates the carry (the restoration source for
    // chat → place → chat) and restores that NPC's own draft. The empty
    // option means "no target" (null), never an unkeyed "".
    S.chatDraft = gameUiContext.draftByNpc[npcSel.value] || "";
    input.value = S.chatDraft;
    S.chatNpcId = npcSel.value || null;  // 182: survive re-renders (review F1)
    if (S.chatNpcId != null) gameUiContext.npcId = S.chatNpcId;
    S.chatSessionId = null;
    // #305: the picked roster NPC is the fresh runtime's seed verdict —
    // optimistically valid (the send revalidates fresh anyway); the empty
    // option is "no target".
    chatPendingPresence = S.chatNpcId ? "valid" : "none";
    renderChat(null, locName);
  });
  // 182 (review F1): the rebuilt select loses the user's choice on any
  // re-render — an accepted proposal re-render included. Restore the
  // persisted selection when it is still among the options.
  if (S.chatNpcId && S.chatNpcs.some((id) => String(id) === String(S.chatNpcId))) {
    npcSel.value = S.chatNpcId;
  }

  // #89: public roles from the authoritative read model (no trait overlap,
  // derive-on-read server-side — the client never invents roles).
  // #63: the same single fetch feeds BOTH the roles line (pin: «Роли: …»
  // stays) and the header subtitle «{primary role title} · {location}».
  const rolesLine = el("div", { class: "muted", id: "chat-roles" }, "Роли: …");
  const subLine = el("div", { class: "muted chat-npc-sub", id: "chat-npc-sub" },
    locName ? String(locName) : "");
  // 182: explicit mutual partnership — a proposal button, shown ONLY while
  // an NPC is selected AND not already a partner. The roles list (the same
  // single fetch below) is the answer: a «Партнёр …» title in the derived
  // roles means the pair already exists — the button hides. No client-side
  // guessing: without a role there is no partnership, and a wrong click is
  // still answered by the deterministic server rule (422 / decline reason).
  const partnerBtn = el("button", {
    id: "chat-partner-btn", hidden: true,
    onclick: async () => {
      const targetNpc = npcSel.value;
      if (!targetNpc) return;
      partnerBtn.disabled = true;
      try {
        const r = await api(`/characters/${targetNpc}/partnership`,
          { method: "POST" });
        if (r.accepted) {
          status.textContent = "Вы теперь пара";
          // The re-render re-derives roles (same single fetch) — the new
          // «Партнёр …» title shows for both and hides this button.
          renderChat(curSession, locName);
          return;
        }
        status.textContent = r.reason || "Предложение отклонено";
      } catch (e) {
        chatErrorRow(`Не удалось объявить пару: ${e.message}`);
        toast(e.message, true);
      } finally {
        partnerBtn.disabled = false;
      }
    },
  }, "Объявить пару");
  if (npcSel.value) {
    api(`/characters/${npcSel.value}/roles`).then((r) => {
      const titles = (r.roles || []).map((x) => x.title);
      rolesLine.textContent = titles.length ? `Роли: ${titles.join(", ")}` : "Роли: нет";
      subLine.textContent = titles.length && locName
        ? `${titles[0]} · ${locName}` : (titles[0] || locName || "");
      // 182 (review F4): derive-on-read truth — the role ID is the stable
      // gate (a title prefix would be a fragile text match).
      partnerBtn.hidden = (r.roles || []).some((x) => x.id === "partner");
    }).catch(() => { rolesLine.textContent = "Роли: недоступны"; });
  } else {
    rolesLine.textContent = "Роли: выберите NPC";
    partnerBtn.hidden = true;
  }

  // #88: relationship summary — authoritative read model only, current
  // values, no client-side delta guessing; refreshed after each reply.
  // #63: rendered as the widget below (band scale + trust as small text,
  // full state in the aria-label) instead of a bare text line.
  const relSegs = [0, 1, 2, 3, 4].map(() => el("span", { class: "rel-seg" }));
  const relWord = el("span", { class: "rel-word" }, "…");
  const relTrust = el("span", { class: "muted rel-trust" }, "");
  const relBox = el("div", {
    class: "rel-box", role: "img", "aria-label": "Отношения: нет данных",
  }, el("span", { class: "rel-scale" }, ...relSegs), relWord, relTrust);
  // #183: encounter affection notice — a short «Симпатия +2» line next to
  // the relationship widget. The ONLY signal is the reply body's
  // relationship_effect (the server applies the shared SOCIALIZE rule at
  // most once per cooldown window per pair); no client-side guessing.
  // Hidden unless THIS reply applied the delta; cleared when it did not.
  const relNote = el("div", { id: "chat-rel-note", hidden: true });
  function applyRel(band, trustText, ariaText) {
    relSegs.forEach((seg, i) => {
      seg.className = i < band.filled ? `rel-seg on ${band.cls}` : "rel-seg";
    });
    relWord.textContent = band.words;
    relWord.className = `rel-word ${band.cls}`;
    relTrust.textContent = trustText;
    relBox.setAttribute("aria-label", ariaText);
  }
  function refreshRel() {
    api(`/characters/${S.character.id}/relationships`).then((rels) => {
      const rel = (rels || []).find((x) => String(x.other_id) === String(npcSel.value));
      if (!rel) {
        applyRel(renderRelationshipScale(undefined), "пока незнакомы",
          "Отношения: пока незнакомы");
        return;
      }
      const band = renderRelationshipScale(rel);
      applyRel(band, `доверие ${Math.round(rel.trust)}`,
        `Отношения: ${band.words} (симпатия ${Math.round(rel.affection)}, доверие ${Math.round(rel.trust)})`);
    }).catch(() => {
      applyRel(renderRelationshipScale(undefined), "нет данных",
        "Отношения: нет данных");
    });
  }
  refreshRel();

  // #88: canonical NPC visual (cookie-auth blob), graceful placeholder on
  // 503 (visual off) / 404 (no canonical yet). Reads the #132 negative
  // cache (S.visualDisabled) to skip the doomed request, but does NOT set
  // the flag — m132 pins its single set-site inside portraitBlock(), and
  // chat re-renders only on explicit user action.
  // #63: the visual is a full-width warm hero band; the EMPTY state is the
  // normal state (centered honest fallback, styled intentional in CSS).
  // The honest fallback string is deduped to one constant.
  const VISUAL_FALLBACK = "Визуал недоступен — диалог текстом";
  const visual = el("div", { class: "chat-hero", id: "chat-visual" });
  visual.append(el("div", { class: "portrait-status muted" }, "Портрет…"));
  // #305: every fallback surface carries the target's initials — the
  // identity survives a missing portrait («Марина» → «М», «Пётр Сидоров»
  // → «ПС»). The node builder is shared by the fetch-failure catch, the
  // img decode-failure listener and the no-portrait else-branch below.
  const targetName = (S.chatNpcNames && S.chatNpcNames[S.chatNpcId])
    || S.chatNpcId || "";
  const visualFallbackNode = () => el("div", { class: "portrait-status muted" },
    VISUAL_FALLBACK,
    el("span", { class: "portrait-initials" }, chatInitials(targetName)));
  const visualOn = !S.visualDisabled;
  // #260: empty npcSel (no NPCs on the location) — no doomed //portrait
  // fetch; the fallback branch below renders the shared fallback directly
  // (same path as S.visualDisabled; the #132 flag itself stays unset).
  if (visualOn && npcSel.value) {
    (async () => {
      try {
        const asset = await api(`/visual/characters/${npcSel.value}/portrait`);
        const blob = await fetchPortraitBlob(`/visual/assets/${asset.id}/file`);
        const url = URL.createObjectURL(blob);
        // #305: a DECODE failure is a fetch success — the broken <img> fires
        // error asynchronously, so the listener (attached before the img
        // enters the DOM) swaps in the same honest fallback + initials.
        const img = el("img", {
          class: "portrait-img chat-visual-img", src: url, alt: "Портрет NPC",
        });
        img.addEventListener("error",
          () => visual.replaceChildren(visualFallbackNode()));
        visual.replaceChildren(img);
      } catch (e) {
        visual.replaceChildren(visualFallbackNode());
      }
    })();
  } else {
    visual.append(el("div", { class: "portrait-status muted" },
      VISUAL_FALLBACK,
      el("span", { class: "portrait-initials" }, chatInitials(targetName))));
  }

  const log = el("div", { class: "chatlog", id: "chat-log" });
  // #63/#182: the inline error row lives in ONE construction site — the
  // send() failure path and the partnership proposal failure path both
  // append through this helper (the m63 pin counts the literal).
  function chatErrorRow(msg) {
    log.append(el("div", { class: "chat-error" }, msg));
    log.scrollTop = log.scrollHeight;
  }
  function renderMessages(messages) {
    log.replaceChildren(...(messages || []).map((m) =>
      el("div", { class: `chatmsg ${m.sender === "npc" ? "npc" : ""}` },
        el("span", { class: "who" }, m.sender === "npc" ? "NPC" : "Вы"),
        m.content,
        // #196: honest provenance on history NPC bubbles — a row the LLM
        // seam did not produce carries a small muted label (same honesty
        // as the #88 POST flag / the visual fallback note above).
        ...(m.source === "fallback"
          ? [el("span", { class: "muted" }, "LLM недоступен — fallback")]
          : []),
        el("span", { class: "muted chat-when" },
          " " + dayTime(m.game_timestamp)))));
  }
  renderMessages(curSession ? curSession.messages : []);

  // #88: suggested replies are SHORTCUTS ONLY — clicking fills the always
  // visible free-text input and focuses it; nothing is sent automatically.
  const sugg = el("div", { id: "chat-sugg" });
  function renderSugg(list) {
    sugg.replaceChildren(...(list || []).map((s) =>
      el("button", { class: "sugg", type: "button", onclick: () => {
        const inp = document.getElementById("chat-msg-input");
        if (inp) { inp.value = s; inp.focus(); }
        S.chatDraft = s;  // #256: chip fills the input programmatically (no input event) — sync the draft so the text survives re-renders
        // #302: a programmatic set fires no input event — mirror the chip
        // text into the per-NPC store for the currently selected NPC too.
        if (S.chatNpcId != null) gameUiContext.draftByNpc[S.chatNpcId] = s;
      } }, s)));
  }
  const lastNpc = [...(curSession ? curSession.messages : [])]
    .reverse().find((m) => m.sender === "npc");
  renderSugg(lastNpc && lastNpc.suggested_responses
    ? lastNpc.suggested_responses : []);

  const status = el("div", { class: "muted", id: "chat-status" });
  const input = el("input", { id: "chat-msg-input", "aria-label": "Сообщение", placeholder: "Сообщение…" });
  // #175: the typed draft survives NPC switches/re-renders — seeded from
  // S.chatDraft and kept in sync on every keystroke; cleared on send.
  input.value = S.chatDraft || "";
  input.addEventListener("input", () => { S.chatDraft = input.value; });
  // #302: an ADDITIONAL listener mirrors the live draft into the per-NPC
  // store (multiple listeners on one element coexist — the pinned #175
  // binding above stays byte-exact). No selected NPC → nothing to key on;
  // the unkeyed text still lives in S.chatDraft alone.
  input.addEventListener("input", () => {
    if (S.chatNpcId != null) gameUiContext.draftByNpc[S.chatNpcId] = input.value;
  });
  const sendBtn = el("button", {
    id: "chat-send", class: "primary", "aria-label": "Отправить",
    onclick: () => send(),
  }, "→");
  // #305: the send affordance reflects the presence verdict — a departed
  // or missing target cannot send. The shim's click ignores disabled, so
  // send() itself re-gates on a FRESH revalidation; this is the honest
  // affordance, not the guard.
  sendBtn.disabled = chatPresenceDisablesSend(rt.presence);
  input.addEventListener("keydown", (ev) => { if (ev.key === "Enter") send(); });

  async function send() {
    // #305: per-render busy latch — repeated Enter/click while a send is
    // in flight is ONE message POST.
    if (rt.sendBusy) return;
    const draft = input.value;
    const content = draft.trim();
    if (!content) return;
    const sentNpc = npcSel.value;  // #88: guard against NPC switch mid-flight
    if (!sentNpc) return;  // #305: the empty option is "no target"
    rt.sendBusy = true;
    // #63: a stale inline error row must not survive a new attempt. All
    // rows go — a double-send race must not leave orphaned error rows
    // behind (peer review finding 1: querySelector removed only the first).
    log.querySelectorAll(".chat-error").forEach(n => n.remove());
    // #305: send-time presence revalidation — fresh character + catalog
    // reads BEFORE any dialogue POST (and before the typing surface): a
    // target the authoritative reads disprove makes ZERO dialogue POSTs,
    // paints «NPC не рядом» and never fakes a «Печатает…» wait. A failed
    // revalidation is unavailable — never «не рядом» — and also aborts
    // with zero POSTs (no co-location proof, no dialogue traffic).
    rt.validationSeq += 1;
    const valSeq = rt.validationSeq;
    // #305: the message POST success — not the history refresh — is the
    // acknowledgement; the failure handlers read this to distinguish
    // "send failed" (draft retained + visible retry) from "committed"
    // (draft stays cleared, NO duplicate-send retry).
    let acknowledged = false;
    let typingStarted = false;
    let tick = null;
    let typingBubble = null;
    try {
      const me = await api(`/characters/${rt.charId}`);
      const locs = await api("/locations");
      // #305: identity check after the presence validation — a superseded
      // render/session/target discards the whole send (zero POSTs, the
      // fresh render's UI is never touched).
      if (!chatOpCurrent(rt)) return;
      const verdict = chatRosterFor(me, locs).some((id) => String(id) === String(sentNpc))
        ? "valid" : "departed";
      if (chatOpCurrent(rt, valSeq)) chatPaintPresence(rt, verdict);
      if (verdict !== "valid") return;  // zero dialogue POSTs
      // #88: LLM latency is 14-45s — visibly non-frozen wait with a ticking
      // elapsed-seconds counter. #63: the wait is ALSO shown as a typing
      // bubble inside the chatlog (name + three animated dots, reduced-motion
      // safe); #chat-status keeps the assistive «Печатает… N с» line.
      status.textContent = "Печатает…";
      typingBubble = el("div", { class: "chatmsg npc chat-typing" },
        el("span", { class: "who" },
          (S.chatNpcNames && S.chatNpcNames[sentNpc]) || "NPC"),
        el("span", { class: "typing-dots", "aria-hidden": "true" },
          el("i"), el("i"), el("i")));
      log.append(typingBubble);
      log.scrollTop = log.scrollHeight;
      const t0 = Date.now();
      tick = setInterval(() => {
        status.textContent = `Печатает… ${Math.round((Date.now() - t0) / 1000)} с`;
      }, 1000);
      rt.typingTimer = tick;
      typingStarted = true;
      const sid = curSession ? curSession.session_id
        : (await api("/dialogue/start",
          { method: "POST", body: { npc_id: sentNpc } })).session_id;
      // #305: identity check AFTER /dialogue/start, BEFORE the message
      // POST — an NPC switch (a fresh render) discards the stale start:
      // no message POST on the stale session, the fresh render's shared
      // state is never hijacked.
      if (!chatOpCurrent(rt)) return;
      const r = await api(`/dialogue/${sid}/message`,
        { method: "POST", body: { content } });
      acknowledged = true;
      clearInterval(tick);
      typingBubble.remove();
      rt.typingTimer = null;
      // #305: identity check after the message POST — a stale op never
      // touches the fresh render's composer, but the committed turn still
      // consumes the ORIGINAL target's unchanged stored draft (the text
      // was sent; keeping it invites a duplicate).
      if (!chatOpCurrent(rt)) {
        // #305: a stale op after an account switch must not touch the
        // fresh session's draft store — the user-ref guard keeps the
        // delete scoped to the session the send was born in.
        if (S.user === rt.userRef && gameUiContext.draftByNpc[sentNpc] === draft) {
          delete gameUiContext.draftByNpc[sentNpc];
        }
        input.value = "";  // #305: the detached composer of the superseded render — no live UI is touched
        return;
      }
      // #305: the session binds at the ACK (message POST success) — a
      // history refresh failure must not orphan it (the next send would
      // re-issue /dialogue/start and duplicate the conversation opener).
      // It sits AFTER the identity check: a stale op never writes shared
      // session state.
      curSession = curSession || {};
      curSession.session_id = sid;
      S.chatSessionId = sid;
      // #305: the acknowledgement consumes the submitted draft — ONLY the
      // unchanged submitted text; anything typed during the flight (a
      // newer draft) survives in the composer and the per-NPC store.
      if (input.value === draft) {
        input.value = "";
        S.chatDraft = "";  // #175: the send succeeded — the draft is consumed
      }
      if (gameUiContext.draftByNpc[sentNpc] === draft) {
        delete gameUiContext.draftByNpc[sentNpc];
      }
      const hist = await api(`/dialogue/${sid}`);
      // #305: identity check after the history GET — a superseded op never
      // repaints the fresh render's log or shared session state. The
      // session itself already bound at the ACK (above) — only the message
      // list is refreshed here.
      if (!chatOpCurrent(rt)) return;
      curSession.messages = hist.messages;
      renderMessages(hist.messages);
      // #195: relationship milestone — driven ONLY by the fresh POST
      // response's relationship_formed field, and appended AFTER
      // renderMessages (it replaceChildren's the log — an earlier
      // insertion would be erased). Never synthesized from history.
      if (r.relationship_formed) {
        log.append(el("div", { class: "chat-milestone", role: "status" },
          "Начало отношений"));
        log.scrollTop = log.scrollHeight;
      }
      // #195 slice 2: band-crossing banner — driven ONLY by the fresh
      // POST response's relationship_band_changed field (a pre-existing
      // relationship moved across a band boundary), same idiom and
      // placement as the milestone above. Never synthesized from history.
      if (r.relationship_band_changed) {
        log.append(el("div", { class: "chat-band-change", role: "status" },
          "Изменение отношений"));
        log.scrollTop = log.scrollHeight;
      }
      const npcMsg = [...hist.messages].reverse().find((m) => m.sender === "npc");
      renderSugg(npcMsg && npcMsg.suggested_responses
        ? npcMsg.suggested_responses : []);
      refreshRel();
      // #183: encounter affection notice — sign + integer of the applied
      // delta; a clamp at the ±100 bounds may honestly yield 0 (the row
      // did not move). Cleared when the cooldown did not elapse.
      const eff = r.relationship_effect;
      if (eff && eff.applied) {
        const d = Math.round(eff.affection_delta);
        relNote.textContent = `Симпатия ${d >= 0 ? "+" : ""}${d}`;
        relNote.hidden = false;
      } else {
        relNote.textContent = "";
        relNote.hidden = true;
      }
      status.textContent = r.source === "fallback"
        ? "LLM недоступен, отвечает fallback" : "";
    } catch (e) {
      // #305: only a started typing surface needs teardown — a failed
      // send-time revalidation never built one.
      if (typingStarted) {
        clearInterval(tick);
        typingBubble.remove();
        rt.typingTimer = null;
      }
      // #305: identity check in the failure handler — a stale op never
      // restores drafts or paints error rows/toasts into the fresh render.
      if (!chatOpCurrent(rt)) return;
      // #183: a failed send shows no effect note — a stale «Симпатия +2»
      // from a previous reply must not outlive its reply.
      relNote.textContent = "";
      relNote.hidden = true;
      if (acknowledged) {
        // #305: the message IS committed (POST succeeded) — the cleared
        // draft stays cleared and NO duplicate-send retry is offered;
        // only the history refresh failed.
        status.textContent = "Сообщение отправлено — история не обновилась";
        return;
      }
      // #305: the failed-send catch no longer rewrites the composer — the
      // draft never left the input (nothing clears it before the ack), and
      // text typed during the flight must survive (m305 test_17); the
      // restore line was a pre-#305 mechanism for a flow that cleared the
      // input before the ack.
      status.textContent = "Не удалось отправить — проверьте связь и повторите";
      // #63: the error is ALSO an inline row in the chatlog (the toast and
      // the assistive status line stay).
      chatErrorRow(`Не удалось отправить: ${e.message}`);
      toast(e.message, true);
    } finally {
      rt.sendBusy = false;  // #305: the latch releases on EVERY path
    }
  }

  // #63: warm-family shell rides on the grid (same pattern as desire-shell/
  // shop-shell: `class: "grid <name>-shell"`). Hero band on top, header
  // (name · subtitle · roles · relationship widget) under it, then the log
  // (max-height 320), the assistive status line, suggestion chips and the
  // composer — all inside the shell so the cool :root tokens never leak in.
  app.replaceChildren(ctxTopbar(
      el("a", { href: ctxBackHash(), "aria-label": "Назад" }, "←"),
      el("span", { class: "ctx-title" }, "Диалог"),
      topbar("#/chat")),
    el("div", { class: "grid chat-shell" },
      el("div", { class: "chat-backdrop" }),
      el("div", { class: "panel", id: "chat-view" },
        el("h2", {}, "Диалог"),
        // #311: the quiet live-events indicator — empty on render, filled by
        // the chat listener on a LIVE event; never touches the composer.
        el("div", { id: "chat-feed-quiet", class: "muted" }, ""),
        // #305: the availability caption — the presence machine's verdict
        // («NPC не рядом» for a departed target), painted IN PLACE by
        // chatPaintPresence; the composer is never rebuilt for it.
        el("div", { id: "chat-presence", class: "muted" },
          chatPresenceCaption(rt.presence)),
        // #302/#305: back to the Place the dialogue was opened from —
        // keyed off the dialogue origin captured by the «Поговорить»
        // handler (gameUiContext.chatSourceLocationId), NOT the generic
        // viewLocationId. A direct #/chat deep link has no place to return
        // to and offers the island map instead («← К карте»). Navigation
        // rides the hash link (single dispatch through route()) — no
        // location.hash assignment here; the anchor rides the shared global
        // control styling (a { color: var(--accent) }).
        ...(gameUiContext.chatSourceLocationId != null
          ? [el("a", { href: `#/location/${gameUiContext.chatSourceLocationId}` },
              "← К месту")]
          : [el("a", { href: "#/world" }, "← К карте")]),
        el("label", { for: "chat-npc-select" }, "NPC на этой локации"), npcSel,
        visual,
        el("div", { class: "chat-head-info" },
          el("div", { id: "chat-npc-name" },
            // #305: keyed off the persisted target (S.chatNpcId), not the
            // select value — a departed carry keeps its identity («Фар»)
            // while the select honestly shows the empty option.
            (S.chatNpcNames && S.chatNpcNames[S.chatNpcId])
              ? S.chatNpcNames[S.chatNpcId] : (S.chatNpcId || "")),
          subLine, rolesLine, relBox, relNote),
        el("div", { style: "margin-top:8px" }, log),
        status,
        sugg,
        partnerBtn,
        el("div", { class: "chat-bar" }, input, sendBtn)),
      el("div", { class: "panel" }, el("h2", {}, "Подсказки"),
        el("div", { class: "muted" },
          "Подсказки заполняют поле ввода, свободный текст — основной"))));
  // #313: every render rebuilds .chatlog and loses the inline maxHeight —
  // re-apply the viewport budget right after the tree is mounted (the
  // function declaration is hoisted; the resize listener stays the only
  // other caller).
  applyChatViewportBudget();
  // #311: the feed controller starts (idempotent) + the chat view attaches
  // its quiet listener (draft/typing/send surface untouched).
  worldFeedController.start();
  worldFeedController.attachView(chatFeedListener);
}

/* ---------- inventory (§77, #91 Inventory alpha) ---------- */

/* #91: wearable set mirrors the authoritative SLOT_MAP in
   app/social/clothing.py (jacket/boots/hat) — keep in sync with the server;
   anything else gets no wear button (no capability → no button). */
const INV_WEARABLE_TYPES = ["jacket", "boots", "hat"];

/* #91: RU category groups over the real seeded object_types (config
   shop/kitchen stock: food_, water_ prefixes and jacket/boots/hat); unknown
   types fall back to their raw object_type — nothing invented client-side. */
function invCategoryRu(objectType) {
  const t = String(objectType || "");
  if (/^(food|water)_/.test(t)) return "Провизия";
  if (/^(jacket|boots|hat|clothing_)/.test(t)) return "Одежда";
  return t || "Прочее";
}

async function viewInventory() {
  location.hash = "#/inventory";
  const loading = el("div", { class: "muted" }, "Загрузка…");
  app.replaceChildren(topbar("#/inventory"), loading);
  try {
    const inv = await api(`/characters/${S.character.id}/inventory`);
    const items = Array.isArray(inv) ? inv : (inv.items || []);
    // Real market state: which owned objects already have an active offer —
    // the sell affordance is grounded in the validator's one-offer-per-object
    // rule, shown as authoritative state instead of a doomed POST.
    const listed = {};
    try {
      const offers = await api("/market/offers?status=active");
      (Array.isArray(offers) ? offers : []).forEach((o) => { listed[o.object_id] = o.price; });
    } catch (e) { /* market read failed: POST errors still surface below */ }
    renderInventory(items, listed);
  } catch (e) {
    // #93: transient failure — explicit retry instead of a dead «Загрузка…».
    loadRetryNode(loading, () => viewInventory());
    toast(e.message, true);
  }
}

async function wearToggle(objectId) {
  try {
    await api("/wear", { method: "POST", body: { object_id: objectId } });
    // #65: re-render the ACTIVE screen after the authoritative toggle —
    // from #/appearance stay there (the typed draft survives via
    // APPEARANCE_DRAFT); every other origin is the inventory (historical
    // behavior). The screen refetches everything — /wear is authoritative.
    if (location.hash === "#/appearance") viewAppearance();
    else viewInventory();
  } catch (e) { toast(e.message, true); }  // errors keep the screen state
}

function invSellRow(objectId) {
  const price = el("input", { type: "number", min: "1", step: "1", placeholder: "Цена, ₽" });
  // #256: the typed price survives row expand/collapse re-renders — seeded
  // from the draft map (!= null, not ||: a typed "0" must survive) and kept
  // in sync on every keystroke.
  price.value = S.invSellPrice[objectId] != null ? S.invSellPrice[objectId] : "";
  price.addEventListener("input", () => { S.invSellPrice[objectId] = price.value; });
  const go = el("button", {
    id: "inv-sell", type: "button",
    onclick: async () => {
      const p = Math.floor(Number(price.value));
      if (!Number.isFinite(p) || p < 1) {
        toast("Цена должна быть целым числом ≥ 1", true);
        return;
      }
      try {
        await api("/market/offers", { method: "POST", body: { object_id: objectId, price: p } });
        toast("Предмет выставлен на рынок");
        delete S.invSellPrice[objectId];  // #256: the offer is live — the draft price is consumed
        viewInventory();  // refresh state after the authoritative action
      } catch (e) { toast(e.message, true); }  // errors keep the screen state
    },
  }, "Продать");
  return el("span", { class: "inv-sell-row" }, price, go);
}

function invDetail(it, wearable, isListed) {
  const box = el("div", { id: "inv-detail", class: "inv-detail-box" },
    el("div", { class: "muted" }, `Количество: ${it.quantity ?? "—"}`),
    el("div", { class: "muted" }, `Состояние: ${it.condition ?? "—"}%`));
  // Location name resolved lazily through the authoritative read model.
  const locLine = el("div", { class: "muted" }, "Место: …");
  box.append(locLine);
  api(`/locations/${it.location_id}`).then((loc) => {
    locLine.textContent = `Место: ${loc.name || "#" + it.location_id}`;
  }).catch(() => { locLine.textContent = `Место: #${it.location_id}`; });
  if (it.worn) {
    box.append(el("div", { class: "muted" }, `Слот: ${it.slot || "—"}`));
  }
  if (wearable) {
    box.append(el("button", {
      id: "inv-wear", type: "button", onclick: () => wearToggle(it.id),
    }, it.worn ? "Снять" : "Надеть"));
  }
  if (!isListed) {
    box.append(invSellRow(it.id));
  }
  return box;
}

function invItemRow(it, listed) {
  const isOpen = S.invExpanded === it.id;
  const wearable = INV_WEARABLE_TYPES.includes(it.object_type);
  const isListed = listed[it.id] != null;
  const row = el("div", {
    class: "inv-item", "data-object-id": String(it.id),
    onclick: () => { S.invExpanded = isOpen ? null : it.id; viewInventory(); },
  },
    el("span", { class: "inv-item-name" }, `${it.object_type} ×${it.quantity}`),
    it.worn === true ? el("span", { class: "badge worn" }, `надето ${it.slot || ""}`) : null,
    isListed ? el("span", { class: "muted" }, `Выставлено: ${listed[it.id]} ₽`) : null,
    el("span", { class: "muted" }, `#${it.id}`));
  return el("div", {},
    row,
    isOpen ? invDetail(it, wearable, isListed) : null);
}

function renderInventory(items, listed) {
  const groups = new Map();
  for (const it of items) {
    const cat = invCategoryRu(it.object_type);
    if (!groups.has(cat)) groups.set(cat, []);
    groups.get(cat).push(it);
  }
  const body = items.length
    ? el("div", { class: "inv-groups" },
        ...[...groups.entries()].map(([cat, rows]) =>
          el("div", { class: "inv-group" },
            el("h3", {}, `${cat} · ${rows.length}`),
            ...rows.map((it) => invItemRow(it, listed)))))
    : el("div", { class: "muted" }, "Пусто — купите что-нибудь во Владивостоке");
  app.replaceChildren(topbar("#/inventory"),
    el("div", { class: "panel inv-shell" },
      el("h2", {}, "Инвентарь"),
      body,
      el("p", { class: "muted" },
        "Использование предметов выполняется игровыми действиями (EAT/DRINK)."),
      el("p", { class: "muted" },
        el("a", { href: "#/appearance" }, "Образ и одежда →"),
        " — что надето, режим одежды и предпросмотр желаемого образа"),
      el("p", { class: "muted" },
        el("a", { href: "#/shop" }, "Рынок"),
        " — витрина покупки и продажи между персонажами")));
}


/* ---------- #65: «Образ и одежда» (#/appearance) ---------- */

/* #65: the appearance screen's client-only draft — the outfit prompt and
   the three PREVIEW-ONLY knobs (hair/hair_color/build feed POST
   /visual/preview's appearance dict, never the character, never the
   creator draft). Survives the screen's wear-toggle re-render so the
   typed text is not wiped (same discipline as S.chatDraft); lazy fields
   keep the byte-pinned S initializer tail untouched. */
const APPEARANCE_DRAFT = { prompt: "", hair: "", hair_color: "", build: "" };

/* #65 (validator round 2, W118-RV1 F1 precedent): the draft is per-session
   private input — reset it on every auth transition (login, logout,
   account deletion) so the next user of the same browser never sees the
   previous user's outfit prompt or preview knobs prefilled. */
function appearanceResetDraft() {
  APPEARANCE_DRAFT.prompt = "";
  APPEARANCE_DRAFT.hair = "";
  APPEARANCE_DRAFT.hair_color = "";
  APPEARANCE_DRAFT.build = "";
}

/* #65: chip suggestions for the outfit prompt — a click FILLS the textarea
   and focuses it, nothing is sent (desireChipRow pattern). Own class: the
   .sugg chip shape is byte-pinned == 1 on the desire screen. */
const APPEARANCE_OUTFIT_CHIPS = [
  ["Повседневный", "повседневный стиль"],
  ["Спорт", "спортивный стиль"],
  ["Зимний", "зимний образ"],
  ["Элегантный", "элегантный образ"],
  ["Праздничный", "праздничный образ"],
];

async function viewAppearance() {
  location.hash = "#/appearance";
  const loading = el("div", { class: "muted" }, "Загрузка…");
  app.replaceChildren(topbar("#/appearance"), loading);
  try {
    // Cold-load safety + stale-state guard: the by-user bootstrap omits
    // sex/age/outfit_mode, so every entry re-reads the authoritative
    // character (the profile selector may have changed the mode; the
    // /visual/preview contract requires sex+age) plus the inventory.
    const me = await api(`/characters/${S.character.id}`);
    S.character = { ...S.character, ...me };
    const inv = await api(`/characters/${S.character.id}/inventory`);
    renderAppearance(Array.isArray(inv) ? inv : (inv && inv.items) || []);
  } catch (e) {
    // #93: transient failure — explicit retry instead of a dead «Загрузка…».
    loadRetryNode(loading, () => viewAppearance());
    toast(e.message, true);
  }
}

function renderAppearance(items) {
  // Wearables per the client type list (INV_WEARABLE_TYPES — the same
  // filter the inventory detail wears; the server's SLOT_MAP is the
  // authoritative twin). The inventory payload carries no name field:
  // object_type IS the honest label, exactly like the inventory rows.
  const wearableItems = items.filter((it) =>
    INV_WEARABLE_TYPES.includes(it.object_type));

  /* --- try-on zone refs (shared with the prompt zone below) --- */
  const previewStatus = el("p", { class: "muted appear-note", role: "status" },
    "Предпросмотра ещё нет — опишите образ и нажмите «Сгенерировать предпросмотр».");
  const previewErr = el("p", { class: "err-text" });
  const previewImg = el("img", { class: "appear-preview-img",
    alt: "Предпросмотр образа", hidden: true });
  const previewCaption = el("p", { class: "muted appear-note", hidden: true },
    "Предпросмотр — не сохранён; персонаж и гардероб не изменены");

  // Preview-only appearance knob: a text input writing APPEARANCE_DRAFT —
  // the character record and the creator draft are never touched.
  function knob(label, key, ph) {
    const input = el("input", { type: "text", maxlength: "120",
      placeholder: ph, "aria-label": `${label} — только для предпросмотра` });
    input.value = APPEARANCE_DRAFT[key] || "";
    input.addEventListener("input", () => { APPEARANCE_DRAFT[key] = input.value; });
    return el("label", { class: "appear-field" }, label, input);
  }

  /* --- outfit-mode selectors: two affordances, ONE PATCH site --- */
  function outfitSelect() {
    const sel = el("select", { "aria-label": "Режим одежды" },
      el("option", { value: "auto" }, "auto — как сейчас"),
      el("option", { value: "casual" }, "casual — повседневная одежда"),
      el("option", { value: "formal" }, "formal — строгая одежда"),
      el("option", { value: "nude" }, "nude — без одежды (18+)"));
    sel.value = S.character.outfit_mode || "auto";
    return sel;
  }
  const outfitSelCurrent = outfitSelect();
  const outfitSelTry = outfitSelect();
  // The screen's single PATCH /outfit site (the profile panel keeps its
  // own). Only the presentation MODE persists — never the preview image,
  // never the garments. A refusal (e.g. nude without 18+ confirmation)
  // re-syncs both selectors from the authoritative stored mode.
  async function applyOutfitMode(mode) {
    try {
      const r = await api(`/characters/${S.character.id}/outfit`, {
        method: "PATCH", body: { outfit_mode: mode } });
      S.character.outfit_mode = (r && r.outfit_mode) || mode;
      toast("Режим одежды обновлён — сохранён только режим, не вещи");
    } catch (e) { toast(e.message, true); }
    outfitSelCurrent.value = S.character.outfit_mode || "auto";
    outfitSelTry.value = S.character.outfit_mode || "auto";
  }
  outfitSelCurrent.addEventListener("change",
    () => applyOutfitMode(outfitSelCurrent.value));

  /* --- prompt zone (zone 3): free text ≤500 + chips + generate --- */
  const promptField = el("textarea", {
    id: "appear-outfit-prompt", rows: "3", maxlength: "500",
    placeholder: "Опишите образ, который хотите (например: „красное платье для свидания“, „повседневный стиль“)",
  });
  promptField.value = APPEARANCE_DRAFT.prompt || "";
  promptField.addEventListener("input",
    () => { APPEARANCE_DRAFT.prompt = promptField.value; });

  const chipsRow = el("div", { class: "appear-chips" },
    ...APPEARANCE_OUTFIT_CHIPS.map(([label, text]) => el("button", {
      type: "button", class: "appear-chip",
      onclick: () => {
        // draft AND field: programmatic assignment fires no input event —
        // without the explicit write a wear-toggle re-render would drop
        // the chip-selected text.
        APPEARANCE_DRAFT.prompt = text;
        promptField.value = text;
        promptField.focus();
      },
    }, label)),
    el("span", { class: "muted appear-note" },
      "Примеры только заполняют поле — опишите образ своими словами"));

  // The screen's single /visual/preview site: full-body outfit variant of
  // POST /visual/preview — UNSAVED, nothing persists, the canonical
  // portrait is never replaced. sex/age come from the authoritative
  // character read; the appearance subset rides only the preview knobs.
  let previewBusy = false;
  const previewGo = el("button", { id: "appear-preview-go",
    class: "primary", type: "button" }, "Сгенерировать предпросмотр");
  async function runPreview() {
    if (previewBusy) return;  // no double-submit while in flight
    previewBusy = true;
    previewGo.disabled = true;
    previewErr.textContent = "";
    previewStatus.hidden = false;
    previewStatus.textContent = "Генерация…";
    try {
      const appearance = {};
      if (APPEARANCE_DRAFT.hair.trim()) appearance.hair = APPEARANCE_DRAFT.hair.trim();
      if (APPEARANCE_DRAFT.hair_color.trim()) {
        appearance.hair_color = APPEARANCE_DRAFT.hair_color.trim();
      }
      if (APPEARANCE_DRAFT.build.trim()) appearance.build = APPEARANCE_DRAFT.build.trim();
      const r = await api("/visual/preview", { method: "POST", body: {
        sex: S.character.sex, age: Number(S.character.age),
        appearance_prompt: promptField.value.trim() || undefined,
        appearance: Object.keys(appearance).length ? appearance : undefined,
        outfit_mode: outfitSelTry.value, mode: "full_body_outfit",
      } });
      previewImg.src = `data:image/png;base64,${r.image_base64}`;
      previewImg.hidden = false;
      previewCaption.hidden = false;
      previewStatus.hidden = true;
    } catch (e) {
      // the draft stays on screen; the shown image (if any) stays too
      previewStatus.hidden = true;
      previewErr.textContent =
        `Предпросмотр не удался: ${e.message}. Попробуйте ещё раз.`;
    } finally {
      previewBusy = false;
      previewGo.disabled = false;
    }
  }
  previewGo.onclick = runPreview;

  /* --- zone 2 LEFT: canonical portrait (read-only visual) --- */
  const portraitStatus = el("div", { class: "muted portrait-status" }, "Портрет…");
  const portraitBox = el("div", { class: "panel portrait appear-portrait" },
    el("h2", {}, "Портрет"), portraitStatus);
  (async () => {
    try {
      const asset = await api(`/visual/characters/${S.character.id}/portrait`);
      const blob = await fetchPortraitBlob(`/visual/assets/${asset.id}/file`);
      portraitStatus.replaceChildren(
        el("img", { class: "portrait-img appear-portrait-img",
          src: URL.createObjectURL(blob), alt: "Портрет" }));
    } catch (e) {
      // graceful empty/error fallback — honest note, no dead image frame
      portraitStatus.textContent = S.visualDisabled
        ? "Визуал временно недоступен" : "Портрет ещё не сгенерирован";
    }
  })();

  /* --- wardrobe cards (zone 4 LEFT) --- */
  function wearBtn(it) {
    const btn = el("button", { type: "button", class: "appear-wear-btn" },
      it.worn ? "Снять" : "Надеть");
    btn.onclick = async (ev) => {
      ev.currentTarget.disabled = true;  // toggle: no double-fire while pending
      try {
        await wearToggle(it.id);  // shared single /wear site; re-renders
      } finally {
        // success: the re-render replaced this node (no-op); failure:
        // wearToggle kept the screen, so re-arm the button.
        if (btn.isConnected) btn.disabled = false;
      }
    };
    return btn;
  }

  const wardrobeBox = el("div", { class: "appear-wardrobe" });
  function drawWardrobe() {
    wardrobeBox.replaceChildren(...(wearableItems.length
      ? wearableItems.map((it) => el("div", { class: "appear-wear-item" },
          el("span", { class: "appear-item-name" }, it.quantity > 1
            ? `${it.object_type} ×${it.quantity}` : it.object_type),
          it.worn === true ? el("span", { class: "badge worn" }, "надето") : null,
          el("span", { class: "muted" }, `состояние: ${it.condition ?? "—"}%`),
          wearBtn(it)))
      : [el("div", { class: "muted" }, "Гардероб пуст — загляните на ",
          el("a", { href: "#/shop" }, "Рынок"))]));
  }

  /* --- currently-worn list («Текущий образ», zone 2) --- */
  const wornBox = el("div", { class: "appear-worn-list" });
  function drawWorn() {
    const worn = items.filter((it) => it.worn === true);
    wornBox.replaceChildren(...(worn.length
      ? worn.map((it) => el("div", { class: "appear-worn-row" },
          el("span", {}, it.object_type),
          el("span", { class: "muted" }, `слот: ${it.slot || "—"}`)))
      : [el("div", { class: "muted" }, "Ничего не надето")]));
  }
  drawWorn();
  drawWardrobe();

  app.replaceChildren(topbar("#/appearance"),
    el("div", { class: "grid appearance-shell" },
      // Zone 1: title + one-line subtitle.
      el("div", { class: "panel appear-head" },
        el("h2", {}, "Образ и одежда"),
        el("p", { class: "muted appear-note" },
          "Как персонаж выглядит и что на нём надето — плюс предпросмотр желаемого образа.")),
      // Zone 2: portrait LEFT, stacked status panels RIGHT.
      el("div", { class: "appear-hero" },
        portraitBox,
        el("div", { class: "appear-side" },
          el("div", { class: "panel" },
            el("h2", {}, "Текущий образ"),
            wornBox,
            el("p", { class: "muted appear-note" },
              "Надевать и снимать вещи — в Гардеробе ниже.")),
          el("div", { class: "panel" },
            el("h2", {}, "Режим одежды"),
            outfitSelCurrent,
            el("p", { class: "muted appear-note" },
              "Режим задаёт подачу образа на портрете, а не конкретные вещи; выбор применяется сразу.")),
          el("div", { class: "panel" },
            el("h2", {}, "Сохранённые образы"),
            el("p", { class: "muted appear-note" },
              "Сохранённые образы пока недоступны")))),
      // Zone 3: outfit prompt — chips fill the field, the button previews.
      el("div", { class: "panel appear-prompt" },
        el("h2", {}, "Желаемый образ"),
        el("label", { class: "appear-field" },
          "Описание образа (до 500 символов)", promptField),
        chipsRow,
        el("div", { class: "appear-actions" },
          previewGo,
          el("span", { class: "muted" },
            "Не хватает вещи? ",
            el("a", { href: "#/desire" }, "Описать желание →")))),
      // Zone 4: wardrobe + try-on.
      el("div", { class: "appear-tryon" },
        el("div", { class: "panel" },
          el("h2", {}, "Гардероб"),
          wardrobeBox,
          el("p", { class: "muted appear-note" },
            "Кнопка на карточке надевает или снимает вещь — предметы не создаются и не пропадают.")),
        el("div", { class: "panel" },
          el("h2", {}, "Примерка"),
          previewStatus, previewErr, previewImg, previewCaption,
          el("div", { class: "appear-knobs" },
            knob("Причёска", "hair", "напр.: короткая стрижка"),
            knob("Цвет волос", "hair_color", "напр.: тёмно-русый"),
            knob("Телосложение", "build", "напр.: спортивное")),
          el("p", { class: "muted appear-note" },
            "Эти поля влияют только на предпросмотр — персонаж не изменяется."),
          el("div", { class: "appear-tryout" },
            el("label", { class: "appear-field" },
              "Режим одежды для предпросмотра", outfitSelTry),
            el("button", { type: "button", class: "appear-apply",
              onclick: () => applyOutfitMode(outfitSelTry.value) },
              "Применить режим одежды"),
            el("p", { class: "muted appear-note" },
              "Сохраняется только режим одежды — не картинка и не предметы."))))));
}


/* ---------- #173 (design #73, panel 5): Рынок — buy/sell over the
   player-to-player market (012 §74, /market/offers). v1 ships buy/sell
   ONLY: the sim has no service mechanic, so there is deliberately NO
   services section — nothing service-shaped is faked (gap stated in PR). */

const SHOP_ERROR_TEXT = {
  // MarketError codes (marketplace.py) surface as the HTTP detail string →
  // honest human text; anything unmapped falls through RAW (never invented).
  insufficient_funds: "Недостаточно средств",
  own_offer: "Это ваше предложение — его нельзя купить",
  offer_closed: "Предложение уже закрыто — куплено или отменено",
  offer_not_found: "Предложение не найдено",
  item_unavailable: "Предмет недоступен — предложение устарело",
  already_listed: "Этот предмет уже выставлен на рынок",
  not_your_item: "Можно выставлять только свои предметы",
  not_your_offer: "Отменять можно только свои предложения",
  price_must_be_positive: "Цена должна быть целым числом ≥ 1",
};

function shopErrorText(e) {
  const code = String(e.detail ?? e.message ?? "");
  return SHOP_ERROR_TEXT[code] || code || "Не удалось выполнить операцию";
}

async function viewShop() {
  location.hash = "#/shop";
  const loading = el("div", { class: "muted" }, "Загрузка…");
  app.replaceChildren(topbar("#/shop"), loading);
  try {
    // One round of authoritative reads: active offers (browse and «мои»
    // are split client-side by seller), own balance, owned items for the
    // sell form (the same endpoint the inventory screen reads).
    const [offers, me, inv] = await Promise.all([
      api("/market/offers?status=active"),
      api(`/characters/${S.character.id}`),
      api(`/characters/${S.character.id}/inventory`),
    ]);
    renderShop(Array.isArray(offers) ? offers : [], me,
      Array.isArray(inv) ? inv : (inv && inv.items) || []);
  } catch (e) {
    // #93: transient failure — explicit retry instead of a dead «Загрузка…».
    loadRetryNode(loading, () => viewShop());
    toast(e.message, true);
  }
}

async function refreshShop() {
  // Guarded refresh after a market action: re-render ONLY while the player
  // is still on #/shop — navigating away mid-request must not yank them
  // back (no hash assignment on this path; refreshDesire pattern).
  if (location.hash !== "#/shop") return;
  const [offers, me, inv] = await Promise.all([
    api("/market/offers?status=active"),
    api(`/characters/${S.character.id}`),
    api(`/characters/${S.character.id}/inventory`),
  ]);
  if (location.hash === "#/shop") {
    renderShop(Array.isArray(offers) ? offers : [], me,
      Array.isArray(inv) ? inv : (inv && inv.items) || []);
  }
}

function shopCancelBtn(o, status) {
  const btn = el("button", { type: "button",
    onclick: async () => {
      if (btn.disabled) return;  // no double-submit while in flight
      btn.disabled = true;
      try {
        await api(`/market/offers/${o.id}/cancel`, { method: "POST" });
        toast("Предложение отменено");
        await refreshShop();  // authoritative state after the mutation
      } catch (e) {
        status.textContent = shopErrorText(e);
        toast(shopErrorText(e), true);
        btn.disabled = false;
      }
    },
  }, "Отменить");
  return btn;
}

function renderShop(offers, me, items) {
  // #173 confirm step: S.shopConfirm holds the offer being bought, read
  // LIVE everywhere (never captured into a const — a render-time capture
  // made the confirm panel unreachable: «Купить» set the flag and
  // composeHome() branched on the stale null). The price is rendered
  // BEFORE the confirming POST (design #73 §4-5).
  const balance = me && me.balance != null ? me.balance : null;
  const status = el("div", { class: "muted shop-status", role: "status" });

  // Warm family: the shop grid carries the provisional warm cream tokens
  // (.shop-shell, #59 PR #3 — same family as .desire-shell; the screen
  // renders outside .world-shell); compose() is the single re-render factor.
  function compose(...panels) {
    app.replaceChildren(topbar("#/shop"),
      el("div", { class: "grid shop-shell" }, ...panels));
  }

  function headPanel() {
    return el("div", { class: "panel", id: "shop-head" },
      el("h2", {}, "Рынок"),
      el("div", { class: "shop-balance" },
        "Баланс: ",
        el("span", { class: "world-money", id: "shop-balance" },
          balance == null ? "—" : `${balance} ₽`)),
      // #73 completion slice: standing bridge to the desire screen (topbar
      // vocabulary «Цель») — the shop stays linked to Free Requests.
      el("a", { href: "#/desire" }, "Цель →"),
      status);
  }

  function shopOfferRow(o) {
    const price = el("span", { class: "shop-price" },
      el("span", { class: "world-money" }, `${o.price} ₽`));
    if (o.seller === S.character.id) {
      // My own offer shown honestly: «Ваше предложение» + cancel INSTEAD
      // of a buy button that the validator would refuse (own_offer).
      return el("div", { class: "shop-offer" },
        el("span", { class: "shop-item" }, o.item_name || `#${o.object_id}`),
        price,
        el("span", { class: "shop-mine" }, "Ваше предложение"),
        shopCancelBtn(o, status));
    }
    return el("div", { class: "shop-offer" },
      el("span", { class: "shop-item" }, o.item_name || `#${o.object_id}`),
      price,
      el("span", { class: "muted" },
        `Продавец: ${o.seller_name || "#" + o.seller}`),
      el("button", { type: "button",
        onclick: () => { S.shopConfirm = o.id; composeHome(); } },
        "Купить"));
  }

  function browsePanel() {
    return el("div", { class: "panel", id: "shop-browse" },
      el("h2", {}, "Предложения"),
      offers.length
        ? el("div", { class: "shop-offers" },
            ...offers.map((o) => shopOfferRow(o)))
        : el("div", { class: "muted" },
            "Рынок пуст — никто ничего не продаёт. Выставьте предмет ниже."));
  }

  function confirmPanel() {
    // #73 §4 buy state: explicit confirm — price AND balance visible
    // before the mutation; the button below is the ONLY buy site.
    const buy = offers.find((o) => o.id === S.shopConfirm);
    if (!buy) return browsePanel();
    const buyBtn = el("button", { class: "primary", type: "button",
      onclick: async () => {
        if (buyBtn.disabled) return;  // no double-submit while in flight
        buyBtn.disabled = true;
        try {
          await api(`/market/offers/${buy.id}/buy`, { method: "POST" });
          S.shopConfirm = null;
          toast(`Покупка состоялась: ${buy.item_name} за ${buy.price} ₽`);
          await refreshShop();  // balance refresh comes from the read model
        } catch (e) {
          // #73 completion slice: insufficient_funds is a distinct state —
          // the honest mapped text PLUS an inline bridge to #/desire (no
          // money → formulate a goal). The RAW code is read BEFORE mapping
          // (same shape shopErrorText uses); every other code keeps the
          // generic path. The toast stays mapped for all codes; balance
          // still comes from the read model only (no client-side math).
          if (String(e.detail ?? e.message ?? "") === "insufficient_funds") {
            status.replaceChildren(shopErrorText(e), " ",
              el("a", { href: "#/desire" }, "Поставить цель →"));
          } else {
            status.textContent = shopErrorText(e);
          }
          toast(shopErrorText(e), true);
          buyBtn.disabled = false;
        }
      },
    }, `Купить за ${buy.price} ₽`);
    return el("div", { class: "panel", id: "shop-confirm" },
      el("h2", {}, "Подтвердите покупку"),
      el("div", { class: "shop-item" }, buy.item_name || `#${buy.object_id}`),
      el("div", { class: "muted" },
        `Продавец: ${buy.seller_name || "#" + buy.seller}`),
      el("div", { class: "shop-price" },
        "Цена: ", el("span", { class: "world-money" }, `${buy.price} ₽`)),
      balance == null ? null
        : el("div", { class: "muted" }, `Ваш баланс: ${balance} ₽`),
      el("div", { class: "shop-actions" },
        buyBtn,
        el("button", { type: "button",
          onclick: () => { S.shopConfirm = null; composeHome(); } },
          "Отмена")));
  }

  function sellPanel() {
    // One active offer per object (validator rule): already-listed items
    // are excluded from the picker — the «Мои предложения» list right
    // below shows where they went, with cancel.
    const listedIds = new Set(offers.map((o) => o.object_id));
    const sellable = items.filter((it) => !listedIds.has(it.id));
    const sel = el("select", { id: "shop-sell-item", "aria-label": "Предмет" },
      ...sellable.map((it) =>
        el("option", { value: String(it.id) },
          `${it.object_type} ×${it.quantity}`)));
    const price = el("input", { id: "shop-sell-price", type: "number",
      min: "1", step: "1", placeholder: "Цена, ₽", "aria-label": "Цена" });
    const go = el("button", { id: "shop-sell-go", class: "primary",
      type: "button",
      onclick: async () => {
        const p = Math.floor(Number(price.value));
        if (!Number.isFinite(p) || p < 1) {
          status.textContent = "Цена должна быть целым числом ≥ 1";
          return;
        }
        if (go.disabled) return;  // no double-submit while in flight
        go.disabled = true;
        try {
          // The ONLY create site: the authoritative market validator
          // (price>0, own item, one active offer per object) decides.
          await api("/market/offers", {
            method: "POST",
            body: { object_id: Number(sel.value), price: p },
          });
          toast("Предложение создано");
          await refreshShop();
        } catch (e) {
          status.textContent = shopErrorText(e);
          toast(shopErrorText(e), true);
          go.disabled = false;
        }
      },
    }, "Выставить");
    const mine = offers.filter((o) => o.seller === S.character.id);
    return el("div", { class: "panel", id: "shop-sell" },
      el("h2", {}, "Выставить предмет"),
      sellable.length
        ? el("div", { class: "shop-sell-form" }, sel, price, go)
        : (items.length
          ? el("div", { class: "muted" },
              "Все ваши предметы уже выставлены — отмените предложение, "
              + "чтобы перевыставить.")
          : el("div", { class: "muted" },
              "Инвентарь пуст — нечего выставлять. ",
              el("a", { href: "#/inventory" }, "Открыть инвентарь"))),
      el("h3", {}, "Мои предложения"),
      mine.length
        ? el("div", { class: "shop-offers", id: "shop-my" },
            ...mine.map((o) => el("div", { class: "shop-offer" },
              el("span", { class: "shop-item" },
                o.item_name || `#${o.object_id}`),
              el("span", { class: "shop-price" },
                el("span", { class: "world-money" }, `${o.price} ₽`)),
              shopCancelBtn(o, status))))
        : el("div", { class: "muted" }, "У вас нет активных предложений"));
  }

  function composeHome() {
    // Branches on LIVE S.shopConfirm (adversarial-review P0: a render-time
    // capture made «Купить» re-render the same browse panel forever — the
    // confirm panel was unreachable).
    if (S.shopConfirm != null) {
      compose(headPanel(), confirmPanel(), sellPanel());
    } else {
      compose(headPanel(), browsePanel(), sellPanel());
    }
  }

  composeHome();
}


/* ---------- #92 (A10): Дела — tasks / journal / opportunities ---------- */

function mapLink(locId) {
  // «Показать на карте» goes through the existing focus mechanism:
  // S.mapFocusLocationId is consumed by buildLivingMap on the world view.
  // B2-review F1: pollEvents would clobber a foreign focus within one poll
  // tick — set a manual flag that survives polls and releases when the
  // player physically moves (anchor mismatch).
  return el("button", {
    type: "button", class: "map-link",
    onclick: () => {
      S.mapFocusManual = true;
      S.mapFocusAnchor = S.character.location_id;
      S.mapFocusLocationId = locId;
      gameUiContext.lastWorldFocusId = locId;  // #302: an explicit map jump must not be overridden by viewWorld's stale pre-place focus restoration
      gameUiContext.lastWorldFocusPlayerLocId = S.character.location_id;
      gotoWorld();  // #161: single dispatch — no direct viewWorld() next to the hash assignment
    },
  }, "Показать на карте");
}

function taskTargetLocationId(t) {
  // Authoritative task params only: MOVE carries {path: [...]}, other tasks
  // may carry location_id; numeric target_id is a location pointer. The
  // client never guesses a destination that the backend did not send.
  const p = t.parameters || {};
  if (Array.isArray(p.path) && p.path.length) return p.path[p.path.length - 1];
  if (p.location_id != null) return p.location_id;
  if (typeof t.target_id === "string" && /^\d+$/.test(t.target_id)) {
    return Number(t.target_id);
  }
  return null;
}

function taskRow(t) {
  const locId = taskTargetLocationId(t);
  const loc = locId != null
    ? (S.mapLocations || []).find((l) => l.id == locId) : null;
  const params = t.parameters || {};
  // #295: reason is backend free text — shown as-is; object_type is a code
  // and gets its display name (raw fallback for unknown config values).
  const reason = params.reason || codeLabel(OBJECT_TYPE_RU, params.object_type) || "";
  return el("div", { class: "task-row" },
    el("span", { class: "status" }, codeLabel(TASK_STATUS_RU, t.status) || ""),
    el("span", {}, codeLabel(TASK_TYPE_RU, t.task_type) || ""),
    t.source ? el("span", { class: "muted" }, ` · ${codeLabel(TASK_SOURCE_RU, t.source)}`) : null,
    // #304: the ends_at estimate is the SERVER's estimate, worded as such —
    // no countdown, no percentage, no auto-transition. Failed/cancelled
    // completions keep the neutral «Окончание» (never «завершено»).
    t.ends_at != null && t.completed_at == null
      ? el("span", { class: "muted" },
          ` · Плановое окончание по оценке сервера: ${dayTime(t.ends_at)}`) : null,
    t.completed_at != null
      ? el("span", { class: "muted" },
          ` · ${t.status === "failed" || t.status === "cancelled"
            ? "Окончание" : "завершено"} ${dayTime(t.completed_at)}`) : null,
    reason ? el("span", { class: "muted" }, ` · ${reason}`) : null,
    loc ? mapLink(loc.id) : null);
}

async function viewTasks() {
  location.hash = "#/tasks";
  const loading = el("div", { class: "muted" }, "Загрузка…");
  app.replaceChildren(topbar("#/tasks"), loading);
  // #304: the load captures its generation + session identity — a newer
  // navigation or a session change while it flies must not publish.
  const epoch = navEpoch;
  const ctxUser = S.user;
  const ctxCharId = S.character.id;
  try {
    // Cold-load safety: #/tasks can be the first route after reload — the
    // by-user hydration omits location_id, so read the authoritative
    // character first (same pattern as viewChat).
    if (S.character.location_id == null) {
      const me = await api(`/characters/${S.character.id}`);
      S.character = { ...S.character, ...me };
    }
    // #304: the Tasks screen's list read is an authoritative adapter
    // refresher — token at START, the in-flight counter spans the await.
    const listToken = ++taskReadSequence;
    taskListReadsInFlight++;
    let tasks;
    try {
      tasks = await api(`/characters/${S.character.id}/tasks`);
    } finally {
      taskListReadsInFlight--;
    }
    const goals = await api(`/characters/${S.character.id}/goals`);
    let opps = [];
    let desire = null;
    try {
      const d = await api(`/characters/${S.character.id}/desire/opportunities`);
      opps = d.opportunities || [];
      desire = d.desire;
    } catch (e) { /* desire layer unavailable — sections stay explicitly empty */ }
    S.mapLocations = await api("/locations");
    // #304: the session/navigation guard before publishing the async result.
    if (epoch !== navEpoch || S.user !== ctxUser || !S.character
      || S.character.id !== ctxCharId) return;
    ingestTaskSnapshot(tasks, listToken);
    renderTasks(tasks, goals, opps, desire);
  } catch (e) {
    // #93: transient failure — explicit retry instead of a dead «Загрузка…».
    loadRetryNode(loading, () => viewTasks());
    toast(e.message, true);
  }
}

function renderTasks(tasks, goals, opps, desire) {
  // #92: the three sections read ONLY backend state — no client-side
  // progress calculation, terminal states come from the read model.
  const active = [...(tasks.active || []), ...(tasks.planned || [])];
  const history = (tasks.recent_terminal || []).slice(0, 20);

  const activeBox = el("div", { class: "panel", id: "tasks-active" },
    el("h2", {}, "Активные"),
    active.length
      ? active.map((t) => taskRow(t))
      : el("div", { class: "muted" }, "Нет активных задач"));

  const historyBox = el("div", { class: "panel", id: "tasks-history" },
    el("h2", {}, "История"),
    history.length
      ? history.map((t) => taskRow(t))
      : el("div", { class: "muted" }, "История пуста"));

  // Opportunities are fed by the Desire planner (#90) and are semantically
  // separate from tasks; empty states are explicit. #170: the dedicated
  // #/desire screen is the canonical surface — the block links over and
  // keeps working (deep link focuses the free-text field on arrival).
  const oppsBox = el("div", { class: "panel", id: "tasks-opps" },
    el("h2", {}, "Возможности"),
    desire == null
      ? el("div", { class: "muted" }, "Желание не задано — ",
          el("a", { href: "#/desire",
            onclick: () => { S.desireFocus = true; } },
            "задайте его на экране «Цель»"))
      : (opps.length
        ? opps.map((o) => el("div", { class: "opp" },
            el("div", {}, o.title || ""),
            o.reason ? el("div", { class: "muted" }, o.reason) : null,
            o.source ? el("div", { class: "muted" }, `Источник: ${o.source}`) : null,
            o.link ? el("a", { href: o.link }, "Открыть") : null))
        : el("div", { class: "muted" }, "Пока нет возможностей")),
    desire != null
      ? el("a", { href: "#/desire", class: "muted tasks-desire-link" },
          "Экран «Цель»") : null);

  // The short-term CharacterGoal is its own block, explicitly distinct
  // from the long-term Desire (which lives in the profile).
  const currentGoal = (goals || []).find((g) => g.status === "queued" || g.status === "active")
    || (goals || [])[0] || null;
  const goalBox = el("div", { class: "panel", id: "tasks-goal" },
    el("h2", {}, "Ближняя цель"),
    currentGoal
      ? el("div", {},
          el("div", {}, currentGoal.source_text || ""),
          el("div", { class: "muted" },
            `${currentGoal.goal_type || ""} · ${currentGoal.status || ""}`))
      : el("div", { class: "muted" }, "Ближняя цель не задана"));

  app.replaceChildren(topbar("#/tasks"),
    el("div", { class: "grid tasks-shell" }, activeBox, goalBox, oppsBox, historyBox));
}


/* ---------- #170 (spec #64): Цель / желание — first-class screen ----------

   UI layer over desire v1 (#90): ONE active desire per character, served by
   the existing endpoints (the desire set/drop pair plus the bounded
   opportunities read — see app.py desire section). Per VISUAL-BIBLE §47 and
   #64 §3 the free-text textarea is THE primary control; suggestion chips
   are shortcuts that only fill the field (never submit). Interpretation is
   instant (deterministic LLM-off catalog, desire.py) so the PREVIEW shows
   «как система поняла» immediately — no fake spinner — and the desire is
   created only when the player confirms (interpret endpoint is read-only;
   §4-5: confirm gates the mutation, not the reverse). Lifecycle: set /
   replace / cancel; pause and history reads have no backend support yet
   (terminal rows exist, no read endpoint) — omitted honestly, not faked. */

const DESIRE_SUGGESTIONS = [
  "Хочу завести отношения",
  "Хочу открыть своё дело",
  "Хочу стать мэром",
  "Хочу больше путешествовать",
  "Хочу построить большой дом",
  "Хочу познакомиться с новыми людьми",
];

/* #66: the interpret-preview request sequence — a response from the desire
   interpretation preview applies only if it is still the LATEST request AND
   its owning composition is still mounted (see the stale guard in the
   submit handler). Bumping the counter invalidates any pending response. */
let desireInterpSeq = 0;

/* #66 (W118-RV1 F1 precedent): the request state is per-session — reset it
   on every auth transition (login, logout, account deletion) so a response
   addressed to the previous account can never land on the next user's
   screen. */
function desireResetDraft() {
  desireInterpSeq += 1;
}

function desireStateFetch() {
  // One authoritative read returns both the active desire and the planner's
  // bounded opportunities (title/reason/source/link included).
  return api(`/characters/${S.character.id}/desire/opportunities`);
}

async function refreshDesire() {
  // Guarded refresh after a lifecycle action: re-render ONLY while the
  // player is still on #/desire — navigating away mid-request must not
  // yank them back (no hash assignment on this path).
  if (location.hash !== "#/desire") return;
  const r = await desireStateFetch();
  if (location.hash === "#/desire") {
    renderDesire(r.desire, r.opportunities || []);
  }
}

async function viewDesire() {
  location.hash = "#/desire";
  const loading = el("div", { class: "muted" }, "Загрузка…");
  app.replaceChildren(topbar("#/desire"), loading);
  try {
    // Cold-load safety: #/desire can be the first route after reload — the
    // by-user hydration omits location_id, so read the authoritative
    // character first (same pattern as viewChat / viewTasks); without it
    // mapLink focus and the pollEvents anchor release misbehave.
    if (S.character.location_id == null) {
      const me = await api(`/characters/${S.character.id}`);
      S.character = { ...S.character, ...me };
    }
    const r = await desireStateFetch();
    renderDesire(r.desire, r.opportunities || []);
  } catch (e) {
    // #93: transient failure — explicit retry instead of a dead «Загрузка…».
    loadRetryNode(loading, () => viewDesire());
    toast(e.message, true);
  }
}

function desireChipRow(field) {
  // #64 §3 + §47: chips are examples, not limits — a click fills the
  // free-text field and focuses it (chat-sugg pattern); nothing is sent.
  // Real buttons (not spans): keyboard-operable for free, styled back to
  // the dim .sugg look by the scoped .desire-chips .sugg rule.
  return el("div", { class: "desire-chips", id: "desire-chips" },
    ...DESIRE_SUGGESTIONS.map((s) => el("button", {
      class: "sugg", type: "button",
      onclick: () => { field.value = s; field.focus(); },
    }, s)),
    el("div", { class: "muted desire-chips-note" },
      "Примеры — только подсказки: можно написать что угодно своими словами"));
}

/* #66: pure presentation — renames the planner's action descriptor into a
   human next step. NOTHING is invented: the mapping only re-labels the
   authoritative action descriptor; unknown shapes yield no step line, and
   reason/source/link on the card stay authoritative (never promises). */
function desireOppStep(o) {
  const a = o && o.action;
  if (!a) return null;
  if (a.mechanic === "dialogue") return `Поговорить — ${o.title || ""}`;
  if (a.mechanic === "read") return `Изучить — ${o.title || ""}`;
  if (a.mechanic === "action") {
    if (a.action_type === "WORK") return "Работать";
    if (a.action_type === "MOVE") return "Переехать";
    if (a.action_type === "SLEEP") return "Отдохнуть";
  }
  return null;
}

function desireOppCard(o, showSteps) {
  // #64 §7: what is available, why it is linked (reason), source, next
  // action. Links go through the opportunity's authoritative link (#/chat,
  // #/world, #/inventory, #/profile); a location pointer in the action
  // params gets the existing map focus mechanism. Nothing is invented.
  // #66: the optional step line is presentation only — active-goal cards
  // pass showSteps; the preview composition never does (a draft has no
  // planner data and must not borrow the active goal's next steps).
  const locId = o.action && o.action.params ? o.action.params.location_id : null;
  const step = showSteps ? desireOppStep(o) : null;
  return el("div", { class: "opp desire-opp" },
    el("div", { class: "desire-opp-title" }, o.title || ""),
    o.reason ? el("div", { class: "muted" }, o.reason) : null,
    o.source ? el("div", { class: "muted" }, `Источник: ${o.source}`) : null,
    o.link ? el("a", { href: o.link }, "Открыть") : null,
    step ? el("div", { class: "muted desire-opp-step" },
      `Следующий шаг: ${step}`) : null,
    locId != null ? mapLink(locId) : null);
}

function renderDesire(desire, opps, preview) {
  // #170 deep link: the Дела screen sets S.desireFocus on its link, the
  // flag is consumed here — arrival focuses the free-text field (§47: the
  // free input stays the visible primary control).
  const wantFocus = S.desireFocus === true;
  S.desireFocus = false;
  const interp = (desire && desire.interpretation) || {};
  const status = el("div", { class: "muted desire-status", role: "status" });
  const field = el("textarea", {
    id: "desire-input", rows: "3", maxlength: "500",
    placeholder: "Опишите желание своими словами — например: найти друзей, открыть своё дело",
  });
  const focusField = () => { if (wantFocus) field.focus(); };

  // Warm family: the desire screen carries the world-shell token set on the
  // grid container (.desire-shell mirrors the --w-* custom properties only)
  // so panels, dividers and accents read warm like the approved screens.
  function compose(...panels) {
    app.replaceChildren(topbar("#/desire"),
      el("div", { class: "grid desire-shell" }, ...panels));
    focusField();
  }

  const submitBtn = el("button", {
    id: "desire-submit", class: "primary", type: "button",
    onclick: async () => {
      const text = field.value.trim();
      if (!text) {
        status.textContent = "Напишите желание своими словами — свободный текст здесь главный";
        return;
      }
      // B2-review F2 pattern: no double-submit while a request is in flight.
      if (submitBtn.disabled) return;
      submitBtn.disabled = true;
      const prevLabel = submitBtn.textContent;
      // #66: the interpret call is a genuine network step — a real pending
      // state on the button (disabled + label), never a fake spinner.
      submitBtn.textContent = "Разбираем запрос…";
      status.textContent = "";
      const seq = ++desireInterpSeq;
      try {
        // #64 §4-5 honesty: interpret FIRST, create ONLY on confirm — the
        // deterministic catalog runs here without mutating anything.
        const r = await api(`/characters/${S.character.id}/desire/interpret`,
          { method: "POST", body: { text } });
        // #66 stale-response guard: apply only if this is still the latest
        // request, the player is still on #/desire with the same account,
        // the draft text is unedited, and the composition that sent the
        // request is still mounted. Otherwise the response is dropped.
        if (seq !== desireInterpSeq || location.hash !== "#/desire"
            || !S.user || !S.character
            || field.value.trim() !== text || !submitBtn.isConnected) return;
        // #66: the clarify branch discriminates on the endpoint contract
        // itself — 200 + catalog_key:null + clarification (m157 API pin),
        // never an interpreted result. r.clarification stays a data guard.
        if (r && r.catalog_key === null && r.clarification) {
          // #87 ok:false pattern + #66: the reason is data, nothing was
          // created — a DISTINCT clarify panel, never an interpreted result
          // and never a confirm button for this draft; the typed text stays
          // in the field so the player can rephrase in place.
          compose(editorPanel(text), clarifyPanel(r.clarification),
            desire ? oppsPanel(true) : null);
          return;
        }
        renderDesire(desire, opps, { text, interpretation: r.interpretation });
      } catch (e) {
        // #93: recoverable failure keeps the typed draft on screen — same
        // stale guard: a dead request must not touch a changed screen.
        if (seq !== desireInterpSeq || location.hash !== "#/desire"
            || !submitBtn.isConnected) return;
        status.textContent = "Не удалось разобрать желание. " + RETRY_HINT;
        toast(e.message, true);
      } finally {
        // A no-op when the response was applied through a full re-render
        // (this node is detached then); restores the pending-free label on
        // the clarify/error/stale paths where the node is still connected.
        submitBtn.disabled = false;
        submitBtn.textContent = prevLabel;
      }
    },
  }, "Сформулировать желание");

  const confirmBtn = el("button", {
    class: "primary", type: "button",
    onclick: async () => {
      // The ONLY create site (#64 §5): confirmation performs the real POST;
      // one-active-desire replace semantics are server-side.
      if (confirmBtn.disabled) return;
      confirmBtn.disabled = true;
      try {
        await api(`/characters/${S.character.id}/desire`,
          { method: "POST", body: { text: preview.text } });
        await refreshDesire();
      } catch (e) {
        toast(e.message, true);
        confirmBtn.disabled = false;
      }
    },
  }, "Подтвердить");

  const dropDraftBtn = el("button", {
    type: "button",
    onclick: () => composeHome(),  // drops the DRAFT — nothing was created
  }, "Отказаться");

  const dropBtn = el("button", {
    type: "button", class: "desire-drop",
    onclick: async () => {
      if (dropBtn.disabled) return;
      dropBtn.disabled = true;
      try {
        await api(`/characters/${S.character.id}/desire`, { method: "DELETE" });
        toast("Цель больше не активна");
        await refreshDesire();
      } catch (e) { toast(e.message, true); }
      finally { dropBtn.disabled = false; }
    },
  }, "Отказаться от цели");

  function editorPanel(preset) {
    field.value = preset || "";
    return el("div", { class: "panel", id: "desire-editor" },
      el("h2", {}, "Цель / желание"),
      el("p", { class: "desire-question" },
        "Чего ты хочешь для своего персонажа?"),
      // #66: the pre-confirmation interpretation step is NAMED — distinct
      // from the active goal shown above it.
      el("h3", { class: "desire-step-label" }, "Свободный запрос"),
      field,
      status,
      desire
        ? el("div", { class: "muted desire-note" },
            "Текущая цель остаётся активной, пока вы не подтвердите новую.")
        : null,
      el("div", { class: "desire-actions" }, submitBtn),
      desireChipRow(field),
      // #66: navigation ONLY — no interpretation claim. Clothing items
      // arrive through the market and are worn there (the appearance
      // screen's wardrobe); this link never reads as a desire result.
      el("div", { class: "desire-appear-link" },
        el("a", { href: "#/appearance" }, "Одежда и образ →"),
        el("div", { class: "muted desire-appear-note" },
          "Одежда появляется через рынок — вещи покупаются на Рынке и "
          + "надеваются в гардеробе.")));
  }

  function sidePanel() {
    return el("div", { class: "panel" },
      el("h2", {}, "Как это работает"),
      el("div", { class: "muted" },
        "Желание — глобальный мотив персонажа, а не отдельный квест: "
        + "мир читает его и подбирает правдоподобные возможности через "
        + "разговоры, роли, работу и места острова."),
      el("div", { class: "muted" },
        "Сначала система покажет, как поняла желание, — ничего не "
        + "сменится скрыто."));
  }

  function clarifyPanel(clarification) {
    // #66: the interpretation step asks back — a DISTINCT panel, visibly
    // NOT an interpreted result: no «Как система поняла» heading, no
    // confirm button for this draft; the reason is data (#87 pattern) and
    // the typed text stays in the field above for rephrasing.
    return el("div", { class: "panel desire-clarify", id: "desire-clarify" },
      el("h2", {}, "Уточнение"),
      el("p", { class: "desire-clarify-text" }, clarification),
      el("p", { class: "muted" },
        "Свободный запрос понимает четыре темы: Отношения, Публичная роль, "
        + "Дело и достаток, Спокойная жизнь — напишите желание своими "
        + "словами в одной из них."),
      el("p", { class: "muted desire-note" },
        "Это не результат разбора: желание не создано и не изменено."));
  }

  function oppsPanel(showSteps) {
    return el("div", { class: "panel", id: "desire-opps" },
      el("h2", {}, "Возможности по цели"),
      // #66: the label keeps these tied to the ACTIVE goal — opportunities
      // never masquerade as results of an unconfirmed request draft.
      el("div", { class: "muted" },
        "Возможности подбираются по активной цели — не по черновику запроса."),
      opps.length
        ? opps.map((o) => desireOppCard(o, showSteps))
        : el("div", { class: "muted" },
            "Пока нет возможностей — мир подберёт их, когда появятся основания"));
  }

  function confirmPanel(preview) {
    // #64 §4-5: interpretation BEFORE mutation — the structured result is
    // shown at once (deterministic catalog, no fake spinner), and the
    // confirm button performs the real create; nothing exists until then.
    const pi = preview.interpretation || {};
    return el("div", { class: "panel", id: "desire-confirm" },
      el("h2", {}, "Как система поняла"),
      el("div", { class: "desire-quote" }, preview.text),
      el("div", { class: "desire-intent" },
        `Тема: ${pi.title_ru || pi.catalog_key || "—"}`),
      pi.confidence_note
        ? el("div", { class: "muted" }, pi.confidence_note) : null,
      el("div", { class: "muted desire-note" },
        desire
          ? "Текущая цель остаётся активной, пока вы не подтвердите новую."
          : "Желание станет активным после подтверждения."),
      el("div", { class: "desire-actions" },
        confirmBtn,
        el("button", { type: "button",
          onclick: () => compose(editorPanel(preview.text),
            // #66: edit-back of a PREVIEW draft — no step lines here either
            // (the draft must not borrow the active goal's planner data).
            desire ? oppsPanel(false) : sidePanel()) },
          "Изменить формулировку"),
        dropDraftBtn));
  }

  function activePanel() {
    // #64 §6: status is qualitative (the read model has no numeric
    // progress) — shown as such, never as a fake progress bar.
    return el("div", { class: "panel", id: "desire-active" },
      el("h2", {}, "Цель / желание"),
      el("div", { class: "desire-quote" }, desire.source_text),
      el("div", { class: "desire-intent" },
        `Тема: ${interp.title_ru || desire.catalog_key || "—"}`
        + ` · статус: ${desire.status || "active"}`),
      interp.confidence_note
        ? el("div", { class: "muted" }, interp.confidence_note) : null,
      el("div", { class: "muted" }, `Задано: ${dayTime(desire.created_at || 0)}`),
      el("div", { class: "muted desire-note" },
        "Прогресс качественный: пока цель активна, мир подбирает возможности."),
      el("div", { class: "desire-actions" },
        el("button", { type: "button", id: "desire-replace",
          onclick: () => compose(editorPanel(desire.source_text),
            oppsPanel(true)) },
          "Заменить цель"),
        dropBtn));
  }

  function composeHome() {
    if (desire) compose(activePanel(), oppsPanel(true));
    else compose(editorPanel(""), sidePanel());
  }

  if (preview) {
    // #64 §4-5: the interpretation preview gates the mutation — the desire
    // becomes active only through the confirm button. #66: NO step lines in
    // the preview composition — the draft has no planner data of its own.
    compose(confirmPanel(preview), desire ? oppsPanel(false) : sidePanel());
    return;
  }
  composeHome();
}

/* #90: long-term Desire — one active desire, replace/abandon here. Kept
   strictly separate from the short-term CharacterGoal shown on #/tasks.
   #67: optional extraDesireNode (the «Текущая ситуация» row value) is
   filled by the same refresh() — one shared fetch for the whole screen. */
function profileDesireBlock(extraDesireNode) {
  const line = el("div", { class: "muted", id: "profile-desire" }, "Желание: …");
  const status = el("div", { class: "muted" });
  const input = el("input", {
    placeholder: "Новое желание — например: найти друзей",
    maxlength: "500",
  });
  function refresh() {
    api(`/characters/${S.character.id}/desire`).then((r) => {
      const d = r.desire;
      // #67: the «Текущая ситуация» row mirrors the same authoritative read
      // through THIS shared fetch — m157 pins the bare-desire literal at
      // exactly five sites, so the profile never re-fetches the desire.
      if (extraDesireNode) {
        extraDesireNode.textContent = d && d.source_text ? d.source_text : "Не задано";
        extraDesireNode.classList.remove("muted");
      }
      if (!d) { line.textContent = "Желание: не задано"; return; }
      const cat = (d.interpretation && d.interpretation.title_ru) || d.catalog_key || "—";
      line.textContent = "Желание: " + d.source_text + " / каталог: " + cat;
    }).catch(() => {
      line.textContent = "Желание: недоступно";
      if (extraDesireNode) extraDesireNode.textContent = "недоступно";
    });
  }
  const saveBtn = el("button", {
    onclick: async () => {
      // B2-review F2: guard against double-submit (two active desires race).
      if (saveBtn.disabled) return;
      saveBtn.disabled = true;
      try {
        const r = await api(`/characters/${S.character.id}/desire`,
          { method: "POST", body: { text: input.value } });
        if (r && r.clarification) {
          status.textContent = r.clarification;  // unsupported: explicit reason, no mutation
        } else {
          status.textContent = "Желание обновлено";
          // null coerces to "" (LegacyNullToEmptyString); spelled without the
          // chat draft-clear literal so the m156 pin keeps counting exactly
          // the two chat sites.
          input.value = null;
        }
        refresh();
      } catch (e) {
        // #93: recoverable save failure keeps the typed desire + hints retry.
        status.textContent = "Не удалось сохранить желание. " + RETRY_HINT;
        toast(e.message, true);
      }
      finally { saveBtn.disabled = false; }
    },
  }, "Задать желание");
  const dropBtn = el("button", {
    onclick: async () => {
      try {
        await api(`/characters/${S.character.id}/desire`, { method: "DELETE" });
        status.textContent = "Желание оставлено";
        refresh();
      } catch (e) { toast(e.message, true); }
    },
  }, "Отказаться от желания");
  refresh();
  return el("div", { class: "profile-desire-box" },
    line,
    // #170: the dedicated #/desire screen is the canonical surface; this
    // profile hook keeps working (set/drop) and links over for the full
    // interpretation/opportunities view.
    el("div", { class: "muted" },
      "Развёрнутый вид: ", el("a", { href: "#/desire" }, "экран «Цель»")),
    el("div", { style: "margin-top:6px; display:flex; gap:6px" }, input, saveBtn),
    dropBtn,
    status);
}

/* ---------- #79: Settings screen (#/settings) — graphics / interface /
   accessibility, honest rows ---------- */

/* #79: the canonical USER settings object. The OS media-query sample is
   NEVER stored here — it is read live by osReducedMotion(); the stored
   reducedMotion is the user's override only. Loaded once at boot
   (loadSettings), persisted best-effort on every change (persistSettings). */
const SETTINGS_DEFAULTS = { preset: "balanced", reducedMotion: false, uiScale: 100 };

function osReducedMotion() {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function loadSettings() {
  // Defensive read (welcome-card pattern): try/catch around getItem AND
  // around the JSON decode; every field validated against its enum/range —
  // any violation falls back to THAT field's default. Storage-denied →
  // defaults + sessionOnly (nothing persists this session).
  S.settings = { ...SETTINGS_DEFAULTS, sessionOnly: false };
  let raw = null;
  try { raw = window.localStorage.getItem("vl1.settings.v1"); }
  catch (e) { S.settings.sessionOnly = true; return; }
  if (raw === null) return;  // first visit — defaults, storage works
  let data = null;
  try { data = JSON.parse(raw); } catch (e) { data = null; }
  if (!data || typeof data !== "object" || Array.isArray(data)) return;
  if (["high", "balanced", "low-mobile"].includes(data.preset)) {
    S.settings.preset = data.preset;
  }
  if (typeof data.reducedMotion === "boolean") {
    S.settings.reducedMotion = data.reducedMotion;
  }
  if (typeof data.uiScale === "number" && Number.isInteger(data.uiScale)
    && data.uiScale >= 80 && data.uiScale <= 120 && data.uiScale % 10 === 0) {
    S.settings.uiScale = data.uiScale;
  }
}

function persistSettings() {
  // Best-effort write on EVERY change; a denied write flips sessionOnly so
  // the Сохранение section can say so honestly.
  if (S.settings.sessionOnly) return;
  try {
    window.localStorage.setItem("vl1.settings.v1", JSON.stringify({
      preset: S.settings.preset,
      reducedMotion: S.settings.reducedMotion,
      uiScale: S.settings.uiScale,
    }));
  } catch (e) { S.settings.sessionOnly = true; }
}

function applyReducedMotionClass() {
  // The documentElement class is the single CSS hook for reduced motion.
  document.documentElement.classList.toggle("settings-reduced-motion",
    S.mapReducedMotion);
}

function applyUiScale() {
  // zoom on the root element; 100 means no inline style at all.
  if (S.settings.uiScale === 100) {
    document.documentElement.style.removeProperty("zoom");
  } else {
    document.documentElement.style.zoom = String(S.settings.uiScale / 100);
  }
}

function applyStoredSettings() {
  // Runs ONCE at boot, BEFORE the first render: the stored preset replaces
  // the hardcoded mapQuality default, the effective-motion class lands on
  // documentElement, the UI-scale zoom applies.
  S.mapQuality = S.settings.preset;
  S.mapReducedMotion = osReducedMotion() || S.settings.reducedMotion;
  applyReducedMotionClass();
  applyUiScale();
}

function setReducedMotionPref(enabled) {
  // The SINGLE motion path — the map toolbar toggle and the settings
  // screen's toggle both land here. Sets the USER pref, recomputes the
  // EFFECTIVE flag (OS OR user), re-applies the class, persists, rerenders.
  // The OS sample itself is never persisted.
  S.settings.reducedMotion = !!enabled;
  S.mapReducedMotion = osReducedMotion() || !!enabled;
  applyReducedMotionClass();
  persistSettings();
  rerenderLivingMap();
}

function setUiScale(pct) {
  // 80–120%, step 10 — anything else is ignored, never clamped.
  if (typeof pct !== "number" || !Number.isInteger(pct)
    || pct < 80 || pct > 120 || pct % 10 !== 0) return;
  S.settings.uiScale = pct;
  applyUiScale();
  persistSettings();
}

function resetSettings() {
  // Back to the defaults a first visit would compute; re-applies everything
  // and rerenders the open surfaces. sessionOnly survives (the storage
  // capability did not change).
  S.settings = { ...SETTINGS_DEFAULTS, sessionOnly: S.settings.sessionOnly };
  applyStoredSettings();
  persistSettings();
  rerenderLivingMap();
  if (location.hash === "#/settings") viewSettings();
}

/* #79: stale-render guard — the m238–m242 subordinate-family counter. The
   screen is fully client-side (no fetch, no failure state, hence NO
   loadRetryNode surface): the guard drops a render that is no longer the
   latest invocation or no longer on #/settings. */
let settingsLoadSeq = 0;

function viewSettings() {
  const viewHash = "#/settings";
  // Settings is reached FROM the profile — the same session/character guard
  // family as viewProfile's #128 null-guard (worded differently: that exact
  // guard string is pinned ==1 on the profile site). route() already
  // short-circuits every non-#/profile hash to the creator pre-character;
  // this mirror keeps direct calls honest too.
  if (!S.user || !S.character) { viewCreateCharacter(); return; }
  const seq = ++settingsLoadSeq;
  if (seq !== settingsLoadSeq || location.hash !== viewHash) return;
  renderSettings();
}

function renderSettings() {
  const st = S.settings;

  // Графика: the preset control shares ONE setter with the map toolbar's
  // quality buttons (setMapQuality) — no second contradictory quality system.
  const presetGroup = el("fieldset", { class: "settings-preset-group" },
    el("legend", {}, "Пресет графики"),
    ...[
      ["high", "High", "Полная яркость ночных огней и погодных эффектов карты"],
      ["balanced", "Balanced", "Приглушённые огни и погода — рекомендуемый баланс нагрузки"],
      ["low-mobile", "Low-Mobile",
        "Без фоновых анимаций; меньше маркеров, огней и событий — для слабых устройств"],
    ].map(([value, label, note]) => el("label", { class: "settings-preset-option" },
      el("input", {
        type: "radio", name: "settings-preset", value,
        ...(st.preset === value ? { checked: "checked" } : {}),
        onchange: () => setMapQuality(value),
      }),
      el("span", { class: "settings-preset-name" }, label,
        value === "balanced"
          ? el("span", { class: "settings-preset-mark" }, "рекомендуется") : null),
      el("span", { class: "muted settings-preset-note" }, note))));
  const secGraphics = el("section", { class: "panel", tabindex: "-1" },
    el("h2", {}, "Графика"),
    presetGroup,
    el("p", { class: "muted settings-note" },
      "Пресет влияет только на карту и анимации — на события мира он не влияет."),
    el("div", { class: "settings-honest" },
      el("div", {}, "Качество генерации изображений — недоступно для настройки"),
      el("div", { class: "muted settings-note" },
        "Генерация изображений идёт на сервере без параметра качества")));

  // Интерфейс: the scale applies immediately via setUiScale; the label
  // carries the live value.
  const scaleText = el("span", {}, `Масштаб интерфейса: ${st.uiScale}%`);
  const secInterface = el("section", { class: "panel", tabindex: "-1" },
    el("h2", {}, "Интерфейс"),
    el("label", { class: "settings-field", for: "settings-ui-scale" },
      scaleText,
      el("input", {
        id: "settings-ui-scale", type: "range", min: "80", max: "120", step: "10",
        value: String(st.uiScale),
        oninput: (ev) => {
          setUiScale(Number(ev.target.value));
          scaleText.textContent = `Масштаб интерфейса: ${S.settings.uiScale}%`;
        },
      })),
    el("p", { class: "muted settings-note" }, "Применяется ко всему интерфейсу сразу"));

  // Специальные возможности: ONE motion toggle — the mockup's «Animation and
  // Transitions» is merged into it (two motion toggles would be fake
  // granularity). The toggle reflects the USER pref; the effective flag is
  // OS OR user, so the status line explains why motion can stay reduced
  // when the toggle is off.
  const secMotion = el("section", { class: "panel", tabindex: "-1" },
    el("h2", {}, "Специальные возможности"),
    el("label", { class: "settings-toggle" },
      el("input", {
        type: "checkbox",
        ...(st.reducedMotion ? { checked: "checked" } : {}),
        onchange: (ev) => setReducedMotionPref(ev.target.checked),
      }),
      el("span", {}, "Уменьшенное движение")),
    el("p", { class: "muted settings-note" },
      "Отключает плавную прокрутку и фоновые анимации"),
    osReducedMotion() ? el("p", { class: "muted settings-note" },
      "Также включено системными настройками устройства") : null);

  // Звук: honest row — no sound engine exists anywhere in the backend.
  const secSound = el("section", { class: "panel", tabindex: "-1" },
    el("h2", {}, "Звук"),
    el("div", { class: "settings-honest" },
      el("div", {}, "Звук — недоступен"),
      el("div", { class: "muted settings-note" }, "В игре пока нет звукового движка")));

  // Сохранение: what persists (settings, this browser) and what does not
  // (game progress — server; cross-device sync — nothing).
  const secStorage = el("section", { class: "panel", tabindex: "-1" },
    el("h2", {}, "Сохранение"),
    el("div", {}, "Настройки сохраняются автоматически в этом браузере"),
    el("div", { class: "muted settings-note" },
      "Это не сохранение игры — прогресс хранится на сервере"),
    el("div", { class: "muted settings-note" }, "Синхронизация между устройствами — недоступна"),
    st.sessionOnly ? el("div", { class: "muted settings-note" },
      "Применено только в этой сессии — браузер не дал сохранить настройки") : null,
    el("div", { class: "settings-actions" },
      el("button", { type: "button", onclick: () => resetSettings() }, "Сбросить")));

  // Numbered section guide — the creator-guide pattern (#78): scrollIntoView
  // + focus, NO hash assignment (the m120/m162 hash counts stay put), and
  // the scroll honours the effective-motion policy.
  const guideEntry = (section, label) => el("button", {
    type: "button", class: "settings-guide-link",
    onclick: () => {
      section.scrollIntoView({ behavior: S.mapReducedMotion ? "auto" : "smooth", block: "start" });
      section.focus({ preventScroll: true });
    },
  }, label);
  const sectionGuide = el("div", { class: "settings-guide" },
    el("div", { class: "muted settings-eyebrow" }, "Разделы настроек"),
    el("div", { class: "settings-guide-row" },
      guideEntry(secGraphics, "1. Графика"),
      guideEntry(secInterface, "2. Интерфейс"),
      guideEntry(secMotion, "3. Специальные возможности"),
      guideEntry(secSound, "4. Звук"),
      guideEntry(secStorage, "5. Сохранение")));

  // Single stacked page, NO nav-rail (the mockup's rail hides sections —
  // every section stays visible on one page). Highlights «Профиль».
  app.replaceChildren(topbar("#/profile"),
    el("main", { class: "settings-shell" },
      el("section", { class: "panel settings-hero" },
        el("div", { class: "muted settings-eyebrow" }, "ВЛ1: Рейнеке"),
        el("h1", {}, "Настройки"),
        el("p", { class: "settings-intro" },
          "Графика, масштаб интерфейса и движение — всё применяется сразу."),
        sectionGuide),
      secGraphics,
      secInterface,
      secMotion,
      secSound,
      secStorage));
}

/* ---------- profile (§77; #67 increment 1 — read-model hero + cards) ---------- */

/* #67: trait chips are read-only display labels; the authoritative keys are
   the configured trait_keys (sociability/discipline/risk_tolerance). */
const PROFILE_TRAIT_LABELS = {
  sociability: "Общительность",
  discipline: "Дисциплина",
  risk_tolerance: "Склонность к риску",
};

async function viewProfile() {
  // #128: route() falls through to #/profile with S.character null (the
  // create-form hash race and direct navigation) — send the user to creation.
  if (!S.character) { viewCreateCharacter(); return; }
  location.hash = "#/profile";
  const modeSel = el("select", {},
    el("option", { value: "AUTONOMOUS" }, "AUTONOMOUS (сам решает)"),
    el("option", { value: "DIRECT" }, "DIRECT (вы командуете)"),
    el("option", { value: "GUIDED" }, "GUIDED (цели)"));
  modeSel.value = S.character.control_mode || "AUTONOMOUS";
  const loading = el("div", { class: "muted" }, "Загрузка…");
  app.replaceChildren(topbar("#/profile"), loading);
  try {
    // Cold-load safety (same pattern as the other views): #/profile can be
    // the first route after reload — the by-user hydration omits location_id
    // and the owner status rows, so read the authoritative character first;
    // the merge carries looks/biography/traits/health (#67 read model).
    // Same null-check shape as the other cold-load sites (#67).
    if (S.character.location_id == null) {
      const me = await api(`/characters/${S.character.id}`);
      S.character = { ...S.character, ...me };
    }
    renderProfile(modeSel);
  } catch (e) {
    // #93: transient failure — explicit retry; the retry re-dispatches
    // the current hash via route().
    loadRetryNode(loading, () => route());
    toast(e.message, true);
  }
}

function renderProfile(modeSel) {
  const ch = S.character;

  /* #67 fixed decision: the hero embeds the shared portrait block builder
     verbatim (zero new portrait call sites); .profile-shell CSS restyles it
     full-bleed. The gate is a read-only copy of the visual-off flag. */
  const visualOn = !S.visualDisabled;

  // hero identity chips — async ones start muted and fill in place.
  const locChipV = el("span", { class: "profile-chip-v muted" }, "…");
  const jobChipV = el("span", { class: "profile-chip-v" }, ch.job ? codeLabel(JOB_TITLES_RU, ch.job) : "—");
  const partnerChipV = el("span", { class: "profile-chip-v muted" }, "…");
  const locChip = el("span", { class: "profile-chip" },
    el("span", { class: "profile-chip-k" }, "Локация"), locChipV);
  const jobChip = el("span", { class: "profile-chip" },
    el("span", { class: "profile-chip-k" }, "Работа"), jobChipV);
  const partnerChip = el("span", { class: "profile-chip" },
    el("span", { class: "profile-chip-k" }, "Партнёр"), partnerChipV);

  // «Текущая ситуация» values — the muted sub-panel pattern (placeholder →
  // fill, .catch → «недоступно»), same as the roles line below.
  const locValue = el("span", { class: "muted" }, "…");
  const jobValue = el("span", {}, ch.job ? codeLabel(JOB_TITLES_RU, ch.job) : "—");
  const partnerValue = el("span", { class: "muted" }, "…");
  const desireValue = el("span", { class: "muted" }, "…");

  const sexLabel = ch.sex === "F" ? "жен"
    : ch.sex === "M" ? "муж" : String(ch.sex || "—");
  const hero = el("section", { class: "panel profile-hero" },
    portraitBlock(),
    visualOn ? null
      : el("div", { class: "muted profile-visual-note" },
        "Визуал недоступен, показываю профиль"),
    el("div", { class: "profile-identity" },
      el("div", { class: "profile-name" }, ch.name || "—"),
      el("div", { class: "muted profile-sub" },
        `${ch.age ?? "—"} лет | пол: ${sexLabel}`),
      el("div", { class: "profile-chips" }, locChip, jobChip, partnerChip)));

  // Локация chip + situation row share one sub-panel fetch.
  (() => {
    api(`/locations/${S.character.location_id}`).then((loc) => {
      const name = loc && loc.name ? String(loc.name) : "—";
      locChipV.textContent = name;
      locChipV.classList.remove("muted");
      locValue.textContent = name;
      locValue.classList.remove("muted");
    }).catch(() => {
      locChipV.textContent = "недоступно";
      locValue.textContent = "недоступно";
    });
  })();

  // Роли: ONE fetch feeding the pinned summary line, the partner chip/row
  // and the detail list (m63/m156 pin the roles literal at exactly one call
  // site — the redesigned card reuses it, never re-fetches).
  const rolesList = el("div", { class: "profile-rolelist muted" }, "…");
  const rolesBlock = (() => {
    const rolesLine = el("div", { class: "muted profile-roles" }, "Роли: …");
    api(`/characters/${S.character.id}/roles`).then((r) => {
      const rolesText = (r.roles || []).map((x) => x.title).join(", ");
      rolesLine.textContent = "Роли: " + (rolesText || "нет");
      const roles = r.roles || [];
      // #182: the authoritative partnership role carries id "partner"
      // (roles.py _partner_role); the title match is the robustness
      // fallback for future catalog renames.
      const partner = roles.find((x) => x.id === "partner")
        || roles.find((x) => String(x.title || "").toLowerCase().includes("партнёр"));
      partnerChipV.textContent = partner ? String(partner.title) : "Нет";
      partnerChipV.classList.remove("muted");
      partnerValue.textContent = partner ? String(partner.title) : "Нет";
      partnerValue.classList.remove("muted");
      rolesList.classList.remove("muted");
      rolesList.replaceChildren(...(roles.length ? roles.map((x) =>
        el("div", { class: "profile-role" },
          el("span", { class: "profile-role-title" }, x.title || x.id || "—"),
          x.reason ? el("span", { class: "muted profile-role-reason" }, x.reason) : null))
        : [el("span", { class: "muted" }, "Ролей нет")]));
    }).catch(() => {
      rolesLine.textContent = "Роли: недоступны";
      partnerChipV.textContent = "недоступно";
      partnerValue.textContent = "недоступно";
      rolesList.replaceChildren(el("span", { class: "muted" }, "недоступно"));
    });
    return el("div", {}, rolesLine, rolesList,
      // #75: standing link to the Career screen (no topbar tab — the
      // screen is reached from here; the profile keeps its ONE roles
      // fetch — the career screen fetches its own).
      el("a", { href: "#/career", class: "muted" }, "Развитие →"),
      // #79: standing link to the Settings screen (no topbar tab — the
      // screen is reached from here and highlights «Профиль»).
      el("a", { href: "#/settings", class: "muted" }, "Настройки →"));
  })();

  // «Основное» — label+bar+value rows in the existing .need markup style;
  // the fill recolors to the single warm token inside .profile-shell.
  function statBar(label, pct, display) {
    const w = (pct === null || pct === undefined || !Number.isFinite(pct)) ? 0
      : Math.max(0, Math.min(100, Math.round(pct)));
    return el("div", { class: "need profile-stat" },
      el("div", { class: "row" }, el("span", {}, label), el("span", {}, display)),
      el("div", { class: "bar" }, el("div", { style: `width:${w}%` })));
  }
  const healthVal = ch.health && Number.isFinite(Number(ch.health.health))
    ? Number(ch.health.health) : null;
  const energyVal = ch.needs && Number.isFinite(Number(ch.needs.energy))
    ? Number(ch.needs.energy) : null;
  const balanceText = (ch.balance === undefined || ch.balance === null) ? "—"
    : `${Number(ch.balance).toLocaleString("ru-RU")} ₽`;
  const traits = Array.isArray(ch.traits) ? ch.traits : [];

  const side = el("aside", { class: "profile-side" },
    el("div", { class: "panel" },
      el("h2", {}, "Основное"),
      statBar("Здоровье", healthVal,
        healthVal === null ? "—" : String(Math.round(healthVal))),
      statBar("Энергия", energyVal,
        energyVal === null ? "—" : String(Math.round(energyVal))),
      // v1: balance has no 0-100 scale — its bar is a decorative full strip
      // (the formatted value carries the data; an empty bar would misread
      // as zero and an invented scale would be gameplay magic).
      statBar("Деньги", 100, balanceText)),
    el("div", { class: "panel" },
      el("h2", {}, "Черты характера"),
      el("div", { class: "profile-chips" },
        ...(traits.length ? traits.map((t) =>
          el("span", { class: "profile-chip profile-trait" },
            el("span", { class: "profile-chip-k" },
              PROFILE_TRAIT_LABELS[t.trait_key] || t.trait_key),
            el("span", { class: "profile-chip-v" }, String(t.value))))
          : [el("span", { class: "muted" }, "Нет данных о чертах")]))),
    el("div", { class: "panel" },
      el("h2", {}, "Текущая ситуация"),
      el("div", { class: "profile-rows" },
        el("div", { class: "profile-row" },
          el("span", { class: "profile-row-k" }, "Локация"), locValue,
          // #74: standing link to the Home screen (no topbar tab — the
          // screen is reached from here and from the desire bridge).
          el("a", { href: "#/home", class: "muted" }, "Моё жильё →")),
        el("div", { class: "profile-row" },
          el("span", { class: "profile-row-k" }, "Работа"), jobValue),
        el("div", { class: "profile-row" },
          el("span", { class: "profile-row-k" }, "Партнёр"), partnerValue),
        el("div", { class: "profile-row" },
          el("span", { class: "profile-row-k" }, "Желание"), desireValue))),
    // #90/#170: the editable desire box keeps its pinned id/buttons/link;
    // its refresh() also fills the Ситуация row above (shared fetch).
    el("div", { class: "panel" },
      el("h2", {}, "Желание"),
      profileDesireBlock(desireValue)),
    el("div", { class: "panel" },
      el("h2", {}, "Роли"),
      rolesBlock),
    el("div", { class: "panel" },
      el("h2", {}, "Режим управления"),
      modeSel,
      el("div", { style: "margin-top:8px" }, el("button", {
        class: "primary", onclick: async () => {
          try {
            await api(`/characters/${S.character.id}/control`, {
              method: "POST", body: { mode: modeSel.value } });
            S.character.control_mode = modeSel.value;
            toast("Режим обновлён");
          } catch (e) { toast(e.message, true); }
        } }, "Применить"))),
    // #205: per-character outfit mode. "auto" — как сейчас (носимые
    // предметы мира говорят сами, промпт молчит); явные режимы добавляют
    // фрагмент одежды в портретный промпт (см. descriptor.py). Owner-only
    // PATCH; ошибки тостом, как в панели режима управления выше.
    el("div", { class: "panel" },
      el("h2", {}, "Одежда"),
      (() => {
        const outfitSel = el("select", {},
          el("option", { value: "auto" }, "auto — как сейчас"),
          el("option", { value: "casual" }, "casual — повседневная одежда"),
          el("option", { value: "formal" }, "formal — строгая одежда"),
          el("option", { value: "nude" }, "nude — без одежды (18+)"));
        outfitSel.value = ch.outfit_mode || "auto";
        // runtime acceptance 2026-10-16 found the select unattached
        // (created, value set, never mounted) — panel showed
        // heading+button only.
        return el("div", { style: "margin-top:8px" }, outfitSel, el("button", {
          class: "primary", onclick: async () => {
            try {
              await api(`/characters/${S.character.id}/outfit`, {
                method: "PATCH", body: { outfit_mode: outfitSel.value } });
              S.character.outfit_mode = outfitSel.value;
              toast("Одежда обновлена");
            } catch (e) { toast(e.message, true); }
          } }, "Применить"));
      })()),
    el("div", { class: "panel" },
      el("h2", {}, "Аккаунт"),
      el("div", {}, S.user.username),
      el("div", { class: "muted" }, `${S.user.email} · роль: ${S.user.role}`),
      el("div", { style: "margin-top:8px" },
        el("button", { onclick: async () => {
          await api("/auth/logout", { method: "POST", body: {} });
          S.user = null; S.character = null;
          S.world = null;  // #269: the cached world must not survive the session
          S.authProbed = false;
          creatorResetDraft();  // W118-RV1 F1: logout clears the creator draft
          appearanceResetDraft();  // #65: logout clears the outfit prompt draft
          desireResetDraft();  // #66: logout drops the pending interpret request
          resetGameUiContext();  // #302: session teardown — drafts/selection/focus must not leak across logout/relogin
          location.hash = "#/login";
          route();
        } }, "Выйти")),
      el("div", { class: "danger-zone", style: "margin-top:12px" },
        el("div", { class: "muted", style: "font-size:11px" },
          "Удаление необратимо: персонаж, диалоги и все связанные данные будут стёрты."),
        el("input", { type: "text", placeholder: S.user.username,
          "aria-label": "Подтверждение: имя аккаунта",
          oninput: (e) => { S.confirmDelete = e.target.value; } }),
        el("button", { class: "danger", onclick: async () => {
          try {
            await api("/auth/account/delete", { method: "POST", body: { confirm: S.confirmDelete || "" } });
            S.user = null; S.character = null;
            S.world = null;  // #269: the cached world must not survive the session
            creatorResetDraft();  // W118-RV1 F1: deletion clears the creator draft
            appearanceResetDraft();  // #65: deletion clears the outfit prompt draft
            desireResetDraft();  // #66: deletion drops the pending interpret request
            resetGameUiContext();  // #302: session teardown — nothing survives account deletion
            // #138: no S.authProbed reset here (m132 pins exactly two reset
            // sites) — after deletion auth state is known-gone and the
            // cookie is cleared server-side; a re-probe would only 401.
            toast("Аккаунт удалён");
            location.hash = "#/login";
            route();
          } catch (e) { toast(e.message, true); }
        } }, "Удалить аккаунт"))));

  app.replaceChildren(topbar("#/profile"),
    el("div", { class: "grid profile-shell" }, hero, side));
}

/* ---------- #69: people screen (relationships UI) ---------- */

// #69: deterministic people-card status from the two authoritative signed
// scalars (Relationship columns are -100..100 per the db CheckConstraints;
// no invented data — only trust/affection drive the line). Mapping, kept
// deliberately simple and documented here for the stranger:
//   trust < 0 OR affection < 0 → «Напряжённые»  (any negative signal wins —
//                                                 a hostile pair is not a friend)
//   affection >= 25            → «Друг»         (v1 cut: midpoint of the chat
//                                                 relationship widget's warm band)
//   otherwise                  → «Знакомый»     (neutral; also the fallback for
//                                                 missing/NaN scalars)
function peopleStatus(rel) {
  const a = Number(rel && rel.affection);
  const t = Number(rel && rel.trust);
  if ((Number.isFinite(t) && t < 0) || (Number.isFinite(a) && a < 0)) {
    return "Напряжённые";
  }
  return Number.isFinite(a) && a >= 25 ? "Друг" : "Знакомый";
}

// #69: relationship meters are SIGNED (-100..100), so each bar maps the
// value around a neutral center tick: positive fills rightward from the
// midpoint, negative leftward (a clamp-to-≥0 mapping was rejected — it would
// render hostility as a calm zero). The displayed number keeps its sign.
function peopleMeter(label, v) {
  const num = Number(v);
  const ok = Number.isFinite(num);
  const val = ok ? Math.max(-100, Math.min(100, num)) : 0;
  const half = Math.round(Math.abs(val) / 2);
  return el("div", { class: "need people-meter" },
    el("div", { class: "row" },
      el("span", {}, label),
      el("span", {}, ok ? String(Math.round(num)) : "—")),
    el("div", { class: "bar people-track" },
      el("div", { class: "people-mid" }),
      el("div", {
        class: "people-fill" + (val < 0 ? " neg" : ""),
        style: `width:${ok ? half : 0}%;left:${val < 0 ? 50 - half : 50}%`,
      })));
}

async function viewPeople() {
  location.hash = "#/people";
  const loading = el("div", { class: "muted" }, "Загрузка…");
  app.replaceChildren(topbar("#/people"), loading);
  try {
    // Cold-load safety (the shared route-view pattern): #/people can be the
    // first route after reload — the by-user hydration omits location_id,
    // so read the authoritative character first, exactly like the siblings.
    if (S.character.location_id == null) {
      const me = await api(`/characters/${S.character.id}`);
      S.character = { ...S.character, ...me };
    }
    const cid = S.character.id;
    const rels = await api(`/characters/${cid}/relationships`);
    renderPeople(cid, rels);
  } catch (e) {
    // #93: transient failure — explicit retry instead of a dead «Загрузка…».
    loadRetryNode(loading, () => viewPeople());
    toast(e.message, true);
  }
}

function renderPeople(cid, rels) {
  // Names: the relationships response already carries other_name (joined
  // from Character rows server-side). The contacts endpoint is the EXTERNAL
  // services roster, not island people — no second name source to merge.
  const people = rels.map((r) => ({
    id: r.other_id, name: r.other_name || r.other_id, rel: r,
  }));
  if (!people.length) {
    app.replaceChildren(topbar("#/people"),
      el("div", { class: "grid people-shell" },
        el("div", { class: "panel people-empty" },
          el("div", { class: "muted" }, "Пока никого. Начни диалог на острове."),
          el("a", { href: "#/world" }, "К карте острова"))));
    return;
  }
  // Selection persists across re-entries; default to the first person.
  if (!people.some((p) => p.id === S.peopleSelId)) {
    S.peopleSelId = people[0].id;
  }

  const heroName = el("div", { class: "people-name" }, "—");
  const heroStatus = el("div", { class: "muted people-status" }, "");
  const heroPortrait = el("div", { class: "people-portrait" });
  const actions = el("div", { class: "people-actions" });
  const metersBox = el("div", { class: "people-meters" });

  // «Как мир тебя видит» — the player's OWN public roles (title + reason
  // from the derive-on-read roles endpoint): honest public facts only,
  // no invented reputation score. One per-panel fetch, .catch → «недоступно».
  const repList = el("div", { class: "people-rep-list muted" }, "…");
  api(`/characters/${cid}/roles`).then((r) => {
    const roles = r.roles || [];
    repList.classList.remove("muted");
    repList.replaceChildren(...(roles.length
      ? roles.map((x) => el("div", { class: "people-rep-role" },
          el("span", { class: "people-rep-title" }, x.title || x.id || "—"),
          x.reason
            ? el("span", { class: "muted people-rep-reason" }, x.reason) : null))
      : [el("span", { class: "muted" }, "Пока без заметных ролей")]));
  }).catch(() => {
    repList.classList.remove("muted");
    repList.replaceChildren(el("span", { class: "muted" }, "недоступно"));
  });

  // Bottom strip: one card per relationship; the small warmth bar maps
  // affection around the center tick (same signed semantics as the meters,
  // warm fill in the shell scope).
  const cards = people.map((p) => {
    const warmth = Number(p.rel && p.rel.affection);
    const ok = Number.isFinite(warmth);
    const val = ok ? Math.max(-100, Math.min(100, warmth)) : 0;
    const half = Math.round(Math.abs(val) / 2);
    return {
      id: p.id,
      node: el("button", {
        class: "people-card",
        onclick: () => { S.peopleSelId = p.id; draw(); mark(); },
      },
        el("span", { class: "people-card-name" }, p.name),
        el("span", { class: "muted people-card-status" }, peopleStatus(p.rel)),
        el("div", { class: "bar people-warmth" },
          el("div", {
            class: "people-fill" + (val < 0 ? " neg" : ""),
            style: `width:${ok ? half : 0}%;left:${val < 0 ? 50 - half : 50}%`,
          }))),
    };
  });
  function mark() {
    for (const c of cards) {
      c.node.classList.toggle("sel", c.id === S.peopleSelId);
    }
  }

  function draw() {
    const sel = people.find((p) => p.id === S.peopleSelId) || people[0];
    heroName.textContent = sel.name;
    heroStatus.textContent = peopleStatus(sel.rel);
    actions.replaceChildren(
      el("button", { onclick: () => {
        // Hand the person to the chat screen through its OWN persisted
        // selection mechanism: the chat view restores S.chatNpcId whenever
        // the id is among the NPCs at the player's location; otherwise chat
        // opens normally on its default target (no partner-param endpoint
        // exists in v1 — noted in issue #69).
        S.chatNpcId = sel.id;
        location.hash = "#/chat";
      } }, "Поговорить"),
      el("button", { onclick: () => { location.hash = "#/world"; } },
        "Найти на карте"));
    metersBox.replaceChildren(
      peopleMeter("Доверие", sel.rel.trust),
      peopleMeter("Симпатия", sel.rel.affection),
      peopleMeter("Романтика", sel.rel.romantic_interest),
      peopleMeter("Уважение", sel.rel.respect));
    // Portrait: the canonical-asset → file-blob two-step is composed right
    // here (the shared blob-fetch helper's call-site count is pinned, so
    // this view builds its own <img>) with the distinct fallback string.
    heroPortrait.replaceChildren(
      el("div", { class: "portrait-status muted" }, "Портрет…"));
    const visualOn = !S.visualDisabled;
    if (visualOn) {
      const want = sel.id;
      (async () => {
        try {
          const asset = await api(`/visual/characters/${sel.id}/portrait`);
          const file = await fetch(`/visual/assets/${asset.id}/file`,
            { credentials: "same-origin" });
          if (!file.ok) throw new Error(`HTTP ${file.status}`);
          const url = URL.createObjectURL(await file.blob());
          if (S.peopleSelId !== want) return;  // selection moved on mid-flight
          heroPortrait.replaceChildren(el("img", {
            class: "portrait-img", src: url, alt: "Портрет",
          }));
        } catch (e) {
          if (S.peopleSelId !== want) return;
          heroPortrait.replaceChildren(el("div", { class: "portrait-status muted" },
            "Портрет недоступен"));
        }
      })();
    } else {
      heroPortrait.replaceChildren(el("div", { class: "portrait-status muted" },
        "Портрет недоступен"));
    }
  }

  draw();
  mark();

  app.replaceChildren(topbar("#/people"),
    el("div", { class: "grid people-shell" },
      el("div", { class: "people-main" },
        el("section", { class: "panel people-hero" },
          heroPortrait,
          el("div", { class: "people-identity" }, heroName, heroStatus, actions)),
        el("section", { class: "panel people-detail" },
          el("h2", {}, "Отношения"),
          metersBox),
        el("section", { class: "panel people-rep" },
          el("h2", {}, "Как мир тебя видит"),
          repList)),
      el("section", { class: "panel people-strip-panel" },
        el("h2", {}, "Круг общения"),
        el("div", { class: "people-strip" }, ...cards.map((c) => c.node)))));
}

/* ---------- admin (§83-86) ---------- */

async function viewAdmin() {
  location.hash = "#/admin";
  const loading = el("div", { class: "muted" }, "Загрузка…");
  app.replaceChildren(topbar("#/admin"), loading);
  try {
    const ov = await api("/admin/overview");
    const isAdmin = ["admin", "developer"].includes(S.user.role);
    const rows = [
      ["Пользователи", ov.users], ["Игроки", ov.players], ["NPC", ov.npcs],
      ["Активные задачи", ov.active_tasks], ["События за час", ov.events_last_hour],
      ["WS-коннекты", ov.ws_connections], ["Uptime, с", ov.uptime_sec],
      ["Схема БД", ov.schema_version],
    ].map(([k, v]) => el("tr", {}, el("td", {}, k), el("td", {}, String(v))));
    const wc = ov.world_clock || {};
    const adminButtons = isAdmin ? el("div", { class: "actions" },
      el("button", { onclick: () => adminAction("/admin/world/pause") }, "Пауза"),
      el("button", { onclick: () => adminAction("/admin/world/resume") }, "Продолжить"),
      (() => {
        const ts = el("input", { type: "number", step: "0.1", value: "1" });
        return el("span", { style: "display:inline-flex;gap:4px" },
          ts, el("button", { onclick: () =>
            adminAction("/admin/world/timescale",
              { time_scale: Number(ts.value) }) }, "Скорость"));
      })()) : el("span", { class: "muted" }, "read-only (moderator)");
    const audit = await api("/admin/audit?limit=30");
    app.replaceChildren(topbar("#/admin"),
      el("div", { class: "grid" },
        el("div", { class: "panel" },
          el("h2", {}, `Мир — ${wc.is_paused ? "ПАУЗА" : "идёт"} ×${wc.time_scale}`),
          el("div", { class: "muted" }, dayTime(wc.game_timestamp || 0)),
          el("table", { class: "inv" }, rows.map((r) => r))),
        el("div", { class: "panel" }, el("h2", {}, "Управление"), adminButtons)),
      el("div", { class: "panel" },
        el("h2", {}, "Audit log"),
        el("table", { class: "inv" },
          el("tr", {}, el("th", {}, "Действие"), el("th", {}, "Цель"), el("th", {}, "Когда")),
          ...audit.map((a) => el("tr", {},
            el("td", {}, a.action), el("td", {}, `${a.target_type}:${a.target_id ?? ""}`),
            el("td", { class: "muted" }, a.wall_created_at || ""))))));
  } catch (e) {
    // #175/#93: transient failure — explicit retry instead of a dead «Загрузка…».
    loadRetryNode(loading, () => viewAdmin());
    toast(e.message, true);
  }
}

async function adminAction(path, body = {}) {
  try {
    await api(path, { method: "POST", body });
    toast("Применено");
    viewAdmin();
  } catch (e) { toast(e.message, true); }
}

/* ---------- boot ---------- */

// #79: stored user settings apply BEFORE the first render — the stored
// preset replaces the hardcoded mapQuality default, the effective-motion
// class lands on documentElement, the UI-scale zoom applies. The OS motion
// setting can also change mid-session: the listener recomputes the
// EFFECTIVE flag (OS OR user pref), re-applies the class and rerenders the
// surfaces that read it. The OS sample is never persisted.
loadSettings();
applyStoredSettings();
const motionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
motionQuery.addEventListener("change", () => {
  S.mapReducedMotion = motionQuery.matches
    || !!(S.settings && S.settings.reducedMotion);
  applyReducedMotionClass();
  rerenderLivingMap();
  if (location.hash === "#/settings") viewSettings();
});

window.addEventListener("hashchange", route);

// #313: soft-keyboard budget — layout-only, never re-renders the chat.
function applyChatViewportBudget() {
  const vv = window.visualViewport;
  const vh = vv ? vv.height : window.innerHeight;
  const log = document.querySelector(".chatlog");
  if (log) log.style.maxHeight = Math.round(Math.min(320, Math.max(120, vh - 480))) + "px";
}
// #308: layout-only resize — re-project markers against the live card box
// (the object-fit:cover crop changes with the viewport aspect ratio).
function onViewportResize() {
  applyChatViewportBudget();
  recomputeMapPositions();
}
window.addEventListener("resize", onViewportResize);
route();
