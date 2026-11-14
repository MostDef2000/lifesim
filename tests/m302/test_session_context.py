"""#302 — session UI context: World ↔ Place ↔ Dialogue navigation state.

Behavioral tests for the #302 session-context machinery in the REAL
production ``backend/app/web/app.js`` (boot included), run inside a Node
``vm`` context using the embedded stdlib harness below. The harness only
provides the browser surface (a minimal DOM, fake timers, and a controlled,
SCRIPTABLE network boundary); it does NOT mock or reimplement ``route()``,
``api()``, ``viewWorld()``, ``viewLocation()``, ``viewChat()`` or any of the
#302 logic (``gameUiContext``, ``navEpoch``, ``observedPlayerLocId``,
``resetGameUiContext()``). Every assertion is on an observable fact: the
fetch log (method+path counts), the rendered DOM (shells, the «Рядом»
row «Поговорить» button, the «← К месту» anchor, the composer), and the
``S`` / ``gameUiContext`` state.

#303: the Place shell moved the dialogue affordance from the old roster
«Диалог» button to a «Рядом» row «Поговорить» button (identical mechanics:
``gameUiContext.npcId`` → ``#/chat``, revalidated in ``viewChat``). The
scenario intent is unchanged; only the click-target label and the
descriptions below moved.

The network boundary is scriptable: responses can be QUEUED/DEFERRED
(``__hold``) and released in an EXPLICIT order (``__releaseAt``), which is
what lets scenario (c) drive out-of-order A→B→A response races.

Approved behavior under test (derived from the work order, not the code):
- (a) World → remote Place via DIRECT hash navigation (``#/location/<id>``
  where id != player location_id) → back to World: the player location_id is
  unchanged, ZERO ``POST /actions`` requests are issued, and the world focus
  is restored (``S.mapFocusLocationId`` == the focus recorded at place entry;
  ``S.mapFocusManual`` true when that focus differs from the player location).
- (b) World → current Place → Chat with a selected NPC («Рядом» row
  «Поговорить») →
  type a draft → back to Place («← К месту») → back to World → re-enter Chat:
  the same NPC is selected and the per-NPC draft is restored into the
  composer.
- (c) A→B→A response race: deferred fetch responses resolve OUT OF ORDER —
  an older view's load must not publish into the newer view's DOM or shared
  state (epoch guard), and no unhandled errors escape. Covers: a world load
  superseded by a place navigation, and a place load superseded by another
  navigation.
- (d) Logout → login as a DIFFERENT user: ``gameUiContext`` must not leak —
  userRef/characterId/selectedLocationId/viewLocationId/lastWorldFocusId/
  npcId null, draftByNpc empty, ``S.chatDraft`` "".
- (e) A carried NPC no longer present in the fresh ``characters_here`` list →
  ``viewChat`` drops ``gameUiContext.npcId`` and does not address the ghost
  NPC (``S.chatNpcId`` not switched to it).
- (f) A poll-driven player move while a remote place is open: the world
  focus restore on the return is SKIPPED (the player-location stamp no
  longer matches the authoritative location) — the focus stays at the
  poll-applied player location, no manual flag. The no-move positive
  control (the restore DOES fire) is scenario (a)/A1.
- (g) A «Рядом» carry superseded DURING the names fetch: the dead load
  writes nothing (``S.chatNpcId``/``S.chatSessionId``/``S.chatDraft``
  untouched, nothing rendered) — the shared S.* writes are deferred past
  the names-fetch guard.
"""
import json
import os
import subprocess

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
APP_JS = os.path.join(ROOT, "backend/app/web/app.js")
SCRATCH = "/tmp/opencode/m302-testing"

# The Node harness (m269 pattern, extended). It is infrastructure only: a
# minimal DOM / timer / fetch shim plus a fact collector and a SCRIPTABLE
# network boundary (queued/deferred responses with explicit release order,
# method+path request logging). No product behavior lives here.
HARNESS_JS = r"""
// #302 behavioral harness — runs the REAL production app.js in a Node vm
// context with a minimal stdlib DOM/timer/fetch shim. It does NOT mock
// route()/api()/viewWorld()/viewLocation()/viewChat() or reimplement any
// production logic; it only provides the browser surface (DOM, timers,
// network boundary) and collects observable facts (fetch log, DOM, S state,
// gameUiContext, navEpoch). Python asserts on the facts.
import fs from "fs";
import vm from "vm";

const appJsPath = process.argv[2];
const scenariosPath = process.argv[3];
const appSrc = fs.readFileSync(appJsPath, "utf-8");
const scenarios = JSON.parse(fs.readFileSync(scenariosPath, "utf-8"));

// #302: unhandled rejections are a first-class observable — a superseded
// load that throws (or a stale closure that rejects) must not escape.
const __unhandled = [];
process.on("unhandledRejection", (reason) => {
  __unhandled.push(String((reason && reason.message) || reason));
});

// Expose the lexical top-level bindings (const S/app/routes + the #302
// module-level lets) to the driver. Appended to the in-memory source only —
// the production file is untouched. The #302 lets are read through getters
// so a reassignment (resetGameUiContext) or increment (navEpoch++) is always
// observed; the typeof guards keep the harness loadable against a base
// app.js that lacks the #302 machinery (RED authenticity).
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
  // #302: first descendant (pre-order) matching a predicate — the minimal
  // selector above has no descendant combinator, so the driver uses this for
  // "the select inside #chat-view", "the «← К месту» anchor", "the
  // «Поговорить» button" (#303: the place dialogue entry), etc.
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

  // ---------- controlled, SCRIPTABLE network boundary ----------
  // Every request is logged with method+path (the zero-POST assertion reads
  // the log). Responses can be QUEUED/DEFERRED (__hold) and released in an
  // EXPLICIT order (__releaseAt by index) — that is what drives the
  // out-of-order A→B→A races in scenario (c).
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
  // #302: release the Nth held entry for a URL (0-based, in request order) —
  // explicit out-of-order resolution control for the A→B→A races.
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
  // #302: click the first BUTTON whose text matches (the «Рядом» row
  // «Поговорить» — #303 renamed the place dialogue entry from roster
  // «Диалог» — the profile «Выйти», ...). Returns found+clicked.
  function __clickButton(text) {
    const b = findDescendant(documentElement,
      (n) => n.tagName === "BUTTON" && n.textContent === text);
    if (b) { b.click(); return true; }
    return false;
  }
  // #302: set the composer value and fire its "input" listeners (the pinned
  // #175 binding AND the #302 per-NPC mirror both run, as in a real keystroke).
  function __fireInput(value) {
    const inp = document.getElementById("chat-msg-input");
    if (!inp) return false;
    inp.value = String(value);
    (inp._listeners["input"] || []).forEach(fn => fn({ target: inp }));
    return true;
  }
  // #302: set the chat NPC select value and fire its "change" listener.
  function __setChatNpc(value) {
    const cv = document.getElementById("chat-view");
    if (!cv) return false;
    const sel = findDescendant(cv, (n) => n.tagName === "SELECT");
    if (!sel) return false;
    sel.value = String(value);
    (sel._listeners["change"] || []).forEach(fn => fn({ target: sel }));
    return true;
  }
  function __facts() {
    const clock = document.getElementById("clock");
    const appEl = globalThis.__app;
    const first = appEl ? appEl._children.find(c => c.nodeType === 1) : null;
    const g = (typeof globalThis.__guc === "function") ? globalThis.__guc() : null;
    const S = globalThis.__S;
    const chatView = document.getElementById("chat-view");
    const chatSel = chatView
      ? findDescendant(chatView, (n) => n.tagName === "SELECT") : null;
    const backAnchor = chatView
      ? findDescendant(chatView,
          (n) => n.tagName === "A" && n.textContent === "← К месту") : null;
    const msgInput = document.getElementById("chat-msg-input");
    return {
      hash: location.hash,
      clockText: clock ? clock.textContent : null,
      hasClock: !!clock,
      worldFetches: __net.log.filter(e => e.url === "/world").length,
      fetchLog: __net.log.map(e => (e.opts.method || "GET").toUpperCase() + " " + e.url),
      postActions: __net.log.filter(
        e => (e.opts.method || "GET").toUpperCase() === "POST" && e.url === "/actions"
      ).length,
      eventsFetches: __net.log.filter(e => e.url.startsWith("/world/events")).length,
      S_userTag: S.user ? S.user.sessionTag : null,
      S_userId: S.user ? S.user.id : null,
      S_charId: S.character ? S.character.id : null,
      S_charLocId: S.character ? S.character.location_id : null,
      S_world: S.world,
      S_mapFocusLocationId: S.mapFocusLocationId,
      S_mapFocusManual: S.mapFocusManual,
      S_mapFocusAnchor: S.mapFocusAnchor,
      S_chatNpcId: S.chatNpcId,
      S_chatDraft: S.chatDraft,
      S_chatSessionId: S.chatSessionId,
      S_scene: S.scene,
      guc: g ? {
        userRefTag: g.userRef ? g.userRef.sessionTag : null,
        characterId: g.characterId,
        selectedLocationId: g.selectedLocationId,
        viewLocationId: g.viewLocationId,
        lastWorldFocusId: g.lastWorldFocusId,
        lastWorldFocusPlayerLocId: g.lastWorldFocusPlayerLocId,
        npcId: g.npcId,
        draftByNpc: g.draftByNpc,
      } : null,
      navEpoch: (typeof globalThis.__navEpoch === "function") ? globalThis.__navEpoch() : 0,
      obsLoc: (typeof globalThis.__obsLoc === "function") ? globalThis.__obsLoc() : null,
      wsCount: __ws.count,
      intervalCount: __timers.intervalCount,
      bodyClass: document.body.className,
      firstClass: first ? first.className : null,
      hasPeopleEmpty: !!document.querySelector(".people-empty"),
      hasChatShell: !!document.querySelector(".chat-shell"),
      hasWorldShell: !!document.querySelector(".world-shell"),
      hasPlaceShell: !!document.querySelector(".place-shell"),
      hasCreator: !!document.querySelector(".creator") || location.hash === "#/create",
      hasLanding: document.body.classList.contains("landing-mode"),
      chatNpcSel: chatSel ? chatSel.value : null,
      chatInputValue: msgInput ? msgInput.value : null,
      backToPlace: backAnchor
        ? { present: true, href: backAnchor.getAttribute("href") }
        : { present: false, href: null },
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
  globalThis.__fireInput = __fireInput;
  globalThis.__setChatNpc = __setChatNpc;
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
USER_B = {"id": 2, "username": "v", "email": "f@x", "role": "player", "sessionTag": "B"}
CHAR1 = {
    "id": 1, "name": "P", "location_id": 7, "needs": {},
    "control_mode": "MANUAL", "is_owner": False, "money": 100,
}
CHAR2 = {
    "id": 2, "name": "P2", "location_id": 7, "needs": {},
    "control_mode": "MANUAL", "is_owner": False, "money": 100,
}
# Scenario (f): the authoritative player move — the poll observes the
# player at the remote place (9) while the place view is open.
CHAR1_AT_9 = {**CHAR1, "location_id": 9}
WORLD_A = {"game_timestamp": 1500, "day": 2}
WEATHER_OFF = {"enabled": False}
LOC7 = {"id": 7, "name": "Settlement", "type": "settlement", "x": None, "y": None, "occupants": []}
LOC9 = {"id": 9, "name": "Remote", "type": "shop", "x": None, "y": None, "occupants": []}
# The player's CURRENT place with two NPC occupants («Рядом» row
# «Поговорить» source — #303 renamed the place dialogue entry).
LOC7_NPCS = {
    "id": 7, "name": "Settlement", "type": "settlement", "x": None, "y": None,
    "occupants": [
        {"id": "npc_1", "name": "NPC1", "kind": "npc"},
        {"id": "npc_2", "name": "NPC2", "kind": "npc"},
    ],
}
# The player's CURRENT place with a SINGLE NPC occupant — scenario (b) uses
# this so «Рядом» has exactly one «Поговорить» button (unambiguous click;
# #303 renamed the place dialogue entry from roster «Диалог»).
LOC7_NPC1 = {
    "id": 7, "name": "Settlement", "type": "settlement", "x": None, "y": None,
    "occupants": [{"id": "npc_1", "name": "NPC1", "kind": "npc"}],
}
# The /locations/<id> detail viewChat reads (characters_here = occupant ids).
LOC7_DETAIL = {"id": 7, "name": "Settlement", "type": "settlement",
               "characters_here": ["npc_1", "npc_2"]}
LOC7_DETAIL_NPC1 = {"id": 7, "name": "Settlement", "type": "settlement",
                    "characters_here": ["npc_1"]}
# npc_1 has left — the ghost-NPC case (scenario e).
LOC7_DETAIL_NO_NPC1 = {"id": 7, "name": "Settlement", "type": "settlement",
                       "characters_here": ["npc_2"]}
NPC1 = {"id": "npc_1", "name": "NPC1"}
NPC2 = {"id": "npc_2", "name": "NPC2"}


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
            "/locations": _resp(200, catalog if catalog is not None else [LOC7, LOC9]),
            "/world/events?since=0&limit=100": _resp(200, []),
        },
    )


def _chat_base(user=USER_A, char=CHAR1, detail=None, catalog=None):
    """World base + the chat loaders' GETs (location detail + NPC names)."""
    return dict(
        _world_base(user, char, catalog),
        **{
            f"/locations/{char['location_id']}": _resp(
                200, detail if detail is not None else LOC7_DETAIL),
            "/characters/npc_1": _resp(200, NPC1),
            "/characters/npc_2": _resp(200, NPC2),
        },
    )


SCENARIOS = [
    # ---------- (a) World → remote Place (direct hash) → World ----------
    {
        "name": "A1_remote_place_focus_player_loc",
        "initialHash": "#/world",
        "responses": _world_base(),
        "driver": (
            "await __settle(); const afterBoot = __facts(); "
            "__setHash('#/location/9'); await __settle(); const afterPlace = __facts(); "
            "__setHash('#/world'); await __settle(); const afterReturn = __facts(); "
            "globalThis.__result = { afterBoot, afterPlace, afterReturn };"
        ),
    },
    {
        "name": "A2_remote_place_foreign_focus",
        "initialHash": "#/world",
        "responses": _world_base(),
        "driver": (
            "await __settle(); "
            "__S.mapFocusLocationId = 9; __S.mapFocusManual = true; "
            "__setHash('#/location/9'); await __settle(); const afterPlace = __facts(); "
            "__setHash('#/world'); await __settle(); const afterReturn = __facts(); "
            "globalThis.__result = { afterPlace, afterReturn };"
        ),
    },
    # ---------- (b) Place → Chat («Рядом» row «Поговорить») → draft → round trip ----------
    {
        "name": "B_chat_draft_roundtrip",
        "initialHash": "#/world",
        # Single-NPC place: exactly one «Рядом» row «Поговорить» button
        # (unambiguous; #303 renamed the place dialogue entry from «Диалог»).
        "responses": _chat_base(catalog=[LOC7_NPC1, LOC9], detail=LOC7_DETAIL_NPC1),
        "driver": (
            "await __settle(); "
            "__setHash('#/location/7'); await __settle(); const place = __facts(); "
            "const clicked = __clickButton('Поговорить'); await __settle(); "
            "const chat1 = __facts(); "
            "__fireInput('hello'); const afterType = __facts(); "
            "__setHash('#/location/7'); await __settle(); "
            "__setHash('#/world'); await __settle(); "
            "__setHash('#/chat'); await __settle(); const chat2 = __facts(); "
            "globalThis.__result = { place, clicked, chat1, afterType, chat2 };"
        ),
    },
    # ---------- (c) A→B→A out-of-order response races ----------
    {
        "name": "C1_world_load_superseded_by_place",
        "initialHash": "#/world",
        "responses": dict(_world_base(), **{"/world": _resp(200, WORLD_A, __hold=True)}),
        "driver": (
            "await __settle(); const held1 = __heldCount('/world'); "
            "__setHash('#/location/9'); await __settle(); const held2 = __heldCount('/world'); "
            "__releaseAt('/world', 1, {status:200, body:" + json.dumps(WORLD_A) +
            "}); await __settle(); const afterNew = __facts(); "
            "__releaseAt('/world', 0, {status:200, body:" + json.dumps(WORLD_A) +
            "}); await __settle(); const afterOld = __facts(); "
            "globalThis.__result = { held1, held2, afterNew, afterOld };"
        ),
    },
    {
        "name": "C2_place_load_superseded_by_world",
        "initialHash": "#/world",
        "responses": _world_base(),
        "driver": (
            "await __settle(); "
            "__hold('/locations'); "
            "__setHash('#/location/7'); await __settle(); const held1 = __heldCount('/locations'); "
            "__setHash('#/world'); await __settle(); const held2 = __heldCount('/locations'); "
            "__releaseAt('/locations', 1, {status:200, body:" + json.dumps([LOC7, LOC9]) +
            "}); await __settle(); const afterNew = __facts(); "
            "__releaseAt('/locations', 0, {status:200, body:" + json.dumps([LOC7, LOC9]) +
            "}); await __settle(); const afterOld = __facts(); "
            "globalThis.__result = { held1, held2, afterNew, afterOld };"
        ),
    },
    # ---------- (d) logout → relogin as a different user: no leak ----------
    {
        "name": "D_logout_relogin_no_leak",
        "initialHash": "#/world",
        # Single-NPC place: exactly one «Рядом» row «Поговорить» button
        # (unambiguous; #303 renamed the place dialogue entry from «Диалог»).
        "responses": dict(_chat_base(catalog=[LOC7_NPC1, LOC9], detail=LOC7_DETAIL_NPC1),
                          **{"/auth/logout": _resp(200, {}),
                             "/characters/by-user/2": _resp(200, [CHAR2]),
                             "/characters/2": _resp(200, CHAR2),
                             "/characters/2/relationships": _resp(200, [])}),
        "driver": (
            "await __settle(); "
            "__setHash('#/location/7'); await __settle(); "
            "__clickButton('Поговорить'); await __settle(); "
            "__fireInput('hello'); "
            "const before = __facts(); "
            "__setResponse('/auth/me', {status:401, body:null}); "
            "__setHash('#/profile'); await __settle(); "
            "const clickedOut = __clickButton('Выйти'); await __settle(); "
            "const afterLogout = __facts(); "
            "__S.user = null; __S.character = null; __S.authProbed = false; "
            "__setResponse('/auth/me', {status:200, body:" + json.dumps(USER_B) + "}); "
            "__setHash('#/world'); await __settle(); const afterRelogin = __facts(); "
            "globalThis.__result = { before, clickedOut, afterLogout, afterRelogin };"
        ),
    },
    # ---------- (e) in-chat selection updates the carry; ghost never addressed ----------
    {
        "name": "E_ghost_npc_dropped",
        "initialHash": "#/world",
        "responses": _chat_base(catalog=[LOC7_NPCS, LOC9]),
        "driver": (
            "await __settle(); "
            "__setHash('#/location/7'); await __settle(); "
            # #302: set the carry directly (work order: "set
            # gameUiContext.npcId + navigate") — the two-NPC «Рядом» has two
            # «Поговорить» buttons (#303 renamed the entry) and the harness
            # first-match click is ambiguous.
            "__guc().npcId = 'npc_1'; "
            "__setHash('#/chat'); await __settle(); "
            "const switched = __setChatNpc('npc_2'); await __settle(); "
            "const mid = __facts(); "
            # #305: the departure signal now lives in the /locations CATALOG
            # (the roster source); the /locations/7 detail is no longer read
            # by viewChat, so this setResponse is an inert witness of the
            # superseded mechanism — kept to document the scenario's origin.
            "__setResponse('/locations/7', {status:200, body:" +
            json.dumps(LOC7_DETAIL_NO_NPC1) + "}); "
            "__setHash('#/world'); await __settle(); "
            "__setHash('#/chat'); await __settle(); const after = __facts(); "
            "globalThis.__result = { switched, mid, after };"
        ),
    },
    # ---------- (f) poll-driven player move → the stale focus restore is skipped ----------
    {
        "name": "F_stale_focus_restore_skipped_after_move",
        "initialHash": "#/world",
        "responses": _world_base(),
        "driver": (
            "await __settle(); const afterBoot = __facts(); "
            "__setHash('#/location/9'); await __settle(); const afterPlace = __facts(); "
            "__setResponse('/characters/1', {status:200, body:" + json.dumps(CHAR1_AT_9) +
            "}); "
            "await __call('pollEvents'); await __settle(); const afterMove = __facts(); "
            "__setHash('#/world'); await __settle(); const afterReturn = __facts(); "
            "globalThis.__result = { afterBoot, afterPlace, afterMove, afterReturn };"
        ),
    },
    # ---------- (g) carry superseded during the catalog read → writes nothing ----------
    {
        "name": "G_carry_superseded_during_names_fetch",
        "initialHash": "#/world",
        # Single-NPC place: exactly one «Рядом» row «Поговорить» button
        # (unambiguous; #303 renamed the place dialogue entry from «Диалог»).
        "responses": _chat_base(catalog=[LOC7_NPC1, LOC9], detail=LOC7_DETAIL_NPC1),
        "driver": (
            "await __settle(); "
            "__setHash('#/location/7'); await __settle(); const place = __facts(); "
            # #305: the roster names now come from the /locations catalog
            # occupants (the per-NPC /characters/{id} names fetch is gone —
            # a departed carry renders its name without one). The entry read
            # whose stale resolution must write nothing is the catalog
            # GET /locations, so that is what gets held.
            "__hold('/locations'); "
            "const clicked = __clickButton('Поговорить'); "
            "await __settle(); const held = __heldCount('/locations'); "
            "__setHash('#/world'); await __settle(); "
            # The superseding world load's OWN catalog read joins the hold
            # (request order: the chat's is idx 0, the world's idx 1).
            # Release the WORLD's first — the newer navigation must complete
            # and own the DOM while the chat load is still parked.
            "const released = __releaseAt('/locations', 1); "
            "await __settle(); const afterNav = __facts(); "
            # Now release the chat's STALE catalog read — it resolves after
            # the supersede and must write nothing.
            "const releasedStale = __releaseAt('/locations', 0); "
            "await __settle(); const after = __facts(); "
            "globalThis.__result = { place, clicked, held, afterNav, "
            "released, releasedStale, after };"
        ),
    },
]


def _run_harness():
    os.makedirs(SCRATCH, exist_ok=True)
    harness_path = os.path.join(SCRATCH, "m302_harness.mjs")
    scen_path = os.path.join(SCRATCH, "m302_scenarios.json")
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


# ---------- (a) World → remote Place → World: focus restored, no action POST ----------

def test_a1_remote_place_focus_player_loc(results):
    r = _facts(results, "A1_remote_place_focus_player_loc")
    boot, place, ret = r["afterBoot"], r["afterPlace"], r["afterReturn"]
    assert boot["S_charLocId"] == 7, "the player starts at location 7"
    assert place["hash"] == "#/location/9", "direct hash navigation lands on the remote place"
    assert place["hasPlaceShell"], "the remote place view renders"
    assert place["guc"] is not None, "the #302 session UI context exists"
    assert place["guc"]["viewLocationId"] == 9, "the validated place origin is recorded"
    assert place["guc"]["lastWorldFocusId"] == 7, (
        "the world focus at place entry is the player location (7)"
    )
    # The return: player location unchanged, zero action POSTs, focus restored.
    assert ret["hash"] == "#/world"
    assert ret["S_charLocId"] == 7, (
        f"browsing a remote place must not move the player, got {ret['S_charLocId']}"
    )
    assert ret["postActions"] == 0, (
        f"World → Place → World must issue ZERO POST /actions, got {ret['postActions']}"
    )
    assert ret["S_mapFocusLocationId"] == 7, (
        "the world focus is restored to the focus recorded at place entry (7), "
        f"got {ret['S_mapFocusLocationId']}"
    )
    assert ret["S_mapFocusManual"] is False, (
        "a focus equal to the player location is the poll's own default — no manual flag"
    )
    assert ret["guc"]["viewLocationId"] is None, "the return origin is consumed once"


def test_a2_remote_place_foreign_focus(results):
    r = _facts(results, "A2_remote_place_foreign_focus")
    place, ret = r["afterPlace"], r["afterReturn"]
    assert place["guc"]["lastWorldFocusId"] == 9, (
        "the foreign (manual) focus is recorded at place entry"
    )
    assert ret["S_charLocId"] == 7, "browsing a remote place must not move the player"
    assert ret["postActions"] == 0, "World → Place → World must issue ZERO POST /actions"
    assert ret["S_mapFocusLocationId"] == 9, (
        "the world focus is restored to the foreign focus recorded at place entry (9), "
        f"got {ret['S_mapFocusLocationId']}"
    )
    assert ret["S_mapFocusManual"] is True, (
        "a focus that DIFFERS from the player location carries the manual flag "
        "(the conditional-spelling restoration in viewWorld)"
    )


# ---------- (b) Place → Chat → draft → round trip: NPC + per-NPC draft restored ----------

def test_b_chat_draft_roundtrip(results):
    r = _facts(results, "B_chat_draft_roundtrip")
    place, clicked, chat1, after_type, chat2 = (
        r["place"], r["clicked"], r["chat1"], r["afterType"], r["chat2"])
    assert place["hasPlaceShell"], "the current place view renders"
    assert clicked is True, (
        "the place «Рядом» row offers a «Поговорить» button for the NPC "
        "occupant (the #302 entry point; #303 renamed it from roster «Диалог»)"
    )
    # Chat with the carried NPC.
    assert chat1["hash"] == "#/chat"
    assert chat1["hasChatShell"], "the chat view renders"
    assert chat1["chatNpcSel"] == "npc_1", (
        f"the «Рядом»-carried NPC is selected, got {chat1['chatNpcSel']!r}"
    )
    assert chat1["chatInputValue"] == "", "the composer starts empty"
    assert chat1["backToPlace"]["present"] is True, (
        "the «← К месту» back-to-Place anchor is rendered (the #302 return path)"
    )
    assert chat1["backToPlace"]["href"] == "#/location/7", (
        f"the back anchor points at the validated place origin, "
        f"got {chat1['backToPlace']['href']!r}"
    )
    # Type a draft.
    assert after_type["S_chatDraft"] == "hello", "the typed draft lands in the shared binding"
    assert after_type["guc"]["draftByNpc"].get("npc_1") == "hello", (
        "the per-NPC draft store captures the composer text keyed by the NPC"
    )
    # Round trip: place → world → chat. The same NPC is selected, the draft restored.
    assert chat2["hash"] == "#/chat"
    assert chat2["chatNpcSel"] == "npc_1", (
        f"re-entering chat re-selects the carried NPC, got {chat2['chatNpcSel']!r}"
    )
    assert chat2["chatInputValue"] == "hello", (
        f"the per-NPC draft is restored into the composer, got {chat2['chatInputValue']!r}"
    )


# ---------- (c) A→B→A out-of-order response races: no stale publish, no unhandled ----------

def test_c1_world_load_superseded_by_place(results):
    r = _facts(results, "C1_world_load_superseded_by_place")
    assert r["held1"] == 1, "the world load is in flight after boot"
    assert r["held2"] == 2, (
        "the place navigation adds a second in-flight /world (no coalescing without a "
        f"bootstrap), got {r['held2']}"
    )
    new, old = r["afterNew"], r["afterOld"]
    assert new["hasPlaceShell"] and new["hash"] == "#/location/9", (
        "the NEWER place load publishes first (out-of-order release)"
    )
    assert old["hasPlaceShell"] is True, (
        "the OLDER world load must not publish into the newer place view's DOM"
    )
    assert old["hasWorldShell"] is False, (
        "the superseded world load must not re-render the world view over the place"
    )
    assert old["hash"] == "#/location/9", (
        "the superseded world load must not yank the address back to #/world"
    )
    assert old["S_world"] is not None, "the shared world is owned by the newer load"
    unhandled = _unhandled(results, "C1_world_load_superseded_by_place")
    assert unhandled == [], f"no unhandled errors may escape the race: {unhandled}"


def test_c2_place_load_superseded_by_world(results):
    r = _facts(results, "C2_place_load_superseded_by_world")
    assert r["held1"] == 1, "the place load is in flight (holding /locations)"
    assert r["held2"] == 2, (
        "the world navigation adds a second in-flight /locations, got "
        f"{r['held2']}"
    )
    new, old = r["afterNew"], r["afterOld"]
    assert new["hasWorldShell"] and not new["hasPlaceShell"], (
        "the NEWER world load publishes first (out-of-order release)"
    )
    assert old["hasWorldShell"] is True, (
        "the OLDER place load must not publish into the newer world view's DOM"
    )
    assert old["hasPlaceShell"] is False, (
        "the superseded place load must not re-render the place over the world"
    )
    assert old["S_world"] is not None, "the shared world is owned by the newer load"
    # #302 machinery observables (absent in base app.js → RED):
    assert old["guc"] is not None, "the #302 session UI context exists"
    assert old["guc"]["selectedLocationId"] == 7, (
        "the selected place origin was recorded by the (superseded) place load"
    )
    assert old["navEpoch"] > 0, "the navigation epoch mechanism is active"
    unhandled = _unhandled(results, "C2_place_load_superseded_by_world")
    assert unhandled == [], f"no unhandled errors may escape the race: {unhandled}"


# ---------- (d) logout → relogin as a different user: gameUiContext must not leak ----------

def _assert_clean_context(f, label):
    assert f["guc"] is not None, f"{label}: the #302 session UI context exists"
    g = f["guc"]
    assert g["userRefTag"] is None, f"{label}: userRef must be null, got {g['userRefTag']!r}"
    assert g["characterId"] is None, f"{label}: characterId must be null"
    assert g["selectedLocationId"] is None, f"{label}: selectedLocationId must be null"
    assert g["viewLocationId"] is None, f"{label}: viewLocationId must be null"
    assert g["lastWorldFocusId"] is None, f"{label}: lastWorldFocusId must be null"
    assert "lastWorldFocusPlayerLocId" in g, (
        f"{label}: the focus-stamp field must exist in the context"
    )
    assert g["lastWorldFocusPlayerLocId"] is None, (
        f"{label}: lastWorldFocusPlayerLocId must be null, "
        f"got {g['lastWorldFocusPlayerLocId']!r}"
    )
    assert g["npcId"] is None, f"{label}: npcId must be null"
    assert g["draftByNpc"] == {}, f"{label}: draftByNpc must be empty, got {g['draftByNpc']!r}"
    assert f["S_chatDraft"] == "", f"{label}: S.chatDraft must be cleared"


def test_d_logout_relogin_no_leak(results):
    r = _facts(results, "D_logout_relogin_no_leak")
    before, clicked_out, after_logout, after_relogin = (
        r["before"], r["clickedOut"], r["afterLogout"], r["afterRelogin"])
    # The "before" state is populated (the leak source).
    assert before["guc"] is not None, "the #302 session UI context exists"
    assert before["guc"]["npcId"] == "npc_1", "the carried NPC is set before logout"
    assert before["guc"]["draftByNpc"].get("npc_1") == "hello", (
        "the per-NPC draft is stored before logout"
    )
    assert before["S_chatDraft"] == "hello", "the composer draft is set before logout"
    assert before["guc"]["viewLocationId"] == 7, "the place origin is recorded before logout"
    assert before["guc"]["lastWorldFocusPlayerLocId"] == 7, (
        "the focus stamp (the player location at place entry) is recorded before logout"
    )
    # The logout actually ran.
    assert clicked_out is True, "the profile «Выйти» button is present and clickable"
    assert after_logout["hash"] == "#/login", "logout lands on the login view"
    assert after_logout["S_userTag"] is None, "logout deauthenticates"
    # No leak after logout.
    _assert_clean_context(after_logout, "after logout")
    # Relogin as a DIFFERENT user — still no leak.
    assert after_relogin["S_userTag"] == "B", "relogin establishes the new session"
    assert after_relogin["S_charId"] == 2, "the new session's character is hydrated"
    _assert_clean_context(after_relogin, "after relogin")


# ---------- (e) in-chat selection updates the carry; ghost never addressed ----------
# #305: the #302 drop-on-absence semantics are SUPERSEDED. The carry
# (gameUiContext.npcId) now reflects the ACTUAL conversation partner: an
# explicit in-chat selection updates it, and a stale carry pointing at a
# departed NPC is retained and identifiable («NPC не рядом», sending
# disabled — pinned by m305 test_02), never silently dropped to None. The
# core #302 intent is preserved and strengthened: the ghost NPC is never
# addressed — the composer keeps the user's chosen present NPC and ZERO
# dialogue POSTs go to the departed one.

def test_e_ghost_npc_dropped(results):
    r = _facts(results, "E_ghost_npc_dropped")
    switched, mid, after = r["switched"], r["mid"], r["after"]
    assert switched is True, "the chat NPC select is present and switchable"
    assert mid["S_chatNpcId"] == "npc_2", "the user switched the dialogue to npc_2"
    # #305: the explicit selection IS the new intended target — the carry
    # updates to the chosen NPC (it is the restoration source for
    # chat → place → chat and the presence machine's target).
    assert mid["guc"]["npcId"] == "npc_2", (
        "the in-chat selection updates the carry to the chosen npc_2 "
        f"(#305 semantics), got {mid['guc']['npcId']!r}"
    )
    # npc_1 has left the place (the detail response); re-enter chat.
    assert after["hash"] == "#/chat"
    # #305: the carry points at the user's CURRENT target — the ghost is
    # neither addressed nor allowed to hijack the dialogue.
    assert after["guc"]["npcId"] == "npc_2", (
        "the carry reflects the user's chosen present NPC, not the ghost"
    )
    assert after["S_chatNpcId"] == "npc_2", (
        "the ghost NPC is NOT addressed — S.chatNpcId is not switched to it"
    )
    assert after["chatNpcSel"] == "npc_2", (
        f"the composer addresses npc_2, not the ghost, got {after['chatNpcSel']!r}"
    )
    # #305: zero-POST proof of the same intent — re-entering the chat with a
    # departed former carry fires NO dialogue POSTs at all (nothing is sent
    # to the ghost; the composer waits for the user).
    dialogue_posts = [e for e in after["fetchLog"]
                      if e.startswith("POST /dialogue")]
    assert dialogue_posts == [], (
        f"the ghost NPC is never addressed — no dialogue POSTs may fire "
        f"on re-entry, got {dialogue_posts!r}"
    )


# ---------- (f) poll-driven player move → the stale focus restore is skipped ----------

def test_f_stale_focus_restore_skipped_after_move(results):
    r = _facts(results, "F_stale_focus_restore_skipped_after_move")
    boot, place, moved, ret = (
        r["afterBoot"], r["afterPlace"], r["afterMove"], r["afterReturn"])
    # Setup: the world with the player (and the focus) at 7, then the remote place.
    assert boot["S_charLocId"] == 7, "the player starts at location 7"
    assert boot["S_mapFocusLocationId"] == 7, (
        "the world focus starts on the player location (7)"
    )
    assert place["hash"] == "#/location/9", (
        "direct hash navigation lands on the remote place"
    )
    assert place["hasPlaceShell"], "the remote place view renders"
    assert place["guc"]["viewLocationId"] == 9, "the validated place origin is recorded"
    assert place["guc"]["lastWorldFocusId"] == 7, (
        "the world focus at place entry (7) is stamped"
    )
    # The poll-driven move: the authoritative character location becomes 9.
    assert moved["S_charLocId"] == 9, (
        "the poll applies the authoritative player move (7 → 9), "
        f"got {moved['S_charLocId']}"
    )
    assert moved["S_mapFocusLocationId"] == 9, (
        "the poll's own #92 behavior moves the focus to the player location"
    )
    assert moved["S_mapFocusManual"] is False, (
        "the poll-applied focus is the default (no manual flag)"
    )
    # The return: the stale focus restore must NOT fire — the player moved
    # since the stamp. (Positive control without the move: A1 — the restore
    # DOES fire and is asserted there; not duplicated here.)
    assert ret["hash"] == "#/world"
    assert ret["hasWorldShell"], "the world view renders on the return"
    assert ret["S_charLocId"] == 9, "the player stays at the moved location"
    assert ret["S_mapFocusLocationId"] == 9, (
        "the restore did NOT fire — the focus stays at the poll-applied player "
        f"location (9), not the pre-place focus (7), got {ret['S_mapFocusLocationId']}"
    )
    assert ret["S_mapFocusManual"] is False, (
        "no manual flag — the focus is the player's own location, "
        f"got {ret['S_mapFocusManual']!r}"
    )
    assert ret["guc"]["viewLocationId"] is None, "the return origin is consumed once"
    assert ret["guc"]["lastWorldFocusId"] == 7, (
        "the focus stamp survives (the restore was skipped, not erased)"
    )
    assert ret["guc"].get("lastWorldFocusPlayerLocId") == 7, (
        "the player-location stamp survives (the restore was skipped, not erased)"
    )
    unhandled = _unhandled(results, "F_stale_focus_restore_skipped_after_move")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- (g) carry superseded during the chat entry's catalog read → writes nothing ----------

def test_g_carry_superseded_during_names_fetch(results):
    r = _facts(results, "G_carry_superseded_during_names_fetch")
    place, clicked, held, after_nav, released, released_stale, after = (
        r["place"], r["clicked"], r["held"], r["afterNav"], r["released"],
        r["releasedStale"], r["after"])
    assert place["hasPlaceShell"], "the current place view renders"
    assert clicked is True, (
        "the place «Рядом» row offers a «Поговорить» button for the NPC "
        "occupant (#303 renamed it from roster «Диалог»)"
    )
    # #305: the held fetch is the chat entry's catalog read — the new
    # viewChat takes roster names from the /locations occupants (the
    # per-NPC /characters/{id} names fetch is gone; a departed carry renders
    # its name from the catalog). Same scenario intent, new mechanism: the
    # in-flight entry read is held when the superseding navigation happens.
    assert held == 1, (
        "the chat entry's catalog read (/locations) is in flight (held) "
        "when the superseding navigation happens"
    )
    # #305: the superseding world load's OWN catalog read joins the hold
    # (request order idx 1) — it is released first so the newer navigation
    # completes and owns the DOM.
    assert released is True, (
        "the superseding world load's catalog read is released — the newer "
        "navigation completes over the parked chat load"
    )
    # The superseding navigation owns the DOM while the chat load is parked.
    assert after_nav["hash"] == "#/world", "the newer navigation is the world"
    assert after_nav["hasWorldShell"], "the world view renders over the pending chat"
    # FIX 1: the dead load writes nothing — not even before its held catalog
    # read resolves (the guard sits before every shared write).
    assert after_nav["S_chatNpcId"] is None, (
        "the superseded load writes nothing even before the held fetch resolves, "
        f"got {after_nav['S_chatNpcId']!r}"
    )
    # #305: the chat load's STALE catalog read is released after the
    # supersede — the guard must discard it.
    assert released_stale is True, (
        "the stale catalog read is released after the supersede"
    )
    assert after["S_chatNpcId"] is None, (
        "a superseded carry must not switch the dialogue, "
        f"got {after['S_chatNpcId']!r}"
    )
    assert after["S_chatDraft"] == "", "the composer draft stays empty"
    assert after["S_chatSessionId"] is None, (
        "no chat session is opened by the dead load"
    )
    # The dead load renders nothing — the newer world view owns the DOM.
    assert after["hash"] == "#/world"
    assert after["hasWorldShell"] is True, "the newer world navigation owns the DOM"
    assert after["hasChatShell"] is False, "the dead chat load renders nothing"
    # The carry itself survives (the restoration source for the next entry).
    assert after["guc"]["npcId"] == "npc_1", (
        "the «Рядом» carry is not cleared by the dead load (restoration source)"
    )
    unhandled = _unhandled(results, "G_carry_superseded_during_names_fetch")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"
