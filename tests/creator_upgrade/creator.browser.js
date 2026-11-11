// tests/creator_upgrade/creator.browser.js
//
// MANUAL browser-acceptance harness for the 020 wave-4 creator upgrade
// (issue #316) — a Playwright `async (page) => {}` function run by the
// PARENT via the native browser MCP / run_code. It is NOT collected by
// pytest and NOT run by CI (scripts/check.sh = ruff + pytest only).
// Pattern: tests/m270/world_first_view.browser.js.
//
// LOCAL CANDIDATE BYTES (never PROD): the parent starts a stdlib static
// server
//   python3 -m http.server 8771 --bind 127.0.0.1 --directory backend/app/web
// and this function pins BASE to that local origin. The real candidate
// index.html / app.js / style.css are served via GET only (the app
// references /static/app.js etc.; the first /static prefix is stripped
// before the transparent fetch, exactly as in m270). The report returns
// the SHA256 of the served app.js / style.css (browser WebCrypto, no
// deps) so the parent can prove the test ran against the candidate bytes.
//
// FAIL-CLOSED CONTRACT (m270 discipline):
// - Static: ONLY the local BASE origin + canonical static paths, GET/HEAD,
//   transparently fulfilled from the local server. /favicon.ico is a
//   browser-chrome request — answered 404 WITHOUT recording. Every other
//   request (any origin, any non-canonical path, any non-GET/HEAD) is
//   recorded + aborted.
// - API: /auth/me + /characters/by-user are GET-only fixtures; the three
//   wave-4 surfaces (POST /characters/draft, POST /visual/preview, POST
//   /characters) are mocked with recorded request bodies; the m270 world
//   fixtures back the post-create world handoff. Every other request is
//   recorded + aborted. No request ever reaches a real backend.
// - WebSocket: PRE-CONNECT interception via page.routeWebSocket, installed
//   BEFORE navigation; every WS is closed WITHOUT connectToServer(). If
//   page.routeWebSocket is unavailable we THROW before any navigation.
// - No real accounts/credentials/production calls. Only creator-form
//   interactions (typing + button clicks) and hash navigation.
//
// CASES:
//   1 generate-one-preview : one-click generate fills ALL 19 draft values
//                            and fires EXACTLY ONE automatic preview
//   2 variant-single-field : a «вариант» click changes ONLY its field,
//                            marks the image stale, never auto-previews
//   3 typing-discards      : a keystroke during in-flight generate
//                            discards the response (revision guard)
//   4 failed-preview       : a failed regen keeps the previous image and
//                            labels it «предыдущая версия»
//   5 create-body          : POST /characters carries outfit_mode +
//                            custom_clothing (atomic create)
//   6 layout-shots         : 1280px + 390px screenshots WITH a generated
//                            preview rendered; no overflow (incl. the
//                            appearance panel-edge probe)
//   7 unmount-discard      : navigating away mid-flight discards the
//                            in-flight generate response (unmount guard)
//
// SCREENSHOTS: /tmp/opencode/lifesim-creator-frontend-01/desktop.png and
// mobile.png (parent mkdirs the directory).

async (page) => {
  const BASE = "http://127.0.0.1:8771";
  const OUT = "/tmp/opencode/lifesim-creator-frontend-01";

  // A real 512x768 preview PNG drawn on a canvas at runtime (no deps, no
  // binary fixture): the mocked /visual/preview returns it, so the case-6
  // screenshots show an actually-rendered image instead of the 1px
  // placeholder.
  const previewPng = await page.evaluate(() => {
    const c = document.createElement("canvas");
    c.width = 512;
    c.height = 768;
    const g = c.getContext("2d");
    if (!g) throw new Error("SETUP: canvas 2d context unavailable");
    const sky = g.createLinearGradient(0, 0, 0, 768);
    sky.addColorStop(0, "#31465c");
    sky.addColorStop(1, "#101820");
    g.fillStyle = sky;
    g.fillRect(0, 0, 512, 768);
    g.fillStyle = "#d9cfc0";  // head + shoulders silhouette
    g.beginPath();
    g.arc(256, 300, 96, 0, Math.PI * 2);
    g.fill();
    g.beginPath();
    g.moveTo(120, 768);
    g.quadraticCurveTo(256, 470, 392, 768);
    g.fill();
    g.fillStyle = "#c8beac";
    g.font = "600 30px sans-serif";
    g.textAlign = "center";
    g.fillText("ПРЕВЬЮ ПОРТРЕТА", 256, 728);
    return c.toDataURL("image/png").split(",")[1];
  });

  // ---- fixtures (shapes from backend/app/api/app.py + creator_routes.py) ----
  const USER = { id: 1, username: "audit", email: "audit@fixture.local", role: "player" };
  const BYUSER = [];  // no character yet -> the creator screen renders
  // A full valid 19-key draft (the /characters/draft generate response).
  const DRAFT = {
    name: "Мира Соколова", sex: "F", age: 34,
    face: "высокие скулы", eyes: "серые глаза", nose: "прямой нос",
    lips: "узкие губы", hair: "короткая стрижка", hair_color: "русые",
    skin: "светлая кожа", height: "170 см", weight: "60 кг",
    build: "жилистое телосложение", chest: "средняя грудь",
    features: "шрам над левой бровью",
    supplement: "ветровка и блокнот в кармане",
    biography: "Приехала с материка за тихой жизнью у моря.",
    outfit_mode: "casual",
    custom_clothing: "worn canvas jacket over a knit sweater",
  };
  const VARIANT_EYES = "зелёные миндалевидные глаза";
  // Minimal create response — exactly what POST /characters returns.
  const CREATED = { id: 1, name: "Мира Соколова", control_mode: "MANUAL",
    location_id: 1, looks: null, biography: null };
  // m270-shaped world fixtures for the post-create handoff.
  const CHAR1 = {
    id: 1, name: "Мира Соколова", sex: "f", age: 30, alive: true, location_id: 1,
    control_mode: "MANUAL", is_owner: true, balance: 100,
    needs: { hunger: 70, energy: 80, thirst: 60, social: 50 }, job: "Рыбак",
    looks: null, biography: null, outfit_mode: "casual", home_location_id: 1,
    traits: [], health: { health: 100, stress: 10 },
  };
  const WORLD = { id: 1, seed: 42, game_timestamp: 1500, day: 1, population: 12, time_scale: 1.0, is_paused: false };
  const WEATHER = {
    enabled: true, day: 1, temperature: 18, wind: 3, precipitation: 0.1,
    cloudiness: 0.3, visibility: 1.0, source: "generated",
    real_date: "2026-11-11", description: "+18°C, ясно, ветер 3 м/с",
  };
  const LOCATIONS = [
    { id: 1, type: "settlement", name: "Посёлок", x: 45, y: 50, parent_id: null, occupants_count: 1,
      occupants: [{ id: 1, name: "Мира Соколова", kind: "player" }] },
    { id: 2, type: "shop", name: "Магазин", x: 50, y: 45, parent_id: 1, occupants_count: 0, occupants: [] },
    { id: 3, type: "workshop", name: "Мастерская", x: 42, y: 56, parent_id: 1, occupants_count: 0, occupants: [] },
    { id: 4, type: "kitchen", name: "Кухня", x: 47, y: 51, parent_id: 1, occupants_count: 0, occupants: [] },
    { id: 5, type: "storage", name: "Склад", x: 40, y: 48, parent_id: 1, occupants_count: 0, occupants: [] },
    { id: 6, type: "well", name: "Колодец", x: 48, y: 55, parent_id: 1, occupants_count: 0, occupants: [] },
    { id: 7, type: "pier", name: "Причал", x: 46, y: 91, parent_id: null, occupants_count: 0, occupants: [] },
    { id: 8, type: "house", name: "House 1", x: 44, y: 47, parent_id: 1, occupants_count: 0, occupants: [] },
  ];
  const LOC1_DETAIL = { id: 1, type: "settlement", name: "Посёлок", characters_here: [1] };
  const EVENTS = [];
  const TASKS_EMPTY = { active: [], planned: [], recent_terminal: [] };
  const PORTRAIT_404 = { status: 404, body: { detail: "canonical portrait not found" } };
  const REFS_EMPTY = { items: [] };
  // 1x1 PNG (transparent) — a valid decodable image for <img>.
  const PNG_1PX = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==";

  // ---- collectors + mock switches ----
  const unexpectedAPI = [];
  const pageErrors = [];
  const wsBlocks = [];
  const draftCalls = [];    // POST /characters/draft bodies
  const previewCalls = [];  // POST /visual/preview bodies
  const createCalls = [];   // POST /characters bodies
  let draftDelayMs = 0;     // case 3: hold the draft response in flight
  let previewShouldFail = false;  // case 4: fail the next preview call(s)
  let loadNonce = 0;        // per-load cache-buster (see loadCreator)

  // ---- setup ----
  async function setup() {
    await page.goto("about:blank", { waitUntil: "load" });
    if (typeof page.routeWebSocket !== "function") {
      throw new Error("page.routeWebSocket unavailable — cannot guarantee pre-connect WS interception; aborting before navigation");
    }
    await page.unrouteAll();

    // Fallback (installed FIRST -> lowest precedence): local static only.
    await page.route("**/*", async (route) => {
      const req = route.request();
      let u;
      try { u = new URL(req.url()); }
      catch (e) { unexpectedAPI.push({ url: req.url(), method: req.method(), reason: "unparseable" }); return route.abort(); }
      const isBase = u.origin === BASE;
      if (isBase && u.pathname === "/favicon.ico") {
        return route.fulfill({ status: 404, contentType: "text/plain", body: "no favicon" });
      }
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

    const json = (route, s) => route.fulfill({ status: s.status, contentType: "application/json; charset=utf-8", body: JSON.stringify(s.body) });
    const ok = (body) => ({ status: 200, body });
    const getOnly = (s) => (route) => {
      if (route.request().method() === "GET") return json(route, s);
      unexpectedAPI.push({ url: route.request().url(), method: route.request().method(), reason: "non-GET-to-known-api" });
      return route.abort();
    };
    // POST /characters registered FIRST (lowest API precedence) — the more
    // specific /characters/* routes below win over it.
    await page.route("**/characters", async (route) => {
      const req = route.request();
      if (req.method() === "POST") {
        createCalls.push(JSON.parse(req.postData() || "{}"));
        return json(route, ok(CREATED));
      }
      unexpectedAPI.push({ url: req.url(), method: req.method(), reason: "non-POST-to-characters" });
      return route.abort();
    });
    await page.route("**/characters/draft", async (route) => {
      const req = route.request();
      if (req.method() !== "POST") {
        unexpectedAPI.push({ url: req.url(), method: req.method(), reason: "non-POST-to-draft" });
        return route.abort();
      }
      const body = JSON.parse(req.postData() || "{}");
      draftCalls.push(body);
      if (draftDelayMs > 0) await new Promise((r) => setTimeout(r, draftDelayMs));
      const resp = body.operation === "generate"
        ? { draft: DRAFT, revision: body.revision }
        : { field: body.field, value: VARIANT_EYES, revision: body.revision };
      return json(route, ok(resp));
    });
    await page.route("**/visual/preview", async (route) => {
      const req = route.request();
      if (req.method() !== "POST") {
        unexpectedAPI.push({ url: req.url(), method: req.method(), reason: "non-POST-to-preview" });
        return route.abort();
      }
      previewCalls.push(JSON.parse(req.postData() || "{}"));
      if (previewShouldFail) {
        return route.fulfill({ status: 503, contentType: "application/json; charset=utf-8",
          body: JSON.stringify({ detail: "preview rate limit exceeded" }) });
      }
      return json(route, ok({ prompt: "preview prompt of record", seed: 424242, image_base64: previewPng }));
    });
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

    // Pre-connect WS interception: close WITHOUT connectToServer().
    await page.routeWebSocket("**/*", (wsRoute) => {
      let url = null;
      try { url = wsRoute.url(); } catch (e) { url = "<unavailable>"; }
      wsBlocks.push({ url });
      try { wsRoute.close(); } catch (e) { /* already closed */ }
    });

    page.on("pageerror", (err) => { pageErrors.push(String((err && err.message) || err)); });
  }

  // ---- helpers ----
  async function until(fn, ms, what) {
    const t0 = Date.now();
    while (Date.now() - t0 < ms) {
      if (await fn()) return;  // await: predicates may be async (formState)
      await page.waitForTimeout(50);
    }
    throw new Error("timeout waiting for " + what);
  }
  async function loadCreator() {
    // fresh document per case + reset mock state. The nonce in the QUERY
    // (not the fragment) is load-bearing: a goto that only changes the
    // hash (e.g. #/world -> #/create after case 5) is a SAME-DOCUMENT
    // navigation — the old document survives with S.character already
    // set, and viewCreateCharacter redirects to #/profile. A unique query
    // forces a full reload, so every case starts from a clean module
    // state (S.character === null -> the creator renders).
    draftCalls.length = 0; previewCalls.length = 0; createCalls.length = 0;
    draftDelayMs = 0; previewShouldFail = false;
    await page.goto(BASE + "/?case=creator&n=" + (++loadNonce) + "#/create",
      { waitUntil: "load" });
    try {
      await page.waitForFunction(() => !!document.querySelector(".creator-shell"),
        undefined, { timeout: 8000 });
    } catch (e) {
      // diagnostic dump — the harness must never fail with a bare timeout
      const diag = await page.evaluate(() => ({
        url: location.href, hash: location.hash,
        appChildren: (document.getElementById("app") || {}).childElementCount ?? -1,
        hasShell: !!document.querySelector(".creator-shell"),
      })).catch((err) => ({ diagError: String(err) }));
      throw new Error("loadCreator: creator shell did not render; diag="
        + JSON.stringify(diag) + "; pageErrors=" + JSON.stringify(pageErrors));
    }
  }
  // The full creator form state, read from the live DOM (the harness never
  // trusts app internals — only rendered values + hidden flags).
  async function formState() {
    return await page.evaluate(() => {
      const shell = document.querySelector(".creator-shell");
      const sections = [...shell.querySelectorAll("section")];
      const sec = (h) => sections.find((s) => {
        const h2 = s.querySelector("h2"); return h2 && h2.textContent === h; });
      const who = sec("Кто вы");
      const outfitSec = sec("Одежда");
      const previewSec = sec("Предпросмотр");
      const aiSec = shell.querySelector(".creator-ai");
      const appearance = {};
      for (const inp of shell.querySelectorAll(".creator-appearance input")) {
        appearance[inp.getAttribute("aria-label")] = inp.value;
      }
      const byPlaceholder = (ph) => [...shell.querySelectorAll("textarea")]
        .find((t) => t.getAttribute("placeholder") === ph);
      const looks = byPlaceholder("Внешность (необязательно)");
      const bio = byPlaceholder("Биография (необязательно)");
      const idea = aiSec ? aiSec.querySelector("textarea") : null;
      const custom = outfitSec.querySelector("textarea[maxlength='500']");
      const img = previewSec.querySelector(".preview-image");
      const stale = previewSec.querySelector(".creator-stale");
      const promptPre = previewSec.querySelector(".creator-prompt-text");
      const promptRev = previewSec.querySelector(".creator-prompt-rev");
      const promptDetails = previewSec.querySelector(".creator-prompt");
      const versionNote = [...previewSec.querySelectorAll(".creator-note")]
        .find((p) => p.textContent === "предыдущая версия");
      const aiErr = aiSec ? aiSec.querySelector(".err-text") : null;
      return {
        name: shell.querySelector("input[placeholder='Имя Фамилия']").value,
        sex: who.querySelector("select").value,
        age: who.querySelector("input[type=number]").value,
        appearance,
        looks: looks ? looks.value : null,
        bio: bio ? bio.value : null,
        idea: idea ? idea.value : null,
        outfit: outfitSec.querySelector("select").value,
        custom: custom ? custom.value : null,
        imgHidden: img ? img.hidden : null,
        imgSrc: img && img.src ? img.src : null,
        staleHidden: stale ? stale.hidden : null,
        promptHidden: promptDetails ? promptDetails.hidden : null,
        promptText: promptPre ? promptPre.textContent : null,
        promptRev: promptRev ? promptRev.textContent : null,
        versionHidden: versionNote ? versionNote.hidden : null,
        aiErr: aiErr ? aiErr.textContent : null,
        overflow: document.documentElement.scrollWidth > window.innerWidth,
      };
    });
  }
  async function shot(name) {
    const path = OUT + "/" + name;
    await page.screenshot({ path, fullPage: true });
    return path;
  }

  // ---- cases ----
  // (1) one-click generate fills ALL 19 draft values + EXACTLY ONE preview.
  async function case1Generate() {
    await loadCreator();
    const IDEA = "Рыбацкая вдова 40 лет, вяжет сети";
    await page.fill(".creator-ai textarea", IDEA);
    await page.click(".creator-ai button.primary");
    await until(() => draftCalls.length === 1 && previewCalls.length === 1,
      6000, "generate + one preview");
    // the request counts above land BEFORE the response is rendered —
    // wait for the applied state too (name filled, image shown).
    await until(async () => {
      const s = await formState();
      return s.name === DRAFT.name && s.imgHidden === false;
    }, 6000, "generate applied to the form");
    const st = await formState();
    const pass =
      st.name === DRAFT.name && st.sex === DRAFT.sex && st.age === String(DRAFT.age)
      && st.appearance["Лицо"] === DRAFT.face && st.appearance["Глаза"] === DRAFT.eyes
      && st.appearance["Нос"] === DRAFT.nose && st.appearance["Губы"] === DRAFT.lips
      && st.appearance["Волосы"] === DRAFT.hair && st.appearance["Цвет волос"] === DRAFT.hair_color
      && st.appearance["Кожа"] === DRAFT.skin && st.appearance["Рост"] === DRAFT.height
      && st.appearance["Вес"] === DRAFT.weight && st.appearance["Телосложение"] === DRAFT.build
      && st.appearance["Грудь"] === DRAFT.chest
      && st.appearance["Дополнительные особенности"] === DRAFT.features
      && st.looks === DRAFT.supplement && st.bio === DRAFT.biography
      && st.outfit === DRAFT.outfit_mode && st.custom === DRAFT.custom_clothing
      && draftCalls.length === 1 && previewCalls.length === 1
      && draftCalls[0].operation === "generate" && draftCalls[0].idea === IDEA
      && st.imgHidden === false && !st.overflow;
    return { name: "generate-one-preview", pass, draftCalls: draftCalls.length,
      previewCalls: previewCalls.length, imgShown: st.imgHidden === false };
  }

  // (2) a «вариант» click changes ONLY its field; image marked stale; no auto-preview.
  async function case2Variant() {
    await loadCreator();
    await page.fill("input[placeholder='Имя Фамилия']", "Тест Человек");
    await page.fill("input[type=number]", "30");
    const before = await formState();
    await page.click(".creator-field:has(label:text-is('Глаза')) .creator-variant");
    await until(() => draftCalls.length === 1, 6000, "variant call");
    await until(async () => (await formState()).appearance["Глаза"] === VARIANT_EYES,
      6000, "variant applied");
    const after = await formState();
    const othersUnchanged =
      before.name === after.name && before.age === after.age
      && before.looks === after.looks && before.bio === after.bio
      && before.outfit === after.outfit && before.custom === after.custom
      && Object.keys(before.appearance).every((k) =>
        k === "Глаза" || before.appearance[k] === after.appearance[k]);
    const body = draftCalls[0] || {};
    // Review fix: the variant request must carry the CANONICAL server draft
    // key — the camelCase grid key (hairColor) reaches the wire as the
    // snake_case DRAFT_FIELDS entry (hair_color), or the server rejects it
    // with "unknown draft field". The mock echoes the requested field back,
    // so the applied value proves the round trip.
    await page.click(".creator-field:has(label:text-is('Цвет волос')) .creator-variant");
    await until(() => draftCalls.length === 2, 6000, "hair variant call");
    await until(async () => (await formState()).appearance["Цвет волос"] === VARIANT_EYES,
      6000, "hair variant applied");
    const body2 = draftCalls[1] || {};
    const pass =
      body.operation === "variant" && body.field === "eyes"
      && body.draft && body.draft.name === "Тест Человек" && body.draft.age === 30
      && after.appearance["Глаза"] === VARIANT_EYES && othersUnchanged
      && body2.operation === "variant" && body2.field === "hair_color"
      && after.staleHidden === false && previewCalls.length === 0 && !after.overflow;
    return { name: "variant-single-field", pass, field: body.field,
      hairField: body2.field,
      staleShown: after.staleHidden === false, previewCalls: previewCalls.length };
  }

  // (3) a keystroke during in-flight generate discards the response.
  // The keystroke lands in «Внешность» — a draft-capturing field whose
  // listener bumps the revision (the guard's documented trigger).
  async function case3TypingDiscards() {
    await loadCreator();
    draftDelayMs = 400;  // hold the response in flight
    await page.fill(".creator-ai textarea", "Медленный помощник");
    await page.click(".creator-ai button.primary");
    await page.waitForTimeout(120);  // the request is now in flight
    await page.fill("textarea[placeholder='Внешность (необязательно)']",
      "Ручная правка внешности");
    await page.waitForTimeout(600);  // the held response arrives + settles
    const st = await formState();
    const pass =
      draftCalls.length === 1
      && st.looks === "Ручная правка внешности"  // the typed value survived
      && st.appearance["Глаза"] === ""  // the proposal was NOT applied
      && (st.aiErr || "").includes("предложение не применено: анкета изменилась")
      && previewCalls.length === 0 && !st.overflow;
    return { name: "typing-discards", pass, aiErr: st.aiErr,
      typedLooks: st.looks, previewCalls: previewCalls.length };
  }

  // (4) a failed regen keeps the previous image and labels it.
  async function case4FailedPreview() {
    await loadCreator();
    await page.fill("input[placeholder='Имя Фамилия']", "Портретный Тест");
    await page.click(".creator-preview button:has-text('Создать предпросмотр')");
    await until(() => previewCalls.length === 1, 6000, "first preview");
    // the request count lands BEFORE the response is rendered — wait for
    // the applied state (image visible with a data: src) before snapshot.
    await until(async () => {
      const s = await formState();
      return s.imgHidden === false && (s.imgSrc || "").startsWith("data:");
    }, 6000, "first preview rendered");
    const good = await formState();
    previewShouldFail = true;
    await page.click(".creator-preview button:has-text('Обновить')");
    await until(() => previewCalls.length === 2, 6000, "failed regen");
    // wait for the failure surface: the retained-image label is shown.
    await until(async () => (await formState()).versionHidden === false,
      6000, "failed regen labeled");
    const after = await formState();
    const pass =
      good.imgHidden === false && good.imgSrc
      && after.imgHidden === false && after.imgSrc === good.imgSrc
      && after.versionHidden === false
      && (after.aiErr === null || after.aiErr === "")  // the AI panel stays quiet
      && !after.overflow;
    return { name: "failed-preview-retains", pass,
      imgRetained: after.imgSrc === good.imgSrc,
      versionLabeled: after.versionHidden === false,
      promptShown: good.promptHidden === false && (good.promptText || "").length > 0
        && (good.promptRev || "").includes("Ревизия анкеты") };
  }

  // (5) POST /characters carries outfit_mode + custom_clothing; world loads.
  async function case5CreateBody() {
    await loadCreator();
    await page.fill("input[placeholder='Имя Фамилия']", "Создаваемый Персонаж");
    await page.fill("input[type=number]", "35");
    await page.selectOption(".creator-field:has(label:text-is('Одежда')) select", "formal");
    await page.fill(".creator-field:has(label:text-is('Своё описание одежды')) textarea",
      "рыбачий плащ и резиновые сапоги");
    await page.click("button:has-text('Появиться на острове')");
    await until(() => createCalls.length === 1, 6000, "create POST");
    const body = createCalls[0] || {};
    await page.waitForFunction(() => location.hash === "#/world"
      && !!document.querySelector(".world-shell"), undefined, { timeout: 8000 });
    const pass =
      body.outfit_mode === "formal"
      && body.custom_clothing === "рыбачий плащ и резиновые сапоги"
      && body.name === "Создаваемый Персонаж"
      && pageErrors.length === 0;
    return { name: "create-body", pass,
      outfit_mode: body.outfit_mode, custom_clothing: body.custom_clothing };
  }

  // (6) layout screenshots at 1280px + 390px WITH a generated preview
  // rendered (reviewers need the real composition, not the empty state),
  // no horizontal overflow, and no appearance field crossing its panel's
  // content edge (the #316 overflow-fix regression probe).
  async function case6Shots() {
    const results = {};
    for (const [w, h, name] of [[1280, 900, "desktop"], [390, 844, "mobile"]]) {
      await page.setViewportSize({ width: w, height: h });
      await loadCreator();
      await page.fill(".creator-ai textarea", "Полный композит для скриншота");
      await page.click(".creator-ai button.primary");
      await until(async () => {
        const s = await formState();
        return s.imgHidden === false && (s.imgSrc || "").startsWith("data:");
      }, 6000, "generated preview rendered for the screenshot");
      const st = await formState();
      // panel-edge probe: no appearance input may cross the appearance
      // panel's content box (the review-reported 14px «Дополнительные
      // особенности» bleed must stay fixed).
      const panelOverflow = await page.evaluate(() => {
        const grid = document.querySelector(".creator-appearance");
        if (!grid) return ["no .creator-appearance"];
        const panel = grid.closest("section.panel");
        const cs = getComputedStyle(panel);
        const pr = panel.getBoundingClientRect();
        const contentRight = pr.right - parseFloat(cs.borderRightWidth)
          - parseFloat(cs.paddingRight);
        const contentLeft = pr.left + parseFloat(cs.borderLeftWidth)
          + parseFloat(cs.paddingLeft);
        const bad = [];
        for (const inp of grid.querySelectorAll("input")) {
          const r = inp.getBoundingClientRect();
          const overRight = +(r.right - contentRight).toFixed(1);
          const overLeft = +(contentLeft - r.left).toFixed(1);
          if (overRight > 0.5 || overLeft > 0.5) {
            bad.push({ label: inp.getAttribute("aria-label"), overRight, overLeft });
          }
        }
        return bad;
      });
      const path = await shot(name + ".png");
      results[w] = { path, overflow: st.overflow, panelOverflow };
    }
    return { name: "layout-shots",
      pass: !results[1280].overflow && !results[390].overflow
        && results[1280].panelOverflow.length === 0
        && results[390].panelOverflow.length === 0,
      results };
  }

  // (7) navigating away mid-flight discards the generate response: the
  // unmount guard (!name.isConnected — route() rebuilds the creator for
  // ANY hash while no character exists) drops the held proposal: no field
  // repopulation and NO automatic preview. draftDelayMs holds the response
  // in flight (the case-3 pattern); nothing is typed after dispatch, so
  // creatorRev is unchanged at response time — the discard can only come
  // from the unmount clause.
  async function case7UnmountDiscards() {
    await loadCreator();
    draftDelayMs = 400;  // hold the response in flight
    await page.fill(".creator-ai textarea", "Ушёл до ответа");
    await page.click(".creator-ai button.primary");
    await page.waitForTimeout(120);  // the request is now in flight
    await page.evaluate(() => { location.hash = "#/world"; });
    // route() short-circuits the no-character #/world into a FRESH creator
    // (hash normalized to #/create; the old elements are detached).
    await page.waitForFunction(() => !!document.querySelector(".creator-shell"),
      undefined, { timeout: 8000 });
    await page.waitForTimeout(600);  // the held response arrives + settles
    const st = await formState();
    const pass =
      draftCalls.length === 1
      && previewCalls.length === 0  // the discarded draft never auto-previews
      && st.name === ""  // the proposal was NOT applied anywhere
      && st.appearance["Глаза"] === "" && st.appearance["Цвет волос"] === ""
      && st.looks === "" && st.bio === ""
      && st.imgHidden !== false;
    return { name: "unmount-discard", pass, previewCalls: previewCalls.length,
      nameAfter: st.name, eyesAfter: st.appearance["Глаза"], looksAfter: st.looks };
  }

  // ---- run ----
  async function run() {
    const report = { base: BASE, staticSha: null, cases: {}, screenshots: [],
      unexpectedAPI, pageErrors, wsBlocks, allPass: false };
    await loadCreator();
    // Source-binding SHA: hash the served candidate bytes (m270 pattern).
    report.staticSha = await page.evaluate(async () => {
      if (!window.crypto || !window.crypto.subtle) {
        throw new Error("SETUP: WebCrypto (crypto.subtle) unavailable");
      }
      const digest = async (path) => {
        const response = await fetch(path);
        if (!response.ok) throw new Error("SETUP: candidate fetch failed: " + path);
        const bytes = await response.arrayBuffer();
        const hash = await crypto.subtle.digest("SHA-256", bytes);
        return [...new Uint8Array(hash)].map((b) => b.toString(16).padStart(2, "0")).join("");
      };
      return { appJs: await digest("/app.js"), styleCss: await digest("/style.css") };
    });
    report.cases.case1 = await case1Generate();
    report.cases.case2 = await case2Variant();
    report.cases.case3 = await case3TypingDiscards();
    report.cases.case4 = await case4FailedPreview();
    report.cases.case5 = await case5CreateBody();
    report.cases.case6 = await case6Shots();
    report.cases.case7 = await case7UnmountDiscards();
    report.screenshots = [OUT + "/desktop.png", OUT + "/mobile.png"];
    report.allPass = Object.values(report.cases).every((c) => c.pass)
      && unexpectedAPI.length === 0 && pageErrors.length === 0;
    return report;
  }

  await setup();
  return await run();
}
