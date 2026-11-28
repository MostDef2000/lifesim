/* #367: browser-only controlled fixture for the production shell components.
   No real backend, no production account, no network mutations. */
const app = document.getElementById("app");
const S = {
  user: {role: "player"},
  character: {id: "c1", name: "Алиса", location_id: 1, control_mode: "DIRECT", balance: 984},
  world: {game_timestamp: 60},
  weather: {enabled: true, description: "ясно"},
  mapLocations: [{id: 1, name: "Дом"}],
  invSellPrice: {},
};
let navEpoch = 0;
let mode = "hold";
let releaseInventory = null;
const calls = [];
const items = [
  {id: 1, object_type: "jacket", quantity: 1, condition: 90, worn: true, slot: "body",
    location_id: 1},
  {id: 2, object_type: "food_bread", quantity: 2, condition: 100, location_id: 1},
];
async function api(path, opts = {}) {
  calls.push({path, method: opts.method || "GET"});
  if (opts.method && opts.method !== "GET") throw Error("Unexpected mutation");
  if (path === "/characters/c1/inventory") {
    if (mode === "hold") return new Promise(resolve => { releaseInventory = resolve; });
    if (mode === "error") throw Error("fixture offline");
    return mode === "empty" ? [] : items;
  }
  if (path === "/market/offers?status=active") return [];
  if (path === "/locations/1") return {id: 1, name: "Дом"};
  throw Error("Unexpected fixture path: " + path);
}
function toast() {}
function clockPendingLabel() { return "…"; }
function placeTaskHostNode() { return el("span", {}, "нет активной задачи"); }
function check(value, label) {
  if (!value) throw Error(label);
}
async function waitFor(pred, label) {
  for (let i = 0; i < 100; i++) {
    if (pred()) return;
    await new Promise(resolve => setTimeout(resolve, 20));
  }
  throw Error("Timed out: " + label);
}
function dock(active, inventory) {
  const nav = document.querySelector(".world-pill");
  check(nav, "dock present");
  const links = [...nav.querySelectorAll("a")];
  check(links.length === 9, "nine destinations");
  check(links.filter(a => a.getAttribute("aria-current") === "page").length === 1,
    "exactly one active");
  check(nav.querySelector('[aria-current="page"]').getAttribute("href") === active,
    "correct active destination");
  const rect = nav.getBoundingClientRect();
  check(rect.left >= -1 && rect.right <= innerWidth + 1 && rect.bottom <= innerHeight + 1,
    "dock fits viewport");
  for (const a of links) {
    const r = a.getBoundingClientRect();
    check(a.getAttribute("aria-label") && r.width > 0 && r.height >= 44,
      "named, reachable link: " + a.textContent);
    check(r.left >= -1 && r.right <= innerWidth + 1 && r.bottom <= innerHeight + 1,
      "link fits viewport: " + a.textContent);
  }
  if (inventory) {
    const scroll = document.querySelector(".inventory-shell-scroll");
    check(scroll.getBoundingClientRect().bottom <= rect.top + 1, "dock does not cover content");
    check(scroll.scrollWidth <= scroll.clientWidth + 1, "inventory has no horizontal overflow");
    check(document.querySelectorAll("#clock").length === 1, "one clock");
    check(!document.querySelector(".topbar"), "no legacy topbar");
  }
  check(document.documentElement.scrollWidth <= innerWidth + 1, "no document overflow");
}
function worldFixture() {
  ++navEpoch;
  app.replaceChildren(el("main", {class: "world-shell"},
    el("section", {}, "Мир — fixture"), gameSectionNav("#/world")));
}
window.addEventListener("hashchange", () => {
  if (location.hash === "#/world") worldFixture();
  else if (location.hash === "#/inventory") viewInventory();
});
async function probe() {
  const first = viewInventory();
  dock("#/inventory", true);
  check(document.querySelector(".game-state-loading"), "loading state");
  check(document.querySelector("#clock").textContent === "День 1, 01:00", "game clock");
  releaseInventory(items);
  await first;
  dock("#/inventory", true);
  check(document.querySelectorAll(".inv-item").length === 2, "real inventory rows");
  check(document.querySelector(".inv-shell").textContent.includes("надето"), "worn state");
  const firstLink = document.querySelector(".world-pill a");
  firstLink.click();
  await waitFor(() => document.querySelector(".world-shell"), "world fixture");
  dock("#/world", false);

  mode = "empty";
  document.querySelector('.world-pill a[href="#/inventory"]').click();
  await waitFor(() => document.querySelector(".inv-shell"), "empty inventory");
  dock("#/inventory", true);
  check(document.querySelector(".inv-shell").textContent.includes("Пусто"), "empty state");

  mode = "error";
  await viewInventory();
  dock("#/inventory", true);
  check(document.querySelector(".game-state button").textContent === "Повторить", "retry");
  mode = "ready";
  document.querySelector(".game-state button").click();
  await waitFor(() => document.querySelectorAll(".inv-item").length === 2, "retry ready");
  dock("#/inventory", true);

  mode = "hold";
  const stale = viewInventory();
  document.querySelector('.world-pill a[href="#/world"]').click();
  await waitFor(() => document.querySelector(".world-shell"), "leave pending inventory");
  releaseInventory(items);
  await stale;
  dock("#/world", false);
  check(!document.querySelector(".inventory-game-shell"), "stale response discarded");
  check(calls.every(c => c.method === "GET"), "navigation never mutates gameplay");

  mode = "ready";
  document.querySelector('.world-pill a[href="#/inventory"]').click();
  await waitFor(() => document.querySelectorAll(".inv-item").length === 2, "return inventory");
  dock("#/inventory", true);
  return {ok: true, width: innerWidth, height: innerHeight, calls};
}
