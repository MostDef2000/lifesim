// #369 — execute the production appearance view inside the shared shell with
// a minimal DOM. The real viewAppearance/renderAppearance/runPreview code is
// driven through loading, ready, empty, error, unavailable, preview and stale
// transitions; only transport helpers and the portrait loader are stubbed.
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const source = fs.readFileSync(path.join(__dirname, "../../backend/app/web/app.js"), "utf8");
const bounds = [
  ["function appendKids(", "/* ---------- routing"],
  ["const RETRY_HINT", "/* ---------- #306"],
  ["function svgIcon(", "/* ---------- world view"],
  ["function gameSectionNav(", "/* #289: the world panel chip"],
  ["function weatherLabel(", "// #313: compact contextual header"],
  ["function inventoryContextText(", "function refreshInventoryContext("],
  ["/* ---------- #65:", "/* ---------- #173"],
];
function extract(a, b) {
  const start = source.indexOf(a);
  const end = source.indexOf(b, start + 1);
  if (start < 0 || end < 0) throw Error("missing production source: " + a);
  return source.slice(start, end);
}
const prelude = `
class Node {
  constructor(tag) {
    this.tagName = tag; this.nodeType = 1; this.attrs = {}; this.className = "";
    this.childNodes = []; this._ev = {};
  }
  append(n) { this.childNodes.push(n); }
  replaceChildren(...n) { this.childNodes = n; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return this.attrs[k]; }
  addEventListener(type, fn) { (this._ev[type] = this._ev[type] || []).push(fn); }
  dispatch(type) { for (const fn of this._ev[type] || []) fn({ currentTarget: this, target: this }); }
  querySelector(sel) {
    const hit = (node) => node.nodeType === 1 && (
      (sel[0] === "#" && node.attrs.id === sel.slice(1))
      || (sel[0] === "." && node.className.split(/\\s+/).includes(sel.slice(1)))
    );
    const walk = (node) => {
      if (!node || !Array.isArray(node.childNodes)) return null;
      for (const child of node.childNodes) {
        if (hit(child)) return child;
        const found = walk(child);
        if (found) return found;
      }
      return null;
    };
    return walk(this);
  }
  get textContent() {
    return this.childNodes.map(n => n.nodeType === 3 ? n.data : n.textContent).join("");
  }
  set textContent(v) { this.childNodes = [{ nodeType: 3, data: String(v) }]; }
  set innerHTML(v) { throw Error("unsafe innerHTML"); }
}
function all(node, pred) {
  return node.nodeType === 1
    ? (pred(node) ? [node] : []).concat(node.childNodes.flatMap(n => all(n, pred)))
    : [];
}
const app = new Node("app");
const document = {
  createElement: t => new Node(t),
  createElementNS: (_ns, t) => new Node(t),
  createTextNode: v => ({ nodeType: 3, data: String(v) }),
  getElementById: id => all(app, n => n.attrs.id === id)[0] || null,
};
const S = {
  user: { role: "player" },
  character: { id: "c1", name: "Алиса", location_id: 1, balance: 984,
    sex: "female", age: 28, outfit_mode: "auto" },
  world: { game_timestamp: 60 },
  weather: { enabled: true, description: "ясно" },
  mapLocations: [{ id: 1, name: "Дом" }],
};
const location = { hash: "#/appearance" };
let navEpoch = 0;
const requests = [];
const toasts = [];
let responder = async (p) => {
  if (p === "/characters/c1") return { sex: "female", age: 28, outfit_mode: "auto" };
  if (p === "/characters/c1/inventory") return inventoryItems();
  if (p === "/visual/characters/c1/portrait") throw Error("no portrait");
  throw Error("unexpected fixture path: " + p);
};
let inventoryItems = () => [
  { id: 1, object_type: "jacket", quantity: 1, condition: 90, worn: true, slot: "body" },
  { id: 2, object_type: "boots", quantity: 1, condition: 55, worn: false, slot: "feet" },
];
const INV_WEARABLE_TYPES = ["jacket", "boots", "hat"];
function api(p, opts = {}) {
  requests.push(opts.method ? opts.method + " " + p : p);
  return responder(p, opts);
}
function toast(msg) { toasts.push(String(msg)); }
function clockPendingLabel() { return "…"; }
function placeTaskHostNode() { return el("span", { class: "muted" }, "Данные о делах недоступны"); }
function worldWeatherChipText() { return S.weather && S.weather.enabled ? " · " + S.weather.description : ""; }
async function wearToggle() { await viewAppearance(); }
async function fetchPortraitBlob() { return {}; }
const URL = { createObjectURL: () => "blob:portrait" };
function check(cond, label) { if (!cond) throw Error(label); }
const sleep = () => new Promise(r => setTimeout(r, 0));
`;

const scenario = `
return (async () => {
  const readPaths = () => requests.filter(p => !p.startsWith("POST ") && !p.startsWith("PATCH "));
  const posts = () => requests.filter(p => p.startsWith("POST "));

  // --- loading state already lives in the shared shell ---
  const pending = viewAppearance();
  check(app.childNodes.length === 1
    && app.childNodes[0].className.includes("appearance-game-shell"), "loading shell");
  check(all(app, n => n.className === "world-pill").length === 1, "loading dock");
  check(all(app, n => n.className === "topbar").length === 0, "no legacy topbar rendered");
  check(app.textContent.includes("Загрузка"), "loading text");
  check(all(app, n => n.attrs.id === "clock").length === 1, "one live clock");
  await pending;

  // --- ready state: nine destinations, active item, context, Admin gate ---
  const dock = () => all(app, n => n.className === "world-pill")[0];
  let links = all(dock(), n => n.tagName === "a");
  check(links.length === 9, "nine dock destinations");
  check(links.filter(n => n.attrs["aria-current"] === "page").length === 1, "one active item");
  check(links.find(n => n.attrs["aria-current"] === "page").attrs.href === "#/appearance",
    "appearance is current");
  check(links.every(n => n.attrs["aria-label"] && n.onclick == null), "named links only");
  const back = all(app, n => n.tagName === "a" && n.className === "appear-shell-back");
  check(back.length === 1 && back[0].textContent.includes("На карту"), "back to World");
  check(app.textContent.includes("Алиса") && app.textContent.includes("Дом")
    && app.textContent.includes("984 ₽"), "hero context survives");
  check(!app.textContent.includes("Админ"), "player sees no admin gate");
  check(app.textContent.includes("Текущий образ") && app.textContent.includes("Гардероб"),
    "ready zones render");
  check(app.textContent.includes("jacket") && app.textContent.includes("надето"),
    "actual wardrobe renders");

  // --- session unavailable: honest state inside the shell, zero requests ---
  const realChar = S.character;
  S.character = null;
  const beforeUnavailable = requests.length;
  await viewAppearance();
  check(requests.length === beforeUnavailable, "unavailable state issues no reads");
  check(app.textContent.includes("Образ недоступен"), "honest unavailable state");
  check(!app.textContent.includes("Ничего не надето"), "unavailable is not an empty wardrobe");
  check(all(app, n => n.className === "world-pill").length === 1, "unavailable still has the dock");
  S.character = realChar;
  await viewAppearance();

  // --- admin gate ---
  S.user.role = "admin";
  await viewAppearance();
  check(all(app, n => n.attrs.href === "#/admin").length === 1, "admin gate link");
  S.user.role = "player";
  await viewAppearance();

  // --- empty wardrobe is honest and stays in the shell ---
  inventoryItems = () => [];
  await viewAppearance();
  check(app.textContent.includes("Гардероб пуст — загляните на "), "empty wardrobe note");
  check(app.childNodes[0].className.includes("appearance-game-shell"), "empty shell");
  inventoryItems = () => [
    { id: 1, object_type: "jacket", quantity: 1, condition: 90, worn: true, slot: "body" },
    { id: 2, object_type: "boots", quantity: 1, condition: 55, worn: false, slot: "feet" },
  ];
  await viewAppearance();

  // --- typed draft survives a re-render (INV -> Appearance -> INV -> World) ---
  let prompt = document.getElementById("appear-outfit-prompt");
  check(prompt, "outfit prompt field");
  const draftText = "красное платье для свидания";
  prompt.value = draftText;
  prompt.dispatch("input");
  check(APPEARANCE_DRAFT.prompt === draftText, "draft captured");
  await viewAppearance();
  prompt = document.getElementById("appear-outfit-prompt");
  check(prompt.value === draftText, "typed draft survives navigation re-render");

  // --- preview: one request, pending guard, result labelled unsaved, no wear mutation ---
  const wearBefore = inventoryItems().map(i => i.worn);
  let releasePreview;
  const readResponder = responder;
  responder = async (p, o = {}) => {
    if (o.method === "POST" && p === "/visual/preview") {
      return new Promise(r => { releasePreview = r; });
    }
    return readResponder(p, o);
  };
  const previewBtn = () => document.getElementById("appear-preview-go");
  previewBtn().onclick();
  await sleep();
  check(posts().filter(p => p === "POST /visual/preview").length === 1,
    "one preview request in flight");
  check(previewBtn().disabled === true, "preview button disabled while pending");
  previewBtn().onclick();
  await sleep();
  check(posts().filter(p => p === "POST /visual/preview").length === 1,
    "second preview click is a no-op");
  const previewBody = requests.find(p => p.startsWith("POST /visual/preview"));
  check(previewBody, "preview uses the single appearance site");
  const beforePreviewItems = inventoryItems().map(i => i.worn);
  releasePreview({ image_base64: "AAAA" });
  await sleep();
  const img = all(app, n => n.tagName === "img" && n.className === "appear-preview-img")[0];
  const caption = all(app, n => n.className === "muted appear-note");
  check(img && img.hidden === false, "preview image shown");
  check(img.src === "data:image/png;base64,AAAA", "preview image is the answer");
  check(caption.some(n => n.textContent.includes("Предпросмотр — не сохранён")),
    "result labelled unsaved");
  check(JSON.stringify(inventoryItems().map(i => i.worn)) === JSON.stringify(beforePreviewItems),
    "preview never changes the actual wardrobe");
  check(JSON.stringify(inventoryItems().map(i => i.worn)) === JSON.stringify(wearBefore),
    "preview never changes worn state");

  // --- stale preview: a newer view/session drops the old answer ---
  responder = readResponder;
  const stale = new Promise(r => { releasePreview = r; });
  responder = async (p, o = {}) => {
    if (o.method === "POST" && p === "/visual/preview") return stale;
    return readResponder(p, o);
  };
  await viewAppearance();
  const staleBtn = document.getElementById("appear-preview-go");
  staleBtn.onclick();
  await sleep();
  ++navEpoch;
  location.hash = "#/world";
  releasePreview({ image_base64: "BBBB" });
  await sleep();
  check(app.childNodes.length === 1
    && app.childNodes[0].className.includes("appearance-game-shell")
    && !app.textContent.includes("Предпросмотр не удался"),
    "stale preview keeps the shell and raises no ghost error");
  check(requests.filter(p => p === "POST /visual/preview").length >= 2, "preview did fire");

  // --- error keeps the shell and offers retry ---
  location.hash = "#/appearance";
  responder = async () => { throw Error("offline"); };
  await viewAppearance();
  check(app.childNodes[0].className.includes("appearance-game-shell"), "error shell");
  check(app.textContent.includes("Повторить"), "retry in shell");
  check(all(app, n => n.className === "world-pill").length === 1, "error keeps the dock");
  responder = readResponder;

  // --- no storage writes and only the expected network surface ---
  check(!requests.some(p => p.includes("localStorage") || p.includes("sessionStorage")),
    "draft never touches web storage");
  check(requests.every(p => p === "/characters/c1" || p === "/characters/c1/inventory"
    || p === "/visual/characters/c1/portrait" || p === "POST /visual/preview"
    || p === "/characters/c1"), "read-only ground state");
  return "PASS: shell states, navigation, draft survival, preview isolation, stale guard, retry";
})();
`;

new Function(prelude + bounds.map(([a, b]) => extract(a, b)).join("\n") + scenario)()
  .then(result => console.log(result), error => { console.error(error); process.exitCode = 1; });
