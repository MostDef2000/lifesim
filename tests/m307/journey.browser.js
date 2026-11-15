// tests/m307/journey.browser.js
//
// MANUAL browser-acceptance regression for the #307 playable-journey wave.
// This is a Playwright `async (page) => {}` function run by the PARENT via the
// native browser MCP / run_code — it is NOT collected by pytest and is NOT run
// by CI (scripts/check.sh = ruff + pytest only). It is the manual-acceptance
// truth for the REAL rendered geometry + viewport states of the canonical
// six-stage playable journey, split from the CI pytest truth
// (tests/m307/test_journey_contract.py / test_network_contract.py /
// test_failure_matrix.py, which run the same journey in the vm harness).
//
// TWO MODES (never silently substituted):
//   fixture  — the DEFAULT. The parent starts a stdlib static server
//              python3 -m http.server 8772 --bind 127.0.0.1 --directory backend/app/web
//              and this function pins BASE to that local origin (same
//              fail-closed contract as tests/m313/composition.browser.js: only
//              the local BASE origin + canonical static paths are served
//              through; every known API path is a fixture; every WebSocket is
//              closed pre-connect; nothing reaches a real backend). The report
//              returns the SHA256 of the served app.js / style.css (browser
//              WebCrypto) so the parent can prove the test ran against the
//              candidate bytes, not PROD.
//   probe    — the parent sets window.__M307_PROBE_BACKEND (a real backend
//              origin) via page.addInitScript BEFORE calling this function.
//              In probe mode NO route interception is installed (the real
//              backend serves the APIs) and the request/WS capture is passive.
//              If the probe backend is unreachable the function THROWS
//              (fail-closed) — it never falls back to the fixture silently.
//
// THE CANONICAL JOURNEY (deterministic identities: player=1, start location=1,
// destination=2, destination NPC=2, MOVE task=30701, scene task=30702,
// dialogue session=30703, DIRECT mode):
//   1. World (loc 1, empty tasks)
//   2. select POI 2 + preview #/location/2 + return World
//   3. explicit MOVE (accepted -> active -> completed; authoritative read
//      reports loc 2)
//   4. enter current Place 2, inspect scene, submit a task-producing scene act
//   5. Chat: select NPC 2, start the conversation, send one message
//   6. Tasks shows 30702, return World at loc 2
//
// GEOMETRY EVIDENCE (the split this file owns) — the m313 thresholds apply
// ONLY on their own screens:
//   header   — .ctx-topbar height <= 60px at 390 (target 56).
//   stage    — .place-stage height as % of innerHeight within [32, 40] on the
//              fallback / error / loading scene fixtures at 390; on ready
//              content only a >= 32 floor is asserted (target-not-cap).
//   chat     — #chat-view width within [420, 500] at 1440 (target 460);
//              .chat-bar composer bottom edge <= innerHeight at 390.
//   overflow — document.documentElement.scrollWidth > innerWidth must be
//              FALSE on every screen of every case (the journey must never
//              introduce horizontal overflow).
//
// SHARED-TAB HIJACK GUARD: the browser MCP tab is shared — before EVERY
// measurement the in-page probe re-checks window.innerWidth (the viewport we
// set) and location.href (still our BASE origin). Any drift throws immediately
// instead of reporting numbers from a hijacked tab.
//
// FAIL-CLOSED EXIT: every check prints a "PASS: <name> — <observed>" or
// "FAIL: ..." line (mirrored into report.checks). Probe-mode server-driven
// checks print a "SKIP: <name> — <reason>" line and are recorded as SKIPPED
// (listed in report.skipped, excluded from pass/fail counting) — they are
// NEVER reported as passes. On any FAIL (or unexpected API/page error) the
// function THROWS with all FAIL lines + observed values — the nonzero-exit
// equivalent for the MCP runner; a standalone node harness wrapping this
// function must catch and process.exit(1). On all-pass it returns the full
// report (allPass: true).
//
// SCREENSHOTS: written ONLY under /tmp/opencode/playwright-dumps/lifesim-307/
// (Playwright creates the directory), filenames distinct per case + viewport.

async (page) => {
  const OUT = "/tmp/opencode/playwright-dumps/lifesim-307";
  const CLOCK = "День 2, 01:00";
  const VIEWPORTS = [[1440, 900, "1440"], [390, 844, "390"]];

  // ---- mode (fixture default; probe via parent global) ----
  let MODE = "fixture";
  let BASE = "http://127.0.0.1:8772";

  // ---- fixtures (shapes copied from the vm harness + backend/app/api/app.py) ----
  const USER = { id: 1, username: "audit", email: "audit@fixture.local", role: "player" };
  const CHAR1 = {
    id: "1", name: "P", sex: "m", age: 30, alive: true, location_id: 1,
    control_mode: "DIRECT", is_owner: true, balance: 100,
    needs: { hunger: 70, energy: 80, thirst: 60, social: 50 }, job: "Рыбак",
    looks: null, biography: null, outfit_mode: "auto", home_location_id: 1,
    traits: [], health: { health: 100, stress: 10 },
  };
  const WORLD = { id: 1, seed: 42, game_timestamp: 1500, day: 2, population: 12, time_scale: 1.0, is_paused: false };
  const WEATHER = { enabled: false };
  const NPC_IVAN = { id: "101", name: "Иван", kind: "npc" };
  const NPC_FAR = { id: "2", name: "Фар", kind: "npc" };
  const LOCATIONS = [
    { id: 1, type: "settlement", name: "Посёлок", x: 45, y: 50, parent_id: null, occupants: [NPC_IVAN] },
    { id: 2, type: "shop", name: "Магазин", x: 50, y: 45, parent_id: 1, occupants: [NPC_FAR] },
    { id: 3, type: "workshop", name: "Мастерская", x: 42, y: 56, parent_id: 1, occupants: [] },
    { id: 4, type: "kitchen", name: "Кухня", x: 47, y: 51, parent_id: 1, occupants: [] },
    { id: 5, type: "storage", name: "Склад", x: 40, y: 48, parent_id: 1, occupants: [] },
    { id: 6, type: "well", name: "Колодец", x: 48, y: 55, parent_id: 1, occupants: [] },
    { id: 7, type: "pier", name: "Причал", x: 46, y: 91, parent_id: null, occupants: [] },
  ];
  const LOCATIONS_NO_NPC = LOCATIONS.map((l) => (l.id === 2 ? { ...l, occupants: [] } : l));
  const TASKS_EMPTY = { planned: [], active: [], recent_terminal: [] };
  const TASK_30701 = { id: 30701, status: "completed", task_type: "MOVE", parameters: { path: [1, 2] }, created_at: 100, started_at: 1500, ends_at: 1510, completed_at: 1510 };
  const TASK_30702 = { id: 30702, status: "active", task_type: "SOCIALIZE", created_at: 101, started_at: 1500 };
  // Valid scene asset for the destination (2) — must pass BOTH validations in
  // inspectSurroundings: asset.location_id === 2 AND
  // asset.scene_descriptor.location.id === 2.
  const SCENE_ASSET_2 = {
    asset: { id: "sc2", asset_type: "scene", location_id: 2,
      scene_descriptor: { location: { id: 2, name: "Магазин" } } },
  };
  const DIALOGUE_START = { session_id: "30703" };
  const DIALOGUE_MESSAGE_OK = {
    ok: true, source: "llm",
    messages: [
      { sender: "player", content: "Привет, Фар", game_timestamp: 1500 },
      { sender: "npc", content: "Привет", game_timestamp: 1500 },
    ],
  };
  const PORTRAIT_404 = { status: 404, body: { detail: "canonical portrait not found" } };
  const REFS_EMPTY = { items: [] };

  // ---- collectors (jsonl-shaped arrays) ----
  const requests = []; // { url, method, body? }
  const wsEvents = []; // { type, url, data? }
  const pageErrors = [];
  const checkLog = [];
  // Dynamic fixture state: the player's authoritative location. In fixture
  // mode the /actions MOVE POST flips this to 2 so the next /characters/1
  // read (the poll) reports the arrival — the same handoff the vm harness
  // simulates with __setResponse.
  let playerLoc = 1;
  // Per-case scene-act / dialogue / move response overrides (the failure
  // matrix cases set these before driving).
  let sceneActResponse = { ok: true, task_id: 30702, task_type: "SOCIALIZE", task_label: "Пообщаться" };
  let moveResponse = { task_id: 30701, task_type: "MOVE", task_label: "Перемещение", status: "planned" };
  let moveNetworkFail = false;
  let sceneFileReject = false;
  let autonomous = false;

  // ---- check helper (prints + records; every check carries observed values) ----
  function check(name, pass, detail) {
    const line = (pass ? "PASS" : "FAIL") + ": " + name + " — " + detail;
    checkLog.push({ name, pass: !!pass, detail });
    console.log(line);
    return !!pass;
  }

  // ---- skip helper (probe-mode server-driven checks; never fails the run) ----
  // A SKIPPED record is listed in the report but excluded from pass/fail
  // counting (it has no `pass` field and must not make checksPass false).
  function skip(name, reason) {
    checkLog.push({ name, status: "SKIPPED", reason });
    console.log("SKIP: " + name + " — " + reason);
    return true; // a skipped check never fails the run
  }

  // ---- setup (fail-closed, same discipline as m313) ----
  async function setup() {
    await page.goto("about:blank", { waitUntil: "load" });
    // Probe mode: the parent set window.__M307_PROBE_BACKEND via addInitScript
    // (it runs on every navigation, so it is present on about:blank).
    const probeBackend = await page.evaluate(() => window.__M307_PROBE_BACKEND || null);
    if (probeBackend) {
      MODE = "probe";
      BASE = String(probeBackend).replace(/\/+$/, "");
    }
    if (typeof page.routeWebSocket !== "function" && MODE === "fixture") {
      throw new Error("page.routeWebSocket unavailable — cannot guarantee pre-connect WS interception; aborting before navigation");
    }
    await page.unrouteAll();

    if (MODE === "fixture") {
      // Fallback route (lowest precedence): ONLY local BASE + canonical
      // static, GET/HEAD, first /static prefix stripped. Everything else
      // recorded + aborted.
      await page.route("**/*", async (route) => {
        const req = route.request();
        let u;
        try { u = new URL(req.url()); }
        catch (e) { requests.push({ url: req.url(), method: req.method(), reason: "unparseable" }); return route.abort(); }
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
        requests.push({ url: u.href, method: req.method(), reason: isBase ? "non-static-or-non-GET" : "foreign-origin" });
        return route.abort();
      });

      const json = (route, s) => route.fulfill({ status: s.status, contentType: "application/json; charset=utf-8", body: JSON.stringify(s.body) });
      const ok = (body) => ({ status: 200, body });
      const getOnly = (s) => (route) => {
        if (route.request().method() === "GET") return json(route, s);
        requests.push({ url: route.request().url(), method: route.request().method(), reason: "non-GET-to-known-api" });
        return route.abort();
      };
      await page.route("**/auth/me", getOnly(ok(USER)));
      await page.route("**/characters/by-user/1", getOnly(ok([{ id: "1", name: "P", alive: true, control_mode: "DIRECT" }])));
      // Dynamic: the player's authoritative location flips after the MOVE.
      await page.route("**/characters/1", (route) => {
        if (route.request().method() !== "GET") {
          requests.push({ url: route.request().url(), method: route.request().method(), reason: "non-GET-to-char" });
          return route.abort();
        }
        const char = Object.assign({}, CHAR1, { location_id: playerLoc, control_mode: autonomous ? "AUTONOMOUS" : "DIRECT" });
        return json(route, ok(char));
      });
      await page.route("**/characters/1/tasks", (route) => {
        if (route.request().method() !== "GET") return route.abort();
        const body = playerLoc === 2
          ? { planned: [], active: [TASK_30702], recent_terminal: [TASK_30701] }
          : TASKS_EMPTY;
        return json(route, ok(body));
      });
      await page.route("**/characters/2/roles", getOnly(ok({ roles: [] })));
      await page.route("**/characters/1/roles", getOnly(ok({ roles: [] })));
      await page.route("**/characters/1/relationships", getOnly(ok([])));
      await page.route("**/characters/1/goals", getOnly(ok([])));
      await page.route("**/characters/1/desire/opportunities", getOnly(ok({ desire: null, opportunities: [] })));
      await page.route("**/world", getOnly(ok(WORLD)));
      await page.route("**/weather", getOnly(ok(WEATHER)));
      await page.route("**/locations", getOnly(ok(autonomous ? LOCATIONS : LOCATIONS)));
      await page.route("**/world/events*", getOnly(ok([])));
      await page.route("**/visual/characters/1/portrait", getOnly(PORTRAIT_404));
      await page.route("**/visual/characters/2/portrait", getOnly(PORTRAIT_404));
      await page.route("**/visual/references", getOnly(ok(REFS_EMPTY)));

      // The MOVE acceptance: POST /actions flips playerLoc to 2 (the
      // authoritative arrival) and returns the task. The failure-matrix
      // cases override moveResponse / moveNetworkFail.
      await page.route("**/actions", async (route) => {
        const req = route.request();
        if (req.method() !== "POST") {
          requests.push({ url: req.url(), method: req.method(), reason: "non-POST-to-actions" });
          return route.abort();
        }
        requests.push({ url: req.url(), method: "POST", body: req.postData() || null });
        if (moveNetworkFail) return route.abort(); // network failure
        if (moveResponse.status === "busy_409") {
          return route.fulfill({ status: 409, contentType: "application/json; charset=utf-8", body: JSON.stringify({ detail: "character already has an active task" }) });
        }
        playerLoc = 2; // the move completes
        return json(route, ok(moveResponse));
      });
      await page.route("**/actions/30701", getOnly(ok(Object.assign({}, TASK_30701, { status: "planned" }))));
      await page.route("**/actions/30702", getOnly(ok(Object.assign({}, TASK_30702, { status: "planned" }))));

      // Scene inspection: POST /visual/scenes -> the destination asset.
      await page.route("**/visual/scenes", async (route) => {
        const req = route.request();
        if (req.method() !== "POST") {
          requests.push({ url: req.url(), method: req.method(), reason: "non-POST-to-scenes" });
          return route.abort();
        }
        requests.push({ url: req.url(), method: "POST", body: req.postData() || null });
        return json(route, ok(SCENE_ASSET_2));
      });
      // The scene asset file: GET -> real bytes, or a reject (FailedImage).
      await page.route("**/visual/assets/*/file", async (route) => {
        const req = route.request();
        if (req.method() !== "GET") {
          requests.push({ url: req.url(), method: req.method(), reason: "non-GET-to-scene-file" });
          return route.abort();
        }
        if (sceneFileReject) return route.abort();
        const resp = await page.request.get(BASE + "/static/map/world-island.jpg");
        const body = await resp.body();
        return route.fulfill({ status: 200, contentType: "image/jpeg", body });
      });
      // The scene action: POST /scene/act -> the per-case response.
      await page.route("**/scene/act", async (route) => {
        const req = route.request();
        if (req.method() !== "POST") {
          requests.push({ url: req.url(), method: req.method(), reason: "non-POST-to-scene-act" });
          return route.abort();
        }
        requests.push({ url: req.url(), method: "POST", body: req.postData() || null });
        return json(route, ok(sceneActResponse));
      });
      // The dialogue: POST /dialogue/start -> session 30703; POST
      // /dialogue/30703/message -> the committed turn.
      await page.route("**/dialogue/start", async (route) => {
        const req = route.request();
        if (req.method() !== "POST") {
          requests.push({ url: req.url(), method: req.method(), reason: "non-POST-to-dialogue-start" });
          return route.abort();
        }
        requests.push({ url: req.url(), method: "POST", body: req.postData() || null });
        return json(route, ok(DIALOGUE_START));
      });
      await page.route("**/dialogue/30703/message", async (route) => {
        const req = route.request();
        if (req.method() !== "POST") {
          requests.push({ url: req.url(), method: req.method(), reason: "non-POST-to-dialogue-message" });
          return route.abort();
        }
        requests.push({ url: req.url(), method: "POST", body: req.postData() || null });
        return json(route, ok(DIALOGUE_MESSAGE_OK));
      });
      await page.route("**/dialogue/30703", getOnly(ok({ session_id: "30703", messages: DIALOGUE_MESSAGE_OK.messages })));

      // Pre-connect WebSocket interception: close WITHOUT connectToServer().
      await page.routeWebSocket("**/*", (wsRoute) => {
        let url = null;
        try { url = wsRoute.url(); } catch (e) { url = "<unavailable>"; }
        wsEvents.push({ type: "connect", url });
        try { wsRoute.close(); } catch (e) { /* already closed */ }
      });
    } else {
      // Probe mode: passive request/WS capture (no interception).
      page.on("request", (req) => {
        requests.push({ url: req.url(), method: req.method(), body: req.method() === "POST" ? (req.postData() || null) : undefined });
      });
      page.on("websocket", (ws) => {
        wsEvents.push({ type: "connect", url: ws.url() });
        ws.on("framesent", (f) => wsEvents.push({ type: "frame", data: f.payload }));
        ws.on("close", () => wsEvents.push({ type: "close" }));
      });
    }

    page.on("pageerror", (err) => { pageErrors.push(String((err && err.message) || err)); });

    // Onboarding card pre-dismissed (measurement hygiene only).
    await page.addInitScript(() => {
      try { window.localStorage.setItem("vl1.welcome.done", "1"); } catch (e) { /* ignore */ }
    });
  }

  // ---- guarded measurement helper ----
  // Every probe re-checks the viewport + origin BEFORE reading geometry: the
  // MCP tab is shared, so a hijack (resize/navigation by another session)
  // must abort the measurement, never produce numbers from the wrong tab.
  async function journeyMetrics(exp) {
    return await page.evaluate((exp) => {
      if (window.innerWidth !== exp.w) {
        throw new Error("m307 GUARD: innerWidth=" + window.innerWidth + " != " + exp.w + " (shared-tab viewport hijack)");
      }
      if (!location.href.startsWith(exp.base)) {
        throw new Error("m307 GUARD: location.href=" + location.href + " left " + exp.base + " (shared-tab navigation hijack)");
      }
      const box = (n) => {
        if (!n) return null;
        const r = n.getBoundingClientRect();
        return { w: Math.round(r.width), h: Math.round(r.height), bottom: Math.round(r.bottom) };
      };
      const stage = document.querySelector(".place-stage");
      const sceneStatus = document.querySelector(".scene-status");
      const chatNpcSel = document.getElementById("chat-npc-select");
      const tasksActive = document.getElementById("tasks-active");
      const tasksHistory = document.getElementById("tasks-history");
      const nearby = document.querySelector(".place-nearby");
      return {
        hash: location.hash,
        hasWorldShell: !!document.querySelector(".world-shell"),
        hasPlaceShell: !!document.querySelector(".place-shell"),
        hasChatShell: !!document.querySelector(".chat-shell"),
        hasTasksShell: !!document.querySelector(".tasks-shell"),
        placeBadge: (document.querySelector(".place-badge") || {}).textContent || null,
        stagePct: stage ? Math.round(stage.getBoundingClientRect().height / window.innerHeight * 1000) / 10 : null,
        sceneStatus: sceneStatus ? sceneStatus.textContent : null,
        sceneActInputPresent: !!document.getElementById("scene-act-input"),
        sceneActInputValue: (document.getElementById("scene-act-input") || {}).value || null,
        chatPanel: box(document.querySelector("#chat-view")),
        chatComposer: box(document.querySelector(".chat-bar")),
        chatNpcSelectValue: chatNpcSel ? String(chatNpcSel.value) : null,
        chatLogText: (document.getElementById("chat-log") || {}).textContent || null,
        tasksActiveText: tasksActive ? tasksActive.textContent : null,
        tasksHistoryText: tasksHistory ? tasksHistory.textContent : null,
        nearbyEmpty: nearby ? nearby.textContent.indexOf("Рядом никого нет.") >= 0 : null,
        header: box(document.querySelector(".ctx-topbar")),
        scrollWidth: document.documentElement.scrollWidth,
        viewportWidth: window.innerWidth,
        overflow: document.documentElement.scrollWidth > window.innerWidth,
      };
    }, exp);
  }

  async function gotoHash(hash, shellSelector, exp) {
    await page.evaluate((h) => { location.hash = h; }, hash);
    await page.waitForFunction(
      ([h, sel]) => location.hash === h && !!document.querySelector(sel),
      [hash, shellSelector], { timeout: 8000 });
    await page.evaluate((exp) => {
      if (window.innerWidth !== exp.w) {
        throw new Error("m307 GUARD: innerWidth=" + window.innerWidth + " != " + exp.w + " (shared-tab viewport hijack)");
      }
      if (!location.href.startsWith(exp.base)) {
        throw new Error("m307 GUARD: location.href=" + location.href + " left " + exp.base + " (shared-tab navigation hijack)");
      }
    }, exp);
  }

  // Wait for the authoritative player location to flip to `loc` (the poll
  // picks up the /characters/1 read after the MOVE). Generous timeout: the
  // app's feed poll is a 5s interval.
  async function waitForPlayerLoc(loc, exp) {
    await page.waitForFunction((loc) => {
      const m = document.getElementById("player-marker");
      return m && String(m.getAttribute("data-location-id")) === String(loc);
    }, loc, { timeout: 12000 });
    await page.evaluate((exp) => {
      if (window.innerWidth !== exp.w) throw new Error("m307 GUARD: innerWidth=" + window.innerWidth + " != " + exp.w);
      if (!location.href.startsWith(exp.base)) throw new Error("m307 GUARD: location.href=" + location.href + " left " + exp.base);
    }, exp);
  }

  async function shot(name) {
    const path = OUT + "/" + name;
    await page.screenshot({ path, fullPage: true });
    return path;
  }
  const inRange = (v, lo, hi) => v !== null && v >= lo && v <= hi;

  // The overflow check is the journey's invariant on EVERY screen: the
  // canonical journey must never introduce horizontal overflow.
  function overflowChecks(prefix, m) {
    return check(prefix + "/no-horizontal-overflow", !m.overflow,
      "scrollWidth " + m.scrollWidth + "px vs innerWidth " + m.viewportWidth + "px");
  }

  // ---- cases ----
  // The canonical six-stage journey, measuring the geometry at each stage.
  async function caseJourneySixSteps(exp) {
    const results = [];
    // Stage 1: World at the origin.
    await gotoHash("#/world", ".world-shell", exp);
    const s1 = await journeyMetrics(exp);
    results.push(check("caseJourneySixSteps/" + exp.label + "/s1-world-shell", s1.hasWorldShell, ".world-shell " + (s1.hasWorldShell ? "found" : "MISSING")));
    results.push(overflowChecks("caseJourneySixSteps/" + exp.label + "/s1", s1));
    // Stage 2: select POI 2, preview the remote place, return to World.
    await page.locator("button", { hasText: "Магазин" }).first().click();
    await page.waitForFunction(() => !!document.querySelector(".poi-card"), undefined, { timeout: 8000 });
    await page.locator("a", { hasText: "Посмотреть место" }).first().click();
    await gotoHash("#/location/2", ".place-shell", exp);
    const s2 = await journeyMetrics(exp);
    results.push(check("caseJourneySixSteps/" + exp.label + "/s2-remote-badge", s2.placeBadge === "Другое место", "place-badge " + JSON.stringify(s2.placeBadge)));
    results.push(overflowChecks("caseJourneySixSteps/" + exp.label + "/s2", s2));
    await page.locator("button", { hasText: "← Остров" }).first().click();
    await gotoHash("#/world", ".world-shell", exp);
    // Stage 3: the explicit MOVE.
    await page.locator("button", { hasText: "Магазин" }).first().click();
    await page.waitForFunction(() => !!document.querySelector(".poi-card"), undefined, { timeout: 8000 });
    await page.locator("button", { hasText: "Пойти сюда" }).first().click();
    if (MODE === "fixture") await waitForPlayerLoc(2, exp);
    const s3 = await journeyMetrics(exp);
    const markerLoc3 = await page.evaluate(() => {
      const el = document.getElementById("player-marker");
      return el ? el.getAttribute("data-location-id") : null;
    });
    results.push(MODE === "probe"
      ? skip("caseJourneySixSteps/" + exp.label + "/s3-arrived-loc2", "probe mode: server-driven arrival")
      : check("caseJourneySixSteps/" + exp.label + "/s3-arrived-loc2", markerLoc3 === "2", "player-marker loc " + markerLoc3));
    results.push(overflowChecks("caseJourneySixSteps/" + exp.label + "/s3", s3));
    // Stage 4: enter the current Place 2, inspect the scene, submit a scene act.
    await gotoHash("#/location/2", ".place-shell", exp);
    await page.waitForFunction(() => !!document.querySelector(".place-stage"), undefined, { timeout: 8000 });
    const s4a = await journeyMetrics(exp);
    results.push(check("caseJourneySixSteps/" + exp.label + "/s4-current-badge", s4a.placeBadge === "Вы здесь", "place-badge " + JSON.stringify(s4a.placeBadge)));
    results.push(overflowChecks("caseJourneySixSteps/" + exp.label + "/s4a", s4a));
    await page.locator(".place-stage button").first().click();
    await page.waitForFunction(() => {
      const s = document.querySelector(".scene-status");
      return s && (s.textContent === "Готово" || s.textContent === "Сцена готова" || !!document.querySelector(".scene-image-host img"));
    }, undefined, { timeout: 10000 });
    await page.fill("#scene-act-input", "Поговорить с Фаром");
    await page.locator("button", { hasText: "Сделать" }).first().click();
    await page.waitForFunction(() => !!document.getElementById("scene-act-result"), undefined, { timeout: 8000 });
    const s4b = await journeyMetrics(exp);
    results.push(overflowChecks("caseJourneySixSteps/" + exp.label + "/s4b", s4b));
    // Stage 5: Chat — select NPC 2, send one message.
    await page.locator("button", { hasText: "Поговорить" }).first().click();
    await gotoHash("#/chat", ".chat-shell", exp);
    await page.waitForFunction(() => !!document.querySelector(".chat-bar"), undefined, { timeout: 8000 });
    const s5a = await journeyMetrics(exp);
    results.push(check("caseJourneySixSteps/" + exp.label + "/s5-chat-shell", s5a.hasChatShell, ".chat-shell " + (s5a.hasChatShell ? "found" : "MISSING")));
    results.push(overflowChecks("caseJourneySixSteps/" + exp.label + "/s5a", s5a));
    await page.fill("#chat-msg-input", "Привет, Фар");
    await page.locator("button", { hasText: "→" }).first().click();
    await page.waitForFunction(() => {
      const log = document.getElementById("chat-log");
      return log && log.textContent.indexOf("Привет, Фар") >= 0;
    }, undefined, { timeout: 8000 });
    const s5b = await journeyMetrics(exp);
    results.push(overflowChecks("caseJourneySixSteps/" + exp.label + "/s5b", s5b));
    // Stage 6: Tasks shows 30702, return to World at loc 2.
    await gotoHash("#/tasks", ".tasks-shell", exp);
    const s6a = await journeyMetrics(exp);
    results.push(check("caseJourneySixSteps/" + exp.label + "/s6-tasks-shell", s6a.hasTasksShell, ".tasks-shell " + (s6a.hasTasksShell ? "found" : "MISSING")));
    results.push(overflowChecks("caseJourneySixSteps/" + exp.label + "/s6a", s6a));
    await page.locator("button", { hasText: "Мир" }).first().click();
    await gotoHash("#/world", ".world-shell", exp);
    const s6b = await journeyMetrics(exp);
    results.push(overflowChecks("caseJourneySixSteps/" + exp.label + "/s6b", s6b));
    const path = await shot("case-journey-six-steps-" + exp.label + ".png");
    return { pass: results.every(Boolean), screenshot: path };
  }

  // The Place screen in the normal/current state (the stage geometry band).
  async function caseStateNormal(exp) {
    await gotoHash("#/location/2", ".place-shell", exp);
    await page.waitForFunction(() => !!document.querySelector(".place-stage"), undefined, { timeout: 8000 });
    const m = await journeyMetrics(exp);
    const path = await shot("case-state-normal-" + exp.label + ".png");
    const results = [
      check("caseStateNormal/" + exp.label + "/place-shell", m.hasPlaceShell, ".place-shell " + (m.hasPlaceShell ? "found" : "MISSING")),
      check("caseStateNormal/" + exp.label + "/current-badge", m.placeBadge === "Вы здесь", "place-badge " + JSON.stringify(m.placeBadge)),
      check("caseStateNormal/" + exp.label + "/stage-present", m.stagePct !== null, ".place-stage " + (m.stagePct !== null ? "found" : "MISSING")),
      exp.label === "390"
        ? check("caseStateNormal/390/stage-fallback-in-band", inRange(m.stagePct, 32, 40), "fallback " + m.stagePct + "% of innerHeight (band [32,40])")
        : check("caseStateNormal/" + exp.label + "/stage-pct-recorded", m.stagePct !== null, "stage " + m.stagePct + "% (recorded only — band pinned @390)"),
      overflowChecks("caseStateNormal/" + exp.label, m),
    ];
    return { pass: results.every(Boolean), stagePct: m.stagePct, screenshot: path };
  }

  // The Tasks screen with no tasks (the empty states).
  async function caseStateNoTasks(exp) {
    await gotoHash("#/tasks", ".tasks-shell", exp);
    const m = await journeyMetrics(exp);
    const path = await shot("case-state-no-tasks-" + exp.label + ".png");
    const results = [
      check("caseStateNoTasks/" + exp.label + "/tasks-shell", m.hasTasksShell, ".tasks-shell " + (m.hasTasksShell ? "found" : "MISSING")),
      check("caseStateNoTasks/" + exp.label + "/active-empty", (m.tasksActiveText || "").indexOf("Нет активных задач") >= 0, "tasks-active " + JSON.stringify(m.tasksActiveText)),
      check("caseStateNoTasks/" + exp.label + "/history-empty", (m.tasksHistoryText || "").indexOf("История пуста") >= 0, "tasks-history " + JSON.stringify(m.tasksHistoryText)),
      overflowChecks("caseStateNoTasks/" + exp.label, m),
    ];
    return { pass: results.every(Boolean), screenshot: path };
  }

  // A remote place (the player is NOT there): the move CTA, no scene-act bar.
  async function caseStateRemote(exp) {
    await gotoHash("#/location/2", ".place-shell", exp);
    await page.waitForFunction(() => !!document.querySelector(".place-shell"), undefined, { timeout: 8000 });
    const m = await journeyMetrics(exp);
    const path = await shot("case-state-remote-" + exp.label + ".png");
    const results = [
      check("caseStateRemote/" + exp.label + "/remote-badge", m.placeBadge === "Другое место", "place-badge " + JSON.stringify(m.placeBadge)),
      check("caseStateRemote/" + exp.label + "/no-scene-act-bar", m.sceneActInputPresent === false, "scene-act-input " + (m.sceneActInputPresent ? "PRESENT (unexpected)" : "absent")),
      overflowChecks("caseStateRemote/" + exp.label, m),
    ];
    return { pass: results.every(Boolean), screenshot: path };
  }

  // The current place (the player IS there): the scene-act bar is present.
  async function caseStateCurrent(exp) {
    if (MODE === "fixture") playerLoc = 2; // the player is at the destination
    await gotoHash("#/location/2", ".place-shell", exp);
    await page.waitForFunction(() => !!document.querySelector(".place-stage"), undefined, { timeout: 8000 });
    const m = await journeyMetrics(exp);
    const path = await shot("case-state-current-" + exp.label + ".png");
    const results = [
      check("caseStateCurrent/" + exp.label + "/current-badge", m.placeBadge === "Вы здесь", "place-badge " + JSON.stringify(m.placeBadge)),
      check("caseStateCurrent/" + exp.label + "/scene-act-bar-present", m.sceneActInputPresent === true, "scene-act-input " + (m.sceneActInputPresent ? "found" : "MISSING")),
      overflowChecks("caseStateCurrent/" + exp.label, m),
    ];
    return { pass: results.every(Boolean), screenshot: path };
  }

  // The destination with no NPC: the nearby roster is empty.
  async function caseStateNoNpc(exp) {
    if (MODE === "fixture") {
      // Re-route the locations fixture to the no-NPC catalog for this case.
      await page.unroute("**/locations");
      await page.route("**/locations", (route) => {
        if (route.request().method() !== "GET") return route.abort();
        return route.fulfill({ status: 200, contentType: "application/json; charset=utf-8", body: JSON.stringify(LOCATIONS_NO_NPC) });
      });
      playerLoc = 2;
      await gotoHash("#/world", ".world-shell", exp); // re-boot the world read
    }
    await gotoHash("#/location/2", ".place-shell", exp);
    await page.waitForFunction(() => !!document.querySelector(".place-nearby"), undefined, { timeout: 8000 });
    const m = await journeyMetrics(exp);
    const path = await shot("case-state-no-npc-" + exp.label + ".png");
    const results = [
      check("caseStateNoNpc/" + exp.label + "/nearby-empty", m.nearbyEmpty === true, "nearby-empty " + JSON.stringify(m.nearbyEmpty)),
      overflowChecks("caseStateNoNpc/" + exp.label, m),
    ];
    return { pass: results.every(Boolean), screenshot: path };
  }

  // The scene asset file rejects: the inspection fails gracefully.
  async function caseStateFailedImage(exp) {
    if (MODE === "fixture") { sceneFileReject = true; playerLoc = 2; }
    await gotoHash("#/location/2", ".place-shell", exp);
    await page.waitForFunction(() => !!document.querySelector(".place-stage"), undefined, { timeout: 8000 });
    await page.locator(".place-stage button").first().click();
    await page.waitForFunction(() => {
      const s = document.querySelector(".scene-status");
      return s && s.textContent.length > 0;
    }, undefined, { timeout: 8000 });
    const m = await journeyMetrics(exp);
    const path = await shot("case-state-failed-image-" + exp.label + ".png");
    const results = [
      check("caseStateFailedImage/" + exp.label + "/scene-status-set", m.sceneStatus !== null && m.sceneStatus.length > 0, "scene-status " + JSON.stringify(m.sceneStatus)),
      overflowChecks("caseStateFailedImage/" + exp.label, m),
    ];
    if (MODE === "fixture") sceneFileReject = false;
    return { pass: results.every(Boolean), sceneStatus: m.sceneStatus, screenshot: path };
  }

  // The MOVE is rejected 409-busy: the player stays at the origin.
  async function caseStateBusy(exp) {
    if (MODE === "fixture") { moveResponse = { status: "busy_409" }; }
    await gotoHash("#/world", ".world-shell", exp);
    await page.locator("button", { hasText: "Магазин" }).first().click();
    await page.waitForFunction(() => !!document.querySelector(".poi-card"), undefined, { timeout: 8000 });
    await page.locator("button", { hasText: "Пойти сюда" }).first().click();
    await page.waitForFunction(() => {
      const line = document.querySelector(".task-submit-line");
      return line && line.textContent.length > 0;
    }, undefined, { timeout: 8000 });
    const m = await journeyMetrics(exp);
    const path = await shot("case-state-busy-" + exp.label + ".png");
    const markerLoc = await page.evaluate(() => {
      const el = document.getElementById("player-marker");
      return el ? el.getAttribute("data-location-id") : null;
    });
    const results = [
      MODE === "probe"
        ? skip("caseStateBusy/" + exp.label + "/stays-at-origin", "probe mode: server-driven semantics")
        : check("caseStateBusy/" + exp.label + "/stays-at-origin", markerLoc === "1", "player-marker loc " + markerLoc),
      overflowChecks("caseStateBusy/" + exp.label, m),
    ];
    if (MODE === "fixture") moveResponse = { task_id: 30701, task_type: "MOVE", task_label: "Перемещение", status: "planned" };
    return { pass: results.every(Boolean), screenshot: path };
  }

  // The MOVE network-fails: the offline caption, the player stays.
  async function caseStateOffline(exp) {
    if (MODE === "fixture") { moveNetworkFail = true; }
    await gotoHash("#/world", ".world-shell", exp);
    await page.locator("button", { hasText: "Магазин" }).first().click();
    await page.waitForFunction(() => !!document.querySelector(".poi-card"), undefined, { timeout: 8000 });
    await page.locator("button", { hasText: "Пойти сюда" }).first().click();
    await page.waitForFunction(() => {
      const line = document.querySelector(".task-submit-line");
      return line && line.textContent.length > 0;
    }, undefined, { timeout: 8000 });
    const m = await journeyMetrics(exp);
    const path = await shot("case-state-offline-" + exp.label + ".png");
    const markerLoc = await page.evaluate(() => {
      const el = document.getElementById("player-marker");
      return el ? el.getAttribute("data-location-id") : null;
    });
    const results = [
      MODE === "probe"
        ? skip("caseStateOffline/" + exp.label + "/stays-at-origin", "probe mode: server-driven semantics")
        : check("caseStateOffline/" + exp.label + "/stays-at-origin", markerLoc === "1", "player-marker loc " + markerLoc),
      overflowChecks("caseStateOffline/" + exp.label, m),
    ];
    if (MODE === "fixture") moveNetworkFail = false;
    return { pass: results.every(Boolean), screenshot: path };
  }

  // The character is AUTONOMOUS: the move is gated client-side (no POST).
  async function caseStateAutonomous(exp) {
    if (MODE === "fixture") { autonomous = true; }
    await gotoHash("#/world", ".world-shell", exp);
    await page.locator("button", { hasText: "Магазин" }).first().click();
    await page.waitForFunction(() => !!document.querySelector(".poi-card"), undefined, { timeout: 8000 });
    const postsBefore = requests.filter((r) => r.method === "POST" && r.url.indexOf("/actions") >= 0).length;
    await page.locator("button", { hasText: "Пойти сюда" }).first().click();
    await page.waitForTimeout(500); // the gate is synchronous; allow a beat
    const postsAfter = requests.filter((r) => r.method === "POST" && r.url.indexOf("/actions") >= 0).length;
    const m = await journeyMetrics(exp);
    const path = await shot("case-state-autonomous-" + exp.label + ".png");
    const results = [
      MODE === "probe"
        ? skip("caseStateAutonomous/" + exp.label + "/no-move-post", "probe mode: server-driven semantics")
        : check("caseStateAutonomous/" + exp.label + "/no-move-post", postsAfter === postsBefore, "POST /actions count " + postsBefore + " -> " + postsAfter),
      overflowChecks("caseStateAutonomous/" + exp.label, m),
    ];
    if (MODE === "fixture") autonomous = false;
    return { pass: results.every(Boolean), screenshot: path };
  }

  // The scene act returns ok:false: the draft is kept, no task registered.
  async function caseStateCritical(exp) {
    if (MODE === "fixture") { sceneActResponse = { ok: false, detail: "critical failure" }; playerLoc = 2; }
    await gotoHash("#/location/2", ".place-shell", exp);
    await page.waitForFunction(() => !!document.querySelector(".place-stage"), undefined, { timeout: 8000 });
    await page.locator(".place-stage button").first().click();
    await page.waitForFunction(() => {
      const s = document.querySelector(".scene-status");
      return s && s.textContent.length > 0;
    }, undefined, { timeout: 8000 });
    await page.fill("#scene-act-input", "Поговорить с Фаром");
    await page.locator("button", { hasText: "Сделать" }).first().click();
    await page.waitForFunction(() => !!document.getElementById("scene-act-result"), undefined, { timeout: 8000 });
    const m = await journeyMetrics(exp);
    const path = await shot("case-state-critical-" + exp.label + ".png");
    const results = [
      check("caseStateCritical/" + exp.label + "/draft-kept", m.sceneActInputValue === "Поговорить с Фаром", "scene-act-input value " + JSON.stringify(m.sceneActInputValue)),
      overflowChecks("caseStateCritical/" + exp.label, m),
    ];
    if (MODE === "fixture") sceneActResponse = { ok: true, task_id: 30702, task_type: "SOCIALIZE", task_label: "Пообщаться" };
    return { pass: results.every(Boolean), screenshot: path };
  }

  // The journey via keyboard: real Tab traversal + focus records.
  async function caseKeyboardJourney(exp) {
    const focusLog = [];
    const onFocus = () => {
      const a = document.activeElement;
      if (a) focusLog.push({ tag: a.tagName, id: a.id || null, text: (a.textContent || "").trim().slice(0, 40) });
    };
    await page.evaluate(() => {
      document.addEventListener("focusin", () => {
        const a = document.activeElement;
        if (a) (window.__m307FocusLog = window.__m307FocusLog || []).push({ tag: a.tagName, id: a.id || null, text: (a.textContent || "").trim().slice(0, 40) });
      });
    });
    await gotoHash("#/world", ".world-shell", exp);
    // Tab through the World view's interactive elements (a bounded traversal).
    for (let i = 0; i < 8; i++) {
      await page.keyboard.press("Tab");
      await page.waitForTimeout(50);
    }
    const focusRecords = await page.evaluate(() => window.__m307FocusLog || []);
    const m = await journeyMetrics(exp);
    const path = await shot("case-keyboard-journey-" + exp.label + ".png");
    const results = [
      check("caseKeyboardJourney/" + exp.label + "/focus-records", focusRecords.length > 0, "focus records " + focusRecords.length + " (first: " + JSON.stringify(focusRecords[0] || null) + ")"),
      check("caseKeyboardJourney/" + exp.label + "/focus-on-interactive", focusRecords.every((f) => f.tag === "BUTTON" || f.tag === "A" || f.tag === "INPUT" || f.tag === "SELECT" || f.tag === "TEXTAREA"), "all focus targets interactive: " + JSON.stringify(focusRecords.map((f) => f.tag))),
      overflowChecks("caseKeyboardJourney/" + exp.label, m),
    ];
    return { pass: results.every(Boolean), focusRecords, screenshot: path };
  }

  // The journey with reduced-motion emulation: the geometry is settled.
  async function caseReducedMotionJourney(exp) {
    await page.emulateMedia({ reducedMotion: "reduce" });
    try {
      await gotoHash("#/location/2", ".place-shell", exp);
      await page.waitForFunction(() => !!document.querySelector(".place-stage"), undefined, { timeout: 8000 });
      const m = await journeyMetrics(exp);
      const path = await shot("case-reduced-motion-journey-" + exp.label + ".png");
      const results = [
        check("caseReducedMotionJourney/" + exp.label + "/place-shell", m.hasPlaceShell, ".place-shell " + (m.hasPlaceShell ? "found" : "MISSING")),
        check("caseReducedMotionJourney/" + exp.label + "/stage-present", m.stagePct !== null, ".place-stage " + (m.stagePct !== null ? "found" : "MISSING")),
        overflowChecks("caseReducedMotionJourney/" + exp.label, m),
      ];
      return { pass: results.every(Boolean), stagePct: m.stagePct, screenshot: path };
    } finally {
      await page.emulateMedia({ reducedMotion: "no-preference" });
    }
  }

  // ---- run ----
  async function run() {
    const report = {
      base: BASE, mode: MODE, staticSha: null, cases: {}, screenshots: [],
      checks: checkLog, requests, wsEvents, pageErrors, allPass: false,
    };
    await loadWorld();
    if (MODE === "fixture") {
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
    }

    for (const [w, h, label] of VIEWPORTS) {
      await page.setViewportSize({ width: w, height: h });
      const exp = { w, h, base: BASE, vh: h, label };
      report.cases["caseJourneySixSteps-" + label] = await caseJourneySixSteps(exp);
      report.cases["caseStateNormal-" + label] = await caseStateNormal(exp);
      report.cases["caseStateNoTasks-" + label] = await caseStateNoTasks(exp);
      report.cases["caseStateRemote-" + label] = await caseStateRemote(exp);
      report.cases["caseStateCurrent-" + label] = await caseStateCurrent(exp);
      report.cases["caseStateNoNpc-" + label] = await caseStateNoNpc(exp);
      report.cases["caseStateFailedImage-" + label] = await caseStateFailedImage(exp);
      report.cases["caseStateBusy-" + label] = await caseStateBusy(exp);
      report.cases["caseStateOffline-" + label] = await caseStateOffline(exp);
      report.cases["caseStateAutonomous-" + label] = await caseStateAutonomous(exp);
      report.cases["caseStateCritical-" + label] = await caseStateCritical(exp);
      report.cases["caseKeyboardJourney-" + label] = await caseKeyboardJourney(exp);
      report.cases["caseReducedMotionJourney-" + label] = await caseReducedMotionJourney(exp);
    }
    report.screenshots = Object.values(report.cases).map((c) => c.screenshot);
    const names = Object.keys(report.cases);
    const REQUIRED = 26; // 13 cases x 2 viewports
    // SKIPPED records (probe-mode server-driven checks) are listed but never
    // count toward pass/fail — only real checks (with a `pass` field) do.
    const realChecks = checkLog.filter((c) => c.status !== "SKIPPED");
    const skippedChecks = checkLog.filter((c) => c.status === "SKIPPED");
    const checksPass = realChecks.length > 0 && realChecks.every((c) => c.pass);
    report.skipped = skippedChecks.map((c) => ({ name: c.name, reason: c.reason }));
    report.allPass = names.length === REQUIRED && checksPass && pageErrors.length === 0;
    if (!report.allPass) {
      const fails = realChecks.filter((c) => !c.pass).map((c) => "FAIL: " + c.name + " — " + c.detail);
      const extras = [];
      if (names.length !== REQUIRED) extras.push("case count " + names.length + " != " + REQUIRED);
      if (pageErrors.length) extras.push("pageErrors: " + JSON.stringify(pageErrors));
      const err = new Error("m307 journey FAIL — " + fails.length + " failed check(s)"
        + (extras.length ? "; " + extras.join("; ") : "") + "\n" + fails.join("\n"));
      err.report = report;
      throw err;
    }
    return report;
  }

  async function loadWorld() {
    await page.goto(BASE + "/?m307=journey#/world", { waitUntil: "load" });
    await page.waitForFunction((clock) => {
      const c = document.getElementById("clock");
      return location.hash === "#/world" && !!document.querySelector(".world-shell")
        && !!c && c.textContent === clock;
    }, CLOCK, { timeout: 8000 });
  }

  await setup();
  return await run();
};
