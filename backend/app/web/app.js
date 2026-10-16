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
  "#/login": viewAuth, "#/world": viewWorld, "#/chat": viewChat,
  "#/people": viewPeople,
  "#/inventory": viewInventory, "#/shop": viewShop, "#/tasks": viewTasks, "#/desire": viewDesire,
  "#/profile": viewProfile, "#/admin": viewAdmin, "#/external": viewExternal,
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
  if (!S.character && hash !== "#/profile") return viewCreateCharacter();
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

/* ---------- character creation ---------- */

function viewCreateCharacter() {
  location.hash = "#/profile";
  const name = el("input", { placeholder: "Имя Фамилия" });
  const sex = el("select", {},
    el("option", { value: "F" }, "Ж"), el("option", { value: "M" }, "М"));
  const age = el("input", { type: "number", value: "28", min: "18", max: "80" });
  const looks = el("textarea", { placeholder: "Внешность (необязательно)", maxlength: "500" });
  const biography = el("textarea", { placeholder: "Биография (необязательно)", maxlength: "2000" });
  const err = el("div", { class: "err-text" });
  app.replaceChildren(el("div", { class: "authbox panel" },
    el("h1", {}, "Новый житель Рейнеке"),
    el("label", {}, "Имя"), name,
    el("label", {}, "Пол"), sex,
    el("label", {}, "Возраст"), age,
    el("label", {}, "Внешность"), looks,
    el("label", {}, "Биография"), biography,
    el("div", { style: "margin-top:10px" }, el("button", {
      class: "primary", onclick: async () => {
        try {
          const r = await api("/characters", { method: "POST", body: {
            name: name.value, sex: sex.value, age: Number(age.value),
            looks: looks.value.trim() || undefined,
            biography: biography.value.trim() || undefined } });
          S.character = r;
          gotoWorld();  // #161: single dispatch — no direct route() next to the hash assignment
        } catch (e) { err.textContent = e.message; }
      },
    }, "Появиться на острове")), err,
  ));
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
    // Force-regenerate: unpin the current canonical so a fresh asset is created.
    if (force && S.canonicalPortraitId) {
      try {
        await api(`/visual/assets/${S.canonicalPortraitId}/canonical`,
          { method: "POST", body: { canonical: false } });
      } catch (e) { /* best effort */ }
    }
    const r = await api(`/visual/portraits/${S.character.id}`, { method: "POST" });
    // Pin the new asset canonical: persists across reloads and feeds §70 scene refs.
    try {
      await api(`/visual/assets/${r.asset.id}/canonical`,
        { method: "POST", body: { canonical: true } });
      S.canonicalPortraitId = r.asset.id;
      S.visualDisabled = false;
    } catch (e) { /* best effort */ }
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

function portraitBlock() {
  // Synchronous box (a real DOM node, not a Promise) so renderWorld can embed it
  // directly; the canonical portrait loads asynchronously and fills in when ready.
  const status = el("div", { class: "portrait-status muted" });
  const box = el("div", { class: "panel portrait" }, el("h2", {}, "Портрет"), status);
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
    ["#/desire", "Цель"], ["#/profile", "Профиль"]];
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

async function doAction(actionType, params = {}) {
  try {
    await api("/actions", { method: "POST",
      body: { character_id: S.character.id, action_type: actionType, params } });
    toast(`Задача ${actionType} принята`);
    viewWorld();
  } catch (e) {
    // #144/§61: map the AUTONOMOUS refusal to the localized hint; every
    // other server error keeps its raw message (out of scope here).
    if (isAutonomousRefusal(e)) {
      toast(AUTONOMOUS_HINT, true);
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
    el("div", { class: "muted" }, dayTime((S.world && S.world.game_timestamp) || 0)),
    S.weather && S.weather.enabled
      ? el("div", { class: "muted" }, S.weather.description) : null,
    el("div", { class: "muted" },
      nearby.length ? `Рядом: ${nearby.join(", ")}` : "Рядом никого"),
    el("button", { type: "button",
      onclick: () => inspectSurroundings() }, "Осмотреть"));
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
    viewInventory();  // refresh state after the authoritative action
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
        el("a", { href: "#/shop" }, "Рынок"),
        " — витрина покупки и продажи между персонажами")));
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
          status.textContent = shopErrorText(e);
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

function desireOppCard(o) {
  // #64 §7: what is available, why it is linked (reason), source, next
  // action. Links go through the opportunity's authoritative link (#/chat,
  // #/world, #/inventory, #/profile); a location pointer in the action
  // params gets the existing map focus mechanism. Nothing is invented.
  const locId = o.action && o.action.params ? o.action.params.location_id : null;
  return el("div", { class: "opp desire-opp" },
    el("div", { class: "desire-opp-title" }, o.title || ""),
    o.reason ? el("div", { class: "muted" }, o.reason) : null,
    o.source ? el("div", { class: "muted" }, `Источник: ${o.source}`) : null,
    o.link ? el("a", { href: o.link }, "Открыть") : null,
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
      status.textContent = "";
      try {
        // #64 §4-5 honesty: interpret FIRST, create ONLY on confirm — the
        // deterministic catalog runs here without mutating anything.
        const r = await api(`/characters/${S.character.id}/desire/interpret`,
          { method: "POST", body: { text } });
        if (r && r.clarification) {
          // #87 ok:false pattern: the reason is data, nothing was created —
          // the typed text stays so the player can rephrase in place.
          status.textContent = r.clarification;
          field.focus();
          return;
        }
        renderDesire(desire, opps, { text, interpretation: r.interpretation });
      } catch (e) {
        // #93: recoverable failure keeps the typed draft on screen.
        status.textContent = "Не удалось разобрать желание. " + RETRY_HINT;
        toast(e.message, true);
      } finally { submitBtn.disabled = false; }
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
      field,
      status,
      desire
        ? el("div", { class: "muted desire-note" },
            "Текущая цель остаётся активной, пока вы не подтвердите новую.")
        : null,
      el("div", { class: "desire-actions" }, submitBtn),
      desireChipRow(field));
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

  function oppsPanel() {
    return el("div", { class: "panel", id: "desire-opps" },
      el("h2", {}, "Возможности для цели"),
      opps.length
        ? opps.map((o) => desireOppCard(o))
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
            desire ? oppsPanel() : sidePanel()) },
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
            oppsPanel()) },
          "Заменить цель"),
        dropBtn));
  }

  function composeHome() {
    if (desire) compose(activePanel(), oppsPanel());
    else compose(editorPanel(""), sidePanel());
  }

  if (preview) {
    // #64 §4-5: the interpretation preview gates the mutation — the desire
    // becomes active only through the confirm button.
    compose(confirmPanel(preview), desire ? oppsPanel() : sidePanel());
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
    return el("div", {}, rolesLine, rolesList);
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
          el("span", { class: "profile-row-k" }, "Локация"), locValue),
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
        return el("div", { style: "margin-top:8px" }, el("button", {
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
