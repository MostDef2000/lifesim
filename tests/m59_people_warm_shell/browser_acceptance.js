// tests/m59_people_warm_shell/browser_acceptance.js
//
// MANUAL browser-acceptance for 023 (People warm shell, #59 wave).
// A Playwright `async (page) => {}` function run by the PARENT via the
// native browser MCP / run_code — NOT collected by pytest, NOT run by CI
// (scripts/check.sh = ruff + pytest only). The CI truth is the source pins
// in test_people_warm_shell.py; this file is the manual truth for REAL
// rendered computed style / geometry / states / contrast.
//
// AUTH/PROD LIMITS: no real accounts, no credentials, no production API
// calls, no network mutations. Every request is intercepted: static is
// served from the LOCAL candidate bytes only, every known API path is an
// origin-guarded GET-only fixture, every unknown path is aborted, every
// WebSocket is closed pre-connect. «Поговорить»/AI/GPU actions are NEVER
// clicked (they could invoke mutating endpoints); only native People card
// selection (click / Tab+Enter) and the real error-retry button are
// interacted with.
//
// LOCAL CANDIDATE BYTES (never PROD): the parent starts
//   python3 -m http.server 8772 --bind 127.0.0.1 --directory backend/app/web
// and mkdirs /tmp/opencode/playwright-dumps/lifesim-023/ before running.
// The app references /static/app.js, /static/style.css, /static/static/...
// so the FIRST /static prefix is stripped before the transparent fetch.
// The report returns the SHA256 of the served app.js / style.css (browser
// WebCrypto — the native MCP VM has no Node crypto import) so the parent
// can compare to the candidate files (base 323d9c00: app
// 33d7bb96b3d354b1a813685e82e48801c5e94309009ee7aaf3497a2d17566bf0,
// css 5417801f7fb87a43f09af22fc26c6ca95f3e86db4d10744e6807a8d91a712894)
// and prove the run hit the candidate bytes, not PROD.
//
// FAIL-CLOSED CONTRACT (the m270 seam, same discipline):
// - Static: ONLY the local BASE origin + canonical static paths
//   ('/', '/index.html', '/static/*', '/app.js', '/style.css'), GET/HEAD,
//   transparently fulfilled from the local server (first /static stripped,
//   real response bytes + content-type). Every other request is recorded +
//   aborted. The real app/CSS/map bytes are NEVER overridden or patched.
// - API: every known API path is served by an origin-guarded GET-only
//   fixture (installed LAST -> highest precedence); a foreign-origin or
//   non-GET request to a known path, and ANY request to an unknown path,
//   is recorded + aborted. The FINAL audit requires unexpected.length === 0
//   (no allowlist of any kind).
// - WebSocket: PRE-CONNECT interception via page.routeWebSocket, installed
//   ONCE before the first navigation; every WS is closed WITHOUT
//   connectToServer(). If page.routeWebSocket is unavailable we THROW
//   before any navigation (no post-connect fallback). No WS console
//   suppression of any kind.
//
// RUN SHAPE: a warmup document (valid People origin + ready clock) computes
// the served-byte hashes BEFORE any style assertion (!response.ok is a
// SETUP failure). Then EACH of the 22 named cases (11 groups x 2
// viewports: 390x844 + 1440x900) runs under its own try/catch: a failing
// case records ok:false + named error + actually-measured style + a
// failure screenshot, and the run CONTINUES — no stop-after-first-red, no
// zero-results on a style fail. allPass requires ALL 22 cases executed +
// all ok + zero unexpected/page/console errors. Each document gets a
// docId; the fixture 404/500 registry and the console classification are
// per-document (a case can never reuse an earlier document's registry).
//
// FRESH DOCUMENT PER CASE: one page for the whole run (the parent gives a
// fresh page and closes old routes before the suite); each case does a
// fresh document + re-installs ONLY the HTTP routes (the previous case's
// patterns are unrouted first — no accumulation); the WS route is never
// re-installed. Screenshots go ONLY under /tmp/opencode/playwright-dumps/
// lifesim-023/, distinct per case + viewport.
//
// BASELINE (base 323d9c00): the style/cream cases (style-*, empty-*) are
// EXPECTED RED on the cream assertions with the ACTUAL observed
// dark/transparent values (collected, never a fictional baseline RGB).
// The contrast cases MAY RED on base — either a real pre-existing
// under-contrast or an explicit unsupported-paint fail (e.g. the base
// panels' backdrop-filter blur, which the oracle refuses to guess). All
// legacy behavior cases must EXECUTE (per-case try/catch, continue-on-
// fail); a style RED never masks them. Production is NEVER changed to
// make the oracle happy.
//
// CONTRAST ORACLE: real RGBA compositing as PREMULTIPLIED sRGB interval
// arithmetic over the ancestor chain (outer->inner, source-over). A
// gradient layer composites EACH fixed legacy (rgb/rgba/transparent) stop
// over the current OPAQUE destination, then takes the per-channel [min,max]
// UNION of the stop results. The composite p(t)+(1-a(t))*D is affine in the
// stop interpolation (premultiplied sRGB, CSS Images 3 §3.4.2) and monotone in D, so
// the stop endpoints' extrema bound every pixel — a CONSERVATIVE bound, not
// necessarily the attainable rendered extrema. Interval source-over is exact
// (opaque layers reset, transparent layers are no-ops, no heuristic
// widening). It FAILS clearly on undetermined paint (no opaque ancestor),
// unsupported paint (url() layers, unmodeled gradient geometry), or
// non-default ancestor opacity/filter/mix-blend/backdrop-filter — no
// fictional fallbacks, no guessed exact pixels. Each checked element
// reports its oracle kind ("exact-solid" vs "gradient-conservative-bound")
// and failures include the actual RGB bounds.

async (page) => {
  const BASE = "http://127.0.0.1:8772";
  const OUT = "/tmp/opencode/playwright-dumps/lifesim-023";
  const CLOCK = "День 2, 01:00"; // dayTime(1500) — app.js:158

  // ---- fixtures (shapes copied from backend/app/api/app.py + visual_routes.py) ----
  const USER = { id: 1, username: "audit", email: "audit@fixture.local", role: "player" };
  const BYUSER = [{ id: 1, name: "P", alive: true, control_mode: "MANUAL" }];
  // location_id non-null => viewPeople skips the extra /characters/1 fetch.
  const CHAR1 = {
    id: 1, name: "P", sex: "m", age: 30, alive: true, location_id: 1,
    control_mode: "MANUAL", is_owner: true, balance: 100,
    needs: { hunger: 70, energy: 80, thirst: 60, social: 50 }, job: "Рыбак",
    looks: null, biography: null, outfit_mode: "auto", home_location_id: 1,
    traits: [], health: { health: 100, stress: 10 },
  };
  // 5 NPCs covering the signed-meter table (0/+50/-50/+100/-100) and all
  // three status texts. trust/affection/respect are signed -100..100;
  // romantic_interest is NONNEGATIVE in the domain (no invented negative
  // romance). updated_at is an INTEGER game timestamp (models.py:317),
  // faithful to the real route (app.py:763-772).
  const T = 1500;
  const rel = (other_id, other_name, trust, affection, familiarity, romantic_interest, respect) =>
    ({ other_id, other_name, trust, affection, familiarity, romantic_interest, updated_at: T, respect });
  const RELS_NORMAL = [
    rel(2, "Анна", 40, 60, 30, 0, 50),           // Друг;      40/60/0/50
    rel(3, "Борис", -30, -10, 20, 0, -50),       // Напряж.;  -30/-10/0/-50
    rel(4, "Виктор", 100, 50, 40, 100, 100),     // Друг;     100/50/100/100
    rel(5, "Галина", 0, 0, 0, 0, 0),             // Знакомый; 0/0/0/0
    rel(6, "Дмитрий", -100, -100, 10, 0, -100),  // Напряж.; -100/-100/0/-100
  ];
  // Long VALID name: Character.first_name/last_name are unconstrained
  // String columns (models.py:82-83) — a long name is a valid domain value,
  // not a fake 1000-char fixture.
  const RELS_LONG = [
    rel(2, "Александра-Екатерина Морская", 40, 60, 30, 0, 50),
    rel(3, "Борис", -30, -10, 20, 0, -50),
  ];
  const ROLES = { roles: [{ id: "fisher", title: "Рыбак", reason: "работает на причале" }] };
  // GET /characters/{cid}/desire: "Active desire or the explicit empty
  // state {desire: null}" (app.py:1635-1642) — the profile's desire chip.
  const DESIRE_EMPTY = { desire: null };
  // Inert synthetic portrait (a fixture asset, NOT a real source asset and
  // NOT a claim about human photo quality): no scripts, no external refs.
  const PORTRAIT_SVG =
    "<svg xmlns='http://www.w3.org/2000/svg' width='300' height='400'>" +
    "<rect width='300' height='400' fill='#c98a2e'/>" +
    "<text x='150' y='200' font-size='20' text-anchor='middle' fill='#1d1506'>fixture portrait</text></svg>";
  const PORTRAIT_META = {
    id: 77, asset_type: "portrait", character_id: 2, location_id: null,
    canonical: true, prompt: null, seed: 1, model: null, lora: null,
    storage_path: null, scene_descriptor: null,
  };
  const PORTRAIT_404 = { status: 404, body: { detail: "canonical portrait not found" } };
  const WORLD = { id: 1, seed: 42, game_timestamp: 1500, day: 1, population: 12, time_scale: 1.0, is_paused: false };
  const WEATHER = {
    enabled: true, day: 1, temperature: 18, wind: 3, precipitation: 0.1,
    cloudiness: 0.3, visibility: 1.0, source: "generated",
    real_date: "2026-11-06", description: "+18°C, ясно, ветер 3 м/с",
  };
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
  const EVENTS = [
    { id: 1, event_type: "CHARACTER_MOVED", actor_id: 2, location_id: 1, day: 1, game_timestamp: 1440, payload: { from_location: 2, to_location: 1 } },
  ];
  const REFS_EMPTY = { items: [] };
  const TASKS_EMPTY = { active: [], planned: [], recent_terminal: [] };

  // ---- run-wide collectors (listeners attached ONCE) ----
  const unexpected = [];         // recorded + aborted requests (must stay EMPTY)
  const pageErrors = [];
  const consoleErrors = [];      // UNEXPECTED console errors (no whitelists)
  const expectedHttp = [];       // registry: fixture 404/500 fulfillments {url, status, doc}
  const wsBlocks = [];           // pre-connect WS interceptions (by design)
  let relCalls = 0;              // error case: first call 500, retry 200
  let relFailFirst = false;
  let docId = 0;                 // per-document id (fresh document per case)
  let currentDoc = 0;            // the document currently being exercised

  // ---- style constants (the profile-equivalent cream values) ----
  const stripWs = (s) => (s || "").replace(/\s+/g, "");
  // Canonicalize a background-image for the hero contains-check: strip ONLY
  // the DEFAULT linear-gradient direction (180deg / to bottom) — Chrome's
  // correct paint serializes the default direction as an omission. Preserves
  // full stops/colors/alpha/order; NEVER strips arbitrary angles (0deg/90deg/
  // any non-default angle). Applied to BOTH the actual hero image and the
  // expected HERO_LINEAR so the comparison is direction-insensitive only for
  // the default.
  const canonGradient = (s) => (s || "").replace(/linear-gradient\(\s*(180deg|to\s+bottom)\s*,/gi, "linear-gradient(");
  const CREAM_BG = "rgb(243,236,223)";  // #f3ecdf
  const CREAM_TEXT = "rgb(42,33,24)";   // #2a2118
  const WARM_BORDER = "rgba(120,96,64,0.35)";
  // The profile-precedent hero linear, fully resolved (top + bottom stops).
  const HERO_LINEAR = "linear-gradient(180deg,rgba(255,253,248,0.9),rgba(236,224,204,0.7))";

  // ---- color math: premultiplied sRGB interval arithmetic (no deps) ----
  function parseColor(s) {
    s = (s || "").trim();
    if (s === "transparent") return { r: 0, g: 0, b: 0, a: 0 };
    const m = s.match(/^rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?\)$/);
    if (!m) return null;
    return { r: +m[1], g: +m[2], b: +m[3], a: m[4] === undefined ? 1 : +m[4] };
  }
  function lumOf(c) {
    const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b);
  }
  function contrastL(L1, L2) {
    const hi = Math.max(L1, L2), lo = Math.min(L1, L2);
    return (hi + 0.05) / (lo + 0.05);
  }
  // A paint as PREMULTIPLIED sRGB intervals: per-channel [lo,hi] in [0,1]
  // (premultiplied channel = sRGB channel * alpha) + alpha [lo,hi].
  function pointPaint(r, g, b, a) {
    return { pr: [r * a, r * a], pg: [g * a, g * a], pb: [b * a, b * a], a: [a, a] };
  }
  // A gradient layer composited OVER an OPAQUE destination: composite EACH
  // fixed RGBA stop over the current opaque acc (source-over), then UNION the
  // per-channel result boxes across all stops. This keeps each stop's RGB and
  // alpha CORRELATED (a stop is one fixed color), unlike the old pre-union
  // premultiplied box which mixed min-RGB with max-alpha (unphysical). Because
  // the composite p(t)+(1-a(t))*D is affine in the stop interpolation and
  // monotone in D, the stop endpoints' extrema bound every pixel — a
  // CONSERVATIVE bound (not necessarily the attainable rendered extrema), no
  // sampling, no heuristic widening. The destination MUST be exactly opaque
  // (alpha [1,1]); a gradient over a non-opaque dest is rejected rather than
  // inventing a canvas.
  function overGradient(stops, acc) {
    // The destination MUST be EXACTLY opaque (alpha [1,1]) — a near-opaque
    // dest (alpha 1±eps) is rejected, never admitted as opaque.
    if (acc.a[0] !== 1 || acc.a[1] !== 1) {
      throw new Error("gradient requires an opaque destination (alpha=" + acc.a[0].toFixed(3) + ".." + acc.a[1].toFixed(3) + ")");
    }
    let box = null;
    for (const s of stops) {
      // s.r/g/b are 0-255 (parseColor); premul stays in the [0,1] convention
      // pointPaint uses, so divide by 255 before * alpha.
      const src = pointPaint(s.r / 255, s.g / 255, s.b / 255, s.a);
      const res = overInterval(src, acc);
      if (!box) {
        box = { pr: [res.pr[0], res.pr[1]], pg: [res.pg[0], res.pg[1]], pb: [res.pb[0], res.pb[1]], a: [res.a[0], res.a[1]] };
      } else {
        box.pr[0] = Math.min(box.pr[0], res.pr[0]); box.pr[1] = Math.max(box.pr[1], res.pr[1]);
        box.pg[0] = Math.min(box.pg[0], res.pg[0]); box.pg[1] = Math.max(box.pg[1], res.pg[1]);
        box.pb[0] = Math.min(box.pb[0], res.pb[0]); box.pb[1] = Math.max(box.pb[1], res.pb[1]);
        box.a[0] = Math.min(box.a[0], res.a[0]); box.a[1] = Math.max(box.a[1], res.a[1]);
      }
    }
    return box;
  }
  // Source-over interval arithmetic (premultiplied): result = src + dst*(1-srcA).
  // Every term is non-negative and monotonic in its inputs, so the interval
  // bounds are tight (exact for this operation): an opaque src resets to src,
  // a transparent src is a no-op.
  function overInterval(src, dst) {
    // resultA = srcA + dstA*(1-srcA): increasing in srcA and dstA — the
    // tight bounds pair each endpoint with ITSELF, not cross-endpoints.
    const aLo = src.a[0] + dst.a[0] * (1 - src.a[0]);
    const aHi = src.a[1] + dst.a[1] * (1 - src.a[1]);
    // resultC = srcC + dstC*(1-srcA): increasing in srcC/dstC, decreasing
    // in srcA — the channel bounds use SRC ALPHA, not channel values
    // (an opaque src must reset the result to src exactly).
    const ch = (sc, dc) => [sc[0] + dc[0] * (1 - src.a[1]), sc[1] + dc[1] * (1 - src.a[0])];
    const cl = (v) => Math.min(1, Math.max(0, v)); // physically valid premul
    const pr = ch(src.pr, dst.pr), pg = ch(src.pg, dst.pg), pb = ch(src.pb, dst.pb);
    return {
      pr: [cl(pr[0]), cl(pr[1])], pg: [cl(pg[0]), cl(pg[1])], pb: [cl(pb[0]), cl(pb[1])],
      a: [cl(aLo), cl(aHi)],
    };
  }
  // Split a CSS background-image list on TOP-LEVEL commas (parens-aware).
  function splitTopLevelCommas(s) {
    const out = [];
    let depth = 0, cur = "";
    for (const ch of s) {
      if (ch === "(") depth++;
      else if (ch === ")") depth--;
      if (ch === "," && depth === 0) { out.push(cur); cur = ""; }
      else cur += ch;
    }
    out.push(cur);
    return out.map((x) => x.trim()).filter(Boolean);
  }
  // The color stops of a gradient layer. Bounded parse: split the gradient
  // args on TOP-LEVEL commas; the first arg is the geometry prelude when it is
  // not itself a color stop; every other arg MUST be a legacy rgb/rgba/
  // transparent stop (+ optional position %/px). The prelude is validated
  // POSITIVELY against the supported geometry (not a blacklist): any explicit
  // interpolation (`in <space>`), color, hex, or unmodeled geometry fails
  // closed — never silently dropped (the old parser accepted `red 20%` and
  // `in display-p3` and silently dropped them).
  function extractStops(layer) {
    layer = layer.trim();
    const m = layer.match(/^(linear|radial|conic)-gradient\((.*)\)$/);
    if (!m) {
      throw new Error("unsupported background paint layer: " + layer.slice(0, 60));
    }
    const kind = m[1];
    if (kind === "conic") {
      throw new Error("unsupported conic-gradient (not modeled): " + layer.slice(0, 60));
    }
    const args = splitTopLevelCommas(m[2]);
    if (!args.length) throw new Error("no args in gradient layer: " + layer.slice(0, 60));
    // A legacy color stop: rgb(...)/rgba(...)/transparent + optional position.
    const stopRe = /^(rgba?\([^)]*\)|transparent)(\s+[\d.]+(px|%))?$/;
    const stops = [];
    // The prelude is the first arg when it is not itself a color stop.
    let firstStopIdx = 0;
    const first = args[0].trim();
    if (!stopRe.test(first)) {
      validateGradientPrelude(kind, first);
      firstStopIdx = 1;
    }
    // Every arg from firstStopIdx onward MUST be a legacy color stop.
    for (let i = firstStopIdx; i < args.length; i++) {
      const s = args[i].trim();
      const cm = s.match(stopRe);
      if (!cm) throw new Error("unsupported gradient arg (not a color stop): " + s.slice(0, 60));
      const colorStr = cm[1];
      const stop = colorStr === "transparent" ? { r: 0, g: 0, b: 0, a: 0 } : parseColor(colorStr);
      if (!stop) throw new Error("unparseable color stop: " + s.slice(0, 60));
      stops.push(stop);
    }
    if (!stops.length) throw new Error("no color stops in gradient layer: " + layer.slice(0, 60));
    return stops;
  }
  // POSITIVE whole-prelude validation for the supported gradient geometry.
  // Fails closed on anything unmodeled — no partial parse, no silent drop.
  function validateGradientPrelude(kind, prelude) {
    // Reject ANY explicit interpolation (`in <space>`) — not a fixed
    // blacklist (catches `in display-p3`, `in oklab`, etc.).
    if (/\bin\s+\S+/i.test(prelude)) {
      throw new Error("unsupported gradient interpolation: " + prelude.slice(0, 60));
    }
    // Reject colors / color functions / hex in the prelude — it must be
    // geometry, not an unrecognized color (catches `red`, `red 20%`, etc.).
    if (/rgba?\(|hsla?\(|color\(|lab\(|lch\(|oklab\(|oklch\(|hwb\(|#|transparent/i.test(prelude)) {
      throw new Error("unsupported gradient prelude (color, not geometry): " + prelude.slice(0, 60));
    }
    if (kind === "linear") {
      // The only supported linear prelude is the default direction
      // (180deg / to bottom). Any other angle is unmodeled -> fail closed.
      if (prelude === "180deg" || prelude === "to bottom") return;
      throw new Error("unsupported linear-gradient angle: " + prelude.slice(0, 60));
    }
    // radial: the only supported prelude is the size-position form
    // (two lengths + at + two positions), e.g. `640px 240px at 18% 0%`.
    if (/^[\d.]+(px|%) [\d.]+(px|%) at [\d.]+% [\d.]+%$/.test(prelude)) return;
    throw new Error("unsupported radial-gradient geometry: " + prelude.slice(0, 60));
  }
  // Non-default paint effects on a chain node: fail explicitly, never
  // silently ignore (they change the composited result in ways this
  // bounded oracle does not model).
  function checkPaintEffects(node) {
    if (node.opacity !== "1") throw new Error("unsupported paint effect: opacity=" + node.opacity);
    if (node.filter !== "none") throw new Error("unsupported paint effect: filter=" + node.filter);
    if (node.mixBlendMode !== "normal") throw new Error("unsupported paint effect: mix-blend-mode=" + node.mixBlendMode);
    if (node.backdropFilter !== "none") throw new Error("unsupported paint effect: backdrop-filter=" + node.backdropFilter);
  }
  // The effective background of an element: composite the ancestor chain
  // OUTER->INNER. For EACH node: check effects, then the FIXED backgroundColor
  // source-over onto the current acc FIRST, then image layers BOTTOM->TOP (the
  // LAST layer in the list is the bottom) — each gradient composited directly
  // onto the (opaque) ancestor acc via overGradient, NOT isolated on
  // transparent then over the ancestor. Fails clearly on undetermined paint
  // (no opaque ancestor) — no fictional fallback. Luminance is monotonic in
  // each channel, so the luminance extrema are at the corners of the final
  // opaque RGB box (a conservative bound, not a rendered-pixel measure).
  function resolveBgInterval(chainInnerToOuter) {
    let acc = pointPaint(0, 0, 0, 0); // transparent
    let gradient = false;
    for (const node of chainInnerToOuter.slice().reverse()) {
      checkPaintEffects(node);
      // FIXED backgroundColor source-over onto the current acc FIRST.
      const bg = parseColor(node.bg);
      if (!bg) throw new Error("unparseable background color: " + node.bg);
      acc = overInterval(pointPaint(bg.r / 255, bg.g / 255, bg.b / 255, bg.a), acc);
      // Image layers BOTTOM->TOP (the LAST layer in the list is the bottom).
      const img = (node.img || "").trim();
      if (img && img !== "none") {
        const layers = splitTopLevelCommas(img);
        for (let i = layers.length - 1; i >= 0; i--) {
          const layer = layers[i].trim();
          if (!/^(linear|radial|conic)-gradient\(/.test(layer)) {
            throw new Error("unsupported background paint layer: " + layer.slice(0, 60));
          }
          acc = overGradient(extractStops(layer), acc);
          gradient = true;
        }
      }
    }
    // The final acc must be EXACTLY opaque (alpha [1,1]) — a near-opaque
    // chain (alpha 1±eps) is undetermined, not opaque. A transparent-
    // with-no-images chain can pass until the BODY is opaque; a gradient over
    // a non-opaque dest already threw in overGradient.
    if (acc.a[0] !== 1 || acc.a[1] !== 1) {
      throw new Error("background paint undetermined (no opaque ancestor, alpha=" + acc.a[0].toFixed(2) + ".." + acc.a[1].toFixed(2) + ")");
    }
    // Carry the FLOATING premultiplied RGB through the luminance math
    // (luminance is monotonic in each channel, so the box corners are the
    // exact Lmin/Lmax); round ONLY the display `rgb` evidence.
    const Lmin = lumOf({ r: acc.pr[0] * 255, g: acc.pg[0] * 255, b: acc.pb[0] * 255 });
    const Lmax = lumOf({ r: acc.pr[1] * 255, g: acc.pg[1] * 255, b: acc.pb[1] * 255 });
    const rgb = [
      [Math.round(acc.pr[0] * 255), Math.round(acc.pg[0] * 255), Math.round(acc.pb[0] * 255)],
      [Math.round(acc.pr[1] * 255), Math.round(acc.pg[1] * 255), Math.round(acc.pb[1] * 255)],
    ];
    // The FLOATING premultiplied box (opaque, a=[1,1]) — the contrast math
    // consumes this, never the rounded display `rgb` (D1).
    const box = {
      pr: [acc.pr[0], acc.pr[1]], pg: [acc.pg[0], acc.pg[1]], pb: [acc.pb[0], acc.pb[1]],
      a: [1, 1],
    };
    return {
      Lmin, Lmax, rgb, box,
      kind: gradient ? "gradient-conservative-bound" : "exact-solid",
    };
  }
  // Minimum WCAG contrast between the foreground (composited over the bg
  // interval) and the background luminance interval: if the luminances can
  // coincide the min is 1.
  function minContrastOf(fgStr, bg) {
    const fg = parseColor(fgStr);
    if (!fg) throw new Error("unparseable foreground color: " + fgStr);
    const fgPaint = pointPaint(fg.r / 255, fg.g / 255, fg.b / 255, fg.a);
    // The FLOATING bg box (D1): `bg.rgb` is rounded display evidence only —
    // reconstructing the paint from it would feed display rounding back
    // into the contrast math.
    const bgPaint = {
      pr: [bg.box.pr[0], bg.box.pr[1]],
      pg: [bg.box.pg[0], bg.box.pg[1]],
      pb: [bg.box.pb[0], bg.box.pb[1]],
      a: [1, 1],
    };
    const res = overInterval(fgPaint, bgPaint);
    const Llo = lumOf({ r: res.pr[0] * 255, g: res.pg[0] * 255, b: res.pb[0] * 255 });
    const Lhi = lumOf({ r: res.pr[1] * 255, g: res.pg[1] * 255, b: res.pb[1] * 255 });
    if (Llo <= bg.Lmax && Lhi >= bg.Lmin) return 1.0;
    if (Llo > bg.Lmax) return contrastL(Llo, bg.Lmax);
    return contrastL(Lhi, bg.Lmin);
  }
  // Self-checks THROUGH THE ACTUAL RESOLVER (synthetic chains, no browser).
  function solidNode(color) {
    return { bg: color, img: "none", opacity: "1", filter: "none", mixBlendMode: "normal", backdropFilter: "none" };
  }
  function gradientNode(img) {
    return { bg: "rgba(0,0,0,0)", img, opacity: "1", filter: "none", mixBlendMode: "normal", backdropFilter: "none" };
  }
  function oracleSelfChecks() {
    // 1. dark body -> opaque cream shell: the opaque layer REPLACES the body.
    let r = resolveBgInterval([solidNode("rgb(243,236,223)"), solidNode("rgb(10,16,22)")]);
    if (r.kind !== "exact-solid") throw new Error("selfcheck: cream over dark must be exact-solid");
    if (r.rgb[0][0] !== 243 || r.rgb[0][1] !== 236 || r.rgb[0][2] !== 223 || r.rgb[0].join() !== r.rgb[1].join()) {
      throw new Error("selfcheck: opaque cream must replace the body exactly: " + JSON.stringify(r.rgb));
    }
    // 2. transparent leaf over dark body: a no-op.
    r = resolveBgInterval([solidNode("rgba(0,0,0,0)"), solidNode("rgb(10,16,22)")]);
    if (r.rgb[0][0] !== 10 || r.rgb[0][1] !== 16 || r.rgb[0][2] !== 22) {
      throw new Error("selfcheck: transparent leaf must be a no-op: " + JSON.stringify(r.rgb));
    }
    // 3. black 50% over white: sRGB-channel blend -> RGB 127.5, L ~ 0.214
    //    (NOT 0.5 — luminance is nonlinear in sRGB).
    r = resolveBgInterval([solidNode("rgba(0,0,0,0.5)"), solidNode("rgb(255,255,255)")]);
    if (Math.abs(r.rgb[0][0] - 128) > 1) throw new Error("selfcheck: black50-over-white rgb " + JSON.stringify(r.rgb));
    if (Math.abs(r.Lmin - 0.214) > 0.01) throw new Error("selfcheck: black50-over-white L " + r.Lmin + " (expected ~0.214, not 0.5)");
    // 4. white 50% over black: the equivalent blend.
    r = resolveBgInterval([solidNode("rgba(255,255,255,0.5)"), solidNode("rgb(0,0,0)")]);
    if (Math.abs(r.rgb[0][0] - 128) > 1 || Math.abs(r.Lmin - 0.214) > 0.01) {
      throw new Error("selfcheck: white50-over-black " + JSON.stringify(r.rgb) + " L=" + r.Lmin);
    }
    // 5. a black/white gradient range includes gray -> a gray foreground's
    //    minimum contrast is 1 (the luminances can coincide).
    r = resolveBgInterval([gradientNode("linear-gradient(rgba(0,0,0,1), rgba(255,255,255,1))"), solidNode("rgb(255,255,255)")]);
    if (r.kind !== "gradient-conservative-bound") throw new Error("selfcheck: gradient kind " + r.kind);
    if (r.Lmin > 0.01 || r.Lmax < 0.99) throw new Error("selfcheck: black/white gradient range " + r.Lmin + ".." + r.Lmax);
    const gray = minContrastOf("rgb(128,128,128)", r);
    if (Math.abs(gray - 1.0) > 1e-9) throw new Error("selfcheck: gray over black/white gradient must be 1, got " + gray);
    // 6. black/white contrast = 21.
    const c21 = contrastL(lumOf({ r: 255, g: 255, b: 255 }), lumOf({ r: 0, g: 0, b: 0 }));
    if (Math.abs(c21 - 21) > 0.01) throw new Error("selfcheck: black/white contrast " + c21 + " != 21");
    // 7. REALISTIC layered radial+linear gradient over an opaque cream body
    //    (the actual app hero paint). The alpha must stay OPAQUE: the old
    //    cross-endpoint alpha math produced alpha=0.08 here and threw
    //    "background paint undetermined" — a false failure. A sane luminance
    //    range (Lmax <= 1) also catches a premul scale bug.
    r = resolveBgInterval([
      gradientNode("radial-gradient(640px 240px at 18% 0%, rgba(226, 180, 90, 0.1), transparent 62%), linear-gradient(180deg, rgba(255, 253, 248, 0.9), rgba(236, 224, 204, 0.7))"),
      solidNode("rgb(243,236,223)"),
    ]);
    if (r.kind !== "gradient-conservative-bound") throw new Error("selfcheck: layered gradient kind " + r.kind);
    if (!(r.Lmin > 0 && r.Lmin < r.Lmax && r.Lmax <= 1.0001)) {
      throw new Error("selfcheck: layered gradient over cream L " + r.Lmin + ".." + r.Lmax + " (expected opaque, 0<Lmin<Lmax<=1)");
    }
    for (const corner of r.rgb) for (const v of corner) if (v < 0 || v > 255) throw new Error("selfcheck: layered gradient rgb out of [0,255]: " + JSON.stringify(r.rgb));
    // 8. FRACTIONAL fg+bg (D1): the contrast math must consume the FLOAT
    //    paint box, never the rounded display rgb. rgba(255,128,0,0.5) over
    //    black composites to premul (0.5, 128/255*0.5, 0) = sRGB (127.5,
    //    64, 0); the display rgb rounds to (128,64,0), which shifts the
    //    contrast of a 50%-white foreground by ~0.007. The expected value
    //    is computed from the exact float channels, independent of the
    //    resolver's display rounding.
    r = resolveBgInterval([solidNode("rgba(255,128,0,0.5)"), solidNode("rgb(0,0,0)")]);
    if (r.rgb[0][0] !== 128 || r.rgb[0][1] !== 64 || r.rgb[0][2] !== 0) {
      throw new Error("selfcheck: fractional bg display rgb must stay rounded: " + JSON.stringify(r.rgb));
    }
    const expFracBgL = lumOf({ r: 0.5 * 255, g: (128 / 255) * 0.5 * 255, b: 0 });
    const expFracFgL = lumOf({
      r: (0.5 + 0.5 * 0.5) * 255,
      g: (0.5 + (128 / 255) * 0.5 * 0.5) * 255,
      b: (0.5 + 0) * 255,
    });
    const expFracC = contrastL(expFracFgL, expFracBgL);
    const gotFrac = minContrastOf("rgba(255,255,255,0.5)", r);
    if (Math.abs(gotFrac - expFracC) > 1e-9) {
      throw new Error("selfcheck: fractional fg/bg contrast must use the float box, got " + gotFrac + " expected " + expFracC);
    }
    // 9. PRECISE hero paint (independent closed-form expectation, NOT the
    //    implementation): the actual app hero (radial + linear over cream)
    //    must have a lower FLOAT RGB of (236.89, 222.84, 197.73) and a min
    //    contrast of #6e5f4e of 4.6769923711852375. The expected values are
    //    hand-derived from the stop endpoints (composite p+(1-a)*D over the
    //    opaque cream, union over stops) — independent of the resolver.
    r = resolveBgInterval([
      gradientNode("radial-gradient(640px 240px at 18% 0%, rgba(226, 180, 90, 0.1), transparent 62%), linear-gradient(180deg, rgba(255, 253, 248, 0.9), rgba(236, 224, 204, 0.7))"),
      solidNode("rgb(243,236,223)"),
    ]);
    const heroLower = [r.box.pr[0] * 255, r.box.pg[0] * 255, r.box.pb[0] * 255];
    if (Math.abs(heroLower[0] - 236.89) > 0.01 || Math.abs(heroLower[1] - 222.84) > 0.01 || Math.abs(heroLower[2] - 197.73) > 0.01) {
      throw new Error("selfcheck: hero lower float RGB " + heroLower.map((v) => v.toFixed(3)).join(",") + " != 236.89,222.84,197.73");
    }
    const heroContrast = minContrastOf("rgb(110,95,78)", r);
    if (Math.abs(heroContrast - 4.6769923711852375) > 1e-6) {
      throw new Error("selfcheck: hero min contrast #6e5f4e " + heroContrast + " != 4.6769923711852375");
    }
    // 10. Partial-alpha RED/transparent gradient over opaque WHITE: the float
    //     box must be red [1,1], green [0.5,1], blue [0.5,1] — the correlated
    //     stop-over-opaque math, NOT the old pre-union box (which gave a fake
    //     gray red starting at 127.5).
    r = resolveBgInterval([gradientNode("linear-gradient(rgba(255,0,0,0.5), transparent)"), solidNode("rgb(255,255,255)")]);
    if (r.kind !== "gradient-conservative-bound") throw new Error("selfcheck: red/transparent gradient kind " + r.kind);
    if (Math.abs(r.box.pr[0] - 1) > 1e-9 || Math.abs(r.box.pr[1] - 1) > 1e-9) {
      throw new Error("selfcheck: red/transparent gradient red box " + JSON.stringify(r.box.pr) + " != [1,1] (no fake gray)");
    }
    if (Math.abs(r.box.pg[0] - 0.5) > 1e-9 || Math.abs(r.box.pg[1] - 1) > 1e-9) {
      throw new Error("selfcheck: red/transparent gradient green box " + JSON.stringify(r.box.pg) + " != [0.5,1]");
    }
    if (Math.abs(r.box.pb[0] - 0.5) > 1e-9 || Math.abs(r.box.pb[1] - 1) > 1e-9) {
      throw new Error("selfcheck: red/transparent gradient blue box " + JSON.stringify(r.box.pb) + " != [0.5,1]");
    }
    // 11. Unsupported paint MUST throw (asserted), never silently pass:
    //     named colors, non-sRGB interpolation, and a non-opaque dest.
    let threw = false;
    try { resolveBgInterval([gradientNode("linear-gradient(180deg, red, blue)"), solidNode("rgb(255,255,255)")]); }
    catch (e) { threw = true; if (!/unsupported gradient/i.test(e.message)) throw new Error("selfcheck: named color should throw unsupported, got: " + e.message); }
    if (!threw) throw new Error("selfcheck: named color in a gradient stop must throw");
    // 11a. FIRST-ARG prelude fail-open holes (the old parser accepted these
    //      and silently dropped them): a named color + position, an explicit
    //      interpolation, and digit junk must all be REJECTED.
    threw = false;
    try { resolveBgInterval([gradientNode("linear-gradient(red 20%, rgba(0,0,0,1), rgba(255,255,255,1))"), solidNode("rgb(255,255,255)")]); }
    catch (e) { threw = true; if (!/unsupported/i.test(e.message)) throw new Error("selfcheck: red 20% prelude should throw unsupported, got: " + e.message); }
    if (!threw) throw new Error("selfcheck: a named-color+position prelude (red 20%) must throw");
    threw = false;
    try { resolveBgInterval([gradientNode("linear-gradient(in display-p3, rgba(0,0,0,1), rgba(255,255,255,1))"), solidNode("rgb(255,255,255)")]); }
    catch (e) { threw = true; if (!/unsupported gradient interpolation/i.test(e.message)) throw new Error("selfcheck: in display-p3 prelude should throw interpolation, got: " + e.message); }
    if (!threw) throw new Error("selfcheck: an explicit interpolation prelude (in display-p3) must throw");
    threw = false;
    try { resolveBgInterval([gradientNode("linear-gradient(20junk, rgba(0,0,0,1), rgba(255,255,255,1))"), solidNode("rgb(255,255,255)")]); }
    catch (e) { threw = true; if (!/unsupported/i.test(e.message)) throw new Error("selfcheck: 20junk prelude should throw unsupported, got: " + e.message); }
    if (!threw) throw new Error("selfcheck: a digit-junk prelude (20junk) must throw");
    threw = false;
    try { resolveBgInterval([gradientNode("linear-gradient(in oklab, rgba(255,0,0,1), rgba(0,0,255,1))"), solidNode("rgb(255,255,255)")]); }
    catch (e) { threw = true; if (!/unsupported gradient interpolation/i.test(e.message)) throw new Error("selfcheck: non-sRGB interpolation should throw, got: " + e.message); }
    if (!threw) throw new Error("selfcheck: non-sRGB interpolation (in oklab) must throw");
    threw = false;
    try { resolveBgInterval([gradientNode("linear-gradient(rgba(255,0,0,0.5), transparent)")]); }
    catch (e) { threw = true; if (!/opaque destination/i.test(e.message)) throw new Error("selfcheck: non-opaque dest should throw, got: " + e.message); }
    if (!threw) throw new Error("selfcheck: a gradient over a non-opaque dest must throw");
    // 11b. NEAR-OPAQUE rejection: a dest with alpha 1-1e-12 (not exactly 1)
    //      must be REJECTED by the exact [1,1] check (the old EPS=1e-12 check
    //      would have admitted it as opaque).
    threw = false;
    try { resolveBgInterval([gradientNode("linear-gradient(rgba(255,0,0,0.5), transparent)"), solidNode("rgba(0,0,0,0.999999999999)")]); }
    catch (e) { threw = true; if (!/opaque destination/i.test(e.message)) throw new Error("selfcheck: near-opaque dest should throw opaque destination, got: " + e.message); }
    if (!threw) throw new Error("selfcheck: a near-opaque dest (alpha 1-1e-12) must be rejected");
    // 12. canonGradient: accept 180deg/default/to-bottom equivalence, reject
    //     arbitrary angles and changed color/alpha.
    if (canonGradient("linear-gradient(180deg,rgba(1,2,3,1),rgba(4,5,6,1))") !== "linear-gradient(rgba(1,2,3,1),rgba(4,5,6,1))") {
      throw new Error("selfcheck: canonGradient must strip the default 180deg");
    }
    if (canonGradient("linear-gradient(to bottom,rgba(1,2,3,1),rgba(4,5,6,1))") !== "linear-gradient(rgba(1,2,3,1),rgba(4,5,6,1))") {
      throw new Error("selfcheck: canonGradient must strip the default to bottom");
    }
    if (canonGradient("linear-gradient(rgba(1,2,3,1),rgba(4,5,6,1))") !== "linear-gradient(rgba(1,2,3,1),rgba(4,5,6,1))") {
      throw new Error("selfcheck: canonGradient must keep an angle-less linear-gradient");
    }
    if (canonGradient("linear-gradient(0deg,rgba(1,2,3,1),rgba(4,5,6,1))") !== "linear-gradient(0deg,rgba(1,2,3,1),rgba(4,5,6,1))") {
      throw new Error("selfcheck: canonGradient must NOT strip an arbitrary angle (0deg)");
    }
    if (canonGradient("linear-gradient(90deg,rgba(1,2,3,1),rgba(4,5,6,1))") !== "linear-gradient(90deg,rgba(1,2,3,1),rgba(4,5,6,1))") {
      throw new Error("selfcheck: canonGradient must NOT strip an arbitrary angle (90deg)");
    }
    if (canonGradient("linear-gradient(180deg,rgba(1,2,3,0.5),rgba(4,5,6,1))") !== "linear-gradient(rgba(1,2,3,0.5),rgba(4,5,6,1))") {
      throw new Error("selfcheck: canonGradient must preserve color+alpha when stripping 180deg");
    }
  }
  // Guard self-checks with PURE STUBS (no browser, no real requests):
  // foreign GET and non-GET must be recorded + aborted once, never fulfilled.
  async function guardSelfChecks() {
    let aborted = 0, fulfilled = 0;
    const stub = (url, method) => ({
      request: () => ({ url: () => url, method: () => method }),
      abort: async () => { aborted += 1; },
      fulfill: async () => { fulfilled += 1; },
    });
    const before = unexpected.length;
    const r1 = await guard(stub("http://evil.example/api/x", "GET"));
    if (r1 !== false || aborted !== 1 || fulfilled !== 0) throw new Error("guard selfcheck: foreign GET must be rejected, aborted once, never fulfilled");
    if (unexpected.length !== before + 1 || unexpected[unexpected.length - 1].reason !== "foreign-origin") throw new Error("guard selfcheck: foreign GET not recorded");
    const r2 = await guard(stub(BASE + "/characters/1/relationships", "POST"));
    if (r2 !== false || aborted !== 2 || fulfilled !== 0) throw new Error("guard selfcheck: non-GET must be rejected, aborted once, never fulfilled");
    if (unexpected.length !== before + 2 || unexpected[unexpected.length - 1].reason !== "non-GET-to-known-api") throw new Error("guard selfcheck: non-GET not recorded");
    const r3 = await guard(stub(BASE + "/characters/1/relationships", "GET"));
    if (r3 !== true || aborted !== 2) throw new Error("guard selfcheck: base-origin GET must pass");
    // Stub records are test artifacts, not real requests — discard them so
    // the final audit (unexpected.length === 0) covers real traffic only.
    unexpected.length = 0;
  }

  // ---- helpers ----
  // The served candidate bytes, hashed IN THE PAGE (WebCrypto — the native
  // MCP VM has no Node crypto import); !response.ok is a SETUP failure.
  async function servedHashes() {
    return await page.evaluate(async () => {
      const out = {};
      for (const [name, path] of [["appJs", "/app.js"], ["css", "/style.css"]]) {
        const r = await fetch(path, { cache: "no-store" });
        if (!r.ok) throw new Error(`setup: served ${path} HTTP ${r.status} (local server not serving candidate bytes)`);
        const d = await crypto.subtle.digest("SHA-256", await r.arrayBuffer());
        out[name] = Array.from(new Uint8Array(d)).map((b) => b.toString(16).padStart(2, "0")).join("");
      }
      return out;
    });
  }
  // Assert every visible text element in `scope` meets the WCAG ratio for
  // its font size (>=24px, or >=18.66px bold = "large" -> 3.0, else 4.5),
  // against the REAL composited background (premultiplied interval chain,
  // gradient conservative bound). Throws on a vacuous scope — no empty-pass.
  async function checkContrast(scope, label) {
    const checked = [];
    const bad = [];
    for (const el of await page.$$(scope)) {
      const info = await el.evaluate((node) => {
        const cs = getComputedStyle(node);
        if (cs.display === "none" || cs.visibility === "hidden") return null;
        const r = node.getBoundingClientRect();
        if (r.width < 2 || r.height < 2) return null;
        const txt = (node.textContent || "").trim();
        if (!txt) return null;
        const size = parseFloat(cs.fontSize);
        const weight = parseInt(cs.fontWeight, 10) || 400;
        const chain = [];
        let n = node;
        while (n && n.nodeType === 1) {
          const c = getComputedStyle(n);
          chain.push({
            bg: c.backgroundColor, img: c.backgroundImage,
            opacity: c.opacity, filter: c.filter,
            mixBlendMode: c.mixBlendMode, backdropFilter: c.backdropFilter,
          });
          n = n.parentElement;
        }
        return { color: cs.color, size, weight, text: txt.slice(0, 40), chain };
      });
      if (!info) continue;
      const bg = resolveBgInterval(info.chain);
      const ratio = minContrastOf(info.color, bg);
      const large = info.size >= 24 || (info.size >= 18.66 && info.weight >= 700);
      const need = large ? 3.0 : 4.5;
      const entry = {
        text: info.text,
        ratio: Math.round(ratio * 100) / 100,
        need,
        oracle: bg.kind,
      };
      checked.push(entry);
      if (ratio < need) {
        bad.push({ ...entry, color: info.color, bgRGB: bg.rgb, bgLum: [Math.round(bg.Lmin * 1000) / 1000, Math.round(bg.Lmax * 1000) / 1000] });
      }
    }
    if (!checked.length) throw new Error(`contrast ${label}: no visible text in ${scope}`);
    if (bad.length) throw new Error(`contrast ${label}: below threshold: ${JSON.stringify(bad)}`);
    const grad = checked.filter((c) => c.oracle === "gradient-conservative-bound").length;
    return { checked: checked.length, gradientBound: grad, solid: checked.length - grad };
  }
  // Signed meter geometry: the fill sits at the spec fraction of the track
  // (app.js peopleMeter: width=|v|/2 %, left = v<0 ? 50-|v|/2 : 50).
  async function checkMeter(meterEl, label, value) {
    const g = await meterEl.evaluate((node) => {
      const bar = node.querySelector(".bar.people-track");
      const fill = node.querySelector(".people-fill");
      const mid = node.querySelector(".people-mid");
      const b = bar.getBoundingClientRect();
      const f = fill.getBoundingClientRect();
      const m = mid.getBoundingClientRect();
      return {
        barW: b.width, barL: b.left, fillW: f.width, fillL: f.left,
        midL: m.left, midW: m.width, neg: fill.classList.contains("neg"),
        shown: node.querySelector(".row span:last-child").textContent,
      };
    });
    const half = Math.round(Math.abs(value) / 2);
    const expW = (half / 100) * g.barW;
    const expL = g.barL + ((value < 0 ? 50 - half : 50) / 100) * g.barW;
    const tol = Math.max(2, g.barW * 0.01);
    if (Math.abs(g.fillW - expW) > tol) throw new Error(`meter ${label}: width ${g.fillW} != ${expW} (v=${value})`);
    if (Math.abs(g.fillL - expL) > tol) throw new Error(`meter ${label}: left ${g.fillL} != ${expL} (v=${value})`);
    if (value < 0 !== g.neg) throw new Error(`meter ${label}: .neg flag wrong for v=${value}`);
    if (g.shown !== String(Math.round(value))) throw new Error(`meter ${label}: shown ${g.shown} != ${value}`);
    if (Math.abs(g.midL + g.midW / 2 - (g.barL + g.barW / 2)) > tol) throw new Error(`meter ${label}: center tick off`);
  }
  async function checkMeters(values, who) {
    const ms = await page.$$(".people-meters .people-meter");
    if (ms.length !== 4) throw new Error(`${who}: expected 4 meters, got ${ms.length}`);
    const labels = ["Доверие", "Симпатия", "Романтика", "Уважение"];
    for (let i = 0; i < 4; i++) await checkMeter(ms[i], `${who} ${labels[i]}`, values[i]);
  }
  async function heroName() {
    return (await (await page.$(".people-name")).textContent()).trim();
  }
  async function heroStatus() {
    return (await (await page.$(".people-status")).textContent()).trim();
  }
  async function clickCard(name) {
    await (await page.waitForSelector(`.people-card:has(.people-card-name:text-is("${name}"))`, { timeout: 8000 })).click();
  }
  async function noPageOverflow() {
    // The people-strip inner horizontal scroll is ALLOWED (its children are
    // clipped by the strip's overflow-x:auto); the PAGE must not overflow.
    // On overflow, report the actual offenders (tag/id/class/rect).
    const o = await page.evaluate(() => {
      if (document.documentElement.scrollWidth <= window.innerWidth) return null;
      const contained = (el) => {
        let n = el.parentElement;
        while (n && n !== document.body) {
          const ox = getComputedStyle(n).overflowX;
          if (ox === "auto" || ox === "scroll" || ox === "hidden") {
            const nr = n.getBoundingClientRect();
            if (nr.width > 0) return true;
          }
          n = n.parentElement;
        }
        return false;
      };
      const offenders = [];
      for (const el of document.querySelectorAll("body *")) {
        const r = el.getBoundingClientRect();
        if (r.width > 0 && r.right > window.innerWidth + 1 && !contained(el)) {
          offenders.push({
            tag: el.tagName.toLowerCase(),
            id: el.id || null,
            cls: String(el.className).slice(0, 60),
            left: Math.round(r.left), right: Math.round(r.right), width: Math.round(r.width),
          });
          if (offenders.length >= 10) break;
        }
      }
      return { scrollWidth: document.documentElement.scrollWidth, innerWidth: window.innerWidth, offenders };
    });
    if (o) throw new Error(`page horizontal overflow: scrollWidth=${o.scrollWidth} > innerWidth=${o.innerWidth}; offenders: ${JSON.stringify(o.offenders)}`);
  }
  async function shot(name) {
    await page.screenshot({ path: `${OUT}/${name}.png` });
    return `${OUT}/${name}.png`;
  }
  // The actually-measured People style (null when the shell is absent) —
  // recorded on case failure so a RED reports real values, not silence.
  // .people-hero is the actual painted hero (app.js renderPeople:
  // el("section", { class: "panel people-hero" })); if it is ever missing,
  // the section classes are reported for diagnosis.
  async function measurePeople() {
    return await page.evaluate(() => {
      const shell = document.querySelector(".people-shell");
      if (!shell) return null;
      const cs = getComputedStyle(shell);
      const panel = shell.querySelector(".panel");
      const pcs = panel ? getComputedStyle(panel) : null;
      const hero = shell.querySelector(".people-hero");
      const sections = Array.from(shell.querySelectorAll("section")).map((s) => String(s.className));
      return {
        bg: cs.backgroundColor, color: cs.color,
        panelBorder: pcs ? pcs.borderColor : null,
        panelBlur: pcs ? pcs.backdropFilter : null,
        panelShadow: pcs ? pcs.boxShadow : null,
        heroBg: hero ? getComputedStyle(hero).backgroundImage : null,
        heroClasses: hero ? null : sections,
      };
    });
  }
  // Readiness: the route hash + the shell + the CLOCK (S.world hydrated).
  async function waitReady(hash, shellSel) {
    await page.waitForFunction((args) => {
      const c = document.getElementById("clock");
      return location.hash === args.hash
        && !!document.querySelector(args.shell)
        && !!c && c.textContent === args.clock;
    }, { hash, shell: shellSel, clock: CLOCK }, { timeout: 15000 });
  }
  async function waitClock() {
    await page.waitForFunction((clock) => {
      const c = document.getElementById("clock");
      return !!c && c.textContent === clock;
    }, CLOCK, { timeout: 15000 });
  }

  // ---- setup: fresh document per case, HTTP routes re-installed per case,
  //      the WS route stays the one installed before the first navigation ----
  let httpPatterns = [];
  const isBaseOrigin = (url) => {
    try { return new URL(url).origin === BASE; } catch (e) { return false; }
  };
  // Origin + method guard shared by every fixture handler. ASYNC, returns a
  // BOOL only: foreign-origin and non-GET are recorded + awaited-aborted.
  async function guard(route) {
    const req = route.request();
    if (!isBaseOrigin(req.url())) {
      unexpected.push({ url: req.url(), method: req.method(), reason: "foreign-origin" });
      await route.abort();
      return false;
    }
    if (req.method() !== "GET") {
      unexpected.push({ url: req.url(), method: req.method(), reason: "non-GET-to-known-api" });
      await route.abort();
      return false;
    }
    return true;
  }
  const json = (route, s) => route.fulfill({ status: s.status, contentType: "application/json; charset=utf-8", body: JSON.stringify(s.body) });
  const ok = (body) => ({ status: 200, body });
  const getOnly = (s) => async (route) => {
    if (!(await guard(route))) return;
    if (s.status >= 400) expectedHttp.push({ url: route.request().url(), status: s.status, doc: currentDoc });
    return json(route, s);
  };

  async function setup(viewport, mode, hash) {
    relCalls = 0;
    relFailFirst = mode === "error";
    docId += 1;
    currentDoc = docId;
    const myDoc = docId;
    await page.setViewportSize(viewport);
    await page.goto("about:blank", { waitUntil: "load" });
    for (const p of httpPatterns) { try { await page.unroute(p); } catch (e) { /* already gone */ } }
    httpPatterns = [];
    // Every registration is AWAITED before navigation; fallback FIRST
    // (lowest precedence), specific API routes LAST (highest precedence).
    const addRoute = async (pattern, handler) => {
      await page.route(pattern, handler);
      httpPatterns.push(pattern);
    };
    // Fallback route: ONLY the BASE origin + canonical static paths,
    // GET/HEAD, transparent fetch (first /static stripped, real response
    // bytes + content-type). Everything else is recorded + aborted so
    // nothing reaches a real backend.
    await addRoute("**/*", async (route) => {
      const req = route.request();
      let u;
      try { u = new URL(req.url()); }
      catch (e) { unexpected.push({ url: req.url(), method: req.method(), reason: "unparseable" }); await route.abort(); return; }
      const isBase = u.origin === BASE;
      const isCanonical = u.pathname === "/" || u.pathname === "/index.html"
        || u.pathname.startsWith("/static/") || u.pathname === "/app.js" || u.pathname === "/style.css";
      if (isBase && isCanonical && (req.method() === "GET" || req.method() === "HEAD")) {
        let stripped = u.pathname;
        if (stripped.startsWith("/static/")) stripped = stripped.slice("/static".length);
        const resp = await page.request.get(BASE + stripped);
        const body = await resp.body();
        const ct = (resp.headers() || {})["content-type"] || "application/octet-stream";
        return route.fulfill({ status: resp.status(), contentType: ct, body });
      }
      const reason = !isBase ? "foreign-origin" : !isCanonical ? "unknown-path" : "non-GET-static";
      unexpected.push({ url: u.href, method: req.method(), reason });
      await route.abort();
    });
    // Specific API routes. Origin-guarded, GET-only.
    await addRoute("**/auth/me", getOnly(ok(USER)));
    await addRoute("**/characters/by-user/1", getOnly(ok(BYUSER)));
    await addRoute("**/characters/1", getOnly(ok(CHAR1)));
    await addRoute("**/characters/1/tasks", getOnly(ok(TASKS_EMPTY)));
    await addRoute("**/world", getOnly(ok(WORLD)));
    await addRoute("**/weather", getOnly(ok(WEATHER)));
    await addRoute("**/locations", getOnly(ok(LOCATIONS)));
    await addRoute("**/locations/*", getOnly(ok(LOC1_DETAIL)));
    await addRoute("**/world/events*", getOnly(ok(EVENTS)));
    await addRoute("**/visual/references", getOnly(ok(REFS_EMPTY)));
    await addRoute("**/visual/characters/1/portrait", getOnly(PORTRAIT_404)); // profile/world hero
    // The 023 People GETs.
    await addRoute("**/characters/1/roles", getOnly(ok(ROLES)));
    // The profile's desire chip (app.py:1635-1642): the explicit empty state.
    await addRoute("**/characters/1/desire", getOnly(ok(DESIRE_EMPTY)));
    await addRoute("**/characters/1/relationships", async (route) => {
      if (!(await guard(route))) return;
      relCalls += 1;
      if (relFailFirst && relCalls === 1) {
        expectedHttp.push({ url: route.request().url(), status: 500, doc: myDoc });
        return json(route, { status: 500, body: { detail: "fixture: transient failure" } });
      }
      const body = mode === "empty" ? [] : mode === "longnames" ? RELS_LONG : RELS_NORMAL;
      return json(route, ok(body));
    });
    // Portrait metadata per NPC id (the people view fetches the SELECTED
    // person's portrait, i.e. other_id — not the player's). "loaded" mode:
    // NPC 2 (the default selection) has the fixture asset; everyone else
    // 404s. "missing" mode: everyone 404s.
    for (const npc of [2, 3, 4, 5, 6]) {
      const meta = mode === "loaded" && npc === 2 ? ok(PORTRAIT_META) : PORTRAIT_404;
      await addRoute(`**/visual/characters/${npc}/portrait`, getOnly(meta));
    }
    // The fixture image: an inert inline SVG (no scripts/external refs) at
    // the dedicated asset-file GET.
    await addRoute("**/visual/assets/77/file", async (route) => {
      if (!(await guard(route))) return;
      return route.fulfill({ status: 200, contentType: "image/svg+xml", body: PORTRAIT_SVG });
    });
    await page.goto(`${BASE}/?m023=${mode}${hash}`, { waitUntil: "load" });
  }

  // Run-wide listeners attached ONCE (no per-case accumulation). A console
  // error is "expected" ONLY when it is a resource-load failure whose URL
  // AND status match a fixture 404/500 registry entry of the CURRENT
  // document — no status-string or WS whitelists that could mask JS bugs.
  page.on("pageerror", (e) => pageErrors.push(String(e)));
  page.on("console", (m) => {
    if (m.type() !== "error") return;
    const t = m.text();
    const loc = typeof m.location === "function" ? m.location() : {};
    const url = loc && loc.url ? loc.url : null;
    if (/Failed to load resource/.test(t) && url) {
      const status = /404/.test(t) ? 404 : /500/.test(t) ? 500 : null;
      if (status && expectedHttp.some((e) => e.url === url && e.status === status && e.doc === currentDoc)) return;
    }
    consoleErrors.push({ text: t, url, doc: currentDoc });
  });

  // ---- cases (11 groups x 2 viewports = 22) ----
  async function caseStyle(vp) {
    const label = vp.width === 390 ? "390" : "1440";
    await setup(vp, "populated", "#/people");
    await waitReady("#/people", ".people-shell");
    const style = await measurePeople();
    if (!style) throw new Error("people shell missing");
    const clockText = (await (await page.$("#clock")).textContent()).trim();
    if (clockText !== CLOCK) throw new Error(`clock ${clockText} != ${CLOCK}`);
    const tabCount = await page.$$eval(".tabs button", (bs) => bs.length);
    if (tabCount !== 9) throw new Error(`tabs ${tabCount} != 9`);
    // The cream computed style — BASELINE RED on base: the ACTUAL observed
    // dark/transparent values are reported. All style problems are
    // collected (no masking) and reported together.
    const problems = [];
    if (stripWs(style.bg) !== CREAM_BG || stripWs(style.color) !== CREAM_TEXT) {
      problems.push(`root not cream: bg=${style.bg} color=${style.color} (actual observed values)`);
    }
    if (style.panelBlur !== "none" || style.panelShadow !== "none") {
      problems.push(`panel not flat: blur=${style.panelBlur} shadow=${style.panelShadow}`);
    }
    if (stripWs(style.panelBorder) !== WARM_BORDER) problems.push(`panel border ${style.panelBorder} != ${WARM_BORDER}`);
    if (!style.heroBg) problems.push(`hero .people-hero not found; sections: ${JSON.stringify(style.heroClasses)}`);
    else if (!stripWs(canonGradient(style.heroBg)).includes(stripWs(canonGradient(HERO_LINEAR)))) problems.push(`hero not warm linear: ${style.heroBg}`);
    if (problems.length) throw new Error(problems.join(" | "));
    await noPageOverflow();
    await shot("style-" + label);
    return `bg=${style.bg} color=${style.color} border=${style.panelBorder}`;
  }

  async function caseEmpty(vp) {
    const label = vp.width === 390 ? "390" : "1440";
    await setup(vp, "empty", "#/people");
    await page.waitForSelector(".people-empty", { timeout: 15000 });
    await waitClock();
    const txt = await page.$eval(".people-empty", (n) => n.textContent);
    if (!txt.includes("Пока никого")) throw new Error(`empty text: ${txt}`);
    if (!(await page.$(".people-empty a[href='#/world']"))) throw new Error("empty: the «К карте острова» link is missing");
    const bg = await page.$eval(".people-shell", (n) => getComputedStyle(n).backgroundColor);
    // Contrast runs INDEPENDENTLY of the cream assertion (collected, not
    // masked): both failures are reported together when present.
    const problems = [];
    try {
      await checkContrast(".people-empty .muted, .people-empty a", "empty-" + label);
    } catch (e) {
      problems.push("contrast: " + String((e && e.message) || e));
    }
    if (stripWs(bg) !== CREAM_BG) problems.push(`empty root not cream: bg=${bg} (actual observed value)`);
    if (problems.length) throw new Error(problems.join(" | "));
    await noPageOverflow();
    await shot("empty-" + label);
    return `bg=${bg}`;
  }

  async function caseError(vp) {
    const label = vp.width === 390 ? "390" : "1440";
    await setup(vp, "error", "#/people");
    await waitClock();
    // The first relationships GET is a fixture 500 -> the real #93 retry
    // node (loadRetryNode) + a toast. No invented UI.
    await page.waitForSelector('button:has-text("Повторить")', { timeout: 15000 });
    const errTxt = await page.evaluate(() => document.body.textContent);
    if (!errTxt.includes("Загрузка не удалась.")) throw new Error("error: the real retry text is missing");
    if (relCalls !== 1) throw new Error(`error: expected 1 relationships call before retry, got ${relCalls}`);
    // Native click on the real retry button -> viewPeople() re-fetches.
    await page.click('button:has-text("Повторить")');
    await waitReady("#/people", ".people-shell");
    if (relCalls !== 2) throw new Error(`error: retry did not re-fetch (relCalls=${relCalls})`);
    if ((await heroName()) !== "Анна") throw new Error("error: retry did not render the populated view");
    await noPageOverflow();
    await shot("error-" + label);
    return `relCalls=${relCalls}`;
  }

  async function caseLongNames(vp) {
    const label = vp.width === 390 ? "390" : "1440";
    await setup(vp, "longnames", "#/people");
    await waitReady("#/people", ".people-shell");
    const name = "Александра-Екатерина Морская";
    if ((await heroName()) !== name) throw new Error(`longnames: hero ${await heroName()} != ${name}`);
    const card = await page.$eval(".people-card .people-card-name", (n) => n.textContent.trim());
    if (card !== name) throw new Error(`longnames: card ${card} != ${name}`);
    await noPageOverflow();
    await shot("longnames-" + label);
    return name;
  }

  async function casePortraitLoaded(vp) {
    const label = vp.width === 390 ? "390" : "1440";
    await setup(vp, "loaded", "#/people");
    await waitReady("#/people", ".people-shell");
    // The default selection (Анна, NPC 2) has the fixture portrait.
    await page.waitForSelector(".people-portrait .portrait-img", { timeout: 15000 });
    await page.waitForFunction(() => {
      const i = document.querySelector(".people-portrait .portrait-img");
      return !!i && i.complete && i.naturalWidth > 0;
    }, undefined, { timeout: 15000 });
    const info = await page.$eval(".people-portrait .portrait-img", (i) => ({ w: i.naturalWidth, h: i.naturalHeight, alt: i.alt }));
    if (info.w <= 0 || info.h <= 0) throw new Error(`portrait not decoded: ${JSON.stringify(info)}`);
    if (info.alt !== "Портрет") throw new Error(`portrait alt ${info.alt}`);
    await shot("portrait-loaded-" + label);
    return `synthetic fixture SVG ${info.w}x${info.h} (not a real photo)`;
  }

  async function casePortraitMissing(vp) {
    const label = vp.width === 390 ? "390" : "1440";
    await setup(vp, "missing", "#/people");
    await waitReady("#/people", ".people-shell");
    // Every NPC portrait 404s -> the existing honest fallback.
    await page.waitForFunction(() => {
      const s = document.querySelector(".people-portrait .portrait-status");
      return !!s && s.textContent.includes("Портрет недоступен");
    }, undefined, { timeout: 15000 });
    if (await page.$(".people-portrait .portrait-img")) throw new Error("missing: an <img> rendered despite the 404");
    // Oracle: the fixture 404 registry entry of THIS document (a case can
    // never reuse an earlier document's registry) for the selected NPC's
    // portrait — not a console-string whitelist.
    if (!expectedHttp.some((e) => /\/visual\/characters\/2\/portrait$/.test(e.url) && e.status === 404 && e.doc === currentDoc)) {
      throw new Error("missing: the fixture 404 for the selected NPC portrait is not in this document's registry: " + JSON.stringify(expectedHttp));
    }
    await shot("portrait-missing-" + label);
    return "existing «Портрет недоступен» fallback + per-doc fixture 404 registry";
  }

  async function caseKeyboard(vp) {
    const label = vp.width === 390 ? "390" : "1440";
    await setup(vp, "populated", "#/people");
    await waitReady("#/people", ".people-shell");
    // Native keyboard: Tab through the real focus order (topbar tabs ->
    // hero actions -> strip cards) until the Борис card is focused.
    let focused = "";
    for (let i = 0; i < 40; i++) {
      await page.keyboard.press("Tab");
      focused = await page.evaluate(() => {
        const a = document.activeElement;
        if (!a || !a.className || !String(a.className).includes("people-card")) return "";
        const n = a.querySelector(".people-card-name");
        return n ? n.textContent.trim() : "";
      });
      if (focused === "Борис") break;
    }
    if (focused !== "Борис") throw new Error(`keyboard: could not Tab to the Борис card (last focus: ${focused || "none"})`);
    await page.keyboard.press("Enter"); // native activation of the focused button
    await page.waitForFunction(() => document.querySelector(".people-name").textContent === "Борис", undefined, { timeout: 8000 });
    const sel = await page.$eval(".people-card.sel .people-card-name", (n) => n.textContent.trim());
    if (sel !== "Борис") throw new Error(`keyboard: selected card ${sel} != Борис`);
    await shot("keyboard-" + label);
    return "Tab focus + Enter selection";
  }

  async function caseMeters(vp) {
    const label = vp.width === 390 ? "390" : "1440";
    await setup(vp, "populated", "#/people");
    await waitReady("#/people", ".people-shell");
    // All 5 selections: the signed table 0/+50/-50/+100/-100 and all three
    // status texts. Pure People UI selection — no POST.
    if ((await heroName()) !== "Анна") throw new Error("meters: default selection is not Анна");
    if ((await heroStatus()) !== "Друг") throw new Error("meters: Анна status != Друг");
    await checkMeters([40, 60, 0, 50], "Анна");
    await clickCard("Виктор");
    await page.waitForFunction(() => document.querySelector(".people-name").textContent === "Виктор", undefined, { timeout: 8000 });
    await checkMeters([100, 50, 100, 100], "Виктор");
    await clickCard("Дмитрий");
    await page.waitForFunction(() => document.querySelector(".people-name").textContent === "Дмитрий", undefined, { timeout: 8000 });
    if ((await heroStatus()) !== "Напряжённые") throw new Error("meters: Дмитрий status != Напряжённые");
    await checkMeters([-100, -100, 0, -100], "Дмитрий");
    await clickCard("Борис");
    await page.waitForFunction(() => document.querySelector(".people-name").textContent === "Борис", undefined, { timeout: 8000 });
    await checkMeters([-30, -10, 0, -50], "Борис");
    await clickCard("Галина");
    await page.waitForFunction(() => document.querySelector(".people-name").textContent === "Галина", undefined, { timeout: 8000 });
    if ((await heroStatus()) !== "Знакомый") throw new Error("meters: Галина status != Знакомый");
    await checkMeters([0, 0, 0, 0], "Галина");
    await shot("meters-" + label);
    return "5 selections, all 3 statuses, signed table 0/+50/-50/+100/-100";
  }

  async function caseWorld(vp) {
    const label = vp.width === 390 ? "390" : "1440";
    // The world first view is UNCHANGED: the m270 geometry truth.
    await setup(vp, "populated", "#/world");
    await page.waitForFunction((clock) => {
      const c = document.getElementById("clock");
      return location.hash === "#/world"
        && !!document.querySelector(".world-shell")
        && !!document.querySelector(".map-card")
        && !!c && c.textContent === clock;
    }, CLOCK, { timeout: 15000 });
    await page.waitForFunction(() => {
      const img = document.querySelector(".map-base");
      return !!img && img.complete && img.naturalWidth > 0;
    }, undefined, { timeout: 15000 });
    const px = await page.evaluate(() => {
      const card = document.querySelector(".map-card");
      const r = card.getBoundingClientRect();
      return Math.max(0, Math.min(r.bottom, window.innerHeight) - Math.max(r.top, 0));
    });
    const minPx = label === "390" ? 160 : 1; // m270 truth: mobile map >= 160px
    if (px < minPx) throw new Error(`world ${label}: map visible ${px}px < ${minPx}px`);
    const tabCount = await page.$$eval(".tabs button", (bs) => bs.length);
    if (tabCount !== 9) throw new Error(`world tabs ${tabCount} != 9`);
    await noPageOverflow();
    await shot("world-" + label);
    return `mapVisiblePx=${px}`;
  }

  async function caseProfile(vp) {
    const label = vp.width === 390 ? "390" : "1440";
    // Profile isolation: the profile shell renders, the people shell does
    // not leak in. The profile's GETs are all fixture-covered
    // (/characters/1, /locations/1, /characters/1/roles,
    // /visual/characters/1/portrait, /characters/1/desire) — any other
    // request is a harness breach (recorded + aborted + failing the final
    // audit).
    await setup(vp, "populated", "#/profile");
    await page.waitForSelector(".profile-shell", { timeout: 15000 });
    await waitClock();
    if (await page.$(".people-shell")) throw new Error("profile: the people shell leaked in");
    const active = await page.$eval(".tabs button.active", (b) => b.textContent.trim());
    if (active !== "Профиль") throw new Error(`profile: active tab ${active} != Профиль`);
    await shot("profile-" + label);
    return "profile shell, no people leak";
  }

  async function caseContrast(vp) {
    const label = vp.width === 390 ? "390" : "1440";
    // Dedicated contrast check (A2): runs independently of the style cases
    // so a style RED never masks it. Real composited bg, >=4.5 normal /
    // >=3 large, for the visible People text incl. muted/roles/status/
    // selected-card text/buttons.
    await setup(vp, "populated", "#/people");
    await waitReady("#/people", ".people-shell");
    const r = await checkContrast(".people-shell h2, .people-shell .people-name, .people-shell .people-status, .people-shell .people-card-name, .people-shell .people-card-status, .people-shell .people-rep-title, .people-shell .people-rep-reason, .people-shell .people-meter .row span, .people-shell .people-actions button", "contrast-" + label);
    await shot("contrast-" + label);
    return `checked=${r.checked} gradientBound=${r.gradientBound} solid=${r.solid}`;
  }

  const V1440 = { width: 1440, height: 900 };
  const V390 = { width: 390, height: 844 };
  const CASES = [
    ["style-1440", () => caseStyle(V1440)],
    ["style-390", () => caseStyle(V390)],
    ["empty-1440", () => caseEmpty(V1440)],
    ["empty-390", () => caseEmpty(V390)],
    ["error-1440", () => caseError(V1440)],
    ["error-390", () => caseError(V390)],
    ["longnames-1440", () => caseLongNames(V1440)],
    ["longnames-390", () => caseLongNames(V390)],
    ["portrait-loaded-1440", () => casePortraitLoaded(V1440)],
    ["portrait-loaded-390", () => casePortraitLoaded(V390)],
    ["portrait-missing-1440", () => casePortraitMissing(V1440)],
    ["portrait-missing-390", () => casePortraitMissing(V390)],
    ["keyboard-1440", () => caseKeyboard(V1440)],
    ["keyboard-390", () => caseKeyboard(V390)],
    ["meters-1440", () => caseMeters(V1440)],
    ["meters-390", () => caseMeters(V390)],
    ["world-1440", () => caseWorld(V1440)],
    ["world-390", () => caseWorld(V390)],
    ["profile-1440", () => caseProfile(V1440)],
    ["profile-390", () => caseProfile(V390)],
    ["contrast-1440", () => caseContrast(V1440)],
    ["contrast-390", () => caseContrast(V390)],
  ];

  // ---- run ----
  const started = Date.now();
  oracleSelfChecks();
  await guardSelfChecks();
  // Pre-connect WebSocket interception ONCE, before the first navigation:
  // close WITHOUT connectToServer(); unavailable -> refuse to navigate.
  if (typeof page.routeWebSocket !== "function") {
    throw new Error("page.routeWebSocket unavailable — refusing to navigate (no post-connect WS fallback)");
  }
  await page.routeWebSocket("**/*", (wsRoute) => {
    wsBlocks.push(wsRoute.url ? wsRoute.url() : "?");
    wsRoute.close();
  });
  // Warmup: a valid People document (ready clock) + the served-byte hashes
  // BEFORE any style assertion. A failure here is a real SETUP failure
  // (readiness / network / crypto), labelled as such.
  let served = null;
  try {
    await setup({ width: 1440, height: 900 }, "populated", "#/people");
    await waitReady("#/people", ".people-shell");
    served = await servedHashes();
  } catch (e) {
    return {
      base: BASE, stage: "setup", served, cases: [], caseCount: 0,
      expectedCaseCount: CASES.length, error: String((e && e.message) || e),
      unexpected, expectedHttp, wsBlocks, pageErrors, consoleErrors,
      pass: false, ms: Date.now() - started,
    };
  }
  // Each named case under its own try/catch: a failure records the named
  // case + actually-measured style + a failure screenshot, and the run
  // CONTINUES — no stop-after-first-red, no zero-results on a style fail.
  const results = [];
  for (const [name, fn] of CASES) {
    try {
      const detail = await fn();
      results.push({ name, ok: true, detail: detail || "" });
    } catch (e) {
      const measured = await measurePeople().catch(() => null);
      const screenshot = await shot(name + "-FAIL").catch(() => null);
      results.push({ name, ok: false, error: String((e && e.message) || e), measured, screenshot });
    }
  }
  const pass = results.length === CASES.length
    && results.every((r) => r.ok)
    && unexpected.length === 0
    && pageErrors.length === 0
    && consoleErrors.length === 0;
  return {
    base: BASE,
    served, // parent compares to the candidate file SHAs
    cases: results,
    caseCount: results.length,
    expectedCaseCount: CASES.length,
    unexpected, // MUST be [] — no allowlist of any kind
    expectedHttp, // labelled fixture 404/500 fulfillments (per document)
    wsBlocks,
    pageErrors,
    consoleErrors,
    pass,
    ms: Date.now() - started,
  };
}
