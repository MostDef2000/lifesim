"""#305 — NPC dialogue inside a place, with draft and source context.

Behavioral tests for the in-place NPC dialogue of the REAL production
``backend/app/web/app.js`` (boot included), run inside a Node ``vm``
context using the embedded stdlib harness below. The harness is an OWN
copy of the m311/m304 vm-harness pattern — the same minimal DOM /
fake-timer / controlled-network / fake-WebSocket shim — extended
additively with the #305 surface:

- chat facts (composer value/focus/marker, send-button disabled state,
  NPC select value, presence caption, message rows with fallback marks,
  relationship banners, initials node, back anchor);
- the dialogue census (POST /dialogue/start, POST /dialogue/{id}/message,
  GET /dialogue/{id}, portrait GETs, /visual/scenes POSTs);
- typeof-guarded accessors for the #305 runtime (draftByNpc,
  chatSourceLocationId/Name) — null on a base app.js that lacks them
  (RED authenticity).

The #305 presence state machine / departed carry / revalidation /
race guards / initials do NOT exist in app.js yet. The NEW-behavior
scenarios MUST fail on base; the behavior-preservation scenarios MUST
pass on base (they guard the refactor).
"""

import json
import os
import subprocess

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
APP_JS = os.path.join(ROOT, "backend/app/web/app.js")
SCRATCH = "/tmp/opencode/m305-testing"


HARNESS_JS = r"""
// #305 — in-place NPC dialogue with draft + source context: RED harness.
// Real production app.js in a Node vm with fake DOM/fetch/timers/WebSocket.
// OWN copy of the m312/m304 vm-harness pattern extended with:
//   - chat-focused __facts() (composer value/focus/marker, send-button
//     disabled state, NPC select value, presence caption, message rows
//     with fallback marks, relationship banners, initials node, back
//     anchor, dialogue/portrait/scene network census);
//   - typeof-guarded __draftByNpc() / __chatSourceLocationId() /
//     __chatSourceLocationName() accessors (null on base app.js — RED
//     authenticity: the #305 runtime does not exist yet);
//   - __markComposer() — stamps the live composer node so a scenario can
//     prove the poll revalidation did NOT rebuild the composer.
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
// in-memory source only — the production file is untouched. The #305
// dialogue-runtime bindings are typeof-guarded so the harness stays
// loadable against a base app.js that lacks the #305 runtime (RED
// authenticity: the presence machine / source capture do not exist yet).
// The drive functions (route/viewWorld/viewLocation/viewChat) are
// top-level function declarations, reachable via __call.
const EXPOSE = [
  "\n;globalThis.__S = S; globalThis.__app = app; globalThis.__routes = routes;",
  "globalThis.__draftByNpc = () => " +
  "(typeof gameUiContext !== 'undefined' ? gameUiContext.draftByNpc : null);",
  "globalThis.__chatSourceLocationId = () => " +
  "(typeof gameUiContext !== 'undefined' " +
  "? gameUiContext.chatSourceLocationId : null);",
  "globalThis.__chatSourceLocationName = () => " +
  "(typeof gameUiContext !== 'undefined' " +
  "? gameUiContext.chatSourceLocationName : null);",
  // #305: driver setters — simulate a stale carry / a stored per-NPC draft
  // AFTER the boot (resetGameUiContext replaces the object, so these must
  // run post-boot). They mutate the live gameUiContext binding.
  "globalThis.__setCarryNpc = (id) => { gameUiContext.npcId = id; };",
  "globalThis.__setDraft = (npcId, text) => { " +
  "gameUiContext.draftByNpc[npcId] = text; };",
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
  // #305: stamp the live composer node so a scenario can prove the poll
  // revalidation did NOT rebuild the composer (a rebuilt node loses the
  // marker; the fact chatInputMarker reads it back).
  function __markComposer() {
    const input = document.getElementById("chat-msg-input");
    if (input) { input._m305Marker = "sentinel"; return true; }
    return false;
  }
  // #305: set the NPC select value + fire its change listener (the app's
  // per-NPC draft snapshot/restore rides the change event).
  function __selectNpc(value) {
    const chatView = document.getElementById("chat-view");
    if (!chatView) return false;
    const sel = findDescendant(chatView, (n) => n.tagName === "SELECT");
    if (!sel) return false;
    sel.value = value;
    (sel._listeners["change"] || []).slice().forEach(fn => fn({ target: sel }));
    return true;
  }
  // #305: type into the composer + fire its input listener (the app's
  // draft-sync rides the input event).
  function __typeChat(text) {
    const input = document.getElementById("chat-msg-input");
    if (!input) return false;
    input.value = text;
    (input._listeners["input"] || []).slice().forEach(fn => fn({ target: input }));
    return true;
  }
  // #305: press Enter in the composer (the app's send-on-Enter rides keydown).
  function __chatEnter() {
    const input = document.getElementById("chat-msg-input");
    if (!input) return false;
    (input._listeners["keydown"] || []).slice().forEach(fn =>
      fn({ key: "Enter", target: input, preventDefault() {} }));
    return true;
  }
  // #305: click the send button (the app's send rides the button onclick).
  function __chatSendClick() {
    const btn = document.getElementById("chat-send");
    if (!btn) return false;
    btn.click();
    return true;
  }
  // #305: click the Place view's «Поговорить» button in the nearby row whose
  // text contains the given NPC name (the buttons all share the same label,
  // so the row's name disambiguates).
  function __clickPlaceTalk(name) {
    const rows = document.querySelectorAll(".place-nearby-row");
    for (const row of rows) {
      if (row.textContent.indexOf(name) >= 0) {
        const btn = findDescendant(row, (n) => n.tagName === "BUTTON");
        if (btn) { btn.click(); return true; }
      }
    }
    return false;
  }
  // #305: fire the chat portrait img's error listener (image decode failure).
  // Returns true iff a listener was present (the #305 decode-fallback path);
  // base app.js attaches none, so this returns false there (RED authenticity).
  function __fireChatImgError() {
    const visual = document.getElementById("chat-visual");
    if (!visual) return false;
    const img = findDescendant(visual, (n) => n.tagName === "IMG");
    if (!img) return false;
    const l = img._listeners["error"] || [];
    l.slice().forEach((fn) => fn({ type: "error", target: img }));
    return l.length > 0;
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
    // ---------- #305: the chat (NPC dialogue) facts ----------
    const chatSendBtn = document.getElementById("chat-send");
    const chatStatusEl = document.getElementById("chat-status");
    const chatLogEl = document.getElementById("chat-log");
    const chatVisualEl = document.getElementById("chat-visual");
    const chatRelNoteEl = document.getElementById("chat-rel-note");
    const chatNpcNameEl = document.getElementById("chat-npc-name");
    const chatNpcSelect = chatView
      ? findDescendant(chatView, (n) => n.tagName === "SELECT") : null;
    const chatBackAnchor = chatView
      ? findDescendant(chatView, (n) => n.tagName === "A") : null;
    // the presence caption — the availability line. Captured by text so the
    // test pins the player-visible wording («NPC не рядом» for departed).
    let presenceCaption = null;
    if (chatView) {
      const n = findDescendant(chatView, (n) => n.nodeType === 1
        && n.textContent.indexOf("не рядом") >= 0);
      if (n) presenceCaption = n.textContent;
    }
    // message rows — the .chatmsg nodes in the log, with the fallback mark.
    const messageRows = chatLogEl
      ? chatLogEl.querySelectorAll(".chatmsg").map((n) => ({
          text: n.textContent,
          fallbackMarked: n.textContent.indexOf("LLM недоступен — fallback") >= 0,
        }))
      : [];
    // the initials node (new design) — a dedicated element inside the visual.
    const initialsEl = chatVisualEl
      ? findDescendant(chatVisualEl, (n) => n.nodeType === 1
        && String(n.className || "").indexOf("initials") >= 0)
      : null;
    // the visual fallback text node (the honest «Визуал недоступен…» line).
    const visualFallbackEl = chatVisualEl
      ? findDescendant(chatVisualEl, (n) => n.nodeType === 1
        && n.textContent.indexOf("Визуал недоступен") >= 0)
      : null;
    const chatMilestoneEl = chatLogEl
      ? chatLogEl.querySelector(".chat-milestone") : null;
    const chatBandChangeEl = chatLogEl
      ? chatLogEl.querySelector(".chat-band-change") : null;
    const chatErrorRowCount = chatLogEl
      ? chatLogEl.querySelectorAll(".chat-error").length : 0;
    // the relationship banner text — the milestone / band-change rows.
    const relationshipBannerText = [
      chatMilestoneEl ? chatMilestoneEl.textContent : null,
      chatBandChangeEl ? chatBandChangeEl.textContent : null,
    ].filter(Boolean).join(" | ");
    // the dialogue / portrait / scene network census.
    const dialogueStartCount = __net.log.filter(
      e => (e.opts.method || "GET").toUpperCase() === "POST"
        && e.url === "/dialogue/start").length;
    const dialogueMessageCount = __net.log.filter(
      e => (e.opts.method || "GET").toUpperCase() === "POST"
        && e.url.indexOf("/dialogue/") === 0
        && e.url.slice(-8) === "/message").length;
    const dialoguePostCount = dialogueStartCount + dialogueMessageCount;
    const historyGetCount = __net.log.filter(
      e => (e.opts.method || "GET").toUpperCase() === "GET"
        && e.url.indexOf("/dialogue/") === 0
        && e.url.indexOf("/message") < 0).length;
    const portraitRequestCount = __net.log.filter(
      e => (e.opts.method || "GET").toUpperCase() === "GET"
        && e.url.indexOf("/visual/characters/") === 0
        && e.url.slice(-9) === "/portrait").length;
    const scenePostCount = __net.log.filter(
      e => (e.opts.method || "GET").toUpperCase() === "POST"
        && e.url === "/visual/scenes").length;
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
      // ---------- #305: the chat (NPC dialogue) facts ----------
      chatViewPresent: !!chatView,
      chatNpcName: chatNpcNameEl ? chatNpcNameEl.textContent : null,
      chatNpcSelectValue: chatNpcSelect ? chatNpcSelect.value : null,
      chatNpcSelectOptions: chatNpcSelect
        ? [...chatNpcSelect.children].map((o) => o.value) : null,
      sendBtnDisabled: chatSendBtn ? !!chatSendBtn.disabled : null,
      presenceCaption: presenceCaption,
      chatStatusText: chatStatusEl ? chatStatusEl.textContent : null,
      chatErrorRowCount: chatErrorRowCount,
      messageRows: messageRows,
      relationshipBannerText: relationshipBannerText,
      chatRelNoteText: chatRelNoteEl ? chatRelNoteEl.textContent : null,
      chatRelNoteHidden: chatRelNoteEl ? !!chatRelNoteEl.hidden : null,
      initialsNodeText: initialsEl ? initialsEl.textContent : null,
      visualFallbackPresent: !!visualFallbackEl,
      chatBackAnchorHref: chatBackAnchor
        ? chatBackAnchor.getAttribute("href") : null,
      chatBackAnchorText: chatBackAnchor ? chatBackAnchor.textContent : null,
      chatInputMarker: chatInput ? (chatInput._m305Marker || null) : null,
      chatInputTag: chatInput ? chatInput.tagName : null,
      chatDraft: S.chatDraft != null ? S.chatDraft : null,
      chatNpcId: S.chatNpcId != null ? S.chatNpcId : null,
      chatSessionId: S.chatSessionId != null ? S.chatSessionId : null,
      draftByNpc: (typeof globalThis.__draftByNpc === "function")
        ? globalThis.__draftByNpc() : null,
      chatSourceLocationId: (typeof globalThis.__chatSourceLocationId === "function")
        ? globalThis.__chatSourceLocationId() : null,
      chatSourceLocationName: (typeof globalThis.__chatSourceLocationName === "function")
        ? globalThis.__chatSourceLocationName() : null,
      dialogueStartCount: dialogueStartCount,
      dialogueMessageCount: dialogueMessageCount,
      dialoguePostCount: dialoguePostCount,
      historyGetCount: historyGetCount,
      portraitRequestCount: portraitRequestCount,
      scenePostCount: scenePostCount,
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
  globalThis.__markComposer = __markComposer;
  globalThis.__selectNpc = __selectNpc;
  globalThis.__typeChat = __typeChat;
  globalThis.__chatEnter = __chatEnter;
  globalThis.__chatSendClick = __chatSendClick;
  globalThis.__fireChatImgError = __fireChatImgError;
  globalThis.__clickPlaceTalk = __clickPlaceTalk;
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
# the player character — a STRING id (the DB Character.id is a String).
CHAR1 = {"id": "1", "name": "P", "location_id": 1, "needs": {},
         "control_mode": "MANUAL", "is_owner": True, "money": 100}
# the same player at the NPC-free loc 3 (the empty-selection scenario).
CHAR1_LOC3 = {**CHAR1, "location_id": 3}
WORLD_A = {"game_timestamp": 1500, "day": 2}
WEATHER_OFF = {"enabled": False}

# The two co-located NPCs carry HYPHEN ids: the base prefix filter
# id.startsWith("npc_") EXCLUDES them, while the #305 kind-based roster
# (occupants kind === "npc") includes them. That asymmetry is the RED lever
# for the roster scenarios. The remote NPC sits in loc 2.
NPC_A = {"id": "npc-a", "name": "Марина"}
NPC_B = {"id": "npc-b", "name": "Пётр"}
# two-word name — the initials rule «Пётр Сидоров» → «ПС».
NPC_C = {"id": "npc-c", "name": "Пётр Сидоров"}
NPC_FAR = {"id": "npc-far", "name": "Фар"}

LOC1 = {"id": 1, "name": "Тавра", "type": "settlement", "x": 40, "y": 50,
        "parent_id": None, "occupants_count": 4,
        "occupants": [
            {"id": "1", "name": "P", "kind": "player"},
            {"id": "npc-a", "name": "Марина", "kind": "npc"},
            {"id": "npc-b", "name": "Пётр", "kind": "npc"},
            {"id": "npc-c", "name": "Пётр Сидоров", "kind": "npc"},
        ]}
LOC2 = {"id": 2, "name": "Remote", "type": "shop", "x": 60, "y": 30,
        "parent_id": None, "occupants_count": 1,
        "occupants": [
            {"id": "npc-far", "name": "Фар", "kind": "npc"},
        ]}
LOC3 = {"id": 3, "name": "Пещера", "type": "cave", "x": 20, "y": 20,
        "parent_id": None, "occupants_count": 1,
        "occupants": [
            {"id": "1", "name": "P", "kind": "player"},
        ]}
CATALOG = [LOC1, LOC2, LOC3]
# the /locations/{id} detail (base viewChat reads characters_here).
LOC1_DETAIL = {"id": 1, "name": "Тавра",
               "characters_here": ["1", "npc-a", "npc-b", "npc-c"]}
LOC2_DETAIL = {"id": 2, "name": "Remote", "characters_here": ["npc-far"]}
LOC3_DETAIL = {"id": 3, "name": "Пещера", "characters_here": ["1"]}
# the by-user bootstrap payload (NO location_id — the cold-load shape).
BY_USER = [{"id": "1", "name": "P", "alive": True,
            "control_mode": "MANUAL"}]

# the player MOVED to loc 2 (the poll-depart scenarios): the character read
# reports location_id 2 and the catalog shows the player at loc 2 with the
# loc-1 NPCs (npc-a/b/c) no longer co-located.
CHAR1_MOVED = {**CHAR1, "location_id": 2}
LOC1_MOVED = {**LOC1, "occupants_count": 3,
              "occupants": [o for o in LOC1["occupants"]
                            if o["kind"] == "npc"]}
LOC2_MOVED = {"id": 2, "name": "Remote", "type": "shop", "x": 60, "y": 30,
              "parent_id": None, "occupants_count": 2,
              "occupants": [
                  {"id": "1", "name": "P", "kind": "player"},
                  {"id": "npc-far", "name": "Фар", "kind": "npc"},
              ]}
CATALOG_MOVED = [LOC1_MOVED, LOC2_MOVED, LOC3]

# relationships: present for npc-a, ABSENT for npc-b (the app filters by
# other_id; a missing row renders «пока незнакомы», never a banner).
RELS_WITH_A = [{"other_id": "npc-a", "affection": 55, "trust": 30}]


def _resp(status, body, **extra):
    d = {"status": status, "body": body}
    d.update(extra)
    return d


def _tasks_empty():
    return {"planned": [], "active": [], "recent_terminal": []}


def _auth(user, char, by_user, rels=RELS_WITH_A):
    return {
        "/auth/me": _resp(200, user),
        f"/characters/by-user/{user['id']}": _resp(200, by_user),
        f"/characters/{char['id']}": _resp(200, char),
        f"/characters/{char['id']}/relationships": _resp(200, rels),
    }


def _chat_base(user=USER_A, char=CHAR1, world=WORLD_A):
    """Authenticated boot + the chat loader's GETs (+ the poll's reads)."""
    return dict(
        _auth(user, char, BY_USER),
        **{
            "/world": _resp(200, world),
            "/weather": _resp(200, WEATHER_OFF),
            "/locations": _resp(200, CATALOG),
            "/locations/1": _resp(200, LOC1_DETAIL),
            "/locations/2": _resp(200, LOC2_DETAIL),
            "/locations/3": _resp(200, LOC3_DETAIL),
            f"/characters/{char['id']}/tasks": _resp(200, _tasks_empty()),
        },
    )


# ---------- dialogue fixtures (server read-model shapes) ----------

def _dialogue_start(session_id, npc_id, char_id="1"):
    return _resp(201, {"session_id": session_id, "npc_id": npc_id,
                       "character_id": char_id})


def _dialogue_message(npc_reply="Привет", source="llm", suggested=None,
                      effect=None, formed=None, band_changed=None,
                      session_id="sess-a"):
    return _resp(200, {
        "npc_reply": npc_reply,
        "suggested_responses": suggested or [],
        "session_id": session_id,
        "source": source,
        "relationship_effect": effect or {"applied": False},
        "relationship_formed": formed,
        "relationship_band_changed": band_changed,
    })


def _msg(sender, content, source="llm", suggested=None, ts=1450):
    return {"sender": sender, "content": content,
            "suggested_responses": suggested,
            "game_timestamp": ts, "source": source}


def _dialogue_history(session_id, messages, npc_id="npc-a"):
    return _resp(200, {
        "session_id": session_id,
        "character_id": "1",
        "npc_id": npc_id,
        "started_at": 1400,
        "ended_at": None,
        "messages": messages,
    })


def _roles(roles):
    return _resp(200, {"roles": roles})


def _roles_all():
    return {
        "/characters/npc-a/roles": _roles([]),
        "/characters/npc-b/roles": _roles([]),
        "/characters/npc-c/roles": _roles([]),
    }


def _portrait_404_all():
    return {
        "/visual/characters/npc-a/portrait": _portrait_fail(),
        "/visual/characters/npc-b/portrait": _portrait_fail(),
        "/visual/characters/npc-c/portrait": _portrait_fail(),
    }


# ---------- portrait fixtures ----------

def _portrait_ok(asset_id=101):
    return _resp(200, {"id": asset_id, "asset_type": "portrait",
                       "character_id": "npc-a", "canonical": True})


def _portrait_fail():
    return _resp(404, {"detail": "canonical portrait not found"})


def _asset_file():
    return _resp(200, None)


# ---------- scenarios (independent tests, this order) ----------
# RED on base: 01,02,03,04,05,06,07,09,10,11,13 (the #305 presence machine /
#   keyed roster / revalidation / race guards / initials / back-anchor do not
#   exist yet). GREEN on base (preservation): 08,12,14,15.
# The hyphen-id NPCs (npc-a/b/c) are EXCLUDED by the base prefix filter
# id.startsWith("npc_") — that asymmetry is the roster RED lever.
# (json is imported by the runner head, which precedes this part.)


def _chat_full(**kw):
    return {**_chat_base(**kw), **_roles_all(), **_portrait_404_all()}


def _start_201(npc_id=""):
    return _dialogue_start("sess-a", npc_id)


def _msg_200(**kw):
    return _dialogue_message(session_id="sess-a", **kw)


def _hist_200(messages, npc_id="npc-a"):
    return _dialogue_history("sess-a", messages, npc_id)


def _moved_responses():
    return {
        "/characters/1": _resp(200, CHAR1_MOVED),
        "/locations": _resp(200, CATALOG_MOVED),
    }


def _moved_js():
    return (
        "__setResponse('/characters/1', "
        + json.dumps(_resp(200, CHAR1_MOVED), ensure_ascii=False)
        + "); __setResponse('/locations', "
        + json.dumps(_resp(200, CATALOG_MOVED), ensure_ascii=False)
        + "); "
    )


# test_18: the second send's history GET (200) — the full two-turn history.
_HIST_2 = _hist_200([
    _msg("user", "привет"),
    _msg("npc", "Привет"),
    _msg("user", "второе"),
    _msg("npc", "Второй ответ")])


SCENARIOS = [
    # ---------- 01. two_npc_target (RED) ----------
    {
        "name": "TWO_NPC_TARGET",
        "initialHash": "#/location/1",
        "responses": _chat_full(),
        "driver": (
            "await __settle(); "
            "__clickPlaceTalk('Марина'); "
            "await __settle(); "
            "const fA = __facts(); "
            "__setHash('#/location/1'); "
            "await __settle(); "
            "__clickPlaceTalk('Сидоров'); "
            "await __settle(); "
            "const fB = __facts(); "
            "globalThis.__result = { fA, fB };"
        ),
    },
    # ---------- 02. stale_carry_no_fallback (RED) ----------
    {
        "name": "STALE_CARRY_NO_FALLBACK",
        "initialHash": "#/location/1",
        "responses": _chat_full(),
        "driver": (
            "await __settle(); "
            "__setCarryNpc('npc-far'); "
            "__setDraft('npc-far', 'привет'); "
            "__setHash('#/chat'); "
            "await __settle(); "
            "const f1 = __facts(); "
            "__typeChat('привет'); "
            "__chatEnter(); "
            "await __settle(); "
            "const f2 = __facts(); "
            "__chatSendClick(); "
            "await __settle(); "
            "const f3 = __facts(); "
            "globalThis.__result = { f1, f2, f3 };"
        ),
    },
    # ---------- 03. poll_depart_disable (RED) ----------
    {
        "name": "POLL_DEPART_DISABLE",
        "initialHash": "#/chat",
        "responses": _chat_full(),
        "driver": (
            "await __settle(); "
            "__markComposer(); "
            "__typeChat('привет'); "
            "const input = document.getElementById('chat-msg-input'); "
            "input.focus(); "
            "const fBefore = __facts(); "
            + _moved_js()
            + "__fireInterval(__S.pollTimer); "
            "await __settle(); "
            "const fAfter = __facts(); "
            "globalThis.__result = { fBefore, fAfter };"
        ),
    },
    # ---------- 04. stale_validation_discarded (RED) ----------
    {
        "name": "STALE_VALIDATION_DISCARDED",
        "initialHash": "#/chat",
        "responses": {**_chat_full(),
                     "/dialogue/start": _start_201(),
                     "/dialogue/sess-a/message": _msg_200()},
        "driver": (
            "await __settle(); "
            + _moved_js()
            + "__typeChat('привет'); "
            "__chatSendClick(); "
            "await __settle(); "
            "const f = __facts(); "
            "globalThis.__result = { f };"
        ),
    },
    # ---------- 05. direct_entry_keyed (RED) ----------
    {
        "name": "DIRECT_ENTRY_KEYED",
        "initialHash": "#/chat",
        "responses": _chat_full(),
        "driver": (
            "await __settle(); "
            "const f1 = __facts(); "
            "__typeChat('привет'); "
            "const f2 = __facts(); "
            "globalThis.__result = { f1, f2 };"
        ),
    },
    # ---------- 06. draft_survival (RED) ----------
    {
        "name": "DRAFT_SURVIVAL",
        "initialHash": "#/chat",
        "responses": _chat_full(),
        "driver": (
            "await __settle(); "
            "__typeChat('привет'); "
            "__selectNpc('npc-b'); "
            "await __settle(); "
            "__typeChat('здравствуй'); "
            "__selectNpc('npc-a'); "
            "await __settle(); "
            "const f = __facts(); "
            "globalThis.__result = { f };"
        ),
    },
    # ---------- 07. draft_consume (RED) ----------
    {
        "name": "DRAFT_CONSUME",
        "initialHash": "#/chat",
        "responses": {**_chat_full(),
                     "/dialogue/start": _start_201(),
                     "/dialogue/sess-a": _hist_200(
                         [_msg("user", "привет"),
                          _msg("npc", "Привет", suggested=["Как дела?"])])},
        "driver": (
            "await __settle(); "
            "__typeChat('привет'); "
            "__hold('/dialogue/sess-a/message'); "
            "__chatSendClick(); "
            "await __settle(); "
            "__typeChat('ещё текст'); "
            "__release('/dialogue/sess-a/message', "
            + json.dumps(_msg_200(), ensure_ascii=False)
            + "); "
            "await __settle(); "
            "const f = __facts(); "
            "globalThis.__result = { f };"
        ),
    },
    # ---------- 08. send_fail_retain (GREEN) ----------
    {
        "name": "SEND_FAIL_RETAIN",
        "initialHash": "#/chat",
        "responses": {**_chat_full(),
                     "/dialogue/start": _start_201(),
                     "/dialogue/sess-a/message": _resp(
                         503, {"detail": "service unavailable"})},
        "driver": (
            "await __settle(); "
            "__typeChat('привет'); "
            "__chatSendClick(); "
            "await __settle(); "
            "const f = __facts(); "
            "globalThis.__result = { f };"
        ),
    },
    # ---------- 09. sent_history_fail_no_dup (RED) ----------
    {
        "name": "SENT_HISTORY_FAIL_NO_DUP",
        "initialHash": "#/chat",
        "responses": {**_chat_full(),
                     "/dialogue/start": _start_201(),
                     "/dialogue/sess-a/message": _msg_200(),
                     "/dialogue/sess-a": _resp(
                         503, {"detail": "service unavailable"})},
        "driver": (
            "await __settle(); "
            "__typeChat('привет'); "
            "__chatSendClick(); "
            "await __settle(); "
            "const f = __facts(); "
            "globalThis.__result = { f };"
        ),
    },
    # ---------- 10. race_aba (RED) ----------
    {
        "name": "RACE_ABA",
        "initialHash": "#/chat",
        "responses": _chat_full(),
        "driver": (
            "await __settle(); "
            "__typeChat('привет'); "
            "__hold('/dialogue/start'); "
            "__chatSendClick(); "
            "await __settle(); "
            "__selectNpc('npc-b'); "
            "await __settle(); "
            "__release('/dialogue/start', "
            + json.dumps(_start_201("npc-a"), ensure_ascii=False)
            + "); "
            "await __settle(); "
            "const f = __facts(); "
            "globalThis.__result = { f };"
        ),
    },
    # ---------- 11. initials (RED phases 1-2, GREEN phase 3) ----------
    {
        "name": "INITIALS",
        "phases": [
            # phase 1: portrait fetch failure (npc-a «Марина» → «М»)
            {
                "initialHash": "#/chat",
                "responses": _chat_full(),
                "driver": (
                    "await __settle(); "
                    "const f = __facts(); "
                    "globalThis.__result = { f };"
                ),
            },
            # phase 2: image decode failure (npc-c «Пётр Сидоров» → «ПС»)
            {
                "initialHash": "#/chat",
                "responses": {**_chat_full(),
                              "/visual/characters/npc-c/portrait":
                                  _portrait_ok(102),
                              "/visual/assets/102/file": _asset_file()},
                "driver": (
                    "await __settle(); "
                    "__selectNpc('npc-c'); "
                    "await __settle(); "
                    "__fireChatImgError(); "
                    "await __settle(); "
                    "const f = __facts(); "
                    "globalThis.__result = { f };"
                ),
            },
            # phase 3: empty selection (player at NPC-free loc 3)
            {
                "initialHash": "#/chat",
                "responses": _chat_full(char=CHAR1_LOC3),
                "driver": (
                    "await __settle(); "
                    "const f = __facts(); "
                    "globalThis.__result = { f };"
                ),
            },
        ],
    },
    # ---------- 12. provenance_relationships (GREEN) ----------
    {
        "name": "PROVENANCE_RELATIONSHIPS",
        "phases": [
            # phase 1: mixed fallback/llm history
            {
                "initialHash": "#/chat",
                "responses": {**_chat_full(),
                              "/dialogue/sess-a": _hist_200([
                                  _msg("user", "Привет"),
                                  _msg("npc", "Здравствуй",
                                       source="fallback"),
                                  _msg("user", "Как дела?"),
                                  _msg("npc", "Хорошо", source="llm")])},
                "driver": (
                    "await __settle(); "
                    "__S.chatSessionId = 'sess-a'; "
                    "__call('viewChat'); "
                    "await __settle(); "
                    "const f = __facts(); "
                    "globalThis.__result = { f };"
                ),
            },
            # phase 2: npc-b (no relationships) → no banner
            {
                "initialHash": "#/chat",
                "responses": _chat_full(),
                "driver": (
                    "await __settle(); "
                    "__selectNpc('npc-b'); "
                    "await __settle(); "
                    "const f = __facts(); "
                    "globalThis.__result = { f };"
                ),
            },
            # phase 3: relationship banner from POST reply fields
            {
                "initialHash": "#/chat",
                "responses": {**_chat_full(),
                              "/dialogue/start": _start_201(),
                              "/dialogue/sess-a/message": _msg_200(
                                  formed={"target_id": "npc-a",
                                          "affection_delta": 5,
                                          "affection_after": 60}),
                              "/dialogue/sess-a": _hist_200(
                                  [_msg("user", "привет"),
                                   _msg("npc", "Привет")])},
                "driver": (
                    "await __settle(); "
                    "__typeChat('привет'); "
                    "__chatSendClick(); "
                    "await __settle(); "
                    "const f = __facts(); "
                    "globalThis.__result = { f };"
                ),
            },
        ],
    },
    # ---------- 13. back_anchor (GREEN phase 1, RED phase 2) ----------
    {
        "name": "BACK_ANCHOR",
        "phases": [
            # phase 1: Place-origin → back anchor href #/location/{source}
            {
                "initialHash": "#/location/1",
                "responses": _chat_full(),
                "driver": (
                    "await __settle(); "
                    "__clickPlaceTalk('Марина'); "
                    "await __settle(); "
                    "const f = __facts(); "
                    "globalThis.__result = { f };"
                ),
            },
            # phase 2: direct entry → «← К карте» → #/world
            {
                "initialHash": "#/chat",
                "responses": _chat_full(),
                "driver": (
                    "await __settle(); "
                    "const f = __facts(); "
                    "globalThis.__result = { f };"
                ),
            },
        ],
    },
    # ---------- 14. no_scene_from_chat (GREEN) ----------
    {
        "name": "NO_SCENE_FROM_CHAT",
        "initialHash": "#/chat",
        "responses": {**_chat_full(),
                     "/dialogue/start": _start_201(),
                     "/dialogue/sess-a/message": _msg_200(),
                     "/dialogue/sess-a": _hist_200(
                         [_msg("user", "привет"),
                          _msg("npc", "Привет")])},
        "driver": (
            "await __settle(); "
            "__fireInterval(__S.pollTimer); "
            "await __settle(); "
            "__typeChat('привет'); "
            "__chatSendClick(); "
            "await __settle(); "
            "const f = __facts(); "
            "globalThis.__result = { f };"
        ),
    },
    # ---------- 15. chips_fill_only (GREEN, preservation) ----------
    {
        "name": "CHIPS_FILL_ONLY",
        "initialHash": "#/chat",
        "responses": {**_chat_full(),
                     "/dialogue/sess-a": _hist_200([
                         _msg("user", "Привет"),
                         _msg("npc", "Здравствуй",
                              suggested=["Привет", "Как дела?"])])},
        "driver": (
            "await __settle(); "
            "__S.chatSessionId = 'sess-a'; "
            "__call('viewChat'); "
            "await __settle(); "
            "const chips = document.querySelectorAll('.sugg'); "
            "if (chips.length) chips[0].click(); "
            "await __settle(); "
            "const f = __facts(); "
            "globalThis.__result = { f };"
        ),
    },
    # ---------- 17. typing_survives_failed_send (RED) ----------
    # The catch path does `input.value = draft;` UNCONDITIONALLY after a
    # failed message POST — it overwrites text typed during the flight.
    # The success path guards with `if (input.value === draft)`; the
    # failure path must too.
    {
        "name": "TYPING_SURVIVES_FAILED_SEND",
        "initialHash": "#/chat",
        "responses": {**_chat_full(),
                     "/dialogue/start": _start_201()},
        "driver": (
            "await __settle(); "
            "__typeChat('привет'); "
            "__hold('/dialogue/sess-a/message'); "
            "__chatSendClick(); "
            "await __settle(); "
            "__typeChat('привет ещё'); "
            "__release('/dialogue/sess-a/message', "
            + json.dumps(_resp(503, {"detail": "service unavailable"}),
                         ensure_ascii=False)
            + "); "
            "await __settle(); "
            "const f = __facts(); "
            "globalThis.__result = { f };"
        ),
    },
    # ---------- 18. session_binds_at_ack (RED) ----------
    # S.chatSessionId is set only AFTER the history GET succeeds. If the
    # history GET fails after the message POST was acked, the session id
    # stays unset → the NEXT send issues a SECOND /dialogue/start.
    {
        "name": "SESSION_BINDS_AT_ACK",
        "initialHash": "#/chat",
        "responses": {**_chat_full(),
                     "/dialogue/start": _start_201(),
                     "/dialogue/sess-a/message": _msg_200(),
                     "/dialogue/sess-a": _resp(
                         503, {"detail": "service unavailable"})},
        "driver": (
            "await __settle(); "
            "__typeChat('привет'); "
            "__chatSendClick(); "
            "await __settle(); "
            "const f1 = __facts(); "
            "__setResponse('/dialogue/sess-a', "
            + json.dumps(_HIST_2, ensure_ascii=False)
            + "); "
            "__typeChat('второе'); "
            "__chatSendClick(); "
            "await __settle(); "
            "const f2 = __facts(); "
            "globalThis.__result = { f1, f2 };"
        ),
    },
]


def _run_harness():
    os.makedirs(SCRATCH, exist_ok=True)
    harness_path = os.path.join(SCRATCH, "m305_harness.mjs")
    scen_path = os.path.join(SCRATCH, "m305_scenarios.json")
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
# RED on base: 01,02,03,04,05,06,07,09,10,11,13. GREEN (preservation):
# 08,12,14,15 + the source-census test.

CAP_DEPARTED = "NPC не рядом"
CAP_VISUAL_FALLBACK = "Визуал недоступен — диалог текстом"
CAP_FALLBACK = "LLM недоступен — fallback"
CAP_FORMED = "Начало отношений"
CAP_SEND_FAIL = ("Не удалось отправить — "
                 "проверьте связь и повторите")


def _no_unhandled(results, name):
    unhandled = _unhandled(results, name)
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 01. two_npc_target (RED) ----------

def test_01_two_npc_target(results):
    r = _facts(results, "TWO_NPC_TARGET")
    a, b = r["fA"], r["fB"]
    assert a["chatNpcName"] == "Марина", (
        f"clicking «Поговорить» for Марина must select her, "
        f"got {a['chatNpcName']!r}")
    assert b["chatNpcName"] == "Пётр Сидоров", (
        f"clicking «Поговорить» for Сидоров must select him, "
        f"got {b['chatNpcName']!r}")
    assert a["dialoguePostCount"] == 0, (
        f"chat entry must make ZERO dialogue POSTs, "
        f"got {a['dialoguePostCount']}")
    assert b["dialoguePostCount"] == 0, (
        f"chat entry must make ZERO dialogue POSTs, "
        f"got {b['dialoguePostCount']}")
    assert a["chatInputTag"] == "INPUT", (
        f"the composer must remain an <input>, got {a['chatInputTag']!r}")
    _no_unhandled(results, "TWO_NPC_TARGET")


# ---------- 02. stale_carry_no_fallback (RED) ----------

def test_02_stale_carry_no_fallback(results):
    r = _facts(results, "STALE_CARRY_NO_FALLBACK")
    f1, f2, f3 = r["f1"], r["f2"], r["f3"]
    assert f1["chatNpcName"] == "Фар", (
        f"a stale carry must show the carried NPC identifiable, "
        f"got {f1['chatNpcName']!r}")
    assert f1["chatNpcName"] not in ("Марина", "Пётр", "Пётр Сидоров"), (
        f"no silent fallback to a nearby NPC, got {f1['chatNpcName']!r}")
    assert f1["presenceCaption"] is not None and \
        CAP_DEPARTED in f1["presenceCaption"], (
        f"a stale carry must show the departed caption, "
        f"got {f1['presenceCaption']!r}")
    assert f1["sendBtnDisabled"] is True, (
        f"a stale carry must disable the send button, "
        f"got {f1['sendBtnDisabled']!r}")
    assert f1["chatInputValue"] == "привет", (
        f"the carried NPC's draft must be retained, "
        f"got {f1['chatInputValue']!r}")
    assert f2["dialoguePostCount"] == 0, (
        f"Enter on a departed NPC must make ZERO POSTs, "
        f"got {f2['dialoguePostCount']}")
    assert f3["dialoguePostCount"] == 0, (
        f"clicking send on a departed NPC must make ZERO POSTs, "
        f"got {f3['dialoguePostCount']}")
    _no_unhandled(results, "STALE_CARRY_NO_FALLBACK")


# ---------- 03. poll_depart_disable (RED) ----------

def test_03_poll_depart_disable(results):
    r = _facts(results, "POLL_DEPART_DISABLE")
    before, after = r["fBefore"], r["fAfter"]
    assert before["sendBtnDisabled"] is False, (
        f"before the poll the NPC is present, so send is enabled, "
        f"got {before['sendBtnDisabled']!r}")
    assert after["sendBtnDisabled"] is True, (
        f"a poll that observes the NPC gone must disable send, "
        f"got {after['sendBtnDisabled']!r}")
    assert after["chatInputValue"] == "привет", (
        f"the poll must preserve the input text, "
        f"got {after['chatInputValue']!r}")
    assert after["chatInputFocused"] is True, (
        f"the poll must preserve the input focus, "
        f"got {after['chatInputFocused']!r}")
    assert after["chatInputMarker"] == "sentinel", (
        f"the poll must NOT rebuild the composer (marker lost), "
        f"got {after['chatInputMarker']!r}")
    _no_unhandled(results, "POLL_DEPART_DISABLE")


# ---------- 04. stale_validation_discarded (RED) ----------

def test_04_stale_validation_discarded(results):
    r = _facts(results, "STALE_VALIDATION_DISCARDED")
    f = r["f"]
    assert f["dialoguePostCount"] == 0, (
        f"a send whose revalidation finds the NPC gone must make "
        f"ZERO POSTs, got {f['dialoguePostCount']}")
    assert f["presenceCaption"] is not None and \
        CAP_DEPARTED in f["presenceCaption"], (
        f"a failed send revalidation must show the departed caption, "
        f"got {f['presenceCaption']!r}")
    _no_unhandled(results, "STALE_VALIDATION_DISCARDED")


# ---------- 05. direct_entry_keyed (RED) ----------

def test_05_direct_entry_keyed(results):
    r = _facts(results, "DIRECT_ENTRY_KEYED")
    f1, f2 = r["f1"], r["f2"]
    assert f1["chatNpcId"] == "npc-a", (
        f"a direct entry must key the target to a real NPC id, "
        f"got {f1['chatNpcId']!r}")
    assert f2["draftByNpc"] is not None and \
        f2["draftByNpc"].get("npc-a") == "привет", (
        f"typing on a direct entry must key to the real NPC id, "
        f"got {f2['draftByNpc']!r}")
    assert f2["chatInputValue"] == "привет", (
        f"the typed text must be in the composer, "
        f"got {f2['chatInputValue']!r}")
    _no_unhandled(results, "DIRECT_ENTRY_KEYED")


# ---------- 06. draft_survival (RED) ----------

def test_06_draft_survival(results):
    r = _facts(results, "DRAFT_SURVIVAL")
    f = r["f"]
    assert f["chatInputValue"] == "привет", (
        f"switching A→B→A must restore A's draft, "
        f"got {f['chatInputValue']!r}")
    assert f["chatNpcId"] == "npc-a", (
        f"the selection must return to A, got {f['chatNpcId']!r}")
    _no_unhandled(results, "DRAFT_SURVIVAL")


# ---------- 07. draft_consume (RED) ----------

def test_07_draft_consume(results):
    r = _facts(results, "DRAFT_CONSUME")
    f = r["f"]
    assert f["chatInputValue"] == "ещё текст", (
        f"a successful send must keep text typed during the flight, "
        f"got {f['chatInputValue']!r}")
    _no_unhandled(results, "DRAFT_CONSUME")


# ---------- 08. send_fail_retain (GREEN) ----------

def test_08_send_fail_retain(results):
    r = _facts(results, "SEND_FAIL_RETAIN")
    f = r["f"]
    assert f["chatInputValue"] == "привет", (
        f"a failed send must retain the draft, "
        f"got {f['chatInputValue']!r}")
    assert f["chatErrorRowCount"] == 1, (
        f"a failed send must show a visible retry row, "
        f"got {f['chatErrorRowCount']}")
    assert f["chatStatusText"] is not None and \
        "Не удалось отправить" in f["chatStatusText"], (
        f"a failed send must show the retry caption, "
        f"got {f['chatStatusText']!r}")
    _no_unhandled(results, "SEND_FAIL_RETAIN")


# ---------- 09. sent_history_fail_no_dup (RED) ----------

def test_09_sent_history_fail_no_dup(results):
    r = _facts(results, "SENT_HISTORY_FAIL_NO_DUP")
    f = r["f"]
    assert f["chatInputValue"] == "", (
        f"a committed message (POST 200) must clear the draft even if "
        f"the history GET fails, got {f['chatInputValue']!r}")
    assert f["chatStatusText"] is None or \
        "повторите" not in f["chatStatusText"], (
        f"a committed message must NOT offer a duplicate-send retry, "
        f"got {f['chatStatusText']!r}")
    _no_unhandled(results, "SENT_HISTORY_FAIL_NO_DUP")


# ---------- 10. race_aba (RED) ----------

def test_10_race_aba(results):
    r = _facts(results, "RACE_ABA")
    f = r["f"]
    assert f["dialogueStartCount"] == 1, (
        f"the /dialogue/start was made, got {f['dialogueStartCount']}")
    assert f["dialogueMessageCount"] == 0, (
        f"an NPC switch after /dialogue/start must discard the stale "
        f"start (no message POST), got {f['dialogueMessageCount']}")
    _no_unhandled(results, "RACE_ABA")


# ---------- 11. initials (RED phases 1-2, GREEN phase 3) ----------

def _phase_facts(results, name):
    phases = _facts(results, name)
    return [ph["result"]["f"] for ph in phases]


def test_11_initials(results):
    p1, p2, p3 = _phase_facts(results, "INITIALS")
    # phase 1: portrait fetch failure → «Марина» → «М» + text preserved
    assert p1["initialsNodeText"] == "М", (
        f"a portrait fetch failure must render the initials «М», "
        f"got {p1['initialsNodeText']!r}")
    assert p1["visualFallbackPresent"] is True, (
        f"the visual fallback text must be preserved, "
        f"got {p1['visualFallbackPresent']!r}")
    # phase 2: image decode failure → «Пётр Сидоров» → «ПС» + text
    assert p2["initialsNodeText"] == "ПС", (
        f"an image decode failure must render the initials «ПС», "
        f"got {p2['initialsNodeText']!r}")
    assert p2["visualFallbackPresent"] is True, (
        f"the visual fallback text must be preserved, "
        f"got {p2['visualFallbackPresent']!r}")
    # phase 3: empty selection → zero portrait requests
    assert p3["portraitRequestCount"] == 0, (
        f"an empty selection must make ZERO portrait requests, "
        f"got {p3['portraitRequestCount']}")
    _no_unhandled(results, "INITIALS")


# ---------- 12. provenance_relationships (GREEN) ----------

def test_12_provenance_relationships(results):
    p1, p2, p3 = _phase_facts(results, "PROVENANCE_RELATIONSHIPS")
    # phase 1: fallbackMarked true only on the fallback row
    rows = p1["messageRows"]
    assert len(rows) == 4, f"expected 4 message rows, got {len(rows)}"
    assert rows[1]["fallbackMarked"] is True, (
        f"the fallback row must carry the muted label, "
        f"got {rows[1]!r}")
    assert rows[3]["fallbackMarked"] is False, (
        f"the llm row must NOT carry the fallback label, "
        f"got {rows[3]!r}")
    assert rows[0]["fallbackMarked"] is False, (
        f"a user row must NOT carry the fallback label, "
        f"got {rows[0]!r}")
    # phase 2: npc-b (no relationships) → no banner
    assert p2["relationshipBannerText"] == "", (
        f"an NPC with no relationship must show no banner, "
        f"got {p2['relationshipBannerText']!r}")
    # phase 3: banner appears only from the POST reply's relationship_formed
    assert p3["relationshipBannerText"] is not None and \
        CAP_FORMED in p3["relationshipBannerText"], (
        f"relationship_formed in the reply must render the banner, "
        f"got {p3['relationshipBannerText']!r}")
    _no_unhandled(results, "PROVENANCE_RELATIONSHIPS")


# ---------- 13. back_anchor (GREEN phase 1, RED phase 2) ----------

def test_13_back_anchor(results):
    p1, p2 = _phase_facts(results, "BACK_ANCHOR")
    # phase 1: Place-origin → back anchor href #/location/{source}
    assert p1["chatBackAnchorHref"] == "#/location/1", (
        f"a Place-origin chat must back to the source place, "
        f"got {p1['chatBackAnchorHref']!r}")
    # phase 2: direct entry → «← К карте» → #/world
    assert p2["chatBackAnchorText"] == "← К карте", (
        f"a direct entry must offer «← К карте», "
        f"got {p2['chatBackAnchorText']!r}")
    assert p2["chatBackAnchorHref"] == "#/world", (
        f"the direct-entry back anchor must target #/world, "
        f"got {p2['chatBackAnchorHref']!r}")
    _no_unhandled(results, "BACK_ANCHOR")


# ---------- 14. no_scene_from_chat (GREEN) ----------

def test_14_no_scene_from_chat(results):
    r = _facts(results, "NO_SCENE_FROM_CHAT")
    f = r["f"]
    assert f["scenePostCount"] == 0, (
        f"chat entry + poll + send must make ZERO /visual/scenes POSTs, "
        f"got {f['scenePostCount']}")
    _no_unhandled(results, "NO_SCENE_FROM_CHAT")


# ---------- 15. chips_fill_only (GREEN, preservation) ----------

def test_15_chips_fill_only(results):
    r = _facts(results, "CHIPS_FILL_ONLY")
    f = r["f"]
    assert f["chatInputValue"] == "Привет", (
        f"a chip must fill the input, got {f['chatInputValue']!r}")
    assert f["dialoguePostCount"] == 0, (
        f"a chip must NOT send, got {f['dialoguePostCount']} POSTs")
    _no_unhandled(results, "CHIPS_FILL_ONLY")


# ---------- source census: exactly two S.chatDraft = ""; sites ----------

def test_16_two_chatdraft_clear_sites():
    src = open(APP_JS, encoding="utf-8").read()
    count = src.count('S.chatDraft = "";')
    assert count == 2, (
        f"app.js must have exactly two `S.chatDraft = \"\";` sites "
        f"(reset + send-success), got {count}")


# ---------- 17. typing_survives_failed_send (RED) ----------

def test_17_typing_survives_failed_send(results):
    r = _facts(results, "TYPING_SURVIVES_FAILED_SEND")
    f = r["f"]
    assert f["chatInputValue"] == "привет ещё", (
        f"a failed send must keep text typed during the flight, "
        f"got {f['chatInputValue']!r}")
    assert f["draftByNpc"] is not None and \
        f["draftByNpc"].get("npc-a") == "привет ещё", (
        f"the per-NPC draft store must hold the newer text, "
        f"got {f['draftByNpc']!r}")
    assert f["chatErrorRowCount"] == 1, (
        f"a failed send must show a visible retry row, "
        f"got {f['chatErrorRowCount']}")
    assert f["chatStatusText"] is not None and \
        "Не удалось отправить" in f["chatStatusText"], (
        f"a failed send must show the retry caption, "
        f"got {f['chatStatusText']!r}")
    _no_unhandled(results, "TYPING_SURVIVES_FAILED_SEND")


# ---------- 18. session_binds_at_ack (RED) ----------

def test_18_session_binds_at_ack(results):
    r = _facts(results, "SESSION_BINDS_AT_ACK")
    f1, f2 = r["f1"], r["f2"]
    # first send: committed (POST 201) but the history GET failed
    assert f1["chatInputValue"] == "", (
        f"a committed message must clear the draft even if the "
        f"history GET fails, got {f1['chatInputValue']!r}")
    assert f1["chatStatusText"] is not None and \
        "история не обновилась" in f1["chatStatusText"], (
        f"a committed message with a failed history refresh must show "
        f"the honest caption, got {f1['chatStatusText']!r}")
    assert f1["chatStatusText"] is None or \
        "повторите" not in f1["chatStatusText"], (
        f"a committed message must NOT offer a duplicate-send retry, "
        f"got {f1['chatStatusText']!r}")
    # second send: must REUSE the acked session (no second /dialogue/start)
    assert f2["dialogueStartCount"] == 1, (
        f"the second send must reuse the acked session (no second "
        f"/dialogue/start), got {f2['dialogueStartCount']}")
    assert f2["dialogueMessageCount"] == 2, (
        f"both sends must POST their message, "
        f"got {f2['dialogueMessageCount']}")
    _no_unhandled(results, "SESSION_BINDS_AT_ACK")
