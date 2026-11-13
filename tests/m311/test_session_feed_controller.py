"""#311 — session-owned world feed controller: RED behavioral suite.

Behavioral tests for the session-owned ``worldFeedController`` of the
world event feed in the REAL production ``backend/app/web/app.js`` (boot
included), run inside a Node ``vm`` context using the embedded stdlib
harness below. The harness is an OWN copy of the m312 vm-harness pattern
(m303 lineage) — the same minimal DOM / fake-timer / controlled-network
shim — extended additively with the #311 surface:

- a rich fake ``WebSocket`` (manual open/message/close/error dispatch on
  the current socket + connect/frame/close logs);
- feed-focused ``__facts()`` (stream state, transport resources, the
  view-local quiet indicators, the network census);
- a typeof-guarded ``__feedController()`` accessor (``null`` on a base
  app.js that lacks the controller — RED authenticity).

The controller does NOT exist in app.js yet. The controller scenarios
(01-07, 09-13) MUST fail on base; the behavior-preservation scenarios
(08, 14-17) MUST pass on base (they guard the refactor).
"""

import json
import os
import subprocess

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
APP_JS = os.path.join(ROOT, "backend/app/web/app.js")
SCRATCH = "/tmp/opencode/m311-testing"


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
// in-memory source only — the production file is untouched. The
// worldFeedController accessor is typeof-guarded so the harness stays
// loadable against a base app.js that lacks the #311 controller.
const EXPOSE = [
  "\n;globalThis.__S = S; globalThis.__app = app; globalThis.__routes = routes;",
  "globalThis.__feedController = () => " +
  "(typeof worldFeedController !== 'undefined' ? worldFeedController : null);",
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
  // #311: feed-focused facts — the shared stream state, the transport
  // resources, the view-local quiet indicators, and the network census.
  function __facts() {
    const S = globalThis.__S;
    const feedBox = document.getElementById("feed");
    const banner = document.getElementById("conn-banner");
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
      appText: globalThis.__app ? globalThis.__app.textContent : null,
      feedGets: feedGets,
      sampleGets: sampleGets,
      worldGets: worldGets,
      locGets: locGets,
      user: S.user ? S.user.id : null,
      character: S.character ? S.character.id : null,
      controller: (typeof globalThis.__feedController === "function")
        ? !!globalThis.__feedController() : null,
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
process.stdout.write(JSON.stringify(out, null, 2));
process.exit(0);
"""


# ---------- fixtures (matching the app's API shapes) ----------

USER_A = {"id": 1, "username": "u", "email": "e@x", "role": "player",
          "sessionTag": "A"}
# #311: a SECOND user object — session identity is by user OBJECT reference,
# so a fresh object (even a re-login) is a new session context.
USER_B = {"id": 2, "username": "u2", "email": "e2@x", "role": "player",
          "sessionTag": "B"}
CHAR1 = {"id": 1, "name": "P", "location_id": 7, "needs": {},
         "control_mode": "MANUAL", "is_owner": False, "money": 100}
CHAR2 = {**CHAR1, "id": 2}
WORLD_A = {"game_timestamp": 1500, "day": 2}
WORLD_A_PLUS1 = {"game_timestamp": 1501, "day": 2}  # +1 min
WEATHER_OFF = {"enabled": False}

LOC7 = {"id": 7, "name": "Settlement", "type": "settlement",
        "x": 40, "y": 50, "occupants": []}
LOC9 = {"id": 9, "name": "Remote", "type": "shop", "x": 60, "y": 30,
        "occupants": []}
LOC10 = {"id": 10, "name": "Workshop", "type": "workshop",
         "x": 42, "y": 56, "occupants": []}
CATALOG = [LOC7, LOC9, LOC10]
# the chat loader reads /locations/{id} (characters_here drives the NPC list)
LOC7_DETAIL = {"id": 7, "name": "Settlement", "characters_here": []}

# a historical event for the Events screen's bounded sample (m241/#76)
HIST_EVENT = {"id": 100, "event_type": "ITEM_CONSUMED", "actor_id": 2,
              "game_timestamp": 1400, "text": "historical-100"}


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
            "/world/events?since=0&limit=100": _resp(200, []),
        },
    )


def _chat_base(user=USER_A, char=CHAR1):
    """Authenticated boot + the Chat loader's GETs."""
    return dict(
        _auth(user, char, [char]),
        **{
            "/locations/7": _resp(200, LOC7_DETAIL),
        },
    )


# ---------- scenarios (independent tests, this order) ----------
# RED on base: 01-07, 09-13. PRESERVATION (must pass on base): 08, 14-17.

SCENARIOS = [
    # ---------- 01. controller_api (RED) ----------
    {
        "name": "CONTROLLER_API",
        "initialHash": "#/world",
        "responses": _world_base(),
        "driver": (
            "await __settle(); "
            "__wsOpen(); __wsMessage({type:'hello', cursor: 500}); await __settle(); "
            "const c = __feedController(); "
            "const r = { hasController: !!c, "
            "api: c ? { start: typeof c.start, subscribe: typeof c.subscribe, "
            "stop: typeof c.stop, teardown: typeof c.teardown } : null }; "
            "if (c) { const b = __facts(); c.start(); await __settle(); "
            "c.start(); await __settle(); const a = __facts(); "
            "r.idem = { wsBefore: b.wsConnects, wsAfter: a.wsConnects, "
            "intervals: a.intervals }; } "
            "globalThis.__result = r;"
        ),
    },
    # ---------- 02. cross_route_event_once (RED) ----------
    {
        "name": "CROSS_ROUTE_EVENT_ONCE",
        "initialHash": "#/world",
        "responses": {**_world_base(), **_place_base(), **_chat_base()},
        "driver": (
            "await __settle(); "
            "__wsOpen(); __wsMessage({type:'hello', cursor: 500}); await __settle(); "
            "__setHash('#/location/7'); await __settle(); "
            "__setHash('#/chat'); await __settle(); "
            "__setHash('#/world'); await __settle(); "
            "if (!__S.ws || __S.ws.readyState !== 1) __wsOpen(); "
            "__wsMessage({type:'events', events:[{id:501, event_type:'ITEM_CONSUMED', "
            "actor_id:2, game_timestamp:1500, text:'Иван съел хлеб'}]}); "
            "await __settle(); "
            "const f = __facts(); "
            "const c = __feedController(); let replayed = null; "
            "if (c) { const seen = []; const disp = c.subscribe((e) => seen.push(e.id)); "
            "await __settle(); replayed = seen; disp(); } "
            "globalThis.__result = { f, replayed };"
        ),
    },
    # ---------- 03. resource_bound_across_nav (RED) ----------
    {
        "name": "RESOURCE_BOUND_ACROSS_NAV",
        "initialHash": "#/world",
        "responses": {**_world_base(), **_place_base(), **_chat_base()},
        "driver": (
            "await __settle(); "
            "__wsOpen(); __wsMessage({type:'hello', cursor: 500}); await __settle(); "
            "const steps = []; "
            "const snap = (label) => { const f = __facts(); "
            "steps.push({label, ws: f.ws, wsConnects: f.wsConnects, "
            "intervals: f.intervals, pollTimer: f.pollTimer}); }; "
            "snap('world0'); "
            "__setHash('#/location/7'); await __settle(); snap('place1'); "
            "__setHash('#/chat'); await __settle(); snap('chat1'); "
            "__setHash('#/location/7'); await __settle(); snap('place2'); "
            "__setHash('#/world'); await __settle(); snap('world1'); "
            "globalThis.__result = { steps };"
        ),
    },
    # ---------- 04. no_nav_burst (RED) ----------
    {
        "name": "NO_NAV_BURST",
        "initialHash": "#/world",
        "responses": {**_world_base(), **_place_base(), **_chat_base()},
        "driver": (
            "await __settle(); "
            "__wsOpen(); __wsMessage({type:'hello', cursor: 500}); await __settle(); "
            "const before = __facts().feedGets.length; "
            "__setHash('#/location/7'); await __settle(); "
            "__setHash('#/chat'); await __settle(); "
            "__setHash('#/world'); await __settle(); "
            "const after = __facts().feedGets.length; "
            "globalThis.__result = { before, after, delta: after - before };"
        ),
    },
    # ---------- 05. cold_nonworld_entry (RED) ----------
    {
        "name": "COLD_NONWORLD_ENTRY",
        "initialHash": "#/location/7",
        "responses": _place_base(),
        "driver": (
            "await __settle(); "
            "const f = __facts(); "
            "globalThis.__result = { f };"
        ),
    },
    # ---------- 06. place_quiet_indicator (RED) ----------
    {
        "name": "PLACE_QUIET_INDICATOR",
        "initialHash": "#/world",
        "responses": {**_world_base(), **_place_base(), **_chat_base()},
        "driver": (
            "await __settle(); "
            "__wsOpen(); __wsMessage({type:'hello', cursor: 500}); await __settle(); "
            "__setHash('#/location/7'); await __settle(); "
            "if (!__S.ws || __S.ws.readyState !== 1) __wsOpen(); "
            "__wsMessage({type:'events', events:[{id:501, event_type:'ITEM_CONSUMED', "
            "actor_id:2, game_timestamp:1500, text:'Иван съел хлеб'}]}); "
            "await __settle(); "
            "const f1 = __facts(); "
            "__setHash('#/chat'); await __settle(); "
            "__setHash('#/location/7'); await __settle(); "
            "const f2 = __facts(); "
            "globalThis.__result = { f1, f2 };"
        ),
    },
    # ---------- 07. chat_quiet_indicator (RED) ----------
    {
        "name": "CHAT_QUIET_INDICATOR",
        "initialHash": "#/world",
        "responses": {**_world_base(), **_chat_base()},
        "driver": (
            "await __settle(); "
            "__wsOpen(); __wsMessage({type:'hello', cursor: 500}); await __settle(); "
            "__setHash('#/chat'); await __settle(); "
            "const input = document.querySelector('#chat-msg-input'); "
            "if (input) { input.value = 'привет'; input.focus(); } "
            "if (!__S.ws || __S.ws.readyState !== 1) __wsOpen(); "
            "__wsMessage({type:'events', events:[{id:501, event_type:'ITEM_CONSUMED', "
            "actor_id:2, game_timestamp:1500, text:'Иван съел хлеб'}]}); "
            "await __settle(); "
            "const after = __facts(); "
            "globalThis.__result = { after };"
        ),
    },
    # ---------- 09. teardown_reset (RED) ----------
    {
        "name": "TEARDOWN_RESET",
        "initialHash": "#/world",
        "responses": _world_base(),
        "driver": (
            "await __settle(); "
            "__wsOpen(); __wsMessage({type:'hello', cursor: 500}); await __settle(); "
            "__wsMessage({type:'events', events:[{id:501, event_type:'ITEM_CONSUMED', "
            "actor_id:2, game_timestamp:1500, text:'x'}]}); "
            "await __settle(); "
            "__call('resetGameUiContext'); "
            "await __settle(); "
            "const after = __facts(); "
            "const reconnectPending = __findTimeout(2000) != null; "
            "globalThis.__result = { after, reconnectPending };"
        ),
    },
    # ---------- 10. teardown_401 (RED) ----------
    {
        "name": "TEARDOWN_401",
        "initialHash": "#/world",
        "responses": _world_base(),
        "driver": (
            "await __settle(); "
            "__wsOpen(); __wsMessage({type:'hello', cursor: 500}); await __settle(); "
            "__wsClose(); await __settle(); "
            "__setResponse('/world/events?since=400', "
            "{status: 401, body: {detail: 'unauthorized'}}); "
            "__fireInterval(__S.pollTimer); "
            "await __settle(); "
            "const after = __facts(); "
            "const reconnectPending = __findTimeout(2000) != null; "
            "globalThis.__result = { after, reconnectPending };"
        ),
    },
    # ---------- 11. stale_reconnect_inert (RED) ----------
    {
        "name": "STALE_RECONNECT_INERT",
        "initialHash": "#/world",
        "responses": {**_world_base(), **_place_base()},
        "driver": (
            "await __settle(); "
            "__wsOpen(); __wsMessage({type:'hello', cursor: 500}); await __settle(); "
            "__wsClose(); await __settle(); "
            "const reconnectId = __findTimeout(2000); "
            "__setHash('#/location/7'); await __settle(); "
            "let fired = false; "
            "if (reconnectId != null) fired = __fireTimeout(reconnectId); "
            "await __settle(); "
            "const after = __facts(); "
            "globalThis.__result = { after, reconnectId, fired };"
        ),
    },
    # ---------- 12. stale_poll_inert (RED) ----------
    {
        "name": "STALE_POLL_INERT",
        "initialHash": "#/world",
        "responses": {**_world_base(), **_place_base()},
        "driver": (
            "await __settle(); "
            "__wsOpen(); __wsMessage({type:'hello', cursor: 500}); await __settle(); "
            "__wsClose(); await __settle(); "
            "__hold('/world/events?since=400'); "
            "__fireInterval(__S.pollTimer); "
            "await __settle(); "
            "__setHash('#/location/7'); await __settle(); "
            "__release('/world/events?since=400', {status: 200, body: "
            "[{id: 501, event_type: 'ITEM_CONSUMED', actor_id: 2, "
            "game_timestamp: 1500, text: 'x'}]}); "
            "await __settle(); "
            "const after = __facts(); "
            "globalThis.__result = { after };"
        ),
    },
    # ---------- 13. old_poll_401_harmless (RED) ----------
    {
        "name": "OLD_POLL_401_HARMLESS",
        "initialHash": "#/world",
        "responses": {**_world_base(), **{"/characters/2": _resp(200, CHAR2)}},
        "driver": (
            "await __settle(); "
            "__wsOpen(); __wsMessage({type:'hello', cursor: 500}); await __settle(); "
            "__wsClose(); await __settle(); "
            "__hold('/world/events?since=400'); "
            "__fireInterval(__S.pollTimer); "
            "await __settle(); "
            "__S.authProbed = true; __S.user = null; __S.character = null; "
            "__setHash('#/chat'); await __settle(); "
            "const USER_B = {id:2, username:'u2', email:'e2@x', role:'player', "
            "sessionTag:'B'}; "
            "const CHAR2 = {id:2, name:'P', location_id:7, needs:{}, "
            "control_mode:'MANUAL', is_owner:false, money:100}; "
            "__S.user = USER_B; __S.character = CHAR2; "
            "__setHash('#/world'); await __settle(); "
            "if (!__S.ws || __S.ws.readyState !== 1) __wsOpen(); "
            "await __settle(); "
            "const before = __facts(); "
            "__release('/world/events?since=400', "
            "{status: 401, body: {detail: 'unauthorized'}}); "
            "await __settle(); "
            "const after = __facts(); "
            "globalThis.__result = { before, after };"
        ),
    },
    # ---------- 08. fresh_place_return (PRESERVATION) ----------
    {
        "name": "FRESH_PLACE_RETURN",
        "initialHash": "#/world",
        "responses": {**_world_base(), **_place_base(), **_chat_base()},
        "driver": (
            "await __settle(); "
            "__wsOpen(); __wsMessage({type:'hello', cursor: 500}); await __settle(); "
            "__setHash('#/location/7'); await __settle(); "
            "const f1 = __facts(); "
            "__setResponse('/world', {status: 200, body: "
            "{game_timestamp: 1501, day: 2}}); "
            "__setHash('#/chat'); await __settle(); "
            "__setHash('#/location/7'); await __settle(); "
            "const f2 = __facts(); "
            "globalThis.__result = { f1, f2 };"
        ),
    },
    # ---------- 14. handshake_matrix (PRESERVATION, multi-phase) ----------
    {
        "name": "HANDSHAKE_MATRIX",
        "phases": [
            # phase 0: hello cursor math + sendWsCursor gating
            {
                "initialHash": "#/world",
                "responses": _world_base(),
                "driver": (
                    "await __settle(); "
                    "__wsOpen(); __wsMessage({type:'hello', cursor: 500}); "
                    "await __settle(); "
                    "globalThis.__result = __facts();"
                ),
            },
            # phase 1: delayed-hello fallback (5s deadline, no hello)
            {
                "initialHash": "#/world",
                "responses": _world_base(),
                "driver": (
                    "await __settle(); "
                    "__wsOpen(); "
                    "const t = __findTimeout(5000); "
                    "if (t != null) __fireTimeout(t); "
                    "await __settle(); "
                    "globalThis.__result = __facts();"
                ),
            },
            # phase 2: close-before-hello
            {
                "initialHash": "#/world",
                "responses": _world_base(),
                "driver": (
                    "await __settle(); "
                    "__wsClose(); "
                    "await __settle(); "
                    "globalThis.__result = __facts();"
                ),
            },
            # phase 3: reconnect backoff 2000*wsTries / 3 tries
            {
                "initialHash": "#/world",
                "responses": _world_base(),
                "driver": (
                    "await __settle(); "
                    "__wsClose(); await __settle(); "
                    "let t = __findTimeout(2000); if (t != null) __fireTimeout(t); "
                    "await __settle(); "
                    "__wsClose(); await __settle(); "
                    "t = __findTimeout(4000); if (t != null) __fireTimeout(t); "
                    "await __settle(); "
                    "__wsClose(); await __settle(); "
                    "t = __findTimeout(6000); if (t != null) __fireTimeout(t); "
                    "await __settle(); "
                    "__wsClose(); await __settle(); "
                    "const f = __facts(); "
                    "f.pendingReconnect = [2000, 4000, 6000].filter("
                    "(ms) => __findTimeout(ms) != null); "
                    "globalThis.__result = f;"
                ),
            },
        ],
    },
    # ---------- 15. rest_fallback (PRESERVATION) ----------
    {
        "name": "REST_FALLBACK",
        "initialHash": "#/world",
        "responses": _world_base(),
        "driver": (
            "await __settle(); "
            "__wsClose(); await __settle(); "
            "__setResponse('/world/events?since=0', {status: 200, body: "
            "[{id:1, event_type:'ITEM_CONSUMED', actor_id:2, game_timestamp:1498, "
            "text:'a'}, {id:2, event_type:'ITEM_CONSUMED', actor_id:2, "
            "game_timestamp:1499, text:'b'}, {id:3, event_type:'ITEM_CONSUMED', "
            "actor_id:2, game_timestamp:1500, text:'c'}]}); "
            "__fireInterval(__S.pollTimer); await __settle(); "
            "const f1 = __facts(); "
            "__setResponse('/world/events?since=3', {status: 200, body: "
            "[{id:4, event_type:'ITEM_CONSUMED', actor_id:2, game_timestamp:1500, "
            "text:'d'}, {id:5, event_type:'ITEM_CONSUMED', actor_id:2, "
            "game_timestamp:1500, text:'e'}]}); "
            "__fireInterval(__S.pollTimer); await __settle(); "
            "const f2 = __facts(); "
            "globalThis.__result = { f1, f2 };"
        ),
    },
    # ---------- 16. replay_coalescing (PRESERVATION) ----------
    {
        "name": "REPLAY_COALESCING",
        "initialHash": "#/world",
        "responses": _world_base(),
        "driver": (
            "await __settle(); "
            "__wsOpen(); __wsMessage({type:'hello', cursor: 500}); await __settle(); "
            "const worldGets0 = __facts().worldGets; "
            "__wsMessage({type:'events', events:[{id:501, event_type:'ITEM_CONSUMED', "
            "actor_id:2, game_timestamp:1500, text:'a'}]}); "
            "await __settle(); "
            "__wsMessage({type:'events', events:[{id:501, event_type:'ITEM_CONSUMED', "
            "actor_id:2, game_timestamp:1500, text:'a'}, {id:502, "
            "event_type:'ITEM_CONSUMED', actor_id:2, game_timestamp:1500, text:'b'}, "
            "{id:503, event_type:'ITEM_CONSUMED', actor_id:2, game_timestamp:1500, "
            "text:'c'}]}); "
            "await __settle(); "
            "const f1 = __facts(); "
            "__wsMessage({type:'events', events:[{id:510, event_type:'CHARACTER_MOVED', "
            "actor_id:1, game_timestamp:1500, payload:{character_id:1}}, "
            "{id:511, event_type:'CHARACTER_MOVED', actor_id:1, game_timestamp:1500, "
            "payload:{character_id:1}}]}); "
            "await __settle(); "
            "const f2 = __facts(); "
            "globalThis.__result = { f1, f2, worldGets0, worldGets1: f2.worldGets };"
        ),
    },
    # ---------- 17. events_isolation (PRESERVATION) ----------
    {
        "name": "EVENTS_ISOLATION",
        "initialHash": "#/world",
        "responses": {
            **_world_base(),
            **{"/world/events?since=0&limit=100": _resp(200, [HIST_EVENT])},
        },
        "driver": (
            "await __settle(); "
            "__wsOpen(); __wsMessage({type:'hello', cursor: 500}); await __settle(); "
            "__wsMessage({type:'events', events:[{id:501, event_type:'ITEM_CONSUMED', "
            "actor_id:2, game_timestamp:1500, text:'shared-event'}]}); "
            "await __settle(); "
            "__setHash('#/events'); await __settle(); "
            "const f1 = __facts(); "
            "globalThis.__result = { f1 };"
        ),
    },
    # ---------- 18. stale_nav_poll_401_harmless (RED) ----------
    {
        "name": "STALE_NAV_POLL_401_HARMLESS",
        "initialHash": "#/world",
        "responses": {**_world_base(), **_place_base()},
        "driver": (
            "await __settle(); "
            "__wsOpen(); __wsMessage({type:'hello', cursor: 500}); await __settle(); "
            "__wsClose(); await __settle(); "
            "__hold('/world/events?since=400'); "
            "__fireInterval(__S.pollTimer); "
            "await __settle(); "
            "const authProbedBefore = __S.authProbed; "
            "__setHash('#/location/7'); await __settle(); "
            "__release('/world/events?since=400', "
            "{status: 401, body: {detail: 'unauthorized'}}); "
            "await __settle(); "
            "const after = __facts(); "
            "const authProbedAfter = __S.authProbed; "
            "globalThis.__result = { after, authProbedBefore, authProbedAfter };"
        ),
    },
]


def _run_harness():
    os.makedirs(SCRATCH, exist_ok=True)
    harness_path = os.path.join(SCRATCH, "m311_harness.mjs")
    scen_path = os.path.join(SCRATCH, "m311_scenarios.json")
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
# RED on base: 01-07, 09-13 (the controller does not exist yet).
# PRESERVATION (must pass on base): 08, 14-17.


# ---------- 01. controller_api (RED) ----------

def test_01_controller_api(results):
    r = _facts(results, "CONTROLLER_API")
    assert r["hasController"] is True, (
        "worldFeedController must exist in app.js (session-owned feed owner)"
    )
    api = r["api"]
    assert api["start"] == "function", f"start() missing: {api!r}"
    assert api["subscribe"] == "function", f"subscribe() missing: {api!r}"
    assert api["stop"] == "function", f"stop() missing: {api!r}"
    assert api["teardown"] == "function", f"teardown() missing: {api!r}"
    # idempotent start: a double start must not stack a second WS or interval
    idem = r["idem"]
    assert idem["wsAfter"] == idem["wsBefore"], (
        f"double start() must not open a second WS: {idem!r}"
    )
    assert idem["intervals"] == 1, (
        f"double start() must not stack a second poll interval: {idem!r}"
    )
    unhandled = _unhandled(results, "CONTROLLER_API")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 02. cross_route_event_once (RED) ----------

def test_02_cross_route_event_once(results):
    r = _facts(results, "CROSS_ROUTE_EVENT_ONCE")
    f = r["f"]
    # one WS event delivered to the active view exactly once
    assert f["feed"].count(501) == 1, (
        f"the event must be buffered exactly once, got {f['feed']!r}"
    )
    assert "Иван съел хлеб" in (f["feedDom"] or ""), (
        f"the active view renders the delivered event, got {f['feedDom']!r}"
    )
    # a new subscription must NOT replay buffered events as notifications
    assert r["replayed"] == [], (
        f"re-subscribing must not replay buffered events, got {r['replayed']!r}"
    )
    unhandled = _unhandled(results, "CROSS_ROUTE_EVENT_ONCE")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 03. resource_bound_across_nav (RED) ----------

def test_03_resource_bound_across_nav(results):
    r = _facts(results, "RESOURCE_BOUND_ACROSS_NAV")
    steps = r["steps"]
    assert [s["label"] for s in steps] == [
        "world0", "place1", "chat1", "place2", "world1"], (
        f"unexpected step labels: {[s['label'] for s in steps]!r}"
    )
    for s in steps:
        running = (s["ws"] is not None and s["ws"]["readyState"] == 1) \
            or s["pollTimer"]
        assert running, (
            f"the feed must keep running on every screen "
            f"(WS open or poll armed), step {s['label']!r}: {s!r}"
        )
        assert s["intervals"] <= 1, (
            f"at most one poll interval may exist, step {s['label']!r}: {s!r}"
        )
    unhandled = _unhandled(results, "RESOURCE_BOUND_ACROSS_NAV")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 04. no_nav_burst (RED) ----------

def test_04_no_nav_burst(results):
    r = _facts(results, "NO_NAV_BURST")
    assert r["delta"] == 0, (
        f"world→place→chat→world must cause NO feed-owned GET re-arm burst "
        f"(WS is the live channel), got {r['delta']} extra feed GETs "
        f"(before={r['before']}, after={r['after']})"
    )
    unhandled = _unhandled(results, "NO_NAV_BURST")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 05. cold_nonworld_entry (RED) ----------

def test_05_cold_nonworld_entry(results):
    r = _facts(results, "COLD_NONWORLD_ENTRY")
    f = r["f"]
    assert f["hasPlaceShell"] is True, "the deep-linked place renders"
    started = (f["ws"] is not None) or f["pollTimer"]
    assert started, (
        f"a cold non-World entry must start the feed controller "
        f"(WS connect attempt or poll scheduled), got ws={f['ws']!r} "
        f"pollTimer={f['pollTimer']!r}"
    )
    unhandled = _unhandled(results, "COLD_NONWORLD_ENTRY")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 06. place_quiet_indicator (RED) ----------

def test_06_place_quiet_indicator(results):
    r = _facts(results, "PLACE_QUIET_INDICATOR")
    f1, f2 = r["f1"], r["f2"]
    # on place, a WS event renders the quiet line (no modal, no route change)
    assert f1["placeQuiet"] is not None, (
        "a WS event on the Place screen must render the quiet line "
        "«В мире произошло…» in the context panel"
    )
    assert "В мире произошло" in f1["placeQuiet"], (
        f"the quiet line is the «В мире произошло…» phrase, got {f1['placeQuiet']!r}"
    )
    assert f1["hash"] == "#/location/7", (
        f"the quiet line must not change the route, got {f1['hash']!r}"
    )
    # on place re-entry the line resets (no stale notice)
    assert f2["placeQuiet"] is None, (
        f"the quiet line must reset on place re-entry, got {f2['placeQuiet']!r}"
    )
    unhandled = _unhandled(results, "PLACE_QUIET_INDICATOR")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 07. chat_quiet_indicator (RED) ----------

def test_07_chat_quiet_indicator(results):
    r = _facts(results, "CHAT_QUIET_INDICATOR")
    f = r["after"]
    # on chat, a WS event shows an unobtrusive indicator
    assert f["chatQuiet"] is not None, (
        "a WS event on the Chat screen must show a muted status indicator "
        "near the heading in #chat-view"
    )
    # …never touching draft / focus / send
    assert f["chatInputValue"] == "привет", (
        f"the chat draft must be untouched, got {f['chatInputValue']!r}"
    )
    assert f["chatInputFocused"] is True, (
        "the chat input focus must be retained"
    )
    unhandled = _unhandled(results, "CHAT_QUIET_INDICATOR")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 09. teardown_reset (RED) ----------

def test_09_teardown_reset(results):
    r = _facts(results, "TEARDOWN_RESET")
    f = r["after"]
    assert f["ws"] is None, (
        f"resetGameUiContext must close the WS, got {f['ws']!r}"
    )
    assert f["pollTimer"] is False, (
        "resetGameUiContext must clear the poll interval"
    )
    assert r["reconnectPending"] is False, (
        "resetGameUiContext must cancel the pending reconnect timer"
    )
    assert f["feed"] == [], (
        f"resetGameUiContext must clear the feed buffer, got {f['feed']!r}"
    )
    assert f["cursor"] == 0, (
        f"resetGameUiContext must reset the cursor, got {f['cursor']!r}"
    )
    assert f["feedInit"] == "pending", (
        f"resetGameUiContext must reset feedInit, got {f['feedInit']!r}"
    )
    assert f["feedFetched"] is False, (
        "resetGameUiContext must reset feedFetched"
    )
    assert f["bannerVisible"] is False, (
        "resetGameUiContext must clear the connection banner"
    )
    unhandled = _unhandled(results, "TEARDOWN_RESET")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 10. teardown_401 (RED) ----------

def test_10_teardown_401(results):
    r = _facts(results, "TEARDOWN_401")
    f = r["after"]
    # a 401 during feed operation deauths (global fail-closed)…
    assert f["user"] is None, (
        f"a 401 must deauthenticate the session, user={f['user']!r}"
    )
    # …and performs the destructive feed teardown: no zombie WS/poll/reconnect
    assert f["ws"] is None, (
        f"no zombie WS after a 401 teardown, got {f['ws']!r}"
    )
    assert f["pollTimer"] is False, (
        "no zombie poll interval after a 401 teardown"
    )
    assert r["reconnectPending"] is False, (
        "the pending reconnect timer must be cancelled by the 401 teardown"
    )
    unhandled = _unhandled(results, "TEARDOWN_401")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 11. stale_reconnect_inert (RED) ----------

def test_11_stale_reconnect_inert(results):
    r = _facts(results, "STALE_RECONNECT_INERT")
    f = r["after"]
    # the reconnect timer was armed before navigation…
    assert r["reconnectId"] is not None, (
        "the reconnect timer must have been armed by the WS close"
    )
    # …and navigation tore the feed down; firing the stale timer must be inert
    assert f["wsConnects"] == 1, (
        f"firing a stale reconnect timer after teardown must NOT open a new "
        f"WS (token check rejects it), got {f['wsConnects']} connects"
    )
    unhandled = _unhandled(results, "STALE_RECONNECT_INERT")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 12. stale_poll_inert (RED) ----------

def test_12_stale_poll_inert(results):
    r = _facts(results, "STALE_POLL_INERT")
    f = r["after"]
    # a poll that resolves late (after teardown) must publish nothing
    assert f["feed"] == [], (
        f"a stale poll resolving after teardown must not mutate the feed, "
        f"got {f['feed']!r}"
    )
    assert f["cursor"] == 400, (
        f"a stale poll resolving after teardown must not advance the cursor, "
        f"got {f['cursor']!r}"
    )
    unhandled = _unhandled(results, "STALE_POLL_INERT")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 13. old_poll_401_harmless (RED) ----------

def test_13_old_poll_401_harmless(results):
    r = _facts(results, "OLD_POLL_401_HARMLESS")
    before, after = r["before"], r["after"]
    # the new session (B) is live before the stale poll resolves…
    assert before["user"] == 2, (
        f"the new session must be authenticated, user={before['user']!r}"
    )
    assert before["ws"] is not None, (
        "the new session's feed must be running (WS open)"
    )
    # …a stale poll resolving 401 must NOT tear down the new session
    assert after["user"] == 2, (
        f"a stale poll 401 must not deauthenticate the NEW session, "
        f"user={after['user']!r}"
    )
    assert after["ws"] is not None, (
        "a stale poll 401 must not close the NEW session's WS"
    )
    unhandled = _unhandled(results, "OLD_POLL_401_HARMLESS")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 08. fresh_place_return (PRESERVATION) ----------

def test_08_fresh_place_return(results):
    r = _facts(results, "FRESH_PLACE_RETURN")
    f1, f2 = r["f1"], r["f2"]
    # the first visit shows the original world time
    assert f1["stageHeadingText"] is not None \
        and "01:00" in f1["stageHeadingText"], (
        f"the first place visit shows the original time, "
        f"got {f1['stageHeadingText']!r}"
    )
    # leaving and returning re-fetches the read model (fresh view GET)…
    assert f2["locGets"] > f1["locGets"], (
        f"returning to the place must re-fetch the catalog (fresh view GET), "
        f"locGets {f1['locGets']} -> {f2['locGets']}"
    )
    # …and presents the fresh snapshot, not a stale one
    assert f2["stageHeadingText"] is not None \
        and "01:01" in f2["stageHeadingText"], (
        f"the returned place must show the fresh world time, "
        f"got {f2['stageHeadingText']!r}"
    )
    unhandled = _unhandled(results, "FRESH_PLACE_RETURN")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 14. handshake_matrix (PRESERVATION, multi-phase) ----------

def test_14_handshake_matrix(results):
    phases = _facts(results, "HANDSHAKE_MATRIX")
    assert len(phases) == 4, f"expected 4 phases, got {len(phases)}"
    # phase 0: hello cursor math + sendWsCursor gating
    f0 = phases[0]["result"]
    assert f0["cursor"] == 400, (
        f"fresh hello cursor=500 -> S.cursor=max(0,500-100)=400, "
        f"got {f0['cursor']!r}"
    )
    assert f0["bootCursor"] == 500, (
        f"the boot replay marker is the hello TAIL (500), got {f0['bootCursor']!r}"
    )
    assert f0["feedInit"] == "done", (
        f"a fresh hello settles feedInit to 'done', got {f0['feedInit']!r}"
    )
    assert f0["feedFetched"] is True, "a fresh hello sets feedFetched"
    cursor_frames = [fr for fr in f0["wsFrames"] if '"cursor"' in fr]
    assert len(cursor_frames) == 1, (
        f"sendWsCursor fires exactly once per connection, "
        f"got {f0['wsFrames']!r}"
    )
    assert '"id":400' in cursor_frames[0], (
        f"the cursor handshake declares the current cursor (400), "
        f"got {cursor_frames[0]!r}"
    )
    # phase 1: delayed-hello fallback (5s deadline, no hello)
    f1 = phases[1]["result"]
    assert f1["feedInit"] == "rest", (
        f"a hello-less socket past the 5s deadline falls back to REST, "
        f"got {f1['feedInit']!r}"
    )
    assert f1["ws"] is None, (
        "the hello-less socket is retired (S.ws cleared)"
    )
    # phase 2: close-before-hello
    f2 = phases[2]["result"]
    assert f2["feedInit"] == "rest", (
        f"a close before hello falls back to REST, got {f2['feedInit']!r}"
    )
    assert f2["ws"] is None, "a closed socket clears S.ws"
    # phase 3: reconnect backoff 2000*wsTries / 3 tries
    f3 = phases[3]["result"]
    assert f3["wsConnects"] == 4, (
        f"3 reconnect tries after the initial connect = 4 sockets, "
        f"got {f3['wsConnects']!r}"
    )
    assert f3["wsTries"] == 4, (
        f"wsTries increments past 3 and stops reconnecting, "
        f"got {f3['wsTries']!r}"
    )
    assert f3["pendingReconnect"] == [], (
        f"no reconnect timer may remain after the 3rd try, "
        f"got {f3['pendingReconnect']!r}"
    )
    unhandled = _unhandled(results, "HANDSHAKE_MATRIX")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 15. rest_fallback (PRESERVATION) ----------

def test_15_rest_fallback(results):
    r = _facts(results, "REST_FALLBACK")
    f1, f2 = r["f1"], r["f2"]
    assert f1["feedInit"] == "rest", (
        f"WS unavailable -> REST fallback, got {f1['feedInit']!r}"
    )
    assert f1["feed"] == [1, 2, 3], (
        f"the REST poll delivers events, got {f1['feed']!r}"
    )
    assert f1["cursor"] == 3, (
        f"the REST cursor advances to the last id, got {f1['cursor']!r}"
    )
    assert f2["feed"] == [1, 2, 3, 4, 5], (
        f"the REST poll keeps delivering (deduped), got {f2['feed']!r}"
    )
    assert f2["cursor"] == 5, (
        f"the REST cursor is monotonic, got {f2['cursor']!r}"
    )
    unhandled = _unhandled(results, "REST_FALLBACK")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 16. replay_coalescing (PRESERVATION) ----------

def test_16_replay_coalescing(results):
    r = _facts(results, "REPLAY_COALESCING")
    f1, f2 = r["f1"], r["f2"]
    # replay of several events > cursor arrives ascending, deduped by id
    assert f1["feed"] == [501, 502, 503], (
        f"replay is deduped by id and kept ascending, got {f1['feed']!r}"
    )
    assert f1["cursor"] == 503, (
        f"the cursor advances to the replay tail, got {f1['cursor']!r}"
    )
    assert f2["feed"] == [501, 502, 503, 510, 511], (
        f"subsequent live events append, got {f2['feed']!r}"
    )
    assert f2["cursor"] == 511, (
        f"the cursor advances to the live tail, got {f2['cursor']!r}"
    )
    # own-arrival coalescing: one frame with 2 own-move events -> 1 re-render
    assert r["worldGets1"] - r["worldGets0"] == 1, (
        f"a frame with 2 own-move events must coalesce to ONE world "
        f"re-render, got {r['worldGets1'] - r['worldGets0']} extra /world GETs"
    )
    unhandled = _unhandled(results, "REPLAY_COALESCING")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 17. events_isolation (PRESERVATION) ----------

def test_17_events_isolation(results):
    r = _facts(results, "EVENTS_ISOLATION")
    f = r["f1"]
    # the Events screen does its own bounded historical fetch…
    assert f["eventsDisclosure"] is True, (
        "the Events screen renders its own bounded sample (m241/#76)"
    )
    assert any(u == "/world/events?since=0&limit=100" for u in f["sampleGets"]), (
        f"the Events screen fetches its own bounded sample, "
        f"got {f['sampleGets']!r}"
    )
    # …and does not touch the shared feed state
    assert f["feed"] == [501], (
        f"the Events screen must not modify the shared feed, got {f['feed']!r}"
    )
    assert f["cursor"] == 501, (
        f"the Events screen must not write the shared cursor, got {f['cursor']!r}"
    )
    # …and renders its own sample, not the shared feed's content
    assert "historical-100" in (f["appText"] or ""), (
        "the Events screen shows its own historical sample"
    )
    assert "shared-event" not in (f["appText"] or ""), (
        "the Events screen must not render the shared live feed's content"
    )
    unhandled = _unhandled(results, "EVENTS_ISOLATION")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 18. stale_nav_poll_401_harmless (RED) ----------

def test_18_stale_nav_poll_401_harmless(results):
    r = _facts(results, "STALE_NAV_POLL_401_HARMLESS")
    f = r["after"]
    # a poll held across a navigation (SAME user, no identity change) that
    # resolves 401 must NOT deauthenticate the live session
    assert f["user"] is not None, (
        f"a stale same-user poll 401 must not deauthenticate the live "
        f"session, user={f['user']!r}"
    )
    # …and must not flip the fail-closed authProbed marker
    assert r["authProbedAfter"] is False, (
        f"a stale same-user poll 401 must not set authProbed, "
        f"got {r['authProbedAfter']!r}"
    )
    # …and the feed transport must stay alive (the REST poll is still armed)
    assert f["pollTimer"] is True, (
        "a stale same-user poll 401 must not tear down the feed transport"
    )
    # …and must not reroute (no error UI / no kick to login)
    assert f["hash"] == "#/location/7", (
        f"a stale same-user poll 401 must not reroute, got {f['hash']!r}"
    )
    unhandled = _unhandled(results, "STALE_NAV_POLL_401_HARMLESS")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"
