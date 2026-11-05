"""#269 W1 — shared player shell: behavioral tests for the cold-entry world
clock bootstrap.

Runs the REAL production ``backend/app/web/app.js`` (boot included) inside a
Node ``vm`` context using the embedded stdlib harness below. The harness only
provides the browser surface (a minimal DOM, fake timers, and a controlled
network boundary); it does NOT mock or reimplement ``route()``, ``api()``,
``topbar()`` or the world-bootstrap logic. Every assertion is on an
observable fact: the fetch log (URLs/counts), the rendered ``#clock`` text,
the ``S`` state, and the WebSocket / poll-interval counts.

Approved behavior under test (derived from the work order, not the code):
- a cold, valid, non-world route boot (authenticated + character present)
  requests ``/world`` exactly once, derives the clock from the server, and
  starts NO feed / WS / poll;
- the topbar is a pure renderer (no fetch);
- anonymous (401) and no-character (creator) get NO ``/world`` before the
  auth + character gates;
- world / location / unknown-fallback cold routes do not double-fetch;
- a pending non-world bootstrap handed off to a world/location route must
  coalesce the GET rather than issue two;
- a reject does not block the view and leaves an honest unavailable mark;
  a later route retries;
- stale success / failure / 401 after logout, same-id relogin, or character
  change is ignored and never deauths the new session or overwrites the
  current world; a CURRENT bootstrap 401 must still deauth / fail-closed;
- the cached world must not survive a logout/relogin as an old-context clock;
- an empty or malformed 200 ``/world`` payload is a failure (honest
  unavailable), never a fake NaN time or a permanent ellipsis.
"""
import json
import os
import subprocess

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
APP_JS = os.path.join(ROOT, "backend/app/web/app.js")
SCRATCH = "/tmp/opencode/lifesim-backlog-waves/testing"

# The Node harness (verbatim). It is infrastructure only: a minimal DOM /
# timer / fetch shim plus a fact collector. No product behavior lives here.
HARNESS_JS = r"""
// #269 W1 behavioral harness — runs the REAL production app.js in a Node vm
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
const EXPOSE = "\n;globalThis.__S = S; globalThis.__app = app; globalThis.__routes = routes;\n";

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
})();
`;

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

# dayTime(1500) == "День 2, 01:00"; dayTime(3000) == "День 3, 02:00".
WORLD_A = {"game_timestamp": 1500, "day": 2}
WORLD_B = {"game_timestamp": 3000, "day": 3}
CLOCK_A = "День 2, 01:00"
CLOCK_B = "День 3, 02:00"
UNAVAIL = "н/д"
ELLIPSIS = "…"

USER_A = {"id": 1, "username": "u", "email": "e@x", "role": "player", "sessionTag": "A"}
USER_B = {"id": 1, "username": "u", "email": "e@x", "role": "player", "sessionTag": "B"}
BYUSER = [{"id": 1, "name": "P", "location_id": None}]
CHAR1 = {
    "id": 1, "name": "P", "location_id": 7, "needs": {},
    "control_mode": "MANUAL", "is_owner": False, "money": 100,
}
LOC7 = {"id": 7, "name": "Settlement", "type": "settlement", "x": None, "y": None, "occupants": []}


def _resp(status, body, **extra):
    d = {"status": status, "body": body}
    d.update(extra)
    return d


def _base_people():
    return {
        "/auth/me": _resp(200, USER_A),
        "/characters/by-user/1": _resp(200, BYUSER),
        "/characters/1": _resp(200, CHAR1),
        "/characters/1/relationships": _resp(200, []),
    }


def _tasks_extra():
    return {
        "/characters/1/tasks": _resp(200, {"active": [], "planned": []}),
        "/characters/1/goals": _resp(200, []),
        "/characters/1/desire/opportunities": _resp(200, {"opportunities": [], "desire": None}),
        "/locations": _resp(200, [LOC7]),
    }


SCENARIOS = [
    {
        "name": "S1_cold_people",
        "initialHash": "#/people",
        "responses": dict(_base_people(), **{"/world": _resp(200, WORLD_A)}),
        "driver": "await __settle(); globalThis.__result = __facts();",
    },
    {
        "name": "S2_reject_people",
        "initialHash": "#/people",
        "responses": dict(_base_people(), **{"/world": _resp(500, {"detail": "boom"})}),
        "driver": "await __settle(); globalThis.__result = __facts();",
    },
    {
        "name": "S3_retry_after_reject",
        "initialHash": "#/people",
        "responses": {**_base_people(), **_tasks_extra(), "/world": _resp(500, {"detail": "boom"})},
        "driver": (
            "await __settle(); const afterReject = __facts(); "
            "__setResponse('/world', {status:200, body:" + json.dumps(WORLD_A) + "}); "
            "__setHash('#/tasks'); await __settle(); "
            "globalThis.__result = {afterReject, after: __facts()};"
        ),
    },
    {
        "name": "S4_singleflight",
        "initialHash": "#/people",
        "responses": {
            **_base_people(), **_tasks_extra(),
            "/world": _resp(200, WORLD_A, __hold=True),
        },
        "driver": (
            "await __settle(); const inFlight1 = __net.log.filter(e=>e.url==='/world').length; "
            "__setHash('#/tasks'); await __settle(); "
            "const inFlight2 = __net.log.filter(e=>e.url==='/world').length; "
            "__release('/world', {status:200, body:" + json.dumps(WORLD_A) +
            "}); await __settle(); "
            "globalThis.__result = {inFlight1, inFlight2, after: __facts()};"
        ),
    },
    {
        "name": "S5_cache",
        "initialHash": "#/people",
        "responses": {**_base_people(), **_tasks_extra(), "/world": _resp(200, WORLD_A)},
        "driver": (
            "await __settle(); const afterBoot = __facts(); "
            "__setHash('#/tasks'); await __settle(); "
            "globalThis.__result = {afterBoot, after: __facts()};"
        ),
    },
    {
        "name": "S6_topbar_pure",
        "initialHash": "#/people",
        "responses": dict(_base_people(), **{"/world": _resp(200, WORLD_A)}),
        "driver": (
            "await __settle(); const before = __net.log.length; "
            "__call('topbar', '#/people'); const after = __net.log.length; "
            "globalThis.__result = {delta: after - before};"
        ),
    },
    {
        "name": "S7_anon_401",
        "initialHash": "#/people",
        "responses": {"/auth/me": _resp(401, None)},
        "driver": "await __settle(); globalThis.__result = __facts();",
    },
    {
        "name": "S8_no_character",
        "initialHash": "#/people",
        "responses": {
            "/auth/me": _resp(200, USER_A),
            "/characters/by-user/1": _resp(200, []),
        },
        "driver": "await __settle(); globalThis.__result = __facts();",
    },
    {
        "name": "S9_world_cold",
        "initialHash": "#/world",
        "responses": dict(_base_people(), **{
            "/world": _resp(200, WORLD_A),
            "/weather": _resp(200, {"enabled": False}),
            "/locations": _resp(200, [LOC7]),
        }),
        "driver": "await __settle(); globalThis.__result = __facts();",
    },
    {
        "name": "S10_location_cold",
        "initialHash": "#/location/7",
        "responses": dict(_base_people(), **{
            "/world": _resp(200, WORLD_A),
            "/weather": _resp(200, {"enabled": False}),
            "/locations": _resp(200, [LOC7]),
        }),
        "driver": "await __settle(); globalThis.__result = __facts();",
    },
    {
        "name": "S11_unknown_cold",
        "initialHash": "#/nonexistent",
        "responses": dict(_base_people(), **{
            "/world": _resp(200, WORLD_A),
            "/weather": _resp(200, {"enabled": False}),
            "/locations": _resp(200, [LOC7]),
        }),
        "driver": "await __settle(); globalThis.__result = __facts();",
    },
    {
        "name": "S12_stale_success_after_deauth",
        "initialHash": "#/people",
        "responses": dict(_base_people(), **{"/world": _resp(200, WORLD_A, __hold=True)}),
        "driver": (
            "await __settle(); "
            "__setResponse('/auth/me', {status:401, body:null}); "
            "__setResponse('/characters/1/relationships', {status:401, body:null}); "
            "__call('route'); await __settle(); const afterDeauth = __facts(); "
            "__release('/world', {status:200, body:" + json.dumps(WORLD_A) +
            "}); await __settle(); "
            "globalThis.__result = {afterDeauth, after: __facts()};"
        ),
    },
    {
        "name": "S13_current_bootstrap_401",
        "initialHash": "#/people",
        "responses": dict(_base_people(), **{"/world": _resp(401, None)}),
        "driver": (
            "await __settle(); "
            "__setResponse('/auth/me', {status:401, body:null}); "
            "await __settle(); globalThis.__result = __facts();"
        ),
    },
    {
        "name": "S14_stale_401_after_relogin",
        "initialHash": "#/people",
        "responses": dict(_base_people(), **{"/world": _resp(200, WORLD_A, __hold=True)}),
        "driver": (
            "await __settle(); __S.user = null; __S.authProbed = false; "
            "__setResponse('/auth/me', {status:200, body:" + json.dumps(USER_B) + "}); "
            "__call('route'); await __settle(); const afterRelogin = __facts(); "
            "__release('/world', {status:401, body:null}); await __settle(); "
            "const afterStale401 = __facts(); "
            "__release('/world', {status:200, body:" + json.dumps(WORLD_A) +
            "}); await __settle(); "
            "globalThis.__result = {afterRelogin, afterStale401, after: __facts()};"
        ),
    },
    {
        "name": "S15_char_change",
        "initialHash": "#/people",
        "responses": dict(_base_people(), **{"/world": _resp(200, WORLD_A, __hold=True)}),
        "driver": (
            "await __settle(); __S.character = {id: 2, name: 'P2', location_id: 7}; "
            "__release('/world', {status:200, body:" + json.dumps(WORLD_A) +
            "}); await __settle(); "
            "globalThis.__result = __facts();"
        ),
    },
    {
        "name": "S16_cached_world_after_relogin",
        "initialHash": "#/people",
        "responses": dict(_base_people(), **{"/world": _resp(200, WORLD_A)}),
        "driver": (
            "await __settle(); const afterBoot = __facts(); "
            "__setResponse('/auth/me', {status:401, body:null}); "
            "__setResponse('/characters/1/relationships', {status:401, body:null}); "
            "__call('route'); await __settle(); const afterDeauth = __facts(); "
            "__S.user = null; __S.character = null; __S.authProbed = false; "
            "__setResponse('/auth/me', {status:200, body:" + json.dumps(USER_B) + "}); "
            "__setResponse('/characters/1/relationships', {status:200, body:[]}); "
            "__setResponse('/world', {status:200, body:" + json.dumps(WORLD_B) + "}); "
            "__setHash('#/people'); await __settle(); "
            "globalThis.__result = {afterBoot, afterDeauth, after: __facts()};"
        ),
    },
    {
        "name": "S17_world_handoff_coalesce",
        "initialHash": "#/people",
        "responses": dict(_base_people(), **{
            "/world": _resp(200, WORLD_A, __hold=True),
            "/weather": _resp(200, {"enabled": False}),
            "/locations": _resp(200, [LOC7]),
        }),
        "driver": (
            "await __settle(); const before = __net.log.filter(e=>e.url==='/world').length; "
            "__setHash('#/world'); await __settle(); "
            "const after = __net.log.filter(e=>e.url==='/world').length; "
            "let guard=0; while (__release('/world', {status:200, body:" +
            json.dumps(WORLD_A) +
            "}) && guard++<5) { await __settle(); } "
            "globalThis.__result = {before, after, facts: __facts()};"
        ),
    },
    {
        "name": "S18_location_handoff_coalesce",
        "initialHash": "#/people",
        "responses": dict(_base_people(), **{
            "/world": _resp(200, WORLD_A, __hold=True),
            "/weather": _resp(200, {"enabled": False}),
            "/locations": _resp(200, [LOC7]),
        }),
        "driver": (
            "await __settle(); const before = __net.log.filter(e=>e.url==='/world').length; "
            "__setHash('#/location/7'); await __settle(); "
            "const after = __net.log.filter(e=>e.url==='/world').length; "
            "let guard=0; while (__release('/world', {status:200, body:" +
            json.dumps(WORLD_A) +
            "}) && guard++<5) { await __settle(); } "
            "globalThis.__result = {before, after, facts: __facts()};"
        ),
    },
    {
        "name": "S19_empty_200_world",
        "initialHash": "#/people",
        "responses": dict(_base_people(), **{"/world": _resp(200, None)}),
        "driver": "await __settle(); globalThis.__result = __facts();",
    },
    {
        "name": "S20_malformed_200_world",
        "initialHash": "#/people",
        "responses": dict(_base_people(), **{"/world": _resp(200, {})}),
        "driver": "await __settle(); globalThis.__result = __facts();",
    },
    {
        "name": "S21_stale_flight_same_context",
        "initialHash": "#/people",
        "responses": {
            **_base_people(),
            "/characters/2/relationships": _resp(200, []),
            "/world": _resp(200, WORLD_A, __hold=True),
        },
        "driver": (
            "await __settle(); "
            "__S.character = {id: 2, name: 'P2', location_id: 7}; "
            "__call('route'); await __settle(); "
            "__S.character = {id: 1, name: 'P', location_id: 7}; "
            "__call('route'); await __settle(); "
            "const beforeOld = __net.log.filter(e=>e.url==='/world').length; "
            "__release('/world', {status:200, body:" + json.dumps(WORLD_A) +
            "}); await __settle(); const afterOld = __facts(); "
            "__release('/world', {status:200, body:" + json.dumps(WORLD_A) +
            "}); await __settle(); "
            "__release('/world', {status:200, body:" + json.dumps(WORLD_B) +
            "}); await __settle(); "
            "globalThis.__result = {beforeOld, afterOld, after: __facts()};"
        ),
    },
    {
        "name": "S22_stale_joiner_writes_world_after_deauth",
        "initialHash": "#/people",
        "responses": {
            **_base_people(),
            "/world": _resp(200, WORLD_A, __hold=True),
            "/weather": _resp(200, {"enabled": False}),
            "/locations": _resp(200, [LOC7]),
        },
        "driver": (
            "await __settle(); "
            "__setHash('#/world'); await __settle(); "
            "__setResponse('/auth/me', {status:401, body:null}); "
            "__setResponse('/characters/1/relationships', {status:401, body:null}); "
            "__setHash('#/people'); await __settle(); const afterDeauth = __facts(); "
            "__release('/world', {status:200, body:" + json.dumps(WORLD_A) +
            "}); await __settle(); "
            "globalThis.__result = {afterDeauth, after: __facts()};"
        ),
    },
    {
        "name": "S23_stale_world_load_publishes_over_newer",
        "initialHash": "#/world",
        "responses": {
            **_base_people(),
            "/world": _resp(200, WORLD_A),
            "/weather": _resp(200, {"enabled": False}, __hold=True),
            "/locations": _resp(200, [LOC7]),
        },
        "driver": (
            "await __settle(); "
            "__setResponse('/world', {status:200, body:" + json.dumps(WORLD_B) +
            "}); "
            "__setResponse('/weather', {status:200, body:{enabled:false}}); "
            "__call('route'); await __settle(); const afterB = __facts(); "
            "__hold('/world'); "
            "__release('/weather', {status:200, body:{enabled:false}}); "
            "await __settle(); "
            "globalThis.__result = {afterB, after: __facts()};"
        ),
    },
]


def _run_harness():
    os.makedirs(SCRATCH, exist_ok=True)
    harness_path = os.path.join(SCRATCH, "m269_harness.mjs")
    scen_path = os.path.join(SCRATCH, "m269_scenarios.json")
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


# ---------- S1: cold non-world boot — the core RED ----------

def test_s1_cold_nonworld_boot(results):
    f = _facts(results, "S1_cold_people")
    assert f["worldFetches"] == 1, (
        f"cold non-world boot must request /world exactly once, got {f['worldFetches']}"
    )
    assert f["clockText"] == CLOCK_A, (
        f"clock must derive from the server timestamp, got {f['clockText']!r}"
    )
    assert f["wsCount"] == 0, "no WebSocket/feed on a non-world route"
    assert f["intervalCount"] == 0, "no poll interval on a non-world route"
    assert f["eventsFetches"] == 0, "no events poll on a non-world route"
    assert f["S_world"] is not None, "S.world must be hydrated from the bootstrap"
    assert f["hash"] == "#/people"
    assert f["hasPeopleEmpty"], "the requested view must render (not be blocked)"


# ---------- S2: reject does not block; honest unavailable mark ----------

def test_s2_reject_does_not_block(results):
    f = _facts(results, "S2_reject_people")
    assert f["worldFetches"] == 1
    assert f["hasPeopleEmpty"], "a /world reject must not block the requested view"
    assert f["clockText"] == UNAVAIL, (
        f"a failed bootstrap must show an honest unavailable mark, got {f['clockText']!r}"
    )
    assert f["S_world"] is None, "a failed bootstrap must not set S.world"


# ---------- S3: retry on a later route ----------

def test_s3_retry_on_later_route(results):
    r = _facts(results, "S3_retry_after_reject")
    after_reject, after = r["afterReject"], r["after"]
    assert after_reject["clockText"] == UNAVAIL, "first failure leaves the unavailable mark"
    assert after["worldFetches"] == after_reject["worldFetches"] + 1, (
        "a later route must retry the bootstrap (exactly one new /world)"
    )
    assert after["clockText"] == CLOCK_A, "the retry must hydrate the clock from the server"
    assert after["S_world"] is not None


# ---------- S4: single-flight coalescing within the same context ----------

def test_s4_singleflight_coalescing(results):
    r = _facts(results, "S4_singleflight")
    assert r["inFlight1"] == 1, "the bootstrap is in flight after boot"
    assert r["inFlight2"] == 1, (
        "a second dispatch while the bootstrap is in flight must coalesce "
        f"(no second /world), got {r['inFlight2']}"
    )
    assert r["after"]["worldFetches"] == 1
    assert r["after"]["clockText"] == CLOCK_A


# ---------- S5: cached world — no refetch on navigation ----------

def test_s5_cache_no_refetch(results):
    r = _facts(results, "S5_cache")
    boot, after = r["afterBoot"], r["after"]
    assert boot["S_world"] is not None, "boot must hydrate S.world"
    assert after["worldFetches"] == boot["worldFetches"], (
        "navigating with a cached S.world must not refetch /world"
    )
    assert after["clockText"] == CLOCK_A


# ---------- S6: topbar is a pure renderer ----------

def test_s6_topbar_pure(results):
    r = _facts(results, "S6_topbar_pure")
    assert r["delta"] == 0, "topbar() must not issue any fetch"


# ---------- S7: anonymous (401) — no /world before the auth gate ----------

def test_s7_anon_no_world_before_auth(results):
    f = _facts(results, "S7_anon_401")
    assert f["worldFetches"] == 0, "an anonymous user must not fetch /world"
    assert f["hash"] == "#/login"
    assert f["S_userTag"] is None


# ---------- S8: no character — no /world before the character gate ----------

def test_s8_no_character_no_world(results):
    f = _facts(results, "S8_no_character")
    assert f["worldFetches"] == 0, "a user without a character must not fetch /world"
    assert f["hash"] == "#/create"
    assert f["S_charId"] is None


# ---------- S9/S10/S11: existing routes do not double-fetch ----------

def test_s9_world_cold_no_double_fetch(results):
    f = _facts(results, "S9_world_cold")
    # Existing contract: viewWorld's own /world + the immediate pollEvents
    # refresh. The bootstrap must not add a third.
    assert f["worldFetches"] == 2, (
        f"#/world cold boot must keep its existing /world count (2), got {f['worldFetches']}"
    )
    assert f["clockText"] == CLOCK_A


def test_s10_location_cold_no_double_fetch(results):
    f = _facts(results, "S10_location_cold")
    # Existing contract: viewLocation's single /world. No second from bootstrap.
    assert f["worldFetches"] == 1, (
        f"#/location cold boot must keep its existing /world count (1), got {f['worldFetches']}"
    )
    assert f["clockText"] == CLOCK_A


def test_s11_unknown_cold_no_double_fetch(results):
    f = _facts(results, "S11_unknown_cold")
    # Existing contract: the unknown-hash fallback dispatches viewWorld (which
    # normalizes the hash and re-dispatches once) + pollEvents. The bootstrap
    # must not add beyond the existing count (3).
    assert f["worldFetches"] == 3, (
        f"unknown-hash cold boot must keep its existing /world count (3), got {f['worldFetches']}"
    )
    assert f["clockText"] == CLOCK_A


# ---------- S12: stale success after deauth is ignored ----------

def test_s12_stale_success_after_deauth(results):
    r = _facts(results, "S12_stale_success_after_deauth")
    deauth, after = r["afterDeauth"], r["after"]
    assert deauth["S_userTag"] is None and deauth["hash"] == "#/login", (
        "the 401 deauth must land on the login view"
    )
    assert after["S_world"] is None, (
        "a stale bootstrap success after deauth must not restore S.world"
    )
    assert after["S_userTag"] is None, "the stale success must not mutate current auth"
    assert after["hash"] == "#/login", "the stale success must not restore protected UI"


# ---------- S13: a CURRENT bootstrap 401 must deauth / fail-closed ----------

def test_s13_current_bootstrap_401_deauths(results):
    f = _facts(results, "S13_current_bootstrap_401")
    assert f["S_userTag"] is None, (
        "a 401 on the CURRENT session's bootstrap must deauth (fail-closed), "
        f"but the session is still {f['S_userTag']!r}"
    )
    assert f["hash"] == "#/login", (
        "a 401 on the current bootstrap must reroute to login, got "
        f"{f['hash']!r}"
    )


# ---------- S14: stale 401 after same-id relogin must not deauth ----------

def test_s14_stale_401_after_relogin(results):
    r = _facts(results, "S14_stale_401_after_relogin")
    relogin, stale, after = r["afterRelogin"], r["afterStale401"], r["after"]
    assert relogin["S_userTag"] == "B", "relogin must establish the new session"
    assert stale["S_userTag"] == "B", (
        "a stale 401 from the OLD session must not deauth the NEW session"
    )
    assert stale["S_world"] is None, "the stale 401 must not set S.world"
    assert after["S_userTag"] == "B", "the new session survives"
    assert after["S_world"] is not None, "the new session's own bootstrap hydrates"
    assert after["clockText"] == CLOCK_A


# ---------- S15: stale success after a character change is ignored ----------

def test_s15_char_change_stale_ignored(results):
    f = _facts(results, "S15_char_change")
    assert f["S_charId"] == 2, "the character changed"
    assert f["S_world"] is None, (
        "a stale bootstrap success from the previous character must not set S.world"
    )
    assert f["clockText"] != CLOCK_A, "the stale success must not restore the old clock"


# ---------- S16: cached world must not survive logout/relogin ----------

def test_s16_cached_world_after_relogin(results):
    r = _facts(results, "S16_cached_world_after_relogin")
    boot, deauth, after = r["afterBoot"], r["afterDeauth"], r["after"]
    assert boot["clockText"] == CLOCK_A and boot["S_world"] is not None
    assert deauth["S_userTag"] is None and deauth["hash"] == "#/login"
    assert after["S_userTag"] == "B", "relogin establishes the new session"
    assert after["worldFetches"] > deauth["worldFetches"], (
        "after logout/relogin the world must be re-hydrated for the new session "
        f"(no fresh /world: {after['worldFetches']} == {deauth['worldFetches']})"
    )
    assert after["clockText"] == CLOCK_B, (
        f"the clock must not show the OLD context's time, got {after['clockText']!r}"
    )
    assert after["S_world"] is not None and after["S_world"].get("game_timestamp") == 3000, (
        "S.world must be the NEW session's world, not the cached old one"
    )


# ---------- S17/S18: pending bootstrap handed off to world/location coalesces ----------

def test_s17_world_handoff_coalesces(results):
    r = _facts(results, "S17_world_handoff_coalesce")
    assert r["before"] == 1, "the bootstrap is in flight before the handoff"
    assert r["after"] == 1, (
        "handing off to #/world while the bootstrap is pending must coalesce the "
        f"GET (one /world), got {r['after']}"
    )
    facts = r["facts"]
    assert facts["S_world"] is not None, (
        "the coalesced bootstrap must deliver S.world to the handed-off view"
    )
    assert facts["clockText"] == CLOCK_A, (
        "the coalesced bootstrap must deliver the clock, "
        f"got {facts['clockText']!r}"
    )


def test_s18_location_handoff_coalesces(results):
    r = _facts(results, "S18_location_handoff_coalesce")
    assert r["before"] == 1, "the bootstrap is in flight before the handoff"
    assert r["after"] == 1, (
        "handing off to #/location while the bootstrap is pending must coalesce "
        f"the GET (one /world), got {r['after']}"
    )
    facts = r["facts"]
    assert facts["S_world"] is not None, (
        "the coalesced bootstrap must deliver S.world to the handed-off view"
    )
    assert facts["clockText"] == CLOCK_A, (
        "the coalesced bootstrap must deliver the clock, "
        f"got {facts['clockText']!r}"
    )


# ---------- S19/S20: empty / malformed 200 world is a failure ----------

def test_s19_empty_200_world_is_failure(results):
    f = _facts(results, "S19_empty_200_world")
    assert f["S_world"] is None, "an empty 200 /world must not set S.world"
    assert f["clockText"] == UNAVAIL, (
        f"an empty 200 /world must show an honest unavailable mark, "
        f"got {f['clockText']!r} (permanent ellipsis or fake time is a defect)"
    )


def test_s20_malformed_200_world_is_failure(results):
    f = _facts(results, "S20_malformed_200_world")
    assert f["S_world"] in (None, {}), "a malformed 200 /world must not be trusted"
    assert "NaN" not in (f["clockText"] or ""), (
        f"a malformed 200 /world must not render a fake NaN time, got {f['clockText']!r}"
    )
    assert f["clockText"] == UNAVAIL, (
        f"a malformed 200 /world must show an honest unavailable mark, "
        f"got {f['clockText']!r}"
    )


# ---------- S21: a stale same-context flight must not publish over a newer one ----------

def test_s21_stale_flight_same_context(results):
    r = _facts(results, "S21_stale_flight_same_context")
    assert r["beforeOld"] == 3, (
        "three bootstrap flights must be in flight (char1, char2, char1), "
        f"got {r['beforeOld']} /world GETs"
    )
    old = r["afterOld"]
    assert old["S_world"] is None, (
        "a stale completion whose context matches again must not publish its "
        f"world over the newer in-flight bootstrap, got {old['S_world']!r}"
    )
    assert old["clockText"] != CLOCK_A, (
        "the stale completion must not restore the old clock, "
        f"got {old['clockText']!r}"
    )
    after = r["after"]
    assert after["S_world"] is not None and after["S_world"].get("game_timestamp") == 3000, (
        "the LATEST flight's world must win, got "
        f"{after['S_world']!r}"
    )
    assert after["clockText"] == CLOCK_B, (
        f"the latest server clock must win, got {after['clockText']!r}"
    )


# ---------- S22: a joined viewWorld must not write S.world after deauth ----------

def test_s22_stale_joiner_writes_world_after_deauth(results):
    r = _facts(results, "S22_stale_joiner_writes_world_after_deauth")
    deauth, after = r["afterDeauth"], r["after"]
    assert deauth["S_userTag"] is None and deauth["hash"] == "#/login", (
        "the 401 deauth must land on the login view"
    )
    assert deauth["S_world"] is None, "the deauth must clear the cached world"
    assert after["S_userTag"] is None, "the session must stay deauthenticated"
    assert after["hash"] == "#/login", "the login route must not be left"
    assert after["S_world"] is None, (
        "a joined viewWorld that resolves after deauth must not repopulate "
        f"S.world with the old session's world, got {after['S_world']!r}"
    )


# ---------- S23: a stale world load must not publish over a newer one ----------

def test_s23_stale_world_load_publishes_over_newer(results):
    r = _facts(results, "S23_stale_world_load_publishes_over_newer")
    after_b, after = r["afterB"], r["after"]
    assert after_b["S_world"] is not None and after_b["S_world"].get("game_timestamp") == 3000, (
        "the second (newer) load must publish before the old load's weather "
        f"releases, got {after_b['S_world']!r}"
    )
    assert after_b["clockText"] == CLOCK_B, (
        f"the newer load's clock must be delivered, got {after_b['clockText']!r}"
    )
    assert after["S_world"] is not None and after["S_world"].get("game_timestamp") == 3000, (
        "the stale older load must not republish its world over the newer one, "
        f"got {after['S_world']!r}"
    )
    assert after["clockText"] == CLOCK_B, (
        "the stale older load must not replace the newer clock, "
        f"got {after['clockText']!r}"
    )
