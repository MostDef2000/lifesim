/* ВЛ1: Рейнеке — MVP web client (§77-78). Vanilla JS, без сборки.
   XSS-гигиена: рендер данных API только через textContent/createTextNode. */
"use strict";

const app = document.getElementById("app");
const toastEl = document.getElementById("toast");
const S = { user: null, character: null, world: null, ws: null, wsTries: 0,
  feed: [], cursor: 0, pollTimer: null, canonicalPortraitId: null, authProbed: false, visualDisabled: false,
  mapLod: "island", mapQuality: "balanced",
  mapReducedMotion: window.matchMedia("(prefers-reduced-motion: reduce)").matches,
  mapFocusLocationId: null, mapLocations: [] };

/* ---------- helpers ---------- */

function toast(msg, isErr) {
  toastEl.textContent = msg;
  toastEl.classList.toggle("err", !!isErr);
  toastEl.classList.remove("hidden");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => toastEl.classList.add("hidden"), 3500);
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
    throw Object.assign(new Error(String(detail)), { status: res.status, detail });
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
  "#/inventory": viewInventory, "#/profile": viewProfile, "#/admin": viewAdmin,
};

async function route() {
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
  const hash = location.hash || "#/world";
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
      location.hash = "#/world";
      route();
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
          location.hash = "#/world";
          route();
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
        /* no portrait yet — keep button */
      }
    }
    box.append(el("button", {
      onclick: () => generatePortrait(box, hasCanonical),
    }, hasCanonical ? "Сгенерировать заново" : "Сгенерировать портрет"));
  })();
  return box;
}

async function generateScene(container) {
  const status = container.querySelector(".scene-status");
  if (status) status.textContent = "Генерация сцены…";
  try {
    const r = await api("/visual/scenes", { method: "POST",
      body: { location_id: S.character.location_id } });
    const blob = await fetchPortraitBlob(`/visual/assets/${r.asset.id}/file`);
    const url = URL.createObjectURL(blob);
    const img = el("img", { class: "portrait-img", src: url, alt: "Сцена" });
    const old = container.querySelector(".portrait-img");
    if (old) old.remove();
    container.insertBefore(img, status);
    if (status) status.textContent = "";
  } catch (e) {
    if (status) status.textContent = String(e.detail || e.message || "Ошибка сцены");
  }
}

function sceneBlock() {
  const status = el("div", { class: "scene-status muted" });
  const box = el("div", { class: "panel portrait", id: "scene-view" }, el("h2", {}, "Сцена"), status);
  box.append(el("button", { onclick: () => generateScene(box) }, "Сгенерировать сцену"));
  return box;
}

/* ---------- topbar ---------- */

function weatherLabel() {
  if (!S.weather || !S.weather.enabled) return "";
  return "Погода: " + S.weather.description + (S.weather.source === "historical"
    ? " (реальная " + S.weather.real_date + ")" : "");
}

function topbar(active) {
  const tabs = [["#/world", "Мир"], ["#/chat", "Чат"], ["#/inventory", "Инвентарь"],
    ["#/profile", "Профиль"]];
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

async function viewWorld() {
  location.hash = "#/world";
  app.replaceChildren(topbar("#/world"), el("div", { class: "muted" }, "Загрузка…"));
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
  } catch (e) { toast(e.message, true); }
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
  const positioned = locs.filter(l => l.x !== null && l.y !== null && l.type !== "island");
  if (S.mapLod === "island") return positioned.filter(l => l.type !== "house");
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

  const locationAnchors = visibleLocs.map(l => {
    const isHere = l.id === ch.location_id;
    return el("button", {
      class: `anchor location-anchor ${isHere ? "here" : ""} ${l.y >= 20 ? "above" : ""}`,
      style: `left:${l.x}%; top:${l.y}%`,
      "aria-label": l.name,
      "data-loc-id": String(l.id),
      onclick: () => moveTo(l),
    },
      el("span", { class: "dot" }),
      (!/^House \d+$/.test(l.name) ? el("span", { class: "map-label" }, l.name) : null),
    );
  });

  const dynamicMarkers = [...mapNpcMarkers(visibleLocs), ...mapEventMarkers(visibleLocs)];
  const markerCount = locationAnchors.length + dynamicMarkers.length + (currentLoc ? 1 : 0);
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
      el("section", { class: "world-map-stage" },
        el("div", { class: "panel world-map-panel" },
          el("div", { class: "world-map-heading" },
            el("h2", {}, "Остров Рейнеке"),
            el("button", { type: "button", onclick: () => {
              const scene = document.getElementById("scene-view");
              if (scene) scene.scrollIntoView({ behavior: S.mapReducedMotion ? "auto" : "smooth", block: "start" });
            } }, "Осмотреть окрестности")),
          mapReady ? el("div", { id: "living-map-host" }, buildLivingMap(locs)) : el("div", { class: "locs" },
            ...locs.map((loc) => el("div", {
              class: `loc ${loc.id === ch.location_id ? "here" : ""}`,
              onclick: () => moveTo(loc),
            }, el("span", {}, loc.name || `#${loc.id}`),
               el("span", { class: "muted" }, loc.type || ""))))),
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
                "Персонаж действует сам — переключите режим в Профиле"),
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
  } catch (e) { toast(e.message, true); }
}

async function moveTo(loc) {
  if (loc.id === S.character.location_id) return;
  try {
    await api("/actions", { method: "POST", body: {
      character_id: S.character.id, action_type: "MOVE",
      params: { location_id: loc.id } } });
    toast(`Идём в ${loc.name || loc.id}`);
    viewWorld();
  } catch (e) {
    if (e.status === 422 && String(e.detail).includes("needs_move")) {
      toast("Сначала дойдите до промежуточной точки", true);
    } else { toast(e.message, true); }
  }
}

/* ---------- external travel (§38-39, M7) ---------- */

async function viewExternal() {
  const oldHash = location.hash;
  location.hash = "#/external";
  app.replaceChildren(topbar("#/world"), el("div", { class: "muted" }, "Загрузка…"));
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
              location.hash = "#/world";
              viewWorld();
            } catch (e) {
              err.textContent = String(e.detail);
              if (String(e.detail).includes("port")) toast("Сначала дойдите до порта", true);
            }
          } }, "Отправиться"),
          el("button", { onclick: () => { location.hash = oldHash; viewWorld(); } },
            "Назад"))));
  } catch (e) { toast(e.message, true); }
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
  S.pollTimer = setInterval(pollEvents, 5000);
  pollEvents();
}

function stopFeed() {
  if (S.ws) { try { S.ws.close(); } catch (e) {} S.ws = null; }
  if (S.pollTimer) { clearInterval(S.pollTimer); S.pollTimer = null; }
}

async function pollEvents() {
  try {
    const data = await api(`/world/events?since=${S.cursor}`);
    const events = Array.isArray(data) ? data : (data.events || []);
    if (events.length) {
      S.cursor = events[events.length - 1].id;
      S.feed.push(...events);
      const feedBox = document.getElementById("feed");
      if (feedBox) {
        feedBox.replaceChildren(el("h2", {}, "События"),
          ...S.feed.slice(-40).reverse().map(feedRow));
      }
    }
    
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
    // F1: LOD focus follows the authoritative player location on every poll refresh.
    if (character.location_id != null) S.mapFocusLocationId = character.location_id;
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
  } catch (e) { /* silent */ }
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
        S.feed.push(...data.events);
        const feedBox = document.getElementById("feed");
        if (feedBox) {
          feedBox.replaceChildren(el("h2", {}, "События"),
            ...S.feed.slice(-40).reverse().map(feedRow));
        }
      }
    } catch (e) { /* ignore malformed */ }
  };
  ws.onclose = () => {
    S.ws = null;
    S.wsTries += 1;
    if (S.wsTries <= 3) setTimeout(connectWs, 2000 * S.wsTries);
    /* иначе остаётся polling */
  };
  ws.onopen = () => { S.wsTries = 0; };
  S.ws = ws;
}

/* ---------- chat (§77) ---------- */

async function viewChat() {
  location.hash = "#/chat";
  app.replaceChildren(topbar("#/chat"), el("div", { class: "muted" }, "Загрузка…"));
  try {
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
    renderChat(null);
  } catch (e) { toast(e.message, true); }
}

async function renderChat(session) {
  const npcSel = el("select", {},
    ...S.chatNpcs.map((id) => el("option", { value: id },
      (S.chatNpcNames && S.chatNpcNames[id])
        ? `${S.chatNpcNames[id]} (${id})` : id)));
  
  // Relationship line in header
  let relLine = null;
  if (S.character) {
    try {
      const rels = await api(`/characters/${S.character.id}/relationships`);
      const rel = rels.find(r => r.other_id === npcSel.value);
      if (rel) {
        const affection = rel.affection;
        const label = affection > 20 ? "тёплые" : (affection < -20 ? "холодные" : "нейтральные");
        relLine = el("div", { class: "muted", style: "font-size:0.85em; margin-bottom:8px" },
          `Отношения: ${label} (симпатия ${Math.round(affection)}, доверие ${Math.round(rel.trust)})`);
      }
    } catch (e) { console.error("Rel fetch failed", e); }
  }

  const log = el("div", { class: "chatlog" });
  const input = el("input", { placeholder: "Сообщение…" });
  const sugg = el("div", {});
  if (session) {
    for (const m of session.messages || []) {
      log.append(el("div", { class: `chatmsg ${m.role === "npc" ? "npc" : ""}` },
        el("span", { class: "who" }, m.role === "npc" ? "NPC" : "Вы"),
        m.content));
    }
    for (const s of session.suggested_responses || []) {
      sugg.append(el("span", { class: "sugg", onclick: () => { input.value = s; send(); } }, s));
    }
  }
  async function send() {
    const content = input.value.trim();
    if (!content) return;
    input.value = "";
    try {
      const sid = session ? session.session_id
        : (await api("/dialogue/start", { method: "POST", body: { npc_id: npcSel.value } })).session_id;
      const r = await api(`/dialogue/${sid}/message`, { method: "POST", body: { content } });
      renderChat({ session_id: sid,
        messages: [...(session ? session.messages : []), 
          { role: "user", content },
          { role: "npc", content: r.npc_reply }],
        suggested_responses: r.suggested_responses || [] });
    } catch (e) { toast(e.message, true); }
  }
  const sendBtn = el("button", { class: "primary", onclick: () => send() }, "Отправить");
  input.addEventListener("keydown", (ev) => { if (ev.key === "Enter") send(); });
  app.replaceChildren(topbar("#/chat"),
    el("div", { class: "grid" },
      el("div", { class: "panel" },
        el("h2", {}, "Диалог"),
        el("label", {}, "NPC на этой локации"), npcSel,
        relLine,
        el("div", { style: "margin-top:8px" }, log),
        sugg,
        el("div", { style: "display:flex;gap:6px;margin-top:8px" }, input, sendBtn)),
      el("div", { class: "panel" }, el("h2", {}, "Подсказки"),
        el("div", { class: "muted" }, "Кликните подсказку, чтобы отправить её."))));
}

/* ---------- inventory (§77) ---------- */

async function viewInventory() {
  location.hash = "#/inventory";
  app.replaceChildren(topbar("#/inventory"), el("div", { class: "muted" }, "Загрузка…"));
  try {
    const inv = await api(`/characters/${S.character.id}/inventory`);
    const items = Array.isArray(inv) ? inv : (inv.items || []);
    const rows = items.map((it) => {
      const badge = (it.worn === true) 
        ? el("span", { class: "badge worn" }, `надето ${it.slot || ""}`) 
        : null;
      return el("tr", {},
        el("td", {}, it.object_type || it.type || "", badge),
        el("td", {}, it.quantity ?? ""),
        el("td", { class: "muted" }, `#${it.id}`));
    });
    app.replaceChildren(topbar("#/inventory"),
      el("div", { class: "panel" },
        el("h2", {}, "Инвентарь"),
        el("table", { class: "inv" },
          el("tr", {}, el("th", {}, "Предмет"), el("th", {}, "Кол-во"), el("th", {}, "ID")),
          rows.length ? rows : el("tr", {}, el("td", { colspan: "3", class: "muted" },
            "Пусто — купите что-нибудь во Владивостоке"))),
        el("p", { class: "muted" },
          "Использование предметов выполняется игровыми действиями (EAT/DRINK).")));
  } catch (e) { toast(e.message, true); }
}


/* ---------- profile (§77) ---------- */

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
  app.replaceChildren(topbar("#/profile"),
    el("div", { class: "grid" },
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
          } }, "Выйти"))),
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
          } }, "Применить")))));
}

/* ---------- admin (§83-86) ---------- */

async function viewAdmin() {
  location.hash = "#/admin";
  app.replaceChildren(topbar("#/admin"), el("div", { class: "muted" }, "Загрузка…"));
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
  } catch (e) { toast(e.message, true); }
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
