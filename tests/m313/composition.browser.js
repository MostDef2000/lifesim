// tests/m313/composition.browser.js
//
// MANUAL browser-acceptance regression for the #313 composition wave.
// This is a Playwright `async (page) => {}` function run by the PARENT via the
// native browser MCP / run_code — it is NOT collected by pytest and is NOT run
// by CI (scripts/check.sh = ruff + pytest only). It is the manual-acceptance
// truth for REAL rendered geometry + viewport states, split from the CI
// pytest truth (tests/m313/test_m313_composition.py).
//
// LOCAL CANDIDATE BYTES (never PROD): the parent starts a stdlib static server
//   python3 -m http.server 8772 --bind 127.0.0.1 --directory backend/app/web
// and this function pins BASE to that local origin (same fail-closed contract
// as tests/m270/world_first_view.browser.js: only the local BASE origin +
// canonical static paths are served through; every known API path is a
// GET-only fixture (plus the two scene-lifecycle routes this wave drives);
// every WebSocket is closed pre-connect; nothing reaches a real backend).
// The report returns the SHA256 of the served app.js / style.css (browser
// WebCrypto) so the parent can prove the test ran against the candidate
// bytes, not PROD.
//
// GEOMETRY EVIDENCE (the split this file owns):
//   caseHeader      — closed contextual header (.ctx-topbar) height <= 60px
//                     at 390 (target 56); recorded at 320/1440.
//   caseStageStates — .place-stage height as % of innerHeight within [32, 40]
//                     on the fallback / error / loading scene fixtures at 390;
//                     on ready content only a >= 32 floor is asserted —
//                     content expansion beyond 40 is ALLOWED (target-not-cap).
//   caseChat        — dialogue panel #chat-view width within [420, 500] at
//                     1440 (target 460; the .chat-shell grid width rides along
//                     as context only, never a pass/fail input);
//                     .chat-backdrop computed background
//                     alpha > 0 (decorative dim scrim); composer (.chat-bar)
//                     bottom edge <= innerHeight at 390.
//   caseZoom        — 200% root font: contextual header + composer GROW
//                     (rem/inherited), World nav pill + map card UNCHANGED
//                     (px canon).
// Viewports: 390x844, 320x700, 1440x900. caseStageStates runs at 390 only
// (the composition-critical viewport; the 36dvh band is viewport-relative
// by construction).
//
// SHARED-TAB HIJACK GUARD: the browser MCP tab is shared — before EVERY
// measurement the in-page probe re-checks window.innerWidth (the viewport we
// set) and location.href (still our local BASE origin). Any drift throws
// immediately instead of reporting numbers from a hijacked tab.
//
// FAIL-CLOSED EXIT: every check prints a "PASS: <name> — <observed>" or
// "FAIL: ..." line (mirrored into report.checks). On any FAIL (or unexpected
// API/page error) the function THROWS with all FAIL lines + observed values —
// the nonzero-exit equivalent for the MCP runner; a standalone node harness
// wrapping this function must catch and process.exit(1). On all-pass it
// returns the full report (allPass: true).
//
// SCREENSHOTS: written ONLY under /tmp/opencode/playwright-dumps/lifesim-313/
// (Playwright creates the directory), filenames distinct per case + viewport.

async (page) => {
  const BASE = "http://127.0.0.1:8772";
  const OUT = "/tmp/opencode/playwright-dumps/lifesim-313";
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
  const WORLD = { id: 1, seed: 42, game_timestamp: 1500, day: 1, population: 12, time_scale: 1.0, is_paused: false };
  const WEATHER = {
    enabled: true, day: 1, temperature: 18, wind: 3, precipitation: 0.1,
    cloudiness: 0.3, visibility: 1.0, source: "generated",
    real_date: "2026-11-06", description: "+18°C, ясно, ветер 3 м/с",
  };
  const LOCATIONS = [
    { id: 1, type: "settlement", name: "Посёлок", x: 45, y: 50, parent_id: null, occupants_count: 2,
      occupants: [{ id: 1, name: "P", kind: "player" }, { id: 2, name: "Фар", kind: "npc" }] },
    { id: 2, type: "shop", name: "Магазин", x: 50, y: 45, parent_id: 1, occupants_count: 0, occupants: [] },
    { id: 3, type: "workshop", name: "Мастерская", x: 42, y: 56, parent_id: 1, occupants_count: 0, occupants: [] },
    { id: 4, type: "kitchen", name: "Кухня", x: 47, y: 51, parent_id: 1, occupants_count: 0, occupants: [] },
    { id: 5, type: "storage", name: "Склад", x: 40, y: 48, parent_id: 1, occupants_count: 0, occupants: [] },
    { id: 6, type: "well", name: "Колодец", x: 48, y: 55, parent_id: 1, occupants_count: 0, occupants: [] },
    { id: 7, type: "pier", name: "Причал", x: 46, y: 91, parent_id: null, occupants_count: 0, occupants: [] },
    { id: 8, type: "house", name: "House 1", x: 44, y: 47, parent_id: 1, occupants_count: 0, occupants: [] },
  ];
  const LOC1_DETAIL = { id: 1, type: "settlement", name: "Посёлок", characters_here: [1, 2] };
  const EVENTS = [
    { id: 1, event_type: "CHARACTER_MOVED", actor_id: 2, location_id: 1, day: 1, game_timestamp: 1440, payload: { from_location: 2, to_location: 1 } },
    { id: 2, event_type: "TASK_COMPLETED", actor_id: 1, location_id: 1, day: 1, game_timestamp: 1450, payload: { task_type: "WORK" } },
  ];
  const PORTRAIT_404 = { status: 404, body: { detail: "canonical portrait not found" } };
  const REFS_EMPTY = { items: [] };
  const TASKS_EMPTY = { active: [], planned: [], recent_terminal: [] };
  // Valid scene asset for the READY state — must pass the app's validation
  // (asset_type "scene", location_id matching the click-captured place,
  // scene_descriptor.location.id matching): app.js inspectSurroundings.
  const SCENE_ASSET = {
    asset: {
      id: "sc1", asset_type: "scene", location_id: 1,
      scene_descriptor: { location: { id: 1, name: "Посёлок" } },
    },
  };

  // ---- collectors ----
  const unexpectedAPI = [];
  const pageErrors = [];
  const wsBlocks = [];
  const checkLog = []; // every PASS/FAIL line, mirrored into the report
  let scenePostMode = "error"; // "error" -> 500 | "ready" -> delayed valid asset

  // ---- check helper (prints + records; every check carries observed values) ----
  function check(name, pass, detail) {
    const line = (pass ? "PASS" : "FAIL") + ": " + name + " — " + detail;
    checkLog.push({ name, pass: !!pass, detail });
    console.log(line);
    return !!pass;
  }

  // ---- setup (fail-closed, same discipline as m270) ----
  async function setup() {
    await page.goto("about:blank", { waitUntil: "load" });
    if (typeof page.routeWebSocket !== "function") {
      throw new Error("page.routeWebSocket unavailable — cannot guarantee pre-connect WS interception; aborting before navigation");
    }
    await page.unrouteAll();

    // Fallback route (lowest precedence): ONLY local BASE + canonical static,
    // GET/HEAD, first /static prefix stripped. Everything else recorded+aborted.
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

    // Specific API routes (highest precedence). GET-only except the two
    // scene-lifecycle routes this wave drives.
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

    // Scene lifecycle: POST /visual/scenes per scenePostMode ("error" -> 500,
    // "ready" -> held ~3s so the loading state is measurable, then a valid
    // asset); the asset file GET is fulfilled from the LOCAL candidate map
    // image (real bytes).
    await page.route("**/visual/scenes", async (route) => {
      const req = route.request();
      if (req.method() !== "POST") {
        unexpectedAPI.push({ url: req.url(), method: req.method(), reason: "non-POST-to-scenes" });
        return route.abort();
      }
      if (scenePostMode === "error") {
        return route.fulfill({ status: 500, contentType: "application/json; charset=utf-8", body: JSON.stringify({ detail: "scene generation failed" }) });
      }
      await new Promise((r) => setTimeout(r, 3000)); // hold the loading state
      return route.fulfill({ status: 200, contentType: "application/json; charset=utf-8", body: JSON.stringify(SCENE_ASSET) });
    });
    await page.route("**/visual/assets/*/file", async (route) => {
      const req = route.request();
      if (req.method() !== "GET") {
        unexpectedAPI.push({ url: req.url(), method: req.method(), reason: "non-GET-to-scene-file" });
        return route.abort();
      }
      const resp = await page.request.get(BASE + "/static/map/world-island.jpg");
      const body = await resp.body();
      return route.fulfill({ status: 200, contentType: "image/jpeg", body });
    });

    // Pre-connect WebSocket interception: close WITHOUT connectToServer().
    await page.routeWebSocket("**/*", (wsRoute) => {
      let url = null;
      try { url = wsRoute.url(); } catch (e) { url = "<unavailable>"; }
      wsBlocks.push({ url });
      try { wsRoute.close(); } catch (e) { /* already closed */ }
    });

    page.on("pageerror", (err) => { pageErrors.push(String((err && err.message) || err)); });

    // Onboarding card pre-dismissed (measurement hygiene only).
    await page.addInitScript(() => {
      try { window.localStorage.setItem("vl1.welcome.done", "1"); } catch (e) { /* ignore */ }
    });
  }

  // ---- guarded measurement helpers ----
  // Every probe re-checks the viewport + origin BEFORE reading geometry: the
  // MCP tab is shared, so a hijack (resize/navigation by another session)
  // must abort the measurement, never produce numbers from the wrong tab.
  async function placeMetrics(exp) {
    return await page.evaluate((exp) => {
      if (window.innerWidth !== exp.w) {
        throw new Error("m313 GUARD: innerWidth=" + window.innerWidth + " != " + exp.w + " (shared-tab viewport hijack)");
      }
      if (!location.href.startsWith(exp.base)) {
        throw new Error("m313 GUARD: location.href=" + location.href + " left " + exp.base + " (shared-tab navigation hijack)");
      }
      const box = (n) => {
        if (!n) return null;
        const r = n.getBoundingClientRect();
        return { w: Math.round(r.width), h: Math.round(r.height), bottom: Math.round(r.bottom) };
      };
      const stage = document.querySelector(".place-stage");
      const details = document.querySelector(".ctx-topbar details");
      return {
        header: box(document.querySelector(".ctx-topbar")),
        headerPresent: !!document.querySelector(".ctx-topbar"),
        disclosureClosed: details ? !details.open : null,
        stagePct: stage ? Math.round(stage.getBoundingClientRect().height
          / window.innerHeight * 1000) / 10 : null,
        overflow: document.documentElement.scrollWidth > window.innerWidth,
      };
    }, exp);
  }
  async function chatMetrics(exp) {
    return await page.evaluate((exp) => {
      if (window.innerWidth !== exp.w) {
        throw new Error("m313 GUARD: innerWidth=" + window.innerWidth + " != " + exp.w + " (shared-tab viewport hijack)");
      }
      if (!location.href.startsWith(exp.base)) {
        throw new Error("m313 GUARD: location.href=" + location.href + " left " + exp.base + " (shared-tab navigation hijack)");
      }
      const box = (n) => {
        if (!n) return null;
        const r = n.getBoundingClientRect();
        return { w: Math.round(r.width), h: Math.round(r.height), bottom: Math.round(r.bottom) };
      };
      const backdrop = document.querySelector(".chat-backdrop");
      let alpha = null;
      if (backdrop) {
        const cs = getComputedStyle(backdrop);
        const layers = cs.backgroundColor + " " + cs.backgroundImage;
        const alphas = [...layers.matchAll(/rgba?\(([^)]+)\)/g)].map((m) => {
          const parts = m[1].split(",").map((s) => parseFloat(s));
          return parts.length >= 4 ? parts[3] : 1;
        });
        alpha = alphas.length ? Math.max(...alphas) : null;
      }
      return {
        panel: box(document.querySelector(".chat-shell")),
        panelView: box(document.querySelector("#chat-view")),
        backdropPresent: !!backdrop,
        backdropAlpha: alpha,
        composer: box(document.querySelector(".chat-bar")),
      };
    }, exp);
  }
  async function worldMetrics(exp) {
    return await page.evaluate((exp) => {
      if (window.innerWidth !== exp.w) {
        throw new Error("m313 GUARD: innerWidth=" + window.innerWidth + " != " + exp.w + " (shared-tab viewport hijack)");
      }
      if (!location.href.startsWith(exp.base)) {
        throw new Error("m313 GUARD: location.href=" + location.href + " left " + exp.base + " (shared-tab navigation hijack)");
      }
      const box = (n) => {
        if (!n) return null;
        const r = n.getBoundingClientRect();
        return { w: Math.round(r.width), h: Math.round(r.height) };
      };
      const card = document.querySelector(".map-card");
      let mapPx = null;
      if (card) {
        const r = card.getBoundingClientRect();
        mapPx = Math.max(0, Math.min(r.bottom, window.innerHeight) - Math.max(r.top, 0));
      }
      return { pill: box(document.querySelector(".world-pill")), mapPx };
    }, exp);
  }

  async function gotoHash(hash, shellSelector, exp) {
    await page.evaluate((h) => { location.hash = h; }, hash);
    await page.waitForFunction(
      ([h, sel]) => location.hash === h && !!document.querySelector(sel),
      [hash, shellSelector], { timeout: 8000 });
    if (exp) { // hijack re-check after the navigation settles
      await page.evaluate((exp) => {
        if (window.innerWidth !== exp.w) {
          throw new Error("m313 GUARD: innerWidth=" + window.innerWidth + " != " + exp.w + " (shared-tab viewport hijack)");
        }
        if (!location.href.startsWith(exp.base)) {
          throw new Error("m313 GUARD: location.href=" + location.href + " left " + exp.base + " (shared-tab navigation hijack)");
        }
      }, exp);
    }
  }

  // 200% root font: double the MEASURED computed root font-size (robust to
  // UA defaults), kill transitions/animations so heights are settled, yield
  // two frames, measure, then restore everything.
  async function at200(measure, exp) {
    await page.evaluate(() => {
      const base = parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;
      document.documentElement.style.fontSize = (base * 2) + "px";
      const st = document.createElement("style");
      st.id = "__m313-notrans";
      st.textContent = "*, *::before, *::after { transition: none !important; animation: none !important; }";
      document.head.appendChild(st);
    });
    await page.evaluate(() => new Promise((r) => {
      requestAnimationFrame(() => requestAnimationFrame(r));
    }));
    try { return await measure(exp); } finally {
      await page.evaluate(() => {
        document.documentElement.style.fontSize = "";
        const st = document.getElementById("__m313-notrans");
        if (st) st.remove();
      });
    }
  }

  async function shot(name) {
    const path = OUT + "/" + name;
    await page.screenshot({ path, fullPage: true });
    return path;
  }
  const inRange = (v, lo, hi) => v !== null && v >= lo && v <= hi;

  // ---- cases ----
  // Closed contextual header geometry (pin @390: height <= 60, target 56).
  async function caseHeader(label, exp) {
    await gotoHash("#/location/1", ".place-shell", exp);
    await page.waitForFunction(() => !!document.querySelector(".place-stage"), undefined, { timeout: 8000 });
    const m = await placeMetrics(exp);
    const path = await shot("case-header-" + label + ".png");
    const headerH = m.header ? m.header.h : null;
    const results = [
      check("caseHeader/" + label + "/ctx-header-present", m.headerPresent,
        ".ctx-topbar " + (m.headerPresent ? "found" : "MISSING")),
      check("caseHeader/" + label + "/disclosure-present", m.disclosureClosed !== null,
        ".ctx-topbar details " + (m.disclosureClosed === null ? "MISSING" : "found")),
      check("caseHeader/" + label + "/disclosure-closed", m.disclosureClosed === true,
        "disclosure open=" + (m.disclosureClosed === null ? "n/a" : !m.disclosureClosed)),
      label === "390"
        ? check("caseHeader/390/header-height-le-60", headerH !== null && headerH <= 60,
          "observed " + headerH + "px (target 56, cap 60)")
        : check("caseHeader/" + label + "/header-height-recorded", headerH !== null,
          "observed " + headerH + "px (recorded only — cap pinned @390)"),
      check("caseHeader/" + label + "/no-horizontal-overflow", !m.overflow,
        "scrollWidth " + (m.overflow ? "EXCEEDS" : "fits") + " innerWidth " + exp.w),
    ];
    return {
      pass: results.every(Boolean), headerH,
      disclosureClosed: m.disclosureClosed, overflow: m.overflow, screenshot: path,
    };
  }
  // Stage asset lifecycle at 390: fallback / error / loading within [32,40];
  // ready content only floor-checked (>= 32) — expansion beyond 40 allowed.
  async function caseStageStates(exp) {
    const btn = page.locator(".place-stage button");
    // fallback: the stage before any scene flight (idle button, no image).
    const fallback = await placeMetrics(exp);
    // error: POST -> 500 -> «Визуал недоступен».
    scenePostMode = "error";
    await btn.click();
    await page.waitForFunction(() => {
      const s = document.querySelector(".scene-status");
      return s && s.textContent === "Визуал недоступен";
    }, undefined, { timeout: 6000 });
    const err = await placeMetrics(exp);
    // loading: retry -> POST held ~3s -> «Создаём визуализацию…».
    scenePostMode = "ready";
    await btn.click();
    await page.waitForFunction(() => {
      const s = document.querySelector(".scene-status");
      return s && s.textContent === "Создаём визуализацию…";
    }, undefined, { timeout: 6000 });
    const loading = await placeMetrics(exp);
    // ready: the asset file GET resolved into a decoded image.
    await page.waitForFunction(() => {
      const img = document.querySelector(".scene-image-host img");
      return !!img && img.complete && img.naturalWidth > 0;
    }, undefined, { timeout: 10000 });
    const ready = await placeMetrics(exp);
    const path = await shot("case-stage-states-390.png");
    const results = [
      check("caseStageStates/390/stage-present", fallback.stagePct !== null,
        ".place-stage " + (fallback.stagePct !== null ? "found" : "MISSING")),
      check("caseStageStates/390/stage-fallback-in-band", inRange(fallback.stagePct, 32, 40),
        "fallback " + fallback.stagePct + "% of innerHeight (band [32,40])"),
      check("caseStageStates/390/stage-error-in-band", inRange(err.stagePct, 32, 40),
        "error " + err.stagePct + "% of innerHeight (band [32,40])"),
      check("caseStageStates/390/stage-loading-in-band", inRange(loading.stagePct, 32, 40),
        "loading " + loading.stagePct + "% of innerHeight (band [32,40])"),
      check("caseStageStates/390/stage-ready-floor", ready.stagePct !== null && ready.stagePct >= 32,
        "ready " + ready.stagePct + "% of innerHeight (floor 32; > 40 allowed — target-not-cap)"),
    ];
    return {
      pass: results.every(Boolean),
      stagePct: { fallback: fallback.stagePct, error: err.stagePct,
        loading: loading.stagePct, ready: ready.stagePct },
      screenshot: path,
    };
  }
  // Chat: panel geometry, scrim alpha, composer bottom edge.
  async function caseChat(label, exp) {
    await gotoHash("#/chat", ".chat-shell", exp);
    await page.waitForFunction(() => !!document.querySelector(".chat-bar"), undefined, { timeout: 8000 });
    const m = await chatMetrics(exp);
    const path = await shot("case-chat-" + label + ".png");
    // #300: the [420,500] band targets the dialogue PANEL (#chat-view), not
    // the whole .chat-shell grid (~1076px @1440 — recorded as context only).
    const panelW = m.panelView ? m.panelView.w : null;
    const shellW = m.panel ? m.panel.w : null;
    const composerBottom = m.composer ? m.composer.bottom : null;
    const results = [
      check("caseChat/" + label + "/chat-shell-present", !!m.panel,
        ".chat-shell " + (m.panel ? "found" : "MISSING")),
      label === "1440"
        ? check("caseChat/1440/panel-width-in-band", inRange(panelW, 420, 500),
          "#chat-view width " + panelW + "px (band [420,500], target 460); .chat-shell grid " + shellW + "px (context only)")
        : check("caseChat/" + label + "/panel-width-recorded", panelW !== null,
          "#chat-view width " + panelW + "px (recorded only — band pinned @1440); .chat-shell grid " + shellW + "px (context only)"),
      check("caseChat/" + label + "/backdrop-present", m.backdropPresent,
        ".chat-backdrop " + (m.backdropPresent ? "found" : "MISSING")),
      check("caseChat/" + label + "/backdrop-alpha-positive",
        m.backdropAlpha !== null && m.backdropAlpha > 0,
        "computed background alpha " + m.backdropAlpha + " (must be > 0)"),
      check("caseChat/" + label + "/composer-present", !!m.composer,
        ".chat-bar " + (m.composer ? "found" : "MISSING")),
      label === "390"
        ? check("caseChat/390/composer-bottom-le-viewport", composerBottom !== null && composerBottom <= exp.vh,
          "composer bottom " + composerBottom + "px vs innerHeight " + exp.vh + "px")
        : check("caseChat/" + label + "/composer-bottom-recorded", composerBottom !== null,
          "composer bottom " + composerBottom + "px (recorded only — pin @390)"),
      check("caseChat/" + label + "/no-horizontal-overflow", !m.overflow,
        "scrollWidth " + (m.overflow ? "EXCEEDS" : "fits") + " innerWidth " + exp.w),
    ];
    return {
      pass: results.every(Boolean), panelW, shellW,
      backdropAlpha: m.backdropAlpha, composerBottom, screenshot: path,
    };
  }
  // 200% root font: header + composer grow; World nav pill + map unchanged.
  async function caseZoom(label, exp) {
    // composer (chat route — caseChat just navigated here)
    await page.waitForFunction(() => !!document.querySelector(".chat-bar"), undefined, { timeout: 8000 });
    const chat100 = await chatMetrics(exp);
    const chat200 = await at200(chatMetrics, exp);
    // header (place route)
    await gotoHash("#/location/1", ".place-shell", exp);
    await page.waitForFunction(() => !!document.querySelector(".place-stage"), undefined, { timeout: 8000 });
    const place100 = await placeMetrics(exp);
    const place200 = await at200(placeMetrics, exp);
    // World nav pill + map card (world route)
    await gotoHash("#/world", ".world-shell", exp);
    await page.waitForFunction(() => !!document.querySelector(".world-pill"), undefined, { timeout: 8000 });
    await page.waitForFunction(() => {
      const img = document.querySelector(".map-base");
      return !!img && img.complete && img.naturalWidth > 0;
    }, undefined, { timeout: 8000 });
    const world100 = await worldMetrics(exp);
    const world200 = await at200(worldMetrics, exp);
    const path = await shot("case-zoom-" + label + ".png");
    const h100 = place100.header ? place100.header.h : null;
    const h200 = place200.header ? place200.header.h : null;
    const c100 = chat100.composer ? chat100.composer.h : null;
    const c200 = chat200.composer ? chat200.composer.h : null;
    const same = (a, b, tol) => a !== null && b !== null && Math.abs(a - b) <= tol;
    const pillSame = world100.pill && world200.pill
      && same(world100.pill.w, world200.pill.w, 1)
      && same(world100.pill.h, world200.pill.h, 1);
    const mapSame = same(world100.mapPx, world200.mapPx, 2);
    const results = [
      check("caseZoom/" + label + "/header-grows-at-200", h100 !== null && h200 !== null && h200 > h100,
        "header " + h100 + "px -> " + h200 + "px at 200% root font"),
      check("caseZoom/" + label + "/composer-grows-at-200", c100 !== null && c200 !== null && c200 > c100,
        "composer " + c100 + "px -> " + c200 + "px at 200% root font"),
      check("caseZoom/" + label + "/world-pill-unchanged-at-200", pillSame,
        "pill " + JSON.stringify(world100.pill) + " -> " + JSON.stringify(world200.pill) + " (tol 1px)"),
      check("caseZoom/" + label + "/world-map-unchanged-at-200", mapSame,
        "map visible " + world100.mapPx + "px -> " + world200.mapPx + "px (tol 2px)"),
    ];
    return {
      pass: results.every(Boolean),
      header: { at100: h100, at200: h200 }, composer: { at100: c100, at200: c200 },
      pill: { at100: world100.pill, at200: world200.pill },
      mapPx: { at100: world100.mapPx, at200: world200.mapPx },
      screenshot: path,
    };
  }

  // ---- run ----
  async function run() {
    const report = {
      base: BASE, staticSha: null, cases: {}, screenshots: [], checks: checkLog,
      unexpectedAPI, pageErrors, wsBlocks, allPass: false,
    };
    await loadWorld();
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

    for (const [w, h, label] of [[390, 844, "390"], [320, 700, "320"], [1440, 900, "1440"]]) {
      await page.setViewportSize({ width: w, height: h });
      const exp = { w, h, base: BASE, vh: h };
      report.cases["caseHeader-" + label] = await caseHeader(label, exp);
      if (label === "390") report.cases["caseStageStates-390"] = await caseStageStates(exp);
      report.cases["caseChat-" + label] = await caseChat(label, exp);
      report.cases["caseZoom-" + label] = await caseZoom(label, exp);
    }
    report.screenshots = Object.values(report.cases).map((c) => c.screenshot);
    const names = Object.keys(report.cases);
    const REQUIRED = 10; // caseHeader x3 + caseStageStates x1 + caseChat x3 + caseZoom x3
    const checksPass = checkLog.length > 0 && checkLog.every((c) => c.pass);
    report.allPass = names.length === REQUIRED && checksPass
      && unexpectedAPI.length === 0 && pageErrors.length === 0;
    if (!report.allPass) {
      const fails = checkLog.filter((c) => !c.pass)
        .map((c) => "FAIL: " + c.name + " — " + c.detail);
      const extras = [];
      if (names.length !== REQUIRED) extras.push("case count " + names.length + " != " + REQUIRED);
      if (unexpectedAPI.length) extras.push("unexpectedAPI: " + JSON.stringify(unexpectedAPI));
      if (pageErrors.length) extras.push("pageErrors: " + JSON.stringify(pageErrors));
      const err = new Error("m313 composition FAIL — " + fails.length + " failed check(s)"
        + (extras.length ? "; " + extras.join("; ") : "") + "\n" + fails.join("\n"));
      err.report = report;
      throw err;
    }
    return report;
  }

  async function loadWorld() {
    await page.goto(BASE + "/?m313=composition#/world", { waitUntil: "load" });
    await page.waitForFunction((clock) => {
      const c = document.getElementById("clock");
      return location.hash === "#/world" && !!document.querySelector(".world-shell")
        && !!c && c.textContent === clock;
    }, CLOCK, { timeout: 8000 });
    await page.waitForFunction(() => {
      const img = document.querySelector(".map-base");
      return !!img && img.complete && img.naturalWidth > 0;
    }, undefined, { timeout: 8000 });
  }

  await setup();
  return await run();
};
