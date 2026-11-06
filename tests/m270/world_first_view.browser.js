// tests/m270/world_first_view.browser.js
//
// MANUAL browser-acceptance regression for the world-first-view wave (022).
// This is a Playwright `async (page) => {}` function run by the PARENT via the
// native browser MCP / run_code — it is NOT collected by pytest and is NOT run
// by CI (scripts/check.sh = ruff + pytest only). It is the manual-acceptance
// truth for REAL rendered geometry + onboarding states, split from the CI
// pytest truth (tests/m270/test_world_first_view.py).
//
// LOCAL CANDIDATE BYTES (never PROD): the parent starts a stdlib static server
//   python3 -m http.server 8771 --bind 127.0.0.1 --directory backend/app/web
// and this function pins BASE to that local origin. The real candidate
// index.html / app.js / style.css / map / photos are served via GET only. The
// app references /static/app.js, /static/style.css and /static/static/... ;
// the local web root is backend/app/web, so the FIRST /static prefix is
// stripped before the transparent fetch (e.g. /static/static/map -> /static/map).
// The report returns the SHA256 of the served app.js / style.css (browser
// WebCrypto crypto.subtle, no deps — the native MCP VM has no Node dynamic-
// import callback) so the parent can compare to the candidate files and prove
// the test ran against the candidate bytes, not PROD.
//
// FAIL-CLOSED CONTRACT (same discipline as the #59 audit helper):
// - Static: ONLY the local BASE origin + canonical static paths, GET/HEAD,
//   transparently fulfilled from the local server (first /static stripped).
//   Every other request (any origin, any non-canonical path, any non-GET/HEAD)
//   is recorded + aborted.
// - API: every known API path is served by a GET-only fixture; a non-GET to a
//   known path, and ANY request (incl. GET) to an unknown path, is recorded +
//   aborted. No request ever reaches a real backend.
// - WebSocket: PRE-CONNECT interception via page.routeWebSocket, installed
//   BEFORE navigation. Every WS the app opens is closed WITHOUT
//   connectToServer(). If page.routeWebSocket is unavailable we THROW before
//   any navigation (no post-connect fallback).
// - No real accounts/credentials/production API calls. Only hash navigation +
//   local UI welcome interactions (keyboard focus+Enter on the welcome
//   summary/dismiss) are allowed — no gameplay button clicks.
//
// STORAGE INSTRUMENTATION: a single addInitScript instruments ONLY the
// onboarding key 'vl1.welcome.done' on window.localStorage (per-document, so no
// accumulated hooks). It reads the ?m270=<mode> query to drive the case,
// seeds/resets the key before app boot, and counts/throws reads+writes for that
// key only (settings keys stay normal). Modes: fresh | persisted | denied-read
// | denied-write. Native fault injection is test-only and never affects prod.
//
// SCREENSHOTS: written ONLY under /tmp/opencode/playwright-dumps/lifesim-022/
// (parent mkdirs it), filenames distinct per case + viewport.

async (page) => {
  const BASE = "http://127.0.0.1:8771";
  const OUT = "/tmp/opencode/playwright-dumps/lifesim-022";
  const KEY = "vl1.welcome.done";
  const CLOCK = "День 2, 01:00";

  // ---- fixtures (shapes copied from backend/app/api/app.py + visual_routes.py) ----
  const USER = { id: 1, username: "audit", email: "audit@fixture.local", role: "player" };
  const BYUSER = [{ id: 1, name: "P", alive: true, control_mode: "MANUAL" }];
  const CHAR1 = {
    id: 1, name: "P", sex: "m", age: 30, alive: true, location_id: 1,
    control_mode: "MANUAL", is_owner: true, balance: 100,
    needs: { hunger: 70, energy: 80, thirst: 60, social: 50 }, job: "Рыбак",
    looks: null, biography: null, outfit_mode: "auto", home_location_id: 1,
    traits: [], health: { health: 100, stress: 10 },
  };
  // game_timestamp 1500 -> clock renders "День 2, 01:00" (display = day + 1).
  // day is the API's 0-indexed value (1500 // 1440 = 1); constant across polls.
  const WORLD = { id: 1, seed: 42, game_timestamp: 1500, day: 1, population: 12, time_scale: 1.0, is_paused: false };
  const WEATHER = {
    enabled: true, day: 1, temperature: 18, wind: 3, precipitation: 0.1,
    cloudiness: 0.3, visibility: 1.0, source: "generated",
    real_date: "2026-11-06", description: "+18°C, ясно, ветер 3 м/с",
  };
  // All 7 keyed POIs with coordinates so the living map (mapReady) renders.
  const LOCATIONS = [
    { id: 1, type: "settlement", name: "Посёлок", x: 45, y: 50, parent_id: null, occupants_count: 2,
      occupants: [{ id: 1, name: "P", kind: "player" }, { id: 2, name: "NPC One", kind: "npc" }] },
    { id: 2, type: "shop", name: "Магазин", x: 50, y: 45, parent_id: 1, occupants_count: 0, occupants: [] },
    { id: 3, type: "workshop", name: "Мастерская", x: 42, y: 56, parent_id: 1, occupants_count: 0, occupants: [] },
    { id: 4, type: "kitchen", name: "Кухня", x: 47, y: 51, parent_id: 1, occupants_count: 0, occupants: [] },
    { id: 5, type: "storage", name: "Склад", x: 40, y: 48, parent_id: 1, occupants_count: 0, occupants: [] },
    { id: 6, type: "well", name: "Колодец", x: 48, y: 55, parent_id: 1, occupants_count: 0, occupants: [] },
    { id: 7, type: "pier", name: "Причал", x: 46, y: 91, parent_id: null, occupants_count: 0, occupants: [] },
    { id: 8, type: "house", name: "House 1", x: 44, y: 47, parent_id: 1, occupants_count: 0, occupants: [] },
  ];
  const LOC1_DETAIL = { id: 1, type: "settlement", name: "Посёлок", characters_here: [1, 2] };
  // events GET fixture kept (the world poll fetches /world/events); place/events
  // journey is NOT part of this wave.
  const EVENTS = [
    { id: 1, event_type: "CHARACTER_MOVED", actor_id: 2, location_id: 1, day: 1, game_timestamp: 1440, payload: { from_location: 2, to_location: 1 } },
    { id: 2, event_type: "TASK_COMPLETED", actor_id: 1, location_id: 1, day: 1, game_timestamp: 1450, payload: { task_type: "WORK" } },
    { id: 3, event_type: "POWER_OUTAGE", actor_id: null, location_id: 2, day: 1, game_timestamp: 1460, payload: { location_id: 2 } },
    { id: 4, event_type: "ITEM_CONSUMED", actor_id: 1, location_id: 1, day: 1, game_timestamp: 1470, payload: { item: "fish" } },
  ];
  const PORTRAIT_404 = { status: 404, body: { detail: "canonical portrait not found" } };
  const REFS_EMPTY = { items: [] };
  const TASKS_EMPTY = { active: [], planned: [], recent_terminal: [] };

  // ---- collectors ----
  const unexpectedAPI = [];
  const pageErrors = [];
  const wsBlocks = [];

  // ---- setup ----
  async function setup() {
    await page.goto("about:blank", { waitUntil: "load" });

    // Pre-connect WS interception MUST be available; otherwise refuse to
    // navigate (no post-connect fallback).
    if (typeof page.routeWebSocket !== "function") {
      throw new Error("page.routeWebSocket unavailable — cannot guarantee pre-connect WS interception; aborting before navigation");
    }

    await page.unrouteAll();

    // Fallback route (installed FIRST -> lowest precedence). ONLY the local BASE
    // origin + canonical static paths, GET/HEAD, transparently fulfilled from
    // the local server (first /static prefix stripped). Everything else is
    // recorded + aborted so nothing reaches a real backend.
    await page.route("**/*", async (route) => {
      const req = route.request();
      let u;
      try { u = new URL(req.url()); }
      catch (e) { unexpectedAPI.push({ url: req.url(), method: req.method(), reason: "unparseable" }); return route.abort(); }
      const isBase = u.origin === BASE;
      const isCanonicalStatic = u.pathname === "/" || u.pathname === "/index.html"
        || u.pathname.startsWith("/static/") || u.pathname === "/app.js" || u.pathname === "/style.css";
      if (isBase && isCanonicalStatic && (req.method() === "GET" || req.method() === "HEAD")) {
        let stripped = u.pathname;
        if (stripped.startsWith("/static/")) stripped = stripped.slice("/static".length);
        const resp = await page.request.get(BASE + stripped);
        const body = await resp.body();
        const ct = (resp.headers() || {})["content-type"] || "application/octet-stream";
        return route.fulfill({ status: resp.status(), contentType: ct, body });
      }
      unexpectedAPI.push({ url: u.href, method: req.method(), reason: isBase ? "non-static-or-non-GET" : "foreign-origin" });
      return route.abort();
    });

    // Specific API routes (installed LAST -> highest precedence). GET-only.
    const json = (route, s) => route.fulfill({ status: s.status, contentType: "application/json; charset=utf-8", body: JSON.stringify(s.body) });
    const ok = (body) => ({ status: 200, body });
    const getOnly = (s) => (route) => {
      if (route.request().method() === "GET") return json(route, s);
      unexpectedAPI.push({ url: route.request().url(), method: route.request().method(), reason: "non-GET-to-known-api" });
      return route.abort();
    };
    await page.route("**/auth/me", getOnly(ok(USER)));
    await page.route("**/characters/by-user/1", getOnly(ok(BYUSER)));
    await page.route("**/characters/1", getOnly(ok(CHAR1)));
    await page.route("**/characters/1/tasks", getOnly(ok(TASKS_EMPTY)));
    await page.route("**/world", getOnly(ok(WORLD)));
    await page.route("**/weather", getOnly(ok(WEATHER)));
    await page.route("**/locations", getOnly(ok(LOCATIONS)));
    await page.route("**/locations/*", getOnly(ok(LOC1_DETAIL)));
    await page.route("**/world/events*", getOnly(ok(EVENTS)));
    await page.route("**/visual/characters/1/portrait", getOnly(PORTRAIT_404));
    await page.route("**/visual/references", getOnly(ok(REFS_EMPTY)));

    // Pre-connect WebSocket interception: close WITHOUT connectToServer().
    await page.routeWebSocket("**/*", (wsRoute) => {
      let url = null;
      try { url = wsRoute.url(); } catch (e) { url = "<unavailable>"; }
      wsBlocks.push({ url });
      try { wsRoute.close(); } catch (e) { /* already closed */ }
    });

    page.on("pageerror", (err) => { pageErrors.push(String((err && err.message) || err)); });

    // Storage instrumentation: ONE init script, per-document, onboarding key only.
    await page.addInitScript((key) => {
      const mode = (new URLSearchParams(location.search).get("m270")) || "fresh";
      const counts = { reads: 0, writes: 0 };
      window.__m270 = { mode, counts };
      const ls = window.localStorage;
      const origGet = ls.getItem.bind(ls);
      const origSet = ls.setItem.bind(ls);
      const origRemove = ls.removeItem.bind(ls);
      try {
        if (mode === "persisted") origSet(key, "1");
        else origRemove(key);
      } catch (e) { /* ignore seed errors */ }
      ls.getItem = function (k) {
        if (k === key) {
          counts.reads++;
          if (mode === "denied-read") throw new Error("m270 denied read");
        }
        return origGet(k);
      };
      ls.setItem = function (k, v) {
        if (k === key) {
          counts.writes++;
          if (mode === "denied-write") throw new Error("m270 denied write");
        }
        return origSet(k, v);
      };
    }, KEY);
  }

  // ---- helpers ----
  async function loadWorld(mode) {
    await page.goto(BASE + "/?m270=" + mode + "#/world", { waitUntil: "load" });
    await page.waitForFunction((clock) => {
      const c = document.getElementById("clock");
      return location.hash === "#/world"
        && !!document.querySelector(".world-shell")
        && !!document.querySelector(".map-card")
        && !!c && c.textContent === clock;
    }, CLOCK, { timeout: 8000 });
    await page.waitForFunction(() => {
      const img = document.querySelector(".map-base");
      return !!img && img.complete && img.naturalWidth > 0;
    }, undefined, { timeout: 8000 });
  }
  async function shell() {
    return await page.evaluate(() => {
      const tabs = document.querySelectorAll(".tabs button");
      const clock = document.getElementById("clock");
      return {
        tabCount: tabs.length,
        clockText: clock ? clock.textContent : null,
        overflow: document.documentElement.scrollWidth > window.innerWidth,
      };
    });
  }
  async function mapVisiblePx() {
    return await page.evaluate(() => {
      const card = document.querySelector(".map-card");
      if (!card) return null;
      const r = card.getBoundingClientRect();
      return Math.max(0, Math.min(r.bottom, window.innerHeight) - Math.max(r.top, 0));
    });
  }
  async function welcomeState() {
    return await page.evaluate(() => {
      const card = document.querySelector(".welcome-card");
      if (!card) return { present: false, hasDetails: false, open: false };
      const details = card.querySelector("details");
      return { present: true, hasDetails: !!details, open: details ? details.open : true };
    });
  }
  async function counts() {
    return await page.evaluate(() => (window.__m270 && window.__m270.counts) || { reads: 0, writes: 0 });
  }
  async function shot(name) {
    const path = OUT + "/" + name;
    await page.screenshot({ path, fullPage: true });
    return path;
  }

  // ---- cases (each a fresh document via loadWorld) ----
  async function case1Geometry(label) {
    await loadWorld("fresh");
    const sh = await shell();
    const disc = await welcomeState();
    const px = await mapVisiblePx();
    const geometryTarget = label === "390" ? (px !== null && px >= 160) : null;
    const defaultClosed = disc.hasDetails ? !disc.open : null; // null = missing disclosure (BASE)
    const path = await shot("case1-geometry-" + label + ".png");
    return {
      viewport: label, shell: sh, mapVisiblePx: px, disclosure: disc,
      geometryTarget, defaultClosed, missingDisclosure: !disc.hasDetails,
      pass: sh.tabCount === 9 && !sh.overflow
        && (label === "390" ? geometryTarget : true)
        && (disc.hasDetails ? !disc.open : false),
      screenshot: path,
    };
  }
  async function case2Summary(label) {
    await loadWorld("fresh");
    const sh = await shell();
    const disc = await welcomeState();
    if (!disc.hasDetails) {
      const path = await shot("case2-summary-" + label + ".png");
      return { viewport: label, shell: sh, missingDisclosure: true, pass: false, screenshot: path };
    }
    const summary = page.locator(".welcome-card summary");
    await summary.focus();
    await page.keyboard.press("Enter");
    await page.waitForFunction(() => { const d = document.querySelector(".welcome-card details"); return d && d.open; }, undefined, { timeout: 4000 });
    // F1: a native closed <details> can lazily load its photos only after
    // opening — wait for both to be decoded before asserting (avoids false RED).
    await page.waitForFunction(() => {
      const imgs = [...document.querySelectorAll(".welcome-card .welcome-photos img")];
      return imgs.length === 2 && imgs.every((i) => i.complete && i.naturalWidth > 0);
    }, undefined, { timeout: 8000 });
    const openState = await page.evaluate(() => {
      const card = document.querySelector(".welcome-card");
      const photos = [...card.querySelectorAll(".welcome-photos img")];
      const tips = [...card.querySelectorAll(".welcome-tip")];
      const texts = [...card.querySelectorAll("h2, p")];
      return {
        photoCount: photos.length,
        photosLoaded: photos.every((i) => i.complete && i.naturalWidth > 0),
        photosVisible: photos.every((i) => i.offsetParent !== null),
        tipCount: tips.length, tipsVisible: tips.every((t) => t.offsetParent !== null),
        textsVisible: texts.every((t) => t.offsetParent !== null),
      };
    });
    await page.keyboard.press("Enter");
    await page.waitForFunction(() => { const d = document.querySelector(".welcome-card details"); return d && !d.open; }, undefined, { timeout: 4000 });
    const closedHidden = await page.evaluate(() => {
      const photos = [...document.querySelectorAll(".welcome-card .welcome-photos img")];
      return photos.length > 0 && photos.every((i) => i.offsetParent === null);
    });
    const c = await counts();
    const path = await shot("case2-summary-" + label + ".png");
    return {
      viewport: label, shell: sh, missingDisclosure: false, openState, closedHidden, storageWrites: c.writes,
      pass: sh.tabCount === 9 && !sh.overflow
        && openState.photoCount === 2 && openState.photosLoaded && openState.photosVisible
        && openState.tipCount === 3 && openState.tipsVisible && openState.textsVisible
        && closedHidden && c.writes === 0,
      screenshot: path,
    };
  }
  async function case3Dismiss(label) {
    await loadWorld("fresh");
    const sh = await shell();
    const btn = page.locator(".welcome-card button");
    const btnCount = await btn.count();
    const btnText = btnCount > 0 ? (await btn.first().textContent() || "").trim() : null;
    await btn.first().focus();
    await page.keyboard.press("Enter");
    await page.waitForFunction(() => !document.querySelector(".welcome-card"), undefined, { timeout: 4000 });
    const cardAbsent = await page.evaluate(() => !document.querySelector(".welcome-card"));
    await page.evaluate(() => { location.hash = "#/location/1"; });
    // F3: wait for the REAL place route (not a fixed sleep) so the away/back
    // round-trip is a genuine navigation, not a no-op.
    await page.waitForFunction(() => location.hash === "#/location/1" && !!document.querySelector(".place-shell"), undefined, { timeout: 4000 });
    await page.evaluate(() => { location.hash = "#/world"; });
    await page.waitForFunction(() => !!document.querySelector(".world-shell"), undefined, { timeout: 4000 });
    const cardStillAbsent = await page.evaluate(() => !document.querySelector(".welcome-card"));
    const c = await counts();
    const path = await shot("case3-dismiss-" + label + ".png");
    return {
      viewport: label, shell: sh, btnCount, btnText, cardAbsent, cardStillAbsent, counts: c,
      pass: sh.tabCount === 9 && !sh.overflow && btnCount === 1 && btnText === "Я освоился"
        && cardAbsent && cardStillAbsent && c.reads >= 1 && c.writes === 1,
      screenshot: path,
    };
  }
  async function case4Persisted(label) {
    await loadWorld("persisted");
    const sh = await shell();
    const cardPresent = await page.evaluate(() => !!document.querySelector(".welcome-card"));
    const c = await counts();
    const path = await shot("case4-persisted-" + label + ".png");
    return {
      viewport: label, shell: sh, cardPresent, counts: c,
      pass: sh.tabCount === 9 && !sh.overflow && !cardPresent && c.reads >= 1 && c.writes === 0,
      screenshot: path,
    };
  }
  async function case5DeniedRead(label) {
    await loadWorld("denied-read");
    const sh = await shell();
    const cardPresent = await page.evaluate(() => !!document.querySelector(".welcome-card"));
    const c = await counts();
    const path = await shot("case5-denied-read-" + label + ".png");
    return {
      viewport: label, shell: sh, cardPresent, counts: c,
      pass: sh.tabCount === 9 && !sh.overflow && !cardPresent && c.reads >= 1 && c.writes === 0,
      screenshot: path,
    };
  }
  async function case6DeniedWrite(label) {
    await loadWorld("denied-write");
    const sh = await shell();
    const initialCardPresent = await page.evaluate(() => !!document.querySelector(".welcome-card"));
    const btn = page.locator(".welcome-card button");
    const btnCount = await btn.count();
    await btn.first().focus();
    await page.keyboard.press("Enter");
    await page.waitForFunction(() => !document.querySelector(".welcome-card"), undefined, { timeout: 4000 });
    const cardAbsentAfterDismiss = await page.evaluate(() => !document.querySelector(".welcome-card"));
    await page.evaluate(() => { location.hash = "#/location/1"; });
    // F3: wait for the REAL place route (not a fixed sleep) so the away/back
    // round-trip is a genuine navigation, not a no-op.
    await page.waitForFunction(() => location.hash === "#/location/1" && !!document.querySelector(".place-shell"), undefined, { timeout: 4000 });
    await page.evaluate(() => { location.hash = "#/world"; });
    await page.waitForFunction(() => !!document.querySelector(".world-shell"), undefined, { timeout: 4000 });
    const cardStillAbsent = await page.evaluate(() => !document.querySelector(".welcome-card"));
    const c = await counts();
    const path = await shot("case6-denied-write-" + label + ".png");
    return {
      viewport: label, shell: sh, initialCardPresent, btnCount, cardAbsentAfterDismiss, cardStillAbsent, counts: c,
      pass: sh.tabCount === 9 && !sh.overflow && initialCardPresent && btnCount === 1
        && cardAbsentAfterDismiss && cardStillAbsent && c.reads >= 1 && c.writes === 1,
      screenshot: path,
    };
  }

  // ---- run ----
  async function run() {
    const report = {
      base: BASE, staticSha: null, cases: {}, screenshots: [],
      unexpectedAPI, pageErrors, wsBlocks, allPass: false,
    };
    // Source-binding SHA: hash the actual same-origin candidate app.js/style.css
    // bytes via browser WebCrypto (the native MCP VM has no Node dynamic-import
    // callback, so Node crypto is unavailable here). Load the world once to get
    // a secure localhost origin, then fetch the statics through the existing
    // strict fallback (GET, real candidate binary fulfill).
    await loadWorld("fresh");
    report.staticSha = await page.evaluate(async () => {
      if (!window.crypto || !window.crypto.subtle) {
        throw new Error("SETUP: WebCrypto (crypto.subtle) unavailable — cannot hash candidate bytes");
      }
      const digest = async (path) => {
        const response = await fetch(path);
        if (!response.ok) throw new Error("SETUP: candidate static fetch failed: " + path + " -> " + response.status);
        const bytes = await response.arrayBuffer();
        const hash = await crypto.subtle.digest("SHA-256", bytes);
        return [...new Uint8Array(hash)].map((b) => b.toString(16).padStart(2, "0")).join("");
      };
      return { appJs: await digest("/app.js"), styleCss: await digest("/style.css") };
    });

    const REQUIRED = 12; // 6 cases x 2 viewports
    for (const [w, h, label] of [[390, 844, "390"], [1440, 900, "1440"]]) {
      await page.setViewportSize({ width: w, height: h });
      report.cases["case1-geometry-" + label] = await case1Geometry(label);
      report.cases["case2-summary-" + label] = await case2Summary(label);
      report.cases["case3-dismiss-" + label] = await case3Dismiss(label);
      report.cases["case4-persisted-" + label] = await case4Persisted(label);
      report.cases["case5-denied-read-" + label] = await case5DeniedRead(label);
      report.cases["case6-denied-write-" + label] = await case6DeniedWrite(label);
    }
    report.screenshots = Object.values(report.cases).map((c) => c.screenshot);
    const names = Object.keys(report.cases);
    report.allPass = names.length === REQUIRED
      && names.every((n) => report.cases[n].pass)
      && unexpectedAPI.length === 0
      && pageErrors.length === 0;
    return report;
  }

  await setup();
  return await run();
}
