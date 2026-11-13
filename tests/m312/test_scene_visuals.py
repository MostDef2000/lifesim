"""#312 — Place scene visuals: the state-aware, cache-backed scene image.

Behavioral tests for the #312 Place scene-visuals in the REAL production
``backend/app/web/app.js`` (boot included), run inside a Node ``vm`` context
using the embedded stdlib harness below. The harness is an OWN copy of the
m303 production-app VM harness (the same minimal DOM / fake-timer /
controlled-network shim, the same driver helpers, the same response-stub
structure) extended additively with the #312 surface:

- ``makeResponse`` gains a controlled ``blob()`` (resolve / reject / hold,
  independent of ``json()``);
- ``URL.createObjectURL`` / ``revokeObjectURL`` are observable (unique ids, a
  create/revoke event log, double-revoke detection);
- ``AbortController`` / signal propagation is visible at the fetch boundary;
- fake timers capture ``setTimeout`` callbacks and fire them manually (the
  300s ``SCENE_REQUEST_TIMEOUT_MS`` deadline is fired by id, never by waiting);
- ``document.activeElement`` tracking + focus/blur events;
- deferred blob completion independent of deferred response completion;
- img decode-error simulation (separate from successful bytes).

The harness only provides the browser surface; it does NOT mock or
reimplement ``route()``, ``api()``, ``viewLocation()``, ``renderPlace()``,
``inspectSurroundings()``, ``buildSceneVisual()``, ``updateSceneVisual()``,
``fetchPortraitBlob()`` or any of the #312 logic. Every assertion is on an
observable fact: the rendered DOM (the ``.place-stage`` composition — hero
heading, weather line, confirmed-data summary, image host, ``role="status"``
line, ONE stable button), the fetch log (zero-POST on entry, the single
``/visual/scenes`` POST, the ``/visual/assets/{id}/file`` GETs, the aborts),
the URL lifecycle log, and the ``S`` scene state.

Approved behavior under test (derived from the work order, not the code):
- The ``.place-stage`` renders a text-first composition: hero heading
  «{location name} · {dayTime(S.world.game_timestamp)}» + a weather line from
  ``S.weather`` (enabled/description; missing time/weather → honest
  "unavailable" wording, NEVER a synthesized day 1 or empty-roster claim) + a
  confirmed-data text summary (from ``sceneFallbackData`` over the loaded
  /locations+/characters+/weather data) + an image host + a
  ``role="status" aria-live="polite"`` line + ONE stable button node.
- Button states: idle → «Показать сцену» (starts generation); generating →
  «Создаём сцену…» non-operable; ready → non-operable «Сцена показана» (node
  kept, aria-disabled + guarded handler); unavailable → «Повторить
  визуализацию» (retries). The button node is never deleted across state
  swaps (focus retention).
- Generation ONLY via explicit button click → the existing
  ``inspectSurroundings()`` POST path (single ``/visual/scenes`` call site).
  No POST on route entry, poll tick, renderer repetition, or return-with-cache.
  A second click while generating issues no second POST.
- After the POST resolves: freshness is validated BEFORE the file is consumed;
  the file is fetched via ``fetchPortraitBlob`` (cookie auth) with the same
  guards before AND after the blob; only then is an objectURL created and the
  state set ready.
- Cache: a valid asset already in memory means ZERO scene POST and ZERO file
  GET on the 5s tick, a route repaint, or a return to the place. Navigation
  away (no movement) detaches the image, revokes the mounted URL exactly once,
  and RETAINS the blob+metadata; the return recreates the URL from the cached
  blob.
- Movement (player location changes) discards the entry (blob+URL), revokes
  the URL exactly once, and resets to idle. A stale generation finishing after
  a route change or movement is silently discarded — no image, no state
  change, no toast on the new location.
- Remote place preview: NO generation button, NO scene POST ever; preview text
  + the existing move CTA only.
- Errors (503 / 404 / 502 / network / 300s deadline / img decode failure) →
  status "unavailable", short honest player-facing copy (never "Flux disabled"
  for all failures), the retry button, and the confirmed text + actions remain
  usable. Only the still-current flight may set unavailable. No automatic
  retry.
- Alt text is from the validated descriptor data, prefixed «Визуализация
  места …»; live nearby/weather always come from the loaded data (no
  fabricated object/NPC/status claims). No innerHTML anywhere.
- World surface: the ``#scene-view`` element is preserved; a scene-action
  success NO LONGER auto-triggers ``inspectSurroundings`` (presentation
  refresh only).

RED authenticity: the two REGRESSION_* scenarios are harness self-checks that
MUST pass on the base tree (no #312 implementation). Every other scenario
asserts #312 behavior and MUST fail on base for the right reason (missing
behavior in the failure trace — not an import error or harness bug).
"""
import json
import os
import subprocess

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
APP_JS = os.path.join(ROOT, "backend/app/web/app.js")
SCRATCH = "/tmp/opencode/m312-testing"

HARNESS_JS = r"""

// #303 behavioral harness (m310 pattern) — runs the REAL production app.js in a Node vm
// context with a minimal stdlib DOM/timer/fetch shim. It does NOT mock
// route()/api()/viewWorld()/buildLivingMap()/selectPoi()/moveTo() or
// reimplement any production logic; it only provides the browser surface
// (DOM, timers, network boundary) and collects observable facts (fetch log,
// DOM, S state, gameUiContext). Python asserts on the facts.
import fs from "fs";
import vm from "vm";

const appJsPath = process.argv[2];
const scenariosPath = process.argv[3];
const appSrc = fs.readFileSync(appJsPath, "utf-8");
const scenarios = JSON.parse(fs.readFileSync(scenariosPath, "utf-8"));

// #303: unhandled rejections are a first-class observable — a stale place
// load or a superseded load that throws must not escape.
const __unhandled = [];
process.on("unhandledRejection", (reason) => {
  __unhandled.push(String((reason && reason.message) || reason));
});

// Expose the lexical top-level bindings (const S/app/routes + the #302
// module-level lets) to the driver. Appended to the in-memory source only —
// the production file is untouched. The #302 lets are read through getters
// so a reassignment (resetGameUiContext) or increment (navEpoch++) is always
// observed; the typeof guards keep the harness loadable against a base
// app.js that lacks the #310 machinery (RED authenticity).
const EXPOSE = [
  "\n;globalThis.__S = S; globalThis.__app = app; globalThis.__routes = routes;",
  "globalThis.__guc = () => (typeof gameUiContext !== 'undefined' ? gameUiContext : null);",
  "globalThis.__navEpoch = () => (typeof navEpoch !== 'undefined' ? navEpoch : 0);",
  "globalThis.__obsLoc = () => " +
  "(typeof observedPlayerLocId !== 'undefined' ? observedPlayerLocId : null);",
  "\n",
].join("");

// The shim installs the browser surface on globalThis. It is infrastructure
// only: no product behavior lives here.
const SHIM = `
"use strict";
(function () {
  // #312: the currently focused element (focus/blur track it; the
  // button-node focus-retention contract reads document.activeElement).
  let __activeElement = null;
  // ---------- classList ----------
  function makeClassList(el) {
    return {
      _el: el,
      add(...c) {
        const s = new Set(this._classes());
        c.forEach(x => s.add(x));
        el.className = [...s].join(" ");
      },
      remove(...c) {
        const s = new Set(this._classes());
        c.forEach(x => s.delete(x));
        el.className = [...s].join(" ");
      },
      toggle(c, force) {
        const s = new Set(this._classes());
        const has = s.has(c);
        const want = force === undefined ? !has : !!force;
        if (want) s.add(c); else s.delete(c);
        el.className = [...s].join(" ");
        return want;
      },
      contains(c) { return this._classes().includes(c); },
      _classes() { return (el.className || "").split(/\\s+/).filter(Boolean); },
    };
  }

  // ---------- node ----------
  let NODE_ID = 0;
  function makeNode(nodeType) {
    return { nodeType, _id: ++NODE_ID, _parent: null, _children: [] };
  }
  function makeText(text) {
    const n = makeNode(3);
    n.textContent = String(text);
    return n;
  }
  function makeElement(tag) {
    const n = makeNode(1);
    n.tagName = String(tag).toUpperCase();
    n.className = "";
    n.attributes = {};
    n.style = { removeProperty(k) { delete n.style[k]; } };
    n.value = "";
    n.disabled = false;
    n.checked = false;
    n.hidden = false;
    n.scrollTop = 0;
    n.scrollHeight = 0;
    n.files = null;
    n.classList = makeClassList(n);
    n.onclick = null;
    n._listeners = {};
    Object.defineProperty(n, "textContent", {
      get() {
        let s = "";
        (function walk(c) {
          for (const k of c._children) {
            if (k.nodeType === 3) s += k.textContent;
            else walk(k);
          }
        })(n);
        return s;
      },
      set(v) {
        n._children.forEach(c => { c._parent = null; });
        n._children = [];
        if (v !== null && v !== undefined && String(v) !== "") {
          const t = makeText(v);
          t._parent = n;
          n._children.push(t);
        }
      },
    });
    // #312: focus/blur track document.activeElement and fire focus/blur
    // events (the m303 shim only toggled _focused).
    n.focus = function () {
      n._focused = true;
      __activeElement = n;
      (n._listeners["focus"] || []).forEach((fn) => fn({ type: "focus", target: n }));
    };
    n.blur = function () {
      n._focused = false;
      if (__activeElement === n) __activeElement = null;
      (n._listeners["blur"] || []).forEach((fn) => fn({ type: "blur", target: n }));
    };
    n.click = function () {
      if (n.onclick) n.onclick({ target: n, currentTarget: n, preventDefault(){} });
    };
    n.scrollIntoView = function () {};
    n.setAttribute = function (k, v) {
      n.attributes[k] = String(v);
      if (k === "class") n.className = String(v);
      if (k === "id") n.id = String(v);
      if (k === "value") n.value = String(v);
      if (k === "hidden") n.hidden = true;
    };
    n.getAttribute = function (k) {
      return Object.prototype.hasOwnProperty.call(n.attributes, k)
        ? n.attributes[k] : null;
    };
    n.append = function (...kids) { appendKids(n, kids); };
    n.appendChild = function (kid) { appendKids(n, [kid]); return kid; };
    n.replaceChildren = function (...kids) {
      n._children.forEach(c => { c._parent = null; });
      n._children = [];
      n.textContent = "";
      appendKids(n, kids);
    };
    n.insertBefore = function (kid, ref) {
      appendKids(n, [kid]);
      const i = n._children.indexOf(ref);
      if (i >= 0) { n._children.splice(i, 0, n._children.pop()); }
    };
    n.remove = function () {
      if (n._parent) {
        const i = n._parent._children.indexOf(n);
        if (i >= 0) n._parent._children.splice(i, 1);
        n._parent = null;
      }
    };
    n.contains = function (other) {
      let c = other;
      while (c) { if (c === n) return true; c = c._parent; }
      return false;
    };
    n.closest = function (sel) {
      let c = n;
      while (c && c.nodeType === 1) {
        if (matchSel(c, sel)) return c;
        c = c._parent;
      }
      return null;
    };
    n.querySelector = function (sel) { return queryIn(n, sel, false); };
    n.querySelectorAll = function (sel) { return queryIn(n, sel, true); };
    n.addEventListener = function (type, fn) {
      (n._listeners[type] = n._listeners[type] || []).push(fn);
    };
    n.removeEventListener = function () {};
    n.firstElementChild = function () { return n._children.find(c => c.nodeType === 1) || null; };
    Object.defineProperty(n, "children", {
      get() { return n._children.filter(c => c.nodeType === 1); },
    });
    Object.defineProperty(n, "id", {
      get() { return n.attributes.id || ""; },
      set(v) { n.attributes.id = String(v); },
    });
    return n;
  }
  function appendKids(parent, kids) {
    for (const kid of kids) {
      if (kid === null || kid === undefined || typeof kid === "boolean") continue;
      if (Array.isArray(kid)) { appendKids(parent, kid); continue; }
      const node = (kid && kid.nodeType) ? kid : makeText(kid);
      if (node._parent) node.remove();
      node._parent = parent;
      parent._children.push(node);
    }
  }
  // minimal selector: tag, #id, .class, compound .a.b, tag.class
  function matchSel(el, sel) {
    sel = sel.trim();
    if (!sel) return false;
    const mId = sel.match(/^#([\\w-]+)$/);
    if (mId) return el.attributes.id === mId[1];
    const mTag = sel.match(/^([a-zA-Z][\\w-]*)$/);
    if (mTag) return el.tagName === mTag[1].toUpperCase();
    let tag = null; const classes = [];
    const re = /([a-zA-Z][\\w-]*|\\.[\\w-]+|#([\\w-]+))/g;
    let m;
    while ((m = re.exec(sel))) {
      if (m[0].startsWith(".")) classes.push(m[0].slice(1));
      else if (m[0].startsWith("#")) { if (el.attributes.id !== m[2]) return false; }
      else tag = m[0].toUpperCase();
    }
    if (tag && el.tagName !== tag) return false;
    const elClasses = (el.className || "").split(/\\s+/).filter(Boolean);
    return classes.length > 0 && classes.every(c => elClasses.includes(c));
  }
  function queryIn(root, sel, all) {
    const out = [];
    (function walk(n) {
      for (const c of n._children) {
        if (c.nodeType === 1) {
          if (matchSel(c, sel)) { out.push(c); if (!all) return; }
          walk(c);
        }
      }
    })(root);
    return all ? out : (out[0] || null);
  }
  // first descendant (pre-order) matching a predicate — the minimal selector
  // above has no descendant combinator, so the driver uses this for "the
  // select inside #chat-view", "the «← К месту» anchor", "the «Диалог»
  // button", etc.
  function findDescendant(root, pred) {
    let found = null;
    (function walk(n) {
      for (const c of n._children) {
        if (c.nodeType === 1) {
          if (pred(c)) { found = c; return; }
          walk(c);
        }
      }
    })(root);
    return found;
  }

  // ---------- document ----------
  const body = makeElement("body");
  const documentElement = makeElement("html");
  documentElement._parent = null;
  body._parent = documentElement;
  documentElement._children.push(body);
  const appEl = makeElement("div"); appEl.attributes.id = "app";
  const toastEl = makeElement("div");
  toastEl.attributes.id = "toast";
  toastEl.className = "toast hidden";
  body.append(appEl, toastEl);
  function findById(id) {
    let found = null;
    (function walk(n) {
      for (const c of n._children) {
        if (c.nodeType === 1 && c.attributes.id === id) { found = c; return; }
        walk(c);
      }
    })(documentElement);
    return found;
  }
  const document = {
    body,
    documentElement,
    createElement: (t) => makeElement(t),
    createElementNS: (ns, t) => makeElement(t),
    createTextNode: (t) => makeText(t),
    getElementById: (id) => findById(id),
    querySelector: (sel) => queryIn(documentElement, sel, false),
    querySelectorAll: (sel) => queryIn(documentElement, sel, true),
    get activeElement() { return __activeElement; },
  };

  // ---------- window / location / history ----------
  const winListeners = {};
  const window = {
    addEventListener: (t, fn) => { (winListeners[t] = winListeners[t] || []).push(fn); },
    removeEventListener: () => {},
    matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }),
    localStorage: {
      _m: new Map(),
      getItem(k) { return this._m.has(k) ? this._m.get(k) : null; },
      setItem(k, v) { this._m.set(k, String(v)); },
      removeItem(k) { this._m.delete(k); },
    },
  };
  let _hash = "";
  const location = {
    get hash() { return _hash; },
    set hash(v) {
      const old = _hash;
      _hash = v;
      if (old === v) return;  // browser: no hashchange when the value is unchanged
      queueMicrotask(() => {
        (winListeners["hashchange"] || []).forEach(fn => fn({ type: "hashchange" }));
      });
    },
    protocol: "http:",
    host: "localhost",
    href: "http://localhost/",
    assign() {}, reload() {}, replace() {},
  };
  const history = { replaceState() {}, pushState() {} };

  // ---------- URL / WebSocket ----------
  // #312: createObjectURL/revokeObjectURL are observable — unique ids,
  // a create/revoke event log, and double-revoke detection (the
  // no-leaked-URL and revoke-exactly-once contracts).
  let __urlSeq = 0;
  const __urlLog = [];
  const URL = {
    createObjectURL() {
      const id = "blob:mock-" + (++__urlSeq);
      __urlLog.push({ op: "create", id });
      return id;
    },
    revokeObjectURL(id) { __urlLog.push({ op: "revoke", id }); },
  };
  // #312: a minimal Blob (the controlled blob() in makeResponse resolves one).
  class Blob {
    constructor(parts, opts) {
      this.parts = parts || [];
      this.type = (opts && opts.type) || "";
    }
  }
  // #312: AbortController — the abort is observable at the fetch boundary.
  class AbortController {
    constructor() {
      this.signal = {
        aborted: false,
        addEventListener(type, fn) { if (type === "abort") this._l.push(fn); },
        removeEventListener() {},
        _l: [],
      };
    }
    abort() {
      if (this.signal.aborted) return;
      this.signal.aborted = true;
      this.signal._l.slice().forEach((fn) => fn());
    }
  }
  const __ws = { count: 0 };
  class WebSocket {
    constructor(url) {
      __ws.count++;
      this.url = url;
      this.readyState = 0;
      this.onopen = null;
      this.onmessage = null;
      this.onclose = null;
      this.onerror = null;
    }
    send() {} close() { this.readyState = 3; }
  }

  // ---------- fake timers ----------
  const __timers = {
    intervalCount: 0, timeouts: new Map(), intervals: new Map(),
    nextId: 1, now: 0,
  };
  function setTimeout(fn, ms) {
    const id = __timers.nextId++;
    // #312: keep ms so the 300s scene deadline can be fired by id.
    __timers.timeouts.set(id, { fn, at: __timers.now + (ms || 0), ms: ms || 0 });
    return id;
  }
  function clearTimeout(id) { __timers.timeouts.delete(id); }
  function setInterval(fn, ms) {
    const id = __timers.nextId++;
    __timers.intervalCount++;
    __timers.intervals.set(id, {
      fn, at: __timers.now + (ms || 0), ms: ms || 0,
    });
    return id;
  }
  function clearInterval(id) { __timers.intervals.delete(id); }
  function __advance(ms) {
    const target = __timers.now + ms;
    for (;;) {
      let nextId = null, nextAt = Infinity;
      for (const [id, t] of __timers.timeouts)
        if (t.at <= target && t.at < nextAt) { nextAt = t.at; nextId = id; }
      for (const [id, t] of __timers.intervals)
        if (t.at <= target && t.at < nextAt) { nextAt = t.at; nextId = id; }
      if (nextId === null) break;
      __timers.now = nextAt;
      const to = __timers.timeouts.get(nextId), iv = __timers.intervals.get(nextId);
      if (to) { __timers.timeouts.delete(nextId); to.fn(); }
      else if (iv) {
        __timers.intervals.delete(nextId);
        __timers.intervals.set(nextId, {
          fn: iv.fn, at: __timers.now + iv.ms, ms: iv.ms,
        });
        iv.fn();
      }
    }
    __timers.now = target;
  }
  // #312: manual timer firing — the 300s scene deadline is fired by id,
  // not by advancing fake time past every other timer.
  function __pendingTimeouts() {
    const out = [];
    for (const [id, t] of __timers.timeouts) out.push({ id, at: t.at, ms: t.ms });
    return out;
  }
  function __fireTimeout(id) {
    const t = __timers.timeouts.get(id);
    if (!t) return false;
    __timers.timeouts.delete(id);
    __timers.now = Math.max(__timers.now, t.at);
    t.fn();
    return true;
  }
  function __fireDeadline() {
    for (const [id, t] of __timers.timeouts) {
      if (t.ms === 300000) {
        __timers.timeouts.delete(id);
        __timers.now = Math.max(__timers.now, t.at);
        t.fn();
        return true;
      }
    }
    return false;
  }

  // ---------- controlled network boundary ----------
  // Every request is logged with method+path (the zero-POST assertion reads
  // the log) and the raw fetch opts (the POST /actions body assertion reads
  // opts.body — api() JSON-stringifies it before fetch).
  const __net = {
    log: [], held: [], blobHeld: [], responses: {},
    catchAll: { status: 404, body: { detail: "not found" } },
  };
  function makeResponse(spec, url) {
    const ok = spec.status >= 200 && spec.status < 300;
    const resp = {
      status: spec.status, ok,
      json: () => Promise.resolve(spec.body === undefined ? null : spec.body),
    };
    // #312: controlled blob() — resolve/reject/hold independently of json().
    if (spec.__blobHold) {
      resp.blob = () => new Promise((resolve, reject) => {
        const hold = {
          url: String(url),
          resolve: () => resolve(new Blob([spec.__blobBytes || "img"])),
          reject: (e) => reject(e),
        };
        __net.blobHeld.push(hold);
      });
    } else if (spec.__blobReject) {
      resp.blob = () => Promise.reject(spec.__blobReject);
    } else {
      resp.blob = () => Promise.resolve(new Blob([spec.__blobBytes || "img"]));
    }
    return resp;
  }
  function fetch(url, opts) {
    const entry = { url: String(url), opts: opts || {}, held: false, aborted: false };
    __net.log.push(entry);
    let spec = __net.responses[String(url)];
    if (!spec) spec = __net.catchAll;
    // #312: a network failure rejects like a real network error.
    if (spec.__networkFail) {
      return Promise.reject(new Error("network failure"));
    }
    const signal = opts && opts.signal;
    if (spec.__hold) {
      entry.held = true;
      __net.held.push(entry);
      return new Promise((resolve, reject) => {
        entry._resolve = () => {
          __net.held = __net.held.filter(h => h !== entry);
          resolve(makeResponse(entry._resp || spec, url));
        };
        entry._reject = (e) => {
          __net.held = __net.held.filter(h => h !== entry);
          reject(e);
        };
        // #312: the abort is observed at the network boundary.
        if (signal) {
          if (signal.aborted) {
            entry.aborted = true;
            __net.held = __net.held.filter(h => h !== entry);
            reject(new Error("AbortError"));
          } else {
            signal.addEventListener("abort", () => {
              entry.aborted = true;
              __net.held = __net.held.filter(h => h !== entry);
              if (entry._reject) entry._reject(new Error("AbortError"));
            });
          }
        }
      });
    }
    if (signal && signal.aborted) {
      entry.aborted = true;
      return Promise.reject(new Error("AbortError"));
    }
    return Promise.resolve().then(() => makeResponse(spec, url));
  }
  function __setResponse(url, spec) { __net.responses[String(url)] = spec; }
  function __hold(url) {
    const cur = __net.responses[String(url)] || { status: 200, body: null };
    __net.responses[String(url)] = Object.assign({}, cur, { __hold: true });
  }
  function __release(url, spec) {
    const entry = __net.held.find(h => h.url === String(url));
    if (!entry) return false;
    entry._resp = spec;
    // #312: release unpins the endpoint — the hold pins the in-flight request,
    // later legs must reach the network (a sticky flag would hold every
    // subsequent POST to this URL forever).
    const cur = __net.responses[String(url)];
    if (cur && cur.__hold) {
      const next = Object.assign({}, cur);
      delete next.__hold;
      __net.responses[String(url)] = next;
    }
    entry._resolve();
    return true;
  }
  function __heldCount(url) {
    return __net.held.filter(h => h.url === String(url)).length;
  }
  // #312: controlled blob completion — release/reject a held blob by URL.
  function __releaseBlob(url, bytes) {
    const hold = __net.blobHeld.find(h => h.url === String(url));
    if (!hold) return false;
    __net.blobHeld = __net.blobHeld.filter(h => h !== hold);
    hold.resolve(new Blob([bytes || "img"]));
    return true;
  }
  function __rejectBlob(url, msg) {
    const hold = __net.blobHeld.find(h => h.url === String(url));
    if (!hold) return false;
    __net.blobHeld = __net.blobHeld.filter(h => h !== hold);
    hold.reject(new Error(msg || "blob failure"));
    return true;
  }
  function __blobHeldCount(url) {
    return __net.blobHeld.filter(h => h.url === String(url)).length;
  }

  // ---------- driver helpers ----------
  async function __settle() {
    for (let i = 0; i < 400; i++) { await Promise.resolve(); await Promise.resolve(); }
  }
  function __setHash(h) { location.hash = h; }
  function __call(name, ...args) { return globalThis[name](...args); }
  // click the first BUTTON whose text matches (the POI card's «Пойти сюда» /
  // «Закрыть» / «×», the map toolbar, ...). Returns whether one was found.
  function __clickButton(text) {
    const b = findDescendant(documentElement,
      (n) => n.tagName === "BUTTON" && n.textContent === text);
    if (b) { b.click(); return true; }
    return false;
  }
  // #310: click the first ANCHOR whose text matches, simulating the browser's
  // same-document hash navigation for an href="#/..." anchor. The shim's
  // n.click() only fires onclick, which a plain href anchor has none of — a
  // real click on <a href="#/location/9"> changes location.hash, which is
  // exactly what this does (the hash setter fires the hashchange listener).
  function __clickAnchor(text) {
    const a = findDescendant(documentElement,
      (n) => n.tagName === "A" && n.textContent === text);
    if (!a) return false;
    const href = a.getAttribute("href");
    if (href && href.startsWith("#")) location.hash = href;
    return true;
  }
  // #310: fire the window "keydown" listeners (the Escape close path binds a
  // window keydown listener via bindPoiSelectionKeys).
  function __fireKey(key) {
    (winListeners["keydown"] || []).forEach(fn =>
      fn({ key, preventDefault() {}, stopPropagation() {} }));
  }
  // #312: the Place stage button / image + img decode-error simulation.
  function __stageEl() { return document.querySelector(".place-stage"); }
  function __stageButton() {
    const stage = __stageEl();
    return stage
      ? findDescendant(stage, (n) => n.nodeType === 1 && n.tagName === "BUTTON")
      : null;
  }
  function __stageImg() {
    const stage = __stageEl();
    return stage
      ? findDescendant(stage, (n) => n.nodeType === 1 && n.tagName === "IMG")
      : null;
  }
  function __fireImgError(img) {
    if (!img) return false;
    const l = img._listeners["error"] || [];
    l.slice().forEach((fn) => fn({ type: "error", target: img }));
    return l.length > 0;
  }
  function __facts() {
    const S = globalThis.__S;
    const g = (typeof globalThis.__guc === "function") ? globalThis.__guc() : null;
    const main = document.querySelector("main.place-shell.place-game");
    // Composition-agnostic "the place screen rendered" guard (present on
    // both the #303 shell and the pre-#303 base) — the contract-neutral
    // scenarios (no-POST entry, retry, stale guard) assert this, not the
    // new .place-game class.
    const placeShell = document.querySelector("main.place-shell");
    // Composition-agnostic rendered place name: .place-context-name on the
    // #303 shell, the hero <h2> on the base — the stale-guard scenario
    // distinguishes "place 9 rendered" from "place 7 rendered" on both.
    const heroEl = document.querySelector(".place-hero");
    const placeNameEl = document.querySelector(".place-context-name")
      || (heroEl ? findDescendant(heroEl, (n) => n.tagName === "H2") : null);
    const topbarEl = document.querySelector(".place-topbar");
    const backBtn = topbarEl
      ? findDescendant(topbarEl, (n) => n.tagName === "BUTTON") : null;
    const titleSpan = topbarEl
      ? findDescendant(topbarEl, (n) => n.tagName === "SPAN") : null;
    const grid = document.querySelector(".place-grid");
    const stage = document.querySelector(".place-stage");
    const context = document.querySelector(".panel.place-context");
    const contextNameEl = document.querySelector(".place-context-name");
    // Always-visible context text — the <details> subtree is EXCLUDED
    // (the reference level is collapsed; its content must not leak into
    // the primary view).
    let contextTextNoDetails = "";
    if (context) {
      (function walk(n) {
        for (const c of n._children) {
          if (c.nodeType === 3) contextTextNoDetails += c.textContent;
          else if (c.nodeType === 1 && c.tagName !== "DETAILS") walk(c);
        }
      })(context);
    }
    const badge = document.querySelector(".place-badge");
    const actionsBlock = document.querySelector(".place-actions-block");
    const actionButtons = [];
    if (actionsBlock) {
      (function walk(n) {
        for (const c of n._children) {
          if (c.nodeType !== 1) continue;
          if (c.tagName === "BUTTON") actionButtons.push(c.textContent);
          walk(c);
        }
      })(actionsBlock);
    }
    const sceneAct = document.querySelector(".place-scene-act");
    const sceneActInput = document.getElementById("scene-act-input");
    const resultHost = document.querySelector(".place-scene-act-result");
    const resultNode = document.getElementById("scene-act-result");
    const goHere = findDescendant(documentElement,
      (n) => n.tagName === "BUTTON" && n.textContent === "Пойти сюда");
    const youAreAt = context
      ? findDescendant(context, (n) => n.nodeType === 1
          && n.textContent.indexOf("Вы сейчас: ") === 0)
      : null;
    const nearby = document.querySelector(".place-nearby");
    const nearbyRows = [];
    let nearbyEmpty = false;
    if (nearby) {
      nearbyEmpty = nearby.textContent.indexOf("Рядом никого нет.") >= 0;
      (function walk(n) {
        for (const c of n._children) {
          if (c.nodeType !== 1) continue;
          if (c.tagName === "DIV"
              && (c.className || "").indexOf("place-nearby-row") >= 0) {
            const span = findDescendant(c, (n) => n.tagName === "SPAN");
            const btn = findDescendant(c, (n) => n.tagName === "BUTTON");
            nearbyRows.push({
              name: span ? span.textContent : null,
              dialog: !!btn,
            });
          }
          walk(c);
        }
      })(nearby);
    }
    let dialogButtonCount = 0;
    (function walk(n) {
      for (const c of n._children) {
        if (c.nodeType !== 1) continue;
        if (c.tagName === "BUTTON" && c.textContent === "Поговорить") dialogButtonCount++;
        walk(c);
      }
    })(documentElement);
    const taskEl = document.querySelector(".place-task");
    const details = document.querySelector("details.place-about");
    const detailsSummary = details
      ? (findDescendant(details, (n) => n.tagName === "SUMMARY")
          || { textContent: null }).textContent
      : null;
    // The weather/time lines: inside .place-context on the #303 shell; on a
    // base app.js (no .place-context) the fallback walks the whole document
    // so the honest-missing wording stays observable (RED authenticity).
    const weatherLine = findDescendant(
      context || documentElement,
      (n) => n.nodeType === 1 && n.textContent.indexOf("Погода в мире: ") === 0);
    const timeLine = findDescendant(
      context || documentElement,
      (n) => n.nodeType === 1 && n.textContent.indexOf("Время мира: ") === 0);
    const retryBtn = findDescendant(documentElement,
      (n) => n.tagName === "BUTTON" && n.textContent === "Повторить");
    const failLine = findDescendant(documentElement,
      (n) => n.tagName === "SPAN" && n.textContent.indexOf("Загрузка не удалась") === 0);
    const toastEl = document.getElementById("toast");
    const postActions = __net.log.filter(
      e => (e.opts.method || "GET").toUpperCase() === "POST" && e.url === "/actions");
    const postSceneAct = __net.log.filter(
      e => (e.opts.method || "GET").toUpperCase() === "POST" && e.url === "/scene/act");
    // DEFECT-1: the Place screen must never call Flux (/visual/scenes) —
    // there is no #scene-view host to display it.
    const postVisualScenes = __net.log.filter(
      e => (e.opts.method || "GET").toUpperCase() === "POST" && e.url === "/visual/scenes");
    const posts = __net.log.filter(
      e => (e.opts.method || "GET").toUpperCase() === "POST");
    // #312 scene-visual facts: the Place stage composition, the URL
    // lifecycle, the scene entry state, and abort observability.
    const stageHeading = stage
      ? findDescendant(stage, (n) => n.nodeType === 1 && /^H[1-4]$/.test(n.tagName))
      : null;
    const stageStatus = stage
      ? findDescendant(stage, (n) => n.nodeType === 1
          && n.getAttribute("role") === "status")
      : null;
    const stageButton = stage
      ? findDescendant(stage, (n) => n.nodeType === 1 && n.tagName === "BUTTON")
      : null;
    const stageImg = stage
      ? findDescendant(stage, (n) => n.nodeType === 1 && n.tagName === "IMG")
      : null;
    const urlCreated = __urlLog.filter((e) => e.op === "create");
    const urlRevoked = __urlLog.filter((e) => e.op === "revoke");
    const urlLeaked = urlCreated
      .filter((c) => !urlRevoked.some((r) => r.id === c.id)).map((e) => e.id);
    const revokeCounts = {};
    urlRevoked.forEach((e) => { revokeCounts[e.id] = (revokeCounts[e.id] || 0) + 1; });
    const doubleRevoked = Object.keys(revokeCounts).filter((k) => revokeCounts[k] > 1);
    const sceneEntry = S && S.scene;
    const sceneFact = sceneEntry
      ? {
          status: sceneEntry.status !== undefined ? sceneEntry.status : null,
          state: sceneEntry.state !== undefined ? sceneEntry.state : null,
          locationId: sceneEntry.locationId !== undefined ? sceneEntry.locationId : null,
          assetId: sceneEntry.assetId !== undefined
            ? sceneEntry.assetId
            : (sceneEntry.asset && sceneEntry.asset.id !== undefined
              ? sceneEntry.asset.id : null),
        }
      : null;
    const sceneCacheSize = (S && S.sceneCache
      && typeof S.sceneCache.size === "number") ? S.sceneCache.size : null;
    const sceneViewEl = document.getElementById("scene-view");
    const fileGets = __net.log.filter(
      e => (e.opts.method || "GET").toUpperCase() === "GET"
        && e.url.indexOf("/visual/assets/") === 0 && e.url.indexOf("/file") >= 0
    ).map(e => e.url);
    const abortedFetches = __net.log.filter(e => e.aborted).map(e => e.url);
    return {
      hash: location.hash,
      hasPlaceShell: !!placeShell,
      placeName: placeNameEl ? placeNameEl.textContent : null,
      hasPlaceGame: !!main,
      hasPlaceGrid: !!grid,
      hasPlaceStage: !!stage,
      hasPlaceContext: !!context,
      hasPlaceTopbar: !!topbarEl,
      backButtonText: backBtn ? backBtn.textContent : null,
      topbarTitle: titleSpan ? titleSpan.textContent : null,
      stageText: stage ? stage.textContent : null,
      contextName: contextNameEl ? contextNameEl.textContent : null,
      contextTextNoDetails,
      badgeText: badge ? badge.textContent : null,
      badgeHere: !!document.querySelector(".place-badge-here"),
      hasActionsBlock: !!actionsBlock,
      actionButtons,
      hasSceneAct: !!sceneAct,
      hasSceneActInput: !!sceneActInput,
      sceneActResultInHost: !!(resultNode && resultHost && resultHost.contains(resultNode)),
      sceneActResultText: resultNode ? resultNode.textContent : null,
      hasGoHere: !!goHere,
      youAreAtText: youAreAt ? youAreAt.textContent : null,
      nearbyRows,
      nearbyEmpty,
      dialogButtonCount,
      taskText: taskEl ? taskEl.textContent : null,
      hasDetails: !!details,
      detailsSummary,
      detailsText: details ? details.textContent : null,
      detailsInContext: !!(details && context && context.contains(details)),
      weatherLineText: weatherLine ? weatherLine.textContent : null,
      timeLineText: timeLine ? timeLine.textContent : null,
      hasRetryButton: !!retryBtn,
      failLineText: failLine ? failLine.textContent : null,
      S_charLocId: S.character ? S.character.location_id : null,
      S_worldTs: S.world ? S.world.game_timestamp : null,
      guc: g ? {
        npcId: g.npcId,
        viewLocationId: g.viewLocationId,
        selectedLocationId: g.selectedLocationId,
      } : null,
      postActions: postActions.length,
      postActionBodies: postActions.map(e => {
        try { return JSON.parse(e.opts.body); } catch (err) { return e.opts.body; }
      }),
      postSceneAct: postSceneAct.length,
      postSceneActBodies: postSceneAct.map(e => {
        try { return JSON.parse(e.opts.body); } catch (err) { return e.opts.body; }
      }),
      postVisualScenes: postVisualScenes.length,
      // #312: the /visual/scenes POST bodies (the captured location_id).
      postVisualScenesBodies: postVisualScenes.map(e => {
        try { return JSON.parse(e.opts.body); } catch (err) { return e.opts.body; }
      }),
      postCount: posts.length,
      getPaths: __net.log.filter(
        e => (e.opts.method || "GET").toUpperCase() === "GET").map(e => e.url),
      toastText: toastEl ? toastEl.textContent : null,
      toastVisible: toastEl ? !toastEl.classList.contains("hidden") : false,
      stageHeadingText: stageHeading ? stageHeading.textContent : null,
      stageStatusText: stageStatus ? stageStatus.textContent : null,
      stageStatusAriaLive: stageStatus ? stageStatus.getAttribute("aria-live") : null,
      stageButtonText: stageButton ? stageButton.textContent : null,
      stageButtonDisabled: stageButton ? !!stageButton.disabled : null,
      stageButtonAriaDisabled: stageButton ? stageButton.getAttribute("aria-disabled") : null,
      stageImgSrc: stageImg ? stageImg.getAttribute("src") : null,
      stageImgAlt: stageImg ? stageImg.getAttribute("alt") : null,
      urlCreated: urlCreated.length,
      urlRevoked: urlRevoked.length,
      urlRevokedIds: urlRevoked.map((e) => e.id),
      urlLeaked: urlLeaked,
      doubleRevoked: doubleRevoked,
      activeElementText: __activeElement ? __activeElement.textContent : null,
      activeElementIsStageButton: !!(stageButton && __activeElement === stageButton),
      scene: sceneFact,
      sceneCacheSize: sceneCacheSize,
      sceneViewCount: document.querySelectorAll("#scene-view").length,
      sceneViewText: sceneViewEl ? sceneViewEl.textContent : null,
      fileGets: fileGets,
      abortedFetches: abortedFetches,
    };
  }

  // install on globalThis
  globalThis.document = document;
  globalThis.window = window;
  globalThis.location = location;
  globalThis.history = history;
  globalThis.URL = URL;
  globalThis.WebSocket = WebSocket;
  globalThis.fetch = fetch;
  globalThis.setTimeout = setTimeout;
  globalThis.clearTimeout = clearTimeout;
  globalThis.setInterval = setInterval;
  globalThis.clearInterval = clearInterval;
  globalThis.__net = __net;
  globalThis.__ws = __ws;
  globalThis.__timers = __timers;
  globalThis.__settle = __settle;
  globalThis.__setHash = __setHash;
  globalThis.__setInitialHash = (h) => { _hash = String(h); };
  globalThis.__call = __call;
  globalThis.__facts = __facts;
  globalThis.__setResponse = __setResponse;
  globalThis.__hold = __hold;
  globalThis.__release = __release;
  globalThis.__heldCount = __heldCount;
  globalThis.__clickButton = __clickButton;
  globalThis.__clickAnchor = __clickAnchor;
  globalThis.__fireKey = __fireKey;
  globalThis.__advance = __advance;
  // #312: the additive driver surface.
  globalThis.__pendingTimeouts = __pendingTimeouts;
  globalThis.__fireTimeout = __fireTimeout;
  globalThis.__fireDeadline = __fireDeadline;
  globalThis.__releaseBlob = __releaseBlob;
  globalThis.__rejectBlob = __rejectBlob;
  globalThis.__blobHeldCount = __blobHeldCount;
  globalThis.__stageEl = __stageEl;
  globalThis.__stageButton = __stageButton;
  globalThis.__stageImg = __stageImg;
  globalThis.__fireImgError = __fireImgError;
  globalThis.__urlLog = __urlLog;
  // #312: scope a leg's urlCreated/urlLeaked assertions to that leg only —
  // the log is cumulative across legs within a scenario.
  globalThis.__resetUrlLog = () => { __urlLog.length = 0; };
  globalThis.Blob = Blob;
  globalThis.AbortController = AbortController;
})();
`;

function runScenarioAsync(name, spec) {
  __unhandled.length = 0;  // per-scenario unhandled-rejection window
  const context = {
    console, queueMicrotask, Promise, JSON, Math, Object, Array, String,
    Number, Boolean, Set, Map, Error, RegExp, Symbol, parseInt,
    parseFloat, isNaN, isFinite, encodeURIComponent, decodeURIComponent,
  };
  vm.createContext(context);
  try {
    vm.runInContext(SHIM, context, { filename: "shim.js" });
    // event-free initial address: only the app's boot route() dispatches
    vm.runInContext(`__setInitialHash(${JSON.stringify(spec.initialHash || "#/world")});`, context);
    vm.runInContext(
      `(function(){
         const r = ${JSON.stringify(spec.responses || {})};
         for (const [k, v] of Object.entries(r)) __setResponse(k, v);
       })();`,
      context,
    );
    vm.runInContext(appSrc + EXPOSE, context, { filename: "app.js" });
    const wrapped = `(async () => { ${spec.driver} return globalThis.__result; })();`;
    const promise = vm.runInContext(wrapped, context, { filename: "driver.js" });
    return promise.then(
      (result) => ({
        name, ok: true,
        result: result === undefined ? null : result,
        unhandled: __unhandled.slice(),
      }),
      (e) => ({
        name, ok: false,
        error: String(e && e.stack || e),
        unhandled: __unhandled.slice(),
      }),
    );
  } catch (e) {
    return Promise.resolve({
      name, ok: false, error: String(e && e.stack || e),
      unhandled: __unhandled.slice(),
    });
  }
}

const out = [];
for (const spec of scenarios) {
  const r = await runScenarioAsync(spec.name, spec);
  out.push(r);
}
process.stdout.write(JSON.stringify(out, null, 2));

"""

# ---------- fixtures (matching the app's API shapes) ----------

USER_A = {"id": 1, "username": "u", "email": "e@x", "role": "player",
          "sessionTag": "A"}
CHAR1 = {"id": 1, "name": "P", "location_id": 7, "needs": {},
         "control_mode": "MANUAL", "is_owner": False, "money": 100}
CHAR9 = {**CHAR1, "location_id": 9}
# #312 identity-switch fixtures: a second user + character on the SAME
# location (7) — the scene cache must not survive the identity change.
USER_B = {"id": 2, "username": "u2", "email": "e2@x", "role": "player",
          "sessionTag": "B"}
CHAR2 = {**CHAR1, "id": 2}
WORLD_A = {"game_timestamp": 1500, "day": 2}
WORLD_A_PLUS1 = {"game_timestamp": 1501, "day": 2}  # +1 min, same world hour
WEATHER_OFF = {"enabled": False}
WEATHER_ON = {"enabled": True, "description": "Ясно"}

NPC_IVAN = {"id": "npc_1", "name": "Иван", "kind": "npc"}
NPC_MARIA = {"id": "npc_2", "name": "Мария", "kind": "npc"}
LOC7 = {"id": 7, "name": "Settlement", "type": "settlement",
        "x": 40, "y": 50, "occupants": []}
LOC9 = {"id": 9, "name": "Remote", "type": "shop", "x": 60, "y": 30,
        "occupants": []}
LOC10 = {"id": 10, "name": "Workshop", "type": "workshop",
         "x": 42, "y": 56, "occupants": []}
CATALOG = [LOC7, LOC9, LOC10]
CATALOG_NPC7 = [{**LOC7, "occupants": [NPC_IVAN]}, LOC9, LOC10]
CATALOG_NPC7_MARIA = [{**LOC7, "occupants": [NPC_IVAN, NPC_MARIA]},
                      LOC9, LOC10]

# The committed-state scene descriptor the backend echoes in the 201 payload.
SCENE_DESCRIPTOR = {
    "location": {"id": 7, "name": "Settlement"},
    "time": {"day": 1, "hour": 1},
    "weather": "Ясно",
    "characters": [{"id": "npc_1", "name": "Иван"}],
    "objects": [],
}
SCENE_ASSET = {
    "id": 101, "asset_type": "scene", "character_id": None,
    "location_id": 7, "canonical": False, "prompt": "p", "seed": 1,
    "model": "m", "lora": None, "storage_path": "s",
    "scene_descriptor": SCENE_DESCRIPTOR,
}
SCENE_201 = {"asset": SCENE_ASSET, "reused": False}
FILE_URL = "/visual/assets/101/file"


def _resp(status, body, **extra):
    d = {"status": status, "body": body}
    d.update(extra)
    return d


def _auth(user, char, by_user):
    return {
        "/auth/me": _resp(200, user),
        f"/characters/by-user/{user['id']}": _resp(200, by_user),
        f"/characters/{char['id']}": _resp(200, char),
        f"/characters/{char['id']}/relationships": _resp(200, []),
    }


def _place_base(user=USER_A, char=CHAR1, catalog=None, weather=WEATHER_OFF,
                world=WORLD_A):
    """Authenticated boot + the place loader's GETs (+ the poll feed GET)."""
    d = dict(
        _auth(user, char, [char]),
        **{
            "/world": _resp(200, world),
            "/weather": _resp(200, weather),
            "/locations": _resp(200, catalog if catalog is not None else CATALOG),
            "/world/events?since=0&limit=100": _resp(200, []),
            "/world/events?since=0": _resp(200, []),
        },
    )
    return d


def _scene_ok():
    """The generation success pair: the 201 scene + the file bytes."""
    return {
        "/visual/scenes": _resp(201, SCENE_201),
        FILE_URL: _resp(200, None, __blobBytes="scene-bytes"),
    }


SCENARIOS = [
    # ---------- 1. REGRESSION_COLD_PLACE_RENDERS (control: PASS on base) ----------
    {
        "name": "REGRESSION_COLD_PLACE_RENDERS",
        "initialHash": "#/location/7",
        "responses": _place_base(catalog=CATALOG_NPC7),
        "driver": (
            "await __settle(); const after = __facts(); "
            "globalThis.__result = after;"
        ),
    },
    # ---------- 2. REGRESSION_REMOTE_ENTRY_ZERO_POST (control: PASS on base) ----------
    {
        "name": "REGRESSION_REMOTE_ENTRY_ZERO_POST",
        "initialHash": "#/location/9",
        "responses": _place_base(catalog=CATALOG_NPC7),
        "driver": (
            "await __settle(); const after = __facts(); "
            "globalThis.__result = after;"
        ),
    },
    # ---------- 3. STAGE_COMPOSITION ----------
    {
        "name": "STAGE_COMPOSITION",
        "initialHash": "#/location/7",
        "responses": _place_base(catalog=CATALOG_NPC7, weather=WEATHER_ON),
        "driver": (
            "await __settle(); const after = __facts(); "
            "globalThis.__result = after;"
        ),
    },
    # ---------- 4. STAGE_WEATHER_LINES ----------
    {
        "name": "STAGE_WEATHER_LINES",
        "initialHash": "#/location/7",
        "responses": _place_base(catalog=CATALOG_NPC7, weather=WEATHER_ON),
        "driver": (
            "await __settle(); const on = __facts(); "
            "__setResponse('/weather', " + json.dumps(_resp(200, WEATHER_OFF)) + "); "
            "__setHash('#/location/9'); await __settle(); "
            "__setHash('#/location/7'); await __settle(); const off = __facts(); "
            "globalThis.__result = { on, off };"
        ),
    },
    # ---------- 5. STAGE_TIME_HONEST ----------
    {
        "name": "STAGE_TIME_HONEST",
        "initialHash": "#/location/7",
        "responses": _place_base(catalog=CATALOG_NPC7, weather=WEATHER_ON),
        "driver": (
            "await __settle(); const after = __facts(); "
            "globalThis.__result = after;"
        ),
    },
    # ---------- 6. GENERATION_SUCCESS ----------
    {
        "name": "GENERATION_SUCCESS",
        "initialHash": "#/location/7",
        "responses": dict(_place_base(catalog=CATALOG_NPC7, weather=WEATHER_ON),
                          **_scene_ok()),
        "driver": (
            "await __settle(); "
            "const idleBtn = __stageButton(); "
            "const clicked = __clickButton('Показать сцену'); "
            "await __settle(); "
            "const after = __facts(); "
            "const btnNow = __stageButton(); "
            "const sameNode = !!(idleBtn && btnNow && idleBtn === btnNow); "
            "globalThis.__result = { clicked, after, sameNode };"
        ),
    },
    # ---------- 7. GENERATION_DOUBLE_CLICK ----------
    {
        "name": "GENERATION_DOUBLE_CLICK",
        "initialHash": "#/location/7",
        "responses": dict(_place_base(catalog=CATALOG_NPC7), **_scene_ok()),
        "driver": (
            "await __settle(); "
            "__hold('/visual/scenes'); "
            "const first = __clickButton('Показать сцену'); "
            "await __settle(); "
            "const generating = __facts(); "
            "const second = __clickButton('Создаём сцену…'); "
            "await __settle(); "
            "const released = __release('/visual/scenes', "
            + json.dumps(_resp(201, SCENE_201)) + "); "
            "await __settle(); "
            "const after = __facts(); "
            "globalThis.__result = { first, second, generating, released, after };"
        ),
    },
    # ---------- 8. SCENE_ACT_NO_AUTO_INSPECT ----------
    {
        "name": "SCENE_ACT_NO_AUTO_INSPECT",
        "initialHash": "#/world",
        "responses": dict(
            _place_base(catalog=CATALOG_NPC7),
            **_scene_ok(),
            **{"/scene/act": _resp(
                200, {"ok": True, "task_id": "t1", "task_type": "work",
                      "task_label": "Работа"})},
        ),
        "driver": (
            "await __settle(); "
            # Precondition: a scene was already inspected (fallback is the
            # state where the base auto-inspect fires; it also avoids the
            # ready-state portrait blob fetch).
            "S.scene = { state: 'fallback' }; "
            "const inp = document.getElementById('scene-act-input'); "
            "inp.value = 'Поговорить с Иваном'; "
            "const goClicked = __clickButton('Сделать'); "
            "await __settle(); "
            "const after = __facts(); "
            "globalThis.__result = { goClicked, after };"
        ),
    },
    # ---------- 9. CACHE_RETURN_ZERO_REQUESTS ----------
    {
        "name": "CACHE_RETURN_ZERO_REQUESTS",
        "initialHash": "#/location/7",
        "responses": dict(_place_base(catalog=CATALOG_NPC7, weather=WEATHER_ON),
                          **_scene_ok()),
        "driver": (
            "await __settle(); "
            "__clickButton('Показать сцену'); await __settle(); "
            "const ready = __facts(); "
            "__setHash('#/location/9'); await __settle(); "
            "const away = __facts(); "
            "__setHash('#/location/7'); await __settle(); "
            "const back = __facts(); "
            "globalThis.__result = { ready, away, back };"
        ),
    },
    # ---------- 10. MOVEMENT_DISCARD (route change / movement / ready discard) ----------
    {
        "name": "MOVEMENT_DISCARD",
        "initialHash": "#/location/7",
        "responses": dict(_place_base(catalog=CATALOG_NPC7, weather=WEATHER_ON),
                          **_scene_ok()),
        "driver": (
            "await __settle(); "
            # Leg A: a stale generation finishing after a ROUTE change.
            "__hold('/visual/scenes'); "
            "__clickButton('Показать сцену'); await __settle(); "
            "__setHash('#/location/9'); await __settle(); "
            "const aReleased = __release('/visual/scenes', "
            + json.dumps(_resp(201, SCENE_201)) + "); "
            "await __settle(); "
            "const legA = __facts(); "
            # Leg B: a stale generation finishing after MOVEMENT (world poll).
            "__setResponse('/characters/1', "
            + json.dumps(_resp(200, CHAR9)) + "); "
            "__setHash('#/location/7'); await __settle(); "
            "__setHash('#/location/9'); await __settle(); "
            "__hold('/visual/scenes'); "
            "__clickButton('Показать сцену'); await __settle(); "
            "__setHash('#/world'); await __settle(); "
            "__setResponse('/characters/1', "
            + json.dumps(_resp(200, CHAR1)) + "); "
            "__advance(5000); await __settle(); "
            "const bReleased = __release('/visual/scenes', "
            + json.dumps(_resp(201, SCENE_201)) + "); "
            "await __settle(); "
            "const legBWorld = __facts(); "
            "__setHash('#/location/7'); await __settle(); "
            "const legB = __facts(); "
            # Leg C: a READY entry is discarded on movement (URL revoked once).
            "__clickButton('Показать сцену'); await __settle(); "
            "const readySeven = __facts(); "
            "__setHash('#/world'); await __settle(); "
            "__setResponse('/characters/1', "
            + json.dumps(_resp(200, CHAR9)) + "); "
            "__advance(5000); await __settle(); "
            "const legCWorld = __facts(); "
            "__setHash('#/location/9'); await __settle(); "
            "const legC = __facts(); "
            "globalThis.__result = { aReleased, legA, bReleased, legBWorld, "
            "legB, readySeven, legCWorld, legC };"
        ),
    },
    # ---------- 11. ERROR_MODES (503/404/502/network/deadline/decode) ----------
    {
        "name": "ERROR_MODES",
        "initialHash": "#/location/7",
        "responses": dict(
            _place_base(catalog=CATALOG_NPC7, weather=WEATHER_ON),
            **{"/visual/scenes": _resp(
                503, {"detail": "visual generation disabled"})},
        ),
        "driver": (
            "await __settle(); "
            # Leg 1: 503 (visuals disabled).
            "__clickButton('Показать сцену'); await __settle(); "
            "const e503 = __facts(); "
            # Leg 2: 404 (file missing).
            "__setResponse('/visual/scenes', "
            + json.dumps(_resp(201, SCENE_201)) + "); "
            "__setResponse('" + FILE_URL + "', "
            + json.dumps(_resp(404, {"detail": "asset file missing"})) + "); "
            "__clickButton('Повторить визуализацию'); await __settle(); "
            "const e404 = __facts(); "
            # Leg 3: 502 (transport).
            "__setResponse('/visual/scenes', "
            + json.dumps(_resp(502, {"detail": "flux exploded"})) + "); "
            "__clickButton('Повторить визуализацию'); await __settle(); "
            "const e502 = __facts(); "
            # Leg 4: network failure.
            "__setResponse('/visual/scenes', "
            + json.dumps(_resp(200, None, __networkFail=True)) + "); "
            "__clickButton('Повторить визуализацию'); await __settle(); "
            "const eNet = __facts(); "
            # Leg 5: the 300s deadline.
            "__setResponse('/visual/scenes', "
            + json.dumps(_resp(201, SCENE_201)) + "); "
            "__hold('/visual/scenes'); "
            "__clickButton('Повторить визуализацию'); await __settle(); "
            "const deadline = __fireDeadline(); "
            "await __settle(); "
            "const eDeadline = __facts(); "
            "const dReleased = __release('/visual/scenes', "
            + json.dumps(_resp(201, SCENE_201)) + "); "
            "await __settle(); "
            "const afterDeadline = __facts(); "
            # Leg 6: img decode failure (bytes OK, decode fails).
            "__setResponse('" + FILE_URL + "', "
            + json.dumps(_resp(200, None, __blobBytes="scene-bytes")) + "); "
            "__clickButton('Повторить визуализацию'); await __settle(); "
            "const readyBeforeDecode = __facts(); "
            "const img = __stageImg(); "
            "const fired = img ? __fireImgError(img) : false; "
            "await __settle(); "
            "const eDecode = __facts(); "
            # Leg 7: blob() rejects → unavailable + no URL created.
            # Reset the cumulative URL log so the urlCreated assertion below
            # is scoped to leg 7 only (leg 6's ready image create is dropped).
            "__resetUrlLog(); "
            "__setResponse('" + FILE_URL + "', "
            + json.dumps(_resp(200, None, __blobHold=True)) + "); "
            "__clickButton('Повторить визуализацию'); await __settle(); "
            "const blobRejected = __rejectBlob('" + FILE_URL + "', "
            "'blob failure'); "
            "await __settle(); "
            "const eBlobReject = __facts(); "
            "globalThis.__result = { e503, e404, e502, eNet, deadline, "
            "eDeadline, dReleased, afterDeadline, readyBeforeDecode, fired, "
            "eDecode, blobRejected, eBlobReject };"
        ),
    },
    # ---------- 12. REMOTE_PREVIEW_NO_GENERATION ----------
    {
        "name": "REMOTE_PREVIEW_NO_GENERATION",
        "initialHash": "#/location/9",
        "responses": _place_base(catalog=CATALOG_NPC7, weather=WEATHER_ON),
        "driver": (
            "await __settle(); const after = __facts(); "
            "globalThis.__result = after;"
        ),
    },
    # ---------- 13. NO_FABRICATED_CLAIMS ----------
    {
        "name": "NO_FABRICATED_CLAIMS",
        "initialHash": "#/location/7",
        "responses": dict(_place_base(catalog=CATALOG_NPC7, weather=WEATHER_ON),
                          **_scene_ok()),
        "driver": (
            "await __settle(); "
            "const before = __facts(); "
            "__clickButton('Показать сцену'); await __settle(); "
            "const after = __facts(); "
            "globalThis.__result = { before, after };"
        ),
    },
    # ---------- 14. NO_LEAKED_URL ----------
    {
        "name": "NO_LEAKED_URL",
        "initialHash": "#/location/7",
        "responses": dict(_place_base(catalog=CATALOG_NPC7, weather=WEATHER_ON),
                          **_scene_ok()),
        "driver": (
            "await __settle(); "
            "__clickButton('Показать сцену'); await __settle(); "
            "const ready = __facts(); "
            "__setHash('#/world'); await __settle(); "
            "const atWorld = __facts(); "
            "__setHash('#/location/9'); await __settle(); "
            "const atNine = __facts(); "
            "globalThis.__result = { ready, atWorld, atNine };"
        ),
    },
    # ---------- 15. NO_POST_ON_TICK_REPAINT ----------
    {
        "name": "NO_POST_ON_TICK_REPAINT",
        "initialHash": "#/location/7",
        "responses": dict(_place_base(catalog=CATALOG_NPC7, weather=WEATHER_ON),
                          **_scene_ok()),
        "driver": (
            "await __settle(); "
            "__clickButton('Показать сцену'); await __settle(); "
            "const ready = __facts(); "
            "__setHash('#/world'); await __settle(); "
            "__advance(5000); await __settle(); "
            "__advance(5000); await __settle(); "
            "const afterTicks = __facts(); "
            "__setHash('#/location/7'); await __settle(); "
            "const back = __facts(); "
            "globalThis.__result = { ready, afterTicks, back };"
        ),
    },
    # ---------- 16. CONTEXT_INVALIDATION ----------
    {
        "name": "CONTEXT_INVALIDATION",
        "initialHash": "#/location/7",
        "responses": dict(_place_base(catalog=CATALOG_NPC7, weather=WEATHER_ON),
                          **_scene_ok()),
        "driver": (
            "await __settle(); "
            "__clickButton('Показать сцену'); await __settle(); "
            "const ready = __facts(); "
            "__setHash('#/world'); await __settle(); "
            "__setResponse('/locations', "
            + json.dumps(_resp(200, CATALOG_NPC7_MARIA)) + "); "
            "__advance(5000); await __settle(); "
            "const afterPoll = __facts(); "
            "__setHash('#/location/7'); await __settle(); "
            "const atSeven = __facts(); "
            "globalThis.__result = { ready, afterPoll, atSeven };"
        ),
    },
    # ---------- 17. BUTTON_NODE_STABLE ----------
    {
        "name": "BUTTON_NODE_STABLE",
        "initialHash": "#/location/7",
        "responses": dict(_place_base(catalog=CATALOG_NPC7, weather=WEATHER_ON),
                          **_scene_ok()),
        "driver": (
            "await __settle(); "
            "const idleBtn = __stageButton(); "
            "if (idleBtn) idleBtn.focus(); "
            "const focusedIdle = __facts(); "
            "__clickButton('Показать сцену'); await __settle(); "
            "const readyFacts = __facts(); "
            "const readyBtn = __stageButton(); "
            "const sameNode = !!(idleBtn && readyBtn && idleBtn === readyBtn); "
            "const stillFocused = !!(idleBtn && idleBtn._focused); "
            "globalThis.__result = { focusedIdle, readyFacts, sameNode, "
            "stillFocused };"
        ),
    },
    # ---------- 18. NAV_AWAY_ABORTS_FLIGHT ----------
    {
        "name": "NAV_AWAY_ABORTS_FLIGHT",
        "initialHash": "#/location/7",
        "responses": dict(_place_base(catalog=CATALOG_NPC7, weather=WEATHER_ON),
                          **_scene_ok()),
        "driver": (
            "await __settle(); "
            "__hold('" + FILE_URL + "'); "
            "__clickButton('Показать сцену'); await __settle(); "
            "__setHash('#/location/9'); await __settle(); "
            "const afterNav = __facts(); "
            "const released = __release('" + FILE_URL + "', "
            + json.dumps(_resp(200, None, __blobBytes="late-bytes")) + "); "
            "await __settle(); "
            "const afterRelease = __facts(); "
            "globalThis.__result = { afterNav, released, afterRelease };"
        ),
    },
    # ---------- 19. MINUTE_TICK_NO_INVALIDATE ----------
    {
        "name": "MINUTE_TICK_NO_INVALIDATE",
        "initialHash": "#/location/7",
        "responses": dict(_place_base(catalog=CATALOG_NPC7, weather=WEATHER_ON),
                          **_scene_ok()),
        "driver": (
            "await __settle(); "
            "__clickButton('Показать сцену'); await __settle(); "
            "const ready = __facts(); "
            "__setHash('#/world'); await __settle(); "
            "__setResponse('/world', "
            + json.dumps(_resp(200, WORLD_A_PLUS1)) + "); "
            "__advance(5000); await __settle(); "
            "const afterTick = __facts(); "
            "__setHash('#/location/7'); await __settle(); "
            "const back = __facts(); "
            "globalThis.__result = { ready, afterTick, back };"
        ),
    },
    # ---------- 20. STALE_BLOB_AND_IDENTITY_SWITCH ----------
    {
        "name": "STALE_BLOB_AND_IDENTITY_SWITCH",
        "initialHash": "#/location/7",
        "responses": dict(_place_base(catalog=CATALOG_NPC7, weather=WEATHER_ON),
                          **_scene_ok()),
        "driver": (
            "await __settle(); "
            # Part (a): stale blob completion — hold the file blob, navigate
            # away, then release it late.
            "__setResponse('" + FILE_URL + "', "
            + json.dumps(_resp(200, None, __hold=True)) + "); "
            "__clickButton('Показать сцену'); await __settle(); "
            "__release('" + FILE_URL + "', "
            + json.dumps(_resp(200, None, __blobHold=True)) + "); "
            "await __settle(); "
            "const blobHeld = __blobHeldCount('" + FILE_URL + "'); "
            "__setHash('#/location/9'); await __settle(); "
            "__releaseBlob('" + FILE_URL + "', 'late-bytes'); "
            "await __settle(); "
            "const afterStale = __facts(); "
            # Part (b): identity switch — establish a ready cache entry under
            # identity A, switch to identity B, return to location 7.
            "__setResponse('" + FILE_URL + "', "
            + json.dumps(_resp(200, None, __blobBytes="scene-bytes")) + "); "
            "__setHash('#/location/7'); await __settle(); "
            "__clickButton('Показать сцену'); await __settle(); "
            "const readyA = __facts(); "
            "__setResponse('/auth/me', "
            + json.dumps(_resp(200, USER_B)) + "); "
            "__setResponse('/characters/by-user/2', "
            + json.dumps(_resp(200, [CHAR2])) + "); "
            "__setResponse('/characters/2', "
            + json.dumps(_resp(200, CHAR2)) + "); "
            "__setResponse('/characters/2/relationships', "
            + json.dumps(_resp(200, [])) + "); "
            "S.user = null; S.authProbed = false; S.character = null; "
            "__setHash('#/world'); await __settle(); "
            "__setHash('#/location/7'); await __settle(); "
            "__clickButton('Показать сцену'); await __settle(); "
            "const afterGen = __facts(); "
            "globalThis.__result = { blobHeld, afterStale, readyA, afterGen };"
        ),
    },
]


def _run_harness():
    os.makedirs(SCRATCH, exist_ok=True)
    harness_path = os.path.join(SCRATCH, "m312_harness.mjs")
    scen_path = os.path.join(SCRATCH, "m312_scenarios.json")
    with open(harness_path, "w", encoding="utf-8") as f:
        f.write(HARNESS_JS)
    with open(scen_path, "w", encoding="utf-8") as f:
        json.dump(SCENARIOS, f)
    proc = subprocess.run(
        ["node", harness_path, APP_JS, scen_path],
        capture_output=True, text=True, timeout=180,
    )
    assert proc.returncode == 0, (
        f"node harness failed (rc={proc.returncode}):\n{proc.stderr}"
    )
    return {r["name"]: r for r in json.loads(proc.stdout)}


@pytest.fixture(scope="module")
def results():
    return _run_harness()


def _facts(results, name):
    r = results[name]
    assert r.get("ok"), f"{name}: harness error: {r.get('error')}"
    return r["result"]


def _unhandled(results, name):
    r = results[name]
    assert r.get("ok"), f"{name}: harness error: {r.get('error')}"
    return r.get("unhandled", [])


# ---------- 1. REGRESSION_COLD_PLACE_RENDERS (control) ----------

def test_regression_cold_place_renders(results):
    r = _facts(results, "REGRESSION_COLD_PLACE_RENDERS")
    # The existing #303 place shell renders the current place…
    assert r["hasPlaceShell"] is True, "the current place renders"
    assert r["badgeText"] == "Вы здесь", (
        f"the current place shows the «Вы здесь» badge, got {r['badgeText']!r}"
    )
    # …and entering it issues ZERO POSTs (no scene generation on entry).
    assert r["postCount"] == 0, (
        f"entering the current place issues ZERO POSTs, got {r['postCount']}"
    )
    assert r["postVisualScenes"] == 0, (
        f"no /visual/scenes POST on entry, got {r['postVisualScenes']}"
    )
    assert r["fileGets"] == [], (
        f"no /visual/assets file GET on entry, got {r['fileGets']!r}"
    )
    unhandled = _unhandled(results, "REGRESSION_COLD_PLACE_RENDERS")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 2. REGRESSION_REMOTE_ENTRY_ZERO_POST (control) ----------

def test_regression_remote_entry_zero_post(results):
    r = _facts(results, "REGRESSION_REMOTE_ENTRY_ZERO_POST")
    assert r["hasPlaceShell"] is True, "the remote place renders"
    assert r["badgeText"] == "Другое место", (
        f"the remote place shows the «Другое место» badge, got {r['badgeText']!r}"
    )
    assert r["postCount"] == 0, (
        f"entering the remote place issues ZERO POSTs, got {r['postCount']}"
    )
    assert r["postVisualScenes"] == 0, (
        f"no /visual/scenes POST on a remote entry, got {r['postVisualScenes']}"
    )
    assert r["fileGets"] == [], (
        f"no /visual/assets file GET on a remote entry, got {r['fileGets']!r}"
    )
    unhandled = _unhandled(results, "REGRESSION_REMOTE_ENTRY_ZERO_POST")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 3. STAGE_COMPOSITION ----------

def test_stage_composition(results):
    r = _facts(results, "STAGE_COMPOSITION")
    # Hero heading: «{location name} · {dayTime}» (1500 → День 2, 01:00).
    assert r["stageHeadingText"] == "Settlement · День 2, 01:00", (
        f"the stage hero heading is «name · dayTime», "
        f"got {r['stageHeadingText']!r}"
    )
    # Weather line from S.weather (enabled → description).
    assert r["stageText"] is not None and "Ясно" in r["stageText"], (
        f"the stage shows the readable weather, got {r['stageText']!r}"
    )
    # Confirmed-data summary: the nearby NPC from the loaded roster.
    assert "Иван" in r["stageText"], (
        f"the stage shows the confirmed nearby NPC, got {r['stageText']!r}"
    )
    # The status line is a live region.
    assert r["stageStatusAriaLive"] == "polite", (
        f"the stage status line is aria-live=polite, "
        f"got {r['stageStatusAriaLive']!r}"
    )
    # ONE stable button in the idle state.
    assert r["stageButtonText"] == "Показать сцену", (
        f"the idle stage button is «Показать сцену», "
        f"got {r['stageButtonText']!r}"
    )
    # The old static photo placeholder is superseded.
    assert "Фото места недоступно" not in (r["stageText"] or ""), (
        f"the old photo placeholder is gone, got {r['stageText']!r}"
    )
    # No generation on entry.
    assert r["postVisualScenes"] == 0, (
        f"no /visual/scenes POST on entry, got {r['postVisualScenes']}"
    )
    unhandled = _unhandled(results, "STAGE_COMPOSITION")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 4. STAGE_WEATHER_LINES ----------

def test_stage_weather_lines(results):
    r = _facts(results, "STAGE_WEATHER_LINES")
    on, off = r["on"], r["off"]
    assert on["stageText"] is not None and "Ясно" in on["stageText"], (
        f"readable weather renders its description, got {on['stageText']!r}"
    )
    assert off["stageText"] is not None and "недоступна" in off["stageText"], (
        f"disabled weather stays honestly unavailable, got {off['stageText']!r}"
    )
    assert "Ясно" not in off["stageText"], (
        f"the disabled weather description must not leak, got {off['stageText']!r}"
    )
    unhandled = _unhandled(results, "STAGE_WEATHER_LINES")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 5. STAGE_TIME_HONEST ----------

def test_stage_time_honest(results):
    r = _facts(results, "STAGE_TIME_HONEST")
    # The original malformed-world premise (WORLD_NO_TS) was unreachable:
    # #269 rejects a timestamp-less world before the place renders, so a
    # rendered place always has a valid world. The no-synthesis teeth now
    # bind to the real-data path (game_timestamp 1500 → «День 2, 01:00»).
    # (1) the heading names the place AND carries the real day.
    assert r["stageHeadingText"] is not None \
        and "Settlement" in r["stageHeadingText"] \
        and "День 2" in r["stageHeadingText"], (
        f"the hero heading names the place and the real day, "
        f"got {r['stageHeadingText']!r}"
    )
    # (2) no synthesized default day.
    assert "День 1" not in (r["stageText"] or ""), (
        f"no synthesized «День 1», got {r['stageText']!r}"
    )
    # (3) no zero-time fallback.
    assert "00:00" not in (r["stageText"] or ""), (
        f"no synthesized «00:00», got {r['stageText']!r}"
    )
    unhandled = _unhandled(results, "STAGE_TIME_HONEST")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 6. GENERATION_SUCCESS ----------

def test_generation_success(results):
    r = _facts(results, "GENERATION_SUCCESS")
    clicked, after, same_node = r["clicked"], r["after"], r["sameNode"]
    assert clicked is True, "the idle «Показать сцену» button is clickable"
    # Exactly ONE /visual/scenes POST with the captured location…
    assert after["postVisualScenes"] == 1, (
        f"the click issues exactly ONE /visual/scenes POST, "
        f"got {after['postVisualScenes']}"
    )
    bodies = after["postVisualScenesBodies"]
    assert len(bodies) == 1, (
        f"the POST body is a single JSON object, got {bodies!r}"
    )
    assert bodies[0].get("location_id") == 7, (
        f"the POST body carries the captured location, got {bodies!r}"
    )
    # …then exactly ONE file GET…
    assert after["fileGets"] == [FILE_URL], (
        f"the ready state fetches the file once, got {after['fileGets']!r}"
    )
    # …and mounts the image from a fresh objectURL with the validated alt.
    assert after["stageImgSrc"] is not None \
        and after["stageImgSrc"].startswith("blob:mock-"), (
        f"the stage image uses a fresh objectURL, got {after['stageImgSrc']!r}"
    )
    assert after["stageImgAlt"] is not None \
        and after["stageImgAlt"].startswith("Визуализация места"), (
        f"the alt is prefixed «Визуализация места …», "
        f"got {after['stageImgAlt']!r}"
    )
    # The ready button is non-operable but the SAME node (focus retention).
    assert after["stageButtonText"] == "Сцена показана", (
        f"the ready button is «Сцена показана», got {after['stageButtonText']!r}"
    )
    assert after["stageButtonAriaDisabled"] == "true", (
        f"the ready button is aria-disabled, got {after['stageButtonAriaDisabled']!r}"
    )
    assert same_node is True, "the button node is retained across the swap"
    # The scene entry is ready and cached.
    assert after["scene"] is not None and after["scene"]["status"] == "ready", (
        f"the scene entry is ready, got {after['scene']!r}"
    )
    assert after["scene"]["assetId"] == 101, (
        f"the scene entry carries the asset id, got {after['scene']!r}"
    )
    assert after["sceneCacheSize"] == 1, (
        f"the scene cache holds the entry, got {after['sceneCacheSize']!r}"
    )
    unhandled = _unhandled(results, "GENERATION_SUCCESS")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 7. GENERATION_DOUBLE_CLICK ----------

def test_generation_double_click(results):
    r = _facts(results, "GENERATION_DOUBLE_CLICK")
    first, second, generating, released, after = (
        r["first"], r["second"], r["generating"], r["released"], r["after"])
    assert first is True, "the idle button is clickable"
    # While generating: the button is non-operable…
    assert generating["stageButtonText"] == "Создаём сцену…", (
        f"the generating button is «Создаём сцену…», "
        f"got {generating['stageButtonText']!r}"
    )
    assert generating["stageButtonAriaDisabled"] == "true", (
        f"the generating button is aria-disabled, "
        f"got {generating['stageButtonAriaDisabled']!r}"
    )
    assert generating["postVisualScenes"] == 1, (
        f"one POST is in flight, got {generating['postVisualScenes']}"
    )
    # …and a second click issues NO second POST.
    assert second is True, "the generating button is still found"
    assert released is True, "the held POST is released"
    assert after["postVisualScenes"] == 1, (
        f"the second click issues no second POST, got {after['postVisualScenes']}"
    )
    assert after["scene"] is not None and after["scene"]["status"] == "ready", (
        f"the released POST completes to ready, got {after['scene']!r}"
    )
    unhandled = _unhandled(results, "GENERATION_DOUBLE_CLICK")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 8. SCENE_ACT_NO_AUTO_INSPECT ----------

def test_scene_act_no_auto_inspect(results):
    r = _facts(results, "SCENE_ACT_NO_AUTO_INSPECT")
    go_clicked, after = r["goClicked"], r["after"]
    assert go_clicked is True, "the scene-act «Сделать» button is clickable"
    # A scene-action success NO LONGER auto-triggers inspectSurroundings.
    # The driver pre-sets S.scene to fallback — the exact state where the
    # base auto-inspect fires (submitSceneAct line: st === "ready" ||
    # "fallback"). So on base this issues one /visual/scenes POST; on #312
    # the success is a presentation refresh only → ZERO POSTs.
    assert after["postVisualScenes"] == 0, (
        f"a scene-act success must NOT re-inspect (no /visual/scenes POST), "
        f"got {after['postVisualScenes']}"
    )
    assert after["sceneViewText"] is not None, (
        "the scene panel is still rendered (presentation refresh only)"
    )
    unhandled = _unhandled(results, "SCENE_ACT_NO_AUTO_INSPECT")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 9. CACHE_RETURN_ZERO_REQUESTS ----------

def test_cache_return_zero_requests(results):
    r = _facts(results, "CACHE_RETURN_ZERO_REQUESTS")
    ready, away, back = r["ready"], r["away"], r["back"]
    assert ready["scene"] is not None and ready["scene"]["status"] == "ready", (
        f"the first generation is ready, got {ready['scene']!r}"
    )
    assert ready["postVisualScenes"] == 1 and ready["fileGets"] == [FILE_URL], (
        "the first generation issues one POST + one file GET"
    )
    # Navigation away: the mounted URL is revoked, the blob is retained.
    assert ready["stageImgSrc"] in away["urlRevokedIds"], (
        f"the mounted URL is revoked on navigation away, "
        f"revoked={away['urlRevokedIds']!r}"
    )
    # Return: ZERO new scene POST and ZERO new file GET (cache hit).
    assert back["postVisualScenes"] == 1, (
        f"the return issues no new /visual/scenes POST, "
        f"got {back['postVisualScenes']}"
    )
    assert back["fileGets"] == [FILE_URL], (
        f"the return issues no new file GET, got {back['fileGets']!r}"
    )
    # The image is recreated from the cached blob (a fresh objectURL).
    assert back["stageImgSrc"] is not None, (
        f"the return restores the image from the cache, got {back['stageImgSrc']!r}"
    )
    assert back["stageImgSrc"] != ready["stageImgSrc"], (
        "the return recreates a fresh objectURL from the cached blob"
    )
    assert back["scene"] is not None and back["scene"]["status"] == "ready", (
        f"the return is ready from the cache, got {back['scene']!r}"
    )
    unhandled = _unhandled(results, "CACHE_RETURN_ZERO_REQUESTS")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 10. MOVEMENT_DISCARD ----------

def test_movement_discard(results):
    r = _facts(results, "MOVEMENT_DISCARD")
    (a_released, leg_a, b_released, leg_b_world, leg_b,
     ready_seven, leg_c_world, leg_c) = (
        r["aReleased"], r["legA"], r["bReleased"], r["legBWorld"], r["legB"],
        r["readySeven"], r["legCWorld"], r["legC"])
    # Leg A: a stale generation finishing after a ROUTE change is discarded.
    assert a_released is True, "the stale POST is released"
    assert leg_a["stageImgSrc"] is None, (
        f"the stale generation shows no image at the new place, "
        f"got {leg_a['stageImgSrc']!r}"
    )
    assert leg_a["scene"] is None or leg_a["scene"]["status"] != "ready", (
        f"the stale generation sets no ready state, got {leg_a['scene']!r}"
    )
    assert leg_a["toastVisible"] is False, (
        f"the stale generation fires no toast, got {leg_a['toastText']!r}"
    )
    # Leg B: a stale generation finishing after MOVEMENT is discarded.
    assert b_released is True, "the movement-stale POST is released"
    assert leg_b_world["stageImgSrc"] is None, (
        "no image after the movement-stale release"
    )
    assert leg_b["stageImgSrc"] is None, (
        f"the movement-stale generation shows no image at the new place, "
        f"got {leg_b['stageImgSrc']!r}"
    )
    assert leg_b["scene"] is None or leg_b["scene"]["status"] != "ready", (
        f"the movement-stale generation sets no ready state, got {leg_b['scene']!r}"
    )
    assert leg_b["toastVisible"] is False, (
        f"the movement-stale generation fires no toast, got {leg_b['toastText']!r}"
    )
    # Leg C: a READY entry is discarded on movement — the URL is revoked
    # exactly once and the stage resets to idle.
    assert ready_seven["scene"] is not None \
        and ready_seven["scene"]["status"] == "ready", (
        f"the leg-C generation is ready, got {ready_seven['scene']!r}"
    )
    assert ready_seven["stageImgSrc"] in leg_c_world["urlRevokedIds"], (
        f"the ready URL is revoked on movement, "
        f"revoked={leg_c_world['urlRevokedIds']!r}"
    )
    assert leg_c_world["doubleRevoked"] == [], (
        f"the URL is revoked exactly once, double={leg_c_world['doubleRevoked']!r}"
    )
    assert leg_c["stageImgSrc"] is None, (
        f"the moved-to place shows no image, got {leg_c['stageImgSrc']!r}"
    )
    assert leg_c["stageButtonText"] == "Показать сцену", (
        f"the moved-to place resets to the idle button, "
        f"got {leg_c['stageButtonText']!r}"
    )
    unhandled = _unhandled(results, "MOVEMENT_DISCARD")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 11. ERROR_MODES ----------

def test_error_modes(results):
    r = _facts(results, "ERROR_MODES")
    (e503, e404, e502, e_net, deadline, e_deadline, d_released,
     after_deadline, ready_before_decode, fired, e_decode,
     blob_rejected, e_blob_reject) = (
        r["e503"], r["e404"], r["e502"], r["eNet"], r["deadline"],
        r["eDeadline"], r["dReleased"], r["afterDeadline"],
        r["readyBeforeDecode"], r["fired"], r["eDecode"],
        r["blobRejected"], r["eBlobReject"])

    def _unavailable(f, label):
        assert f["scene"] is not None and f["scene"]["status"] == "unavailable", (
            f"{label}: the scene is unavailable, got {f['scene']!r}"
        )
        assert f["stageButtonText"] == "Повторить визуализацию", (
            f"{label}: the retry button is «Повторить визуализацию», "
            f"got {f['stageButtonText']!r}"
        )
        # The confirmed text stays usable (location + nearby NPC).
        assert "Settlement" in (f["stageText"] or ""), (
            f"{label}: the confirmed location name stays visible, "
            f"got {f['stageText']!r}"
        )
        assert "Иван" in (f["stageText"] or ""), (
            f"{label}: the confirmed nearby NPC stays visible, "
            f"got {f['stageText']!r}"
        )

    # Leg 1: 503 → unavailable, no automatic retry.
    _unavailable(e503, "503")
    # #312 regression control: the guarded catch still toasts (base toasts
    # unguarded; #312 keeps the exact toast copy in the guarded path).
    assert e503["toastText"] == "Визуал недоступен, показываю данные", (
        f"503: the guarded catch toasts, got {e503['toastText']!r}"
    )
    assert e503["toastVisible"] is True, (
        f"503: the toast is visible, got {e503['toastVisible']!r}"
    )
    assert e503["postVisualScenes"] == 1, (
        f"503: no automatic retry POST, got {e503['postVisualScenes']}"
    )
    # Leg 2: 404 file → unavailable (the retry POST + the file GET happened).
    _unavailable(e404, "404")
    assert e404["postVisualScenes"] == 2, (
        f"404: the retry issued a 2nd POST, got {e404['postVisualScenes']}"
    )
    assert e404["fileGets"] == [FILE_URL], (
        f"404: the file GET was attempted, got {e404['fileGets']!r}"
    )
    # Leg 3: 502 → unavailable.
    _unavailable(e502, "502")
    assert e502["postVisualScenes"] == 3, (
        f"502: the retry issued a 3rd POST, got {e502['postVisualScenes']}"
    )
    # Leg 4: network failure → unavailable.
    _unavailable(e_net, "network")
    assert e_net["postVisualScenes"] == 4, (
        f"network: the retry issued a 4th POST, got {e_net['postVisualScenes']}"
    )
    # Leg 5: the 300s deadline → unavailable; the late release is discarded.
    assert deadline is True, "the 300s deadline timeout was found and fired"
    _unavailable(e_deadline, "deadline")
    assert d_released is True, "the held deadline POST is released"
    assert after_deadline["scene"] is not None \
        and after_deadline["scene"]["status"] == "unavailable", (
        f"deadline: the late release is discarded (still unavailable), "
        f"got {after_deadline['scene']!r}"
    )
    # Leg 6: img decode failure → unavailable + the URL is released.
    assert ready_before_decode["scene"] is not None \
        and ready_before_decode["scene"]["status"] == "ready", (
        f"decode: the image loaded ready first, "
        f"got {ready_before_decode['scene']!r}"
    )
    assert fired is True, "decode: the img error listener fired"
    _unavailable(e_decode, "decode")
    assert ready_before_decode["stageImgSrc"] in e_decode["urlRevokedIds"], (
        f"decode: the failed image URL is released, "
        f"revoked={e_decode['urlRevokedIds']!r}"
    )
    # Leg 7: blob() rejects → unavailable + no URL created.
    assert blob_rejected is True, "blob-reject: the held blob is rejected"
    _unavailable(e_blob_reject, "blob-reject")
    assert e_blob_reject["urlCreated"] == 0, (
        f"blob-reject: no objectURL is created within the blob-reject leg, "
        f"got {e_blob_reject['urlCreated']}"
    )
    unhandled = _unhandled(results, "ERROR_MODES")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 12. REMOTE_PREVIEW_NO_GENERATION ----------

def test_remote_preview_no_generation(results):
    r = _facts(results, "REMOTE_PREVIEW_NO_GENERATION")
    assert r["badgeText"] == "Другое место", (
        f"the remote place badge, got {r['badgeText']!r}"
    )
    # The remote preview shows the text-first composition (name + weather)…
    assert r["stageText"] is not None and "Remote" in r["stageText"], (
        f"the remote preview names the place, got {r['stageText']!r}"
    )
    assert "Ясно" in r["stageText"], (
        f"the remote preview shows the weather, got {r['stageText']!r}"
    )
    # …but NO generation button and NO scene POST.
    assert r["stageButtonText"] is None, (
        f"the remote preview has no generation button, "
        f"got {r['stageButtonText']!r}"
    )
    assert r["postVisualScenes"] == 0, (
        f"the remote preview issues no /visual/scenes POST, "
        f"got {r['postVisualScenes']}"
    )
    assert r["fileGets"] == [], (
        f"the remote preview issues no file GET, got {r['fileGets']!r}"
    )
    unhandled = _unhandled(results, "REMOTE_PREVIEW_NO_GENERATION")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 13. NO_FABRICATED_CLAIMS ----------

def test_no_fabricated_claims(results):
    r = _facts(results, "NO_FABRICATED_CLAIMS")
    before, after = r["before"], r["after"]
    # Before generation: only confirmed data (location, time, weather, NPC).
    assert "Settlement" in before["stageText"], (
        f"the confirmed location name, got {before['stageText']!r}"
    )
    assert "Иван" in before["stageText"], (
        f"the confirmed nearby NPC, got {before['stageText']!r}"
    )
    assert "Ясно" in before["stageText"], (
        f"the confirmed weather, got {before['stageText']!r}"
    )
    # No fabricated object claims (the confirmed data has no objects).
    assert "Объекты" not in before["stageText"], (
        f"no fabricated object claims, got {before['stageText']!r}"
    )
    # After generation: the alt is from the validated descriptor…
    assert after["stageImgAlt"] is not None \
        and after["stageImgAlt"].startswith("Визуализация места"), (
        f"the alt is from the validated descriptor, got {after['stageImgAlt']!r}"
    )
    # …and the live nearby/weather still come from the loaded data.
    assert "Иван" in after["stageText"], (
        f"the live nearby NPC stays from loaded data, got {after['stageText']!r}"
    )
    assert "Ясно" in after["stageText"], (
        f"the live weather stays from loaded data, got {after['stageText']!r}"
    )
    unhandled = _unhandled(results, "NO_FABRICATED_CLAIMS")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 14. NO_LEAKED_URL ----------

def test_no_leaked_url(results):
    r = _facts(results, "NO_LEAKED_URL")
    ready, at_world, at_nine = r["ready"], r["atWorld"], r["atNine"]
    assert ready["stageImgSrc"] is not None, "a URL was created for the image"
    # After navigating away (world, then a remote place): no leaked URLs.
    assert at_world["urlLeaked"] == [], (
        f"no URL object is leaked at the world step, got {at_world['urlLeaked']!r}"
    )
    assert at_nine["urlLeaked"] == [], (
        f"no URL object is leaked after navigation, got {at_nine['urlLeaked']!r}"
    )
    assert at_nine["doubleRevoked"] == [], (
        f"no URL is revoked twice, got {at_nine['doubleRevoked']!r}"
    )
    # The remote place shows no image.
    assert at_nine["stageImgSrc"] is None, (
        f"the remote place shows no image, got {at_nine['stageImgSrc']!r}"
    )
    unhandled = _unhandled(results, "NO_LEAKED_URL")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 15. NO_POST_ON_TICK_REPAINT ----------

def test_no_post_on_tick_repaint(results):
    r = _facts(results, "NO_POST_ON_TICK_REPAINT")
    ready, after_ticks, back = r["ready"], r["afterTicks"], r["back"]
    assert ready["postVisualScenes"] == 1, "the first generation issues one POST"
    # The 5s poll tick issues no image generation request.
    assert after_ticks["postVisualScenes"] == 1, (
        f"the 5s tick issues no /visual/scenes POST, "
        f"got {after_ticks['postVisualScenes']}"
    )
    # The route repaint / return to the place issues no request (cache hit).
    assert back["postVisualScenes"] == 1, (
        f"the return issues no /visual/scenes POST, got {back['postVisualScenes']}"
    )
    assert back["fileGets"] == [FILE_URL], (
        f"the return issues no new file GET, got {back['fileGets']!r}"
    )
    assert back["stageImgSrc"] is not None, (
        f"the return restores the image from the cache, got {back['stageImgSrc']!r}"
    )
    unhandled = _unhandled(results, "NO_POST_ON_TICK_REPAINT")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 16. CONTEXT_INVALIDATION ----------

def test_context_invalidation(results):
    r = _facts(results, "CONTEXT_INVALIDATION")
    ready, after_poll, at_seven = r["ready"], r["afterPoll"], r["atSeven"]
    assert ready["scene"] is not None and ready["scene"]["status"] == "ready", (
        f"the first generation is ready, got {ready['scene']!r}"
    )
    # A context change (a new occupant) invalidates WITHOUT generating.
    assert after_poll["postVisualScenes"] == 1, (
        f"the context change issues no regeneration POST, "
        f"got {after_poll['postVisualScenes']}"
    )
    # The place resets to idle with the updated confirmed text.
    assert at_seven["stageButtonText"] == "Показать сцену", (
        f"the context change resets the stage to idle, "
        f"got {at_seven['stageButtonText']!r}"
    )
    assert at_seven["stageImgSrc"] is None, (
        f"the context change drops the image, got {at_seven['stageImgSrc']!r}"
    )
    assert "Мария" in at_seven["stageText"], (
        f"the confirmed text reflects the new occupant, got {at_seven['stageText']!r}"
    )
    unhandled = _unhandled(results, "CONTEXT_INVALIDATION")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 17. BUTTON_NODE_STABLE ----------

def test_button_node_stable(results):
    r = _facts(results, "BUTTON_NODE_STABLE")
    focused_idle, ready_facts, same_node, still_focused = (
        r["focusedIdle"], r["readyFacts"], r["sameNode"], r["stillFocused"])
    assert focused_idle["activeElementIsStageButton"] is True, (
        "the idle button is focusable and becomes the active element"
    )
    assert same_node is True, (
        "the button node is retained across the idle→ready swap"
    )
    assert still_focused is True, (
        "focus is retained on the button node across the swap"
    )
    assert ready_facts["stageButtonText"] == "Сцена показана", (
        f"the ready button label, got {ready_facts['stageButtonText']!r}"
    )
    unhandled = _unhandled(results, "BUTTON_NODE_STABLE")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 18. NAV_AWAY_ABORTS_FLIGHT ----------

def test_nav_away_aborts_flight(results):
    r = _facts(results, "NAV_AWAY_ABORTS_FLIGHT")
    after_nav, released, after_release = (
        r["afterNav"], r["released"], r["afterRelease"])
    # The in-flight file GET is aborted at the network boundary.
    assert FILE_URL in after_nav["abortedFetches"], (
        f"the navigation aborts the in-flight file GET, "
        f"got {after_nav['abortedFetches']!r}"
    )
    # The aborted flight is not releasable and sets no ready state.
    assert released is False, "the aborted file GET is no longer held"
    assert after_nav["scene"] is None \
        or after_nav["scene"]["status"] != "ready", (
        f"the aborted flight sets no ready state, got {after_nav['scene']!r}"
    )
    assert after_nav["stageImgSrc"] is None, (
        f"the aborted flight mounts no image, got {after_nav['stageImgSrc']!r}"
    )
    assert after_nav["urlCreated"] == 0, (
        f"the aborted flight creates no objectURL, got {after_nav['urlCreated']}"
    )
    assert after_release["scene"] is None \
        or after_release["scene"]["status"] != "ready", (
        f"the late release sets no ready state, got {after_release['scene']!r}"
    )
    unhandled = _unhandled(results, "NAV_AWAY_ABORTS_FLIGHT")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 19. MINUTE_TICK_NO_INVALIDATE ----------

def test_minute_tick_no_invalidate(results):
    r = _facts(results, "MINUTE_TICK_NO_INVALIDATE")
    ready, after_tick, back = r["ready"], r["afterTick"], r["back"]
    assert ready["scene"] is not None and ready["scene"]["status"] == "ready", (
        f"the first generation is ready, got {ready['scene']!r}"
    )
    # A minute tick within the same world hour does NOT invalidate the cache.
    assert after_tick["postVisualScenes"] == 1, (
        f"the minute tick issues no regeneration POST, "
        f"got {after_tick['postVisualScenes']}"
    )
    assert back["scene"] is not None and back["scene"]["status"] == "ready", (
        f"the cache is still valid after the minute tick, got {back['scene']!r}"
    )
    assert back["postVisualScenes"] == 1, (
        f"the return issues no new POST, got {back['postVisualScenes']}"
    )
    assert back["fileGets"] == [FILE_URL], (
        f"the return issues no new file GET, got {back['fileGets']!r}"
    )
    assert back["stageImgSrc"] is not None, (
        f"the return restores the image from the cache, got {back['stageImgSrc']!r}"
    )
    unhandled = _unhandled(results, "MINUTE_TICK_NO_INVALIDATE")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 20. STALE_BLOB_AND_IDENTITY_SWITCH ----------

def test_stale_blob_and_identity_switch(results):
    r = _facts(results, "STALE_BLOB_AND_IDENTITY_SWITCH")
    blob_held, after_stale, ready_a, after_gen = (
        r["blobHeld"], r["afterStale"], r["readyA"], r["afterGen"])

    # Part (a): stale blob completion.
    # RED on base: base never starts generation (no «Показать сцену» button),
    # so the file blob is never held.
    assert blob_held == 1, (
        f"the file blob is held during generation, got {blob_held}"
    )
    # Regression controls (pass on base): the stale completion creates no
    # objectURL, does not set ready, and leaks no URL.
    assert after_stale["urlCreated"] == 0, (
        f"the stale completion creates no objectURL, "
        f"got {after_stale['urlCreated']}"
    )
    assert after_stale["scene"] is None or \
        after_stale["scene"]["status"] != "ready", (
        f"the stale completion does not set ready, got {after_stale['scene']!r}"
    )
    assert after_stale["urlLeaked"] == [], (
        f"the stale completion leaks no URL, got {after_stale['urlLeaked']!r}"
    )

    # Part (b): identity switch.
    # RED on base: base has no «Показать сцену» button, so no generation and
    # no ready cache entry exists before the identity switch.
    assert ready_a["scene"] is not None and ready_a["scene"]["status"] == "ready", (
        f"a ready cache entry exists before the identity switch, "
        f"got {ready_a['scene']!r}"
    )
    # The identity change invalidates the cache: a FRESH POST fires on return.
    assert after_gen["postVisualScenes"] > ready_a["postVisualScenes"], (
        f"the identity change invalidates the cache (a FRESH POST fires), "
        f"got {after_gen['postVisualScenes']} "
        f"(was {ready_a['postVisualScenes']})"
    )
    unhandled = _unhandled(results, "STALE_BLOB_AND_IDENTITY_SWITCH")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"
