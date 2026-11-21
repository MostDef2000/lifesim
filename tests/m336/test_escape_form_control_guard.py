"""#336 — Escape inside a form control must NOT navigate (form-control guard).

Behavioral tests for the #336 Escape guard in the REAL production
``backend/app/web/app.js`` (boot included), run inside a Node ``vm``
context using the embedded stdlib harness below — an OWN copy of the m310
vm-harness pattern with ONE extension: ``__fireKey(key, opts)`` merges
``opts`` into the synthetic window keydown event so a scenario can shape
``ev.target``. The m310 driver fired ``{key, preventDefault,
stopPropagation}`` — NO ``target`` at all — which is exactly the
null-safety case the fix must keep working.

Approved behavior under test (derived from the work order, not the code):
- Escape whose ``ev.target.tagName`` is INPUT / TEXTAREA / SELECT on a
  contextual route (``.ctx-topbar`` mounted) does NOT change
  ``location.hash`` — the key belongs to the control (e.g. closing a native
  ``<select>``), not to contextual back navigation.
- Escape with NO ``target`` on the event (the m310 harness's existing
  synthetic shape) still navigates: the guard is null-safe — no throw, the
  contextual back hash (``#/world``) is applied. Critical regression guard:
  the m310 Escape scenarios fire without ``target``.
- Escape with ``ev.target.tagName == "DIV"`` still navigates (the guard is
  narrow — only form controls are exempt).
- Disclosure-first order (E14): with an OPEN ``<details>`` inside the
  ctx-topbar, Escape closes it and does NOT navigate.
- Source pin: the guard literal precedes the ``.ctx-topbar`` query inside
  the Escape handler (structure is supporting evidence; the scenarios above
  are the proof).

The boot always passes through ``#/world`` first: ``bindPoiSelectionKeys()``
— the ONLY window keydown listener in app.js — binds from ``viewWorld()``.
"""
import json
import os
import subprocess

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
APP_JS = os.path.join(ROOT, "backend/app/web/app.js")
# The source text for the structure pin (APP_JS above is the PATH the node
# harness loads).
with open(APP_JS, encoding="utf-8") as _f:
    APP_JS_TEXT = _f.read()
SCRATCH = "/tmp/opencode/m336-testing"

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
  // #310/#336: fire the window "keydown" listeners. opts is merged into the
  // synthetic event so a scenario can SHAPE the target (the form-control
  // guard); omitting opts keeps the original m310 event shape — no target
  // key at all — which is the null-safety case the fix must not break.
  // (No backticks here: this comment lives inside the SHIM template
  // literal, where an unescaped backtick would terminate it.)
  function __fireKey(key, opts) {
    const ev = Object.assign({ key, preventDefault() {}, stopPropagation() {} },
      opts || {});
    (winListeners["keydown"] || []).forEach(fn => fn(ev));
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
    const ctxBar = document.querySelector(".ctx-topbar");
    const ctxDet = ctxBar ? ctxBar.querySelector("details") : null;
    return {
      hash: location.hash,
      ctxTopbar: !!ctxBar,
      ctxDetailsOpen: ctxDet ? !!ctxDet.open : null,
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



# ---------- #336 fixtures ----------

LOC7_DETAIL = {"id": 7, "name": "Settlement", "characters_here": []}
TASKS_EMPTY = {"planned": [], "active": [], "recent_terminal": []}


def _world_then_place():
    """Authenticated #/world boot + the Place loader's GETs.

    The boot MUST pass through #/world: bindPoiSelectionKeys() (the only
    Escape path in app.js) binds from viewWorld(), so a cold entry to
    #/location/7 would never register the window keydown listener.
    """
    return dict(
        _world_base(),
        **{
            "/locations/7": _resp(200, LOC7_DETAIL),
            "/characters/1/tasks": _resp(200, TASKS_EMPTY),
        },
    )


# ---------- scenarios (one fresh boot each) ----------

_BOOT_ON_PLACE = (
    "await __settle(); "
    "__setHash('#/location/7'); await __settle(); "
)


def _place_escape_driver(fire_call, pre="", extra=""):
    """Boot #/world -> navigate to the contextual place route -> optional
    pre-work -> snapshot -> Escape -> snapshot -> optional extra facts."""
    return (
        _BOOT_ON_PLACE
        + pre
        + "const before = __facts(); "
        + fire_call
        + " await __settle(); const after = __facts(); "
        + "globalThis.__result = { before, after" + extra + " };"
    )


SCENARIOS = []
for _tag in ("INPUT", "TEXTAREA", "SELECT"):
    SCENARIOS.append({
        "name": "ESC_%s_NO_NAV" % _tag,
        "initialHash": "#/world",
        "responses": _world_then_place(),
        "driver": _place_escape_driver(
            "__fireKey('Escape', {target: {tagName: '%s'}});" % _tag),
    })
SCENARIOS += [
    # the m310 synthetic event shape: NO target key at all (null-safety).
    {
        "name": "ESC_NULL_TARGET_NAV",
        "initialHash": "#/world",
        "responses": _world_then_place(),
        "driver": _place_escape_driver("__fireKey('Escape', {});"),
    },
    {
        "name": "ESC_DIV_TARGET_NAV",
        "initialHash": "#/world",
        "responses": _world_then_place(),
        "driver": _place_escape_driver(
            "__fireKey('Escape', {target: {tagName: 'DIV'}});"),
    },
    {
        "name": "ESC_DISCLOSURE_FIRST",
        "initialHash": "#/world",
        "responses": _world_then_place(),
        "driver": _place_escape_driver(
            "__fireKey('Escape', {});",
            pre=(
                "const bar = document.querySelector('.ctx-topbar'); "
                "const det = bar ? bar.querySelector('details') : null; "
                "if (det) det.open = true; "
            ),
            extra=", detPresent: !!det",
        ),
    },
]


def _run_harness():
    os.makedirs(SCRATCH, exist_ok=True)
    harness_path = os.path.join(SCRATCH, "m336_harness.mjs")
    scen_path = os.path.join(SCRATCH, "m336_scenarios.json")
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


# ---------- 1. form controls swallow Escape (RED on base) ----------

def test_escape_in_form_control_does_not_navigate(results):
    # INPUT / TEXTAREA / SELECT on a contextual route: no navigation.
    for tag in ("INPUT", "TEXTAREA", "SELECT"):
        name = f"ESC_{tag}_NO_NAV"
        r = _facts(results, name)
        assert r["before"]["hash"] == "#/location/7", (
            f"{name}: the scenario must start on the contextual route, "
            f"got {r['before']['hash']!r}"
        )
        assert r["before"]["ctxTopbar"] is True, (
            f"{name}: the contextual header must be mounted before Escape"
        )
        assert r["after"]["hash"] == "#/location/7", (
            f"Escape with ev.target.tagName={tag!r} must NOT navigate "
            f"(the key belongs to the control), got {r['after']['hash']!r}"
        )
        assert r["after"]["ctxTopbar"] is True, (
            f"{name}: the route must still be the contextual one"
        )
        assert _unhandled(results, name) == [], (
            f"{name}: no unhandled errors may escape: "
            f"{_unhandled(results, name)}"
        )


# ---------- 2. null target: still navigates (null-safety guard) ----------

def test_escape_without_target_still_navigates(results):
    # The m310 harness fires {key, preventDefault, stopPropagation} with NO
    # target — the guard must not throw and must not block contextual back.
    name = "ESC_NULL_TARGET_NAV"
    r = _facts(results, name)
    assert r["before"]["hash"] == "#/location/7", (
        f"the scenario must start on the contextual route, "
        f"got {r['before']['hash']!r}"
    )
    assert r["before"]["ctxTopbar"] is True, (
        "the contextual header must be mounted before Escape"
    )
    assert r["after"]["hash"] == "#/world", (
        "Escape without ev.target must still navigate via ctxBackHash() "
        f"(null-safe, no throw), got {r['after']['hash']!r}"
    )
    assert _unhandled(results, name) == [], (
        f"no unhandled errors may escape: {_unhandled(results, name)}"
    )


# ---------- 3. the guard is narrow: a DIV still navigates ----------

def test_escape_with_div_target_still_navigates(results):
    name = "ESC_DIV_TARGET_NAV"
    r = _facts(results, name)
    assert r["before"]["ctxTopbar"] is True, (
        "the contextual header must be mounted before Escape"
    )
    assert r["after"]["hash"] == "#/world", (
        "Escape with ev.target.tagName=DIV must still navigate "
        f"(only form controls are exempt), got {r['after']['hash']!r}"
    )
    assert _unhandled(results, name) == [], (
        f"no unhandled errors may escape: {_unhandled(results, name)}"
    )


# ---------- 4. E14: disclosure closes first, no navigation ----------

def test_escape_closes_open_disclosure_without_navigating(results):
    name = "ESC_DISCLOSURE_FIRST"
    r = _facts(results, name)
    assert r["detPresent"] is True, (
        "the ctx-topbar must carry the native <details> disclosure"
    )
    assert r["before"]["ctxDetailsOpen"] is True, (
        "the scenario must open the disclosure before Escape"
    )
    assert r["after"]["ctxDetailsOpen"] is False, (
        "Escape must close the OPEN disclosure first (E14 order)"
    )
    assert r["after"]["hash"] == "#/location/7", (
        "a closed-on-Escape disclosure must NOT navigate, "
        f"got {r['after']['hash']!r}"
    )
    assert _unhandled(results, name) == [], (
        f"no unhandled errors may escape: {_unhandled(results, name)}"
    )


# ---------- 5. source pin (supporting evidence, m327 style) ----------

def test_escape_form_control_guard_source_pin():
    # The guard literal lives inside the Escape handler BEFORE the
    # .ctx-topbar query. Order matters: a guard placed after the navigation
    # would never run. Plain index checks (not _balanced): the #336 comment
    # itself carries a backtick, which poisons quote-aware extraction.
    handler_at = APP_JS_TEXT.index("function bindPoiSelectionKeys(")
    escape_at = APP_JS_TEXT.index('if (ev.key !== "Escape") return;', handler_at)
    guard_at = APP_JS_TEXT.index('["INPUT", "TEXTAREA", "SELECT"]', handler_at)
    ctx_at = APP_JS_TEXT.index('document.querySelector(".ctx-topbar")', handler_at)
    assert escape_at < guard_at < ctx_at, (
        "the form-control guard must sit between the Escape key check and "
        f"the .ctx-topbar query (escape@{escape_at}, guard@{guard_at}, "
        f"ctx@{ctx_at})"
    )
    assert APP_JS_TEXT.count('document.querySelector(".ctx-topbar")') == 1, (
        "the Escape handler's ctx-topbar query must stay unique"
    )
