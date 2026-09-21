/* ВЛ1: Рейнеке — MVP web client (§77-78). Vanilla JS, без сборки.
   XSS-гигиена: рендер данных API только через textContent/createTextNode. */
"use strict";

const app = document.getElementById("app");
const toastEl = document.getElementById("toast");
const S = { user: null, character: null, world: null, ws: null, wsTries: 0,
  feed: [], cursor: 0, pollTimer: null, canonicalPortraitId: null };

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
  if (res.status === 401) { S.user = null; route(); throw new Error("unauthorized"); }
  let data = null;
  try { data = await res.json(); } catch (e) { data = null; }
  if (!res.ok) {
    const detail = data && data.detail ? data.detail : `HTTP ${res.status}`;
    throw Object.assign(new Error(String(detail)), { status: res.status, detail });
  }
  return data;
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "onclick") node.onclick = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const child of children) {
    if (child === null || child === undefined) continue;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
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
  if (!S.user) {
    try { S.user = await api("/auth/me"); } catch (e) { S.user = null; }
  }
  if (!S.user) return viewAuth();
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
  const mode = S.authMode === "register" ? "register" : "login";
  const isReg = mode === "register";
  const err = el("div", { class: "err-text" });
  const username = el("input", { autocomplete: "username" });
  const password = el("input", { type: "password", autocomplete: "current-password" });
  const email = el("input", { type: "email", placeholder: "you@example.com" });
  const age = el("input", { type: "checkbox", style: "width:auto" });
  const submit = el("button", {
    class: "primary", onclick: async () => {
      err.textContent = "";
      try {
        if (isReg) {
          if (!age.checked) { err.textContent = "Подтвердите 18+"; return; }
          await api("/auth/register", { method: "POST", body: {
            username: username.value, email: email.value,
            password: password.value, age_confirmed: true } });
        }
        await api("/auth/login", { method: "POST", body: {
          username: username.value, password: password.value } });
        S.user = await api("/auth/me");
        S.character = null;
        location.hash = "#/world";
        route();
      } catch (e) { err.textContent = e.message; }
    },
  }, isReg ? "Создать аккаунт" : "Войти");
  app.replaceChildren(el("div", { class: "authbox panel" },
    el("h1", {}, "ВЛ1: Рейнеке"),
    el("p", { class: "muted" }, "Симулятор жизни на острове Рейнеке. 18+"),
    el("label", {}, "Имя пользователя"), username,
    isReg && el("label", {}, "Email"), isReg && email,
    el("label", {}, "Пароль"), password,
    isReg && el("label", { style: "display:flex;gap:6px;align-items:center" },
      age, "Мне 18 лет и больше"),
    el("div", { style: "margin-top:10px" }, submit),
    err,
    el("p", {}, el("a", {
      href: "#", onclick: (ev) => {
        ev.preventDefault();
        S.authMode = isReg ? "login" : "register";
        viewAuth();
      },
    }, isReg ? "У меня уже есть аккаунт" : "Регистрация")),
  ));
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
    try {
      const asset = await api(`/visual/characters/${S.character.id}/portrait`);
      S.canonicalPortraitId = asset.id;
      const blob = await fetchPortraitBlob(`/visual/assets/${asset.id}/file`);
      const url = URL.createObjectURL(blob);
      box.insertBefore(
        el("img", { class: "portrait-img", src: url, alt: "Портрет" }), status);
      hasCanonical = true;
    } catch { /* no portrait yet — keep button */ }
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
  const box = el("div", { class: "panel portrait" }, el("h2", {}, "Сцена"), status);
  box.append(el("button", { onclick: () => generateScene(box) }, "Сгенерировать сцену"));
  return box;
}

/* ---------- topbar ---------- */

function topbar(active) {
  const tabs = [["#/world", "Мир"], ["#/chat", "Чат"], ["#/inventory", "Инвентарь"],
    ["#/profile", "Профиль"]];
  if (["admin", "developer", "moderator"].includes(S.user.role)) {
    tabs.push(["#/admin", "Админ"]);
  }
  const weatherSpan = S.weather && S.weather.enabled
    ? el("span", { id: "weather", class: "muted" },
        "Погода: " + S.weather.description + (S.weather.source === "historical"
          ? " (реальная " + S.weather.real_date + ")" : ""))
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
    renderWorld(locs);
    startFeed();
  } catch (e) { toast(e.message, true); }
}

function needBar(label, value) {
  const v = Math.max(0, Math.min(100, Math.round(value)));
  return el("div", { class: `need ${v < 30 ? "low" : ""}` },
    el("div", { class: "row" }, el("span", {}, label), el("span", {}, `${v}`)),
    el("div", { class: "bar" }, el("div", { style: `width:${v}%` })));
}

function renderWorld(locs) {
  const ch = S.character;
  const needs = ch.needs || {};
  const feed = el("div", { class: "panel feed", id: "feed" },
    el("h2", {}, "События"),
    ...S.feed.slice(-40).reverse().map(feedRow));
  const actions = el("div", { class: "actions" },
    ...["WORK", "EAT", "DRINK", "SLEEP", "SOCIALIZE"].map((a) =>
      el("button", { onclick: () => doAction(a) }, a)));
  const extBtn = el("button", { onclick: () => viewExternal() },
    "Поездка во Владивосток");
  const tasks = (ch.tasks || []).map((t) => el("div", { class: "task" },
    el("span", { class: "status" }, t.status), el("span", {}, t.action_type || t.task_type || "")));
  const portraitBox = portraitBlock();
  const sceneBox = sceneBlock();
  app.replaceChildren(
    topbar("#/world"),
    el("div", { class: "grid" },
      el("div", {},
        el("div", { class: "panel" },
          el("h2", {}, "Карта"),
          el("div", { class: "locs" },
            ...locs.map((loc) => el("div", {
              class: `loc ${loc.id === ch.location_id ? "here" : ""}`,
              onclick: () => moveTo(loc),
            }, el("span", {}, loc.name || `#${loc.id}`),
               el("span", { class: "muted" }, loc.type || ""))))),
        el("div", { class: "panel" },
          el("h2", {}, "Персонаж"),
          el("div", {}, `${ch.name || `${ch.first_name || ""} ${ch.last_name || ""}`}`),
          el("div", { class: "muted" }, `Деньги: ${ch.money ?? ch.balance ?? "—"} ₽`),
          needBar("Сытость", needs.hunger ?? 100),
          needBar("Вода", needs.thirst ?? 100),
          needBar("Энергия", needs.energy ?? 100),
          needBar("Общение", needs.social ?? 100),
          actions,
          el("div", { style: "margin-top:6px" }, extBtn))),
      el("div", {},
        el("div", { class: "panel" },
          el("h2", {}, "Задачи"),
          tasks.length ? tasks : el("div", { class: "muted" }, "Нет активных задач")),
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
  } catch (e) { /* silent */ }
}

function connectWs() {
  if (!S.user || S.ws) return;
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws?token=${S.user.ws_token}`);
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

function renderChat(session) {
  const npcSel = el("select", {},
    ...S.chatNpcs.map((id) => el("option", { value: id },
      (S.chatNpcNames && S.chatNpcNames[id])
        ? `${S.chatNpcNames[id]} (${id})` : id)));
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
    const rows = items.map((it) => el("tr", {},
      el("td", {}, it.object_type || it.type || ""),
      el("td", {}, it.quantity ?? ""),
      el("td", { class: "muted" }, `#${it.id}`)));
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
