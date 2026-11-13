"""#303 — Place game shell: the state-aware place screen.

Behavioral tests for the #303 place game-shell in the REAL production
``backend/app/web/app.js`` (boot included), run inside a Node ``vm`` context
using the embedded stdlib harness below (the m301/m310 vm-harness pattern:
the same shim, the same driver helpers, the same response-stub structure —
extended with #303 place facts). The harness only provides the browser
surface (a minimal DOM, fake timers, and a controlled network boundary); it
does NOT mock or reimplement ``route()``, ``api()``, ``viewLocation()``,
``renderPlace()``, ``doAction()``, ``moveTo()``, ``submitSceneAct()`` or any
of the #303 logic. Every assertion is on an observable fact: the rendered
DOM (the ``main.place-shell.place-game`` grid, the ``.place-topbar`` back
button, the ``.place-context`` panel, the ``.place-badge``, the
``.place-actions-block``, the ``.place-scene-act`` bar, the
``details.place-about`` reference level) and the fetch log (zero-POST on
entry, the MOVE/EAT POST bodies, the single /scene/act POST).

Approved behavior under test (derived from the work order, not the code):
- ``renderPlace(locId, place, locs, tasksData)`` mounts
  ``main.place-shell.place-game`` = topbar(``#/world``) + ``.place-topbar``
  («← Остров» button with the old manual-focus + gotoWorld mechanics + the
  title span) + ``.place-grid`` (``.place-stage`` static placeholder left;
  ``section.panel.place-context`` right).
- ``isCurrent = locId === S.character.location_id``. Current: badge «Вы
  здесь»; the «Действия здесь» block = ACTION_VOCABULARY (WORK/EAT/DRINK/
  SLEEP/SOCIALIZE) buttons → doAction(a) + the honest hint; the scene-act
  bar (``sceneActBar(submitPlaceSceneAct)``) + the
  ``.place-scene-act-result`` host; «Поговорить» per NPC (gated by the
  existing placeDialogueNpcs → current only). Remote: badge «Другое
  место»; «Вы сейчас: {catalog name by S.character.location_id}»; the
  single CTA «Пойти сюда» → the existing moveTo(place); NO actions /
  dialog / scene-act / «Вы здесь».
- «Рядом» = occupants filtered kind === "npc", name rows only (the API
  gives exactly {id,name,kind}); zero → «Рядом никого нет.»; a player-kind
  occupant never appears in the slice.
- Task line: tasksData == null → «Данные о делах недоступны»; active[0]
  else planned[0] → codeLabel(TASK_STATUS_RU, status) ·
  codeLabel(TASK_TYPE_RU, task_type); empty → «нет активной задачи».
  tasksData is fetched in viewLocation (``/characters/{id}/tasks``, own
  catch, gate ``S.character.is_owner !== false``).
- «О месте» = native ``<details class="place-about">``: ID/Тип/Координаты/
  Входит в/Людей сейчас + the full roster (no affordances) + the events
  listing. ID/coordinates must NOT appear in the primary (always-visible)
  view.
- viewLocation keeps its stale-response guards, the retry on transient
  failure, and the honest «недоступно» for missing world/weather.
- Entering a place (current or remote) issues NO POST — GETs only.
"""
import json
import os
import subprocess

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
APP_JS = os.path.join(ROOT, "backend/app/web/app.js")
SCRATCH = "/tmp/opencode/m303-testing"

HARNESS_JS = r"""
// #303 behavioral harness (m310 pattern) — runs the REAL production app.js in a Node vm
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

// #303: unhandled rejections are a first-class observable — a stale place
// load or a superseded load that throws must not escape.
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
    const main = document.querySelector("main.place-shell.place-game");
    // Composition-agnostic "the place screen rendered" guard (present on
    // both the #303 shell and the pre-#303 base) — the contract-neutral
    // scenarios (no-POST entry, retry, stale guard) assert this, not the
    // new .place-game class.
    const placeShell = document.querySelector("main.place-shell");
    // Composition-agnostic rendered place name: .place-context-name on the
    // #303 shell, the hero <h2> on the base — the stale-guard scenario
    // distinguishes "place 9 rendered" from "place 7 rendered" on both.
    const heroEl = document.querySelector(".place-hero");
    const placeNameEl = document.querySelector(".place-context-name")
      || (heroEl ? findDescendant(heroEl, (n) => n.tagName === "H2") : null);
    const topbarEl = document.querySelector(".place-topbar");
    const backBtn = topbarEl
      ? findDescendant(topbarEl, (n) => n.tagName === "BUTTON") : null;
    const titleSpan = topbarEl
      ? findDescendant(topbarEl, (n) => n.tagName === "SPAN") : null;
    const grid = document.querySelector(".place-grid");
    const stage = document.querySelector(".place-stage");
    const context = document.querySelector(".panel.place-context");
    const contextNameEl = document.querySelector(".place-context-name");
    // Always-visible context text — the <details> subtree is EXCLUDED
    // (the reference level is collapsed; its content must not leak into
    // the primary view).
    let contextTextNoDetails = "";
    if (context) {
      (function walk(n) {
        for (const c of n._children) {
          if (c.nodeType === 3) contextTextNoDetails += c.textContent;
          else if (c.nodeType === 1 && c.tagName !== "DETAILS") walk(c);
        }
      })(context);
    }
    const badge = document.querySelector(".place-badge");
    const actionsBlock = document.querySelector(".place-actions-block");
    const actionButtons = [];
    if (actionsBlock) {
      (function walk(n) {
        for (const c of n._children) {
          if (c.nodeType !== 1) continue;
          if (c.tagName === "BUTTON") actionButtons.push(c.textContent);
          walk(c);
        }
      })(actionsBlock);
    }
    const sceneAct = document.querySelector(".place-scene-act");
    const sceneActInput = document.getElementById("scene-act-input");
    const resultHost = document.querySelector(".place-scene-act-result");
    const resultNode = document.getElementById("scene-act-result");
    const goHere = findDescendant(documentElement,
      (n) => n.tagName === "BUTTON" && n.textContent === "Пойти сюда");
    const youAreAt = context
      ? findDescendant(context, (n) => n.nodeType === 1
          && n.textContent.indexOf("Вы сейчас: ") === 0)
      : null;
    const nearby = document.querySelector(".place-nearby");
    const nearbyRows = [];
    let nearbyEmpty = false;
    if (nearby) {
      nearbyEmpty = nearby.textContent.indexOf("Рядом никого нет.") >= 0;
      (function walk(n) {
        for (const c of n._children) {
          if (c.nodeType !== 1) continue;
          if (c.tagName === "DIV"
              && (c.className || "").indexOf("place-nearby-row") >= 0) {
            const span = findDescendant(c, (n) => n.tagName === "SPAN");
            const btn = findDescendant(c, (n) => n.tagName === "BUTTON");
            nearbyRows.push({
              name: span ? span.textContent : null,
              dialog: !!btn,
            });
          }
          walk(c);
        }
      })(nearby);
    }
    let dialogButtonCount = 0;
    (function walk(n) {
      for (const c of n._children) {
        if (c.nodeType !== 1) continue;
        if (c.tagName === "BUTTON" && c.textContent === "Поговорить") dialogButtonCount++;
        walk(c);
      }
    })(documentElement);
    const taskEl = document.querySelector(".place-task");
    const details = document.querySelector("details.place-about");
    const detailsSummary = details
      ? (findDescendant(details, (n) => n.tagName === "SUMMARY")
          || { textContent: null }).textContent
      : null;
    // The weather/time lines: inside .place-context on the #303 shell; on a
    // base app.js (no .place-context) the fallback walks the whole document
    // so the honest-missing wording stays observable (RED authenticity).
    const weatherLine = findDescendant(
      context || documentElement,
      (n) => n.nodeType === 1 && n.textContent.indexOf("Погода в мире: ") === 0);
    const timeLine = findDescendant(
      context || documentElement,
      (n) => n.nodeType === 1 && n.textContent.indexOf("Время мира: ") === 0);
    const retryBtn = findDescendant(documentElement,
      (n) => n.tagName === "BUTTON" && n.textContent === "Повторить");
    const failLine = findDescendant(documentElement,
      (n) => n.tagName === "SPAN" && n.textContent.indexOf("Загрузка не удалась") === 0);
    const toastEl = document.getElementById("toast");
    const postActions = __net.log.filter(
      e => (e.opts.method || "GET").toUpperCase() === "POST" && e.url === "/actions");
    const postSceneAct = __net.log.filter(
      e => (e.opts.method || "GET").toUpperCase() === "POST" && e.url === "/scene/act");
    // DEFECT-1: the Place screen must never call Flux (/visual/scenes) —
    // there is no #scene-view host to display it.
    const postVisualScenes = __net.log.filter(
      e => (e.opts.method || "GET").toUpperCase() === "POST" && e.url === "/visual/scenes");
    const posts = __net.log.filter(
      e => (e.opts.method || "GET").toUpperCase() === "POST");
    return {
      hash: location.hash,
      hasPlaceShell: !!placeShell,
      placeName: placeNameEl ? placeNameEl.textContent : null,
      hasPlaceGame: !!main,
      hasPlaceGrid: !!grid,
      hasPlaceStage: !!stage,
      hasPlaceContext: !!context,
      hasPlaceTopbar: !!topbarEl,
      backButtonText: backBtn ? backBtn.textContent : null,
      topbarTitle: titleSpan ? titleSpan.textContent : null,
      stageText: stage ? stage.textContent : null,
      contextName: contextNameEl ? contextNameEl.textContent : null,
      contextTextNoDetails,
      badgeText: badge ? badge.textContent : null,
      badgeHere: !!document.querySelector(".place-badge-here"),
      hasActionsBlock: !!actionsBlock,
      actionButtons,
      hasSceneAct: !!sceneAct,
      hasSceneActInput: !!sceneActInput,
      sceneActResultInHost: !!(resultNode && resultHost && resultHost.contains(resultNode)),
      sceneActResultText: resultNode ? resultNode.textContent : null,
      hasGoHere: !!goHere,
      youAreAtText: youAreAt ? youAreAt.textContent : null,
      nearbyRows,
      nearbyEmpty,
      dialogButtonCount,
      taskText: taskEl ? taskEl.textContent : null,
      hasDetails: !!details,
      detailsSummary,
      detailsText: details ? details.textContent : null,
      detailsInContext: !!(details && context && context.contains(details)),
      weatherLineText: weatherLine ? weatherLine.textContent : null,
      timeLineText: timeLine ? timeLine.textContent : null,
      hasRetryButton: !!retryBtn,
      failLineText: failLine ? failLine.textContent : null,
      S_charLocId: S.character ? S.character.location_id : null,
      S_worldTs: S.world ? S.world.game_timestamp : null,
      guc: g ? {
        npcId: g.npcId,
        viewLocationId: g.viewLocationId,
        selectedLocationId: g.selectedLocationId,
      } : null,
      postActions: postActions.length,
      postActionBodies: postActions.map(e => {
        try { return JSON.parse(e.opts.body); } catch (err) { return e.opts.body; }
      }),
      postSceneAct: postSceneAct.length,
      postSceneActBodies: postSceneAct.map(e => {
        try { return JSON.parse(e.opts.body); } catch (err) { return e.opts.body; }
      }),
      postVisualScenes: postVisualScenes.length,
      postCount: posts.length,
      getPaths: __net.log.filter(
        e => (e.opts.method || "GET").toUpperCase() === "GET").map(e => e.url),
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
# The owner character — viewLocation fetches /characters/{id}/tasks only
# when is_owner !== false, so the task-line legs boot with this.
CHAR1_OWNER = {**CHAR1, "is_owner": True}
# DEFECT-4: the cold-bootstrap by-user payload — a deep link / F5 to
# #/location/{id} boots from /characters/by-user/{uid}, which carries NO
# location_id (only the World poll's /characters/{id} does). viewLocation
# must self-refresh S.character from that source, or the current place
# renders as remote.
BY_USER_NO_LOC = {
    "id": 1, "name": "P", "needs": {},
    "control_mode": "DIRECT", "is_owner": True, "money": 100,
}
WORLD_A = {"game_timestamp": 1500, "day": 2}
WEATHER_OFF = {"enabled": False}
WEATHER_ON = {"enabled": True, "description": "Ясно"}

# Occupant fixtures — the API gives exactly {id, name, kind}.
NPC_IVAN = {"id": "npc_1", "name": "Иван", "kind": "npc"}
NPC_MARIA = {"id": "npc_2", "name": "Мария", "kind": "npc"}
NPC_PETR = {"id": "npc_3", "name": "Пётр", "kind": "npc"}
PLAYER_OCC = {"id": 2, "name": "Q", "kind": "player"}

LOC7 = {"id": 7, "name": "Settlement", "type": "settlement", "x": 40, "y": 50, "occupants": []}
LOC9 = {"id": 9, "name": "Remote", "type": "shop", "x": 60, "y": 30, "occupants": []}
LOC10 = {"id": 10, "name": "Workshop", "type": "workshop", "x": 42, "y": 56, "occupants": []}
CATALOG = [LOC7, LOC9, LOC10]
# NPCs at both the current (7) and the remote (9) place — the dialog gate.
CATALOG_NPC_BOTH = [
    {**LOC7, "occupants": [NPC_IVAN]},
    {**LOC9, "occupants": [NPC_MARIA]},
    LOC10,
]
# The «Рядом» slice census: zero / one / many (+ a player-kind occupant
# that must never appear in the NPC slice).
CATALOG_SLICES = [
    LOC7,
    {**LOC9, "occupants": [NPC_IVAN]},
    {**LOC10, "occupants": [NPC_MARIA, NPC_PETR, PLAYER_OCC]},
]
# DEFECT-3: an unnamed place (name "") + an unnamed NPC occupant (name "") —
# the raw #id fallback in the primary view (the fix renders «Без названия» /
# «Без имени»; the ID belongs in the <details> reference level only).
NPC_UNNAMED = {"id": "npc_1", "name": "", "kind": "npc"}
LOC7_UNNAMED = {**LOC7, "name": "", "occupants": [NPC_UNNAMED]}
CATALOG_UNNAMED = [LOC7_UNNAMED, LOC9, LOC10]

# Task fixture — the server pre-orders active/planned oldest created_at
# first, so the line takes active[0] (else planned[0]) without re-sorting.
TASK_WORK_ACTIVE = {"status": "active", "task_type": "WORK", "created_at": 100}


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


def _place_base(user=USER_A, char=CHAR1, catalog=None, weather=WEATHER_OFF, tasks=None):
    """Authenticated boot + the place loader's GETs."""
    d = dict(
        _auth(user, char, [char]),
        **{
            "/world": _resp(200, WORLD_A),
            "/weather": _resp(200, weather),
            "/locations": _resp(200, catalog if catalog is not None else CATALOG),
            "/world/events?since=0&limit=100": _resp(200, []),
        },
    )
    if tasks is not None:
        d[f"/characters/{char['id']}/tasks"] = tasks
    return d


SCENARIOS = [
    # ---------- 1. GAME_SHELL_MOUNT ----------
    {
        "name": "GAME_SHELL_MOUNT",
        "initialHash": "#/location/7",
        "responses": _place_base(),
        "driver": (
            "await __settle(); const after = __facts(); "
            "globalThis.__result = after;"
        ),
    },
    # ---------- 2. CURRENT_STATE ----------
    {
        "name": "CURRENT_STATE",
        "initialHash": "#/location/7",
        "responses": _place_base(),
        "driver": (
            "await __settle(); const after = __facts(); "
            "globalThis.__result = after;"
        ),
    },
    # ---------- 3. REMOTE_STATE ----------
    {
        "name": "REMOTE_STATE",
        "initialHash": "#/location/9",
        "responses": _place_base(catalog=CATALOG_NPC_BOTH),
        "driver": (
            "await __settle(); const after = __facts(); "
            "globalThis.__result = after;"
        ),
    },
    # ---------- 4. REMOTE_CTA_MOVE ----------
    {
        "name": "REMOTE_CTA_MOVE",
        "initialHash": "#/location/9",
        "responses": dict(_place_base(), **{"/actions": _resp(200, {})}),
        "driver": (
            "await __settle(); "
            "const goClicked = __clickButton('Пойти сюда'); await __settle(); "
            "const after = __facts(); "
            "globalThis.__result = { goClicked, after };"
        ),
    },
    # ---------- 5. NO_POST_ON_ENTRY ----------
    {
        "name": "NO_POST_ON_ENTRY",
        "initialHash": "#/location/7",
        "responses": _place_base(),
        "driver": (
            "await __settle(); const atCurrent = __facts(); "
            "__setHash('#/location/9'); await __settle(); "
            "const atRemote = __facts(); "
            "globalThis.__result = { atCurrent, atRemote };"
        ),
    },
    # ---------- 6. ACTION_POST ----------
    {
        "name": "ACTION_POST",
        "initialHash": "#/location/7",
        "responses": dict(_place_base(), **{"/actions": _resp(200, {})}),
        "driver": (
            "await __settle(); "
            "const eatClicked = __clickButton('Поесть'); await __settle(); "
            "const after = __facts(); "
            "globalThis.__result = { eatClicked, after };"
        ),
    },
    # ---------- 7. NPC_SLICES ----------
    {
        "name": "NPC_SLICES",
        "initialHash": "#/location/7",
        "responses": _place_base(catalog=CATALOG_SLICES),
        "driver": (
            "await __settle(); const zero = __facts(); "
            "__setHash('#/location/9'); await __settle(); const one = __facts(); "
            "__setHash('#/location/10'); await __settle(); const many = __facts(); "
            "globalThis.__result = { zero, one, many };"
        ),
    },
    # ---------- 8. DIALOG_GATE ----------
    {
        "name": "DIALOG_GATE",
        "initialHash": "#/location/9",
        "responses": dict(
            _place_base(catalog=CATALOG_NPC_BOTH),
            **{
                "/locations/7": _resp(200, {
                    "id": 7, "name": "Settlement",
                    "characters_here": ["npc_1"],
                }),
                "/characters/npc_1": _resp(200, {"id": "npc_1", "name": "Иван"}),
            },
        ),
        "driver": (
            "await __settle(); const remote = __facts(); "
            "__setHash('#/location/7'); await __settle(); const current = __facts(); "
            "const dialogClicked = __clickButton('Поговорить'); await __settle(); "
            "const after = __facts(); "
            "globalThis.__result = { remote, current, dialogClicked, after };"
        ),
    },
    # ---------- 9. SCENE_ACT_PLACE ----------
    {
        "name": "SCENE_ACT_PLACE",
        "initialHash": "#/location/7",
        "responses": dict(
            _place_base(),
            **{"/scene/act": _resp(
                200, {"ok": True, "action": "look", "result": "Осмотрено"})},
        ),
        "driver": (
            "await __settle(); "
            "const inp = document.getElementById('scene-act-input'); "
            "inp.value = 'Поговорить с Иваном'; "
            "const goClicked = __clickButton('Сделать'); await __settle(); "
            "const atCurrent = __facts(); "
            "__setHash('#/location/9'); await __settle(); const atRemote = __facts(); "
            "globalThis.__result = { goClicked, atCurrent, atRemote };"
        ),
    },
    # ---------- 10. TASK_LINE ----------
    {
        "name": "TASK_LINE",
        "initialHash": "#/location/7",
        "responses": _place_base(
            char=CHAR1_OWNER,
            tasks=_tasks_resp(active=[TASK_WORK_ACTIVE]),
        ),
        "driver": (
            "await __settle(); const active = __facts(); "
            "__setResponse('/characters/1/tasks', " + json.dumps(_tasks_resp()) + "); "
            "__setHash('#/location/9'); await __settle(); "
            "__setHash('#/location/7'); await __settle(); const empty = __facts(); "
            "__setResponse('/characters/1/tasks', "
            + json.dumps(_resp(500, {"detail": "tasks down"})) + "); "
            "__setHash('#/location/9'); await __settle(); "
            "__setHash('#/location/7'); await __settle(); const failed = __facts(); "
            "globalThis.__result = { active, empty, failed };"
        ),
    },
    # ---------- 11. DETAILS_COLLAPSED ----------
    {
        "name": "DETAILS_COLLAPSED",
        "initialHash": "#/location/7",
        "responses": _place_base(),
        "driver": (
            "await __settle(); const after = __facts(); "
            "globalThis.__result = after;"
        ),
    },
    # ---------- 12. HONEST_MISSING ----------
    {
        "name": "HONEST_MISSING",
        "initialHash": "#/location/7",
        "responses": _place_base(weather=WEATHER_ON),
        "driver": (
            "await __settle(); const on = __facts(); "
            "__setResponse('/weather', " + json.dumps(_resp(200, WEATHER_OFF)) + "); "
            "__setHash('#/location/9'); await __settle(); "
            "__setHash('#/location/7'); await __settle(); const off = __facts(); "
            "__setResponse('/weather', "
            + json.dumps(_resp(500, {"detail": "weather down"})) + "); "
            "__setHash('#/location/9'); await __settle(); "
            "__setHash('#/location/7'); await __settle(); const failed = __facts(); "
            "globalThis.__result = { on, off, failed };"
        ),
    },
    # ---------- 13. RETRY_ON_FAILURE ----------
    {
        "name": "RETRY_ON_FAILURE",
        "initialHash": "#/location/7",
        "responses": dict(
            _place_base(),
            **{"/locations": _resp(500, {"detail": "locations down"})},
        ),
        "driver": (
            "await __settle(); const failed = __facts(); "
            "__setResponse('/locations', " + json.dumps(_resp(200, CATALOG)) + "); "
            "const retryClicked = __clickButton('Повторить'); await __settle(); "
            "const after = __facts(); "
            "globalThis.__result = { failed, retryClicked, after };"
        ),
    },
    # ---------- 14. STALE_SLOW_RESPONSE ----------
    {
        "name": "STALE_SLOW_RESPONSE",
        "initialHash": "#/location/7",
        "responses": _place_base(),
        "driver": (
            "__hold('/locations'); "
            "await __settle(); "
            "__setHash('#/location/9'); await __settle(); "
            "const newerReleased = __releaseAt('/locations', 1, "
            + json.dumps(_resp(200, CATALOG)) + "); "
            "await __settle(); const atNine = __facts(); "
            "const olderReleased = __releaseAt('/locations', 0, "
            + json.dumps(_resp(200, CATALOG)) + "); "
            "await __settle(); const after = __facts(); "
            "globalThis.__result = { newerReleased, atNine, olderReleased, after };"
        ),
    },
    # ---------- 15. FLUX_NOT_CALLED_FROM_PLACE (DEFECT-1) ----------
    {
        "name": "FLUX_NOT_CALLED_FROM_PLACE",
        "initialHash": "#/location/7",
        "responses": dict(
            _place_base(),
            **{"/scene/act": _resp(
                200, {"ok": True, "task_id": "t1", "task_type": "work",
                      "task_label": "Работа"})},
        ),
        "driver": (
            "await __settle(); "
            "__S.scene = { state: 'ready', asset: 'x' }; "
            "const inp1 = document.getElementById('scene-act-input'); "
            "inp1.value = 'Поговорить с Иваном'; "
            "__clickButton('Сделать'); await __settle(); "
            "const leg1 = __facts(); "
            "__S.scene = { state: 'fallback' }; "
            "const inp2 = document.getElementById('scene-act-input'); "
            "inp2.value = 'Поговорить с Петром'; "
            "__clickButton('Сделать'); await __settle(); "
            "const leg2 = __facts(); "
            "globalThis.__result = { leg1, leg2 };"
        ),
    },
    # ---------- 16. SCENE_ACT_REFUSAL_LOCALIZED (DEFECT-2) ----------
    {
        "name": "SCENE_ACT_REFUSAL_LOCALIZED",
        "initialHash": "#/location/7",
        "responses": dict(
            _place_base(),
            **{"/scene/act": _resp(
                409, {"detail": "character is AUTONOMOUS; "
                                "switch to GUIDED or DIRECT first (§61)"})},
        ),
        "driver": (
            "await __settle(); "
            "const inp = document.getElementById('scene-act-input'); "
            "inp.value = 'Поговорить с Иваном'; "
            "const goClicked = __clickButton('Сделать'); await __settle(); "
            "const after = __facts(); "
            "globalThis.__result = { goClicked, after };"
        ),
    },
    # ---------- 17. UNNAMED_FALLBACK (DEFECT-3) ----------
    {
        "name": "UNNAMED_FALLBACK",
        "initialHash": "#/location/7",
        "responses": _place_base(catalog=CATALOG_UNNAMED),
        "driver": (
            "await __settle(); const after = __facts(); "
            "globalThis.__result = after;"
        ),
    },
    # ---------- 18. DEEPLINK_BOOT_STILL_CURRENT_PLACE (DEFECT-4) ----------
    # Cold bootstrap: the by-user payload carries NO location_id (a deep
    # link / F5 never runs the World poll). /characters/1 is the full char
    # WITH location_id 7 — the source viewLocation must self-refresh from.
    {
        "name": "DEEPLINK_BOOT_STILL_CURRENT_PLACE",
        "initialHash": "#/location/7",
        "responses": dict(
            _auth(USER_A, CHAR1_OWNER, [BY_USER_NO_LOC]),
            **{
                "/world": _resp(200, WORLD_A),
                "/weather": _resp(200, WEATHER_OFF),
                "/locations": _resp(200, CATALOG_NPC_BOTH),
                "/world/events?since=0&limit=100": _resp(200, []),
            },
        ),
        "driver": (
            "await __settle(); const after = __facts(); "
            "globalThis.__result = after;"
        ),
    },
]


def _run_harness():
    os.makedirs(SCRATCH, exist_ok=True)
    harness_path = os.path.join(SCRATCH, "m303_harness.mjs")
    scen_path = os.path.join(SCRATCH, "m303_scenarios.json")
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


# ---------- 1. GAME_SHELL_MOUNT ----------

def test_game_shell_mount(results):
    r = _facts(results, "GAME_SHELL_MOUNT")
    # The game-shell composition: main.place-shell.place-game with the
    # .place-grid (stage + context) and the .place-topbar back button.
    assert r["hasPlaceGame"] is True, (
        "the place view mounts main.place-shell.place-game"
    )
    assert r["hasPlaceGrid"] is True, "the .place-grid is mounted"
    assert r["hasPlaceStage"] is True, "the static .place-stage placeholder is mounted"
    assert r["hasPlaceContext"] is True, "the .panel.place-context is mounted"
    assert r["hasPlaceTopbar"] is True, "the .place-topbar is mounted"
    assert r["backButtonText"] == "← Остров", (
        f"the topbar back button is «← Остров», got {r['backButtonText']!r}"
    )
    assert r["topbarTitle"] == "/ Settlement", (
        f"the topbar title carries the place name, got {r['topbarTitle']!r}"
    )
    # The stage is an honest static placeholder.
    assert r["stageText"] is not None and "Фото места недоступно" in r["stageText"], (
        f"the stage carries the honest photo note, got {r['stageText']!r}"
    )
    # No raw #id debug string in the always-visible context panel — the ID
    # lives only inside the collapsed details.
    assert "#7" not in r["contextTextNoDetails"], (
        f"no raw #id in the always-visible context, got {r['contextTextNoDetails']!r}"
    )
    assert "#9" not in r["contextTextNoDetails"], (
        f"no raw #id in the always-visible context, got {r['contextTextNoDetails']!r}"
    )
    assert r["hasDetails"] is True, "the details.place-about reference level is mounted"
    assert r["detailsText"] is not None and "ID" in r["detailsText"], (
        "the place ID lives inside the details"
    )
    unhandled = _unhandled(results, "GAME_SHELL_MOUNT")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 2. CURRENT_STATE ----------

def test_current_state(results):
    r = _facts(results, "CURRENT_STATE")
    assert r["badgeText"] == "Вы здесь", (
        f"the current place shows the «Вы здесь» badge, got {r['badgeText']!r}"
    )
    assert r["badgeHere"] is True, "the badge carries the place-badge-here class"
    assert r["hasActionsBlock"] is True, "the «Действия здесь» block is present"
    assert r["actionButtons"] == [
        "Пойти на работу", "Поесть", "Попить", "Отдохнуть / поспать", "Пообщаться",
    ], (
        f"the actions block renders the shared ACTION_VOCABULARY, "
        f"got {r['actionButtons']!r}"
    )
    assert r["hasSceneAct"] is True, "the scene-act bar is present on the current place"
    assert r["hasSceneActInput"] is True, "the scene-act input is present"
    assert r["hasGoHere"] is False, "no «Пойти сюда» CTA on the current place"
    assert r["youAreAtText"] is None, "no «Вы сейчас:» line on the current place"
    assert "Кнопка только отправляет запрос" in r["contextTextNoDetails"], (
        "the actions hint keeps the contract honest"
    )
    assert r["postCount"] == 0, "entering the current place issues no POST"
    unhandled = _unhandled(results, "CURRENT_STATE")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 3. REMOTE_STATE ----------

def test_remote_state(results):
    r = _facts(results, "REMOTE_STATE")
    assert r["badgeText"] == "Другое место", (
        f"the remote place shows the «Другое место» badge, got {r['badgeText']!r}"
    )
    assert r["badgeHere"] is False, "no place-badge-here class on a remote place"
    assert r["youAreAtText"] == "Вы сейчас: Settlement", (
        f"the remote place states the player's own location, got {r['youAreAtText']!r}"
    )
    assert r["hasGoHere"] is True, "the remote place offers the «Пойти сюда» CTA"
    assert r["hasActionsBlock"] is False, "no actions block on a remote place"
    assert r["hasSceneAct"] is False, "no scene-act bar on a remote place"
    assert r["hasSceneActInput"] is False, "no scene-act input on a remote place"
    assert r["dialogButtonCount"] == 0, (
        "no «Поговорить» on a remote place even with NPCs present"
    )
    assert "Вы здесь" not in r["contextTextNoDetails"], (
        "no «Вы здесь» on a remote place"
    )
    assert r["postCount"] == 0, "entering the remote place issues no POST"
    unhandled = _unhandled(results, "REMOTE_STATE")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 4. REMOTE_CTA_MOVE ----------

def test_remote_cta_move(results):
    r = _facts(results, "REMOTE_CTA_MOVE")
    go_clicked, after = r["goClicked"], r["after"]
    assert go_clicked is True, "the «Пойти сюда» CTA is present and clickable"
    # The ONE move path: a single POST /actions with the MOVE body (the
    # existing moveTo path).
    assert after["postActions"] == 1, (
        f"«Пойти сюда» issues exactly ONE POST /actions, got {after['postActions']}"
    )
    assert after["postActionBodies"] == [
        {"character_id": 1, "action_type": "MOVE", "params": {"location_id": 9}}
    ], (
        f"the POST body is the MOVE action to location 9, "
        f"got {after['postActionBodies']!r}"
    )
    assert after["hash"] == "#/world", (
        f"the move returns to the world view, got {after['hash']!r}"
    )
    assert after["toastText"] == "Идём в Remote", (
        f"the move toast names the destination, got {after['toastText']!r}"
    )
    unhandled = _unhandled(results, "REMOTE_CTA_MOVE")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 5. NO_POST_ON_ENTRY ----------

def test_no_post_on_entry(results):
    r = _facts(results, "NO_POST_ON_ENTRY")
    at_current, at_remote = r["atCurrent"], r["atRemote"]
    assert at_current["hash"] == "#/location/7", "boot lands on the current place"
    # Composition-agnostic render guard (the place screen rendered — the
    # no-POST contract is what is under test, not the shell class).
    assert at_current["hasPlaceShell"] is True, "the current place renders"
    assert at_current["postCount"] == 0, (
        f"entering the current place issues ZERO POSTs, got {at_current['postCount']}"
    )
    assert at_remote["hash"] == "#/location/9", "navigation lands on the remote place"
    assert at_remote["hasPlaceShell"] is True, "the remote place renders"
    assert at_remote["postCount"] == 0, (
        f"entering the remote place issues ZERO POSTs, got {at_remote['postCount']}"
    )
    # GETs only — the place load is a read.
    for path in ("/auth/me", "/world", "/weather", "/locations",
                 "/world/events?since=0&limit=100"):
        assert path in at_remote["getPaths"], (
            f"the place load GETs {path}, got {at_remote['getPaths']!r}"
        )
    unhandled = _unhandled(results, "NO_POST_ON_ENTRY")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 6. ACTION_POST ----------

def test_action_post(results):
    r = _facts(results, "ACTION_POST")
    eat_clicked, after = r["eatClicked"], r["after"]
    assert eat_clicked is True, "the «Поесть» action button is present and clickable"
    assert after["postActions"] == 1, (
        f"an action button issues exactly ONE POST /actions, got {after['postActions']}"
    )
    assert after["postActionBodies"] == [
        {"character_id": 1, "action_type": "EAT", "params": {}}
    ], (
        f"the POST body is the EAT action via doAction, got {after['postActionBodies']!r}"
    )
    assert after["toastText"] == "Задача EAT принята", (
        f"the doAction success toast, got {after['toastText']!r}"
    )
    assert after["hash"] == "#/world", (
        f"doAction's default after() returns to the world view, got {after['hash']!r}"
    )
    unhandled = _unhandled(results, "ACTION_POST")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 7. NPC_SLICES ----------

def test_npc_slices(results):
    r = _facts(results, "NPC_SLICES")
    zero, one, many = r["zero"], r["one"], r["many"]
    # Zero occupants — the honest empty line.
    assert zero["nearbyEmpty"] is True, "zero occupants → «Рядом никого нет.»"
    assert zero["nearbyRows"] == [], (
        f"zero occupants → no rows, got {zero['nearbyRows']!r}"
    )
    # One NPC — a name-only row (no invented work/mood text).
    assert one["nearbyRows"] == [{"name": "Иван", "dialog": False}], (
        f"one NPC → a single name-only row, got {one['nearbyRows']!r}"
    )
    assert one["nearbyEmpty"] is False, "one NPC → no empty line"
    # Many NPCs + a player-kind occupant — the player never enters «Рядом».
    assert many["nearbyRows"] == [
        {"name": "Мария", "dialog": False},
        {"name": "Пётр", "dialog": False},
    ], (
        f"many occupants → NPC name rows only (the player-kind occupant is "
        f"excluded), got {many['nearbyRows']!r}"
    )
    unhandled = _unhandled(results, "NPC_SLICES")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 8. DIALOG_GATE ----------

def test_dialog_gate(results):
    r = _facts(results, "DIALOG_GATE")
    remote, current, dialog_clicked, after = (
        r["remote"], r["current"], r["dialogClicked"], r["after"])
    # Remote place with NPCs — the affordance is honestly absent.
    assert remote["dialogButtonCount"] == 0, (
        "a remote place with NPCs offers no «Поговорить»"
    )
    # Current place with NPCs — the affordance is present.
    assert current["dialogButtonCount"] == 1, (
        f"the current place offers «Поговорить» per NPC, "
        f"got {current['dialogButtonCount']}"
    )
    assert dialog_clicked is True, "the «Поговорить» button is present and clickable"
    # The dialogue entry: the confirmed occupant id into the session UI
    # context + navigation to #/chat (revalidated in viewChat).
    assert after["hash"] == "#/chat", (
        f"«Поговорить» navigates to #/chat, got {after['hash']!r}"
    )
    assert after["guc"] is not None, "the #302 session UI context exists"
    assert after["guc"]["npcId"] == "npc_1", (
        f"the confirmed occupant id is carried in gameUiContext.npcId, "
        f"got {after['guc']['npcId']!r}"
    )
    unhandled = _unhandled(results, "DIALOG_GATE")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 9. SCENE_ACT_PLACE ----------

def test_scene_act_place(results):
    r = _facts(results, "SCENE_ACT_PLACE")
    go_clicked, at_current, at_remote = r["goClicked"], r["atCurrent"], r["atRemote"]
    assert go_clicked is True, "the scene-act «Сделать» button is present and clickable"
    # Exactly one POST /scene/act with the typed text.
    assert at_current["postSceneAct"] == 1, (
        f"the scene-act submit issues exactly ONE POST /scene/act, "
        f"got {at_current['postSceneAct']}"
    )
    assert at_current["postSceneActBodies"] == [{"text": "Поговорить с Иваном"}], (
        f"the POST body carries the typed text, got {at_current['postSceneActBodies']!r}"
    )
    # The committed result is re-read into the Place host.
    assert at_current["sceneActResultInHost"] is True, (
        "the result node is rendered into the .place-scene-act-result host"
    )
    assert at_current["sceneActResultText"] is not None \
        and "Осмотрено" in at_current["sceneActResultText"], (
        f"the result text is shown in the Place host, "
        f"got {at_current['sceneActResultText']!r}"
    )
    # Remote place — no scene-act input at all.
    assert at_remote["hasSceneAct"] is False, "no scene-act bar on the remote place"
    assert at_remote["hasSceneActInput"] is False, (
        "no scene-act input on the remote place"
    )
    unhandled = _unhandled(results, "SCENE_ACT_PLACE")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 10. TASK_LINE ----------

def test_task_line(results):
    r = _facts(results, "TASK_LINE")
    active, empty, failed = r["active"], r["empty"], r["failed"]
    assert active["taskText"] == "Задача:активна · работа", (
        f"an active task renders status·type labels, got {active['taskText']!r}"
    )
    assert empty["taskText"] == "Задача:нет активной задачи", (
        f"empty tasks render the honest empty line, got {empty['taskText']!r}"
    )
    assert failed["taskText"] == "Задача:Данные о делах недоступны", (
        f"a tasks fetch failure renders the explicit unavailable line, "
        f"got {failed['taskText']!r}"
    )
    unhandled = _unhandled(results, "TASK_LINE")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 11. DETAILS_COLLAPSED ----------

def test_details_collapsed(results):
    r = _facts(results, "DETAILS_COLLAPSED")
    assert r["hasDetails"] is True, (
        "the native <details class=\"place-about\"> is mounted"
    )
    assert r["detailsSummary"] == "О месте", (
        f"the details summary is «О месте», got {r['detailsSummary']!r}"
    )
    assert r["detailsInContext"] is True, (
        "the details is nested inside the .place-context panel (DOM nesting)"
    )
    # The reference fields live inside the details…
    d = r["detailsText"]
    assert d is not None, "the details has content"
    for frag in ("ID", "Тип", "settlement", "Координаты", "40 · 50",
                 "Входит в", "Людей сейчас"):
        assert frag in d, f"the details carries {frag!r}, got {d!r}"
    # …and never in the primary (always-visible) view.
    assert "Координаты" not in r["contextTextNoDetails"], (
        "coordinates are not in the always-visible context"
    )
    assert "ID" not in r["contextTextNoDetails"], (
        "the raw ID is not in the always-visible context"
    )
    unhandled = _unhandled(results, "DETAILS_COLLAPSED")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 12. HONEST_MISSING ----------

def test_honest_missing(results):
    r = _facts(results, "HONEST_MISSING")
    on, off, failed = r["on"], r["off"], r["failed"]
    assert on["weatherLineText"] == "Погода в мире: Ясно", (
        f"readable weather renders its description, got {on['weatherLineText']!r}"
    )
    assert off["weatherLineText"] == "Погода в мире: недоступна", (
        f"disabled weather stays honestly unavailable, got {off['weatherLineText']!r}"
    )
    assert failed["weatherLineText"] == "Погода в мире: недоступна", (
        f"a failed weather fetch stays honestly unavailable, "
        f"got {failed['weatherLineText']!r}"
    )
    # World time renders from the fresh read (no zero-fallback).
    assert on["timeLineText"] == "Время мира: День 2, 01:00", (
        f"the world time line, got {on['timeLineText']!r}"
    )
    unhandled = _unhandled(results, "HONEST_MISSING")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 13. RETRY_ON_FAILURE ----------

def test_retry_on_failure(results):
    r = _facts(results, "RETRY_ON_FAILURE")
    failed, retry_clicked, after = r["failed"], r["retryClicked"], r["after"]
    assert failed["hasPlaceShell"] is False, "a failed load does not render the place"
    assert failed["failLineText"] == "Загрузка не удалась. Проверьте связь и повторите", (
        f"the failure line explains the transient error, got {failed['failLineText']!r}"
    )
    assert failed["hasRetryButton"] is True, "the retry affordance is present"
    assert retry_clicked is True, "the «Повторить» button is present and clickable"
    # Composition-agnostic: the retry re-runs the load and the place screen
    # renders again (the retry contract, not the shell class, is under test).
    assert after["hasPlaceShell"] is True, "the retry re-runs the load and renders"
    assert after["placeName"] == "Settlement", (
        f"the retried load renders the requested place, got {after['placeName']!r}"
    )
    unhandled = _unhandled(results, "RETRY_ON_FAILURE")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 14. STALE_SLOW_RESPONSE ----------

def test_stale_slow_response(results):
    r = _facts(results, "STALE_SLOW_RESPONSE")
    newer_released, at_nine, older_released, after = (
        r["newerReleased"], r["atNine"], r["olderReleased"], r["after"])
    assert newer_released is True, "the newer load's held /locations is released"
    # Composition-agnostic observables: the place screen rendered the newer
    # place (the stale-guard contract, not the shell class, is under test).
    assert at_nine["hasPlaceShell"] is True, "the newer load renders the place screen"
    assert at_nine["placeName"] == "Remote", (
        f"the newer load shows place 9, got {at_nine['placeName']!r}"
    )
    assert older_released is True, "the older load's held /locations is released"
    # The stale (older) response must not clobber the newer render.
    assert after["hasPlaceShell"] is True, (
        "the screen still renders after the stale release"
    )
    assert after["placeName"] == "Remote", (
        f"the stale slow response did not clobber the newer render, "
        f"got {after['placeName']!r}"
    )
    unhandled = _unhandled(results, "STALE_SLOW_RESPONSE")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 15. FLUX_NOT_CALLED_FROM_PLACE (DEFECT-1) ----------

def test_flux_not_called_from_place(results):
    r = _facts(results, "FLUX_NOT_CALLED_FROM_PLACE")
    leg1, leg2 = r["leg1"], r["leg2"]
    # DEFECT-1: the Place screen must not call Flux (POST /visual/scenes) —
    # there is no #scene-view host to display it, and a failure would toast
    # «Визуал недоступен» on a screen with no visual. Both the ready and the
    # fallback scene states must skip the re-inspection after a scene-act
    # that returns a task.
    assert leg1["postVisualScenes"] == 0, (
        f"the ready-state scene-act must NOT fire POST /visual/scenes, "
        f"got {leg1['postVisualScenes']}"
    )
    assert leg2["postVisualScenes"] == 0, (
        f"the fallback-state scene-act must NOT fire POST /visual/scenes, "
        f"got {leg2['postVisualScenes']}"
    )
    assert "Визуал недоступен" not in (leg2["toastText"] or ""), (
        f"no «Визуал недоступен» toast on the Place screen, "
        f"got {leg2['toastText']!r}"
    )
    unhandled = _unhandled(results, "FLUX_NOT_CALLED_FROM_PLACE")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 16. SCENE_ACT_REFUSAL_LOCALIZED (DEFECT-2) ----------

def test_scene_act_refusal_localized(results):
    r = _facts(results, "SCENE_ACT_REFUSAL_LOCALIZED")
    go_clicked, after = r["goClicked"], r["after"]
    assert go_clicked is True, "the scene-act «Сделать» button is present and clickable"
    # DEFECT-2: an AUTONOMOUS refusal must show the localized hint in the
    # persistent result line, not the raw English server detail (the toast
    # already localizes; the stored S.sceneAct.detail must too).
    assert after["sceneActResultText"] is not None, (
        "the scene-act result line is rendered"
    )
    assert "Персонаж действует сам" in after["sceneActResultText"], (
        f"the refusal result line is localized, got {after['sceneActResultText']!r}"
    )
    assert "character is AUTONOMOUS" not in after["sceneActResultText"], (
        f"the raw English server detail must not leak into the result line, "
        f"got {after['sceneActResultText']!r}"
    )
    unhandled = _unhandled(results, "SCENE_ACT_REFUSAL_LOCALIZED")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 17. UNNAMED_FALLBACK (DEFECT-3) ----------

def test_unnamed_fallback(results):
    r = _facts(results, "UNNAMED_FALLBACK")
    # DEFECT-3: unnamed places/NPCs must render «Без названия» / «Без имени»
    # in the primary (always-visible) view, never a raw #id debug string —
    # the ID belongs in the <details> reference level only.
    assert "Без названия" in r["contextTextNoDetails"], (
        f"the unnamed place renders «Без названия», got {r['contextTextNoDetails']!r}"
    )
    assert "Без имени" in r["contextTextNoDetails"], (
        f"the unnamed NPC renders «Без имени», got {r['contextTextNoDetails']!r}"
    )
    assert "#7" not in r["contextTextNoDetails"], (
        f"no raw #id for the place in the primary view, got {r['contextTextNoDetails']!r}"
    )
    assert "#npc_1" not in r["contextTextNoDetails"], (
        f"no raw #id for the NPC in the primary view, got {r['contextTextNoDetails']!r}"
    )
    assert "#7" not in (r["topbarTitle"] or ""), (
        f"no raw #id in the topbar title, got {r['topbarTitle']!r}"
    )
    assert "Без названия" in (r["topbarTitle"] or ""), (
        f"the topbar title renders «Без названия», got {r['topbarTitle']!r}"
    )
    unhandled = _unhandled(results, "UNNAMED_FALLBACK")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"


# ---------- 18. DEEPLINK_BOOT_STILL_CURRENT_PLACE (DEFECT-4) ----------

def test_deeplink_boot_still_current_place(results):
    r = _facts(results, "DEEPLINK_BOOT_STILL_CURRENT_PLACE")
    # DEFECT-4: a deep link / F5 to #/location/{id} boots the client from
    # /characters/by-user/{uid}, whose payload carries NO location_id. The
    # World poll (the only other /characters/{id} reader) never ran, so
    # viewLocation must self-refresh S.character from that same source —
    # otherwise the place the player is standing in renders as REMOTE
    # («Другое место» + «Пойти сюда», no actions, no «Поговорить»).
    assert r["badgeText"] == "Вы здесь", (
        f"the deep-linked current place shows the «Вы здесь» badge, "
        f"got {r['badgeText']!r}"
    )
    assert r["hasActionsBlock"] is True, (
        "the deep-linked current place offers the «Действия здесь» block"
    )
    assert r["dialogButtonCount"] >= 1, (
        f"the deep-linked current place offers «Поговорить» for its NPC "
        f"occupant, got {r['dialogButtonCount']}"
    )
    unhandled = _unhandled(results, "DEEPLINK_BOOT_STILL_CURRENT_PLACE")
    assert unhandled == [], f"no unhandled errors may escape: {unhandled}"
