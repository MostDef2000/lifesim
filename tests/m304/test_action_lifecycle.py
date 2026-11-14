"""#304 — action lifecycle: «команда принята ≠ действие выполнено».

Behavioral tests for the module-local task presentation adapter of the
action lifecycle in the REAL production ``backend/app/web/app.js`` (boot
included), run inside a Node ``vm`` context using the embedded stdlib
harness below. The harness is an OWN copy of the m311 vm-harness pattern
(m312/m303 lineage) — the same minimal DOM / fake-timer / controlled-
network / fake-WebSocket shim — extended additively with the #304
surface:

- task status-host facts (World «Сейчас», Place «Задача:», Tasks
  active/history boxes) + the submission census (POST /actions,
  POST /scene/act, GET /actions/{id}, GET /characters/{id}/tasks);
- a typeof-guarded ``__taskPresentation()`` / ``__currentTaskId()`` /
  ``__actionSubmission()`` accessor (``null`` on a base app.js that
  lacks the #304 adapter — RED authenticity);
- fetch deferrals (``__hold``/``__release``) on the four distinct
  submission/detail URLs.

The #304 adapter does NOT exist in app.js yet. The lifecycle scenarios
(01-16) MUST fail on base; the behavior-preservation scenarios (17-19)
MUST pass on base (they guard the refactor).
"""

import json
import os
import subprocess

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
APP_JS = os.path.join(ROOT, "backend/app/web/app.js")
SCRATCH = "/tmp/opencode/m304-testing"


HARNESS_JS = r"""
// #311 — session-owned world feed controller: RED behavioral harness.
// Real production app.js in a Node vm with fake DOM/fetch/timers/WebSocket.
// OWN copy of the m312 vm-harness pattern (m303 lineage) extended with:
//   - a rich WebSocket fake (manual open/message/close/error dispatch on
//     S.ws + connect/frame/close logs);
//   - feed-focused __facts() (stream state, transport resources, view-local
//     quiet indicators, network census);
//   - a typeof-guarded __feedController() accessor (null on base app.js —
//     RED authenticity: the controller does not exist yet).
import fs from "node:fs";
import vm from "vm";

const appJsPath = process.argv[2];
const scenariosPath = process.argv[3];
const appSrc = fs.readFileSync(appJsPath, "utf-8");
const scenarios = JSON.parse(fs.readFileSync(scenariosPath, "utf-8"));

// unhandled rejections are a first-class observable.
const __unhandled = [];
process.on("unhandledRejection", (reason) => {
  __unhandled.push(String((reason && reason.message) || reason));
});

// Expose the lexical top-level bindings to the driver. Appended to the
// in-memory source only — the production file is untouched. The #304
// task-presentation bindings are typeof-guarded so the harness stays
// loadable against a base app.js that lacks the #304 adapter (RED
// authenticity: the adapter does not exist yet). The drive functions
// (doAction/moveTo/submitSceneAct/route/viewWorld/viewLocation/viewTasks)
// are top-level function declarations, reachable via __call.
const EXPOSE = [
  "\n;globalThis.__S = S; globalThis.__app = app; globalThis.__routes = routes;",
  "globalThis.__taskPresentation = () => " +
  "(typeof taskPresentation !== 'undefined' ? taskPresentation : null);",
  "globalThis.__currentTaskId = () => " +
  "(typeof currentTaskId !== 'undefined' ? currentTaskId : null);",
  "globalThis.__actionSubmission = () => " +
  "(typeof actionSubmission !== 'undefined' ? actionSubmission : null);",
  "globalThis.__latestTaskSnapshot = () => " +
  "(typeof latestTaskSnapshot !== 'undefined' ? latestTaskSnapshot : null);",
  "\n",
].join("");

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
    // #311: isConnected — the app's stale-response guards check the host
    // node is still mounted; a node is connected iff its parent chain
    // reaches the document element.
    Object.defineProperty(n, "isConnected", {
      get() {
        let c = n;
        while (c) { if (c === documentElement) return true; c = c._parent; }
        return false;
      },
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
  // #311: rich WebSocket fake — manual dispatch + connect/frame/close
  // logs. The driver fires onopen/onmessage/onclose/onerror on the
  // CURRENT socket (S.ws) via __wsOpen/__wsMessage/__wsClose/__wsError;
  // the app's own close() (stopFeed/teardown) only flips readyState.
  const __wsLog = { connects: [], frames: [], closes: [] };
  let __wsSeq = 0;
  class WebSocket {
    constructor(url) {
      __wsSeq++;
      this.url = url;
      this.readyState = 0;  // CONNECTING
      this.onopen = null;
      this.onmessage = null;
      this.onclose = null;
      this.onerror = null;
      this._seq = __wsSeq;
      __wsLog.connects.push({ seq: this._seq, url });
    }
    send(data) { __wsLog.frames.push({ seq: this._seq, data }); }
    close() {
      if (this.readyState === 3) return;
      this.readyState = 3;
      __wsLog.closes.push({ seq: this._seq });
    }
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
  // #304: action-lifecycle facts — the task status hosts (World/Place/Tasks),
  // the submission census (POST /actions, POST /scene/act), the toast, and
  // the #304 adapter bindings (typeof-guarded — null on base).
  function __facts() {
    const S = globalThis.__S;
    const feedBox = document.getElementById("feed");
    const banner = document.getElementById("conn-banner");
    // #304: the World HUD «Сейчас» task status host (base: .world-now-*).
    const worldNowStatus = document.querySelector(".world-now-status");
    const worldNowType = document.querySelector(".world-now-type");
    const worldNowState = document.querySelector(".world-now-state");
    // #304: the Place view's current-task host (base: .place-task-*).
    const placeTask = document.querySelector(".place-task");
    // #304: the Tasks screen's active/history boxes.
    const tasksActive = document.getElementById("tasks-active");
    const tasksHistory = document.getElementById("tasks-history");
    // #304: the toast (last message).
    const toastEl = document.getElementById("toast");
    // #304: the submission feedback line (Place view) — the sending text
    // while the latch is held, else the last submission-failure caption.
    const submitLine = document.querySelector(".task-submit-line");
    // #304: the submission census — POST /actions and POST /scene/act.
    const postActions = __net.log.filter(
      e => (e.opts.method || "GET").toUpperCase() === "POST"
        && e.url === "/actions").length;
    const postScene = __net.log.filter(
      e => (e.opts.method || "GET").toUpperCase() === "POST"
        && e.url === "/scene/act").length;
    const getActionsDetail = __net.log.filter(
      e => (e.opts.method || "GET").toUpperCase() === "GET"
        && e.url.indexOf("/actions/") === 0
        && e.url.length > "/actions/".length).map(e => e.url);
    const getTasksList = __net.log.filter(
      e => (e.opts.method || "GET").toUpperCase() === "GET"
        && e.url.indexOf("/characters/") === 0
        && e.url.indexOf("/tasks") === e.url.length - 6).length;
    const placeCtx = document.querySelector(".place-context");
    const chatView = document.getElementById("chat-view");
    const stage = document.querySelector(".place-stage");
    const stageHeading = stage
      ? findDescendant(stage, (n) => n.nodeType === 1 && /^H[1-4]$/.test(n.tagName))
      : null;
    // #311: the place quiet line — «В мире произошло…» inside the context
    // panel (contextKids). Absent on base (RED authenticity).
    let placeQuiet = null;
    if (placeCtx) {
      const n = findDescendant(placeCtx, (n) => n.nodeType === 1
        && n.textContent.indexOf("В мире произошло") === 0);
      if (n) placeQuiet = n.textContent;
    }
    // #311: the chat quiet indicator — a muted status node near the heading
    // in #chat-view. Absent on base (RED authenticity).
    let chatQuiet = null;
    if (chatView) {
      const n = findDescendant(chatView, (n) => n.nodeType === 1
        && n.textContent.indexOf("В мире произошло") >= 0);
      if (n) chatQuiet = n.textContent;
    }
    const chatInput = chatView
      ? findDescendant(chatView, (n) => n.tagName === "TEXTAREA" || n.tagName === "INPUT")
      : null;
    // #311: the Events screen's persistent disclosure (m241/#76) — its
    // presence proves the isolated screen rendered its own sample.
    const eventsDisclosure = findDescendant(documentElement,
      (n) => n.nodeType === 1
        && n.textContent.indexOf("События мира: до 100 записей") === 0);
    // #311: feed-owned GETs (pollEventsFeed) vs the bounded historical
    // sample (place panel + Events screen, ?since=0&limit=100).
    const feedGets = __net.log.filter(
      e => (e.opts.method || "GET").toUpperCase() === "GET"
        && e.url.indexOf("/world/events?since=") === 0
        && e.url.indexOf("limit=100") < 0).map(e => e.url);
    const sampleGets = __net.log.filter(
      e => (e.opts.method || "GET").toUpperCase() === "GET"
        && e.url.indexOf("/world/events?since=0&limit=100") === 0).map(e => e.url);
    const worldGets = __net.log.filter(
      e => (e.opts.method || "GET").toUpperCase() === "GET" && e.url === "/world").length;
    const locGets = __net.log.filter(
      e => (e.opts.method || "GET").toUpperCase() === "GET" && e.url === "/locations").length;
    return {
      hash: location.hash,
      feed: (S.feed || []).map((e) => e.id),
      cursor: S.cursor,
      feedInit: S.feedInit,
      feedFetched: S.feedFetched,
      bootCursor: S.bootCursor === Infinity ? null : S.bootCursor,
      wsTries: S.wsTries,
      wsCursorSent: S.wsCursorSent,
      pollBusy: S.pollBusy,
      pollTimer: S.pollTimer != null,
      helloDeadline: S.helloDeadline != null,
      connLost: S.connLost,
      pollFails: S.pollFails,
      ws: S.ws ? { seq: S.ws._seq, readyState: S.ws.readyState } : null,
      wsConnects: __wsLog.connects.length,
      wsFrames: __wsLog.frames.map((f) => f.data),
      wsCloses: __wsLog.closes.length,
      intervals: __timers.intervals.size,
      timeouts: __timers.timeouts.size,
      feedDom: feedBox ? feedBox.textContent : null,
      feedRows: feedBox ? feedBox.querySelectorAll(".ev").length : 0,
      bannerVisible: banner ? !banner.classList.contains("hidden") : false,
      placeQuiet: placeQuiet,
      chatQuiet: chatQuiet,
      chatInputValue: chatInput ? chatInput.value : null,
      chatInputFocused: chatInput ? !!chatInput._focused : null,
      stageHeadingText: stageHeading ? stageHeading.textContent : null,
      hasPlaceShell: !!document.querySelector("main.place-shell"),
      eventsDisclosure: !!eventsDisclosure,
      // #304: truncated — the full app text would blow the 64KB pipe buffer
      // (30 scenarios). No m304 assertion reads beyond this prefix.
      appText: globalThis.__app
        ? String(globalThis.__app.textContent).slice(0, 150) : null,
      feedGets: feedGets,
      sampleGets: sampleGets,
      worldGets: worldGets,
      locGets: locGets,
      user: S.user ? S.user.id : null,
      character: S.character ? S.character.id : null,
      controller: (typeof globalThis.__feedController === "function")
        ? !!globalThis.__feedController() : null,
      // #304: the task status hosts (World/Place/Tasks).
      worldNowStatus: worldNowStatus ? worldNowStatus.textContent : null,
      worldNowType: worldNowType ? worldNowType.textContent : null,
      worldNowState: worldNowState ? worldNowState.textContent : null,
      placeTaskText: placeTask ? placeTask.textContent : null,
      tasksActiveText: tasksActive ? tasksActive.textContent : null,
      tasksHistoryText: tasksHistory ? tasksHistory.textContent : null,
      // #304: the submission census.
      postActions: postActions,
      postScene: postScene,
      getActionsDetail: getActionsDetail,
      getTasksList: getTasksList,
      // #304: the toast (last message).
      toastText: toastEl ? toastEl.textContent : null,
      // #304: the submission feedback line text (Place view).
      submitLineText: submitLine ? submitLine.textContent : null,
      // #304: the app state (travel/scene).
      travel: S.travel ? { state: S.travel.state,
        destName: S.travel.destName || null,
        taskId: S.travel.taskId != null ? S.travel.taskId : null } : null,
      sceneAct: S.sceneAct ? { ok: S.sceneAct.ok,
        task_id: S.sceneAct.task_id != null ? S.sceneAct.task_id : null,
        detail: S.sceneAct.detail || null } : null,
      sceneActDraft: S.sceneActDraft != null ? S.sceneActDraft : null,
      // #304: the adapter bindings (typeof-guarded — null on base).
      hasTaskPresentation: (typeof globalThis.__taskPresentation === "function")
        ? !!globalThis.__taskPresentation() : null,
      currentTaskId: (typeof globalThis.__currentTaskId === "function")
        ? globalThis.__currentTaskId() : null,
    };
  }

  // #311: manual WS dispatch on the CURRENT socket (S.ws).
  function __wsOpen() {
    const ws = globalThis.__S.ws;
    if (!ws) return false;
    ws.readyState = 1;
    if (ws.onopen) ws.onopen();
    return true;
  }
  function __wsMessage(obj) {
    const ws = globalThis.__S.ws;
    if (!ws) return false;
    if (ws.onmessage) ws.onmessage({ data: JSON.stringify(obj) });
    return true;
  }
  function __wsClose() {
    const ws = globalThis.__S.ws;
    if (!ws) return false;
    ws.readyState = 3;
    if (ws.onclose) ws.onclose();
    return true;
  }
  function __wsError() {
    const ws = globalThis.__S.ws;
    if (!ws) return false;
    if (ws.onerror) ws.onerror({});
    return true;
  }
  // #311: fire a specific interval once (re-armed like a real setInterval).
  function __fireInterval(id) {
    const iv = __timers.intervals.get(id);
    if (!iv) return false;
    __timers.intervals.delete(id);
    __timers.intervals.set(id, { fn: iv.fn, at: __timers.now + iv.ms, ms: iv.ms });
    iv.fn();
    return true;
  }
  // #311: the pending timeout id with the given delay (ms), or null.
  function __findTimeout(ms) {
    for (const [id, t] of __timers.timeouts) if (t.ms === ms) return id;
    return null;
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
  globalThis.__wsLog = __wsLog;
  globalThis.__wsOpen = __wsOpen;
  globalThis.__wsMessage = __wsMessage;
  globalThis.__wsClose = __wsClose;
  globalThis.__wsError = __wsError;
  globalThis.__fireInterval = __fireInterval;
  globalThis.__findTimeout = __findTimeout;
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


// #311: one boot = one fresh vm context (full isolation). A scenario may
// carry `phases` — each phase is an independent boot with its own
// responses/driver (the handshake matrix needs fresh page sessions because
// feedInit settles once per page session and never resets).
async function runPhase(name, spec) {
  __unhandled.length = 0;  // per-phase unhandled-rejection window
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
        ok: true,
        result: result === undefined ? null : result,
        unhandled: __unhandled.slice(),
      }),
      (e) => ({
        ok: false,
        error: String(e && e.stack || e),
        unhandled: __unhandled.slice(),
      }),
    );
  } catch (e) {
    return Promise.resolve({
      ok: false, error: String(e && e.stack || e),
      unhandled: __unhandled.slice(),
    });
  }
}

async function runScenarioAsync(name, spec) {
  if (Array.isArray(spec.phases)) {
    const phases = [];
    for (let i = 0; i < spec.phases.length; i++) {
      phases.push(await runPhase(`${name}#phase${i}`, spec.phases[i]));
    }
    return {
      name,
      ok: phases.every((p) => p.ok),
      phases,
      unhandled: phases.reduce((a, p) => a.concat(p.unhandled), []),
    };
  }
  const p = await runPhase(name, spec);
  return { name, ok: p.ok, result: p.result, unhandled: p.unhandled,
           ...(p.error ? { error: p.error } : {}) };
}

const out = [];
for (const spec of scenarios) {
  const r = await runScenarioAsync(spec.name, spec);
  out.push(r);
}
// #311: force exit — a scenario's app may leave the vm context's event loop
// with pending bookkeeping that would otherwise keep the process alive (and
// even defer the write callback). The write to a file/pipe is synchronous,
// so the output is fully flushed before the forced exit.
// #304: compact output — 30 scenarios pretty-printed would blow the 64KB
// pipe buffer; compact JSON is still valid and parseable by the runner.
process.stdout.write(JSON.stringify(out));
process.exit(0);
"""


# ---------- fixtures (matching the app's API shapes) ----------

USER_A = {"id": 1, "username": "u", "email": "e@x", "role": "player",
          "sessionTag": "A"}
CHAR1 = {"id": 1, "name": "P", "location_id": 7, "needs": {},
         "control_mode": "MANUAL", "is_owner": True, "money": 100}
CHAR_AUTO = {**CHAR1, "control_mode": "AUTONOMOUS"}
WORLD_A = {"game_timestamp": 1500, "day": 2}
WEATHER_OFF = {"enabled": False}

LOC7 = {"id": 7, "name": "Settlement", "type": "settlement",
        "x": 40, "y": 50, "occupants": []}
LOC9 = {"id": 9, "name": "Remote", "type": "shop", "x": 60, "y": 30,
        "occupants": []}
CATALOG = [LOC7, LOC9]
LOC7_DETAIL = {"id": 7, "name": "Settlement", "characters_here": []}

# ---------- task fixtures (server read-model shapes) ----------
# planned/active carry no completed_at; terminal carry a non-null completed_at.
T_PLANNED = {"task_id": 101, "task_type": "EAT", "status": "planned",
             "source": "player", "started_at": None, "ends_at": 1600,
             "parameters": {}}
T_ACTIVE = {"task_id": 101, "task_type": "EAT", "status": "active",
            "source": "player", "started_at": 1500, "ends_at": 1600,
            "parameters": {}}
T_COMPLETED = {"task_id": 101, "task_type": "EAT", "status": "completed",
               "source": "player", "started_at": 1500, "ends_at": 1600,
               "completed_at": 1600, "parameters": {}}
T_FAILED = {"task_id": 101, "task_type": "EAT", "status": "failed",
            "source": "player", "started_at": 1500, "ends_at": 1600,
            "completed_at": 1600, "parameters": {"reason": "нет еды"}}
T_FAILED_NOREASON = {"task_id": 101, "task_type": "EAT", "status": "failed",
                     "source": "player", "started_at": 1500, "ends_at": 1600,
                     "completed_at": 1600, "parameters": {}}
# a second planned task (older) for the planned-not-active / activation tests
T_PLANNED2 = {"task_id": 102, "task_type": "DRINK", "status": "planned",
              "source": "player", "started_at": None, "ends_at": 1700,
              "parameters": {}}
# cancelled tasks (terminal) — the detail carries the reason in parameters
T_CANCELLED_REASON = {"task_id": 101, "task_type": "EAT",
                      "status": "cancelled", "source": "player",
                      "started_at": 1500, "ends_at": 1600,
                      "completed_at": 1600,
                      "parameters": {"reason": "по запросу"}}
T_CANCELLED_NOREASON = {"task_id": 101, "task_type": "EAT",
                        "status": "cancelled", "source": "player",
                        "started_at": 1500, "ends_at": 1600,
                        "completed_at": 1600, "parameters": {}}


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


def _world_base(user=USER_A, char=CHAR1, world=WORLD_A):
    """Authenticated boot + the World view's GETs (+ the poll feed GET)."""
    return dict(
        _auth(user, char, [char]),
        **{
            "/world": _resp(200, world),
            "/weather": _resp(200, WEATHER_OFF),
            "/locations": _resp(200, CATALOG),
            "/world/events?since=0": _resp(200, []),
            "/world/events?since=400": _resp(200, []),
            "/characters/1/tasks": _resp(200, {
                "planned": [], "active": [], "recent_terminal": []}),
        },
    )


def _place_base(user=USER_A, char=CHAR1, world=WORLD_A):
    """Authenticated boot + the Place loader's GETs (+ bounded sample)."""
    return dict(
        _auth(user, char, [char]),
        **{
            "/world": _resp(200, world),
            "/weather": _resp(200, WEATHER_OFF),
            "/locations": _resp(200, CATALOG),
            "/locations/7": _resp(200, LOC7_DETAIL),
            "/world/events?since=0&limit=100": _resp(200, []),
            "/characters/1/tasks": _resp(200, {
                "planned": [], "active": [], "recent_terminal": []}),
        },
    )


def _tasks_list(planned=None, active=None, recent=None):
    return _resp(200, {
        "planned": planned or [],
        "active": active or [],
        "recent_terminal": recent or [],
    })


def _action_201(task_id=101, task_type="EAT", status="planned"):
    return _resp(201, {"task_id": task_id, "task_type": task_type,
                       "status": status})


def _action_detail(task):
    """GET /actions/{id} — NO completed_at / reason in the detail body."""
    return _resp(200, {
        "task_id": task["task_id"], "task_type": task["task_type"],
        "status": task["status"], "source": task.get("source", "player"),
        "started_at": task.get("started_at"),
        "ends_at": task.get("ends_at"),
    })


def _action_detail_cancelled(reason=None):
    """GET /actions/{id} for a cancelled task — the reason (if any) rides
    in parameters.reason (the backend free-text failure reason)."""
    params = {"reason": reason} if reason else {}
    return _resp(200, {
        "task_id": 101, "task_type": "EAT", "status": "cancelled",
        "source": "player", "started_at": 1500, "ends_at": 1600,
        "parameters": params,
    })


def _scene_ok_false(reason="unsupported", detail="Нечто невозможно"):
    return _resp(200, {"ok": False, "reason": reason, "detail": detail,
                       "events": []})


def _scene_task(task_id=201, task_type="EAT", label="еда",
                status="planned"):
    return _resp(201, {"ok": True, "task_id": task_id,
                       "task_type": task_type, "task_label": label,
                       "status": status, "interpretation": "eat",
                       "events": []})


def _conflict(detail="busy"):
    return _resp(409, {"detail": detail})


def _unavailable(detail="Energy already full"):
    return _resp(422, {"detail": detail})


# ---------- scenarios (independent tests, this order) ----------
# RED on base: 01-16 (the #304 adapter/captions/latch do not exist yet).
# PRESERVATION (must pass on base): 17-19.
# Coverage-gap scenarios (adversarial review): 20-24.

# the active-task list body, embedded in a driver's __setResponse call
# (json is imported by the runner head, which precedes this part)
_ACTIVE_LIST = json.dumps({"planned": [], "active": [T_ACTIVE],
                           "recent_terminal": []})

SCENARIOS = [
    # ---------- 01. place_stays_lifecycle (RED) ----------
    {
        "name": "PLACE_STAYS_LIFECYCLE",
        "initialHash": "#/location/7",
        "responses": {
            **_place_base(),
            **{"/actions": _action_201(101, "EAT", "planned"),
               "/actions/101": _action_detail(T_PLANNED)},
        },
        "driver": (
            "await __settle(); "
            "const before = __facts(); "
            "__call('doAction', 'EAT', {}); "
            "await __settle(); "
            "const after = __facts(); "
            "globalThis.__result = { before, after };"
        ),
    },
    # ---------- 02. world_return_same_status (RED) ----------
    {
        "name": "WORLD_RETURN_SAME_STATUS",
        "initialHash": "#/location/7",
        "responses": {
            **_place_base(),
            **{"/actions": _action_201(101, "EAT", "planned"),
               "/actions/101": _action_detail(T_ACTIVE),
               "/characters/1/tasks": _tasks_list(active=[T_ACTIVE])},
        },
        "driver": (
            "await __settle(); "
            "__call('doAction', 'EAT', {}); "
            "await __settle(); "
            "__setHash('#/world'); await __settle(); "
            "const after = __facts(); "
            "globalThis.__result = { after };"
        ),
    },
    # ---------- 03. single_post_latch (RED) ----------
    {
        "name": "SINGLE_POST_LATCH",
        "initialHash": "#/location/7",
        "responses": {
            **_place_base(),
            **{"/actions": _action_201(101, "EAT", "planned"),
               "/scene/act": _scene_task(201, "EAT", "еда", "planned")},
        },
        "driver": (
            "await __settle(); "
            "__call('doAction', 'EAT', {}); "
            "__call('doAction', 'EAT', {}); "
            "await __settle(); "
            "const afterAction = __facts(); "
            "const input = document.getElementById('scene-act-input'); "
            "if (input) input.value = 'съешь'; "
            "__call('submitSceneAct'); "
            "__call('submitSceneAct'); "
            "await __settle(); "
            "const afterScene = __facts(); "
            "globalThis.__result = { afterAction, afterScene };"
        ),
    },
    # ---------- 04. planned_not_active (RED) ----------
    {
        "name": "PLANNED_NOT_ACTIVE",
        "initialHash": "#/world",
        "responses": {
            **_world_base(),
            **{"/characters/1/tasks": _tasks_list(
                planned=[T_PLANNED2, T_PLANNED])},
        },
        "driver": (
            "await __settle(); "
            "const f1 = __facts(); "
            "globalThis.__result = { f1 };"
        ),
    },
    # ---------- 05. terminal_sticky (RED) ----------
    {
        "name": "TERMINAL_STICKY",
        "initialHash": "#/world",
        "responses": {
            **_world_base(),
            **{"/actions": _action_201(101, "EAT", "planned"),
               "/actions/101": _action_detail(T_COMPLETED),
               "/characters/1/tasks": _tasks_list(
                   planned=[T_PLANNED], recent=[T_COMPLETED])},
        },
        "driver": (
            "await __settle(); "
            "__call('doAction', 'EAT', {}); "
            "await __settle(); "
            "const f1 = __facts(); "
            "globalThis.__result = { f1 };"
        ),
    },
    # ---------- 06. preacceptance_absence_ignored (RED) ----------
    {
        "name": "PREACCEPTANCE_ABSENCE_IGNORED",
        "initialHash": "#/world",
        "responses": {
            **_world_base(),
            **{"/actions": _action_201(101, "EAT", "planned")},
        },
        "driver": (
            "await __settle(); "
            "__hold('/characters/1/tasks'); "
            "__fireInterval(__S.pollTimer); "
            "await __settle(); "
            "__call('doAction', 'EAT', {}); "
            "await __settle(); "
            "__release('/characters/1/tasks', "
            "{status: 200, body: {planned: [], active: [], "
            "recent_terminal: []}}); "
            "await __settle(); "
            "const after = __facts(); "
            "globalThis.__result = { after };"
        ),
    },
    # ---------- 07. failed_via_detail (RED) ----------
    {
        "name": "FAILED_VIA_DETAIL",
        "initialHash": "#/world",
        "responses": {
            **_world_base(),
            **{"/actions": _action_201(101, "EAT", "planned"),
               "/actions/101": _resp(200, T_FAILED),
               "/characters/1/tasks": _tasks_list()},
        },
        "driver": (
            "await __settle(); "
            "__call('doAction', 'EAT', {}); "
            "await __settle(); "
            "const after = __facts(); "
            "globalThis.__result = { after };"
        ),
    },
    # ---------- 08. failed_no_reason (RED) ----------
    {
        "name": "FAILED_NO_REASON",
        "initialHash": "#/world",
        "responses": {
            **_world_base(),
            **{"/actions": _action_201(101, "EAT", "planned"),
               "/actions/101": _resp(200, T_FAILED_NOREASON),
               "/characters/1/tasks": _tasks_list()},
        },
        "driver": (
            "await __settle(); "
            "__call('doAction', 'EAT', {}); "
            "await __settle(); "
            "const after = __facts(); "
            "globalThis.__result = { after };"
        ),
    },
    # ---------- 09. unavailable_vs_history_missing (RED) ----------
    {
        "name": "UNAVAILABLE_VS_HISTORY_MISSING",
        "initialHash": "#/world",
        "responses": {
            **_world_base(),
            **{"/actions": _action_201(101, "EAT", "planned"),
               "/actions/101": _resp(404, {"detail": "not found"})},
        },
        "driver": (
            "await __settle(); "
            "__call('doAction', 'EAT', {}); "
            "await __settle(); "
            "const f1 = __facts(); "
            "globalThis.__result = { f1 };"
        ),
    },
    # ---------- 10. events_trigger_only (RED) ----------
    {
        "name": "EVENTS_TRIGGER_ONLY",
        "initialHash": "#/world",
        "responses": {
            **_world_base(),
            **{"/actions": _action_201(101, "EAT", "planned"),
               "/actions/101": _action_detail(T_ACTIVE)},
        },
        "driver": (
            "await __settle(); "
            "__wsOpen(); __wsMessage({type:'hello', cursor: 500}); "
            "await __settle(); "
            "__call('doAction', 'EAT', {}); "
            "await __settle(); "
            "__wsMessage({type:'events', events:[{id:601, "
            "event_type:'TASK_COMPLETED', actor_id:2, "
            "game_timestamp:1500, text:'other task done'}]}); "
            "await __settle(); "
            "const after = __facts(); "
            "globalThis.__result = { after };"
        ),
    },
    # ---------- 11. route_switch_keeps_task (RED) ----------
    {
        "name": "ROUTE_SWITCH_KEEPS_TASK",
        "initialHash": "#/location/7",
        "responses": {
            **_place_base(),
            **{"/actions": _action_201(101, "EAT", "planned"),
               "/actions/101": _action_detail(T_ACTIVE)},
        },
        "driver": (
            "await __settle(); "
            "__hold('/actions'); "
            "__call('doAction', 'EAT', {}); "
            "await __settle(); "
            "__setHash('#/world'); await __settle(); "
            "__release('/actions', "
            "{status: 201, body: {task_id: 101, task_type: 'EAT', "
            "status: 'planned'}}); "
            "await __settle(); "
            "const after = __facts(); "
            "globalThis.__result = { after };"
        ),
    },
    # ---------- 12. relogin_clears (RED) ----------
    {
        "name": "RELOGIN_CLEARS",
        "initialHash": "#/location/7",
        "responses": {
            **_place_base(),
            **{"/actions": _action_201(101, "EAT", "planned"),
               "/actions/101": _action_detail(T_ACTIVE),
               "/scene/act": _scene_task(201, "EAT", "еда", "planned")},
        },
        "driver": (
            "await __settle(); "
            "__call('doAction', 'EAT', {}); "
            "await __settle(); "
            # set up the session state resetGameUiContext must clear
            "__S.travel = {state: 'travelling', destName: 'Remote', "
            "taskId: 301}; "
            "__S.sceneActDraft = 'съешь'; "
            "const before = __facts(); "
            "__call('resetGameUiContext'); "
            "await __settle(); "
            "const after = __facts(); "
            "globalThis.__result = { before, after };"
        ),
    },
    # ---------- 13. poll_refresh_no_second_interval (RED) ----------
    {
        "name": "POLL_REFRESH_NO_SECOND_INTERVAL",
        "initialHash": "#/world",
        "responses": {
            **_world_base(),
            **{"/characters/1/tasks": _tasks_list(active=[T_ACTIVE])},
        },
        "driver": (
            "await __settle(); "
            "const f1 = __facts(); "
            "__fireInterval(__S.pollTimer); "
            "await __settle(); "
            "const f2 = __facts(); "
            "globalThis.__result = { f1, f2 };"
        ),
    },
    # ---------- 14. reload_reconstructs (RED) ----------
    {
        "name": "RELOAD_RECONSTRUCTS",
        "initialHash": "#/world",
        "responses": {
            **_world_base(),
            **{"/characters/1/tasks": _tasks_list(active=[T_ACTIVE])},
        },
        "driver": (
            "await __settle(); "
            "const f1 = __facts(); "
            "globalThis.__result = { f1 };"
        ),
    },
    # ---------- 15. ends_at_estimate (RED) ----------
    {
        "name": "ENDS_AT_ESTIMATE",
        "initialHash": "#/world",
        "responses": {
            **_world_base(),
            **{"/characters/1/tasks": _tasks_list(active=[T_ACTIVE])},
        },
        "driver": (
            "await __settle(); "
            "const f1 = __facts(); "
            "globalThis.__result = { f1 };"
        ),
    },
    # ---------- 16. scene_travel_adapter (RED) ----------
    {
        "name": "SCENE_TRAVEL_ADAPTER",
        "initialHash": "#/location/7",
        "responses": {
            **_place_base(),
            **{"/scene/act": _scene_task(201, "EAT", "еда", "planned"),
               "/actions/201": _action_detail(T_ACTIVE)},
        },
        "driver": (
            "await __settle(); "
            "const input = document.getElementById('scene-act-input'); "
            "if (input) input.value = 'съешь'; "
            "__call('submitSceneAct'); "
            "await __settle(); "
            "const after = __facts(); "
            "globalThis.__result = { after };"
        ),
    },
    # ---------- 17. autonomous_gates (PRESERVATION) ----------
    {
        "name": "AUTONOMOUS_GATES",
        "initialHash": "#/world",
        "responses": {
            **_world_base(user=USER_A, char=CHAR_AUTO),
            **{"/actions": _action_201(101, "EAT", "planned")},
        },
        "driver": (
            "await __settle(); "
            "const loc = {id: 9, name: 'Remote'}; "
            "__call('moveTo', loc); "
            "await __settle(); "
            "const after = __facts(); "
            "globalThis.__result = { after };"
        ),
    },
    # ---------- 18. scene_non_task (PRESERVATION) ----------
    {
        "name": "SCENE_NON_TASK",
        "initialHash": "#/location/7",
        "responses": {
            **_place_base(),
            **{"/scene/act": _scene_ok_false("unsupported",
                                             "Нечто невозможно")},
        },
        "driver": (
            "await __settle(); "
            "const input = document.getElementById('scene-act-input'); "
            "if (input) input.value = 'покажи'; "
            "__call('submitSceneAct'); "
            "await __settle(); "
            "const after = __facts(); "
            "globalThis.__result = { after };"
        ),
    },
    # ---------- 19. tasks_screen_integrity (PRESERVATION) ----------
    {
        "name": "TASKS_SCREEN_INTEGRITY",
        "initialHash": "#/tasks",
        "responses": {
            **_world_base(),
            **{"/characters/1/goals": _resp(200, []),
               "/characters/1/desire/opportunities": _resp(
                   200, {"opportunities": [], "desire": None}),
               "/characters/1/tasks": _tasks_list(
                   planned=[T_PLANNED2, T_PLANNED],
                   recent=[T_COMPLETED])},
        },
        "driver": (
            "await __settle(); "
            "const f1 = __facts(); "
            "globalThis.__result = { f1 };"
        ),
    },
    # ---------- 20. list_failure_recovery (coverage F-A) ----------
    {
        "name": "LIST_FAILURE_RECOVERY",
        "initialHash": "#/world",
        "responses": {
            **_world_base(),
            **{"/actions": _action_201(101, "EAT", "planned")},
        },
        "driver": (
            "await __settle(); "
            "__hold('/characters/1/tasks'); "
            "__fireInterval(__S.pollTimer); "
            "await __settle(); "
            "__call('doAction', 'EAT', {}); "
            "await __settle(); "
            "__release('/characters/1/tasks', "
            "{status: 503, body: {detail: 'server error'}}); "
            "await __settle(); "
            "__setResponse('/characters/1/tasks', "
            "{status: 200, body: " + _ACTIVE_LIST + "}); "
            "__fireInterval(__S.pollTimer); "
            "await __settle(); "
            "const after = __facts(); "
            "globalThis.__result = { after };"
        ),
    },
    # ---------- 21a. post_failure 409 REAL server detail (coverage F-B) ----------
    # The real backend's 409-active detail is the English string
    # "character already has an active task" (app.py:1164/:1243/:1507 —
    # POST /actions, /travel/plan, /scene/act, all identical). It does NOT
    # match the classifier's /busy|занят/i, so the current code renders the
    # generic caption. This sub-case is RED against the current code.
    {
        "name": "POST_FAIL_409_REAL_ACTIVE",
        "initialHash": "#/location/7",
        "responses": {
            **_place_base(),
            **{"/actions": _resp(409, {"detail":
                "character already has an active task"})},
        },
        "driver": (
            "await __settle(); "
            "__call('doAction', 'EAT', {}); "
            "await __settle(); "
            "const after = __facts(); "
            "globalThis.__result = { after };"
        ),
    },
    # ---------- 21b. post_failure 409 synthetic busy (coverage F-B) ----------
    # A synthetic busy-flavored detail (matches /busy/i) — the classifier
    # maps it to the busy caption. GREEN against the current code.
    {
        "name": "POST_FAIL_409_BUSY",
        "initialHash": "#/location/7",
        "responses": {
            **_place_base(),
            **{"/actions": _resp(409, {"detail": "character is busy"})},
        },
        "driver": (
            "await __settle(); "
            "__call('doAction', 'EAT', {}); "
            "await __settle(); "
            "const after = __facts(); "
            "globalThis.__result = { after };"
        ),
    },
    # ---------- 21b. post_failure 409 other (coverage F-B) ----------
    {
        "name": "POST_FAIL_409_OTHER",
        "initialHash": "#/location/7",
        "responses": {
            **_place_base(),
            **{"/actions": _resp(409, {"detail": "action unavailable "
                                          "right now"})},
        },
        "driver": (
            "await __settle(); "
            "__call('doAction', 'EAT', {}); "
            "await __settle(); "
            "const after = __facts(); "
            "globalThis.__result = { after };"
        ),
    },
    # ---------- 21c. post_failure 422 (coverage F-B) ----------
    {
        "name": "POST_FAIL_422",
        "initialHash": "#/location/7",
        "responses": {
            **_place_base(),
            **{"/actions": _resp(422, {"detail": "invalid action "
                                          "parameters"})},
        },
        "driver": (
            "await __settle(); "
            "__call('doAction', 'EAT', {}); "
            "await __settle(); "
            "const after = __facts(); "
            "globalThis.__result = { after };"
        ),
    },
    # ---------- 21d. post_failure 503 (coverage F-B) ----------
    {
        "name": "POST_FAIL_503",
        "initialHash": "#/location/7",
        "responses": {
            **_place_base(),
            **{"/actions": _resp(503, {"detail": "service unavailable"})},
        },
        "driver": (
            "await __settle(); "
            "__call('doAction', 'EAT', {}); "
            "await __settle(); "
            "const after = __facts(); "
            "globalThis.__result = { after };"
        ),
    },
    # ---------- 22a. history_missing corroborated (coverage F-B/F-C) ----------
    {
        "name": "HISTORY_MISSING_CORROBORATED",
        "initialHash": "#/world",
        "responses": {
            **_world_base(),
            **{"/actions": _action_201(101, "EAT", "planned"),
               "/actions/101": _resp(404, {"detail": "not found"})},
        },
        "driver": (
            "await __settle(); "
            "__hold('/characters/1/tasks'); "
            "__fireInterval(__S.pollTimer); "
            "await __settle(); "
            "__call('doAction', 'EAT', {}); "
            "await __settle(); "
            "__release('/characters/1/tasks', "
            "{status: 200, body: {planned: [], active: [], "
            "recent_terminal: []}}); "
            "await __settle(); "
            "__fireInterval(__S.pollTimer); "
            "await __settle(); "
            "const after = __facts(); "
            "globalThis.__result = { after };"
        ),
    },
    # ---------- 22b. history_missing contrast (404 alone) ----------
    {
        "name": "HISTORY_MISSING_CONTRAST",
        "initialHash": "#/world",
        "responses": {
            **_world_base(),
            **{"/actions": _action_201(101, "EAT", "planned"),
               "/actions/101": _resp(404, {"detail": "not found"})},
        },
        "driver": (
            "await __settle(); "
            "__call('doAction', 'EAT', {}); "
            "await __settle(); "
            "const after = __facts(); "
            "globalThis.__result = { after };"
        ),
    },
    # ---------- 23. event_trigger_get_count (coverage F-D) ----------
    {
        "name": "EVENT_TRIGGER_RECHECK",
        "initialHash": "#/world",
        "responses": {
            **_world_base(),
            **{"/actions": _action_201(101, "EAT", "planned"),
               "/actions/101": _action_detail(T_ACTIVE)},
        },
        "driver": (
            "await __settle(); "
            "__call('doAction', 'EAT', {}); "
            "await __settle(); "
            "const before = __facts(); "
            "__wsOpen(); __wsMessage({type:'hello', cursor: 500}); "
            "await __settle(); "
            "__wsMessage({type:'events', events:[{id:601, "
            "event_type:'TASK_COMPLETED', actor_id:2, "
            "game_timestamp:1500, text:'other task done'}]}); "
            "await __settle(); "
            "const after = __facts(); "
            "globalThis.__result = { before, after };"
        ),
    },
    # ---------- 24a. sending caption (coverage F-B remainder) ----------
    {
        "name": "SENDING_CAPTION",
        "initialHash": "#/location/7",
        "responses": {
            **_place_base(),
            **{"/actions": _action_201(101, "EAT", "planned")},
        },
        "driver": (
            "await __settle(); "
            "__hold('/actions'); "
            "__call('doAction', 'EAT', {}); "
            "await __settle(); "
            "const after = __facts(); "
            "globalThis.__result = { after };"
        ),
    },
    # ---------- 24b. cancelled with reason (coverage F-B remainder) ----------
    {
        "name": "CANCELLED_REASON",
        "initialHash": "#/world",
        "responses": {
            **_world_base(),
            **{"/actions": _action_201(101, "EAT", "planned"),
               "/actions/101": _action_detail_cancelled("по запросу")},
        },
        "driver": (
            "await __settle(); "
            "__call('doAction', 'EAT', {}); "
            "await __settle(); "
            "const after = __facts(); "
            "globalThis.__result = { after };"
        ),
    },
    # ---------- 24c. cancelled without reason (coverage F-B remainder) ----------
    {
        "name": "CANCELLED_NOREASON",
        "initialHash": "#/world",
        "responses": {
            **_world_base(),
            **{"/actions": _action_201(101, "EAT", "planned"),
               "/actions/101": _action_detail_cancelled(None)},
        },
        "driver": (
            "await __settle(); "
            "__call('doAction', 'EAT', {}); "
            "await __settle(); "
            "const after = __facts(); "
            "globalThis.__result = { after };"
        ),
    },
]


def _run_harness():
    os.makedirs(SCRATCH, exist_ok=True)
    harness_path = os.path.join(SCRATCH, "m304_harness.mjs")
    scen_path = os.path.join(SCRATCH, "m304_scenarios.json")
    with open(harness_path, "w", encoding="utf-8") as f:
        f.write(HARNESS_JS)
    with open(scen_path, "w", encoding="utf-8") as f:
        json.dump(SCENARIOS, f, ensure_ascii=False)
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
    if "phases" in r:
        return r["phases"]
    return r["result"]


def _unhandled(results, name):
    r = results[name]
    if "phases" in r:
        out = []
        for p in r["phases"]:
            out.extend(p.get("unhandled", []))
        return out
    return r.get("unhandled", [])


# ---------- tests ----------
# RED on base: 01-16 (the #304 adapter/captions/latch do not exist yet).
# PRESERVATION (must pass on base): 17-19.

# RU captions from the #304 design contract.
CAP_ACCEPTED = "Задача принята. Ожидает начала"
CAP_ACTIVE = "Персонаж занят"
CAP_COMPLETED = "Действие завершено"
CAP_FAILED = "Не удалось"
CAP_FAILED_NOREASON = "Не удалось: причина не указана"
CAP_UNAVAILABLE = "Не удаётся уточнить состояние"
CAP_HISTORY_MISSING = "История задачи недоступна"
CAP_MOVE = "Запрос на перемещение принят"
CAP_ENDS_AT = "Плановое окончание по оценке сервера"
CAP_CANCELLED = "Действие отменено"
CAP_SENDING = "Отправляем запрос…"
CAP_BUSY = "Персонаж уже занят другим действием"
CAP_ACTION_UNAVAILABLE = "Действие сейчас недоступно"
CAP_IMPOSSIBLE = "Действие сейчас невозможно. Проверьте условия"
CAP_CONFIRM = "Не удалось подтвердить принятие запроса. Проверьте связь"


# ---------- 01. place_stays_lifecycle (RED) ----------

def test_01_place_stays_lifecycle(results):
    r = _facts(results, "PLACE_STAYS_LIFECYCLE")
    after = r["after"]
    # the Place view stays mounted (no forced #/world)
    assert after["hash"] == "#/location/7", (
        f"doAction must stay on the Place (no forced #/world), "
        f"got {after['hash']!r}"
    )
    # the accepted feedback is the #304 caption, not the base toast
    assert after["placeTaskText"] is not None and \
        CAP_ACCEPTED in after["placeTaskText"], (
        f"the Place must show the accepted caption, "
        f"got {after['placeTaskText']!r}"
    )
    unhandled = _unhandled(results, "PLACE_STAYS_LIFECYCLE")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 02. world_return_same_status (RED) ----------

def test_02_world_return_same_status(results):
    r = _facts(results, "WORLD_RETURN_SAME_STATUS")
    after = r["after"]
    # returning to World shows the same task (no reset)
    assert after["worldNowStatus"] is not None and \
        CAP_ACTIVE in after["worldNowStatus"], (
        f"World must show the active task after return, "
        f"got {after['worldNowStatus']!r}"
    )
    unhandled = _unhandled(results, "WORLD_RETURN_SAME_STATUS")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 03. single_post_latch (RED) ----------

def test_03_single_post_latch(results):
    r = _facts(results, "SINGLE_POST_LATCH")
    a, s = r["afterAction"], r["afterScene"]
    # double-click = exactly 1 POST /actions
    assert a["postActions"] == 1, (
        f"a double-click must produce exactly 1 POST /actions, "
        f"got {a['postActions']}"
    )
    # scene Enter+click = exactly 1 POST /scene/act
    assert s["postScene"] == 1, (
        f"a scene double-submit must produce exactly 1 POST /scene/act, "
        f"got {s['postScene']}"
    )
    unhandled = _unhandled(results, "SINGLE_POST_LATCH")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 04. planned_not_active (RED) ----------

def test_04_planned_not_active(results):
    r = _facts(results, "PLANNED_NOT_ACTIVE")
    f1 = r["f1"]
    # a planned task must NOT render «Персонаж занят»
    assert f1["worldNowStatus"] is not None and \
        CAP_ACCEPTED in f1["worldNowStatus"], (
        f"a planned task must show the accepted caption, "
        f"got {f1['worldNowStatus']!r}"
    )
    assert CAP_ACTIVE not in (f1["worldNowStatus"] or ""), (
        f"a planned task must not show the active caption, "
        f"got {f1['worldNowStatus']!r}"
    )
    unhandled = _unhandled(results, "PLANNED_NOT_ACTIVE")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 05. terminal_sticky (RED) ----------

def test_05_terminal_sticky(results):
    r = _facts(results, "TERMINAL_STICKY")
    f1 = r["f1"]
    # a completed task must not regress to planned/active
    assert f1["worldNowStatus"] is not None and \
        CAP_COMPLETED in f1["worldNowStatus"], (
        f"a completed task must stay terminal, "
        f"got {f1['worldNowStatus']!r}"
    )
    unhandled = _unhandled(results, "TERMINAL_STICKY")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 06. preacceptance_absence_ignored (RED) ----------

def test_06_preacceptance_absence_ignored(results):
    r = _facts(results, "PREACCEPTANCE_ABSENCE_IGNORED")
    after = r["after"]
    # RED lever: the #304 adapter must exist (base has none — the
    # pre-acceptance-absence rule is a property of the adapter).
    assert after["hasTaskPresentation"] is True, (
        f"the #304 task adapter must exist, "
        f"got hasTaskPresentation={after['hasTaskPresentation']!r}"
    )
    # a pre-acceptance list read must not trigger a detail GET
    assert after["getActionsDetail"] == [], (
        f"a pre-acceptance absence must not trigger a detail GET, "
        f"got {after['getActionsDetail']!r}"
    )
    unhandled = _unhandled(results, "PREACCEPTANCE_ABSENCE_IGNORED")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 07. failed_via_detail (RED) ----------

def test_07_failed_via_detail(results):
    r = _facts(results, "FAILED_VIA_DETAIL")
    after = r["after"]
    assert after["worldNowStatus"] is not None and \
        CAP_FAILED in after["worldNowStatus"] and \
        "нет еды" in after["worldNowStatus"], (
        f"a failed task must show the reason, "
        f"got {after['worldNowStatus']!r}"
    )
    assert CAP_COMPLETED not in (after["worldNowStatus"] or ""), (
        f"a failed task must not show «завершено», "
        f"got {after['worldNowStatus']!r}"
    )
    unhandled = _unhandled(results, "FAILED_VIA_DETAIL")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 08. failed_no_reason (RED) ----------

def test_08_failed_no_reason(results):
    r = _facts(results, "FAILED_NO_REASON")
    after = r["after"]
    assert after["worldNowStatus"] is not None and \
        CAP_FAILED_NOREASON in after["worldNowStatus"], (
        f"a failed task with no reason must show the exact caption, "
        f"got {after['worldNowStatus']!r}"
    )
    unhandled = _unhandled(results, "FAILED_NO_REASON")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 09. unavailable_vs_history_missing (RED) ----------

def test_09_unavailable_vs_history_missing(results):
    r = _facts(results, "UNAVAILABLE_VS_HISTORY_MISSING")
    f1 = r["f1"]
    # a detail 404 alone → unavailable
    assert f1["worldNowStatus"] is not None and \
        CAP_UNAVAILABLE in f1["worldNowStatus"], (
        f"a detail 404 must show the unavailable caption, "
        f"got {f1['worldNowStatus']!r}"
    )
    unhandled = _unhandled(results, "UNAVAILABLE_VS_HISTORY_MISSING")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 10. events_trigger_only (RED) ----------

def test_10_events_trigger_only(results):
    r = _facts(results, "EVENTS_TRIGGER_ONLY")
    after = r["after"]
    # RED lever: the #304 adapter must exist (base has none — the
    # trigger-only rule is a property of the adapter).
    assert after["hasTaskPresentation"] is True, (
        f"the #304 task adapter must exist, "
        f"got hasTaskPresentation={after['hasTaskPresentation']!r}"
    )
    # a same-type different-task event must not complete our task
    assert CAP_COMPLETED not in (after["worldNowStatus"] or ""), (
        f"a different-task event must not complete our task, "
        f"got {after['worldNowStatus']!r}"
    )
    unhandled = _unhandled(results, "EVENTS_TRIGGER_ONLY")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 11. route_switch_keeps_task (RED) ----------

def test_11_route_switch_keeps_task(results):
    r = _facts(results, "ROUTE_SWITCH_KEEPS_TASK")
    after = r["after"]
    # the task id is retained after navigation
    assert after["currentTaskId"] == 101, (
        f"the task id must be retained after navigation, "
        f"got {after['currentTaskId']!r}"
    )
    unhandled = _unhandled(results, "ROUTE_SWITCH_KEEPS_TASK")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 12. relogin_clears (RED) ----------

def test_12_relogin_clears(results):
    r = _facts(results, "RELOGIN_CLEARS")
    before, after = r["before"], r["after"]
    # RED lever: the #304 adapter must exist before the reset (base has
    # none — the clear-on-reset rule is a property of the adapter).
    assert before["hasTaskPresentation"] is True, (
        f"the #304 task adapter must exist before reset, "
        f"got hasTaskPresentation={before['hasTaskPresentation']!r}"
    )
    # resetGameUiContext clears the #304 pointer + travel/scene state
    assert after["currentTaskId"] is None, (
        f"resetGameUiContext must clear the task pointer, "
        f"got currentTaskId={after['currentTaskId']!r}"
    )
    assert after["travel"] is None, (
        f"resetGameUiContext must clear S.travel, got {after['travel']!r}"
    )
    assert after["sceneActDraft"] is None, (
        f"resetGameUiContext must clear S.sceneActDraft, "
        f"got {after['sceneActDraft']!r}"
    )
    unhandled = _unhandled(results, "RELOGIN_CLEARS")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 13. poll_refresh_no_second_interval (RED) ----------

def test_13_poll_refresh_no_second_interval(results):
    r = _facts(results, "POLL_REFRESH_NO_SECOND_INTERVAL")
    f2 = r["f2"]
    # the poll refresh updates the status host
    assert f2["worldNowStatus"] is not None and \
        CAP_ACTIVE in f2["worldNowStatus"], (
        f"the poll refresh must update the World status host, "
        f"got {f2['worldNowStatus']!r}"
    )
    # the interval count stays exactly 1
    assert f2["intervals"] == 1, (
        f"the poll must not add a second interval, got {f2['intervals']}"
    )
    unhandled = _unhandled(results, "POLL_REFRESH_NO_SECOND_INTERVAL")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 14. reload_reconstructs (RED) ----------

def test_14_reload_reconstructs(results):
    r = _facts(results, "RELOAD_RECONSTRUCTS")
    f1 = r["f1"]
    # a fresh boot with a server active task shows it (no localStorage)
    assert f1["worldNowStatus"] is not None and \
        CAP_ACTIVE in f1["worldNowStatus"], (
        f"a fresh boot must show the server active task, "
        f"got {f1['worldNowStatus']!r}"
    )
    unhandled = _unhandled(results, "RELOAD_RECONSTRUCTS")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 15. ends_at_estimate (RED) ----------

def test_15_ends_at_estimate(results):
    r = _facts(results, "ENDS_AT_ESTIMATE")
    f1 = r["f1"]
    # ends_at renders ONLY as the server-estimate caption
    assert f1["worldNowStatus"] is not None and \
        CAP_ENDS_AT in f1["worldNowStatus"], (
        f"ends_at must render as the server-estimate caption, "
        f"got {f1['worldNowStatus']!r}"
    )
    unhandled = _unhandled(results, "ENDS_AT_ESTIMATE")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 16. scene_travel_adapter (RED) ----------

def test_16_scene_travel_adapter(results):
    r = _facts(results, "SCENE_TRAVEL_ADAPTER")
    after = r["after"]
    # RED lever: the #304 adapter must exist (base has none — the
    # scene-task adapter consumption is a property of the adapter).
    assert after["hasTaskPresentation"] is True, (
        f"the #304 task adapter must exist, "
        f"got hasTaskPresentation={after['hasTaskPresentation']!r}"
    )
    # the scene task result consumes the adapter
    assert after["sceneAct"] is not None and \
        after["sceneAct"]["task_id"] == 201, (
        f"the scene task must be tracked, got {after['sceneAct']!r}"
    )
    unhandled = _unhandled(results, "SCENE_TRAVEL_ADAPTER")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 17. autonomous_gates (PRESERVATION) ----------

def test_17_autonomous_gates(results):
    r = _facts(results, "AUTONOMOUS_GATES")
    after = r["after"]
    # AUTONOMOUS MOVE: zero POST, localized refusal
    assert after["postActions"] == 0, (
        f"AUTONOMOUS MOVE must not POST, got {after['postActions']}"
    )
    assert after["toastText"] == \
        "Персонаж действует сам — переключите режим в Профиле", (
        f"AUTONOMOUS MOVE must show the localized refusal, "
        f"got {after['toastText']!r}"
    )
    unhandled = _unhandled(results, "AUTONOMOUS_GATES")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 18. scene_non_task (PRESERVATION) ----------

def test_18_scene_non_task(results):
    r = _facts(results, "SCENE_NON_TASK")
    after = r["after"]
    # scene ok:false preserves the draft
    assert after["sceneActDraft"] == "покажи", (
        f"scene ok:false must preserve the draft, "
        f"got {after['sceneActDraft']!r}"
    )
    # …and creates no task entry
    assert after["sceneAct"] is not None and \
        after["sceneAct"]["ok"] is False, (
        f"scene ok:false must not create a task, got {after['sceneAct']!r}"
    )
    unhandled = _unhandled(results, "SCENE_NON_TASK")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 19. tasks_screen_integrity (PRESERVATION) ----------

def test_19_tasks_screen_integrity(results):
    r = _facts(results, "TASKS_SCREEN_INTEGRITY")
    f1 = r["f1"]
    # the Tasks screen shows the planned tasks (oldest-first)
    assert f1["tasksActiveText"] is not None and \
        "Активные" in f1["tasksActiveText"], (
        f"the Tasks screen must show the active box, "
        f"got {f1['tasksActiveText']!r}"
    )
    # …and the history (bounded)
    assert f1["tasksHistoryText"] is not None and \
        "История" in f1["tasksHistoryText"], (
        f"the Tasks screen must show the history box, "
        f"got {f1['tasksHistoryText']!r}"
    )
    unhandled = _unhandled(results, "TASKS_SCREEN_INTEGRITY")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 20. list_failure_recovery (coverage F-A) ----------

def test_20_list_failure_recovery(results):
    r = _facts(results, "LIST_FAILURE_RECOVERY")
    after = r["after"]
    # recovery: the successful list read shows the server-backed active
    # caption (recovered via the list, not a detail lookup)
    assert after["worldNowStatus"] is not None and \
        CAP_ACTIVE in after["worldNowStatus"], (
        f"the recovery must show the active caption, "
        f"got {after['worldNowStatus']!r}"
    )
    # no detail GET fired (recovery is via the list, not a detail lookup)
    assert after["getActionsDetail"] == [], (
        f"recovery must not fire a detail GET, "
        f"got {after['getActionsDetail']!r}"
    )
    # no false unavailable caption persists after recovery
    assert CAP_UNAVAILABLE not in (after["worldNowStatus"] or ""), (
        f"no false unavailable caption may persist, "
        f"got {after['worldNowStatus']!r}"
    )
    unhandled = _unhandled(results, "LIST_FAILURE_RECOVERY")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 21. post_failure_captions (coverage F-B) ----------

def test_21_post_failure_captions(results):
    # (a) 409 synthetic busy -> the busy caption (classifier matches /busy/i)
    a = _facts(results, "POST_FAIL_409_BUSY")["after"]
    assert a["submitLineText"] == CAP_BUSY, (
        f"a 409 busy must show the busy caption, "
        f"got {a['submitLineText']!r}"
    )
    assert a["currentTaskId"] is None, (
        f"a failed POST must not accept a task, "
        f"got currentTaskId={a['currentTaskId']!r}"
    )
    # (b) 409 other -> the generic unavailable caption
    b = _facts(results, "POST_FAIL_409_OTHER")["after"]
    assert b["submitLineText"] == CAP_ACTION_UNAVAILABLE, (
        f"a 409 other must show the unavailable caption, "
        f"got {b['submitLineText']!r}"
    )
    assert b["currentTaskId"] is None, (
        f"a failed POST must not accept a task, "
        f"got currentTaskId={b['currentTaskId']!r}"
    )
    # (c) 422 -> the impossible caption
    c = _facts(results, "POST_FAIL_422")["after"]
    assert c["submitLineText"] == CAP_IMPOSSIBLE, (
        f"a 422 must show the impossible caption, "
        f"got {c['submitLineText']!r}"
    )
    assert c["currentTaskId"] is None, (
        f"a failed POST must not accept a task, "
        f"got currentTaskId={c['currentTaskId']!r}"
    )
    # (d) 503 -> the connection caption
    d = _facts(results, "POST_FAIL_503")["after"]
    assert d["submitLineText"] == CAP_CONFIRM, (
        f"a 503 must show the connection caption, "
        f"got {d['submitLineText']!r}"
    )
    assert d["currentTaskId"] is None, (
        f"a failed POST must not accept a task, "
        f"got currentTaskId={d['currentTaskId']!r}"
    )
    for name in ("POST_FAIL_409_REAL_ACTIVE", "POST_FAIL_409_BUSY",
                 "POST_FAIL_409_OTHER", "POST_FAIL_422", "POST_FAIL_503"):
        unhandled = _unhandled(results, name)
        assert unhandled == [], f"no unhandled errors may escape: {unhandled}"
    # (e) 409 with the REAL server detail -> the busy caption (the contract).
    # The real backend detail is "character already has an active task"
    # (app.py:1164/:1243/:1507) — it does NOT match the classifier's
    # /busy|занят/i, so the current code renders the generic caption.
    # This sub-case is RED against the current code: it documents the
    # production gap (the code worker fixes the classifier next).
    e = _facts(results, "POST_FAIL_409_REAL_ACTIVE")["after"]
    assert e["submitLineText"] == CAP_BUSY, (
        f"a 409 with the REAL server detail must show the busy caption, "
        f"got {e['submitLineText']!r}"
    )
    assert e["currentTaskId"] is None, (
        f"a failed POST must not accept a task, "
        f"got currentTaskId={e['currentTaskId']!r}"
    )


# ---------- 22. history_missing_corroborated (coverage F-B/F-C) ----------

def test_22_history_missing_corroborated(results):
    # corroborated: a post-acceptance list omission + a detail 404 ->
    # exactly ONE corroborating detail GET, the history-missing caption
    a = _facts(results, "HISTORY_MISSING_CORROBORATED")["after"]
    assert a["worldNowStatus"] is not None and \
        CAP_HISTORY_MISSING in a["worldNowStatus"], (
        f"a corroborated missing task must show the history caption, "
        f"got {a['worldNowStatus']!r}"
    )
    assert len(a["getActionsDetail"]) == 1, (
        f"exactly ONE corroborating detail GET must fire, "
        f"got {a['getActionsDetail']!r}"
    )
    assert CAP_UNAVAILABLE not in (a["worldNowStatus"] or ""), (
        f"a corroborated missing must not show the unavailable caption, "
        f"got {a['worldNowStatus']!r}"
    )
    assert CAP_COMPLETED not in (a["worldNowStatus"] or ""), (
        f"a missing task must not show «завершено», "
        f"got {a['worldNowStatus']!r}"
    )
    # contrast: a detail 404 WITHOUT a corroborating list omission keeps
    # the unavailable caption
    b = _facts(results, "HISTORY_MISSING_CONTRAST")["after"]
    assert b["worldNowStatus"] is not None and \
        CAP_UNAVAILABLE in b["worldNowStatus"], (
        f"a 404 alone must show the unavailable caption, "
        f"got {b['worldNowStatus']!r}"
    )
    assert CAP_HISTORY_MISSING not in (b["worldNowStatus"] or ""), (
        f"a 404 alone must not show the history caption, "
        f"got {b['worldNowStatus']!r}"
    )
    for name in ("HISTORY_MISSING_CORROBORATED", "HISTORY_MISSING_CONTRAST"):
        unhandled = _unhandled(results, name)
        assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 23. event_trigger_get_count (coverage F-D) ----------

def test_23_event_trigger_get_count(results):
    r = _facts(results, "EVENT_TRIGGER_RECHECK")
    before, after = r["before"], r["after"]
    # (a) the event triggers a recheck detail GET for the tracked task
    assert len(after["getActionsDetail"]) >= \
        len(before["getActionsDetail"]) + 1, (
        f"a TASK_COMPLETED event must trigger a recheck detail GET, "
        f"before={before['getActionsDetail']!r} "
        f"after={after['getActionsDetail']!r}"
    )
    # (b) the caption comes from the detail response (still active), not
    # the event type
    assert after["worldNowStatus"] is not None and \
        CAP_ACTIVE in after["worldNowStatus"], (
        f"the caption must come from the detail (active), "
        f"got {after['worldNowStatus']!r}"
    )
    # (c) a same-type different-task event never completes our task
    assert CAP_COMPLETED not in (after["worldNowStatus"] or ""), (
        f"a different-task event must not complete our task, "
        f"got {after['worldNowStatus']!r}"
    )
    unhandled = _unhandled(results, "EVENT_TRIGGER_RECHECK")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 24. cancelled_and_sending_captions (coverage F-B remainder) ----------

def test_24_cancelled_and_sending_captions(results):
    # (a) while a POST is in flight, the sending line shows the local
    # sending text and NO task entry exists
    s = _facts(results, "SENDING_CAPTION")["after"]
    assert s["submitLineText"] == CAP_SENDING, (
        f"an in-flight POST must show the sending caption, "
        f"got {s['submitLineText']!r}"
    )
    assert s["currentTaskId"] is None, (
        f"an in-flight POST must not accept a task yet, "
        f"got currentTaskId={s['currentTaskId']!r}"
    )
    # (b) a tracked task reaching cancelled WITH a reason
    cr = _facts(results, "CANCELLED_REASON")["after"]
    assert cr["worldNowStatus"] is not None and \
        cr["worldNowStatus"].startswith(CAP_CANCELLED + ": "), (
        f"a cancelled task with a reason must show the reason caption, "
        f"got {cr['worldNowStatus']!r}"
    )
    assert "по запросу" in cr["worldNowStatus"], (
        f"the cancelled caption must carry the reason, "
        f"got {cr['worldNowStatus']!r}"
    )
    assert CAP_COMPLETED not in (cr["worldNowStatus"] or ""), (
        f"a cancelled task must not show «завершено», "
        f"got {cr['worldNowStatus']!r}"
    )
    # (c) a tracked task reaching cancelled WITHOUT a reason
    cn = _facts(results, "CANCELLED_NOREASON")["after"]
    assert cn["worldNowStatus"] == CAP_CANCELLED, (
        f"a cancelled task without a reason must show the bare caption, "
        f"got {cn['worldNowStatus']!r}"
    )
    assert CAP_COMPLETED not in (cn["worldNowStatus"] or ""), (
        f"a cancelled task must not show «завершено», "
        f"got {cn['worldNowStatus']!r}"
    )
    for name in ("SENDING_CAPTION", "CANCELLED_REASON",
                 "CANCELLED_NOREASON"):
        unhandled = _unhandled(results, name)
        assert unhandled == [], f"no unhandled errors may escape: {unhandled}"
