// tests/m308/tiles.browser.js
//
// MANUAL browser-acceptance + performance companion for the #308 tile-loader
// wave. Playwright `async ({ page, baseURL, viewport }) => {}` function run by
// the PARENT via the native browser MCP / run_code — NOT collected by pytest,
// NOT run by CI (scripts/check.sh = ruff + pytest only). It is the manual
// acceptance truth for the REAL served manifest + WebP tiles + POI alignment +
// #310 semantics + poll stability + failure paths + rapid switching, split
// from the CI pytest truth (tests/m308/test_map_projection.py,
// tests/m308/test_map_tile_loader.py).
//
// FIXTURE MODE ONLY. The parent starts the stdlib static server
//   python3 -m http.server 8774 --bind 127.0.0.1 --directory backend/app/web
// and passes BASE via `baseURL` (default below). Fail-closed contract copied
// from tests/m307/journey.browser.js:
//   - Static: ONLY the local BASE origin + canonical static paths, GET/HEAD,
//     transparently fulfilled from the local server (first /static stripped).
//   - Terrain: the real manifest + real WebP bytes from that same local
//     server; per-case overrides (404 / invalid / available:false / one
//     blocked tile / delayed responses / manifest blocked for the baseline).
//   - API: every known API path is a GET-only fixture; a non-GET to a known
//     path and ANY unknown path is recorded + aborted. No real backend.
//   - WebSocket: pre-connect interception via page.routeWebSocket, installed
//     BEFORE navigation; every WS is closed WITHOUT connectToServer().
//   - No real accounts/credentials/production calls.
//
// COVERAGE (fail-closed; every check prints PASS:/FAIL:, mirrored in
// report.checks; on any FAIL the function THROWS with err.report):
//   1. Real served manifest: GET 200 + validateTileManifest ok + schema
//      pins (v1/webp/1600x900/512/bleed2/8-12-8); tile requests only for
//      the selected LOD layer; no `source` paths; no absolute/escaping URLs.
//   2. Matrix: 390x844 + 1440x900 x island/region/local x
//      High/Balanced/Low-Mobile x OS reduced-motion + in-app Reduced Motion.
//      Per combo: .map-tiles after .map-base inside .map-stage; .map-base
//      decoded; every attached tile img decoded, no broken image; no stale
//      layer after a switch; .map-attribution visible with the OSM credit and
//      NOT intersecting .world-inspect / .world-pill / .poi-card (bounding-
//      box intersection); POI anchor CENTER vs coords.json image_space mapped
//      through the displayed raster (cover math + observed stage transform),
//      tolerance <= 2 CSS px.
//   3. #310 semantics: select/preview ZERO MOVE POSTs; explicit travel
//      exactly ONE MOVE POST; selection survives two real 5s poll intervals.
//   4. Poll stability: across two real poll intervals — identical .map-tile
//      node identities, zero childList mutations on .map-tiles, zero new
//      terrain requests (network log).
//   5. Failure paths: manifest 404 / invalid -> zero WebP requests + JPEG
//      underlay intact; available:false layer -> no requests for it; one
//      blocked tile -> only that core falls back, neighbours remain.
//   6. Rapid switching island->region->local->Scene with delayed responses:
//      no stale art, no broken image, no console errors.
//   7. Performance receipt: at both viewports x each quality preset —
//      terrain + page request counts and transferred bytes, cancellations,
//      visible/decoded/resident tile counts, rAF p50/p95/worst frame;
//      single-JPEG baseline (manifest route blocked); one screenshot per LOD
//      per viewport to shared/lifesim-308-<lod>-<viewport>.png; a compact
//      JSON line prefixed `PERF ` per viewport.
//
// SCREENSHOTS: case dumps under /tmp/opencode/playwright-dumps/lifesim-308/;
// owner-visible LOD receipts under /home/mostdef/vibe/projects/ai-playground/shared/.

(async ({ page, baseURL, viewport }) => {
  const BASE = String(baseURL || "http://127.0.0.1:8774").replace(/\/+$/, "");
  const OUT = "/tmp/opencode/playwright-dumps/lifesim-308";
  const SHARED = "/home/mostdef/vibe/projects/ai-playground/shared";
  const CLOCK = "День 2, 01:00";
  const VIEWPORTS = [[390, 844, "390"], [1440, 900, "1440"]];
  const LODS = [
    ["island", "Остров", "lod1"],
    ["region", "Район", "lod2"],
    ["local", "Локально", "lod3"],
  ];
  const QUALITIES = [["high", "High"], ["balanced", "Balanced"], ["low-mobile", "Low-Mobile"]];
  const LOD_SCALE = { island: 1, region: 1.65, local: 2.75 };
  const LOD_SCALE_MOBILE = { island: 1, region: 1.5, local: 2.35 };
  const BUDGETS = {
    high: { concurrent: 4, resident: 24 },
    balanced: { concurrent: 3, resident: 16 },
    "low-mobile": { concurrent: 2, resident: 12 },
  };

  // ---- coords.json image_space: the INDEPENDENT POI oracle (the projection
  // formula is test_map_projection.py's cover math, re-derived live from the
  // displayed raster — never from app.js helpers) ----
  const IMAGE_SPACE = {
    settlement: { x: 50.47, y: 46.8 },
    shop: { x: 53.11, y: 56.0 },
    workshop: { x: 45.18, y: 56.0 },
    kitchen: { x: 47.83, y: 51.4 },
    storage: { x: 53.11, y: 51.4 },
    well: { x: 50.47, y: 51.4 },
    pier: { x: 46.6, y: 89.33 },
    home: { x: 42.54, y: 51.4 },
  };
  // Fixture location ids (deterministic; home is type "house" like config).
  const POI_IDS = {
    settlement: 1, home: 2, shop: 3, workshop: 4,
    storage: 5, well: 6, pier: 7, kitchen: 8,
  };

  // ---- fixtures (shapes copied from backend/app/api/app.py + visual_routes.py;
  // x/y are the coords.json `locations` values the real backend seeds) ----
  const USER = { id: 1, username: "audit", email: "audit@fixture.local", role: "player" };
  const BYUSER = [{ id: 1, name: "P", alive: true, control_mode: "MANUAL" }];
  const CHAR1 = {
    id: 1, name: "P", sex: "m", age: 30, alive: true, location_id: 1,
    control_mode: "MANUAL", is_owner: true, balance: 100,
    needs: { hunger: 70, energy: 80, thirst: 60, social: 50 }, job: "Рыбак",
    looks: null, biography: null, outfit_mode: "auto", home_location_id: 2,
    traits: [], health: { health: 100, stress: 10 },
  };
  const WORLD = { id: 1, seed: 42, game_timestamp: 1500, day: 1, population: 12, time_scale: 1.0, is_paused: false };
  const WEATHER = {
    enabled: true, day: 1, temperature: 18, wind: 3, precipitation: 0.1,
    cloudiness: 0.3, visibility: 1.0, source: "generated",
    real_date: "2026-11-26", description: "+18°C, ясно, ветер 3 м/с",
  };
  // The eight keyed POIs (coords.json image_space census) + the player's
  // settlement as focus. Player = settlement (loc 1); travel target = shop (3).
  const LOCATIONS = [
    { id: 1, type: "settlement", name: "Посёлок", x: 50.69, y: 46.8, parent_id: null, occupants_count: 1,
      occupants: [{ id: 1, name: "P", kind: "player" }] },
    { id: 2, type: "house", name: "Residential House", x: 38.99, y: 51.4, parent_id: 1, occupants_count: 0, occupants: [] },
    { id: 3, type: "shop", name: "Магазин", x: 54.59, y: 56.0, parent_id: 1, occupants_count: 0, occupants: [] },
    { id: 4, type: "workshop", name: "Мастерская", x: 42.89, y: 56.0, parent_id: 1, occupants_count: 0, occupants: [] },
    { id: 5, type: "storage", name: "Склад", x: 54.59, y: 51.4, parent_id: 1, occupants_count: 0, occupants: [] },
    { id: 6, type: "well", name: "Колодец", x: 50.69, y: 51.4, parent_id: 1, occupants_count: 0, occupants: [] },
    { id: 7, type: "pier", name: "Причал", x: 44.98, y: 89.33, parent_id: null, occupants_count: 0, occupants: [] },
    { id: 8, type: "kitchen", name: "Кухня", x: 46.79, y: 51.4, parent_id: 1, occupants_count: 0, occupants: [] },
  ];
  const LOC1_DETAIL = { id: 1, type: "settlement", name: "Посёлок", characters_here: [1] };
  const EVENTS = [
    { id: 1, event_type: "CHARACTER_MOVED", actor_id: 2, location_id: 1, day: 1, game_timestamp: 1440,
      payload: { from_location: 2, to_location: 1 } },
  ];
  const TASKS_EMPTY = { active: [], planned: [], recent_terminal: [] };
  const PORTRAIT_404 = { status: 404, body: { detail: "canonical portrait not found" } };
  const REFS_EMPTY = { items: [] };

  // ---- collectors + per-case route state ----
  const requests = [];        // every request the routes saw { url, method }
  const terrainLog = [];      // terrain requests only { url, key, layer }
  const pageErrors = [];
  const consoleErrors = [];
  const checkLog = [];
  const unexpectedAPI = [];
  const bytes = { terrain: 0, page: 0, api: 0 };
  const counts = { terrain: 0, page: 0, api: 0 };
  let cancellations = 0;
  let movePosts = 0;
  let charReads = 0;
  let manifestStatus = null;
  // Route-state knobs (set by cases before a boot):
  let manifestMode = "ok";        // ok | notfound | invalid | lod2-off
  let manifestBlocked = false;    // perf baseline: block the manifest route
  let blockedTileKey = null;      // "lodN/row-col" -> abort that tile once
  let tileDelayMs = 0;            // delayed responses for the rapid case

  // ---- check helpers (journey.browser.js discipline) ----
  function check(name, pass, detail) {
    const line = (pass ? "PASS" : "FAIL") + ": " + name + " — " + detail;
    checkLog.push({ name, pass: !!pass, detail });
    console.log(line);
    return !!pass;
  }
  function skip(name, reason) {
    checkLog.push({ name, status: "SKIPPED", reason });
    console.log("SKIP: " + name + " — " + reason);
    return true;
  }

  function resetCaseState() {
    manifestMode = "ok";
    manifestBlocked = false;
    blockedTileKey = null;
    tileDelayMs = 0;
    movePosts = 0;
    charReads = 0;
    manifestStatus = null;
    cancellations = 0;
    counts.terrain = 0; counts.page = 0; counts.api = 0;
    bytes.terrain = 0; bytes.page = 0; bytes.api = 0;
    terrainLog.length = 0;
  }

  // ---- setup (fail-closed, same discipline as m307) ----
  async function setup() {
    await page.goto("about:blank", { waitUntil: "load" });
    if (typeof page.routeWebSocket !== "function") {
      throw new Error("page.routeWebSocket unavailable — cannot guarantee pre-connect WS interception; aborting before navigation");
    }
    await page.unrouteAll();

    // Fallback route (installed FIRST -> lowest precedence): ONLY local BASE
    // + canonical static, GET/HEAD, first /static prefix stripped. Everything
    // else recorded + aborted.
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
        counts.page += 1;
        bytes.page += body.length;
        requests.push({ url: u.href, method: req.method() });
        return route.fulfill({ status: resp.status(), contentType: ct, body });
      }
      unexpectedAPI.push({ url: u.href, method: req.method(), reason: isBase ? "non-static-or-non-GET" : "foreign-origin" });
      return route.abort();
    });

    // Terrain route (installed AFTER the fallback -> higher precedence): the
    // REAL manifest + REAL WebP bytes from the local server, with per-case
    // overrides. Records every tile request for the LOD-only / hygiene checks.
    await page.route("**/static/static/map/tiles/**", async (route) => {
      const req = route.request();
      const u = new URL(req.url());
      const path = u.pathname; // /static/static/map/tiles/<rel>
      const rel = path.slice("/static/static/map/tiles/".length);
      if (req.method() !== "GET" && req.method() !== "HEAD") {
        unexpectedAPI.push({ url: u.href, method: req.method(), reason: "non-GET-to-terrain" });
        return route.abort();
      }
      requests.push({ url: u.href, method: req.method() });

      if (rel === "manifest.json") {
        if (manifestBlocked) {
          manifestStatus = 404;
          counts.terrain += 1;
          return route.fulfill({ status: 404, contentType: "text/plain", body: "blocked" });
        }
        if (manifestMode === "notfound") {
          manifestStatus = 404;
          counts.terrain += 1;
          return route.fulfill({ status: 404, contentType: "text/plain", body: "not found" });
        }
        const resp = await page.request.get(BASE + "/static/map/tiles/manifest.json");
        const real = await resp.text();
        manifestStatus = resp.status();
        counts.terrain += 1;
        let body = real;
        if (manifestMode === "invalid") {
          body = JSON.stringify({ schemaVersion: 99, format: "png", worldSize: [1, 1] });
        } else if (manifestMode === "lod2-off") {
          const m = JSON.parse(real);
          m.layers.lod2 = { available: false };
          body = JSON.stringify(m);
        }
        bytes.terrain += Buffer.byteLength(body);
        return route.fulfill({ status: manifestStatus, contentType: "application/json", body });
      }

      // Tile bytes: <lod>/<row>-<col>.webp
      const m = /^(lod[123])\/(\d+-\d+)\.webp$/.exec(rel);
      if (!m) {
        unexpectedAPI.push({ url: u.href, method: req.method(), reason: "tile-path-not-confined" });
        return route.abort();
      }
      const layer = m[1];
      const key = layer + "/" + m[2];
      terrainLog.push({ url: u.href, key, layer });
      if (tileDelayMs > 0) await new Promise((r) => setTimeout(r, tileDelayMs));
      if (blockedTileKey === key) {
        cancellations += 1;
        return route.abort();
      }
      const resp = await page.request.get(BASE + "/static/map/tiles/" + rel);
      const body = await resp.body();
      counts.terrain += 1;
      bytes.terrain += body.length;
      const ct = (resp.headers() || {})["content-type"] || "image/webp";
      return route.fulfill({ status: resp.status(), contentType: ct, body });
    });

    // API fixtures (installed LAST -> highest precedence). GET-only.
    const json = (route, s) => route.fulfill({ status: s.status, contentType: "application/json; charset=utf-8", body: JSON.stringify(s.body) });
    const ok = (body) => ({ status: 200, body });
    const getOnly = (s) => (route) => {
      if (route.request().method() === "GET") {
        counts.api += 1;
        bytes.api += JSON.stringify(s.body).length;
        requests.push({ url: route.request().url(), method: "GET" });
        return json(route, s);
      }
      unexpectedAPI.push({ url: route.request().url(), method: route.request().method(), reason: "non-GET-to-known-api" });
      return route.abort();
    };
    await page.route("**/auth/me", getOnly(ok(USER)));
    await page.route("**/characters/by-user/1", getOnly(ok(BYUSER)));
    await page.route("**/characters/1", (route) => {
      if (route.request().method() !== "GET") {
        unexpectedAPI.push({ url: route.request().url(), method: route.request().method(), reason: "non-GET-to-char" });
        return route.abort();
      }
      charReads += 1;
      counts.api += 1;
      bytes.api += JSON.stringify(CHAR1).length;
      requests.push({ url: route.request().url(), method: "GET" });
      return json(route, ok(CHAR1));
    });
    await page.route("**/characters/1/tasks", getOnly(ok(TASKS_EMPTY)));
    await page.route("**/world", getOnly(ok(WORLD)));
    await page.route("**/weather", getOnly(ok(WEATHER)));
    await page.route("**/locations", getOnly(ok(LOCATIONS)));
    await page.route("**/locations/*", getOnly(ok(LOC1_DETAIL)));
    await page.route("**/world/events*", getOnly(ok(EVENTS)));
    await page.route("**/visual/characters/1/portrait", getOnly(PORTRAIT_404));
    await page.route("**/visual/references", getOnly(ok(REFS_EMPTY)));

    // The ONE authorized MOVE path (#310): POST /actions flips nothing —
    // it counts the MOVE POST and returns the accepted task.
    await page.route("**/actions", (route) => {
      const req = route.request();
      if (req.method() !== "POST") {
        unexpectedAPI.push({ url: req.url(), method: req.method(), reason: "non-POST-to-actions" });
        return route.abort();
      }
      let body = null;
      try { body = JSON.parse(req.postData() || "{}"); } catch (e) { body = null; }
      movePosts += 1;
      counts.api += 1;
      bytes.api += (req.postData() || "").length;
      requests.push({ url: req.url(), method: "POST" });
      return json(route, ok({ task_id: 30801, task_type: "MOVE", task_label: "Перемещение", status: "planned" }));
    });
    await page.route("**/actions/*", getOnly(ok({ id: 30801, status: "planned", task_type: "MOVE" })));

    // Pre-connect WebSocket interception: close WITHOUT connectToServer().
    await page.routeWebSocket("**/*", (wsRoute) => {
      let url = null;
      try { url = wsRoute.url(); } catch (e) { url = "<unavailable>"; }
      try { wsRoute.close(); } catch (e) { /* already closed */ }
    });

    page.on("pageerror", (err) => { pageErrors.push(String((err && err.message) || err)); });
    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(msg.text());
    });

    // Onboarding card pre-dismissed + deterministic settings per boot
    // (measurement hygiene only; never affects prod semantics).
    await page.addInitScript(() => {
      try { window.localStorage.setItem("vl1.welcome.done", "1"); } catch (e) { /* ignore */ }
      try { window.localStorage.removeItem("vl1.settings.v1"); } catch (e) { /* ignore */ }
    });
  }

  // ---- boot (unique query param = full document load, like m307) ----
  let bootSeq = 0;
  async function loadWorld() {
    bootSeq += 1;
    await page.goto(BASE + "/?m308=tiles&boot=" + bootSeq + "#/world", { waitUntil: "load" });
    await page.waitForFunction((clock) => {
      const c = document.getElementById("clock");
      return location.hash === "#/world" && !!document.querySelector(".world-shell")
        && !!document.querySelector(".map-card") && !!c && c.textContent === clock;
    }, CLOCK, { timeout: 10000 });
    await page.waitForFunction(() => {
      const img = document.querySelector(".map-base");
      return !!img && img.complete && img.naturalWidth > 0;
    }, undefined, { timeout: 10000 });
  }

  // Wait until the terrain controller is quiet: manifest settled, zero in-
  // flight fetches, every attached tile img decoded, and (optionally) at
  // least one tile painted. Default mode dispatches a resize so the coalesced
  // reconcile pass runs without waiting for the next poll tick.
  // opts.strict (poll-stability baseline) additionally requires EVERY
  // currently wanted key to have an attached .map-tile[data-key] node and NO
  // attached key outside terrainState.wanted — "zero inflight + some painted
  // tiles" can look settled while a later wanted key (1440, budget 2–4) is
  // still queued, whose late arrival would masquerade as poll churn — and it
  // never dispatches synthetic resize (the baseline must not manufacture
  // mutations; strict also always requires tiles > 0).
  async function settleTerrain(expectTiles, timeoutMs, opts) {
    const strict = !!(opts && opts.strict);
    const needTiles = expectTiles || strict;
    const deadline = Date.now() + (timeoutMs || 20000);
    for (;;) {
      const st = await page.evaluate(() => {
        const host = document.querySelector(".map-tiles");
        const imgs = host ? Array.from(host.querySelectorAll("img")) : [];
        const attachedKeys = host
          ? Array.from(host.querySelectorAll(".map-tile")).map((n) => n.getAttribute("data-key") || "")
          : [];
        const wanted = typeof terrainState !== "undefined" && Array.isArray(terrainState.wanted)
          ? terrainState.wanted.slice() : [];
        return {
          mounted: typeof terrainState !== "undefined" && terrainState.mounted === true,
          manifest: typeof terrainState !== "undefined" && !!terrainState.manifest,
          manifestError: typeof terrainState !== "undefined" ? terrainState.manifestError : "missing",
          inflight: typeof terrainState !== "undefined" ? terrainState.requests.size : -1,
          tiles: host ? host.querySelectorAll(".map-tile").length : 0,
          pending: imgs.filter((i) => !i.complete || i.naturalWidth === 0).length,
          wanted,
          attachedKeys,
        };
      });
      if (st.manifestError) {
        if (!expectTiles) return st;
        throw new Error("terrain settle: manifest failed while tiles were expected: " + JSON.stringify(st));
      }
      const quiet = st.mounted && st.manifest && st.inflight === 0 && st.pending === 0
        && (!needTiles || st.tiles > 0);
      const covered = !strict
        || (st.wanted.every((k) => st.attachedKeys.indexOf(k) !== -1)
          && st.attachedKeys.every((k) => st.wanted.indexOf(k) !== -1));
      if (quiet && covered) return st;
      if (Date.now() > deadline) throw new Error("terrain settle timeout: " + JSON.stringify(st));
      if (!strict) await page.evaluate(() => { window.dispatchEvent(new Event("resize")); });
      await page.waitForTimeout(120);
    }
  }

  function expectedScale(lod, w) {
    const table = w <= 520 ? LOD_SCALE_MOBILE : LOD_SCALE;
    return table[lod];
  }

  async function waitForScale(scale) {
    await page.waitForFunction((s) => {
      const stage = document.querySelector(".map-stage");
      if (!stage) return false;
      const m = getComputedStyle(stage).transform;
      if (!m || m === "none") return s === 1;
      const mm = /matrix\(([^)]+)\)/.exec(m);
      if (!mm) return false;
      const a = parseFloat(mm[1].split(",")[0]);
      return Math.abs(a - s) < 0.01;
    }, scale, { timeout: 6000 });
  }

  async function shot(name) {
    const path = OUT + "/" + name;
    await page.screenshot({ path, fullPage: true });
    return path;
  }

  // ---- attribution probe: text + in-viewport + bounding-box intersection
  // against .world-inspect / .world-pill / .poi-card ----
  async function probeAttribution() {
    return await page.evaluate(() => {
      const attr = document.querySelector(".map-attribution");
      if (!attr) return { present: false };
      const r = attr.getBoundingClientRect();
      const box = (sel) => {
        const n = document.querySelector(sel);
        if (!n) return null;
        const b = n.getBoundingClientRect();
        if (!b.width || !b.height) return null;
        return { left: b.left, top: b.top, right: b.right, bottom: b.bottom };
      };
      const overlapArea = (a, b) => {
        if (!a || !b) return 0;
        const w = Math.min(a.right, b.right) - Math.max(a.left, b.left);
        const h = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
        return w > 1 && h > 1 ? Math.round(w * h) : 0; // >1px each axis = real
      };
      const A = { left: r.left, top: r.top, right: r.right, bottom: r.bottom };
      const targets = {
        inspect: box(".world-inspect"),
        pill: box(".world-pill"),
        poiCard: box(".poi-card"),
      };
      const overlaps = {};
      for (const k of Object.keys(targets)) overlaps[k] = overlapArea(A, targets[k]);
      return {
        present: true,
        text: (attr.textContent || "").trim(),
        inViewport: r.width > 0 && r.height > 0
          && r.left >= -1 && r.top >= -1
          && r.right <= window.innerWidth + 1 && r.bottom <= window.innerHeight + 1,
        rect: A,
        targets,
        overlaps,
      };
    });
  }

  // ---- POI alignment probe: anchor CENTER vs image_space through the
  // displayed raster (cover math on naturalWidth/Height + the OBSERVED
  // stage transform matrix/origin) ----
  async function probePois() {
    return await page.evaluate(({ imageSpace, poiIds }) => {
      const card = document.querySelector(".map-card");
      const stage = card && card.querySelector(".map-stage");
      const base = card && card.querySelector(".map-base");
      if (!card || !stage || !base) return { error: "map card/stage/base missing" };
      if (!base.naturalWidth) return { error: "map-base not decoded" };
      const cw = card.clientWidth;
      const ch = card.clientHeight;
      const crect = card.getBoundingClientRect();
      const bl = (crect.width - cw) / 2; // symmetric 1px border
      const bt = (crect.height - ch) / 2;

      // Observed stage transform (matrix + resolved origin in stage px).
      const cs = getComputedStyle(stage);
      let a = 1, b = 0, c = 0, d = 1, e = 0, f = 0;
      const tm = cs.transform;
      if (tm && tm !== "none") {
        const mm = /matrix\(([^)]+)\)/.exec(tm);
        if (mm) {
          const p = mm[1].split(",").map(Number);
          a = p[0]; b = p[1]; c = p[2]; d = p[3]; e = p[4]; f = p[5];
        }
      }
      const orig = (cs.transformOrigin || "0 0").split(" ");
      const dim = (v, total) => (v || "").endsWith("%")
        ? parseFloat(v) / 100 * total : parseFloat(v) || 0;
      const ox = dim(orig[0], cw);
      const oy = dim(orig[1], ch);

      // Cover mapping of the displayed raster onto the untransformed stage
      // (test_map_projection.py's worldToScreen, expressed as cover math).
      const iw = base.naturalWidth;
      const ih = base.naturalHeight;
      const k = Math.max(cw / iw, ch / ih);
      const offX = (cw - iw * k) / 2;
      const offY = (ch - ih * k) / 2;

      const out = [];
      for (const key of Object.keys(imageSpace)) {
        const id = poiIds[key];
        const anchor = card.querySelector('[data-loc-id="' + id + '"]');
        const img = imageSpace[key];
        if (!anchor) {
          out.push({ key, visible: false, reason: "anchor not rendered at this LOD" });
          continue;
        }
        const ar = anchor.getBoundingClientRect();
        if (!ar.width || !ar.height) {
          out.push({ key, visible: false, reason: "anchor has no box" });
          continue;
        }
        // Attachment point = element CENTER (translate(-50%,-50%) puts the
        // (left%,top%) point at the box center; the label is absolute and
        // never grows the box).
        const cx = ar.left + ar.width / 2;
        const cy = ar.top + ar.height / 2;
        const inside = cx >= crect.left - 2 && cx <= crect.right + 2
          && cy >= crect.top - 2 && cy <= crect.bottom + 2;
        if (!inside) {
          out.push({ key, visible: false, reason: "anchor center clipped outside the card" });
          continue;
        }
        const wx = img.x / 100 * iw;
        const wy = img.y / 100 * ih;
        const px = offX + wx * k;
        const py = offY + wy * k;
        const ddx = px - ox;
        const ddy = py - oy;
        const fx = ox + a * ddx + c * ddy + e;
        const fy = oy + b * ddx + d * ddy + f;
        const ex = crect.left + bl + fx;
        const ey = crect.top + bt + fy;
        const dist = Math.hypot(cx - ex, cy - ey);
        out.push({
          key, visible: true,
          dist: Math.round(dist * 100) / 100,
          dx: Math.round((cx - ex) * 100) / 100,
          dy: Math.round((cy - ey) * 100) / 100,
        });
      }
      return { points: out };
    }, { imageSpace: IMAGE_SPACE, poiIds: POI_IDS });
  }

  // ---- rAF frame-interval sampler (perf receipt) ----
  async function sampleFrames(n) {
    return await page.evaluate((count) => new Promise((resolve) => {
      const ts = [];
      let last = null;
      const tick = (t) => {
        if (last !== null) ts.push(t - last);
        last = t;
        if (ts.length < count) requestAnimationFrame(tick);
        else resolve(ts);
      };
      requestAnimationFrame(tick);
    }), n);
  }
  function frameStats(ts) {
    const s = ts.slice().sort((x, y) => x - y);
    const pick = (p) => s[Math.min(s.length - 1, Math.floor(p * s.length))];
    return {
      p50: Math.round(pick(0.5) * 100) / 100,
      p95: Math.round(pick(0.95) * 100) / 100,
      worst: Math.round(s[s.length - 1] * 100) / 100,
      samples: s.length,
    };
  }

  async function tileCounts() {
    return await page.evaluate(() => {
      const host = document.querySelector(".map-tiles");
      const imgs = host ? Array.from(host.querySelectorAll("img")) : [];
      const cache = typeof terrainState !== "undefined" ? terrainState.cache.size : -1;
      return {
        visible: host ? host.querySelectorAll(".map-tile").length : 0,
        decoded: imgs.filter((i) => i.complete && i.naturalWidth > 0).length,
        broken: imgs.filter((i) => i.complete && i.naturalWidth === 0).length,
        resident: cache,
      };
    });
  }

  const seenPois = new Set();

  // =====================================================================
  // CASE: assertion 1 — real served manifest + request hygiene
  // =====================================================================
  async function caseManifest(label) {
    resetCaseState();
    await loadWorld();
    await settleTerrain(true);

    const man = await page.evaluate(async () => {
      const r = await fetch("/static/static/map/tiles/manifest.json");
      const data = await r.json();
      const v = validateTileManifest(data);
      return { status: r.status, ok: v.ok, error: v.error, data };
    });
    const results = [];
    results.push(check("manifest/" + label + "/http-200", man.status === 200, "GET status " + man.status));
    results.push(check("manifest/" + label + "/route-status", manifestStatus === 200, "route saw status " + manifestStatus));
    results.push(check("manifest/" + label + "/validates", man.ok === true, "validateTileManifest -> " + JSON.stringify({ ok: man.ok, error: man.error })));
    const d = man.data || {};
    results.push(check("manifest/" + label + "/schema-pins",
      d.schemaVersion === 1 && d.format === "webp"
      && Array.isArray(d.worldSize) && d.worldSize[0] === 1600 && d.worldSize[1] === 900
      && d.tileSize === 512 && d.bleedPx === 2,
      JSON.stringify({ schemaVersion: d.schemaVersion, format: d.format, worldSize: d.worldSize, tileSize: d.tileSize, bleedPx: d.bleedPx })));
    const L = d.layers || {};
    const countsOk = L.lod1 && L.lod1.available === true && L.lod1.tiles && L.lod1.tiles.length === 8
      && L.lod2 && L.lod2.available === true && L.lod2.tiles && L.lod2.tiles.length === 12
      && L.lod3 && L.lod3.available === true && L.lod3.tiles && L.lod3.tiles.length === 8;
    results.push(check("manifest/" + label + "/layer-counts", !!countsOk,
      "lod1=" + (L.lod1 && L.lod1.tiles && L.lod1.tiles.length) +
      " lod2=" + (L.lod2 && L.lod2.tiles && L.lod2.tiles.length) +
      " lod3=" + (L.lod3 && L.lod3.tiles && L.lod3.tiles.length)));
    // Tile URLs confined: relative, no scheme, no .., no backslash.
    let confined = true;
    const badUrl = [];
    for (const lid of ["lod1", "lod2", "lod3"]) {
      for (const t of (L[lid] && L[lid].tiles) || []) {
        const u = t.url;
        const okUrl = typeof u === "string" && u.length > 0
          && u[0] !== "/" && u[0] !== "#" && u[0] !== "?"
          && u.indexOf("\\") === -1 && !/^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(u)
          && u.split("/").every((seg) => seg !== "" && seg !== "." && seg !== "..");
        if (!okUrl) { confined = false; badUrl.push(lid + ":" + u); }
      }
    }
    results.push(check("manifest/" + label + "/urls-confined", confined, badUrl.length ? JSON.stringify(badUrl) : "all 28 tile urls confined"));

    // Request hygiene over the whole boot: only the selected LOD layer is
    // ever requested, no `source` paths, no absolute/escaping URLs.
    const layersSeen = new Set(terrainLog.map((t) => t.layer));
    results.push(check("manifest/" + label + "/tile-layers",
      layersSeen.size > 0 && [...layersSeen].every((l) => ["lod1", "lod2", "lod3"].includes(l)),
      "layers requested: " + JSON.stringify([...layersSeen])));
    const sourceReq = requests.filter((r) => /\/source\//.test(r.url) || /map-source/.test(r.url));
    results.push(check("manifest/" + label + "/no-source-requests", sourceReq.length === 0,
      sourceReq.length ? JSON.stringify(sourceReq.slice(0, 3)) : "no source paths requested"));
    const badTileUrl = terrainLog.filter((t) => {
      try {
        const u = new URL(t.url);
        return u.origin !== BASE
          || !u.pathname.startsWith("/static/static/map/tiles/")
          || u.pathname.split("/").includes("..")
          || u.pathname.split("").includes("\\");
      } catch (e) { return true; }
    });
    results.push(check("manifest/" + label + "/tile-urls-same-origin-confined", badTileUrl.length === 0,
      badTileUrl.length ? JSON.stringify(badTileUrl.slice(0, 3)) : terrainLog.length + " tile requests all same-origin and confined"));
    // Only the island layer is wanted before any LOD switch — every tile
    // request so far must be lod1 (the boot default).
    const wrongLayer = terrainLog.filter((t) => t.layer !== "lod1");
    results.push(check("manifest/" + label + "/boot-layer-lod1", wrongLayer.length === 0,
      wrongLayer.length ? JSON.stringify(wrongLayer.slice(0, 3)) : "all " + terrainLog.length + " boot tile requests are lod1"));

    const path = await shot("case-manifest-" + label + ".png");
    return { pass: results.every(Boolean), screenshot: path, manifestStatus: man.status };
  }

  // =====================================================================
  // CASE: assertion 2 — the full matrix (per viewport x motion mode)
  // =====================================================================
  async function caseMatrix(label, w, h, motion) {
    resetCaseState();
    const exp = { w, h, base: BASE, vh: h, label };
    if (motion === "os") await page.emulateMedia({ reducedMotion: "reduce" });
    else await page.emulateMedia({ reducedMotion: "no-preference" });
    await loadWorld();

    const results = [];
    const motionTag = motion === "os" ? "os-reduce" : "in-app";

    // In-app Reduced Motion: flip the toolbar toggle (OS mode keeps it off).
    if (motion === "app") {
      await page.locator(".map-reduced-toggle input").click();
      await page.waitForFunction(() => {
        const c = document.querySelector(".map-card");
        return !!c && c.classList.contains("reduced-motion");
      }, undefined, { timeout: 4000 });
    }
    const reducedClass = await page.evaluate(() => {
      const c = document.querySelector(".map-card");
      return !!c && c.classList.contains("reduced-motion");
    });
    results.push(check("matrix/" + label + "/" + motionTag + "/reduced-motion-class",
      reducedClass === true, "map-card.reduced-motion = " + reducedClass));

    // Open the POI selection card once (shop — never the player's own
    // settlement, whose card variant has no «Пойти сюда»); the card rides
    // along every combo so its occluder rect is measured too.
    await page.locator('.location-anchor[data-loc-id="3"]').click();
    await page.waitForFunction(() => !!document.getElementById("poi-card"), undefined, { timeout: 4000 });

    let prevLayer = "lod1";
    for (const [qKey, qLabel] of QUALITIES) {
      await page.locator(".map-quality button", { hasText: qLabel }).click();
      await page.waitForFunction((q) => {
        const c = document.querySelector(".map-card");
        return !!c && c.classList.contains("quality-" + q);
      }, qKey, { timeout: 4000 });
      const want = BUDGETS[qKey];
      // The quality class flips synchronously with the click, but
      // terrainState.budget is rewritten by the next rAF-coalesced reconcile
      // pass — wait for the controller to adopt THIS preset before reading it
      // (the exact-equality assertion below is unchanged).
      await page.waitForFunction((b) => typeof terrainState !== "undefined"
        && terrainState.budget.concurrent === b.concurrent
        && terrainState.budget.resident === b.resident, want, { timeout: 6000 });
      const budget = await page.evaluate(() => ({
        concurrent: terrainState.budget.concurrent,
        resident: terrainState.budget.resident,
      }));
      results.push(check("matrix/" + label + "/" + motionTag + "/" + qKey + "/budget",
        budget.concurrent === want.concurrent && budget.resident === want.resident,
        JSON.stringify(budget) + " expected " + JSON.stringify(want)));

      for (const [lodKey, lodLabel, layer] of LODS) {
        await page.locator(".living-map > .map-toolbar:not(.map-quality) button", { hasText: lodLabel }).click();
        const scale = expectedScale(lodKey, w);
        await waitForScale(scale);
        // reconcileTerrain is rAF-coalesced: the previous iteration's pass can
        // still fire after this click, attaching the previous LOD's keys and
        // issuing its requests after the marker. Wait until a pass has
        // ADOPTED this LOD (terrainAdmitTile resets signature to "" on every
        // admit, so poll until it is repopulated with this lod prefix) before
        // stamping the request marker and reading the DOM.
        await page.waitForFunction((lod) => typeof terrainState !== "undefined"
          && terrainState.signature.indexOf(lod + "|") === 0, lodKey, { timeout: 6000 });
        const marker = terrainLog.length;
        const wantedNow = await page.evaluate(() => (
          typeof terrainState !== "undefined" && Array.isArray(terrainState.wanted)
            ? terrainState.wanted.length : -1));
        // Zero wanted tiles outside lod1 = focus outside the detail ROI
        // (architect contract: zero detail requests is valid) — settle WITHOUT
        // demanding tiles (settleTerrain(true) would time out) and skip only
        // the two tile-dependent checks below; everything else still runs.
        const zeroRoi = wantedNow === 0 && layer !== "lod1";
        const st = await settleTerrain(!zeroRoi);
        const tag = "matrix/" + label + "/" + motionTag + "/" + qKey + "/" + lodKey;

        // (a) tree order: .map-tiles directly after .map-base in .map-stage
        const tree = await page.evaluate(() => {
          const stage = document.querySelector(".map-stage");
          const base = document.querySelector(".map-base");
          const tiles = document.querySelector(".map-tiles");
          return {
            ok: !!stage && !!base && !!tiles
              && tiles.parentElement === stage
              && base.nextElementSibling === tiles,
            aria: tiles ? tiles.getAttribute("aria-hidden") : null,
          };
        });
        results.push(check(tag + "/tiles-after-base", tree.ok && tree.aria === "true",
          "parent/stage+next/base ok=" + tree.ok + " aria-hidden=" + tree.aria));

        // (b) underlay decoded
        const baseDecoded = await page.evaluate(() => {
          const i = document.querySelector(".map-base");
          return !!i && i.complete && i.naturalWidth > 0;
        });
        results.push(check(tag + "/base-decoded", baseDecoded, "map-base naturalWidth>0 = " + baseDecoded));

        // (c) tiles painted + every attached img decoded, none broken
        const tc = await tileCounts();
        if (zeroRoi) {
          skip(tag + "/tiles-painted", "focus outside detail ROI (zero wanted tiles is valid)");
          // The broken-image half of (c) is NOT tile-dependent — keep it.
          results.push(check(tag + "/no-broken-image", tc.broken === 0,
            "visible=" + tc.visible + " decoded=" + tc.decoded + " broken=" + tc.broken
            + " resident=" + tc.resident + " (zero wanted tiles)"));
        } else {
          results.push(check(tag + "/tiles-painted", tc.visible > 0 && tc.decoded === tc.visible && tc.broken === 0,
            "visible=" + tc.visible + " decoded=" + tc.decoded + " broken=" + tc.broken + " resident=" + tc.resident + " inflight-settled=" + (st.inflight === 0)));
        }

        // (d) no stale detail: every attached key belongs to THIS layer
        const keys = await page.evaluate(() => Array.from(document.querySelectorAll(".map-tile"))
          .map((n) => n.getAttribute("data-key") || ""));
        const stale = keys.filter((k) => !k.startsWith(layer + "/"));
        if (zeroRoi) {
          skip(tag + "/no-stale-layer", "focus outside detail ROI (zero wanted tiles is valid)");
        } else {
          results.push(check(tag + "/no-stale-layer", keys.length > 0 && stale.length === 0,
            keys.length + " tiles, stale=" + JSON.stringify(stale)));
        }

        // (e) terrain requests since this switch are only for this layer
        const switched = terrainLog.slice(marker);
        const wrongLayer = switched.filter((t) => t.layer !== layer);
        results.push(check(tag + "/switch-requests-only-current-layer", wrongLayer.length === 0,
          switched.length + " requests since switch, wrong-layer=" + JSON.stringify(wrongLayer.slice(0, 3))));

        // (f) attribution: text, in-viewport, no bounding-box intersection
        const attr = await probeAttribution();
        if (!attr.present) {
          results.push(check(tag + "/attribution-present", false, ".map-attribution missing"));
        } else {
          results.push(check(tag + "/attribution-text",
            attr.text.indexOf("© OpenStreetMap contributors") >= 0, JSON.stringify(attr.text)));
          results.push(check(tag + "/attribution-in-viewport", attr.inViewport === true,
            "rect=" + JSON.stringify(attr.rect)));
          results.push(check(tag + "/attribution-not-occluded",
            attr.overlaps.inspect === 0 && attr.overlaps.pill === 0 && attr.overlaps.poiCard === 0,
            "overlap px^2 inspect=" + attr.overlaps.inspect + " pill=" + attr.overlaps.pill + " poiCard=" + attr.overlaps.poiCard
            + " (targets " + JSON.stringify(attr.targets) + ")"));
        }

        // (g) POI alignment: anchor center vs image_space through the raster
        const probe = await probePois();
        if (probe.error) {
          results.push(check(tag + "/poi-probe", false, probe.error));
        } else {
          for (const p of probe.points) {
            if (!p.visible) {
              skip("poi/" + p.key + "/" + label + "/" + motionTag + "/" + qKey + "/" + lodKey, p.reason);
              continue;
            }
            seenPois.add(p.key);
            results.push(check("poi/" + p.key + "/" + label + "/" + motionTag + "/" + qKey + "/" + lodKey,
              p.dist <= 2, "offset=" + p.dist + "px (dx=" + p.dx + " dy=" + p.dy + ") <= 2px"));
          }
        }
      }
    }

    const path = await shot("case-matrix-" + label + "-" + motionTag + ".png");
    return { pass: results.every(Boolean), screenshot: path };
  }

  // =====================================================================
  // CASE: assertion 3 — #310 semantics (select/preview/travel/selection)
  // =====================================================================
  async function casePoiSemantics(label) {
    resetCaseState();
    await loadWorld();
    await settleTerrain(true);
    const results = [];

    // (a) POI select = ZERO MOVE POSTs
    const postsBeforeSelect = movePosts;
    await page.locator('.location-anchor[data-loc-id="3"]').click();
    await page.waitForFunction(() => !!document.getElementById("poi-card"), undefined, { timeout: 4000 });
    await page.waitForTimeout(400);
    results.push(check("poi-semantics/" + label + "/select-zero-move-posts",
      movePosts === postsBeforeSelect, "MOVE POSTs " + postsBeforeSelect + " -> " + movePosts));

    // (b) preview («Посмотреть место») = still ZERO MOVE POSTs
    await page.locator(".poi-card a.poi-card-btn", { hasText: "Посмотреть место" }).click();
    await page.waitForFunction(() => location.hash.indexOf("#/location/") === 0, undefined, { timeout: 6000 });
    await page.waitForTimeout(400);
    results.push(check("poi-semantics/" + label + "/preview-zero-move-posts",
      movePosts === 0, "MOVE POSTs after preview = " + movePosts));

    // (c) back to World, re-select, then TWO REAL poll intervals
    await page.evaluate(() => { location.hash = "#/world"; });
    await page.waitForFunction(() => !!document.querySelector(".world-shell") && !!document.querySelector(".map-card"),
      undefined, { timeout: 8000 });
    await settleTerrain(true);
    await page.locator('.location-anchor[data-loc-id="3"]').click();
    await page.waitForFunction(() => !!document.getElementById("poi-card"), undefined, { timeout: 4000 });
    const readTarget = charReads + 2; // two real /characters/1 poll reads
    await page.waitForFunction((t) => window.__m308CharReads >= t, readTarget, { timeout: 1000 }).catch(() => {});
    // charReads lives in Node; poll it here instead.
    const pollDeadline = Date.now() + 20000;
    while (charReads < readTarget) {
      if (Date.now() > pollDeadline) break;
      await page.waitForTimeout(250);
    }
    results.push(check("poi-semantics/" + label + "/two-poll-intervals",
      charReads >= readTarget, "char reads " + readTarget + " reached: " + charReads));
    const survived = await page.evaluate(() => ({
      card: !!document.getElementById("poi-card"),
      ring: !!document.querySelector(".anchor--selected"),
      title: (document.querySelector(".poi-card-title") || {}).textContent || null,
    }));
    results.push(check("poi-semantics/" + label + "/selection-survives-polls",
      survived.card && survived.ring, "card=" + survived.card + " ring=" + survived.ring + " title=" + JSON.stringify(survived.title)));

    // (d) explicit travel = exactly ONE authorized MOVE POST
    const postsBeforeTravel = movePosts;
    await page.locator(".poi-card .poi-card-go").click();
    const moveDeadline = Date.now() + 8000;
    while (movePosts === postsBeforeTravel && Date.now() < moveDeadline) await page.waitForTimeout(100);
    results.push(check("poi-semantics/" + label + "/travel-one-move-post",
      movePosts === postsBeforeTravel + 1, "MOVE POSTs " + postsBeforeTravel + " -> " + movePosts));

    const path = await shot("case-poi-semantics-" + label + ".png");
    return { pass: results.every(Boolean), screenshot: path };
  }

  // =====================================================================
  // CASE: assertion 5 — poll stability across two REAL poll intervals
  // =====================================================================
  async function casePollStability(label) {
    resetCaseState();
    await loadWorld();
    // STRICT baseline settle: every wanted key attached + nothing foreign
    // attached — a loose "zero inflight + some tiles" settle could stamp
    // while a sixth wanted key (1440, budget 2–4) was still queued, and its
    // later arrival would masquerade as poll churn.
    await settleTerrain(true, undefined, { strict: true });
    const results = [];

    // Geometry + wanted/attached evidence, read at the stamp and after each
    // completed poll, so a future legitimate adaptation (card box / stage
    // scale / wanted-set change) is distinguishable from childList/request
    // churn in the check details below.
    const viewSnap = (v) => v ? JSON.stringify({
      w: v.w, h: v.h, scale: v.scale, wanted: v.wanted, attachedKeys: v.attachedKeys,
    }) : "null";
    const readView = () => page.evaluate(() => {
      const card = document.querySelector(".map-card");
      const stage = document.querySelector(".map-stage");
      const host = document.querySelector(".map-tiles");
      const rect = card ? card.getBoundingClientRect() : null;
      let scale = 1;
      const tm = stage ? getComputedStyle(stage).transform : "";
      if (tm && tm !== "none") {
        const mm = /matrix\(([^)]+)\)/.exec(tm);
        if (mm) scale = parseFloat(mm[1].split(",")[0]);
      }
      return {
        w: rect ? Math.round(rect.width) : 0,
        h: rect ? Math.round(rect.height) : 0,
        scale,
        wanted: typeof terrainState !== "undefined" && Array.isArray(terrainState.wanted)
          ? terrainState.wanted.slice() : [],
        attachedKeys: host
          ? Array.from(host.querySelectorAll(".map-tile")).map((n) => n.getAttribute("data-key") || "")
          : [],
      };
    });

    const stamped = await page.evaluate(() => {
      const host = document.querySelector(".map-tiles");
      if (!host) return null;
      window.__m308Tiles = Array.from(host.querySelectorAll(".map-tile"));
      window.__m308Mut = [];
      window.__m308Obs = new MutationObserver((recs) => {
        for (const r of recs) {
          window.__m308Mut.push({ type: r.type, added: r.addedNodes.length, removed: r.removedNodes.length });
        }
      });
      window.__m308Obs.observe(host, { childList: true, subtree: false });
      return { count: window.__m308Tiles.length };
    });
    const stampView = await readView();
    results.push(check("poll-stability/" + label + "/stamp", !!stamped && stamped.count > 0,
      "stamped " + (stamped ? stamped.count : "null") + " .map-tile nodes; view=" + viewSnap(stampView)));
    const terrainMark = terrainLog.length;
    const readTarget = charReads + 2; // two REAL 5s poll intervals

    const pollViews = [];
    let seenReads = charReads;
    const pollDeadline = Date.now() + 25000;
    while (charReads < readTarget) {
      if (Date.now() > pollDeadline) break;
      await page.waitForTimeout(250);
      if (charReads > seenReads) {
        seenReads = charReads;
        pollViews.push(await readView());
      }
    }
    results.push(check("poll-stability/" + label + "/two-poll-intervals",
      charReads >= readTarget, "char reads target " + readTarget + " reached: " + charReads
      + "; poll views=" + JSON.stringify(pollViews)));

    const after = await page.evaluate(() => {
      const host = document.querySelector(".map-tiles");
      const now = host ? Array.from(host.querySelectorAll(".map-tile")) : [];
      const same = now.length === window.__m308Tiles.length
        && now.every((n, i) => n === window.__m308Tiles[i]);
      const muts = window.__m308Mut.slice();
      if (window.__m308Obs) window.__m308Obs.disconnect();
      const card = document.querySelector(".map-card");
      const stage = document.querySelector(".map-stage");
      const rect = card ? card.getBoundingClientRect() : null;
      let scale = 1;
      const tm = stage ? getComputedStyle(stage).transform : "";
      if (tm && tm !== "none") {
        const mm = /matrix\(([^)]+)\)/.exec(tm);
        if (mm) scale = parseFloat(mm[1].split(",")[0]);
      }
      return {
        same, count: now.length, muts,
        w: rect ? Math.round(rect.width) : 0,
        h: rect ? Math.round(rect.height) : 0,
        scale,
        wanted: typeof terrainState !== "undefined" && Array.isArray(terrainState.wanted)
          ? terrainState.wanted.slice() : [],
        attachedKeys: now.map((n) => n.getAttribute("data-key") || ""),
      };
    });
    results.push(check("poll-stability/" + label + "/node-identities-identical",
      after.same, "before=" + (stamped && stamped.count) + " after=" + after.count + " identical=" + after.same
      + "; stamp=" + viewSnap(stampView) + " after=" + viewSnap(after)));
    results.push(check("poll-stability/" + label + "/zero-childlist-mutations",
      after.muts.length === 0, after.muts.length + " mutation records " + JSON.stringify(after.muts.slice(0, 3))
      + "; stamp=" + viewSnap(stampView) + " after=" + viewSnap(after)));
    const newTileReqs = terrainLog.slice(terrainMark);
    results.push(check("poll-stability/" + label + "/zero-new-terrain-requests",
      newTileReqs.length === 0, newTileReqs.length + " new tile requests " + JSON.stringify(newTileReqs.slice(0, 3))
      + "; stamp=" + viewSnap(stampView) + " after=" + viewSnap(after)));

    const path = await shot("case-poll-stability-" + label + ".png");
    return { pass: results.every(Boolean), screenshot: path };
  }

  // =====================================================================
  // CASE: assertion 6 — failure paths
  // =====================================================================
  async function caseFailurePaths(label, w) {
    resetCaseState();
    const results = [];

    // (a) manifest 404 -> zero WebP requests, JPEG underlay intact
    manifestMode = "notfound";
    await loadWorld();
    await settleTerrain(false, 8000);
    await page.waitForTimeout(400); // let any stray late request land
    let tc = await tileCounts();
    let baseOk = await page.evaluate(() => {
      const i = document.querySelector(".map-base");
      return !!i && i.complete && i.naturalWidth > 0;
    });
    results.push(check("failure/" + label + "/manifest-404-zero-webp",
      terrainLog.filter((t) => t.url.endsWith(".webp")).length === 0 && tc.visible === 0,
      "webp=" + terrainLog.filter((t) => t.url.endsWith(".webp")).length + " tiles=" + tc.visible + " manifestStatus=" + manifestStatus));
    results.push(check("failure/" + label + "/manifest-404-underlay-intact", baseOk, "map-base decoded=" + baseOk));

    // (b) manifest invalid (200, wrong schema) -> same honest fallback
    resetCaseState();
    manifestMode = "invalid";
    await loadWorld();
    await settleTerrain(false, 8000);
    await page.waitForTimeout(400);
    tc = await tileCounts();
    baseOk = await page.evaluate(() => {
      const i = document.querySelector(".map-base");
      return !!i && i.complete && i.naturalWidth > 0;
    });
    results.push(check("failure/" + label + "/manifest-invalid-zero-webp",
      terrainLog.filter((t) => t.url.endsWith(".webp")).length === 0 && tc.visible === 0,
      "webp=" + terrainLog.filter((t) => t.url.endsWith(".webp")).length + " tiles=" + tc.visible + " manifestStatus=" + manifestStatus));
    results.push(check("failure/" + label + "/manifest-invalid-underlay-intact", baseOk, "map-base decoded=" + baseOk));

    // (c) lod2 available:false -> no lod2 requests; lod1 still works
    resetCaseState();
    manifestMode = "lod2-off";
    await loadWorld();
    await settleTerrain(true);
    const lod1AtIsland = terrainLog.filter((t) => t.layer === "lod1").length;
    results.push(check("failure/" + label + "/lod1-still-loads", lod1AtIsland > 0,
      lod1AtIsland + " lod1 requests at island with lod2 unavailable"));
    await page.locator(".living-map > .map-toolbar:not(.map-quality) button", { hasText: "Район" }).click();
    await waitForScale(expectedScale("region", w));
    await settleTerrain(false, 6000);
    await page.waitForTimeout(400);
    const lod2Reqs = terrainLog.filter((t) => t.layer === "lod2");
    const lod2Nodes = await page.evaluate(() => document.querySelectorAll('.map-tile[data-key^="lod2/"]').length);
    results.push(check("failure/" + label + "/available-false-zero-lod2-requests",
      lod2Reqs.length === 0, lod2Reqs.length + " lod2 requests " + JSON.stringify(lod2Reqs.slice(0, 3))));
    results.push(check("failure/" + label + "/available-false-zero-lod2-nodes",
      lod2Nodes === 0, lod2Nodes + " lod2 nodes in DOM"));
    baseOk = await page.evaluate(() => {
      const i = document.querySelector(".map-base");
      return !!i && i.complete && i.naturalWidth > 0;
    });
    results.push(check("failure/" + label + "/available-false-underlay-intact", baseOk, "map-base decoded=" + baseOk));

    // (d) one blocked tile -> only that core falls back; neighbours remain.
    // Probe boot first to learn a wanted key (deterministic per viewport).
    resetCaseState();
    await loadWorld();
    await settleTerrain(true);
    const wantedKey = await page.evaluate(() => (terrainState.wanted || [])[0] || null);
    if (!wantedKey) {
      results.push(check("failure/" + label + "/blocked-tile-wanted-key", false, "terrainState.wanted empty at island"));
    } else {
      // Scope the request log to the BLOCKED boot only: the probe boot above
      // already requested wantedKey once, and terrainLog is Node-side state
      // that accumulates across loadWorld()s (resetCaseState stays where it
      // is — it owns the other per-case knobs).
      terrainLog.length = 0;
      blockedTileKey = wantedKey;
      await loadWorld();
      await settleTerrain(true);
      // terrainLog is Node-scoped (filled by the route handler) — it does not
      // exist inside page.evaluate; count the blocked key here instead.
      const st = await page.evaluate(() => {
        const host = document.querySelector(".map-tiles");
        const keys = host ? Array.from(host.querySelectorAll(".map-tile")).map((n) => n.getAttribute("data-key")) : [];
        const imgs = host ? Array.from(host.querySelectorAll("img")) : [];
        return {
          keys,
          wanted: terrainState.wanted.slice(),
          failed: Array.from(terrainState.failed),
          broken: imgs.filter((i) => i.complete && i.naturalWidth === 0).length,
        };
      });
      const blockedRequested = terrainLog.filter((t) => t.key === wantedKey).length;
      const others = st.wanted.filter((k) => k !== wantedKey);
      const missingOthers = others.filter((k) => st.keys.indexOf(k) === -1);
      results.push(check("failure/" + label + "/blocked-tile-requested-once",
        blockedRequested === 1, wantedKey + " requested " + blockedRequested + " times"));
      results.push(check("failure/" + label + "/blocked-core-absent",
        st.keys.indexOf(wantedKey) === -1 && st.failed.indexOf(wantedKey) >= 0,
        "wanted=" + JSON.stringify(st.wanted) + " failed=" + JSON.stringify(st.failed)));
      results.push(check("failure/" + label + "/neighbours-remain",
        others.length > 0 && missingOthers.length === 0 && st.broken === 0,
        "others=" + JSON.stringify(others) + " missing=" + JSON.stringify(missingOthers) + " brokenImgs=" + st.broken));
    }

    const path = await shot("case-failure-" + label + ".png");
    return { pass: results.every(Boolean), screenshot: path };
  }

  // =====================================================================
  // CASE: assertion 7 — rapid switching with delayed tile responses
  // =====================================================================
  async function caseRapidSwitch(label, w) {
    resetCaseState();
    tileDelayMs = 250;
    const consoleMark = consoleErrors.length;
    await loadWorld();
    await settleTerrain(true);

    // island -> region -> local -> Scene, WITHOUT waiting for settle between
    // switches (delayed responses keep old requests in flight).
    await page.locator(".living-map > .map-toolbar:not(.map-quality) button", { hasText: "Район" }).click();
    await page.waitForTimeout(120);
    await page.locator(".living-map > .map-toolbar:not(.map-quality) button", { hasText: "Локально" }).click();
    await page.waitForTimeout(120);
    await waitForScale(expectedScale("local", w));
    const sceneBtn = page.locator(".living-map > .map-toolbar:not(.map-quality) button", { hasText: "Открыть Scene" });
    await sceneBtn.waitFor({ state: "visible", timeout: 4000 });
    await sceneBtn.click();
    await page.waitForTimeout(300);

    await settleTerrain(true, 25000);
    const st = await page.evaluate(() => {
      const host = document.querySelector(".map-tiles");
      const keys = host ? Array.from(host.querySelectorAll(".map-tile")).map((n) => n.getAttribute("data-key")) : [];
      const imgs = host ? Array.from(host.querySelectorAll("img")) : [];
      return {
        keys,
        broken: imgs.filter((i) => i.complete && i.naturalWidth === 0).length,
        pending: imgs.filter((i) => !i.complete).length,
      };
    });
    const results = [];
    const stale = st.keys.filter((k) => k.indexOf("lod3/") !== 0);
    results.push(check("rapid/" + label + "/no-stale-art", st.keys.length > 0 && stale.length === 0,
      st.keys.length + " tiles, stale=" + JSON.stringify(stale)));
    results.push(check("rapid/" + label + "/no-broken-image", st.broken === 0 && st.pending === 0,
      "broken=" + st.broken + " pending=" + st.pending));
    const newConsole = consoleErrors.slice(consoleMark)
      .filter((t) => !/Failed to load resource|net::ERR_ABORTED/.test(t));
    results.push(check("rapid/" + label + "/no-console-errors", newConsole.length === 0,
      newConsole.length ? JSON.stringify(newConsole.slice(0, 3)) : "0 new console errors"));

    tileDelayMs = 0;
    const path = await shot("case-rapid-" + label + ".png");
    return { pass: results.every(Boolean), screenshot: path };
  }

  // =====================================================================
  // CASE: assertion 8 — performance receipt (+ baseline + LOD screenshots)
  // =====================================================================
  async function casePerf(label, w, h) {
    resetCaseState();
    const perf = { viewport: label, baseline: null, runs: [], screenshots: [] };
    const results = [];

    const snapshot = async () => {
      const tc = await tileCounts();
      const frames = frameStats(await sampleFrames(90));
      return {
        requests: { terrain: counts.terrain, page: counts.page, api: counts.api },
        bytes: { terrain: bytes.terrain, page: bytes.page, api: bytes.api },
        cancellations,
        tiles: tc,
        frames,
      };
    };

    // Baseline: manifest route blocked -> single JPEG underlay only.
    manifestBlocked = true;
    await loadWorld();
    await settleTerrain(false, 8000);
    await page.waitForTimeout(300);
    perf.baseline = await snapshot();
    results.push(check("perf/" + label + "/baseline-zero-terrain",
      perf.baseline.requests.terrain <= 1 && perf.baseline.tiles.visible === 0 && perf.baseline.tiles.resident === 0,
      JSON.stringify({ requests: perf.baseline.requests, tiles: perf.baseline.tiles })));
    manifestBlocked = false;

    // Each quality preset: boot fresh, then apply the preset through the REAL
    // toolbar — the init script clears vl1.settings.v1 on every boot, so the
    // card always starts quality-balanced and no URL param seeds it.
    for (const [qKey, qLabel] of QUALITIES) {
      resetCaseState();
      bootSeq += 1;
      await page.goto(BASE + "/?m308=tiles&boot=" + bootSeq + "#/world", { waitUntil: "load" });
      await page.waitForFunction((clock) => {
        const c = document.getElementById("clock");
        return location.hash === "#/world" && !!document.querySelector(".world-shell")
          && !!document.querySelector(".map-card") && !!c && c.textContent === clock;
      }, CLOCK, { timeout: 10000 });
      await page.waitForFunction(() => {
        const i = document.querySelector(".map-base");
        return !!i && i.complete && i.naturalWidth > 0;
      }, undefined, { timeout: 10000 });
      await page.locator(".map-quality button", { hasText: qLabel }).click();
      await page.waitForFunction((q) => {
        const c = document.querySelector(".map-card");
        return !!c && c.classList.contains("quality-" + q);
      }, qKey, { timeout: 6000 });
      const cardQuality = await page.evaluate(() => {
        const c = document.querySelector(".map-card");
        return c ? Array.from(c.classList).find((x) => x.indexOf("quality-") === 0) : null;
      });
      results.push(check("perf/" + label + "/" + qKey + "/boot-preset",
        cardQuality === "quality-" + qKey,
        "preset-applied-via-ui; card class " + cardQuality));
      await settleTerrain(true);
      const rec = await snapshot();
      rec.quality = qKey;
      perf.runs.push(rec);
      results.push(check("perf/" + label + "/" + qKey + "/tiles-loaded",
        rec.tiles.visible > 0 && rec.tiles.decoded === rec.tiles.visible && rec.requests.terrain > 0,
        JSON.stringify({ requests: rec.requests, bytes: rec.bytes, tiles: rec.tiles })));
      results.push(check("perf/" + label + "/" + qKey + "/baseline-comparison",
        rec.bytes.terrain > perf.baseline.bytes.terrain && rec.requests.terrain > perf.baseline.requests.terrain,
        "terrain bytes " + perf.baseline.bytes.terrain + " -> " + rec.bytes.terrain
        + ", terrain requests " + perf.baseline.requests.terrain + " -> " + rec.requests.terrain));
    }

    // One screenshot per LOD per viewport (owner-visible receipt), balanced
    // preset, plus the frame/terrain numbers at each LOD.
    resetCaseState();
    await loadWorld();
    await settleTerrain(true);
    for (const [lodKey, lodLabel, layer] of LODS) {
      if (lodKey !== "island") {
        await page.locator(".living-map > .map-toolbar:not(.map-quality) button", { hasText: lodLabel }).click();
        await waitForScale(expectedScale(lodKey, w));
      }
      await settleTerrain(true);
      const tc = await tileCounts();
      results.push(check("perf/" + label + "/" + lodKey + "/screenshot-tiles",
        tc.visible > 0 && tc.decoded === tc.visible,
        JSON.stringify(tc)));
      const file = SHARED + "/lifesim-308-" + lodKey + "-" + label + ".png";
      await page.screenshot({ path: file, fullPage: false });
      perf.screenshots.push(file);
    }

    console.log("PERF " + JSON.stringify(perf));
    const path = await shot("case-perf-" + label + ".png");
    return { pass: results.every(Boolean), screenshot: path, perf };
  }

  // =====================================================================
  // run
  // =====================================================================
  async function run() {
    const report = {
      base: BASE, mode: "fixture", staticSha: null,
      cases: {}, checks: checkLog, requests, terrainLog,
      pageErrors, consoleErrors, unexpectedAPI, perf: [],
      screenshots: [], allPass: false,
    };

    await loadWorld();
    report.staticSha = await page.evaluate(async () => {
      if (!window.crypto || !window.crypto.subtle) {
        throw new Error("SETUP: WebCrypto (crypto.subtle) unavailable — cannot hash candidate bytes");
      }
      const digest = async (path) => {
        const response = await fetch(path);
        if (!response.ok) throw new Error("SETUP: candidate static fetch failed: " + path);
        const bytes = await response.arrayBuffer();
        const hash = await crypto.subtle.digest("SHA-256", bytes);
        return [...new Uint8Array(hash)].map((b) => b.toString(16).padStart(2, "0")).join("");
      };
      return { appJs: await digest("/app.js"), styleCss: await digest("/style.css") };
    });

    // Viewport selection: a supplied {width,height} matching one of the two
    // runs ONLY that viewport (lets the executor split 390 / 1440 into
    // separate invocations); absent — or non-matching — defaults to BOTH via
    // the same internal loop.
    const active = (viewport && typeof viewport.width === "number"
      && typeof viewport.height === "number")
      ? VIEWPORTS.filter(([w, h]) => w === viewport.width && h === viewport.height)
      : [];
    const runViewports = active.length ? active : VIEWPORTS.slice();
    for (const [w, h, label] of runViewports) {
      await page.setViewportSize({ width: w, height: h });
      report.cases["manifest-" + label] = await caseManifest(label);
      report.cases["matrix-" + label + "-os"] = await caseMatrix(label, w, h, "os");
      report.cases["matrix-" + label + "-app"] = await caseMatrix(label, w, h, "app");
      report.cases["poi-semantics-" + label] = await casePoiSemantics(label);
      report.cases["poll-stability-" + label] = await casePollStability(label);
      report.cases["failure-" + label] = await caseFailurePaths(label, w);
      report.cases["rapid-" + label] = await caseRapidSwitch(label, w);
      const perfCase = await casePerf(label, w, h);
      report.cases["perf-" + label] = perfCase;
      if (perfCase.perf) report.perf.push(perfCase.perf);
    }

    // The alignment oracle must actually have measured every EXPECTED POI
    // somewhere in the matrix (otherwise the skip path hid everything).
    // Viewport-aware expectation: in a 390-only run `home` is legitimately
    // unmeasurable — it is a house (excluded at island/region by #106 Lane B)
    // and at local its anchor center falls outside the narrow 390 card crop —
    // so the expected set drops it with an explicit, auditable reason. Any
    // run that includes 1440 must still measure all eight.
    const HOME_EXCLUSION = "home is a house: excluded at island/region by #106 and clipped at 390 local";
    const ran390 = runViewports.some(([, , l]) => l === "390");
    const ran1440 = runViewports.some(([, , l]) => l === "1440");
    const excludeHome = ran390 && !ran1440;
    const expectedPois = Object.keys(IMAGE_SPACE)
      .filter((k) => !(excludeHome && k === "home"));
    const missingPois = expectedPois.filter((k) => !seenPois.has(k));
    check("poi-alignment/all-eight-measured", missingPois.length === 0,
      "expected=" + JSON.stringify(expectedPois)
      + " measured=" + JSON.stringify([...seenPois])
      + " missing=" + JSON.stringify(missingPois)
      + (excludeHome ? "; excluded=" + HOME_EXCLUSION : ""));

    report.screenshots = Object.values(report.cases).map((c) => c.screenshot).filter(Boolean);
    const names = Object.keys(report.cases);
    const REQUIRED = 8 * runViewports.length; // (manifest + matrix x2 + poi + poll + failure + rapid + perf) x N viewports
    const realChecks = checkLog.filter((c) => c.status !== "SKIPPED");
    const checksPass = realChecks.length > 0 && realChecks.every((c) => c.pass);
    report.skipped = checkLog.filter((c) => c.status === "SKIPPED")
      .map((c) => ({ name: c.name, reason: c.reason }));
    report.allPass = names.length === REQUIRED && checksPass
      && unexpectedAPI.length === 0 && pageErrors.length === 0;
    if (!report.allPass) {
      const fails = realChecks.filter((c) => !c.pass)
        .map((c) => "FAIL: " + c.name + " — " + c.detail);
      const extras = [];
      if (names.length !== REQUIRED) extras.push("case count " + names.length + " != " + REQUIRED);
      if (unexpectedAPI.length) extras.push("unexpectedAPI: " + JSON.stringify(unexpectedAPI));
      if (pageErrors.length) extras.push("pageErrors: " + JSON.stringify(pageErrors));
      const err = new Error("m308 tiles FAIL — " + fails.length + " failed check(s)"
        + (extras.length ? "; " + extras.join("; ") : "") + "\n" + fails.join("\n"));
      err.report = report;
      throw err;
    }
    return report;
  }

  await setup();
  return await run();
})
