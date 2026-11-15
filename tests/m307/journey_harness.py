"""#307 — playable-journey E2E suite: shared vm-harness.

Behavioral tests for the REAL production ``backend/app/web/app.js`` (boot
included), run inside a Node ``vm`` context using the embedded stdlib harness
below. The harness is an OWN copy of the m311 vm-harness pattern (m303/m310
lineage) — the same minimal DOM / fake-timer / controlled-network shim —
extended additively with the #307 journey surface:

- the m311 blob machinery (``__blobHold``/``__blobReject``/``Blob``/
  ``AbortController``) for the scene asset file GET + the chat portrait GETs;
- the m310 driver helpers (``__clickButton``/``__clickAnchor``/``__fireKey``);
- a ``__type(id, value)`` driver helper (set ``input.value`` THEN fire the
  ``input`` listeners — a programmatic ``.value`` set fires no input event, so
  ``S.sceneActDraft``/``S.chatDraft``/``gameUiContext.draftByNpc`` would never
  update and the draft roundtrip tests would falsely fail);
- ``location.search``/``location.pathname`` (``consumeAuthError`` reads them at
  boot), ``window.innerWidth``/``window.innerHeight`` (``applyChatViewportBudget``
  is called at ``renderChat`` — otherwise a NaN maxHeight), and ``Date`` in the
  vm context (used at app.js:1574, :5913);
- a journey-focused ``__facts()`` (identity/location/control, the network
  census, the WS log, the place/chat/tasks DOM facts, the scene cache + drafts,
  the task-presentation registry, the feed stream, the toast).

The harness only provides the browser surface (a minimal DOM, fake timers, and
a controlled network boundary); it does NOT mock or reimplement ``route()``,
``api()``, ``viewWorld()``, ``viewLocation()``, ``viewChat()``, ``viewTasks()``,
``moveTo()``, ``inspectSurroundings()``, ``submitSceneAct()``, ``acceptTask()``
or any of the journey logic. Every assertion is on an observable fact.
"""

import json
import os
import subprocess

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
APP_JS = os.path.join(ROOT, "backend/app/web/app.js")
SCRATCH = "/tmp/opencode/m307-testing"


HARNESS_JS = r"""
// #307 — playable-journey E2E harness. Real production app.js in a Node vm
// with fake DOM/fetch/timers/WebSocket. OWN copy of the m311 vm-harness
// pattern (m303/m310 lineage) extended with the journey surface. It does NOT
// mock route()/api()/viewWorld()/viewLocation()/viewChat()/viewTasks()/
// moveTo()/inspectSurroundings()/submitSceneAct()/acceptTask() or reimplement
// any production logic; it only provides the browser surface (DOM, timers,
// network boundary) and collects observable facts. Python asserts on the facts.
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
// task-presentation / currentTaskId / feedController accessors are
// typeof-guarded so the harness stays loadable against a base app.js that
// lacks them.
const EXPOSE = [
  "\n;globalThis.__S = S; globalThis.__app = app; globalThis.__routes = routes;",
  "globalThis.__guc = () => (typeof gameUiContext !== 'undefined' ? gameUiContext : null);",
  "globalThis.__navEpoch = () => (typeof navEpoch !== 'undefined' ? navEpoch : 0);",
  "globalThis.__obsLoc = () => " +
  "(typeof observedPlayerLocId !== 'undefined' ? observedPlayerLocId : null);",
  "globalThis.__taskPresentation = () => " +
  "(typeof taskPresentation !== 'undefined' ? taskPresentation : null);",
  "globalThis.__currentTaskId = () => " +
  "(typeof currentTaskId !== 'undefined' ? currentTaskId : null);",
  "globalThis.__taskSubmitFeedback = () => " +
  "(typeof taskSubmitFeedback !== 'undefined' ? taskSubmitFeedback : null);",
  "globalThis.__feedController = () => " +
  "(typeof worldFeedController !== 'undefined' ? worldFeedController : null);",
  "\n",
].join("");

const SHIM = `

"use strict";
(function () {
  // #312: the currently focused element (focus/blur track it).
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
    if (n.tagName === "SELECT") {
      let value = "";
      Object.defineProperty(n, "value", {
        enumerable: true,
        configurable: true,
        get() { return value; },
        set(v) { value = String(v); },
      });
    } else {
      n.value = "";
    }
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
    // #311: isConnected — the app's stale-response guards check the host node
    // is still mounted; a node is connected iff its parent chain reaches the
    // document element.
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
  // first descendant (pre-order) matching a predicate.
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
    // #307: applyChatViewportBudget() reads window.innerHeight (visualViewport
    // is absent) — a concrete value keeps the chatlog maxHeight a real number.
    innerWidth: 1440,
    innerHeight: 900,
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
    // #307: consumeAuthError() reads location.search at boot (and
    // location.pathname when it strips a query) — both must exist.
    search: "",
    pathname: "/",
    protocol: "http:",
    host: "localhost",
    href: "http://localhost/",
    assign() {}, reload() {}, replace() {},
  };
  const history = { replaceState() {}, pushState() {} };

  // ---------- URL / WebSocket ----------
  // #312: createObjectURL/revokeObjectURL are observable — unique ids, a
  // create/revoke event log.
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
  // #311: rich WebSocket fake — manual dispatch + connect/frame/close logs.
  const __wsLog = { connects: [], frames: [], closes: [], timeline: [] };
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
      __wsLog.timeline.push({ type: "connect", seq: this._seq });
    }
    send(data) { __wsLog.frames.push({ seq: this._seq, data }); }
    close() {
      if (this.readyState === 3) return;
      this.readyState = 3;
      __wsLog.closes.push({ seq: this._seq });
      __wsLog.timeline.push({ type: "close", seq: this._seq });
    }
  }

  // ---------- fake timers ----------
  const __timers = {
    intervalCount: 0, timeouts: new Map(), intervals: new Map(),
    nextId: 1, now: 0,
  };
  function setTimeout(fn, ms) {
    const id = __timers.nextId++;
    __timers.timeouts.set(id, { fn, at: __timers.now + (ms || 0), ms: ms || 0 });
    return id;
  }
  function clearTimeout(id) { __timers.timeouts.delete(id); }
  function setInterval(fn, ms) {
    const id = __timers.nextId++;
    __timers.intervalCount++;
    __timers.intervals.set(id, { fn, at: __timers.now + (ms || 0), ms: ms || 0 });
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
        __timers.intervals.set(nextId, { fn: iv.fn, at: __timers.now + iv.ms, ms: iv.ms });
        iv.fn();
      }
    }
    __timers.now = target;
  }
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
  function __fireInterval(id) {
    const iv = __timers.intervals.get(id);
    if (!iv) return false;
    __timers.intervals.delete(id);
    __timers.intervals.set(id, { fn: iv.fn, at: __timers.now + iv.ms, ms: iv.ms });
    iv.fn();
    return true;
  }
  function __findTimeout(ms) {
    for (const [id, t] of __timers.timeouts) if (t.ms === ms) return id;
    return null;
  }

  // ---------- controlled network boundary ----------
  // Every request is logged with method+path (the zero-POST / exact-POST
  // assertions read the log) and the raw fetch opts (the POST body assertion
  // reads opts.body — api() JSON-stringifies it before fetch).
  const __net = {
    log: [], held: [], blobHeld: [], responses: {}, completions: [],
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
          const respSpec = entry._resp || spec;
          __net.completions.push({ url: String(url), status: respSpec.status });
          resolve(makeResponse(respSpec, url));
        };
        entry._reject = (e) => {
          __net.held = __net.held.filter(h => h !== entry);
          reject(e);
        };
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
    return Promise.resolve().then(() => {
    __net.completions.push({ url: String(url), status: spec.status });
    return makeResponse(spec, url);
  });
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
    const cur = __net.responses[String(url)];
    if (cur && cur.__hold) {
      const next = Object.assign({}, cur);
      delete next.__hold;
      __net.responses[String(url)] = next;
    }
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
  // click the first BUTTON whose text matches. Returns whether one was found.
  function __clickButton(text) {
    const b = findDescendant(documentElement,
      (n) => n.tagName === "BUTTON" && n.textContent === text);
    if (b) { b.click(); return true; }
    return false;
  }
  // click the first ANCHOR whose text matches, simulating the browser's
  // same-document hash navigation for an href="#/..." anchor.
  function __clickAnchor(text) {
    const a = findDescendant(documentElement,
      (n) => n.tagName === "A" && n.textContent === text);
    if (!a) return false;
    const href = a.getAttribute("href");
    if (href && href.startsWith("#")) location.hash = href;
    return true;
  }
  // fire the window "keydown" listeners.
  function __fireKey(key) {
    (winListeners["keydown"] || []).forEach(fn =>
      fn({ key, preventDefault() {}, stopPropagation() {} }));
  }
  // #307: type into an input by id — set .value THEN fire the "input"
  // listeners. A programmatic .value set fires no input event, so without the
  // manual dispatch S.sceneActDraft / S.chatDraft / gameUiContext.draftByNpc
  // would never update and the draft roundtrip tests would falsely fail.
  // (el() routes oninput through addEventListener, so _listeners["input"] is
  // the single binding for both the scene-act and the chat composer.)
  function __type(id, value) {
    const el = document.getElementById(id);
    if (!el) return false;
    el.value = value;
    (el._listeners["input"] || []).forEach((fn) => fn({ type: "input", target: el }));
    return true;
  }
  // #307: click the scene-inspect button in the Place stage (the single
  // stable button node, present only on the current place). Robust to the
  // idle/ready label — it clicks the first BUTTON inside .place-stage.
  function __stageButton() {
    const stage = document.querySelector(".place-stage");
    if (!stage) return false;
    const b = findDescendant(stage, (n) => n.nodeType === 1 && n.tagName === "BUTTON");
    if (!b) return false;
    b.click();
    return true;
  }
  // #307: journey-focused facts — identity/location/control, the network
  // census, the WS log, the place/chat/tasks DOM facts, the scene cache +
  // drafts, the task-presentation registry, the feed stream, the toast.
  function __facts() {
    const S = globalThis.__S;
    const g = (typeof globalThis.__guc === "function") ? globalThis.__guc() : null;
    const tp = (typeof globalThis.__taskPresentation === "function")
      ? globalThis.__taskPresentation() : null;
    const registry = {};
    if (tp) {
      for (const [k, v] of tp) {
        registry[k] = {
          serverStatus: v.serverStatus, readState: v.readState,
          label: v.label, taskType: v.taskType,
        };
      }
    }
    const placeBadge = document.querySelector(".place-badge");
    const nearby = document.querySelector(".place-nearby");
    const nearbyRows = [];
    let nearbyEmpty = false;
    if (nearby) {
      nearbyEmpty = nearby.textContent.indexOf("Рядом никого нет.") >= 0;
      (function walk(n) {
        for (const c of n._children) {
          if (c.nodeType !== 1) continue;
          if (c.tagName === "DIV" && (c.className || "").indexOf("place-nearby-row") >= 0) {
            const span = findDescendant(c, (n) => n.tagName === "SPAN");
            const btn = findDescendant(c, (n) => n.tagName === "BUTTON");
            nearbyRows.push({ name: span ? span.textContent : null, dialog: !!btn });
          }
          walk(c);
        }
      })(nearby);
    }
    const sceneActInput = document.getElementById("scene-act-input");
    const placeResult = document.querySelector(".place-scene-act-result");
    const placeResultNode = placeResult
      ? findDescendant(placeResult,
          (n) => n.nodeType === 1 && n.attributes.id === "scene-act-result")
      : null;
    const chatView = document.getElementById("chat-view");
    const chatInput = document.getElementById("chat-msg-input");
    const chatNpcSel = document.getElementById("chat-npc-select");
    const chatLog = document.getElementById("chat-log");
    const chatStatus = document.getElementById("chat-status");
    const chatPresence = document.getElementById("chat-presence");
    const tasksActive = document.getElementById("tasks-active");
    const tasksHistory = document.getElementById("tasks-history");
    const feedBox = document.getElementById("feed");
    const playerMarker = document.getElementById("player-marker");
    const toastEl = document.getElementById("toast");
    // network census
    const posts = __net.log.filter(
      (e) => (e.opts.method || "GET").toUpperCase() === "POST");
    const nonGetHead = __net.log.filter((e) => {
      const m = (e.opts.method || "GET").toUpperCase();
      return m !== "GET" && m !== "HEAD";
    });
    const methods = {};
    for (const e of __net.log) {
      const m = (e.opts.method || "GET").toUpperCase();
      methods[m] = (methods[m] || 0) + 1;
    }
    return {
      hash: location.hash,
      // identity / location / control
      userId: S.user ? S.user.id : null,
      charId: S.character ? S.character.id : null,
      charLocId: S.character ? S.character.location_id : null,
      charControl: S.character ? S.character.control_mode : null,
      // network census
      postCount: posts.length,
      posts: posts.map((e) => {
        let b = e.opts.body;
        try { b = JSON.parse(e.opts.body); } catch (err) { /* keep raw */ }
        return { url: e.url, body: b };
      }),
      nonGetHead: nonGetHead.map((e) => ({
        method: (e.opts.method || "GET").toUpperCase(), url: e.url,
      })),
      methods,
      // #307: the task-detail reads (GET /actions/{id}) — read-only revisits
      // must reference only the accepted task ids, never an invented one.
      actionsDetailGets: __net.log.filter((e) => {
        const m = (e.opts.method || "GET").toUpperCase();
        return m === "GET" && e.url.indexOf("/actions/") === 0;
      }).map((e) => e.url),
      // #307: fetch completions (url + status) — shows a held/released
      // response was actually delivered to the app (non-trivial guard).
      fetchCompletions: __net.completions.map((c) => ({ url: c.url, status: c.status })),
      // WS transport
      ws: S.ws ? { seq: S.ws._seq, readyState: S.ws.readyState } : null,
      wsConnects: __wsLog.connects.length,
      wsCloses: __wsLog.closes.length,
      // #307: the peak number of simultaneously-open sockets (replay the
      // connect/close timeline) and the count open right now. The feed
      // controller must never hold more than one open socket at a time, and
      // teardown (logout) must leave zero.
      wsMaxConcurrent: (function () {
        let open = 0, peak = 0;
        for (const ev of __wsLog.timeline) {
          if (ev.type === "connect") { open++; if (open > peak) peak = open; }
          else { open--; }
        }
        return peak;
      })(),
      wsOpenNow: (function () {
        let open = 0;
        for (const ev of __wsLog.timeline) {
          if (ev.type === "connect") open++;
          else open--;
        }
        return open;
      })(),
      wsFrames: __wsLog.frames.map((f) => f.data),
      intervals: __timers.intervals.size,
      // world / place / chat / tasks DOM
      hasWorldShell: !!document.querySelector(".world-shell"),
      hasPlaceShell: !!document.querySelector(".place-shell"),
      hasChatShell: !!document.querySelector(".chat-shell"),
      hasTasksShell: !!document.querySelector(".tasks-shell"),
      playerMarkerLocId: playerMarker ? playerMarker.getAttribute("data-location-id") : null,
      placeBadgeText: placeBadge ? placeBadge.textContent : null,
      placeNearbyRows: nearbyRows,
      placeNearbyEmpty: nearbyEmpty,
      placeSceneActInputValue: sceneActInput ? sceneActInput.value : null,
      placeSceneActResultText: placeResultNode ? placeResultNode.textContent : null,
      chatNpcSelectValue: chatNpcSel ? String(chatNpcSel.value) : null,
      chatInputValue: chatInput ? chatInput.value : null,
      chatLogText: chatLog ? chatLog.textContent : null,
      chatStatusText: chatStatus ? chatStatus.textContent : null,
      chatPresenceText: chatPresence ? chatPresence.textContent : null,
      tasksActiveText: tasksActive ? tasksActive.textContent : null,
      tasksHistoryText: tasksHistory ? tasksHistory.textContent : null,
      feedRows: feedBox ? feedBox.querySelectorAll(".ev").length : 0,
      // scene cache + drafts
      sceneStatus: S.scene ? S.scene.status : null,
      sceneLocId: S.scene ? S.scene.locationId : null,
      sceneAssetId: S.scene ? S.scene.assetId : null,
      // #307: whether the Place stage currently shows a decoded scene image
      // (a stale/old-location image must NOT be installed into the stage).
      placeStageHasImage: !!document.querySelector(
        ".place-stage .scene-image-host img"),
      sceneCacheSize: S.sceneCache ? S.sceneCache.size : 0,
      sceneActDraft: S.sceneActDraft,
      sceneAct: S.sceneAct ? {
        ok: S.sceneAct.ok, task_id: S.sceneAct.task_id,
        detail: S.sceneAct.detail,
      } : null,
      chatDraft: S.chatDraft,
      chatSessionId: S.chatSessionId,
      chatNpcId: S.chatNpcId,
      draftByNpc: g ? Object.assign({}, g.draftByNpc) : {},
      // task-presentation registry
      taskRegistry: registry,
      currentTaskId: (typeof globalThis.__currentTaskId === "function")
        ? globalThis.__currentTaskId() : null,
      // feed stream
      feedIds: (S.feed || []).map((e) => e.id),
      cursor: S.cursor,
      bootCursor: S.bootCursor === Infinity ? null : S.bootCursor,
      feedInit: S.feedInit,
      // session UI context
      guc: g ? {
        selectedPoiId: g.selectedPoiId,
        npcId: g.npcId,
        viewLocationId: g.viewLocationId,
        chatSourceLocationId: g.chatSourceLocationId,
      } : null,
      // #304: the submission feedback line (the busy/offline captions land
      // here via refreshTaskPresentationHosts, not a toast). The DOM host is
      // only present when mounted; the module-level taskSubmitFeedback is the
      // source of truth for the last submission-failure caption.
      taskSubmitLine: (function () {
        const line = document.querySelector(".task-submit-line");
        return line ? line.textContent : null;
      })(),
      taskSubmitFeedback: (typeof globalThis.__taskSubmitFeedback === "function")
        ? globalThis.__taskSubmitFeedback() : null,
      // toast
      toastText: toastEl ? toastEl.textContent : null,
      toastVisible: toastEl ? !toastEl.classList.contains("hidden") : false,
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
  globalThis.__releaseAt = __releaseAt;
  globalThis.__heldCount = __heldCount;
  globalThis.__clickButton = __clickButton;
  globalThis.__clickAnchor = __clickAnchor;
  globalThis.__fireKey = __fireKey;
  globalThis.__type = __type;
  globalThis.__stageButton = __stageButton;
  globalThis.__advance = __advance;
  globalThis.__pendingTimeouts = __pendingTimeouts;
  globalThis.__fireTimeout = __fireTimeout;
  globalThis.__releaseBlob = __releaseBlob;
  globalThis.__rejectBlob = __rejectBlob;
  globalThis.__blobHeldCount = __blobHeldCount;
  globalThis.__urlLog = __urlLog;
  globalThis.__resetUrlLog = () => { __urlLog.length = 0; };
  globalThis.Blob = Blob;
  globalThis.AbortController = AbortController;
})();
`;

// one boot = one fresh vm context (full isolation).
async function runPhase(name, spec) {
  __unhandled.length = 0;  // per-phase unhandled-rejection window
  const context = {
    console, queueMicrotask, Promise, JSON, Math, Object, Array, String,
    Number, Boolean, Set, Map, Error, RegExp, Symbol, parseInt,
    parseFloat, isNaN, isFinite, encodeURIComponent, decodeURIComponent,
    // #307: Date is used by the app (scene capturedAt, the chat typing
    // counter) — it must exist in the vm context.
    Date,
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
  const p = await runPhase(name, spec);
  return { name, ok: p.ok, result: p.result, unhandled: p.unhandled,
           ...(p.error ? { error: p.error } : {}) };
}

const out = [];
for (const spec of scenarios) {
  const r = await runScenarioAsync(spec.name, spec);
  out.push(r);
}
// force exit — a scenario's app may leave the vm context's event loop with
// pending bookkeeping that would otherwise keep the process alive. The write
// is ASYNC when stdout is a pipe (pytest's capture_output), so exit only in
// the flush callback — an immediate process.exit(0) truncates the output
// mid-string.
const data = JSON.stringify(out, null, 2);
process.stdout.write(data, () => process.exit(0));
"""


# ---------- fixtures (matching the app's API shapes) ----------

USER = {"id": 1, "username": "u", "email": "e@x", "role": "player", "sessionTag": "A"}

# The DIRECT-mode owner character at the origin (location 1).
CHAR1 = {
    "id": "1", "name": "P", "location_id": 1, "needs": {},
    "control_mode": "DIRECT", "is_owner": True, "money": 100,
}
# The authoritative player after the MOVE completes (location 2).
CHAR1_AT_2 = {**CHAR1, "location_id": 2}
# The AUTONOMOUS variant — moveTo() gates the move client-side (no POST).
CHAR1_AUTONOMOUS = {**CHAR1, "control_mode": "AUTONOMOUS"}

WORLD = {"id": 1, "seed": 42, "game_timestamp": 1500, "day": 2}
WEATHER_OFF = {"enabled": False}

# The Living Map requires ALL seven keyed POI types to carry coordinates for
# mapReady to be true. The origin (1) carries the origin NPC; the destination
# (2) carries the destination NPC (id 2, «Фар»).
NPC_IVAN = {"id": "101", "name": "Иван", "kind": "npc"}
NPC_FAR = {"id": "2", "name": "Фар", "kind": "npc"}
LOC1 = {"id": 1, "name": "Посёлок", "type": "settlement", "x": 45, "y": 50,
        "parent_id": None, "occupants": [NPC_IVAN]}
LOC2 = {"id": 2, "name": "Магазин", "type": "shop", "x": 50, "y": 45,
        "parent_id": 1, "occupants": [NPC_FAR]}
LOC3 = {"id": 3, "name": "Мастерская", "type": "workshop", "x": 42, "y": 56,
        "parent_id": 1, "occupants": []}
LOC4 = {"id": 4, "name": "Кухня", "type": "kitchen", "x": 47, "y": 51,
        "parent_id": 1, "occupants": []}
LOC5 = {"id": 5, "name": "Склад", "type": "storage", "x": 40, "y": 48,
        "parent_id": 1, "occupants": []}
LOC6 = {"id": 6, "name": "Колодец", "type": "well", "x": 48, "y": 55,
        "parent_id": 1, "occupants": []}
LOC7 = {"id": 7, "name": "Причал", "type": "pier", "x": 46, "y": 91,
        "parent_id": None, "occupants": []}
CATALOG = [LOC1, LOC2, LOC3, LOC4, LOC5, LOC6, LOC7]
# The destination with NO NPC occupant (the no-NPC failure case).
LOC2_NO_NPC = {**LOC2, "occupants": []}
CATALOG_NO_NPC = [LOC1, LOC2_NO_NPC, LOC3, LOC4, LOC5, LOC6, LOC7]

TASKS_EMPTY = {"active": [], "planned": [], "recent_terminal": []}
# The MOVE task (30701) — completed after the authoritative arrival.
TASK_30701 = {
    "id": 30701, "status": "completed", "task_type": "MOVE",
    "parameters": {"path": [1, 2]}, "created_at": 100,
    "started_at": 1500, "ends_at": 1510, "completed_at": 1510,
}
# The scene-action task (30702) — active after the scene act is accepted.
TASK_30702 = {
    "id": 30702, "status": "active", "task_type": "SOCIALIZE",
    "created_at": 101, "started_at": 1500,
}
# The scene-action task in the planned state (the acceptance's initial status).
TASK_30702_PLANNED = {**TASK_30702, "status": "planned"}

# A valid scene asset for the destination (2) — must pass BOTH validations in
# inspectSurroundings: asset.location_id === 2 AND
# asset.scene_descriptor.location.id === 2.
SCENE_ASSET_2 = {
    "asset": {
        "id": "sc2", "asset_type": "scene", "location_id": 2,
        "scene_descriptor": {"location": {"id": 2, "name": "Магазин"}},
    },
}
# A valid scene asset for the origin (1) — the stale-image failure case.
SCENE_ASSET_1 = {
    "asset": {
        "id": "sc1", "asset_type": "scene", "location_id": 1,
        "scene_descriptor": {"location": {"id": 1, "name": "Посёлок"}},
    },
}

# The dialogue fixtures — /dialogue/start ALWAYS returns session 30703 so the
# failed-message retry (which re-issues start because the session binds only at
# the message ACK) keeps the same session.
DIALOGUE_START = {"session_id": "30703"}
DIALOGUE_MESSAGE_OK = {
    "ok": True, "source": "llm",
    "messages": [
        {"sender": "player", "content": "Привет, Фар", "game_timestamp": 1500},
        {"sender": "npc", "content": "Привет", "game_timestamp": 1500},
    ],
}
DIALOGUE_HISTORY = {
    "session_id": "30703",
    "messages": [
        {"sender": "player", "content": "Привет, Фар", "game_timestamp": 1500},
        {"sender": "npc", "content": "Привет", "game_timestamp": 1500},
    ],
}

# The feed event (30704) — a live event beyond the boot cursor.
FEED_EVENT_30704 = {
    "id": 30704, "event_type": "CHARACTER_MOVED", "actor_id": "1",
    "location_id": 2, "game_timestamp": 1500,
    "text": "P дошёл до Магазина",
}


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


def _tasks_resp(planned=(), active=(), recent=()):
    """GET /characters/1/tasks → {planned, active, recent_terminal}."""
    return _resp(200, {
        "planned": list(planned),
        "active": list(active),
        "recent_terminal": list(recent),
    })


def _base(user=USER, char=CHAR1, catalog=None, tasks=None, world=WORLD,
          weather=WEATHER_OFF):
    """Authenticated boot + the world/place/chat/tasks loaders' GETs.

    This is the full endpoint map for the canonical journey. Individual
    scenarios override specific endpoints (e.g. /actions, /visual/scenes,
    /scene/act, /dialogue/*) by merging on top of this.
    """
    d = dict(
        _auth(user, char, [char]),
        **{
            "/world": _resp(200, world),
            "/weather": _resp(200, weather),
            "/locations": _resp(200, catalog if catalog is not None else CATALOG),
            # the profile view reads the character's location detail
            "/locations/1": _resp(200, LOC1),
            "/locations/2": _resp(200, LOC2),
            # the profile view's logout button POSTs /auth/logout
            "/auth/logout": _resp(200, {"ok": True}),
            "/world/events?since=0": _resp(200, []),
            "/world/events?since=0&limit=100": _resp(200, []),
            # the chat roles / relationship / portrait reads
            "/characters/2/roles": _resp(200, {"roles": []}),
            "/characters/1/roles": _resp(200, {"roles": []}),
            "/visual/characters/1/portrait": _resp(404, {"detail": "not found"}),
            "/visual/characters/2/portrait": _resp(404, {"detail": "not found"}),
            "/visual/references": _resp(200, {"items": []}),
            # the tasks screen reads
            "/characters/1/goals": _resp(200, []),
            "/characters/1/desire/opportunities": _resp(
                200, {"desire": None, "opportunities": []}),
        },
    )
    if tasks is not None:
        d[f"/characters/{char['id']}/tasks"] = tasks
    else:
        d[f"/characters/{char['id']}/tasks"] = _tasks_resp()
    return d


def _journey_responses(user=USER, char=CHAR1, catalog=None, tasks=None):
    """The full canonical-journey endpoint map (boot + all five mutations)."""
    d = _base(user=user, char=char, catalog=catalog, tasks=tasks)
    d.update({
        # the MOVE acceptance (task 30701)
        "/actions": _resp(200, {"task_id": 30701, "task_type": "MOVE",
                                "task_label": "Перемещение",
                                "status": "planned"}),
        "/actions/30701": _resp(200, {**TASK_30701, "status": "planned"}),
        # the scene inspection (destination 2)
        "/visual/scenes": _resp(200, SCENE_ASSET_2),
        "/visual/assets/sc2/file": _resp(200, None),
        # the scene action (task 30702)
        "/scene/act": _resp(200, {"ok": True, "task_id": 30702,
                                  "task_type": "SOCIALIZE",
                                  "task_label": "Пообщаться"}),
        "/actions/30702": _resp(200, {**TASK_30702, "status": "planned"}),
        # the dialogue
        "/dialogue/start": _resp(200, DIALOGUE_START),
        "/dialogue/30703/message": _resp(200, DIALOGUE_MESSAGE_OK),
        "/dialogue/30703": _resp(200, DIALOGUE_HISTORY),
    })
    return d


# ---------- runner ----------

def run_scenarios(scenarios):
    """Write the harness + scenarios to scratch, run node, return results."""
    os.makedirs(SCRATCH, exist_ok=True)
    harness_path = os.path.join(SCRATCH, "m307_harness.mjs")
    scen_path = os.path.join(SCRATCH, "m307_scenarios.json")
    with open(harness_path, "w", encoding="utf-8") as f:
        f.write(HARNESS_JS)
    with open(scen_path, "w", encoding="utf-8") as f:
        json.dump(scenarios, f)
    proc = subprocess.run(
        ["node", harness_path, APP_JS, scen_path],
        capture_output=True, text=True, timeout=180,
    )
    assert proc.returncode == 0, (
        f"node harness failed (rc={proc.returncode}):\n{proc.stderr}"
    )
    return {r["name"]: r for r in json.loads(proc.stdout)}


def facts(results, name):
    r = results[name]
    assert r.get("ok"), f"{name}: harness error: {r.get('error')}"
    return r["result"]


def unhandled(results, name):
    r = results[name]
    assert r.get("ok"), f"{name}: harness error: {r.get('error')}"
    return r.get("unhandled", [])
