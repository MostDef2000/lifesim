"""#310 — Living Map POI click = SELECTION, not MOVE.

Behavioral tests for the #310 POI-selection machinery in the REAL production
``backend/app/web/app.js`` (boot included), run inside a Node ``vm`` context
using the embedded stdlib harness below (the m302 vm-harness pattern,
extended). The harness only provides the browser surface (a minimal DOM,
fake timers, and a controlled network boundary); it does NOT mock or
reimplement ``route()``, ``api()``, ``viewWorld()``, ``buildLivingMap()``,
``selectPoi()``, ``moveTo()`` or any of the #310 logic
(``gameUiContext.selectedPoiId``, ``poiSelectionCard()``,
``updatePoiSelectionCard()``, ``bindPoiSelectionKeys()``). Every assertion is
on an observable fact: the fetch log (method+path counts and POST bodies),
the rendered DOM (the ``#poi-card`` context card, the ``anchor--selected``
ring, the ``#player-marker``), and the ``S`` / ``gameUiContext`` state.

Approved behavior under test (derived from the work order, not the code):
- A POI marker click SELECTS the marker (context card + ``anchor--selected``
  ring) and issues ZERO ``POST /actions`` — a click is never a MOVE intent.
- The card's «Посмотреть место» anchor is a GET-only place navigation
  (``#/location/<id>``); it moves nothing.
- The card's «Пойти сюда» button is the ONE move path: it reuses the existing
  ``moveTo()`` (a single ``POST /actions`` with ``action_type "MOVE"`` and
  ``params.location_id``) and the selection is consumed (card gone) after the
  move completes.
- The current-location variant («вы здесь») offers «Войти в место» (GET-only)
  and «Закрыть»; it never posts.
- An AUTONOMOUS character's «Пойти сюда» is gated client-side by ``moveTo()``
  (the localized hint toast, ZERO ``POST /actions``).
- The card is re-derived from CURRENT state on every poll rebuild: a poll that
  observes the player at the selected location flips the card to the «вы
  здесь» variant (no stale «другое место»), and the player marker still
  derives from ``S.character.location_id``.
- The selection survives a poll rebuild (the ``anchor--selected`` ring is
  re-derived inside ``buildLivingMap``).
- Escape closes the card and clears the selection.
- The × close button closes the card and clears the selection.
- Any route change away from ``#/world`` drops the selection (it is
  World-screen-local).
"""
import json
import os
import subprocess

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
APP_JS = os.path.join(ROOT, "backend/app/web/app.js")
SCRATCH = "/tmp/opencode/m310-testing"

# The Node harness (m302 pattern, extended for #310). It is infrastructure
# only: a minimal DOM / timer / fetch shim plus a fact collector and a
# controlled network boundary. No product behavior lives here. Two #310
# driver helpers are added:
#   __fireKey(key)   — invoke the window "keydown" listeners (the Escape
#                      close path binds a window keydown listener).
#   __clickAnchor(t) — click the first A whose text matches, simulating the
#                      browser's same-document hash navigation for an
#                      href="#/..." anchor (the shim's n.click() only fires
#                      onclick, which a plain href anchor has none of).
HARNESS_JS = r"""
// #310 behavioral harness — runs the REAL production app.js in a Node vm
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

// #310: unhandled rejections are a first-class observable — a stale card
// updater or a superseded load that throws must not escape.
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
    n.focus = function () { n._focused = true; };
    n.blur = function () { n._focused = false; };
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
  const URL = { createObjectURL: () => "blob:mock", revokeObjectURL() {} };
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
    __timers.timeouts.set(id, { fn, at: __timers.now + (ms || 0) });
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

  // ---------- controlled network boundary ----------
  // Every request is logged with method+path (the zero-POST assertion reads
  // the log) and the raw fetch opts (the POST /actions body assertion reads
  // opts.body — api() JSON-stringifies it before fetch).
  const __net = {
    log: [], held: [], responses: {},
    catchAll: { status: 404, body: { detail: "not found" } },
  };
  function makeResponse(spec) {
    const ok = spec.status >= 200 && spec.status < 300;
    return {
      status: spec.status, ok,
      json: () => Promise.resolve(spec.body === undefined ? null : spec.body),
    };
  }
  function fetch(url, opts) {
    const entry = { url: String(url), opts: opts || {}, held: false };
    __net.log.push(entry);
    let spec = __net.responses[String(url)];
    if (!spec) spec = __net.catchAll;
    if (spec.__hold) {
      entry.held = true;
      __net.held.push(entry);
      return new Promise((resolve) => {
        entry._resolve = () => {
          __net.held = __net.held.filter(h => h !== entry);
          resolve(makeResponse(entry._resp || spec));
        };
      });
    }
    return Promise.resolve().then(() => makeResponse(spec));
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
    entry._resolve();
    return true;
  }
  function __releaseAt(url, idx, spec) {
    const entries = __net.held.filter(h => h.url === String(url));
    const entry = entries[idx];
    if (!entry) return false;
    entry._resp = spec;
    entry._resolve();
    return true;
  }
  function __heldCount(url) {
    return __net.held.filter(h => h.url === String(url)).length;
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
  function __facts() {
    const S = globalThis.__S;
    const g = (typeof globalThis.__guc === "function") ? globalThis.__guc() : null;
    const poiCard = document.getElementById("poi-card");
    const poiTitle = poiCard
      ? (findDescendant(poiCard, (n) => n.tagName === "H3") || { textContent: null }).textContent
      : null;
    const poiAnchors = [];
    const poiButtons = [];
    if (poiCard) {
      (function walk(n) {
        for (const c of n._children) {
          if (c.nodeType !== 1) continue;
          if (c.tagName === "A")
            poiAnchors.push({ text: c.textContent, href: c.getAttribute("href") });
          if (c.tagName === "BUTTON") poiButtons.push(c.textContent);
          walk(c);
        }
      })(poiCard);
    }
    const selAnchor = document.querySelector(".anchor--selected");
    const playerMarker = document.getElementById("player-marker");
    const toastEl = document.getElementById("toast");
    const postActions = __net.log.filter(
      e => (e.opts.method || "GET").toUpperCase() === "POST" && e.url === "/actions"
    );
    return {
      hash: location.hash,
      postActions: postActions.length,
      postActionBodies: postActions.map(e => {
        try { return JSON.parse(e.opts.body); } catch (err) { return e.opts.body; }
      }),
      S_charLocId: S.character ? S.character.location_id : null,
      S_charControl: S.character ? S.character.control_mode : null,
      guc: g ? {
        selectedPoiId: g.selectedPoiId,
        selectedLocationId: g.selectedLocationId,
        viewLocationId: g.viewLocationId,
      } : null,
      poiCard: !!poiCard,
      poiTitle: poiTitle,
      poiAnchors: poiAnchors,
      poiButtons: poiButtons,
      selectedAnchorLocId: selAnchor ? selAnchor.getAttribute("data-loc-id") : null,
      playerMarkerLocId: playerMarker ? playerMarker.getAttribute("data-location-id") : null,
      hasWorldShell: !!document.querySelector(".world-shell"),
      hasPlaceShell: !!document.querySelector(".place-shell"),
      hasLivingMap: !!document.getElementById("living-map-host"),
      toastText: toastEl ? toastEl.textContent : null,
      toastVisible: toastEl ? !toastEl.classList.contains("hidden") : false,
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
  globalThis.__releaseAt = __releaseAt;
  globalThis.__heldCount = __heldCount;
  globalThis.__clickButton = __clickButton;
  globalThis.__clickAnchor = __clickAnchor;
  globalThis.__fireKey = __fireKey;
  globalThis.__advance = __advance;
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

USER_A = {"id": 1, "username": "u", "email": "e@x", "role": "player", "sessionTag": "A"}
CHAR1 = {
    "id": 1, "name": "P", "location_id": 7, "needs": {},
    "control_mode": "MANUAL", "is_owner": False, "money": 100,
}
# The authoritative player move — the poll observes the player at the remote
# place (9) while the world view is open (STALE_SELECTION_REDERIVES).
CHAR1_AT_9 = {**CHAR1, "location_id": 9}
# The AUTONOMOUS character — moveTo() gates the move client-side (no POST).
CHAR1_AUTONOMOUS = {**CHAR1, "control_mode": "AUTONOMOUS"}
WORLD_A = {"game_timestamp": 1500, "day": 2}
WEATHER_OFF = {"enabled": False}

# The Living Map requires ALL seven keyed POI types to carry coordinates for
# mapReady to be true (renderWorld: keyedPois.every(k => poiMap[k] &&
# poiMap[k].x !== null)). m302's LOC7/LOC9 carried x/y = None (the fallback
# list rendered); #310 needs the real map, so the catalog is extended with
# the remaining keyed POIs, each with coordinates.
LOC7 = {"id": 7, "name": "Settlement", "type": "settlement", "x": 40, "y": 50, "occupants": []}
LOC9 = {"id": 9, "name": "Remote", "type": "shop", "x": 60, "y": 30, "occupants": []}
LOC10 = {"id": 10, "name": "Workshop", "type": "workshop", "x": 42.89, "y": 56.0, "occupants": []}
LOC11 = {"id": 11, "name": "Kitchen", "type": "kitchen", "x": 46.79, "y": 51.4, "occupants": []}
LOC12 = {"id": 12, "name": "Storage", "type": "storage", "x": 50, "y": 60, "occupants": []}
LOC13 = {"id": 13, "name": "Well", "type": "well", "x": 35, "y": 45, "occupants": []}
LOC14 = {"id": 14, "name": "Pier", "type": "pier", "x": 46, "y": 91, "occupants": []}
CATALOG = [LOC7, LOC9, LOC10, LOC11, LOC12, LOC13, LOC14]

# The localized AUTONOMOUS hint (app.js AUTONOMOUS_HINT) — the client-side
# gate's observable.
AUTONOMOUS_HINT = "Персонаж действует сам — переключите режим в Профиле"


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


def _world_base(user=USER_A, char=CHAR1, catalog=None):
    """Authenticated boot + the world/place loaders' GETs."""
    return dict(
        _auth(user, char, [char]),
        **{
            "/world": _resp(200, WORLD_A),
            "/weather": _resp(200, WEATHER_OFF),
            "/locations": _resp(200, catalog if catalog is not None else CATALOG),
            "/world/events?since=0&limit=100": _resp(200, []),
        },
    )


SCENARIOS = [
    # ---------- 1. REMOTE_CLICK_NO_POST ----------
    {
        "name": "REMOTE_CLICK_NO_POST",
        "initialHash": "#/world",
        "responses": _world_base(),
        "driver": (
            "await __settle(); const afterBoot = __facts(); "
            "const clicked = __clickButton('Remote'); await __settle(); const after = __facts(); "
            "globalThis.__result = { afterBoot, clicked, after };"
        ),
    },
    # ---------- 2. VIEW_PLACE_GET_ONLY ----------
    {
        "name": "VIEW_PLACE_GET_ONLY",
        "initialHash": "#/world",
        "responses": _world_base(),
        "driver": (
            "await __settle(); "
            "const clicked = __clickButton('Remote'); await __settle(); "
            "const anchorClicked = __clickAnchor('Посмотреть место'); await __settle(); "
            "const after = __facts(); "
            "globalThis.__result = { clicked, anchorClicked, after };"
        ),
    },
    # ---------- 3. GO_HERE_ONE_POST ----------
    {
        "name": "GO_HERE_ONE_POST",
        "initialHash": "#/world",
        "responses": dict(_world_base(), **{"/actions": _resp(200, {})}),
        "driver": (
            "await __settle(); "
            "const clicked = __clickButton('Remote'); await __settle(); "
            "const goClicked = __clickButton('Пойти сюда'); await __settle(); "
            "const after = __facts(); "
            "globalThis.__result = { clicked, goClicked, after };"
        ),
    },
    # ---------- 4. CURRENT_LOC_CARD ----------
    {
        "name": "CURRENT_LOC_CARD",
        "initialHash": "#/world",
        "responses": _world_base(),
        "driver": (
            "await __settle(); "
            "const clicked = __clickButton('Settlement'); await __settle(); "
            "const afterSelect = __facts(); "
            "const anchorClicked = __clickAnchor('Войти в место'); await __settle(); "
            "const after = __facts(); "
            "globalThis.__result = { clicked, afterSelect, anchorClicked, after };"
        ),
    },
    # ---------- 5. AUTONOMOUS_NO_POST ----------
    {
        "name": "AUTONOMOUS_NO_POST",
        "initialHash": "#/world",
        "responses": _world_base(char=CHAR1_AUTONOMOUS),
        "driver": (
            "await __settle(); "
            "const clicked = __clickButton('Remote'); await __settle(); "
            "const goClicked = __clickButton('Пойти сюда'); await __settle(); "
            "const after = __facts(); "
            "globalThis.__result = { clicked, goClicked, after };"
        ),
    },
    # ---------- 6. STALE_SELECTION_REDERIVES ----------
    {
        "name": "STALE_SELECTION_REDERIVES",
        "initialHash": "#/world",
        "responses": _world_base(),
        "driver": (
            "await __settle(); "
            "const clicked = __clickButton('Remote'); await __settle(); "
            "const afterSelect = __facts(); "
            "__setResponse('/characters/1', {status:200, body:" + json.dumps(CHAR1_AT_9) + "}); "
            "await __call('pollEvents'); await __settle(); const after = __facts(); "
            "globalThis.__result = { clicked, afterSelect, after };"
        ),
    },
    # ---------- 7. SELECTION_SURVIVES_RERENDER ----------
    {
        "name": "SELECTION_SURVIVES_RERENDER",
        "initialHash": "#/world",
        "responses": _world_base(),
        "driver": (
            "await __settle(); "
            "const clicked = __clickButton('Remote'); await __settle(); "
            "await __call('pollEvents'); await __settle(); const after = __facts(); "
            "globalThis.__result = { clicked, after };"
        ),
    },
    # ---------- 8. ESCAPE_CLOSE ----------
    {
        "name": "ESCAPE_CLOSE",
        "initialHash": "#/world",
        "responses": _world_base(),
        "driver": (
            "await __settle(); "
            "const clicked = __clickButton('Remote'); await __settle(); "
            "const afterSelect = __facts(); "
            "__fireKey('Escape'); await __settle(); const after = __facts(); "
            "globalThis.__result = { clicked, afterSelect, after };"
        ),
    },
    # ---------- 9. CLOSE_BUTTON ----------
    {
        "name": "CLOSE_BUTTON",
        "initialHash": "#/world",
        "responses": _world_base(),
        "driver": (
            "await __settle(); "
            "const clicked = __clickButton('Remote'); await __settle(); "
            "const afterSelect = __facts(); "
            "const closeClicked = __clickButton('×'); await __settle(); const after = __facts(); "
            "globalThis.__result = { clicked, afterSelect, closeClicked, after };"
        ),
    },
    # ---------- 10. ROUTE_CHANGE_CLEARS ----------
    {
        "name": "ROUTE_CHANGE_CLEARS",
        "initialHash": "#/world",
        "responses": _world_base(),
        "driver": (
            "await __settle(); "
            "const clicked = __clickButton('Remote'); await __settle(); "
            "const afterSelect = __facts(); "
            "__setHash('#/location/9'); await __settle(); const atPlace = __facts(); "
            "__setHash('#/world'); await __settle(); const after = __facts(); "
            "globalThis.__result = { clicked, afterSelect, atPlace, after };"
        ),
    },
]


def _run_harness():
    os.makedirs(SCRATCH, exist_ok=True)
    harness_path = os.path.join(SCRATCH, "m310_harness.mjs")
    scen_path = os.path.join(SCRATCH, "m310_scenarios.json")
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


# ---------- 1. REMOTE_CLICK_NO_POST ----------

def test_remote_click_no_post(results):
    r = _facts(results, "REMOTE_CLICK_NO_POST")
    boot, clicked, after = r["afterBoot"], r["clicked"], r["after"]
    assert boot["hasLivingMap"] is True, (
        "the world renders the Living Map (mapReady), not the fallback list"
    )
    assert boot["S_charLocId"] == 7, "the player starts at location 7"
    assert clicked is True, "the remote POI marker (loc 9) is a clickable button"
    # The click SELECTS — it never moves.
    assert after["postActions"] == 0, (
        f"a POI click must issue ZERO POST /actions, got {after['postActions']}"
    )
    assert after["S_charLocId"] == 7, (
        f"a POI click must not move the player, got {after['S_charLocId']}"
    )
    # The context card is present with the remote variant.
    assert after["poiCard"] is True, "the POI selection card is rendered"
    assert after["poiTitle"] is not None and "другое место" in after["poiTitle"], (
        f"the remote card title carries «другое место», got {after['poiTitle']!r}"
    )
    assert after["poiAnchors"] == [
        {"text": "Посмотреть место", "href": "#/location/9"}
    ], f"the remote card offers the «Посмотреть место» anchor, got {after['poiAnchors']!r}"
    assert after["poiButtons"] == ["×", "Пойти сюда"], (
        f"the remote card offers «×» and «Пойти сюда», got {after['poiButtons']!r}"
    )
    # The clicked anchor carries the selection ring.
    assert after["selectedAnchorLocId"] == "9", (
        f"the clicked marker carries anchor--selected, got {after['selectedAnchorLocId']!r}"
    )
    assert after["guc"] is not None, "the #302 session UI context exists"
    assert after["guc"]["selectedPoiId"] == 9, (
        f"the selection is recorded in gameUiContext.selectedPoiId, "
        f"got {after['guc']['selectedPoiId']!r}"
    )
    unhandled = _unhandled(results, "REMOTE_CLICK_NO_POST")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 2. VIEW_PLACE_GET_ONLY ----------

def test_view_place_get_only(results):
    r = _facts(results, "VIEW_PLACE_GET_ONLY")
    clicked, anchor_clicked, after = r["clicked"], r["anchorClicked"], r["after"]
    assert clicked is True, "the remote POI marker is clickable"
    assert anchor_clicked is True, (
        "the card's «Посмотреть место» anchor is present and clickable"
    )
    assert after["hash"] == "#/location/9", (
        f"the anchor navigates to the remote place, got {after['hash']!r}"
    )
    assert after["hasPlaceShell"] is True, "the place view renders"
    assert after["postActions"] == 0, (
        f"viewing a place is GET-only — ZERO POST /actions, got {after['postActions']}"
    )
    assert after["S_charLocId"] == 7, "viewing a remote place must not move the player"
    assert after["poiCard"] is False, (
        "the selection card is World-screen-local — it is gone on the place view"
    )
    unhandled = _unhandled(results, "VIEW_PLACE_GET_ONLY")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 3. GO_HERE_ONE_POST ----------

def test_go_here_one_post(results):
    r = _facts(results, "GO_HERE_ONE_POST")
    clicked, go_clicked, after = r["clicked"], r["goClicked"], r["after"]
    assert clicked is True, "the remote POI marker is clickable"
    assert go_clicked is True, "the card's «Пойти сюда» button is present and clickable"
    # The ONE move path: a single POST /actions with the MOVE body.
    assert after["postActions"] == 1, (
        f"«Пойти сюда» issues exactly ONE POST /actions, got {after['postActions']}"
    )
    assert after["postActionBodies"] == [
        {"character_id": 1, "action_type": "MOVE", "params": {"location_id": 9}}
    ], (
        f"the POST body is the MOVE action to location 9, "
        f"got {after['postActionBodies']!r}"
    )
    # The selection is consumed after the move completes.
    assert after["poiCard"] is False, (
        "the selection card is gone after the move completes (selection consumed)"
    )
    assert after["guc"]["selectedPoiId"] is None, (
        f"the selection is cleared after the move, got {after['guc']['selectedPoiId']!r}"
    )
    assert after["hash"] == "#/world", "the move returns to the world view"
    unhandled = _unhandled(results, "GO_HERE_ONE_POST")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 4. CURRENT_LOC_CARD ----------

def test_current_loc_card(results):
    r = _facts(results, "CURRENT_LOC_CARD")
    clicked, after_select, anchor_clicked, after = (
        r["clicked"], r["afterSelect"], r["anchorClicked"], r["after"])
    assert clicked is True, "the current-location marker (loc 7) is clickable"
    # The current-location variant.
    assert after_select["poiCard"] is True, "the POI selection card is rendered"
    assert after_select["poiTitle"] is not None and "вы здесь" in after_select["poiTitle"], (
        f"the current card title carries «вы здесь», got {after_select['poiTitle']!r}"
    )
    assert after_select["poiAnchors"] == [
        {"text": "Войти в место", "href": "#/location/7"}
    ], (
        f"the current card offers the «Войти в место» anchor, "
        f"got {after_select['poiAnchors']!r}"
    )
    assert after_select["poiButtons"] == ["×", "Закрыть"], (
        f"the current card offers «×» and «Закрыть», got {after_select['poiButtons']!r}"
    )
    assert after_select["postActions"] == 0, (
        f"selecting the current location issues ZERO POST /actions, "
        f"got {after_select['postActions']}"
    )
    assert after_select["selectedAnchorLocId"] == "7", (
        f"the current marker carries anchor--selected, "
        f"got {after_select['selectedAnchorLocId']!r}"
    )
    # «Войти в место» is a GET-only place navigation.
    assert anchor_clicked is True, "the «Войти в место» anchor is present and clickable"
    assert after["hash"] == "#/location/7", (
        f"the anchor navigates to the current place, got {after['hash']!r}"
    )
    assert after["hasPlaceShell"] is True, "the place view renders"
    assert after["postActions"] == 0, (
        f"«Войти в место» is GET-only — still ZERO POST /actions, "
        f"got {after['postActions']}"
    )
    unhandled = _unhandled(results, "CURRENT_LOC_CARD")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 5. AUTONOMOUS_NO_POST ----------

def test_autonomous_no_post(results):
    r = _facts(results, "AUTONOMOUS_NO_POST")
    clicked, go_clicked, after = r["clicked"], r["goClicked"], r["after"]
    assert after["S_charControl"] == "AUTONOMOUS", "the character is AUTONOMOUS"
    assert clicked is True, "the remote POI marker is clickable"
    assert go_clicked is True, "the card's «Пойти сюда» button is present and clickable"
    # The client-side gate: no POST, the localized hint toast.
    assert after["postActions"] == 0, (
        f"an AUTONOMOUS character's «Пойти сюда» is gated client-side — "
        f"ZERO POST /actions, got {after['postActions']}"
    )
    assert after["toastText"] == AUTONOMOUS_HINT, (
        f"the AUTONOMOUS hint toast is shown, got {after['toastText']!r}"
    )
    assert after["toastVisible"] is True, "the hint toast is visible"
    # The selection is cleared after the gated move.
    assert after["poiCard"] is False, (
        "the selection card is gone after the gated move (selection cleared)"
    )
    assert after["guc"]["selectedPoiId"] is None, (
        f"the selection is cleared after the gated move, "
        f"got {after['guc']['selectedPoiId']!r}"
    )
    unhandled = _unhandled(results, "AUTONOMOUS_NO_POST")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 6. STALE_SELECTION_REDERIVES ----------

def test_stale_selection_rederives(results):
    r = _facts(results, "STALE_SELECTION_REDERIVES")
    clicked, after_select, after = r["clicked"], r["afterSelect"], r["after"]
    assert clicked is True, "the remote POI marker is clickable"
    # Before the poll: the remote variant.
    assert after_select["poiTitle"] is not None and "другое место" in after_select["poiTitle"], (
        f"before the poll the card is the remote variant, got {after_select['poiTitle']!r}"
    )
    # The poll applies the authoritative player move (7 → 9).
    assert after["S_charLocId"] == 9, (
        f"the poll applies the authoritative player move (7 → 9), got {after['S_charLocId']}"
    )
    # The card re-derives to the current variant (no stale «другое место»).
    assert after["poiCard"] is True, "the selection card is still open (the loc is valid)"
    assert after["poiTitle"] is not None and "вы здесь" in after["poiTitle"], (
        f"the card re-derives to the «вы здесь» variant, got {after['poiTitle']!r}"
    )
    assert "другое место" not in after["poiTitle"], (
        f"no stale «другое место» after the player moved to the selected loc, "
        f"got {after['poiTitle']!r}"
    )
    assert after["poiAnchors"] == [
        {"text": "Войти в место", "href": "#/location/9"}
    ], (
        f"the re-derived card offers «Войти в место», got {after['poiAnchors']!r}"
    )
    # No fake player marker: it still derives from S.character.location_id.
    assert after["playerMarkerLocId"] == "9", (
        f"the player marker derives from S.character.location_id (9), "
        f"got {after['playerMarkerLocId']!r}"
    )
    assert after["selectedAnchorLocId"] == "9", (
        f"the selected anchor ring is on loc 9, got {after['selectedAnchorLocId']!r}"
    )
    unhandled = _unhandled(results, "STALE_SELECTION_REDERIVES")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 7. SELECTION_SURVIVES_RERENDER ----------

def test_selection_survives_rerender(results):
    r = _facts(results, "SELECTION_SURVIVES_RERENDER")
    clicked, after = r["clicked"], r["after"]
    assert clicked is True, "the remote POI marker is clickable"
    assert after["S_charLocId"] == 7, "the player did not move (still at 7)"
    # The poll rebuild wipes the map host children — the selection survives.
    assert after["poiCard"] is True, (
        "the selection card survives the poll rebuild (re-derived from state)"
    )
    assert after["poiTitle"] is not None and "другое место" in after["poiTitle"], (
        f"the card is still the remote variant, got {after['poiTitle']!r}"
    )
    assert after["selectedAnchorLocId"] == "9", (
        f"the anchor--selected ring is re-derived on the rebuilt map (loc 9), "
        f"got {after['selectedAnchorLocId']!r}"
    )
    assert after["guc"]["selectedPoiId"] == 9, (
        f"the selection is retained in gameUiContext, got {after['guc']['selectedPoiId']!r}"
    )
    unhandled = _unhandled(results, "SELECTION_SURVIVES_RERENDER")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 8. ESCAPE_CLOSE ----------

def test_escape_close(results):
    r = _facts(results, "ESCAPE_CLOSE")
    clicked, after_select, after = r["clicked"], r["afterSelect"], r["after"]
    assert clicked is True, "the remote POI marker is clickable"
    assert after_select["poiCard"] is True, "the selection card is open before Escape"
    # Escape closes the card and clears the selection.
    assert after["poiCard"] is False, "Escape closes the selection card"
    assert after["guc"]["selectedPoiId"] is None, (
        f"Escape clears the selection, got {after['guc']['selectedPoiId']!r}"
    )
    assert after["selectedAnchorLocId"] is None, (
        f"Escape drops the anchor--selected ring, got {after['selectedAnchorLocId']!r}"
    )
    assert after["postActions"] == 0, "Escape issues no POST /actions"
    unhandled = _unhandled(results, "ESCAPE_CLOSE")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 9. CLOSE_BUTTON ----------

def test_close_button(results):
    r = _facts(results, "CLOSE_BUTTON")
    clicked, after_select, close_clicked, after = (
        r["clicked"], r["afterSelect"], r["closeClicked"], r["after"])
    assert clicked is True, "the remote POI marker is clickable"
    assert after_select["poiCard"] is True, "the selection card is open before close"
    assert close_clicked is True, "the card's «×» close button is present and clickable"
    # The × close button closes the card and clears the selection.
    assert after["poiCard"] is False, "the × close button closes the selection card"
    assert after["guc"]["selectedPoiId"] is None, (
        f"the × close button clears the selection, got {after['guc']['selectedPoiId']!r}"
    )
    assert after["selectedAnchorLocId"] is None, (
        f"the × close button drops the anchor--selected ring, "
        f"got {after['selectedAnchorLocId']!r}"
    )
    assert after["postActions"] == 0, "the × close button issues no POST /actions"
    unhandled = _unhandled(results, "CLOSE_BUTTON")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 10. ROUTE_CHANGE_CLEARS ----------

def test_route_change_clears(results):
    r = _facts(results, "ROUTE_CHANGE_CLEARS")
    clicked, after_select, at_place, after = (
        r["clicked"], r["afterSelect"], r["atPlace"], r["after"])
    assert clicked is True, "the remote POI marker is clickable"
    assert after_select["poiCard"] is True, "the selection card is open before navigation"
    # Leaving #/world drops the selection (route() clears it).
    assert at_place["hash"] == "#/location/9", "navigation lands on the remote place"
    assert at_place["poiCard"] is False, (
        "route() drops the World-screen-local selection on leaving #/world"
    )
    assert at_place["guc"]["selectedPoiId"] is None, (
        f"route() clears gameUiContext.selectedPoiId, "
        f"got {at_place['guc']['selectedPoiId']!r}"
    )
    # Returning to #/world: still no card (the selection was dropped, not carried).
    assert after["hash"] == "#/world", "the return lands on the world view"
    assert after["hasWorldShell"] is True, "the world view renders on the return"
    assert after["poiCard"] is False, (
        "the selection is not carried back to the world (it was dropped on the route change)"
    )
    assert after["guc"]["selectedPoiId"] is None, (
        f"the selection stays cleared on the return, got {after['guc']['selectedPoiId']!r}"
    )
    unhandled = _unhandled(results, "ROUTE_CHANGE_CLEARS")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"
