// #368 — interactive inventory cards: production view state exercised with a
// minimal DOM. Extracts the real inventory region (no invItemRow/invDetail
// stubs — selection, filters, sell draft and the action guards are all
// production code driven through renderInventory/viewInventory).
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const source = fs.readFileSync(path.join(__dirname, "../../backend/app/web/app.js"), "utf8");
// Shared #367 primitives (el/dayTime, retry, dock/weather) + the full #368
// inventory region: invCategoryRu..#65 covers helpers, viewInventory,
// wearToggle, invSellRow, invDetail, invItemRow and renderInventory — the
// two recon slices plus the middle chunk between them (invSelectedPanel
// calls invDetail, which lives there).
const bounds = [
  ["function appendKids(", "/* ---------- routing"],
  ["const RETRY_HINT", "/* ---------- #306"],
  ["function svgIcon(", "/* ---------- world view"],
  // #368 needs the wearable set too (invDetail reads it; m367 never did).
  ["const INV_WEARABLE_TYPES", "function invCategoryRu("],
  ["function invCategoryRu(", "/* ---------- #65: «Образ и одежда»"],
];
function extract(a, b) {
  const start = source.indexOf(a), end = source.indexOf(b, start + 1);
  if (start < 0 || end < 0) throw Error("missing production source: " + a);
  return source.slice(start, end);
}
const prelude = `
class Node {
  constructor(tag) { this.tagName = tag; this.nodeType = 1; this.attrs = {}; this.className = ""; this.childNodes = []; this._ev = {}; }
  append(n) { this.childNodes.push(n); }
  replaceChildren(...n) { this.childNodes = n; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  addEventListener(type, fn) { (this._ev[type] = this._ev[type] || []).push(fn); }
  dispatch(type) { for (const fn of this._ev[type] || []) fn({ currentTarget: this, target: this }); }
  get textContent() { return this.childNodes.map(n => n.nodeType === 3 ? n.data : n.textContent).join(""); }
  set textContent(v) { this.childNodes = [{ nodeType: 3, data: String(v) }]; }
  set innerHTML(v) { throw Error("unsafe innerHTML"); }
}
function all(node, pred) {
  return node.nodeType === 1 ? (pred(node) ? [node] : []).concat(node.childNodes.flatMap(n => all(n, pred))) : [];
}
const app = new Node("app");
const document = { createElement: t => new Node(t), createElementNS: (_ns, t) => new Node(t),
  createTextNode: v => ({ nodeType: 3, data: String(v) }),
  getElementById: id => all(app, n => n.attrs.id === id)[0] || null,
  activeElement: null };
const S = { user: { role: "player" }, character: { id: "c1", name: "Алиса", location_id: 1, control_mode: "DIRECT", balance: 984 },
  world: { game_timestamp: 60 }, weather: { enabled: true, description: "ясно" },
  mapLocations: [{ id: 1, name: "Дом" }], invSellPrice: {} };
const location = { hash: "#/inventory" };
let navEpoch = 0;
const requests = [];
const toasts = [];
let responder = async (path) => {
  if (path === "/characters/c1/inventory") return inventoryItems();
  if (path === "/market/offers?status=active") return [];
  throw Error("unexpected fixture path: " + path);
};
const jacket = { id: 1, object_type: "jacket", quantity: 1, condition: 90, worn: false, slot: "body", location_id: 1 };
const bread = { id: 2, object_type: "food_bread", quantity: 2, condition: 100, location_id: 1 };
const odd = { id: 3, object_type: "relic_box", quantity: 1, condition: 50, location_id: 1 };
let inventoryItems = () => [jacket, bread, odd];
function api(path, opts = {}) {
  requests.push(opts.method ? opts.method + " " + path : path);
  return responder(path, opts);
}
function toast(msg) { toasts.push(String(msg)); }
function clockPendingLabel() { return "…"; }
function placeTaskHostNode() { return el("span", {}, "Данные о делах недоступны"); }
function check(cond, label) { if (!cond) throw Error(label); }
const sleep = () => new Promise(r => setTimeout(r, 0));
`;
const scenario = `
return (async () => {
  const invPath = "/characters/c1/inventory";
  const reads = () => requests.filter(p => !p.startsWith("POST "));
  const posts = () => requests.filter(p => p.startsWith("POST "));

  // --- #368 label/location contract (raw fallbacks, no invented names) ---
  check(invTypeLabel("food_apple") === "Провизия", "food label");
  check(invTypeLabel("water_bottle") === "Провизия", "water label");
  check(invTypeLabel("jacket") === "Одежда", "jacket label");
  check(invTypeLabel("hat") === "Одежда", "hat label");
  check(invTypeLabel("") === "Неизвестный тип предмета", "empty label");
  check(invLocationName(1) === "Дом", "known location");
  check(invLocationName(9) === "#9", "unknown location: raw id");
  check(invLocationName(null) === "—", "null location");

  // --- initial authoritative load (read-only) ---
  await viewInventory();
  check(reads().length === 2 && reads()[0] === invPath
    && reads()[1] === "/market/offers?status=active", "initial load is read-only");
  check(app.textContent.includes("Предмет не выбран"), "no selection explains selection");
  check(!requests.some(p => p.includes("/locations")), "inventory never fetches /locations");

  // --- pinned shell behavior ---
  const nav = gameSectionNav("#/inventory");
  const links = all(nav, n => n.tagName === "a");
  check(links.length === 9, "nine dock destinations");
  check(all(app, n => n.className === "world-pill").length === 1, "dock in shell");
  check(!app.textContent.includes("Админ"), "player sees no admin gate");
  const shellLinks = all(app, n => n.tagName === "a");
  check(shellLinks.some(a => a.attrs.href === "#/appearance"
    && a.textContent.includes("Образ и одежда")), "appearance footer link");
  check(shellLinks.some(a => a.attrs.href === "#/shop" && a.textContent === "Рынок"),
    "shop footer link");
  check(app.textContent.includes("EAT/DRINK"), "use-through-actions note");
  check(!/Съесть|Выпить|Использовать|Выбросить/.test(app.textContent),
    "no invented use verbs");

  // --- 1. selection is LOCAL: repaint from cache, zero new requests ---
  let cards = all(app, n => n.className === "inv-item");
  check(cards.length === 3, "three cards rendered");
  let before = requests.length;
  all(app, n => n.className === "inv-item")
    .find(c => c.attrs["data-object-id"] === "1").onclick();
  check(invView.selectedId === 1, "selection stored in view state");
  check(requests.length === before, "selection performs zero API calls");
  check(document.getElementById("inv-detail"), "detail panel repaints");
  check(document.getElementById("inv-detail").textContent.includes("Одежда"),
    "detail reads cached jacket fields");
  cards = all(app, n => n.className === "inv-item");
  check(cards.find(c => c.attrs["data-object-id"] === "1").attrs["aria-pressed"] === "true",
    "selected card marked pressed");
  all(app, n => n.className === "inv-item")
    .find(c => c.attrs["data-object-id"] === "1").onclick();
  check(invView.selectedId === null, "second click clears selection");
  check(requests.length === before, "deselect also performs zero API calls");

  // --- 3. unknown object_type: honest label + raw value, no invented fields ---
  all(app, n => n.className === "inv-item")
    .find(c => c.attrs["data-object-id"] === "3").onclick();
  const oddCard = all(app, n => n.className === "inv-item")
    .find(c => c.attrs["data-object-id"] === "3");
  check(oddCard.textContent.includes("Неизвестный тип предмета"),
    "unknown card gets the unknown-type label");
  check(oddCard.textContent.includes("relic_box"), "unknown card keeps raw object_type");
  const oddDetail = document.getElementById("inv-detail").textContent;
  check(oddDetail.includes("Неизвестный тип предмета")
    && oddDetail.includes("relic_box"), "detail: unknown label plus raw value");
  check(!oddDetail.includes("Одежда") && !oddDetail.includes("Провизия"),
    "detail invents no category for unknown type");
  check(oddDetail.includes("1 шт") && oddDetail.includes("50%"),
    "detail shows only real fields");

  // --- 2. category + search filters are local; filter-empty != empty bag ---
  before = requests.length;
  const chip = (label) => all(app, n => n.className === "inv-chip")
    .find(c => c.textContent === label);
  chip("Провизия").onclick();
  check(requests.length === before, "category filter performs zero API calls");
  let shown = all(app, n => n.className === "inv-item");
  check(shown.length === 1 && shown[0].attrs["data-object-id"] === "2",
    "category filter narrows the cached grid");
  chip("Всё").onclick();
  const search = document.getElementById("inv-search");
  search.value = "relic";
  search.oninput();
  check(requests.length === before, "search performs zero API calls");
  shown = all(app, n => n.className === "inv-item");
  check(shown.length === 1 && shown[0].attrs["data-object-id"] === "3",
    "search matches label and raw type");
  search.value = "zzz-no-match";
  search.oninput();
  check(app.textContent.includes("Ничего не найдено"), "filter-empty message");
  check(!app.textContent.includes("Пусто — купите"), "filter-empty is not the empty bag");
  const reset = all(app, n => n.tagName === "button" && n.textContent === "Сбросить фильтр")[0];
  check(reset, "filter-empty offers a reset");
  reset.onclick();
  check(all(app, n => n.className === "inv-item").length === 3, "reset restores the grid");

  inventoryItems = () => [];
  await viewInventory();
  check(app.textContent.includes("Пусто — купите что-нибудь во Владивостоке"),
    "empty inventory message");
  check(!app.textContent.includes("Ничего не найдено"),
    "empty inventory is distinct from filter-empty");
  inventoryItems = () => [jacket, bread, odd];
  await viewInventory();

  // --- 4. sell draft survives selection/filter/re-render, typed "0" kept ---
  all(app, n => n.className === "inv-item")
    .find(c => c.attrs["data-object-id"] === "1").onclick();
  const sellInput = () => all(app, n => n.tagName === "input" && n.attrs.type === "number")[0];
  check(sellInput(), "sell row for unlisted jacket");
  let price = sellInput();
  check(price.value === "", "no draft starts empty");
  price.value = "0";
  price.dispatch("input");
  check(S.invSellPrice[1] === "0", "typed 0 stored in draft");
  all(app, n => n.className === "inv-chip").find(c => c.textContent === "Одежда").onclick();
  all(app, n => n.className === "inv-chip").find(c => c.textContent === "Всё").onclick();
  all(app, n => n.className === "inv-item")
    .find(c => c.attrs["data-object-id"] === "2").onclick();
  all(app, n => n.className === "inv-item")
    .find(c => c.attrs["data-object-id"] === "1").onclick();
  price = sellInput();
  check(price.value === "0", "typed 0 draft survives selection/filter re-renders");

  // --- 5a. sell: no double submit while in flight ---
  const sellBtn = () => all(app, n => n.attrs.id === "inv-sell")[0];
  price.value = "0";
  price.dispatch("input");
  sellBtn().onclick();
  await sleep();
  check(posts().filter(p => p === "POST /market/offers").length === 0,
    "invalid draft price posts nothing");
  check(toasts.some(t => t.includes("Цена должна быть целым числом")),
    "invalid draft price explains itself");
  price.value = "10";
  price.dispatch("input");
  const readResponder = responder;
  let releaseSell;
  responder = async (p, o = {}) => {
    if (o.method === "POST" && p === "/market/offers") {
      return new Promise(r => { releaseSell = r; });
    }
    return readResponder(p, o);
  };
  const sellPosts = () => posts().filter(p => p === "POST /market/offers").length;
  const held = sellBtn();
  held.onclick();
  await sleep();
  check(sellPosts() === 1, "one sell POST in flight");
  check(held.disabled === true, "sell button disabled while in flight");
  held.onclick();
  held.onclick();
  await sleep();
  check(sellPosts() === 1, "second click during flight is a no-op");
  all(app, n => n.className === "inv-item")
    .find(c => c.attrs["data-object-id"] === "2").onclick();
  all(app, n => n.className === "inv-item")
    .find(c => c.attrs["data-object-id"] === "1").onclick();
  check(all(app, n => n.attrs.id === "inv-sell")[0].disabled === true,
    "repaint keeps the sell button disabled while in flight");

  // --- 5b. stale response: generation bumped, no mutation of money/inventory ---
  const money = S.character.balance;
  const beforeBump = requests.length;
  await viewInventory();  // fresh authoritative load bumps invActionGen
  check(requests.length === beforeBump + 2, "fresh load refetches");
  releaseSell({ id: 11, object_id: 1, price: 10 });
  await sleep();
  check(requests.length === beforeBump + 2, "stale sell issues no follow-up request");
  check(S.character.balance === money, "stale sell leaves money untouched");
  check(S.invSellPrice[1] === "10", "stale sell does not consume the draft");
  check(!toasts.includes("Предмет выставлен на рынок"), "stale success is not announced");
  check(!invActionBusy.has(1), "stale sell clears the busy flag");

  // --- 5c. wear: disabled in flight, stale response leaves worn/inventory alone ---
  let releaseWear;
  const wearBtn = () => all(app, n => n.attrs.id === "inv-wear")[0];
  const heldWear = wearBtn();
  check(heldWear && heldWear.textContent === "Надеть", "wear action for jacket");
  const origResponder = responder;
  responder = async (p, o = {}) => {
    if (o.method === "POST" && p === "/wear") {
      return new Promise(r => { releaseWear = r; });
    }
    return origResponder(p, o);
  };
  heldWear.onclick();
  await sleep();
  check(posts().filter(p => p === "POST /wear").length === 1, "one wear POST in flight");
  check(all(app, n => n.attrs.id === "inv-wear")[0].disabled === true,
    "wear button disabled while in flight");
  all(app, n => n.attrs.id === "inv-wear")[0].onclick();
  await sleep();
  check(posts().filter(p => p === "POST /wear").length === 1,
    "second wear click during flight is a no-op");
  const beforeWearBump = requests.length;
  await viewInventory();
  releaseWear({ object_id: 1, worn: true });
  await sleep();
  check(requests.length === beforeWearBump + 2, "stale wear issues no follow-up request");
  check(invCache.items.find(i => i.id === 1).worn === false,
    "stale wear does not mutate the cached inventory");
  check(S.character.balance === money, "stale wear leaves money untouched");
  check(!invActionBusy.has(1), "stale wear clears the busy flag");
  responder = origResponder;

  // --- 6. honest missing data, retry, admin gate ---
  const char = S.character;
  S.character = null;
  await viewInventory();
  check(app.textContent.includes("Инвентарь недоступен"),
    "no character is unavailable, not an empty bag");
  check(!app.textContent.includes("Пусто — купите"), "unavailable is not the empty message");
  S.character = char;
  S.character.location_id = 99;
  S.mapLocations = [];
  S.weather = null;
  await viewInventory();
  check(app.textContent.includes("Место не уточнено"), "honest missing location");
  check(app.textContent.includes("Погода не уточнена"), "honest missing weather");
  S.character.location_id = 1;
  S.mapLocations = [{ id: 1, name: "Дом" }];
  responder = async () => { throw Error("offline"); };
  await viewInventory();
  check(app.textContent.includes("Повторить"), "retry offered on failure");
  responder = origResponder;
  await viewInventory();
  S.user.role = "admin";
  await viewInventory();
  check(all(app, n => n.attrs.href === "#/admin").length === 1, "admin gate link");
  S.user.role = "player";
  await viewInventory();
  check(!app.textContent.includes("Админ"), "gate closes for players again");

  // --- final network audit ---
  check(!requests.some(p => p.includes("/locations")), "never a /locations fetch");
  check(requests.every(p => p === invPath || p === "/market/offers?status=active"
    || p === "POST /market/offers" || p === "POST /wear"),
    "only inventory and market/wear paths are ever called");
  return "PASS: selection, filters, unknown type, sell draft, double-submit, stale guards, pinned shell";
})();
`;
new Function(prelude + bounds.map(([a, b]) => extract(a, b)).join("\n") + scenario)()
  .then(result => console.log(result), error => { console.error(error); process.exitCode = 1; });
