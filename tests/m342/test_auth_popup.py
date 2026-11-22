"""#342 PART B — OIDC popup flow: frontend Node-VM tests.

Runs the REAL production ``backend/app/web/app.js`` (boot included) and
``backend/app/web/auth-popup.js`` inside Node ``vm`` contexts. The app.js
harness is the m269 SHIM verbatim plus the #342 additions (``location.origin``,
a ``location.reload()`` counter, a ``window.open`` stub with call log +
configurable return, the ``window.location = location`` alias assigned after
both consts, and the ``__openCalls`` / ``__setOpenReturn`` / ``__reloadCount``
/ ``__winListeners`` / ``__dispatchMessage`` exports); the popup harness is a
minimal browser surface (status node, ``body.dataset``, ``window.opener``
variants, ``location.origin``, a ``window.close`` counter, postMessage
capture). Neither harness mocks or reimplements any product logic — every
assertion is on an observable fact.

Approved behavior under test (derived from the work order, not the code):
- A1: the landing CTA opens the popup exactly once with
  ``("/auth/login?mode=popup", "lifesim-auth", "popup,width=520,height=680")``
  and does not touch ``location.href``;
- A2: a null ``window.open`` return falls back to
  ``location.href = "/auth/login"``;
- A3: exactly ONE window ``message`` listener at module load, still exactly
  one after repeated ``viewAuth()`` renders;
- A4: an own-origin ``{type:"lifesim-auth", ok:true}`` message reloads
  exactly once;
- A5: foreign origin / wrong type / ``ok:false`` / ``ok:"true"`` / null /
  string payload → ZERO reloads;
- P1: ``data-ok="true"`` posts exactly ``{type:"lifesim-auth", ok:true}`` to
  ``location.origin``, then ``window.close()`` once; success status text;
- P2: each of the 6 allowlisted error codes → its fixed Russian string, no
  post, no close;
- P3: unknown codes (``__proto__``, ``evil``) → the generic ``auth_failed``
  text;
- P4/P5: missing / closed opener → no throw, no close;
- P6: a throwing ``postMessage`` → no uncaught exception, no close;
- the completion page's script is served: ``GET /static/auth-popup.js`` →
  200, ``text/javascript``, the real source.
"""
import json
import os
import shutil
import subprocess
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.api.app import create_app
from app.config.config import load_config
from app.db.models import bootstrap, create_engine_factory

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
APP_JS = os.path.join(ROOT, "backend/app/web/app.js")
POPUP_JS = os.path.join(ROOT, "backend/app/web/auth-popup.js")
SCRATCH = "/tmp/opencode/m342/harness"

# The Node harness for app.js (m269's harness + SHIM verbatim, plus the #342
# additions marked in the diff). It is infrastructure only: a minimal DOM /
# timer / fetch shim plus a fact collector. No product behavior lives here.
HARNESS_APP_JS = r"""// #342 PART B harness — runs the REAL production app.js in a Node vm
// context with a minimal stdlib DOM/timer/fetch shim. It does NOT mock
// route()/api()/topbar()/hydrateWorldClock() or reimplement any production
// logic; it only provides the browser surface (DOM, timers, network boundary)
// and collects observable facts (fetch log, clock text, S state, WS/interval
// counts). Python asserts on the facts.
import fs from "fs";
import vm from "vm";

const appJsPath = process.argv[2];
const scenariosPath = process.argv[3];
const appSrc = fs.readFileSync(appJsPath, "utf-8");
const scenarios = JSON.parse(fs.readFileSync(scenariosPath, "utf-8"));

// Expose the lexical top-level bindings (const S/app/routes) to the driver.
// Appended to the in-memory source only — the production file is untouched.
const EXPOSE = "\n;globalThis.__S = S; globalThis.__app = app; "
  + "globalThis.__routes = routes; globalThis.__viewAuth = viewAuth;\n";

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
    // textContent: real accessor over child text nodes (browser semantics).
    // Reads concatenate descendant text; writes replace children with one
    // text node. Makes clock.textContent writes and the initial el() label
    // child observable by the driver.
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
    // compound: split into tag + classes
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

  // ---------- document ----------
  const body = makeElement("body");
  const documentElement = makeElement("html");
  documentElement._parent = null;
  body._parent = documentElement;
  documentElement._children.push(body);
  // index.html static roots: #app (the render target) and #toast.
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
  let __reloadCount = 0;  // #342: location.reload() counter
  const location = {
    get hash() { return _hash; },
    set hash(v) {
      const old = _hash;
      _hash = v;
      if (old === v) return;  // browser: no hashchange when the value is unchanged
      // browser: hashchange fires async (after current task)
      queueMicrotask(() => {
        (winListeners["hashchange"] || []).forEach(fn => fn({ type: "hashchange" }));
      });
    },
    protocol: "http:",
    host: "localhost",
    origin: "http://localhost",  // #342
    href: "http://localhost/",
    assign() {}, reload() { __reloadCount++; }, replace() {},
  };
  // #342: window.location aliases the SAME location object (assigned
  // AFTER both consts — TDZ). window.open is a stub: call log +
  // configurable return (the popup CTA's null-fallback is the contract).
  window.location = location;
  const __openCalls = [];
  let __openReturn = {};
  window.open = function (url, name, features) {
    __openCalls.push([url, name, features]);
    return __openReturn;
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

  // ---------- driver helpers ----------
  async function __settle() {
    for (let i = 0; i < 400; i++) { await Promise.resolve(); await Promise.resolve(); }
  }
  function __setHash(h) { location.hash = h; }
  function __call(name, ...args) { return globalThis[name](...args); }
  function __facts() {
    const clock = document.getElementById("clock");
    const appEl = globalThis.__app;
    const first = appEl ? appEl._children.find(c => c.nodeType === 1) : null;
    return {
      hash: location.hash,
      clockText: clock ? clock.textContent : null,
      hasClock: !!clock,
      worldFetches: __net.log.filter(e => e.url === "/world").length,
      fetchLog: __net.log.map(e => e.url),
      eventsFetches: __net.log.filter(e => e.url.startsWith("/world/events")).length,
      S_userTag: globalThis.__S.user ? globalThis.__S.user.sessionTag : null,
      S_userId: globalThis.__S.user ? globalThis.__S.user.id : null,
      S_charId: globalThis.__S.character ? globalThis.__S.character.id : null,
      S_world: globalThis.__S.world,
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
  // Set the address BEFORE boot without firing hashchange — a real cold load
  // starts at the URL and the app's own boot route() is the ONLY dispatch.
  globalThis.__setInitialHash = (h) => { _hash = String(h); };
  globalThis.__call = __call;
  globalThis.__facts = __facts;
  globalThis.__setResponse = __setResponse;
  globalThis.__hold = __hold;
  globalThis.__release = __release;
  globalThis.__advance = __advance;
  // #342: popup-flow observability
  globalThis.__openCalls = __openCalls;
  globalThis.__setOpenReturn = (v) => { __openReturn = v; };
  globalThis.__reloadCount = () => __reloadCount;
  globalThis.__winListeners = winListeners;
  globalThis.__dispatchMessage = (event) => {
    (winListeners["message"] || []).forEach(fn => fn(event));
  };
})();
`;
;

function runScenarioAsync(name, spec) {
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
    // wrap the driver so its returned promise is captured; the driver body
    // sets globalThis.__result, which we return here.
    const wrapped = `(async () => { ${spec.driver} return globalThis.__result; })();`;
    const promise = vm.runInContext(wrapped, context, { filename: "driver.js" });
    return promise.then(
      (result) => ({ name, ok: true, result: result === undefined ? null : result }),
      (e) => ({ name, ok: false, error: String(e && e.stack || e) }),
    );
  } catch (e) {
    return Promise.resolve({ name, ok: false, error: String(e && e.stack || e) });
  }
}

const out = [];
for (const spec of scenarios) {
  const r = await runScenarioAsync(spec.name, spec);
  out.push(r);
}
process.stdout.write(JSON.stringify(out, null, 2));
"""

# The Node harness for auth-popup.js — a minimal browser surface per scenario
# (fresh vm context each run): the #auth-popup-status node, body.dataset,
# window.opener variants, location.origin, a window.close counter and a
# postMessage capture. No product behavior lives here.
HARNESS_POPUP_JS = r"""
// #342 PART B — auth-popup.js harness. Runs the REAL production
// backend/app/web/auth-popup.js in a fresh Node vm context per scenario.
import fs from "fs";
import vm from "vm";

const popupJsPath = process.argv[2];
const scenariosPath = process.argv[3];
const popupSrc = fs.readFileSync(popupJsPath, "utf-8");
const scenarios = JSON.parse(fs.readFileSync(scenariosPath, "utf-8"));

const SHIM = `
"use strict";
(function () {
  const status = { textContent: "" };
  const document = {
    body: { dataset: {} },
    getElementById: (id) => (id === "auth-popup-status" ? status : null),
  };
  const location = { origin: "http://localhost" };
  const __posts = [];
  let __closeCount = 0;
  let __opener = null;
  const window = {
    close() { __closeCount++; },
  };
  // #342 race fix: controlled timers — the success path awaits a 150ms
  // cookie-commit tick before postMessage/close. No wall-clock waits:
  // callbacks queue with their delay; __drainTimers() runs them in delay
  // order and drains microtasks so the awaited continuation completes.
  const __timers = [];
  let __now = 0;
  function setTimeout(fn, ms) {
    __timers.push({ fn, at: __now + (ms || 0) });
    return __timers.length;
  }
  globalThis.setTimeout = setTimeout;
  globalThis.__drainTimers = async () => {
    for (;;) {
      let ran = false;
      while (__timers.length) {
        __timers.sort((a, b) => a.at - b.at);
        const t = __timers.shift();
        __now = Math.max(__now, t.at);
        t.fn();
        ran = true;
      }
      for (let i = 0; i < 50; i++) await Promise.resolve();
      if (!ran) break;
    }
  };
  globalThis.document = document;
  globalThis.window = window;
  globalThis.location = location;
  globalThis.__posts = __posts;
  globalThis.__closeCount = () => __closeCount;
  globalThis.__statusText = () => status.textContent;
  globalThis.__setDataset = (d) => { document.body.dataset = d; };
  globalThis.__setOpener = (kind) => {
    if (kind === "none") __opener = null;
    else if (kind === "closed") __opener = {
      closed: true,
      postMessage() { throw new Error("a closed opener must not be posted"); },
    };
    else if (kind === "ok") __opener = {
      closed: false,
      postMessage(data, target) { __posts.push({ data, target }); },
    };
    else if (kind === "throwing") __opener = {
      closed: false,
      postMessage() { throw new Error("boom"); },
    };
    else throw new Error("unknown opener kind: " + kind);
    window.opener = __opener;
  };
})();
`;

async function runScenario(name, spec) {
  const context = {
    console, JSON, Object, Array, String, Number, Boolean, Error, RegExp, Symbol,
    Promise,
  };
  vm.createContext(context);
  try {
    vm.runInContext(SHIM, context, { filename: "shim.js" });
    vm.runInContext(
      `(function(){
         __setDataset(${JSON.stringify(spec.dataset || {})});
         __setOpener(${JSON.stringify(spec.opener || "none")});
       })();`,
      context,
    );
    vm.runInContext(popupSrc, context, { filename: "auth-popup.js" });
    // #342 race fix: the success path is async (150ms cookie-commit tick) —
    // drain the controlled timers + microtasks BEFORE reading facts. Harmless
    // for the synchronous error branch (no timers queued).
    await vm.runInContext("__drainTimers()", context);
    const facts = vm.runInContext(
      "({ statusText: __statusText(), posts: __posts, closeCount: __closeCount() })",
      context,
    );
    return { name, ok: true, result: facts };
  } catch (e) {
    return { name, ok: false, error: String((e && e.stack) || e) };
  }
}

const out = [];
for (const spec of scenarios) out.push(await runScenario(spec.name, spec));
process.stdout.write(JSON.stringify(out, null, 2));
"""

# ---------- app.js scenarios (boot: /auth/me → 401 → landing) ----------


def _resp(status, body):
    return {"status": status, "body": body}


ANON = {"/auth/me": _resp(401, None)}
# The session the popup's callback establishes — the opener's /auth/me probe
# sees this (200) once the cookie is live.
USER_A = {"id": 1, "username": "u", "email": "e@x", "role": "player"}

APP_SCENARIOS = [
    {
        "name": "A1_cta_opens_popup",
        "initialHash": "#/login",
        "responses": dict(ANON),
        "driver": (
            "await __settle(); "
            "const cta = document.querySelector('.landing-cta'); "
            "cta.click(); "
            "globalThis.__result = { "
            "openCalls: __openCalls.map(a => a.slice()), "
            "href: location.href, "
            "landing: document.body.classList.contains('landing-mode') };",
        ),
    },
    {
        "name": "A2_cta_null_falls_back",
        "initialHash": "#/login",
        "responses": dict(ANON),
        "driver": (
            "await __settle(); "
            "__setOpenReturn(null); "
            "document.querySelector('.landing-cta').click(); "
            "globalThis.__result = { href: location.href, "
            "openCalls: __openCalls.length };",
        ),
    },
    {
        "name": "A3_single_message_listener",
        "initialHash": "#/login",
        "responses": dict(ANON),
        "driver": (
            "await __settle(); "
            "const n1 = (__winListeners['message'] || []).length; "
            "__viewAuth(); __viewAuth(); __viewAuth(); "
            "const n2 = (__winListeners['message'] || []).length; "
            "globalThis.__result = { n1, n2 };",
        ),
    },
    {
        # The popup's callback set the session cookie, so the opener's
        # /auth/me probe now sees 200 — the listener confirms the live
        # session before reloading (the contract under test).
        "name": "A4_own_origin_success_reloads",
        "initialHash": "#/login",
        "responses": dict(ANON),
        "driver": (
            "await __settle(); "
            "__setResponse('/auth/me', { status: 200, body: " + json.dumps(USER_A) + " }); "
            "__dispatchMessage({ origin: location.origin, "
            "data: { type: 'lifesim-auth', ok: true } }); "
            "await __settle(); "
            "globalThis.__result = { reloads: __reloadCount() };",
        ),
    },
    {
        # Probes 1-2 see 401 (cookie not live yet), probe 3 sees 200 — the
        # listener's retry loop must still yield exactly one reload.
        "name": "A4b_retry_then_success_reloads",
        "initialHash": "#/login",
        "responses": dict(ANON),
        "driver": (
            "await __settle(); "
            "__dispatchMessage({ origin: location.origin, "
            "data: { type: 'lifesim-auth', ok: true } }); "
            "await __settle(); "
            "__advance(200); "
            "await __settle(); "
            "__setResponse('/auth/me', { status: 200, body: " + json.dumps(USER_A) + " }); "
            "__advance(400); "
            "await __settle(); "
            "globalThis.__result = { reloads: __reloadCount() };",
        ),
    },
    {
        # All three probes see 401 — the listener must still fail closed with
        # exactly one reload (the landing shows the honest state).
        "name": "A4c_all_probes_401_fail_closed_reloads",
        "initialHash": "#/login",
        "responses": dict(ANON),
        "driver": (
            "await __settle(); "
            "__dispatchMessage({ origin: location.origin, "
            "data: { type: 'lifesim-auth', ok: true } }); "
            "await __settle(); "
            "__advance(200); "
            "await __settle(); "
            "__advance(400); "
            "await __settle(); "
            "__advance(600); "
            "await __settle(); "
            "globalThis.__result = { reloads: __reloadCount() };",
        ),
    },
    {
        "name": "A5_bad_messages_no_reload",
        "initialHash": "#/login",
        "responses": dict(ANON),
        "driver": (
            "await __settle(); "
            "__dispatchMessage({ origin: 'http://evil.example', "
            "data: { type: 'lifesim-auth', ok: true } }); "
            "__dispatchMessage({ origin: location.origin, "
            "data: { type: 'other-type', ok: true } }); "
            "__dispatchMessage({ origin: location.origin, "
            "data: { type: 'lifesim-auth', ok: false } }); "
            "__dispatchMessage({ origin: location.origin, "
            "data: { type: 'lifesim-auth', ok: 'true' } }); "
            "__dispatchMessage({ origin: location.origin, data: null }); "
            "__dispatchMessage({ origin: location.origin, "
            "data: 'lifesim-auth' }); "
            "globalThis.__result = { reloads: __reloadCount() };",
        ),
    },
]

# ---------- auth-popup.js scenarios ----------

POPUP_SUCCESS = "Вход выполнен. Вернитесь в игру."
POPUP_ERRORS = {
    "auth_failed": "Ошибка входа, попробуйте ещё раз",
    "state_mismatch": "Сессия входа устарела, попробуйте ещё раз",
    "provider_error": "Провайдер входа недоступен, попробуйте позже",
    "group_denied": "Вход по приглашениям. Если у вас есть приглашение — откройте ссылку из письма",
    "registration_closed": "Регистрация новых игроков закрыта",
    "username_taken": "Имя занято, обратитесь к администратору",
}

POPUP_SCENARIOS = [
    {"name": "P1_success_posts_and_closes", "dataset": {"ok": "true"}, "opener": "ok"},
    {"name": "P2_auth_failed", "dataset": {"error": "auth_failed"}, "opener": "ok"},
    {"name": "P2_state_mismatch", "dataset": {"error": "state_mismatch"}, "opener": "ok"},
    {"name": "P2_provider_error", "dataset": {"error": "provider_error"}, "opener": "ok"},
    {"name": "P2_group_denied", "dataset": {"error": "group_denied"}, "opener": "ok"},
    {"name": "P2_registration_closed", "dataset": {"error": "registration_closed"}, "opener": "ok"},
    {"name": "P2_username_taken", "dataset": {"error": "username_taken"}, "opener": "ok"},
    {"name": "P3_proto_code", "dataset": {"error": "__proto__"}, "opener": "ok"},
    {"name": "P3_evil_code", "dataset": {"error": "evil"}, "opener": "ok"},
    {"name": "P4_missing_opener", "dataset": {"ok": "true"}, "opener": "none"},
    {"name": "P5_closed_opener", "dataset": {"ok": "true"}, "opener": "closed"},
    {"name": "P6_throwing_postmessage", "dataset": {"ok": "true"}, "opener": "throwing"},
]


# ---------- harness runners ----------


def _node():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    return node


def _run_harness(harness_js, harness_name, target_js, scenarios, scen_name):
    os.makedirs(SCRATCH, exist_ok=True)
    harness_path = os.path.join(SCRATCH, harness_name)
    scen_path = os.path.join(SCRATCH, scen_name)
    with open(harness_path, "w", encoding="utf-8") as f:
        f.write(harness_js)
    with open(scen_path, "w", encoding="utf-8") as f:
        json.dump(scenarios, f)
    proc = subprocess.run(
        [_node(), harness_path, target_js, scen_path],
        capture_output=True, text=True, timeout=180,
    )
    assert proc.returncode == 0, (
        f"node harness failed (rc={proc.returncode}):\n{proc.stderr}"
    )
    return {r["name"]: r for r in json.loads(proc.stdout)}


@pytest.fixture(scope="module")
def app_results():
    return _run_harness(
        HARNESS_APP_JS, "m342_app_harness.mjs", APP_JS,
        APP_SCENARIOS, "m342_app_scenarios.json",
    )


@pytest.fixture(scope="module")
def popup_results():
    return _run_harness(
        HARNESS_POPUP_JS, "m342_popup_harness.mjs", POPUP_JS,
        POPUP_SCENARIOS, "m342_popup_scenarios.json",
    )


def _facts(results, name):
    r = results[name]
    assert r.get("ok"), f"{name}: harness error: {r.get('error')}"
    return r["result"]


# ---------- A: app.js — the landing CTA + the popup-completion listener ----------


class TestAppJsPopupFlow:
    def test_a1_cta_opens_popup(self, app_results):
        f = _facts(app_results, "A1_cta_opens_popup")
        assert f["landing"] is True, "a 401 boot must land on the landing"
        assert f["openCalls"] == [
            ["/auth/login?mode=popup", "lifesim-auth", "popup,width=520,height=680"],
        ], (
            "the CTA must open the popup exactly once with the fixed args, "
            f"got {f['openCalls']}"
        )
        assert f["href"] == "http://localhost/", (
            "a successful popup open must not touch location.href, "
            f"got {f['href']!r}"
        )

    def test_a2_cta_null_falls_back(self, app_results):
        f = _facts(app_results, "A2_cta_null_falls_back")
        assert f["openCalls"] == 1, "the CTA must still call window.open once"
        assert f["href"] == "/auth/login", (
            "a null window.open return must fall back to the full-page login, "
            f"got {f['href']!r}"
        )

    def test_a3_single_message_listener(self, app_results):
        f = _facts(app_results, "A3_single_message_listener")
        assert f["n1"] == 1, (
            f"exactly ONE window message listener at module load, got {f['n1']}"
        )
        assert f["n2"] == 1, (
            "repeated viewAuth() renders must not add message listeners, "
            f"got {f['n2']}"
        )

    def test_a4_own_origin_success_reloads(self, app_results):
        f = _facts(app_results, "A4_own_origin_success_reloads")
        assert f["reloads"] == 1, (
            "an own-origin {type:'lifesim-auth', ok:true} message must reload "
            f"exactly once, got {f['reloads']}"
        )

    def test_a4b_retry_then_success_reloads(self, app_results):
        f = _facts(app_results, "A4b_retry_then_success_reloads")
        assert f["reloads"] == 1, (
            "probes 1-2 at 401 then probe 3 at 200 must still reload exactly "
            f"once (the retry loop works), got {f['reloads']}"
        )

    def test_a4c_all_probes_401_fail_closed_reloads(self, app_results):
        f = _facts(app_results, "A4c_all_probes_401_fail_closed_reloads")
        assert f["reloads"] == 1, (
            "three 401 probes must still fail closed with exactly one reload "
            f"(the landing shows the honest state), got {f['reloads']}"
        )

    def test_a5_bad_messages_no_reload(self, app_results):
        f = _facts(app_results, "A5_bad_messages_no_reload")
        assert f["reloads"] == 0, (
            "foreign origin / wrong type / ok:false / ok:'true' / null / string "
            f"payloads must never reload, got {f['reloads']}"
        )


# ---------- P: auth-popup.js — the completion page ----------


class TestAuthPopupJs:
    def test_p1_success_posts_and_closes(self, popup_results):
        f = _facts(popup_results, "P1_success_posts_and_closes")
        assert f["statusText"] == POPUP_SUCCESS, (
            f"the success status text must be the fixed string, got {f['statusText']!r}"
        )
        assert f["posts"] == [
            {"data": {"type": "lifesim-auth", "ok": True}, "target": "http://localhost"},
        ], (
            "success must post exactly {type:'lifesim-auth', ok:true} to "
            f"location.origin, got {f['posts']}"
        )
        assert f["closeCount"] == 1, (
            f"success must window.close() exactly once, got {f['closeCount']}"
        )

    @pytest.mark.parametrize("code", sorted(POPUP_ERRORS))
    def test_p2_allowlisted_codes(self, popup_results, code):
        f = _facts(popup_results, f"P2_{code}")
        assert f["statusText"] == POPUP_ERRORS[code], (
            f"code {code!r} must show its fixed Russian string, got {f['statusText']!r}"
        )
        assert f["posts"] == [], f"an error page must never postMessage, got {f['posts']}"
        assert f["closeCount"] == 0, (
            f"an error page must never window.close(), got {f['closeCount']}"
        )

    @pytest.mark.parametrize(
        ("code", "scenario"),
        [("__proto__", "P3_proto_code"), ("evil", "P3_evil_code")],
    )
    def test_p3_unknown_codes_generic(self, popup_results, code, scenario):
        f = _facts(popup_results, scenario)
        assert f["statusText"] == POPUP_ERRORS["auth_failed"], (
            f"unknown code {code!r} must fall back to the generic auth_failed "
            f"text, got {f['statusText']!r}"
        )
        assert f["posts"] == [], f"an error page must never postMessage, got {f['posts']}"
        assert f["closeCount"] == 0, (
            f"an error page must never window.close(), got {f['closeCount']}"
        )

    def test_p4_missing_opener(self, popup_results):
        f = _facts(popup_results, "P4_missing_opener")
        assert f["statusText"] == POPUP_SUCCESS, (
            "a missing opener must still show the success text (the fixed "
            f"return link stays usable), got {f['statusText']!r}"
        )
        assert f["posts"] == [], "a missing opener must not be posted to"
        assert f["closeCount"] == 0, "a missing opener must not window.close()"

    def test_p5_closed_opener(self, popup_results):
        f = _facts(popup_results, "P5_closed_opener")
        assert f["statusText"] == POPUP_SUCCESS, (
            "a closed opener must still show the success text, "
            f"got {f['statusText']!r}"
        )
        assert f["posts"] == [], "a closed opener must not be posted to"
        assert f["closeCount"] == 0, "a closed opener must not window.close()"

    def test_p6_throwing_postmessage(self, popup_results):
        # _facts already proves the IIFE did not throw (harness ok:true).
        f = _facts(popup_results, "P6_throwing_postmessage")
        assert f["posts"] == [], "a throwing postMessage must not be captured"
        assert f["closeCount"] == 0, (
            "a throwing postMessage must not window.close() (the catch swallows "
            f"it), got {f['closeCount']}"
        )


# ---------- static serving: the completion page's script ----------


@pytest.fixture
def full_env(tmp_path):
    """create_app() with the real default.yaml (the m309 full_env pattern)."""
    settings = load_config("config/default.yaml")
    settings = settings.model_copy(update={
        "persistence": settings.persistence.model_copy(
            update={"db_path": str(tmp_path / "w.db")}),
    })
    engine = create_engine_factory(settings)
    bootstrap(engine, settings, seed=42)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app(settings, factory)
    yield SimpleNamespace(settings=settings, app=app, factory=factory, engine=engine)
    engine.dispose()


class TestStaticServing:
    def test_auth_popup_js_served(self, full_env):
        client = TestClient(full_env.app)
        r = client.get("/static/auth-popup.js")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/javascript")
        assert "lifesim-auth" in r.text, "the served file must be the real source"
