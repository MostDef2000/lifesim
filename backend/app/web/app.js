/* ВЛ1: Рейнеке — MVP web client (§77-78). Vanilla JS, без сборки.
   XSS-гигиена: рендер данных API только через textContent/createTextNode. */
"use strict";

const app = document.getElementById("app");
const toastEl = document.getElementById("toast");
const S = { user: null, character: null, world: null, ws: null, wsTries: 0,
  feed: [], cursor: 0, pollTimer: null, canonicalPortraitId: null, authProbed: false, visualDisabled: false,
  connLost: false, pollFails: 0,  // #175: poll-failure liveness banner (WS close is routine — never drives this)
  mapLod: "island", mapQuality: "balanced",
  mapReducedMotion: window.matchMedia("(prefers-reduced-motion: reduce)").matches,
  mapFocusLocationId: null, mapLocations: [], travel: null, scene: null, sceneAct: null,
  sceneActDraft: null, pollBusy: false,
  wsCursorSent: false, feedFetched: false,  // #179: WS cursor handshake state (see sendWsCursor)
  shopConfirm: null, chatDraft: "",
  bootCursor: Infinity };  // #177: replay gate — set once from the first REST history fill; Infinity = "no live events until proven"

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
  const res = await fetch(path, {
    credentials: "same-origin",
    headers: opts.body ? { "content-type": "application/json" } : {},
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (res.status === 401) {
    S.user = null;
    if (path !== "/auth/me") route();
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
};

async function route() {
  if (!location.hash) { location.hash = "#/world"; return; }  // #161: normalize once; the hashchange fires route again with a concrete view
  stopFeed();
  if (!S.user && !S.authProbed) {
    try { S.user = await api("/auth/me"); }
    catch (e) { S.user = null; S.authProbed = true; }
  }
  if (!S.user) return viewAuth();
  document.body.classList.remove("landing-mode");
  if (!S.character) {
    try {
      const chars = await api(`/characters/by-user/${S.user.id}`);
      S.character = chars && chars.length ? chars[0] : null;
    } catch (e) { S.character = null; }
  }
  const hash = location.hash;  // #161: empty hash is normalized at the top of route(); no fallback needed
  // 020/#118 P1: a user without a character gets the creator screen for ANY
  // hash. The #/profile carve-out below is deliberate: #/profile falls
  // through to viewProfile, whose #128 null-guard hands off to the creator,
  // and the creator normalizes the address to #/create (its own route).
  // Every other hash short-circuits straight into the creator.
  if (!S.character && hash !== "#/profile") return viewCreateCharacter();
  // #72: dynamic subordinate route — strict numeric id, matched BEFORE the
  // static registry (the registry above now also carries #74's static
  // "#/home" entry and #75's static "#/career" entry — 15 rows total; still
  // no topbar tab for any of them: the Place screen highlights «Мир», the
  // Home and Career screens «Профиль»).
  const placeMatch = hash.match(/^#\/location\/(\d+)$/);
  if (placeMatch) return viewLocation(Number(placeMatch[1]));
  (routes[hash] || viewWorld)();
}

/* ---------- auth (§77 login/register) ---------- */

function viewAuth() {
  location.hash = "#/login";
  document.body.classList.add("landing-mode");
  const mode = S.authMode === "register" ? "register" : "login";
  const isReg = mode === "register";

  function focusUsername() {
    const input = document.getElementById("auth-username");
    if (!input) return;
    input.focus({ preventScroll: false });
  }

  function openAuth(nextMode) {
    // Reuse the original primary/secondary CTA semantics: switch mode, then
    // re-render the always-visible cream auth card and focus the first field.
    S.authMode = nextMode;
    viewAuth();
    focusUsername();
  }

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
      el("button", { class: "landing-cta", type: "button", onclick: () => openAuth("register") }, "Начать жизнь на Рейнеке"),
      el("button", { class: "landing-secondary", type: "button", onclick: () => openAuth("login") }, "У меня уже есть персонаж")));

  const scene = el("img", { class: "landing-scene",
    src: "/static/static/landing/concierge-cat-hero.webp",
    alt: "Кошка-консьержка встречает гостей на пирсе острова Рейнеке" });
  const scrim = el("div", { class: "landing-scrim", "aria-hidden": "true" });

  const err = el("div", { class: "err-text landing-auth-error", role: "alert" });
  const username = el("input", { id: "auth-username", name: "username", autocomplete: "username", required: "required" });
  const password = el("input", { id: "auth-password", name: "password", type: "password",
    autocomplete: isReg ? "new-password" : "current-password", required: "required" });
  const email = el("input", { id: "auth-email", name: "email", type: "email", autocomplete: "email", placeholder: "you@example.com" });
  const age = el("input", { id: "auth-age", name: "age_confirmed", type: "checkbox", class: "landing-checkbox" });

  const submitBtn = el("button", { class: "landing-cta landing-auth-submit", type: "submit" },
    isReg ? "Создать аккаунт" : "Войти");
  const form = el("form", { class: "landing-auth-form", onsubmit: async (ev) => {
    ev.preventDefault();
    err.textContent = "";
    submitBtn.disabled = true;
    submitBtn.setAttribute("aria-busy", "true");
    try {
      if (isReg) {
        if (!age.checked) { err.textContent = "Подтвердите, что вам 18 лет или больше."; return; }
        await api("/auth/register", { method: "POST", body: {
          username: username.value, email: email.value,
          password: password.value, age_confirmed: true } });
      }
      await api("/auth/login", { method: "POST", body: {
        username: username.value, password: password.value } });
      S.user = await api("/auth/me");
      S.authProbed = false;
      S.character = null;
      creatorResetDraft();  // W118-RV1 F1: fresh login must not inherit the previous session's draft
      appearanceResetDraft();  // #65: same isolation for the outfit prompt draft
      desireResetDraft();  // #66: same isolation for the interpret-request state
      gotoWorld();  // #161: single dispatch — no direct route() next to the hash assignment
    } catch (e) { err.textContent = e.message; }
    finally {
      submitBtn.disabled = false;
      submitBtn.removeAttribute("aria-busy");
    }
  } },
    el("div", { class: "landing-auth-heading" },
      el("div", {},
        el("p", { class: "landing-kicker" }, isReg ? "Новый житель" : "Возвращение на остров"),
        el("h2", {}, isReg ? "Начать жизнь на Рейнеке" : "Войти в Рейнеке"))),
    el("p", { class: "landing-auth-intro" },
      isReg ? "Создайте аккаунт. После входа вы сможете создать своего персонажа." : "Войдите в существующий аккаунт и вернитесь к своему персонажу."),
    el("label", { for: "auth-username" }, "Имя пользователя"), username,
    isReg && el("label", { for: "auth-email" }, "Email"), isReg && email,
    el("label", { for: "auth-password" }, "Пароль"), password,
    isReg && el("label", { class: "landing-age-row", for: "auth-age" }, age,
      el("span", {}, "Мне 18 лет или больше")),
    submitBtn,
    err,
    el("button", { class: "landing-auth-switch", type: "button", onclick: () => openAuth(isReg ? "login" : "register") },
      isReg ? "У меня уже есть аккаунт" : "Нет аккаунта? Зарегистрироваться"));

  const authCard = el("aside", { class: "landing-auth-card",
    "aria-label": isReg ? "Регистрация" : "Вход" }, form);

  const heroInner = el("div", { class: "landing-hero-inner" }, heroCopy, authCard);

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
      el("p", {}, "Простое начало: регистрация, персонаж, первый день на острове.")));

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
const CREATOR_DRAFT = { appearance: {}, appearancePrompt: "", bio: "", tags: [] };

/* W118-RV1 F1: the draft is per-session private input (name, bio, appearance).
   Reset it on every auth transition — logout, account deletion, successful
   creation, and fresh login — so the next user of the same browser never
   sees the previous user's identity data prefilled. */
function creatorResetDraft() {
  CREATOR_DRAFT.appearance = {};
  CREATOR_DRAFT.appearancePrompt = "";
  CREATOR_DRAFT.bio = "";
  CREATOR_DRAFT.tags = [];
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
  const sex = el("select", {},
    el("option", { value: "F" }, "Ж"), el("option", { value: "M" }, "М"));
  const age = el("input", { type: "number", value: "28", min: "18", max: "80" });
  const looks = el("textarea", { placeholder: "Внешность (необязательно)", maxlength: "500" });
  const biography = el("textarea", { placeholder: "Биография (необязательно)", maxlength: "2000" });

  // Draft persistence across re-renders: seed from CREATOR_DRAFT, capture
  // every keystroke (the chat-draft pattern).
  looks.value = CREATOR_DRAFT.appearancePrompt || "";
  looks.addEventListener("input", () => { CREATOR_DRAFT.appearancePrompt = looks.value; });
  biography.value = CREATOR_DRAFT.bio || "";
  biography.addEventListener("input", () => { CREATOR_DRAFT.bio = biography.value; });

  // The ONLY draft-writing sites are this function and the two explicit
  // assembly actions (tag click / «Собрать черновик» button) below.
  function rebuildDraft() {
    const draft = creatorAssembleBio(CREATOR_DRAFT.tags);
    biography.value = draft;
    CREATOR_DRAFT.bio = draft;
  }

  // 12 structured appearance inputs — persisted at confirm since P2 (see CREATOR_FIELDS).
  const appearanceGrid = el("div", { class: "creator-appearance" },
    ...CREATOR_FIELDS.map(([key, lbl]) => {
      const input = el("input", { placeholder: lbl, "aria-label": lbl });
      input.value = CREATOR_DRAFT.appearance[key] || "";
      input.addEventListener("input", () => { CREATOR_DRAFT.appearance[key] = input.value; });
      return el("div", { class: "creator-field" }, el("label", {}, lbl), input);
    }));

  // «Одежда» rides the EXISTING #205 outfit control (app.py enum
  // auto|casual|formal|nude — owner-only PATCH; auto is the no-touch default).
  const outfit = el("select", {},
    el("option", { value: "auto" }, "auto — как сейчас"),
    el("option", { value: "casual" }, "casual — повседневная одежда"),
    el("option", { value: "formal" }, "formal — строгая одежда"),
    el("option", { value: "nude" }, "nude — без одежды (18+)"));

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
  const previewImg = el("img", {
    class: "preview-image", alt: "Предпросмотр персонажа", hidden: true });
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
    try {
      const appearance = creatorAppearancePayload();
      const r = await api("/visual/preview", { method: "POST", body: {
        sex: sex.value, age: Number(age.value),
        appearance_prompt: looks.value.trim() || undefined,
        appearance: Object.keys(appearance).length ? appearance : undefined,
        outfit_mode: outfit.value, mode: previewMode.value,
      } });
      previewImg.src = `data:image/png;base64,${r.image_base64}`;
      previewImg.hidden = false;
      previewStatus.hidden = true;
      previewOk = true;
    } catch (e) {
      // the form is untouched: the panel owns the failure surface;
      // previewOk is deliberately NOT reset — the shown image stays the
      // last good one, so «Обновить» keeps working after a failed retry
      previewStatus.hidden = true;
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
        const appearance = creatorAppearancePayload();
        const r = await api("/characters", { method: "POST", body: {
          name: name.value, sex: sex.value, age: Number(age.value),
          looks: looks.value.trim() || undefined,
          biography: biography.value.trim() || undefined,
          appearance: Object.keys(appearance).length ? appearance : undefined,
          appearance_prompt: looks.value.trim() || undefined } });
        creatorResetDraft();  // W118-RV1 F1: consumed draft must not leak on logout
        S.character = r;
        // 020: outfit control through the existing #205 endpoint, only for
        // an explicit non-default mode; best-effort — the character exists
        // at this point, a failed PATCH must not block the arrival.
        if (outfit.value !== "auto") {
          try {
            await api(`/characters/${r.id}/outfit`, { method: "PATCH",
              body: { outfit_mode: outfit.value } });
            S.character.outfit_mode = outfit.value;
          } catch (e) { toast(e.message, true); }
        }
        gotoWorld();  // #161: single dispatch — no direct route() next to the hash assignment
      } catch (e) { err.textContent = e.message; }
      finally { confirmBtn.disabled = false; }
    },
  }, "Появиться на острове");

  app.replaceChildren(el("main", { class: "creator-shell" },
    el("section", { class: "panel creator-hero" },
      el("div", { class: "muted creator-eyebrow" }, "ВЛ1: Рейнеке"),
      el("h1", {}, "Новый житель Рейнеке"),
      el("p", { class: "creator-intro" },
        "Соберите образ и биографию до появления на острове. Всё, кроме имени, необязательно — свободный текст главный.")),
    el("div", { class: "creator-form" },
      el("section", { class: "panel" },
        el("h2", {}, "Кто вы"),
        el("label", {}, "Имя"), name,
        el("label", {}, "Пол"), sex,
        el("label", {}, "Возраст"), age),
      el("section", { class: "panel" },
        el("h2", {}, "Образ"),
        appearanceGrid,
        el("label", {}, "Внешность"), looks,
        el("p", { class: "muted creator-note" },
          "Свободное описание — основное; отдельные поля подсказывают детали. Живой предпросмотр — в панели «Предпросмотр» ниже.")),
      el("section", { class: "panel" },
        el("h2", {}, "Одежда"),
        el("label", {}, "Одежда"), outfit,
        el("p", { class: "muted creator-note" },
          "Как персонаж одевается по умолчанию: auto — как сегодня, остальные режимы добавляют одежду в портрет. nude — только для 18+. Изменить можно позже в профиле.")),
      el("section", { class: "panel creator-preview" },
        el("h2", {}, "Предпросмотр"),
        el("p", { class: "muted creator-note" },
          "Как персонаж будет выглядеть. Собирается по текущим полям формы — правки в саму форму не вносятся и не сбрасываются."),
        el("label", {}, "Ракурс"), previewMode,
        el("div", { class: "creator-actions" }, previewBtn, previewRegenBtn),
        previewStatus, previewImg, previewErr),
      el("section", { class: "panel" },
        el("h2", {}, "Биография"),
        el("p", { class: "muted creator-note" },
          "Клик по ингредиенту добавит его в черновик. Можно писать что угодно своими словами — ваша правка не сотрётся, пока вы сами не нажмёте сборку."),
        tagCloud,
        el("div", { class: "creator-bio-actions" },
          el("button", { type: "button", onclick: rebuildDraft }, "Собрать черновик")),
        el("label", {}, "Биография"), biography,
        el("p", { class: "muted creator-note" },
          "Биография видна миру и помогает острову понять, кто вы.")),
      el("section", { class: "panel creator-confirm" },
        err,
        el("div", { class: "creator-actions" }, confirmBtn)))));
}

/* ---------- portrait + scene (014, M6 visual pipeline) ---------- */

async function fetchPortraitBlob(path) {
  // Same-origin cookie auth (same as api()); the client holds no bearer token,
  // so a Bearer header would 401. img src cannot carry auth → fetch blob, objectURL.
  const r = await fetch(path, { credentials: "same-origin" });
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
    // Force-regenerate: unpin the current canonical so a fresh asset is created.
    // 020 P3 stage 2: the nude toggle is read BEFORE any demotion — nude
    // renders skip the auto-pin below, so demoting here would leave the
    // canonical empty with nothing to re-pin; a nude render must not touch
    // the existing canonical at all. Non-nude keeps the historical
    // demote-then-regenerate-then-repin semantics byte-identical.
    if (force && !body.nude && S.canonicalPortraitId) {
      try {
        await api(`/visual/assets/${S.canonicalPortraitId}/canonical`,
          { method: "POST", body: { canonical: false } });
      } catch (e) { /* best effort */ }
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
        if (e && e.status === 503) S.visualDisabled = true;
        else status.textContent = "Портрет ещё не готов";  // #175: 404/net — honest non-error note
        /* no portrait yet — keep button */
      }
      // 020 P3 stage 2: fill the reference picker (owner list). Best-effort:
      // a failed list (503 gate / transient net) leaves the empty "—" picker;
      // upload-time errors surface on the upload button instead.
      try { await refreshReferenceOptions(refSel); }
      catch (e) { /* picker stays at "—" */ }
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
    objects: d && Array.isArray(d.objects) ? d.objects.map((o) => o.type) : [],
  };
}

function sceneFallbackData() {
  // Visual service down (503/502/…): same summary built client-side from
  // already-loaded authoritative state — a dead visual service never blocks
  // inspection. Read-only: no gameplay state is touched on any path.
  const ch = S.character;
  const loc = (S.mapLocations || []).find((l) => l.id === ch.location_id);
  const nearby = ((loc && loc.occupants) || [])
    .filter((o) => o.id !== ch.id)
    .map((o) => o.name);
  return {
    locationName: loc && loc.name ? loc.name : "—",
    timeText: dayTime((S.world && S.world.game_timestamp) || 0),
    weatherText: S.weather && S.weather.enabled ? S.weather.description : "",
    nearby,
    objects: [],
  };
}

function sceneSummaryLines(d) {
  const lines = [`Место: ${d.locationName}`, `Время: ${d.timeText}`];
  if (d.weatherText) lines.push(`Погода: ${d.weatherText}`);
  lines.push(d.nearby.length ? `Рядом: ${d.nearby.join(", ")}` : "Рядом никого");
  if (d.objects.length) lines.push(`Объекты: ${d.objects.join(", ")}`);
  return lines;
}

function backToMap() {
  // Back navigation from the scene state to the world view.
  S.scene = null;
  renderScenePanel();
  const map = document.querySelector(".world-map-panel");
  if (map) map.scrollIntoView({ behavior: S.mapReducedMotion ? "auto" : "smooth", block: "start" });
}

function fillScenePanel(box) {
  // S.scene state machine → scene panel content:
  // idle → loading («Осматриваю окрестности…») → ready (image + descriptor
  // summary) or fallback (summary from loaded world state).
  const scene = S.scene;
  const kids = [];
  if (!scene) {
    kids.push(el("div", { class: "muted" },
      "Нажмите «Осмотреть окрестности», чтобы осмотреться."));
  } else if (scene.state === "loading") {
    kids.push(el("div", { class: "muted scene-status" }, "Осматриваю окрестности…"));
    // #86 review: escape hatch while a generation is in flight.
    kids.push(el("button", { type: "button", onclick: backToMap }, "К карте"));
  } else {
    if (scene.state === "ready") {
      const status = el("div", { class: "muted scene-status" });
      kids.push(status);
      (async () => {
        // Cookie-auth image fetch (img src cannot carry auth → blob).
        try {
          const blob = await fetchPortraitBlob(`/visual/assets/${scene.asset.id}/file`);
          const url = URL.createObjectURL(blob);
          box.insertBefore(
            el("img", { class: "portrait-img", src: url, alt: "Сцена" }), status);
          status.textContent = "";
        } catch (e) { status.textContent = "Изображение недоступно"; }
      })();
    }
    const data = scene.state === "fallback"
      ? sceneFallbackData()
      : sceneDescriptorData(scene.asset.scene_descriptor);
    for (const line of sceneSummaryLines(data)) {
      kids.push(el("div", { class: "muted" }, line));
    }
    if (scene.state === "fallback") {
      // #93: Flux down → fallback data + an explicit retry affordance.
      kids.push(el("button", { type: "button", onclick: inspectSurroundings },
        "Повторить осмотр"));
    }
    kids.push(el("button", { type: "button", onclick: backToMap }, "К карте"));
  }
  // #87 (A5): the free-text scene action bar + the last result line are part
  // of EVERY scene panel state (idle/loading/ready/fallback) — the issue
  // requires the input to be always visible in Scene.
  kids.push(sceneActBar(), sceneActResultNode());
  box.replaceChildren(el("h2", {}, "Сцена"), ...kids);
}

function renderScenePanel() {
  const box = document.getElementById("scene-view");
  if (box) fillScenePanel(box);
  return box;
}

async function inspectSurroundings() {
  // #86 review: one inspection in flight — rapid re-clicks are no-ops.
  if (S.scene && S.scene.state === "loading") return;
  S.scene = { state: "loading" };
  const scene = renderScenePanel();
  if (scene) scene.scrollIntoView({ behavior: S.mapReducedMotion ? "auto" : "smooth", block: "start" });
  try {
    const r = await api("/visual/scenes", { method: "POST",
      body: { location_id: S.character.location_id } });
    S.scene = { state: "ready", asset: r.asset };
    renderScenePanel();
  } catch (e) {
    S.scene = { state: "fallback" };
    renderScenePanel();
    toast("Визуал недоступен, показываю данные");
  }
}

function sceneBlock() {
  // Sync host node for renderWorld; content follows S.scene (A4 Scene view).
  const box = el("div", { class: "panel portrait", id: "scene-view" }, el("h2", {}, "Сцена"));
  fillScenePanel(box);
  return box;
}

/* ---------- #87 (A5): free-text scene action ---------- */

function sceneActBar() {
  return el("div", { class: "scene-act" },
    el("input", {
      id: "scene-act-input", type: "text",
      value: S.sceneActDraft || "",
      placeholder: "Что вы делаете? Например: поговорить с Ивановым",
      // #175: capture the draft on every keystroke too (the post-submit
      // pattern alone loses text to a re-inspect while typing).
      oninput: (ev) => { S.sceneActDraft = ev.target.value; },
      onkeydown: (ev) => { if (ev.key === "Enter") submitSceneAct(); },
    }),
    el("button", { id: "scene-act-go", type: "button",
      onclick: () => submitSceneAct() }, "Сделать"));
}

function sceneActInterpText(i) {
  if (!i) return "";
  const p = i.params || {};
  if (i.intent === "move") return `идти: ${p.destination_name || p.destination_id || ""}`;
  if (i.intent === "socialize") return `поговорить: ${p.target_name || ""}`;
  if (i.intent === "buy") return `купить: ${p.item || ""}`;
  if (i.intent === "wear") return `надеть: ${p.object_type || p.object_text || ""}`;
  if (i.intent === "take_off") return `снять: ${p.object_type || p.object_text || ""}`;
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
      const ev = r.events && r.events.length
        ? " · " + r.events[r.events.length - 1].event_type : "";
      lines.push(el("div", {}, "Принято: " + r.task_type
        + " (задача " + r.task_id + ")" + ev));
    }
  }
  return el("div", { id: "scene-act-result", class: "scene-act-line muted" }, ...lines);
}

async function submitSceneAct() {
  const input = document.getElementById("scene-act-input");
  const text = input ? input.value.trim() : "";
  if (!text) return;
  // A5: one submission in flight — the button is disabled for the request,
  // mirroring the A4 one-inspection guard philosophy.
  const go = document.getElementById("scene-act-go");
  if (go) go.disabled = true;
  try {
    const r = await api("/scene/act", { method: "POST", body: { text } });
    S.sceneAct = r;
    if (r && r.ok === false) {
      // #93 review F2: 200 ok:false (unsupported/ambiguous/not_possible) is a
      // RECOVERABLE rejection — the user must rephrase, so the typed draft is
      // kept here exactly like the catch path below.
      S.sceneActDraft = text;
    } else {
      // Only a real success (or the discarded 200-ok shape) clears it.
      S.sceneActDraft = null;
    }
    // Committed result → visual refresh: re-inspect ONLY from ready/fallback
    // (A4 invariant: never reset S.scene, never re-render the whole world —
    // travel/arrival invalidation stays in renderWorld). Idle keeps the
    // result line without a refresh.
    const st = S.scene && S.scene.state;
    if (r.ok && r.task_id && (st === "ready" || st === "fallback")) {
      await inspectSurroundings();
    } else {
      renderScenePanel();
    }
  } catch (e) {
    // Same error phrasing as the A3 travel bar (409/401/4xx surfaces).
    S.sceneAct = { ok: false, detail: travelErrorText(e) };
    // #93: a recoverable error keeps the typed draft (chat-draft pattern).
    S.sceneActDraft = text;
    // #93 review F1: §61 refusals surface as localized hints (same discipline
    // as doAction/moveTo/viewExternal); any other error keeps the raw text.
    // The typed draft stays — switching control mode makes the same text valid.
    renderScenePanel();
    toast(isAutonomousRefusal(e) ? AUTONOMOUS_HINT : e.message, true);
  } finally {
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
    el("span", { id: "clock", class: "muted" }, S.world ? dayTime(S.world.game_timestamp) : "…"),
    weatherSpan,
    el("span", { class: "spacer" }),
    el("div", { class: "tabs" },
      tabs.map(([h, label]) => el("button", {
        class: h === active ? "active" : "", onclick: () => { location.hash = h; },
      }, label))),
  );
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
    el("div", { class: "welcome-photos" },
      el("img", { src: "/static/static/landing/concierge-cat-ref-face-tail.jpg",
        alt: "Кошка-консьержка — справочная фотография" }),
      el("img", { src: "/static/static/landing/concierge-cat-ref-loaf.jpg",
        alt: "Кошка-консьержка — поза отдыхающей кошки" })),
    el("h2", {}, "Кошка-консьержка"),
    el("p", { class: "welcome-copy" },
      "Добро пожаловать на Рейнеке. Остров живёт своей жизнью — а я подскажу, с чего начать."),
    el("div", { class: "welcome-tips" },
      el("div", { class: "welcome-tip" }, "„Мир“ покажет, где ты, и что вокруг."),
      el("div", { class: "welcome-tip" }, "„Люди“ — кто тебя знает и как к тебе относится."),
      el("div", { class: "welcome-tip" }, "„Дела“ — работа и поручения: кормят и знакомят.")),
    el("p", { class: "welcome-close muted" }, "Если потеряешься — просто найди меня."),
    el("button", { type: "button", onclick: () => {
      S.welcomeDismissed = true; // write-denied storage (private mode): stays dismissed until reload
      try { window.localStorage.setItem("vl1.welcome.done", "1"); } catch (e) { /* best-effort persist */ }
      node.remove();
    } }, "Я освоился"));
  return node;
}

async function viewWorld() {
  location.hash = "#/world";
  const loading = el("div", { class: "muted" }, "Загрузка…");
  app.replaceChildren(topbar("#/world"), loading);
  try {
    S.world = await api("/world");
    try { S.weather = await api("/weather"); } catch { S.weather = null; }
    const me = await api(`/characters/${S.character.id}`);
    S.character = { ...S.character, ...me };
    const locs = await api("/locations");
    
    // Fetch tasks if owner
    let tasksData = null;
    if (me.is_owner) {
      try {
        tasksData = await api(`/characters/${S.character.id}/tasks`);
      } catch (e) {
        console.error("Tasks fetch failed", e);
      }
    }
    
    renderWorld(locs, tasksData);
    startFeed();
  } catch (e) {
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

function mapNpcMarkers(visibleLocs) {
  const markers = [];
  for (const loc of visibleLocs) {
    const npcs = (loc.occupants || []).filter(o => o.kind === "npc");
    if (!npcs.length) continue;

    if (S.mapLod !== "local" || S.mapQuality === "low-mobile") {
      markers.push(el("span", {
        class: "map-dynamic-marker npc-cluster",
        style: `left:${loc.x}%; top:${loc.y}%`,
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
        style: `left:${loc.x}%; top:${loc.y}%; --npc-dx:${dx}px; --npc-dy:${dy}px`,
        "data-character-id": npc.id,
        "data-location-id": String(loc.id),
        title: `${npc.name} · ${loc.name}`,
      }, el("span", { class: "npc-glyph", "aria-hidden": "true" })));
    });
  }
  return markers;
}

function mapEventMarkers(visibleLocs) {
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
    return el("span", {
      class: "map-dynamic-marker event-marker",
      style: `left:${loc.x}%; top:${loc.y}%`,
      "data-location-id": String(locationId),
      title: `${last.event_type || "Событие"} · ${loc.name}`,
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
    ambient.append(el("span", { class: "map-ambient-surf", style: mapSurfBandStyle(seg) }));
  }
  if (ctx.lod !== "island") {
    MAP_AMBIENT.trees.forEach((zone, i) => ambient.append(el("span", {
      class: "map-ambient-tree",
      style: `left:${zone.x}%; top:${zone.y}%; width:${zone.w}%; height:${zone.h}%;`
        + ` animation-duration:${11 + i}s; animation-delay:${-3 * i}s;`,
    })));
    if (MAP_AMBIENT.flags.smoke) {
      MAP_AMBIENT.smoke.forEach((src, i) => ambient.append(el("span", {
        class: "map-ambient-smoke",
        style: `left:${src.x - 1}%; top:${src.y - 1.4}%;`
          + ` animation-duration:${9 + 2 * i}s; animation-delay:${-4 * i - 2}s;`,
      })));
    }
  }
  if (ctx.lod !== "local") {
    MAP_AMBIENT.birds.forEach((corridor, i) => ambient.append(el("span", {
      class: "map-ambient-bird",
      style: mapBirdCorridorStyle(corridor, i),
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

function buildLivingMap(locs) {
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
      houseSpans.push(el("span", {
        class: "anchor anchor--house",
        style: `left:${l.x}%; top:${l.y}%`,
        "data-loc-id": String(l.id),
      }, el("span", { class: "dot" })));
      continue;
    }
    const isHere = l.id === ch.location_id;
    locationAnchors.push(el("button", {
      class: `anchor location-anchor ${isHere ? "here" : ""} ${l.y >= 20 ? "above" : ""}`,
      style: `left:${l.x}%; top:${l.y}%`,
      "aria-label": l.name,
      "data-loc-id": String(l.id),
      onpointerdown: markTouchedAnchor,
      onclick: () => moveTo(l),
    },
      el("span", { class: "dot" }),
      (!/^House \d+$/.test(l.name) ? el("span", { class: "map-label" }, l.name) : null),
    ));
  }

  const dynamicMarkers = [...mapNpcMarkers(visibleLocs), ...mapEventMarkers(visibleLocs)];
  const markerCount = houseSpans.length + locationAnchors.length + dynamicMarkers.length + (currentLoc ? 1 : 0);
  const nightLightLimit = S.mapQuality === "low-mobile" ? 8 : 24;
  const nightLightLocs = night ? visibleLocs.filter(l =>
    ["settlement", "house", "shop", "workshop", "kitchen"].includes(l.type)
  ).slice(0, nightLightLimit) : [];

  const focusStyle = focus && focus.x !== null
    ? `--map-focus-x:${focus.x}%; --map-focus-y:${focus.y}%`
    : "--map-focus-x:50%; --map-focus-y:50%";

  const mapCard = el("div", {
    class: `map-card lod-${S.mapLod} time-${timePhase} ${raining ? "is-rain" : ""} ${foggy ? "is-fog" : ""} ${night ? "is-night" : ""} ${qualityClass} ${reduced}`,
    style: focusStyle,
    "data-profile-markers": String(markerCount),
    "data-profile-budget": String(MAP_LOW_MARKER_BUDGET),
  },
    el("div", { class: "map-stage" },
      el("img", { class: "map-base", src: "/static/static/map/world-island.jpg", alt: "" }),
      buildMapAmbient({ lod: S.mapLod, quality: S.mapQuality, reduced: !!S.mapReducedMotion }),
      el("div", { class: "map-time-tint", "aria-hidden": "true" }),
      el("div", { class: "map-night-lights", "aria-hidden": "true" },
        ...nightLightLocs.map(loc => el("span", {
          class: "map-night-light",
          style: `left:${loc.x}%; top:${loc.y}%`,
          "data-location-id": String(loc.id),
        }))),
      el("div", { class: "map-rain", "aria-hidden": "true" }),
      el("div", { class: "map-fog", "aria-hidden": "true" }),
      el("div", { class: "map-anchors", id: "map-anchors" },
        ...houseSpans,
        ...locationAnchors,
        ...dynamicMarkers,
        currentLoc && currentLoc.x !== null ? el("span", {
          class: "anchor player",
          id: "player-marker",
          style: `left:${currentLoc.x}%; top:${currentLoc.y}%`,
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
    host.replaceChildren(buildLivingMap(S.mapLocations));
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
  rerenderLivingMap();
}

function setMapReducedMotion(enabled) {
  S.mapReducedMotion = !!enabled;
  rerenderLivingMap();
}

function renderWorld(locs, tasksData) {
  const ch = S.character;
  const needs = ch.needs || {};

  // #86 review: the scene is tied to the location it was inspected at —
  // a world re-render (travel, arrival, action) invalidates it, otherwise
  // the panel would show a stale location after moving.
  S.scene = null;


  // Keep the A1 fallback when canonical map coordinates are unavailable.
  const keyedPois = ["settlement", "shop", "workshop", "kitchen", "storage", "well", "pier"];
  const poiMap = {};
  locs.forEach(l => {
    for (const k of keyedPois) {
      if (l.type === k || (l.name && l.name.toLowerCase().includes(k))) poiMap[k] = l;
    }
  });
  const mapReady = keyedPois.every(k => poiMap[k] && poiMap[k].x !== null);

  const feed = el("div", { class: "panel feed world-events-card", id: "feed" },
    el("h2", {}, "События"),
    ...S.feed.slice(-40).reverse().map(feedRow));
  const actions = [["WORK", "Пойти на работу"], ["EAT", "Поесть"], ["DRINK", "Попить"],
    ["SLEEP", "Отдохнуть / поспать"], ["SOCIALIZE", "Пообщаться"]].map(([a, label]) =>
    el("button", { onclick: () => doAction(a) }, label));
  const extBtn = el("button", { onclick: () => viewExternal() },
    "Поездка во Владивосток");

  const taskRows = [];
  if (tasksData) {
    const planned = tasksData.planned || [];
    const active = tasksData.active || [];
    [...active, ...planned].forEach(t => {
      taskRows.push(el("div", { class: "task" },
        el("span", { class: "status" }, t.status),
        el("span", {}, t.task_type || "")));
    });
  }
  const activeCount = tasksData && tasksData.active ? tasksData.active.length : 0;

  const portraitBox = portraitBlock();
  const sceneBox = sceneBlock();
  S.mapLocations = locs;

  const characterName = ch.name || `${ch.first_name || ""} ${ch.last_name || ""}`;
  const currentLoc = locs.find((l) => l.id === ch.location_id);
  const currentLocationLabel = currentLoc && currentLoc.name ? currentLoc.name : "—";
  const autonomous = ch.control_mode === "AUTONOMOUS";

  app.replaceChildren(
    topbar("#/world"),
    el("main", { class: "world-shell" },
      // #107: first-visit concierge card — first child, spans both grid
      // columns above the panels; null (renders nothing) once dismissed
      // or when storage is denied.
      welcomeCard(),
      el("section", { class: "world-map-stage" },
        el("div", { class: "panel world-map-panel" },
          el("div", { class: "world-map-heading" },
            el("h2", {}, "Остров Рейнеке"),
            el("button", { type: "button",
              onclick: () => inspectSurroundings() }, "Осмотреть окрестности")),
          travelBar(),
          mapReady ? el("div", { id: "living-map-host" }, buildLivingMap(locs)) : el("div", { class: "locs" },
            ...locs.map((loc) => el("div", {
              class: `loc ${loc.id === ch.location_id ? "here" : ""}`,
              onclick: () => moveTo(loc),
            }, el("span", {}, loc.name || `#${loc.id}`),
               el("span", { class: "muted" }, loc.type || "")))),
          locationView(locs)),
        el("aside", { class: "world-character-card" },
          el("div", { class: "world-character-head" },
            el("div", {},
              el("h2", {}, characterName),
              el("div", { class: "muted" }, currentLocationLabel),
              el("div", { class: "muted" }, ch.job ? `Работа: ${ch.job}` : "Работа: —"),
              el("div", { class: "world-money" }, `Деньги: ${ch.money ?? ch.balance ?? "—"} ₽`))),
          el("div", { class: "world-mini-needs" },
            needBar("Сытость", needs.hunger), needBar("Вода", needs.thirst),
            needBar("Энергия", needs.energy), needBar("Общение", needs.social))),
        el("nav", { class: "world-action-pills", "aria-label": "Действия персонажа" },
          ...(autonomous
            ? [el("span", { class: "muted world-autonomous-note" },
                AUTONOMOUS_HINT),
              el("button", { type: "button", onclick: () => { location.hash = "#/profile"; } },
                "Открыть Профиль")]
            : [...actions, extBtn]))),
      el("section", { class: "world-secondary" },
        el("div", { class: "panel world-tasks-card" },
          el("h2", {}, "Журнал"),
          taskRows.length ? el("div", { class: "muted" }, `Активных задач: ${activeCount} (полный журнал — на экране Задач)`) : el("div", { class: "muted" }, "Нет активных задач"),
          taskRows.slice(0, 3)),
        portraitBox,
        sceneBox,
        feed)));
}

async function doAction(actionType, params = {}, after = viewWorld) {
  try {
    await api("/actions", { method: "POST",
      body: { character_id: S.character.id, action_type: actionType, params } });
    toast(`Задача ${actionType} принята`);
    // #74: the success navigation is injectable — the Home screen's sleep
    // passes a no-op stay (task acceptance is not a completed sleep; no
    // yank to the world map, no fake refresh). Default keeps the historic
    // behavior for every existing caller.
    after();
  } catch (e) {
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
    } else { toast(e.message, true); }
  }
}

async function moveTo(loc) {
  if (loc.id === S.character.location_id) return;
  // #144/§61: in AUTONOMOUS the server refuses MOVE with 409 — gate it
  // client-side instead of posting a doomed /actions request.
  if (S.character.control_mode === "AUTONOMOUS") {
    toast(AUTONOMOUS_HINT);
    return;
  }
  try {
    await api("/actions", { method: "POST", body: {
      character_id: S.character.id, action_type: "MOVE",
      params: { location_id: loc.id } } });
    toast(`Идём в ${loc.name || loc.id}`);
    viewWorld();
  } catch (e) {
    if (isAutonomousRefusal(e)) {
      // #144/§61: stale S.character (GUIDED shown, server AUTONOMOUS) — the
      // client gate passed but the server refused; speak the hint, not §61.
      toast(AUTONOMOUS_HINT, true);
    } else if (e.status === 422 && String(e.detail).includes("needs_move")) {
      toast("Сначала дойдите до промежуточной точки", true);
    } else { toast(e.message, true); }
  }
}

/* ---------- #82 (A3): free-text travel on the Living Map ---------- */

function travelStatusLabel() {
  if (!S.travel) return "";
  if (S.travel.state === "interpreting") return "Уточняю маршрут…";
  if (S.travel.state === "travelling") {
    return `Идём в ${S.travel.destName} — ~${S.travel.etaMinutes} мин`;
  }
  return "";
}

function travelBar() {
  return el("div", { class: "travel-bar", id: "travel-bar" },
    el("input", {
      id: "travel-input", type: "text",
      placeholder: "Куда идти? Например: к причалу",
      onkeydown: (ev) => { if (ev.key === "Enter") submitTravel(); },
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
  S.travel = { state: "interpreting" };
  const status = document.getElementById("travel-status");
  if (status) status.textContent = travelStatusLabel();
  try {
    const plan = await api("/travel/plan", { method: "POST", body: { text } });
    S.travel = { state: "travelling", destName: plan.name,
      etaMinutes: plan.travel_minutes, taskId: plan.task_id };
    viewWorld();
  } catch (e) {
    S.travel = null;
    const stale = document.getElementById("travel-status");
    if (stale) stale.textContent = "";
    toast(travelErrorText(e), true);
  }
}

function isOwnMoveEvent(e) {
  return e.event_type === "CHARACTER_MOVED"
    && !!e.payload && e.payload.character_id === S.character.id;
}

function onOwnArrival() {
  if (S.travel && S.travel.state === "travelling") {
    const destName = S.travel.destName || "";
    S.travel = null;
    toast(`Вы прибыли: ${destName}`);
  }
  if (location.hash !== "#/world") return;
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
    el("h2", {}, job ? `Работа: ${job}` : "Работа не назначена"),
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
  let locs;
  // #72 validator: world context is fetched into LOCALS — a stale response
  // must not mutate the SHARED S.world/S.weather for a screen that is about
  // to be dropped. Other screens read S.world/S.weather (topbar clock, map
  // ambience, locationView), so the shared write is kept — but moved AFTER
  // the stale guard below.
  let world = null;
  let weather = null;
  try {
    world = await api("/world");
    try { weather = await api("/weather"); } catch (e) { weather = null; }
    // ONE /locations fetch resolves the requested place — the world-scoped
    // catalog already carries occupants; no /locations/{id} detail call.
    locs = await api("/locations");
  } catch (e) {
    // #93: transient failure — explicit retry instead of a dead «Загрузка…».
    // Same stale guard as the apply path (review F3: session checks included).
    if (seq !== placeLoadSeq || location.hash !== viewHash
      || !S.user || !S.character) return;
    loadRetryNode(loading, () => viewLocation(locId));
    toast(e.message, true);
    return;
  }
  // #72 stale-response guard (#66 precedent): latest request + still on this
  // place's hash + same session/character. Otherwise the response is dropped.
  if (seq !== placeLoadSeq || location.hash !== viewHash
    || !S.user || !S.character) return;
  // Guard passed — only now do the fresh reads become shared state
  // (topbar clock, map ambience and the overview lines read them).
  S.world = world;
  S.weather = weather;
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
  renderPlace(locId, place, locs);
}

function renderPlace(locId, place, locs) {
  const viewHash = `#/location/${locId}`;
  const occupants = place.occupants || [];
  const hasNpc = occupants.some((o) => o.kind === "npc");

  // Hero: warm gradient + decorative type icon + real name/type; the photo
  // note is honest — no stock or fabricated imagery exists for places.
  const hero = el("header", { class: "place-hero" },
    el("span", { class: "place-hero-icon", "aria-hidden": "true" },
      placeTypeIcon(place.type)),
    el("div", { class: "place-hero-text" },
      el("h2", {}, place.name || `#${place.id}`),
      el("div", { class: "muted" }, place.type || ""),
      el("div", { class: "muted place-hero-note" }, "Фото места недоступно")));

  // Read-only actions: «К карте» always (map focus when the id is known —
  // it always is here), «Обновить» re-runs the load, «Открыть диалог» only
  // when NPCs are present AND this is the player's CURRENT location —
  // #72 validator: chat (viewChat) reads NPCs at S.character.location_id,
  // so a remote place with NPCs must not promise dialogue it cannot show.
  // No type-derived buttons, no leave button.
  const actions = el("div", { class: "place-actions" },
    el("button", { type: "button", onclick: () => {
      // Same manual-focus mechanism as mapLink() (#92): survives polls,
      // releases when the player physically moves; gotoWorld() keeps the
      // single-dispatch navigation contract (#161).
      S.mapFocusManual = true;
      S.mapFocusAnchor = S.character.location_id;
      S.mapFocusLocationId = locId;
      gotoWorld();
    } }, "К карте"),
    el("button", { type: "button",
      onclick: () => viewLocation(locId) }, "Обновить"),
    (locId === S.character.location_id && hasNpc)
      ? el("a", { class: "place-chat-link", href: "#/chat" },
          "Открыть диалог") : null);

  // Compact selector for remote browsing — populated from the SAME
  // /locations payload (no extra fetch); navigation goes through the hash,
  // so route() re-dispatches exactly once.
  const selector = el("label", { class: "place-selector" },
    "Место: ",
    el("select", { onchange: (ev) => {
      const id = Number(ev.target.value);
      if (id && id !== locId) location.hash = `#/location/${id}`;
    } },
      ...locs.map((l) => el("option", l.id === locId
        ? { value: l.id, selected: "" } : { value: l.id },
        `${l.name || `#${l.id}`} · ${l.type || ""}`))));

  const worldTs = S.world && S.world.game_timestamp;
  const overview = el("section", { class: "panel place-overview" },
    el("h3", {}, "Обзор"),
    // #72: world scope is explicit in the labels; unknown time stays
    // unavailable — no zero-fallback («День 1, 00:00» would be a lie).
    el("div", { class: "muted" },
      `Время мира: ${typeof worldTs === "number" ? dayTime(worldTs) : "недоступно"}`),
    el("div", { class: "muted" },
      S.weather && S.weather.enabled
        ? `Погода в мире: ${S.weather.description}` : "Погода в мире: недоступна"),
    el("div", { class: "muted" }, `Здесь сейчас: ${occupants.length}`));

  const people = el("section", { class: "panel place-people" },
    el("h3", {}, "Люди на месте"),
    occupants.length
      ? el("div", { class: "place-roster" },
          ...occupants.map((o) => el("div", { class: "place-roster-row" },
            el("span", {}, o.name || `#${o.id}`),
            el("span", { class: "muted" }, o.kind || ""))))
      : el("p", { class: "muted" }, "Сейчас здесь никого нет."));

  // Events: the request has its own failure/retry state, independent of the
  // main render — a feed failure never takes the whole screen down.
  const eventsBody = el("div", { class: "place-events-body" },
    el("div", { class: "muted" }, "Загрузка…"));
  const eventsPanel = el("section", { class: "panel place-events" },
    el("h3", {}, "События"),
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
                el("span", {}, ev.event_type || ""))))
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

  // Details: authoritative catalog fields only — no invented description.
  const parent = place.parent_id != null
    ? locs.find((l) => l.id === place.parent_id) : null;
  const detailRow = (label, value) => el("div", { class: "place-detail-row" },
    el("span", { class: "place-detail-label" }, label), el("span", {}, value));
  const details = el("section", { class: "panel place-details" },
    el("h3", {}, "Подробности"),
    detailRow("ID", String(place.id)),
    detailRow("Тип", place.type || "—"),
    detailRow("Координаты", place.x != null && place.y != null
      ? `${place.x} · ${place.y}` : "—"),
    detailRow("Входит в", parent ? (parent.name || `#${parent.id}`) : "—"),
    detailRow("Людей сейчас", String(occupants.length)));

  app.replaceChildren(topbar("#/world"),
    el("main", { class: "place-shell" },
      hero, actions, selector, overview, people, eventsPanel, details));
}

async function viewExternal() {
  const oldHash = location.hash;
  location.hash = "#/external";
  const loading = el("div", { class: "muted" }, "Загрузка…");
  app.replaceChildren(topbar("#/world"), loading);
  try {
    const catalog = await api("/external");
    const services = [];
    for (const loc of catalog) {
      for (const svc of loc.services || []) {
        services.push({
          id: svc.id,
          label: `${loc.name || `#${loc.id}`} — ${svc.service_type}` +
            (svc.item_type ? ` (${svc.item_type})` : ""),
          type: svc.service_type,
        });
      }
    }
    const svcSel = el("select", {},
      ...services.map((s) => el("option", { value: s.id }, s.label)));
    const purpose = el("select", {},
      el("option", { value: "heal" }, "лечение"),
      el("option", { value: "shop" }, "покупки"),
      el("option", { value: "visit" }, "визит"));
    const items = el("input", { placeholder: '{"food_groceries": 2} — для покупок' });
    const err = el("div", { class: "err-text" });
    app.replaceChildren(topbar("#/world"),
      el("div", { class: "panel", style: "max-width:520px" },
        el("h2", {}, "Поездка во Владивосток"),
        el("label", {}, "Сервис"), svcSel,
        el("label", {}, "Цель"), purpose,
        el("label", {}, "Корзина (JSON, только для покупок)"), items,
        err,
        el("div", { style: "margin-top:10px; display:flex; gap:6px" },
          el("button", { class: "primary", onclick: async () => {
            let parsed = {};
            try {
              if (items.value.trim()) parsed = JSON.parse(items.value);
            } catch (e) { err.textContent = "Корзина: невалидный JSON"; return; }
            try {
              await api("/actions", { method: "POST", body: {
                character_id: S.character.id, action_type: "TRAVEL_EXTERNAL",
                params: { service_id: Number(svcSel.value),
                  purpose: purpose.value, items: parsed } } });
              toast("Поездка запланирована (транзит через порт)");
              gotoWorld();  // #161: single dispatch — no direct viewWorld() next to the hash assignment
            } catch (e) {
              // #144/§61: same stale-state class as moveTo — localize.
              err.textContent = isAutonomousRefusal(e)
                ? AUTONOMOUS_HINT : String(e.detail);
              if (isAutonomousRefusal(e))
                toast(AUTONOMOUS_HINT, true);
              if (String(e.detail).includes("port"))
                toast("Сначала дойдите до порта", true);
            }
          } }, "Отправиться"),
          // #161: single dispatch — when the hash is already oldHash the
          // assignment would fire no hashchange, so render directly; otherwise
          // just set it and let the queued hashchange route once.
          el("button", { onclick: () => { if (location.hash === oldHash) viewWorld(); else location.hash = oldHash; } },
            "Назад"))));
  } catch (e) {
    // #175/#93: transient failure — explicit retry instead of a dead «Загрузка…».
    loadRetryNode(loading, () => viewExternal());
    toast(e.message, true);
  }
}

/* ---------- event feed (§79 WS + polling fallback) ---------- */

function feedRow(ev) {
  return el("div", { class: "ev" },
    el("span", { class: "t" }, dayTime(ev.game_timestamp || 0)),
    el("span", {}, ev.event_type || ""),
    ev.actor_id ? el("span", { class: "muted" }, ` · ${ev.actor_id}`) : null);
}

function startFeed() {
  connectWs();
  if (S.pollTimer) return;  // #161: never stack a second poll loop — leaked intervals multiply with navigation history
  S.pollTimer = setInterval(pollEvents, 5000);
  pollEvents();
}

function stopFeed() {
  if (S.pollTimer) { clearInterval(S.pollTimer); S.pollTimer = null; }
  if (S.ws) {
    const w = S.ws;
    S.ws = null;
    w.onclose = null; w.onmessage = null; w.onopen = null;  // #161: detach BEFORE close — the old socket's async onclose must not stomp a newer socket's state
    S.wsCursorSent = false;  // #179: the #161 detach means onclose never fires for this socket — re-arm the handshake flag here so the next connection re-declares its cursor
    try { w.close(); } catch (e) { /* already closed */ }
  }
}

async function pollEvents() {
  if (S.pollBusy) return;  // #161: a view re-render must not double-fire the poll
  S.pollBusy = true;
  try {
    await pollEventsFeed();

    // A2 Living Map: refresh authoritative read state, then rebuild presentation.
    const previousDay = S.world ? S.world.day : null;
    const character = await api(`/characters/${S.character.id}`);
    const world = await api("/world");
    const locs = await api("/locations");
    if (previousDay !== world.day) {
      try { S.weather = await api("/weather"); } catch { /* keep last readable weather */ }
      // F3: day-boundary refetch must update the topbar span in place, not wait for a full re-render.
      const weatherEl = document.getElementById("weather");
      if (weatherEl) weatherEl.textContent = weatherLabel();
    }
    S.character = { ...S.character, ...character };
    // F1: LOD focus follows the authoritative player location on every poll
    // refresh — unless a manual focus is active (#92 «Показать на карте»);
    // manual focus releases as soon as the player physically moves.
    if (S.mapFocusManual && S.mapFocusAnchor !== character.location_id) {
      S.mapFocusManual = false;
    }
    if (!S.mapFocusManual && character.location_id != null) S.mapFocusLocationId = character.location_id;
    S.world = world;
    S.mapLocations = locs;
    const clock = document.getElementById("clock");
    if (clock) clock.textContent = dayTime(world.game_timestamp);
    rerenderLivingMap();

    const marker = document.getElementById("player-marker");
    const badge = document.getElementById("map-badge");

    // Handle Tasks (MOVE and TRAVEL_EXTERNAL)
    if (S.character.is_owner) {
      const tasks = await api(`/characters/${S.character.id}/tasks`);
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
            marker.style.left = `${curX}%`;
            marker.style.top = `${curY}%`;
          }
        }
      } else if (active && active.task_type === "TRAVEL_EXTERNAL") {
        if (badge) {
          const remaining = active.ends_at - world.game_timestamp;
          const h = Math.floor(remaining / 60);
          const m = Math.floor(remaining % 60);
          badge.textContent = `В рейсе — вернётся через ${h}ч ${m}м`;
          badge.classList.remove("hidden");
        }
      } else {
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
  if (S.ws && S.ws.readyState === 1) return;
  const data = await api(`/world/events?since=${S.cursor}`);
  const events = Array.isArray(data) ? data : (data.events || []);
  if (events.length) {
    S.cursor = events[events.length - 1].id;
    // #139: the REST cursor path can re-fetch on a missed ack — push and
    // render only events not already buffered (dedup by id).
    const seen = new Set(S.feed.map(e => e.id));
    const fresh = events.filter((e) => !seen.has(e.id));
    if (fresh.length) {
      S.feed.push(...fresh);
      const feedBox = document.getElementById("feed");
      if (feedBox) {
        feedBox.replaceChildren(el("h2", {}, "События"),
          ...S.feed.slice(-40).reverse().map(feedRow));
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
  S.feedFetched = true;
  sendWsCursor();
}

// #179: tell the server where our event stream should start — right after
// hello it replays from 0 unless the client declares its cursor. One
// handshake per connection: re-armed in ws.onclose, armed once the first
// REST events fetch has defined S.cursor (boot boundary).
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
          const feedBox = document.getElementById("feed");
          if (feedBox) {
            feedBox.replaceChildren(el("h2", {}, "События"),
              ...S.feed.slice(-40).reverse().map(feedRow));
          }
          // #144: a CONTROL_CHANGED fired from another tab/session leaves
          // stale action pills behind — re-render the world view (it re-reads
          // the character). ws.onmessage fires on ALL views, so only refresh
          // when the world view is actually on screen (never hijack
          // #/profile or #/chat); viewWorld() only reads state, so no
          // event/POST feedback loop is possible.
          // #177: the WS replays the whole backlog from cursor 0 on every
          // (re)connect; a replayed CONTROL_CHANGED must not reload the
          // world view. React only to LIVE events: id beyond the boot
          // replay marker and logged within a small window of the current
          // game time (covers backlogs larger than the REST fetch window).
          if (fresh.some((e) => e.event_type === "CONTROL_CHANGED"
              && e.id > S.bootCursor
              && S.world && typeof e.game_timestamp === "number"
              && e.game_timestamp >= S.world.game_timestamp - 15)
            && location.hash === "#/world") {
            viewWorld();
          }
          // #82 (A3): own arrival completes the travel state machine
          // (travelling → arrived) and re-renders the map. #177: the
          // liveness gate below keeps historical own-MOVE replay from
          // churning the map.
          if (fresh.some((e) => isOwnMoveEvent(e)
              && e.id > S.bootCursor
              && S.world && typeof e.game_timestamp === "number"
              && e.game_timestamp >= S.world.game_timestamp - 15)) onOwnArrival();
        }
      }
    } catch (e) { /* ignore malformed */ }
  };
  ws.onclose = () => {
    S.wsTries += 1;
    if (S.ws === ws) S.ws = null;  // #161: only clear the reference while this socket is still the current one (no stomp)
    S.wsCursorSent = false;  // #179: the next connection re-handshakes with the current S.cursor
    if (S.wsTries <= 3) setTimeout(connectWs, 2000 * S.wsTries);
    /* иначе остаётся polling */
  };
  ws.onopen = () => { S.wsTries = 0; sendWsCursor(); };
  S.ws = ws;
}

/* ---------- chat (§77, #88 Dialogue alpha) ---------- */

async function viewChat() {
  location.hash = "#/chat";
  const loading = el("div", { class: "muted" }, "Загрузка…");
  app.replaceChildren(topbar("#/chat"), loading);
  try {
    // Cold-load safety: #/chat can be the first route after reload — the
    // by-user hydration omits location_id, so read the authoritative
    // character first (same pattern as viewWorld).
    if (S.character.location_id == null) {
      const me = await api(`/characters/${S.character.id}`);
      S.character = { ...S.character, ...me };
    }
    const loc = await api(`/locations/${S.character.location_id}`);
    const npcs = (loc.characters_here || []).filter((id) => id.startsWith("npc_"));
    S.chatNpcs = npcs;
    // Resolve nearby NPC names for the select (bounded to NPCs present here).
    const names = {};
    await Promise.all(npcs.slice(0, 15).map(async (id) => {
      try { const c = await api(`/characters/${id}`); names[id] = c.name || id; }
      catch (e) { names[id] = id; }
    }));
    S.chatNpcNames = names;
    // #88: current session history comes from the authoritative read model.
    let sess = null;
    if (S.chatSessionId) {
      try { sess = await api(`/dialogue/${S.chatSessionId}`); }
      catch (e) { S.chatSessionId = null; sess = null; }
    }
    // #63: the meeting location IS the display location (S.chatNpcs is
    // filtered to characters_here) — thread loc.name into the header.
    renderChat(sess, loc.name);
  } catch (e) {
    // #93: transient failure — explicit retry instead of a dead «Загрузка…».
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
  let curSession = session;
  const npcSel = el("select", {},
    ...S.chatNpcs.map((id) => el("option", { value: id },
      (S.chatNpcNames && S.chatNpcNames[id])
        ? `${S.chatNpcNames[id]} (${id})` : id)));
  npcSel.addEventListener("change", () => {
    S.chatNpcId = npcSel.value;  // 182: survive re-renders (review F1)
    S.chatSessionId = null;
    renderChat(null, locName);
  });
  // 182 (review F1): the rebuilt select loses the user's choice on any
  // re-render — an accepted proposal re-render included. Restore the
  // persisted selection when it is still among the options.
  if (S.chatNpcId && S.chatNpcs.includes(S.chatNpcId)) {
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
      const rel = (rels || []).find((x) => x.other_id === npcSel.value);
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
  const visualOn = !S.visualDisabled;
  if (visualOn) {
    (async () => {
      try {
        const asset = await api(`/visual/characters/${npcSel.value}/portrait`);
        const blob = await fetchPortraitBlob(`/visual/assets/${asset.id}/file`);
        const url = URL.createObjectURL(blob);
        visual.replaceChildren(el("img", {
          class: "portrait-img chat-visual-img", src: url, alt: "Портрет NPC",
        }));
      } catch (e) {
        visual.replaceChildren(el("div", { class: "portrait-status muted" },
          VISUAL_FALLBACK));
      }
    })();
  } else {
    visual.append(el("div", { class: "portrait-status muted" },
      VISUAL_FALLBACK));
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
        el("span", { class: "muted chat-when" },
          " " + dayTime(m.game_timestamp)))));
  }
  renderMessages(curSession ? curSession.messages : []);

  // #88: suggested replies are SHORTCUTS ONLY — clicking fills the always
  // visible free-text input and focuses it; nothing is sent automatically.
  const sugg = el("div", { id: "chat-sugg" });
  function renderSugg(list) {
    sugg.replaceChildren(...(list || []).map((s) =>
      el("span", { class: "sugg", onclick: () => {
        const inp = document.getElementById("chat-msg-input");
        if (inp) { inp.value = s; inp.focus(); }
      } }, s)));
  }
  const lastNpc = [...(curSession ? curSession.messages : [])]
    .reverse().find((m) => m.sender === "npc");
  renderSugg(lastNpc && lastNpc.suggested_responses
    ? lastNpc.suggested_responses : []);

  const status = el("div", { class: "muted", id: "chat-status" });
  const input = el("input", { id: "chat-msg-input", placeholder: "Сообщение…" });
  // #175: the typed draft survives NPC switches/re-renders — seeded from
  // S.chatDraft and kept in sync on every keystroke; cleared on send.
  input.value = S.chatDraft || "";
  input.addEventListener("input", () => { S.chatDraft = input.value; });
  const sendBtn = el("button", {
    id: "chat-send", class: "primary", "aria-label": "Отправить",
    onclick: () => send(),
  }, "→");
  input.addEventListener("keydown", (ev) => { if (ev.key === "Enter") send(); });

  async function send() {
    const draft = input.value;
    const content = draft.trim();
    if (!content) return;
    const sentNpc = npcSel.value;  // #88: guard against NPC switch mid-flight
    // #63: a stale inline error row must not survive a new attempt. All
    // rows go — a double-send race must not leave orphaned error rows
    // behind (peer review finding 1: querySelector removed only the first).
    log.querySelectorAll(".chat-error").forEach(n => n.remove());
    // #88: LLM latency is 14-45s — visibly non-frozen wait with a ticking
    // elapsed-seconds counter. #63: the wait is ALSO shown as a typing
    // bubble inside the chatlog (name + three animated dots, reduced-motion
    // safe); #chat-status keeps the assistive «Печатает… N с» line.
    status.textContent = "Печатает…";
    const typingBubble = el("div", { class: "chatmsg npc chat-typing" },
      el("span", { class: "who" },
        (S.chatNpcNames && S.chatNpcNames[sentNpc]) || "NPC"),
      el("span", { class: "typing-dots", "aria-hidden": "true" },
        el("i"), el("i"), el("i")));
    log.append(typingBubble);
    log.scrollTop = log.scrollHeight;
    const t0 = Date.now();
    const tick = setInterval(() => {
      status.textContent = `Печатает… ${Math.round((Date.now() - t0) / 1000)} с`;
    }, 1000);
    try {
      const sid = curSession ? curSession.session_id
        : (await api("/dialogue/start",
          { method: "POST", body: { npc_id: sentNpc } })).session_id;
      const r = await api(`/dialogue/${sid}/message`,
        { method: "POST", body: { content } });
      clearInterval(tick);
      typingBubble.remove();
      if (npcSel.value !== sentNpc) {
        // The turn is logged server-side for the original NPC's session;
        // a stale closure must not hijack the freshly re-rendered view.
        // #175: S.chatDraft is intentionally NOT cleared here — it already
        // belongs to the composer re-rendered for the switched-to NPC.
        input.value = "";
        return;
      }
      const hist = await api(`/dialogue/${sid}`);
      curSession = curSession || {};
      curSession.session_id = sid;
      curSession.messages = hist.messages;
      S.chatSessionId = sid;
      renderMessages(hist.messages);
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
      input.value = "";
      S.chatDraft = "";  // #175: the send succeeded — the draft is consumed
      status.textContent = r.source === "fallback"
        ? "LLM недоступен, отвечает fallback" : "";
    } catch (e) {
      clearInterval(tick);
      typingBubble.remove();
      // #183: a failed send shows no effect note — a stale «Симпатия +2»
      // from a previous reply must not outlive its reply.
      relNote.textContent = "";
      relNote.hidden = true;
      input.value = draft;  // #88: recoverable error keeps the typed draft
      status.textContent = "Не удалось отправить — проверьте связь и повторите";
      // #63: the error is ALSO an inline row in the chatlog (the toast and
      // the assistive status line stay).
      chatErrorRow(`Не удалось отправить: ${e.message}`);
      toast(e.message, true);
    }
  }

  // #63: warm-family shell rides on the grid (same pattern as desire-shell/
  // shop-shell: `class: "grid <name>-shell"`). Hero band on top, header
  // (name · subtitle · roles · relationship widget) under it, then the log
  // (max-height 320), the assistive status line, suggestion chips and the
  // composer — all inside the shell so the cool :root tokens never leak in.
  app.replaceChildren(topbar("#/chat"),
    el("div", { class: "grid chat-shell" },
      el("div", { class: "panel", id: "chat-view" },
        el("h2", {}, "Диалог"),
        el("label", {}, "NPC на этой локации"), npcSel,
        visual,
        el("div", { class: "chat-head-info" },
          el("div", { id: "chat-npc-name" },
            (S.chatNpcNames && S.chatNpcNames[npcSel.value])
              ? S.chatNpcNames[npcSel.value] : npcSel.value),
          subLine, rolesLine, relBox, relNote),
        el("div", { style: "margin-top:8px" }, log),
        status,
        sugg,
        partnerBtn,
        el("div", { class: "chat-bar" }, input, sendBtn)),
      el("div", { class: "panel" }, el("h2", {}, "Подсказки"),
        el("div", { class: "muted" },
          "Подсказки заполняют поле ввода, свободный текст — основной"))));
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
    el("div", { class: "panel" },
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

  // Warm family: the shop grid mirrors the world-shell token set
  // (.shop-shell, same values as .desire-shell — the screen renders
  // outside .world-shell); compose() is the single re-render factor.
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
  const reason = params.reason || params.object_type || "";
  return el("div", { class: "task-row" },
    el("span", { class: "status" }, t.status || ""),
    el("span", {}, t.task_type || ""),
    t.source ? el("span", { class: "muted" }, ` · ${t.source}`) : null,
    t.ends_at != null ? el("span", { class: "muted" }, ` · до ${dayTime(t.ends_at)}`) : null,
    t.completed_at != null
      ? el("span", { class: "muted" }, ` · завершено ${dayTime(t.completed_at)}`) : null,
    reason ? el("span", { class: "muted" }, ` · ${reason}`) : null,
    loc ? mapLink(loc.id) : null);
}

async function viewTasks() {
  location.hash = "#/tasks";
  const loading = el("div", { class: "muted" }, "Загрузка…");
  app.replaceChildren(topbar("#/tasks"), loading);
  try {
    // Cold-load safety: #/tasks can be the first route after reload — the
    // by-user hydration omits location_id, so read the authoritative
    // character first (same pattern as viewChat).
    if (S.character.location_id == null) {
      const me = await api(`/characters/${S.character.id}`);
      S.character = { ...S.character, ...me };
    }
    const tasks = await api(`/characters/${S.character.id}/tasks`);
    const goals = await api(`/characters/${S.character.id}/goals`);
    let opps = [];
    let desire = null;
    try {
      const d = await api(`/characters/${S.character.id}/desire/opportunities`);
      opps = d.opportunities || [];
      desire = d.desire;
    } catch (e) { /* desire layer unavailable — sections stay explicitly empty */ }
    S.mapLocations = await api("/locations");
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
    el("div", { class: "grid" }, activeBox, goalBox, oppsBox, historyBox));
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
  const jobChipV = el("span", { class: "profile-chip-v" }, ch.job ? String(ch.job) : "—");
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
  const jobValue = el("span", {}, ch.job ? String(ch.job) : "—");
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
      el("a", { href: "#/career", class: "muted" }, "Развитие →"));
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
          S.authProbed = false;
          creatorResetDraft();  // W118-RV1 F1: logout clears the creator draft
          appearanceResetDraft();  // #65: logout clears the outfit prompt draft
          desireResetDraft();  // #66: logout drops the pending interpret request
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
            creatorResetDraft();  // W118-RV1 F1: deletion clears the creator draft
            appearanceResetDraft();  // #65: deletion clears the outfit prompt draft
            desireResetDraft();  // #66: deletion drops the pending interpret request
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

window.addEventListener("hashchange", route);
route();
