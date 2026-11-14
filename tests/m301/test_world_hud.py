"""#301 — World HUD: the «Сейчас» band, island events, expand toggle.

Behavioral tests for the #301 World HUD machinery in the REAL production
``backend/app/web/app.js`` (boot included), run inside a Node ``vm`` context
using the embedded stdlib harness below (the m310 vm-harness pattern: the
same shim, the same driver helpers, the same response-stub structure —
extended with #301 facts). The harness only provides the browser surface
(a minimal DOM, fake timers, and a controlled network boundary); it does
NOT mock or reimplement ``route()``, ``api()``, ``viewWorld()``,
``renderWorld()``, ``nowPanel()``, ``islandEventsBlock()``, ``feedRow()``
or any of the #301 logic. Every assertion is on an observable fact: the
rendered DOM (the ``.world-now`` HUD band, the ``.world-island-events``
preview, the ``world-panel--expanded`` class, the ``#poi-card-host`` style
attr, the below-fold ``.world-action-pills``) and the fetch log (zero-POST
on a POI click — m310 semantics intact).

Approved behavior under test (derived from the work order, verified
against the code):
- ``nowPanel(tasksData, autonomous)`` mounts in ``.world-panel`` between
  the header and ``.world-collapsible``:
  - tasksData == null (the tasks fetch failed) → span.world-now-state
    «Данные о делах недоступны»;
  - empty active+planned → span.world-now-state «Сейчас нет активных дел»;
  - else active[0] else planned[0] → span.world-now-status = the #304
    lifecycle caption (taskPresentationText: active/STARTED →
    «Персонаж занят: {label}», planned/queued → «Задача принята.
    Ожидает начала», terminal → its own caption) + span.world-now-type =
    codeLabel(TASK_TYPE_RU, task_type) || "—";
  - autonomous → p.world-now-mode (the AUTONOMOUS hint) +
    button.world-now-profile «Открыть Профиль»;
  - always: button.world-panel-expand «Детали» (aria-expanded,
    aria-controls=world-panel-details) toggling .world-panel--expanded on
    the panel + setting the --poi-top style attr on #poi-card-host
    ("--poi-top: 156px" collapsed / "--poi-top: calc(42vh + 20px)"
    expanded), the label swaps to «Свернуть».
- ``islandEventsBlock()``: div.world-island-events with h2.world-sec-h
  «Остров» + up to 3 newest S.feed rows via feedRow (critical →
  ev-critical class via isCriticalEvent); null when the feed is empty.
- TASK_STATUS_RU gained queued: "в очереди" — no raw status code ever
  reaches the HUD text.
- The below-fold actions card in AUTONOMOUS mode renders NO pills.
"""
import json
import os
import subprocess

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
APP_JS = os.path.join(ROOT, "backend/app/web/app.js")
SCRATCH = "/tmp/opencode/m310-testing"

# The Node harness (m302/m310 pattern, reused verbatim for #301). It is
# infrastructure only: a minimal DOM / timer / fetch shim plus a fact
# collector and a controlled network boundary. No product behavior lives
# here. The #301 facts (worldNowText, worldNowState, worldNowStatus,
# worldNowType, nowModeText, nowProfileBtn, expandBtn, panelExpanded,
# poiHostStyle, islandBlock, islandHeading, islandRows, belowFoldPills)
# are collected in __facts; the m310 driver helpers (__clickButton,
# __setResponse, __settle, ...) are reused unchanged.
HARNESS_JS = r"""
// #301 behavioral harness (m310 pattern) — runs the REAL production app.js
// in a Node vm context with a minimal stdlib DOM/timer/fetch shim. It does
// NOT mock route()/api()/viewWorld()/renderWorld()/nowPanel()/
// islandEventsBlock()/feedRow() or reimplement any production logic; it
// only provides the browser surface (DOM, timers, network boundary) and
// collects observable facts (fetch log, DOM, S state). Python asserts on
// the facts.
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
    // #301 World HUD facts — the .world-now band, the expand control, the
    // island events preview, and the below-fold action pills.
    const worldNow = document.querySelector(".world-now");
    const worldNowState = document.querySelector(".world-now-state");
    const worldNowStatus = document.querySelector(".world-now-status");
    const worldNowType = document.querySelector(".world-now-type");
    const nowMode = document.querySelector(".world-now-mode");
    const nowProfile = document.querySelector(".world-now-profile");
    const expandBtn = document.querySelector(".world-panel-expand");
    const worldPanel = document.querySelector(".world-panel");
    const poiHost = document.getElementById("poi-card-host");
    const islandBlock = document.querySelector(".world-island-events");
    const islandRows = [];
    if (islandBlock) {
      (function walk(n) {
        for (const c of n._children) {
          if (c.nodeType !== 1) continue;
          if (c.classList.contains("ev"))
            islandRows.push({ className: c.className, text: c.textContent });
          walk(c);
        }
      })(islandBlock);
    }
    const pillsHost = document.querySelector(".world-action-pills");
    let belowFoldPills = 0;
    if (pillsHost) {
      (function walk(n) {
        for (const c of n._children) {
          if (c.nodeType !== 1) continue;
          if (c.tagName === "BUTTON") belowFoldPills++;
          walk(c);
        }
      })(pillsHost);
    }
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
      worldNow: !!worldNow,
      worldNowText: worldNow ? worldNow.textContent : null,
      worldNowState: worldNowState ? worldNowState.textContent : null,
      worldNowStatus: worldNowStatus ? worldNowStatus.textContent : null,
      worldNowType: worldNowType ? worldNowType.textContent : null,
      nowModeText: nowMode ? nowMode.textContent : null,
      nowProfileBtn: !!nowProfile,
      expandBtn: expandBtn ? {
        label: expandBtn.textContent,
        ariaExpanded: expandBtn.getAttribute("aria-expanded"),
        ariaControls: expandBtn.getAttribute("aria-controls"),
      } : null,
      panelExpanded: worldPanel
        ? worldPanel.classList.contains("world-panel--expanded") : null,
      poiHostStyle: poiHost ? poiHost.getAttribute("style") : null,
      islandBlock: !!islandBlock,
      islandBlockId: islandBlock ? islandBlock.getAttribute("id") : null,
      islandHeading: islandBlock
        ? (findDescendant(islandBlock, (n) => n.tagName === "H2")
           || { textContent: null }).textContent
        : null,
      islandRows: islandRows,
      belowFoldPills: belowFoldPills,
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
# The owner character — viewWorld() fetches /characters/1/tasks ONLY for the
# owner (me.is_owner), so every #301 tasks scenario boots with this.
CHAR1_OWNER = {**CHAR1, "is_owner": True}
# The AUTONOMOUS owner — the HUD carries the hint + Profile button and the
# below-fold actions card renders no pills.
CHAR1_AUTONOMOUS_OWNER = {**CHAR1, "control_mode": "AUTONOMOUS", "is_owner": True}
WORLD_A = {"game_timestamp": 1500, "day": 2}
WEATHER_OFF = {"enabled": False}

# The Living Map requires ALL seven keyed POI types to carry coordinates for
# mapReady to be true (renderWorld: keyedPois.every(k => poiMap[k] &&
# poiMap[k].x !== null)) — the same catalog as m310.
LOC7 = {"id": 7, "name": "Settlement", "type": "settlement", "x": 40, "y": 50, "occupants": []}
LOC9 = {"id": 9, "name": "Remote", "type": "shop", "x": 60, "y": 30, "occupants": []}
LOC10 = {"id": 10, "name": "Workshop", "type": "workshop", "x": 42.89, "y": 56.0, "occupants": []}
LOC11 = {"id": 11, "name": "Kitchen", "type": "kitchen", "x": 46.79, "y": 51.4, "occupants": []}
LOC12 = {"id": 12, "name": "Storage", "type": "storage", "x": 50, "y": 60, "occupants": []}
LOC13 = {"id": 13, "name": "Well", "type": "well", "x": 35, "y": 45, "occupants": []}
LOC14 = {"id": 14, "name": "Pier", "type": "pier", "x": 46, "y": 91, "occupants": []}
CATALOG = [LOC7, LOC9, LOC10, LOC11, LOC12, LOC13, LOC14]

# The localized AUTONOMOUS hint (app.js AUTONOMOUS_HINT) — the HUD's
# autonomy line observable.
AUTONOMOUS_HINT = "Персонаж действует сам — переключите режим в Профиле"

# Task fixtures — the server pre-orders active/planned oldest created_at
# first, so the HUD takes active[0] (else planned[0]) without re-sorting.
# #304: the stub rows carry realistic ids — the backend's task_to_dict
# ALWAYS includes `id` on /characters/{id}/tasks rows, and the #304 adapter
# keys/refreshes tracked tasks by that id (id-less rows are display-
# ineligible). Distinct ids keep the combined scenarios collision-free.
TASK_WORK_STARTED = {"id": 11, "status": "STARTED", "task_type": "WORK", "created_at": 100}
TASK_EAT_STARTED = {"id": 12, "status": "STARTED", "task_type": "EAT", "created_at": 90}
TASK_EAT_ACTIVE = {"id": 13, "status": "active", "task_type": "EAT", "created_at": 50}
TASK_WORK_QUEUED = {"id": 14, "status": "queued", "task_type": "WORK", "created_at": 100}
TASK_SLEEP_PLANNED = {"id": 15, "status": "planned", "task_type": "SLEEP", "created_at": 100}

# Five island events (ids 1-5, oldest first) — two critical (id 3
# CHARACTER_DIED, id 5 OBJECT_BURNING, both in CRITICAL_EVENT_TYPES). The
# preview must show exactly the three NEWEST (5, 4, 3) — the two oldest
# (ITEM_CONSUMED, CHARACTER_MOVED) are truncated out.
FEED_EVENTS = [
    {"id": 1, "event_type": "ITEM_CONSUMED", "game_timestamp": 1500, "actor_id": 2},
    {"id": 2, "event_type": "CHARACTER_MOVED", "game_timestamp": 1510, "actor_id": 3},
    {"id": 3, "event_type": "CHARACTER_DIED", "game_timestamp": 1520, "actor_id": 4},
    {"id": 4, "event_type": "TASK_COMPLETED", "game_timestamp": 1530, "actor_id": 5},
    {"id": 5, "event_type": "OBJECT_BURNING", "game_timestamp": 1540, "actor_id": None},
]


# Two live events (ids 101-102, oldest first) — the REST poll's
# /world/events?since=0 response. id 102 is critical (OBJECT_BURNING);
# neither is the player's own move (actor ids 2-3, no payload), so the
# arrival must not trigger onOwnArrival or a viewWorld re-render — the
# island preview can only change via refreshIslandPreview.
POLL_EVENTS = [
    {"id": 101, "event_type": "CHARACTER_MOVED", "game_timestamp": 1600, "actor_id": 2},
    {"id": 102, "event_type": "OBJECT_BURNING", "game_timestamp": 1610, "actor_id": 3},
]


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


def _world_base(user=USER_A, char=CHAR1_OWNER, catalog=None, tasks=None):
    """Authenticated boot + the world loaders' GETs + the tasks GET."""
    return dict(
        _auth(user, char, [char]),
        **{
            "/world": _resp(200, WORLD_A),
            "/weather": _resp(200, WEATHER_OFF),
            "/locations": _resp(200, catalog if catalog is not None else CATALOG),
            f"/characters/{char['id']}/tasks": tasks if tasks is not None
            else _tasks_resp(),
        },
    )


SCENARIOS = [
    # ---------- 1. ZERO_TASKS ----------
    {
        "name": "ZERO_TASKS",
        "initialHash": "#/world",
        "responses": _world_base(char=CHAR1_OWNER),
        "driver": (
            "await __settle(); const after = __facts(); "
            "globalThis.__result = after;"
        ),
    },
    # ---------- 2. TASKS_ENDPOINT_FAILED ----------
    {
        "name": "TASKS_ENDPOINT_FAILED",
        "initialHash": "#/world",
        "responses": _world_base(
            char=CHAR1_OWNER,
            tasks=_resp(500, {"detail": "internal error"}),
        ),
        "driver": (
            "await __settle(); const after = __facts(); "
            "globalThis.__result = after;"
        ),
    },
    # ---------- 3. ACTIVE_SELECTED ----------
    {
        "name": "ACTIVE_SELECTED",
        "initialHash": "#/world",
        "responses": _world_base(
            char=CHAR1_OWNER,
            tasks=_tasks_resp(active=[TASK_WORK_STARTED, TASK_EAT_ACTIVE]),
        ),
        "driver": (
            "await __settle(); const after = __facts(); "
            "globalThis.__result = after;"
        ),
    },
    # ---------- 4. PLANNED_FALLBACK ----------
    {
        "name": "PLANNED_FALLBACK",
        "initialHash": "#/world",
        "responses": _world_base(
            char=CHAR1_OWNER,
            tasks=_tasks_resp(planned=[TASK_SLEEP_PLANNED]),
        ),
        "driver": (
            "await __settle(); const after = __facts(); "
            "globalThis.__result = after;"
        ),
    },
    # ---------- 5. QUEUED_COPY ----------
    {
        "name": "QUEUED_COPY",
        "initialHash": "#/world",
        "responses": _world_base(
            char=CHAR1_OWNER,
            tasks=_tasks_resp(active=[TASK_WORK_QUEUED]),
        ),
        "driver": (
            "await __settle(); const after = __facts(); "
            "globalThis.__result = after;"
        ),
    },
    # ---------- 6. ACTIVE_OVER_PLANNED ----------
    {
        "name": "ACTIVE_OVER_PLANNED",
        "initialHash": "#/world",
        "responses": _world_base(
            char=CHAR1_OWNER,
            tasks=_tasks_resp(
                active=[TASK_WORK_STARTED], planned=[TASK_SLEEP_PLANNED]),
        ),
        "driver": (
            "await __settle(); const after = __facts(); "
            "globalThis.__result = after;"
        ),
    },
    # ---------- 7. AUTONOMOUS_HUD ----------
    {
        "name": "AUTONOMOUS_HUD",
        "initialHash": "#/world",
        "responses": _world_base(char=CHAR1_AUTONOMOUS_OWNER),
        "driver": (
            "await __settle(); const before = __facts(); "
            "const clicked = __clickButton('Remote'); await __settle(); "
            "const after = __facts(); "
            "globalThis.__result = { before, clicked, after };"
        ),
    },
    # ---------- 8. ISLAND_EVENTS ----------
    {
        "name": "ISLAND_EVENTS",
        "initialHash": "#/world",
        "responses": _world_base(char=CHAR1_OWNER),
        "driver": (
            "await __settle(); "
            "const evs = " + json.dumps(FEED_EVENTS) + "; "
            "__S.feed.push(...evs); "
            "await __call('viewWorld'); await __settle(); "
            "const after = __facts(); "
            "globalThis.__result = after;"
        ),
    },
    # ---------- 9. EXPAND_TOGGLE ----------
    {
        "name": "EXPAND_TOGGLE",
        "initialHash": "#/world",
        "responses": _world_base(char=CHAR1_OWNER),
        "driver": (
            "await __settle(); const before = __facts(); "
            "const clicked1 = __clickButton('Детали'); await __settle(); "
            "const expanded = __facts(); "
            "const clicked2 = __clickButton('Свернуть'); await __settle(); "
            "const collapsed = __facts(); "
            "globalThis.__result = "
            "{ before, clicked1, expanded, clicked2, collapsed };"
        ),
    },
    # ---------- 10. NO_RAW_STATUS ----------
    {
        "name": "NO_RAW_STATUS",
        "initialHash": "#/world",
        "responses": _world_base(
            char=CHAR1_OWNER,
            tasks=_tasks_resp(
                active=[TASK_WORK_QUEUED, TASK_EAT_STARTED],
                planned=[TASK_SLEEP_PLANNED],
            ),
        ),
        "driver": (
            "await __settle(); const after = __facts(); "
            "globalThis.__result = after;"
        ),
    },
    # ---------- 11. ISLAND_REFRESH_ON_POLL ----------
    # The island preview must follow a REAL feed arrival through the
    # existing poll plumbing (not a direct refreshIslandPreview call):
    # boot with an empty S.feed (the WS shim never delivers a hello, so
    # the 5s hello deadline settles feedInit to "rest" and the 5s poll
    # tick fetches /world/events?since=0), then assert the preview gains
    # its rows in place.
    {
        "name": "ISLAND_REFRESH_ON_POLL",
        "initialHash": "#/world",
        "responses": {
            **_world_base(char=CHAR1_OWNER),
            "/world/events?since=0": _resp(200, POLL_EVENTS),
        },
        "driver": (
            "await __settle(); "
            "const before = __facts(); "
            "__advance(5000); await __settle(); "
            "const after = __facts(); "
            "const worldEventsFetches = __net.log.filter("
            "e => e.url.startsWith('/world/events')).length; "
            "globalThis.__result = { before, after, worldEventsFetches };"
        ),
    },
]


def _run_harness():
    os.makedirs(SCRATCH, exist_ok=True)
    harness_path = os.path.join(SCRATCH, "m301_harness.mjs")
    scen_path = os.path.join(SCRATCH, "m301_scenarios.json")
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


# ---------- 1. ZERO_TASKS ----------

def test_zero_tasks(results):
    r = _facts(results, "ZERO_TASKS")
    assert r["worldNow"] is True, "the .world-now HUD band is mounted in the panel"
    assert r["worldNowState"] == "Сейчас нет активных дел", (
        f"empty active+planned renders the empty-state line, "
        f"got {r['worldNowState']!r}"
    )
    assert "Сейчас нет активных дел" in r["worldNowText"], (
        f"the HUD text carries the empty-state phrase, got {r['worldNowText']!r}"
    )
    assert r["worldNowStatus"] is None, (
        "no task node (status/type) may render for an empty task list, "
        f"got {r['worldNowStatus']!r}"
    )
    assert r["belowFoldPills"] == 6, (
        f"the MANUAL below-fold actions card keeps its 6 pills, "
        f"got {r['belowFoldPills']}"
    )
    unhandled = _unhandled(results, "ZERO_TASKS")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 2. TASKS_ENDPOINT_FAILED ----------

def test_tasks_endpoint_failed(results):
    r = _facts(results, "TASKS_ENDPOINT_FAILED")
    assert r["worldNowState"] == "Данные о делах недоступны", (
        f"a failed tasks fetch renders the unavailable state, "
        f"got {r['worldNowState']!r}"
    )
    assert "Сейчас нет активных дел" not in r["worldNowText"], (
        "the unavailable state must not be confused with the empty state, "
        f"got {r['worldNowText']!r}"
    )
    assert r["worldNowStatus"] is None, (
        "no task node may render when the tasks data is unavailable, "
        f"got {r['worldNowStatus']!r}"
    )
    unhandled = _unhandled(results, "TASKS_ENDPOINT_FAILED")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 3. ACTIVE_SELECTED ----------

def test_active_selected(results):
    r = _facts(results, "ACTIVE_SELECTED")
    # #304: the HUD status is the lifecycle caption of active[0] — an
    # active/STARTED task reads «Персонаж занят: {label}» (the raw-code
    # label «начата» is gone; the caption still derives from the server
    # status, never from the POST response).
    assert r["worldNowStatus"] == "Персонаж занят: работа", (
        f"the HUD status is the #304 lifecycle caption of active[0] "
        f"(STARTED/WORK → «Персонаж занят: работа»), got {r['worldNowStatus']!r}"
    )
    assert r["worldNowType"] == "работа", (
        f"the HUD type is the Russian label of active[0] "
        f"(WORK → «работа»), got {r['worldNowType']!r}"
    )
    assert "еда" not in r["worldNowText"], (
        "only active[0] is shown — the second active task's type "
        f"(EAT → «еда») must be absent from the HUD, got {r['worldNowText']!r}"
    )
    assert r["worldNowState"] is None, (
        "a task node renders, not a state line, "
        f"got {r['worldNowState']!r}"
    )
    unhandled = _unhandled(results, "ACTIVE_SELECTED")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 4. PLANNED_FALLBACK ----------

def test_planned_fallback(results):
    r = _facts(results, "PLANNED_FALLBACK")
    # #304: the planned fallback renders the accepted caption — a planned
    # task is a ACCEPTED task awaiting its start, not «запланирована».
    assert r["worldNowStatus"] == "Задача принята. Ожидает начала", (
        f"an empty active list falls back to planned[0] "
        f"(#304 planned → «Задача принята. Ожидает начала»), "
        f"got {r['worldNowStatus']!r}"
    )
    assert r["worldNowType"] == "сон", (
        f"the planned task's type renders (SLEEP → «сон»), "
        f"got {r['worldNowType']!r}"
    )
    unhandled = _unhandled(results, "PLANNED_FALLBACK")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 5. QUEUED_COPY ----------

def test_queued_copy(results):
    r = _facts(results, "QUEUED_COPY")
    # #304: queued renders the accepted caption (the dedicated «в очереди»
    # copy is superseded — queued and planned share the accepted phase).
    assert r["worldNowStatus"] == "Задача принята. Ожидает начала", (
        f"queued maps to the #304 accepted caption, "
        f"got {r['worldNowStatus']!r}"
    )
    assert "queued" not in r["worldNowText"], (
        f"the raw code must never reach the HUD text, got {r['worldNowText']!r}"
    )
    unhandled = _unhandled(results, "QUEUED_COPY")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 6. ACTIVE_OVER_PLANNED ----------

def test_active_over_planned(results):
    r = _facts(results, "ACTIVE_OVER_PLANNED")
    # #304: the active task wins over the planned one (caption form).
    assert r["worldNowStatus"] == "Персонаж занят: работа", (
        f"the active task wins over the planned one "
        f"(#304 caption), got {r['worldNowStatus']!r}"
    )
    assert r["worldNowType"] == "работа", (
        f"the HUD shows the active task's type (WORK → «работа»), "
        f"got {r['worldNowType']!r}"
    )
    # #304: the planned task must not be the selected one — its accepted
    # caption and its type label are both absent. (The bare «сон» substring
    # scan is superseded: the active caption «Персонаж занят: …» itself
    # contains «сон» inside «Персонаж», so the check moved to the precise
    # markers — the planned caption in the band text, the planned type in
    # the type span.)
    assert "Задача принята" not in r["worldNowText"], (
        f"the planned task's accepted caption must be absent, "
        f"got {r['worldNowText']!r}"
    )
    assert "сон" not in r["worldNowType"], (
        f"the type span shows the active task's type, not the planned "
        f"task's (SLEEP → «сон»), got {r['worldNowType']!r}"
    )
    unhandled = _unhandled(results, "ACTIVE_OVER_PLANNED")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 7. AUTONOMOUS_HUD ----------

def test_autonomous_hud(results):
    r = _facts(results, "AUTONOMOUS_HUD")
    before, clicked, after = r["before"], r["clicked"], r["after"]
    assert before["S_charControl"] == "AUTONOMOUS", "the character is AUTONOMOUS"
    assert before["nowModeText"] == AUTONOMOUS_HINT, (
        f"the HUD carries the autonomy hint line, got {before['nowModeText']!r}"
    )
    assert before["nowProfileBtn"] is True, (
        "the HUD offers the «Открыть Профиль» button"
    )
    assert before["belowFoldPills"] == 0, (
        f"the AUTONOMOUS below-fold actions card renders NO pills, "
        f"got {before['belowFoldPills']}"
    )
    assert clicked is True, "the remote POI marker is clickable"
    assert after["postActions"] == 0, (
        f"a POI click still issues ZERO POST /actions (m310 semantics), "
        f"got {after['postActions']}"
    )
    assert after["S_charLocId"] == 7, "a POI click must not move the player"
    unhandled = _unhandled(results, "AUTONOMOUS_HUD")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 8. ISLAND_EVENTS ----------

def test_island_events(results):
    r = _facts(results, "ISLAND_EVENTS")
    assert r["islandBlock"] is True, "the .world-island-events preview is rendered"
    assert r["islandHeading"] == "Остров", (
        f"the preview heading is «Остров», got {r['islandHeading']!r}"
    )
    rows = r["islandRows"]
    assert len(rows) == 3, (
        f"the preview shows at most 3 rows (5 seeded), got {len(rows)}"
    )
    assert [row["className"] for row in rows] == [
        "ev ev-critical", "ev", "ev ev-critical",
    ], (
        "the critical rows (ids 5 and 3) carry ev-critical, got "
        f"{[row['className'] for row in rows]!r}"
    )
    assert "OBJECT_BURNING" in rows[0]["text"], (
        f"the newest event (id 5) is first, got {rows[0]['text']!r}"
    )
    assert "TASK_COMPLETED" in rows[1]["text"], (
        f"the second-newest event (id 4) is second, got {rows[1]['text']!r}"
    )
    assert "CHARACTER_DIED" in rows[2]["text"], (
        f"the third-newest event (id 3) is third, got {rows[2]['text']!r}"
    )
    joined = "".join(row["text"] for row in rows)
    assert "ITEM_CONSUMED" not in joined and "CHARACTER_MOVED" not in joined, (
        f"the two oldest events (ids 1-2) are truncated out, got {joined!r}"
    )
    unhandled = _unhandled(results, "ISLAND_EVENTS")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 9. EXPAND_TOGGLE ----------

def test_expand_toggle(results):
    r = _facts(results, "EXPAND_TOGGLE")
    before, clicked1, expanded = r["before"], r["clicked1"], r["expanded"]
    clicked2, collapsed = r["clicked2"], r["collapsed"]
    assert before["expandBtn"] is not None, "the «Детали» expand button is present"
    assert before["expandBtn"]["label"] == "Детали", (
        f"the initial label is «Детали», got {before['expandBtn']['label']!r}"
    )
    assert before["expandBtn"]["ariaExpanded"] == "false", (
        f"the initial aria-expanded is false, "
        f"got {before['expandBtn']['ariaExpanded']!r}"
    )
    assert before["expandBtn"]["ariaControls"] == "world-panel-details", (
        f"aria-controls targets the collapsible, "
        f"got {before['expandBtn']['ariaControls']!r}"
    )
    assert before["panelExpanded"] is False, "the panel starts collapsed"
    assert before["poiHostStyle"] is None, (
        "no --poi-top style attr on #poi-card-host before the first toggle"
    )
    assert clicked1 is True, "the «Детали» button is clickable"
    assert expanded["panelExpanded"] is True, (
        "the panel gains world-panel--expanded on expand"
    )
    assert expanded["expandBtn"]["ariaExpanded"] == "true", (
        f"aria-expanded flips to true, got {expanded['expandBtn']['ariaExpanded']!r}"
    )
    assert expanded["expandBtn"]["label"] == "Свернуть", (
        f"the label swaps to «Свернуть», got {expanded['expandBtn']['label']!r}"
    )
    assert expanded["poiHostStyle"] == "--poi-top: calc(42vh + 20px)", (
        f"the expanded POI card offset, got {expanded['poiHostStyle']!r}"
    )
    assert clicked2 is True, "the «Свернуть» button is clickable"
    assert collapsed["panelExpanded"] is False, (
        "the panel loses world-panel--expanded on collapse"
    )
    assert collapsed["expandBtn"]["ariaExpanded"] == "false", (
        f"aria-expanded flips back to false, "
        f"got {collapsed['expandBtn']['ariaExpanded']!r}"
    )
    assert collapsed["expandBtn"]["label"] == "Детали", (
        f"the label swaps back to «Детали», got {collapsed['expandBtn']['label']!r}"
    )
    assert collapsed["poiHostStyle"] == "--poi-top: 156px", (
        f"the collapsed POI card offset, got {collapsed['poiHostStyle']!r}"
    )
    unhandled = _unhandled(results, "EXPAND_TOGGLE")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 10. NO_RAW_STATUS ----------

def test_no_raw_status(results):
    r = _facts(results, "NO_RAW_STATUS")
    # #304: active[0] (queued) renders the accepted caption — the no-raw-code
    # doctrine is unchanged (the caption carries no status code either).
    assert r["worldNowStatus"] == "Задача принята. Ожидает начала", (
        f"active[0] (queued) renders its #304 caption, "
        f"got {r['worldNowStatus']!r}"
    )
    assert r["worldNowType"] == "работа", (
        f"active[0]'s type renders (WORK → «работа»), got {r['worldNowType']!r}"
    )
    text = r["worldNowText"]
    for raw in ("queued", "STARTED", "planned"):
        assert raw not in text, (
            f"the raw status code {raw!r} must not appear in the HUD text, "
            f"got {text!r}"
        )
    unhandled = _unhandled(results, "NO_RAW_STATUS")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 11. ISLAND_REFRESH_ON_POLL ----------

def test_island_refresh_on_poll(results):
    r = _facts(results, "ISLAND_REFRESH_ON_POLL")
    before, after = r["before"], r["after"]
    # 1. cold entry: the container is ALWAYS present (stable id) so the
    #    feed updaters can target it; empty feed → zero-height div, no
    #    heading, no rows.
    assert before["islandBlock"] is True, (
        "the .world-island-events container is rendered on a cold entry"
    )
    assert before["islandBlockId"] == "world-island-events", (
        f"the container carries the stable id the updaters target, "
        f"got {before['islandBlockId']!r}"
    )
    assert before["islandHeading"] is None, (
        f"an empty feed renders no «Остров» heading, "
        f"got {before['islandHeading']!r}"
    )
    assert before["islandRows"] == [], (
        f"an empty feed renders zero rows, got {before['islandRows']!r}"
    )
    # 2. a real REST poll arrival (hello deadline → feedInit "rest" → the
    #    5s poll tick's /world/events?since=0) must refresh the preview in
    #    place: h2 «Остров» + min(3, events) rows, newest first.
    assert after["islandHeading"] == "Остров", (
        f"after the poll arrival the preview gains the «Остров» heading, "
        f"got {after['islandHeading']!r}"
    )
    rows = after["islandRows"]
    assert len(rows) == 2, (
        f"the preview shows min(3, events) = 2 rows, got {len(rows)}"
    )
    assert rows[0]["className"] == "ev ev-critical", (
        f"the newest event (id 102, OBJECT_BURNING) is first and critical, "
        f"got {rows[0]!r}"
    )
    assert "OBJECT_BURNING" in rows[0]["text"], (
        f"the newest event is first, got {rows[0]['text']!r}"
    )
    assert rows[1]["className"] == "ev", (
        f"the older event (id 101) is second as a plain row, got {rows[1]!r}"
    )
    assert "CHARACTER_MOVED" in rows[1]["text"], (
        f"the older event is second, got {rows[1]['text']!r}"
    )
    # 3. the rows use the same feedRow shape as #feed (className "ev"), and
    #    the refresh re-derives from S.feed — no second /world/events
    #    fetch beyond the existing poll.
    assert all(row["className"].startswith("ev") for row in rows), (
        f"the preview rows use the feedRow 'ev' shape, got "
        f"{[row['className'] for row in rows]!r}"
    )
    assert r["worldEventsFetches"] == 1, (
        f"the fix re-derives from S.feed — exactly ONE /world/events call "
        f"(the existing poll), got {r['worldEventsFetches']}"
    )
    unhandled = _unhandled(results, "ISLAND_REFRESH_ON_POLL")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"
