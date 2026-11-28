"""#370 — cross-browser acceptance of the shared shell + interactive inventory.

This module ADDS to the #307 journey suite: it reuses the real-production
``tests/m307/journey_harness.py`` vm harness (real ``backend/app/web/app.js``,
boot included) and the canonical fixtures, and asserts the #370 acceptance
matrix on observable facts only:

  1. Map intent: POI selection never MOVEs; the explicit send is the ONE
     supported action; arrival is server-confirmed.
  2. Shop: an open confirm is not a purchase; only the explicit confirm POSTs;
     only the authoritative read changes balance/inventory.
  3. Inventory: filter/selection are local; equipment is server-confirmed;
     preview never changes worn state.
  4. Return: context, task status and frame freshness survive; no double
     requests; a stale scene never becomes current.
  5. Failure matrix: refusal, insufficient funds, Flux unavailable/timeout,
     autonomous, offline/reconnect, stale session, empty inventory; no
     gameplay is blocked by a missing image.
  6. Shared shell parity across the map / inventory / appearance screens.

No production code is changed. Existing #307 tests are untouched.
"""

import json

import pytest
from tests.m307.journey_harness import (
    CHAR1,
    CHAR1_AUTONOMOUS,
    SCENE_ASSET_1,
    TASK_30701,
    TASK_30702,
    USER,
    _base,
    _journey_responses,
    _resp,
    _tasks_resp,
    run_scenarios,
    unhandled,
)

# ---------- fixtures ----------

CHAR_BAL = {
    **CHAR1,
    "balance": 100,
    "sex": "m",
    "age": 30,
    "outfit_mode": "auto",
}
CHAR_BAL_AT_2 = {**CHAR_BAL, "location_id": 2}
CHAR_BAL_50 = {**CHAR_BAL, "balance": 50}

INV_JACKET = {
    "id": 1, "object_type": "jacket", "quantity": 1, "condition": 90,
    "worn": False, "slot": "body", "location_id": 1,
}
INV_BREAD = {
    "id": 2, "object_type": "food_bread", "quantity": 2, "condition": 100,
    "location_id": 1,
}
INV_ODD = {
    "id": 3, "object_type": "relic_box", "quantity": 1, "condition": 50,
    "location_id": 1,
}
INV_HAT = {
    "id": 42, "object_type": "hat", "quantity": 1, "condition": 100,
    "worn": False, "slot": "head", "location_id": 1,
}
INV_JACKET_WORN = {**INV_JACKET, "worn": True}

OFFER_HAT = {
    "id": 9001, "object_id": 42, "item_name": "Шляпа", "price": 50,
    "seller": "99", "seller_name": "Торговец", "status": "active",
}

INITIAL_ITEMS = [INV_JACKET, INV_BREAD, INV_ODD]
BOUGHT_ITEMS = [INV_JACKET, INV_BREAD, INV_ODD, INV_HAT]

# JSON tokens embedded into the vm drivers (never interpolated as Python).
_TOKENS = {
    "__CHAR_AT_2__": json.dumps(CHAR_BAL_AT_2),
    "__CHAR_BAL__": json.dumps(CHAR_BAL),
    "__CHAR_50__": json.dumps(CHAR_BAL_50),
    "__CHAR_AUTONOMOUS__": json.dumps(CHAR1_AUTONOMOUS),
    "__INITIAL_ITEMS__": json.dumps(INITIAL_ITEMS),
    "__BOUGHT_ITEMS__": json.dumps(BOUGHT_ITEMS),
    "__JACKET_WORN__": json.dumps([INV_JACKET_WORN, INV_BREAD, INV_ODD]),
    "__OFFER_HAT__": json.dumps([OFFER_HAT]),
    "__TASKS_30701_DONE__": json.dumps(_tasks_resp(recent=[TASK_30701])),
    "__TASKS_30702_ACTIVE__": json.dumps(_tasks_resp(active=[TASK_30702])),
    "__SCENE_ASSET_1__": json.dumps(SCENE_ASSET_1),
    "__SCENE_ACT_OK__": json.dumps({
        "ok": True, "task_id": 30702, "task_type": "SOCIALIZE",
        "task_label": "Пообщаться",
    }),
}


def _d(template):
    for token, value in _TOKENS.items():
        template = template.replace(token, value)
    return template


def _shop_responses(balance=100, inventory=None, offers=None):
    """Boot + the #/shop + #/inventory read model (authoritative GETs only)."""
    d = _base(user=USER, char={**CHAR_BAL, "balance": balance})
    d["/characters/1/inventory"] = _resp(
        200, INITIAL_ITEMS if inventory is None else inventory)
    d["/market/offers?status=active"] = _resp(
        200, [OFFER_HAT] if offers is None else offers)
    return d


def _appearance_responses():
    d = _base(user=USER, char=CHAR_BAL)
    d["/characters/1/inventory"] = _resp(200, INITIAL_ITEMS)
    d["/market/offers?status=active"] = _resp(200, [])
    return d


# ---------- driver helpers shared by every scenario ----------

_JS = r"""
function elById(id) { return document.getElementById(id); }
function qAll(sel) { return document.querySelectorAll(sel); }
function reqCount() { return globalThis.__net.log.length; }
function postList() { return __facts().posts; }
function invCard(id) {
  return qAll('.inv-item').find((c) =>
    c.getAttribute('data-object-id') === String(id));
}
// The minimal harness selector engine has no descendant combinators; check
// the scene image host and its <img> child explicitly.
function stageHasImage() {
  const host = document.querySelector('.scene-image-host');
  return !!(host && host.querySelector('img'));
}
function invFacts() {
  const cards = qAll('.inv-item');
  const detail = elById('inv-detail');
  const emptyDetail = document.querySelector('.inv-detail-empty');
  const wear = elById('inv-wear');
  return {
    cardCount: cards.length,
    cardIds: cards.map((c) => c.getAttribute('data-object-id')),
    pressed: cards.map((c) => c.getAttribute('aria-pressed')),
    jacketText: invCard(1) ? invCard(1).textContent : null,
    detailText: detail ? detail.textContent : null,
    emptyDetailText: emptyDetail ? emptyDetail.textContent : null,
    wearText: wear ? wear.textContent : null,
    wearDisabled: wear ? !!wear.disabled : null,
    hasSearch: !!elById('inv-search'),
    appText: elById('app') ? elById('app').textContent : null,
    reqCount: reqCount(),
    postCount: __facts().postCount,
  };
}
function shopFacts() {
  const bal = elById('shop-balance');
  const conf = elById('shop-confirm');
  const sell = elById('shop-sell');
  const status = document.querySelector('.shop-status');
  return {
    balanceText: bal ? bal.textContent : null,
    hasConfirm: !!conf,
    confirmText: conf ? conf.textContent : null,
    sellText: sell ? sell.textContent : null,
    statusText: status ? status.textContent : null,
    postCount: __facts().postCount,
    posts: __facts().posts,
    reqCount: reqCount(),
  };
}
function appearanceFacts() {
  const img = document.querySelector('.appear-preview-img');
  return {
    hasShell: !!document.querySelector('.appearance-game-shell'),
    pillCount: qAll('.world-pill').length,
    hasPreviewImg: !!img,
    previewHidden: img ? !!img.hidden : null,
    previewSrc: img ? img.src : null,
    appText: elById('app') ? elById('app').textContent : null,
    posts: __facts().posts,
    nonGetHead: __facts().nonGetHead,
    postCount: __facts().postCount,
  };
}
function shellFacts() {
  return {
    hash: location.hash,
    pillCount: qAll('.world-pill').length,
    hasWorldShell: !!document.querySelector('.world-shell'),
    hasInventoryShell: !!document.querySelector('.inventory-game-shell'),
    hasAppearanceShell: !!document.querySelector('.appearance-game-shell'),
    hasAdminLink: qAll('a').some((a) => a.getAttribute('href') === '#/admin'),
    appText: elById('app') ? elById('app').textContent : null,
    postCount: __facts().postCount,
  };
}
"""


def _driver(body):
    return _JS + _d(body)


# ---------- scenarios ----------

SCENARIOS = [
    # 1. Map intent: selection ≠ MOVE; the explicit send is one action.
    {
        "name": "MAP_INTENT",
        "initialHash": "#/world",
        "responses": _journey_responses(char=CHAR_BAL),
        "driver": _driver(
            "await __settle(); const boot = __facts(); "
            "__clickButton('Магазин'); await __settle(); "
            "const selected = __facts(); "
            "__clickButton('Пойти сюда'); await __settle(); "
            "const accepted = __facts(); "
            "__setResponse('/characters/1', {status:200, body: __CHAR_AT_2__}); "
            "__setResponse('/characters/1/tasks', __TASKS_30701_DONE__); "
            "await __call('pollEvents'); await __settle(); "
            "const arrived = __facts(); "
            "globalThis.__result = { boot, selected, accepted, arrived };"
        ),
    },
    # 2. Shop: explicit confirm, accepted ≠ completed.
    {
        "name": "SHOP_CONFIRM",
        "initialHash": "#/shop",
        "responses": _shop_responses(),
        "driver": _driver(
            "await __settle(); const boot = shopFacts(); "
            "__clickButton('Купить'); await __settle(); "
            "const confirmStep = shopFacts(); "
            "__hold('/market/offers/9001/buy'); "
            "__clickButton('Купить за 50 ₽'); await __settle(); "
            "const accepted = shopFacts(); "
            "__setResponse('/characters/1', {status:200, body: __CHAR_BAL__}); "
            "__setResponse('/characters/1/inventory', "
            "{status:200, body: __INITIAL_ITEMS__}); "
            "__setResponse('/market/offers?status=active', "
            "{status:200, body: __OFFER_HAT__}); "
            "__release('/market/offers/9001/buy', {status:200, body: {ok: true}}); "
            "await __settle(); const completed = shopFacts(); "
            "__setResponse('/characters/1', {status:200, body: __CHAR_50__}); "
            "__setResponse('/characters/1/inventory', "
            "{status:200, body: __BOUGHT_ITEMS__}); "
            "__setResponse('/market/offers?status=active', "
            "{status:200, body: []}); "
            "__setHash('#/world'); await __settle(); "
            "__setHash('#/shop'); await __settle(); "
            "const refreshed = shopFacts(); "
            "globalThis.__result = { boot, confirmStep, accepted, completed, "
            "refreshed };"
        ),
    },
    # 3. Shop failure: insufficient funds changes nothing, gameplay continues.
    {
        "name": "SHOP_INSUFFICIENT",
        "initialHash": "#/shop",
        "responses": {
            **_shop_responses(),
            "/market/offers/9001/buy": _resp(
                400, {"detail": "insufficient_funds"}),
        },
        "driver": _driver(
            "await __settle(); "
            "__clickButton('Купить'); await __settle(); "
            "__clickButton('Купить за 50 ₽'); await __settle(); "
            "const refused = shopFacts(); "
            "__setHash('#/world'); await __settle(); "
            "const world = shellFacts(); "
            "globalThis.__result = { refused, world };"
        ),
    },
    # 4. Inventory: local filter/selection, server-confirmed wear.
    {
        "name": "INVENTORY_LOCAL",
        "initialHash": "#/inventory",
        "responses": _shop_responses(),
        "driver": _driver(
            "await __settle(); const boot = invFacts(); const r0 = reqCount(); "
            "invCard(1).click(); await __settle(); "
            "const selected = invFacts(); const r1 = reqCount(); "
            "__clickButton('Провизия'); await __settle(); "
            "const filtered = invFacts(); const r2 = reqCount(); "
            "__clickButton('Всё'); await __settle(); "
            "const search = elById('inv-search'); "
            "search.value = 'relic'; search.oninput(); await __settle(); "
            "const searched = invFacts(); const r3 = reqCount(); "
            "__hold('/wear'); "
            "__clickButton('Надеть'); await __settle(); "
            "const wearAccepted = invFacts(); "
            "__clickButton('Надеть'); await __settle(); "
            "const wearDouble = invFacts(); "
            "__release('/wear', {status:200, body: {ok: true}}); "
            "await __settle(); const wearRefreshOld = invFacts(); "
            "__setResponse('/characters/1/inventory', "
            "{status:200, body: __JACKET_WORN__}); "
            "__setHash('#/world'); await __settle(); "
            "__setHash('#/inventory'); await __settle(); "
            "const wearConfirmed = invFacts(); "
            "globalThis.__result = { boot, selected, filtered, searched, "
            "wearAccepted, wearDouble, wearRefreshOld, wearConfirmed, "
            "r0, r1, r2, r3 };"
        ),
    },
    # 5. Return: context, task status and frame freshness survive.
    {
        "name": "RETURN_CONTEXT",
        "initialHash": "#/world",
        "responses": _journey_responses(char=CHAR_BAL),
        "driver": _driver(
            "await __settle(); "
            "__setResponse('/characters/1', {status:200, body: __CHAR_AT_2__}); "
            "__setResponse('/characters/1/tasks', __TASKS_30701_DONE__); "
            "await __call('pollEvents'); await __settle(); "
            "__setHash('#/location/2'); await __settle(); "
            "__stageButton(); await __settle(); "
            "__type('scene-act-input', 'Поговорить с Фаром'); "
            "__clickButton('Сделать'); await __settle(); "
            "const atPlace = __facts(); "
            "__setResponse('/characters/1/tasks', __TASKS_30702_ACTIVE__); "
            "__setHash('#/tasks'); await __settle(); "
            "const atTasks = __facts(); "
            "__setHash('#/location/2'); await __settle(); "
            "const returned = __facts(); "
            "const returnedImg = stageHasImage(); "
            "globalThis.__result = { atPlace, atTasks, returned, returnedImg };"
        ),
    },
    # 6. A stale scene response never becomes the current frame.
    {
        "name": "STALE_SCENE",
        "initialHash": "#/world",
        "responses": _journey_responses(char=CHAR_BAL),
        "driver": _driver(
            "await __settle(); "
            "__hold('/visual/scenes'); "
            "__setHash('#/location/1'); await __settle(); "
            "__stageButton(); await __settle(); "
            "__setResponse('/characters/1', {status:200, body: __CHAR_AT_2__}); "
            "__setResponse('/characters/1/tasks', __TASKS_30701_DONE__); "
            "await __call('pollEvents'); await __settle(); "
            "__setHash('#/location/2'); await __settle(); "
            "__release('/visual/scenes', {status:200, body: __SCENE_ASSET_1__}); "
            "await __settle(); "
            "await Promise.resolve(); await Promise.resolve(); "
            "await Promise.resolve(); "
            "const _f = __facts(); "
            "const atPlace = Object.assign({}, _f, { staleDelivered: "
            "_f.fetchCompletions.some((c) => c.url === '/visual/scenes' "
            "&& c.status === 200), stageImg: stageHasImage() }); "
            "__clickButton('Поговорить'); await __settle(); "
            "const atChat = __facts(); "
            "globalThis.__result = { atPlace, atChat };"
        ),
    },
    # 7. Action refusal: the draft survives; no task is fabricated.
    {
        "name": "ACTION_REFUSAL",
        "initialHash": "#/world",
        "responses": {
            **_journey_responses(char=CHAR_BAL),
            "/scene/act": _resp(200, {"ok": False, "detail": "critical failure"}),
        },
        "driver": _driver(
            "await __settle(); "
            "__setResponse('/characters/1', {status:200, body: __CHAR_AT_2__}); "
            "__setResponse('/characters/1/tasks', __TASKS_30701_DONE__); "
            "await __call('pollEvents'); await __settle(); "
            "__setHash('#/location/2'); await __settle(); "
            "__stageButton(); await __settle(); "
            "__type('scene-act-input', 'Поговорить с Фаром'); "
            "__clickButton('Сделать'); await __settle(); "
            "const refused = __facts(); "
            "__setHash('#/world'); await __settle(); "
            "const roundtrip = __facts(); "
            "__setHash('#/location/2'); await __settle(); "
            "__clickButton('Поговорить'); await __settle(); "
            "const atChat = __facts(); "
            "globalThis.__result = { refused, roundtrip, atChat };"
        ),
    },
    # 8. Flux unavailable/timeout: no image, gameplay continues.
    {
        "name": "FLUX_TIMEOUT",
        "initialHash": "#/world",
        "responses": _journey_responses(char=CHAR_BAL),
        "driver": _driver(
            "await __settle(); "
            "__setResponse('/characters/1', {status:200, body: __CHAR_AT_2__}); "
            "__setResponse('/characters/1/tasks', __TASKS_30701_DONE__); "
            "await __call('pollEvents'); await __settle(); "
            "__setHash('#/location/2'); await __settle(); "
            "__hold('/visual/scenes'); "
            "__stageButton(); await __settle(); "
            "const pending = __facts(); const pendingImg = stageHasImage(); "
            "__release('/visual/scenes', {status:504, body: "
            "{detail: 'flux timeout'}}); await __settle(); "
            "const atPlace = __facts(); const atPlaceImg = stageHasImage(); "
            "__type('scene-act-input', 'Поговорить с Фаром'); "
            "__clickButton('Сделать'); await __settle(); "
            "const atScene = __facts(); "
            "__clickButton('Поговорить'); await __settle(); "
            "__type('chat-msg-input', 'Привет, Фар'); "
            "__clickButton('→'); await __settle(); "
            "const atChat = __facts(); "
            "globalThis.__result = { pending, pendingImg, atPlace, atPlaceImg, "
            "atScene, atChat };"
        ),
    },
    # 9. Autonomous: the AI acts itself; a manual move is deferred (no POST).
    {
        "name": "AUTONOMOUS",
        "initialHash": "#/world",
        "responses": _journey_responses(char=CHAR1_AUTONOMOUS),
        "driver": _driver(
            "await __settle(); "
            "__clickButton('Магазин'); await __settle(); "
            "__clickButton('Пойти сюда'); await __settle(); "
            "const gated = __facts(); "
            "__clickButton('Мир'); await __settle(); "
            "const back = __facts(); "
            "__setResponse('/characters/1', {status:200, body: __CHAR_BAL__}); "
            "await __call('pollEvents'); await __settle(); "
            "__clickButton('Магазин'); await __settle(); "
            "__clickButton('Пойти сюда'); await __settle(); "
            "const retried = __facts(); "
            "globalThis.__result = { gated, back, retried };"
        ),
    },
    # 10. Offline/reconnect: the failed move stays put; an explicit retry works.
    {
        "name": "OFFLINE_RECONNECT",
        "initialHash": "#/world",
        "responses": {
            **_journey_responses(char=CHAR_BAL),
            "/actions": _resp(200, None, __networkFail=True),
        },
        "driver": _driver(
            "await __settle(); "
            "__clickButton('Магазин'); await __settle(); "
            "__clickButton('Пойти сюда'); await __settle(); "
            "const failed = __facts(); "
            "__clickButton('Мир'); await __settle(); "
            "const back = __facts(); "
            "__setResponse('/actions', {status:200, body: {task_id: 30701, "
            "task_type: 'MOVE', task_label: 'Перемещение', "
            "status: 'planned'}}); "
            "__clickButton('Магазин'); await __settle(); "
            "__clickButton('Пойти сюда'); await __settle(); "
            "__setResponse('/characters/1', {status:200, body: __CHAR_AT_2__}); "
            "__setResponse('/characters/1/tasks', __TASKS_30701_DONE__); "
            "await __call('pollEvents'); await __settle(); "
            "const retried = __facts(); "
            "globalThis.__result = { failed, back, retried };"
        ),
    },
    # 11. Stale session: a 401 move fails closed back to the login route.
    {
        "name": "STALE_SESSION",
        "initialHash": "#/world",
        "responses": {
            **_journey_responses(char=CHAR_BAL),
            "/actions": _resp(401, {"detail": "unauthorized"}),
        },
        "driver": _driver(
            "await __settle(); const boot = __facts(); "
            "__clickButton('Магазин'); await __settle(); "
            "__clickButton('Пойти сюда'); await __settle(); "
            "const loggedOut = __facts(); "
            "globalThis.__result = { boot, loggedOut };"
        ),
    },
    # 12. Empty inventory: the empty bag is distinct from filter-empty.
    {
        "name": "EMPTY_INVENTORY",
        "initialHash": "#/inventory",
        "responses": _shop_responses(inventory=[], offers=[]),
        "driver": _driver(
            "await __settle(); const empty = invFacts(); "
            "const search = elById('inv-search'); "
            "search.value = 'zzz'; search.oninput(); await __settle(); "
            "const filteredEmpty = invFacts(); "
            "__setHash('#/world'); await __settle(); "
            "const world = shellFacts(); "
            "globalThis.__result = { empty, filteredEmpty, world };"
        ),
    },
    # 13. Shared shell parity + appearance preview isolation.
    {
        "name": "SHELL_AND_PREVIEW",
        "initialHash": "#/world",
        "responses": _appearance_responses(),
        "driver": _driver(
            "await __settle(); const world = shellFacts(); "
            "__setHash('#/inventory'); await __settle(); "
            "const inventory = shellFacts(); "
            "__setHash('#/appearance'); await __settle(); "
            "const boot = appearanceFacts(); "
            "__hold('/visual/preview'); "
            "__clickButton('Сгенерировать предпросмотр'); await __settle(); "
            "const pending = appearanceFacts(); "
            "__release('/visual/preview', {status:200, body: "
            "{image_base64: 'Zm9v'}}); await __settle(); "
            "const preview = appearanceFacts(); "
            "__setHash('#/inventory'); await __settle(); "
            "const inv = invFacts(); "
            "globalThis.__result = { world, inventory, boot, pending, preview, "
            "inv };"
        ),
    },
]


@pytest.fixture(scope="module")
def results():
    return run_scenarios(SCENARIOS)


def _uh(results, name):
    assert unhandled(results, name) == [], (
        f"{name}: no unhandled errors may escape: {unhandled(results, name)}")


def _raw(results, name):
    r = results[name]
    assert r.get("ok"), f"{name}: harness error: {r.get('error')}"
    return r["result"]


# ---------- 1. Map intent ----------

def test_map_selection_is_not_a_move_and_send_is_one_action(results):
    r = _raw(results, "MAP_INTENT")
    boot, selected, accepted, arrived = (
        r["boot"], r["selected"], r["accepted"], r["arrived"])
    assert boot["charLocId"] == 1
    # Selecting a POI is a local map highlight; zero mutations.
    assert selected["guc"]["selectedPoiId"] == 2
    assert selected["postCount"] == 0, "selecting a POI must not POST"
    assert selected["charLocId"] == 1
    # The explicit send is exactly ONE supported action (the real MOVE).
    assert accepted["postCount"] == 1
    assert accepted["posts"][0]["url"] == "/actions"
    assert accepted["posts"][0]["body"] == {
        "character_id": "1", "action_type": "MOVE",
        "params": {"location_id": 2},
    }
    assert "30701" in accepted["taskRegistry"]
    assert accepted["taskRegistry"]["30701"]["serverStatus"] in ("planned", "active")
    # Arrival is confirmed by the server read, never by the client.
    assert accepted["charLocId"] == 1, "the player has not arrived yet"
    assert arrived["charLocId"] == 2
    assert arrived["taskRegistry"]["30701"]["serverStatus"] == "completed"
    _uh(results, "MAP_INTENT")


# ---------- 2. Shop ----------

def test_shop_confirm_is_the_only_purchase_and_reads_are_authoritative(results):
    r = _raw(results, "SHOP_CONFIRM")
    boot, confirm_step, accepted, completed, refreshed = (
        r["boot"], r["confirmStep"], r["accepted"], r["completed"],
        r["refreshed"])
    # Opening the buy sheet is not a purchase.
    assert boot["balanceText"] == "100 ₽"
    assert boot["hasConfirm"] is False
    assert boot["postCount"] == 0, "browsing the shop is read-only"
    assert "hat ×1" not in (boot["sellText"] or "")
    assert confirm_step["hasConfirm"] is True
    assert "Купить за 50 ₽" in (confirm_step["confirmText"] or "")
    assert confirm_step["postCount"] == 0, (
        "an open confirm panel must not POST a purchase")
    # The explicit confirm is the ONE purchase action.
    assert accepted["postCount"] == 1
    assert accepted["posts"][0]["url"] == "/market/offers/9001/buy"
    # Accepted ≠ completed: while the response is in flight / before the reads
    # land, neither balance nor inventory changes (no client-side math).
    assert accepted["balanceText"] == "100 ₽"
    assert completed["balanceText"] == "100 ₽", (
        "a successful POST must not change the balance before the authoritative read")
    assert "hat ×1" not in (completed["sellText"] or "")
    # The authoritative read (new balance + new inventory) is what changes state.
    assert refreshed["balanceText"] == "50 ₽"
    assert "hat ×1" in (refreshed["sellText"] or "")
    assert refreshed["postCount"] == 1, (
        "a read refresh must not repeat the purchase POST")
    _uh(results, "SHOP_CONFIRM")


def test_shop_insufficient_funds_changes_nothing_and_gameplay_continues(results):
    r = _raw(results, "SHOP_INSUFFICIENT")
    refused, world = r["refused"], r["world"]
    assert refused["postCount"] == 1, "the explicit confirm is the one attempt"
    assert refused["balanceText"] == "100 ₽"
    assert "hat ×1" not in (refused["sellText"] or "")
    assert "Недостаточно средств" in (refused["statusText"] or "")
    assert "Поставить цель" in (refused["statusText"] or ""), (
        "insufficient funds must bridge to the goal screen")
    assert refused["hasConfirm"] is True, "a refusal keeps the confirm panel"
    # The shared shell/gameplay continues after the failure.
    assert world["hash"] == "#/world"
    assert world["hasWorldShell"] is True
    _uh(results, "SHOP_INSUFFICIENT")


# ---------- 3. Inventory ----------

def test_inventory_filters_are_local_and_wear_is_server_confirmed(results):
    r = _raw(results, "INVENTORY_LOCAL")
    boot, selected, filtered, searched = (
        r["boot"], r["selected"], r["filtered"], r["searched"])
    # The initial view is read-only and no card is selected.
    assert boot["cardCount"] == 3
    assert boot["cardIds"] == ["1", "2", "3"]
    assert boot["pressed"] == ["false", "false", "false"]
    assert "Предмет не выбран" in (boot["emptyDetailText"] or "")
    assert boot["postCount"] == 0
    # Selection repaints from cache; zero new requests.
    assert selected["pressed"][0] == "true"
    assert "Одежда" in (selected["detailText"] or "")
    assert "jacket" in (selected["detailText"] or "")
    assert selected["reqCount"] == r["r0"] == r["r1"]
    # Category filter is local.
    assert filtered["cardIds"] == ["2"]
    assert filtered["reqCount"] == r["r2"] == r["r1"]
    # Search is local.
    assert searched["cardIds"] == ["3"]
    assert searched["reqCount"] == r["r3"] == r["r2"]
    # Wear: one POST, no double submit while in flight.
    wear_accepted, wear_double = r["wearAccepted"], r["wearDouble"]
    assert wear_accepted["postCount"] == 1
    assert wear_accepted["wearText"] == "Надеть"
    assert wear_accepted["wearDisabled"] is True
    assert "надето" not in (wear_accepted["detailText"] or "")
    assert wear_double["postCount"] == 1, "a second wear click is a no-op"
    # Accepted ≠ equipped: the stale read does not change worn state.
    wear_old = r["wearRefreshOld"]
    assert wear_old["postCount"] == 1
    assert "надето" not in (wear_old["detailText"] or ""), (
        "only the authoritative inventory read may equip the item")
    # The authoritative read confirms the equipment.
    wear_confirmed = r["wearConfirmed"]
    assert "надето" in (wear_confirmed["detailText"] or "")
    assert wear_confirmed["postCount"] == 1, (
        "the authoritative refresh must not repeat the /wear POST")
    _uh(results, "INVENTORY_LOCAL")


# ---------- 4. Return ----------

def test_return_preserves_context_task_and_frame_without_double_requests(results):
    r = _raw(results, "RETURN_CONTEXT")
    at_place, at_tasks, returned = r["atPlace"], r["atTasks"], r["returned"]
    # The place stage is ready and the scene task is registered.
    assert at_place["sceneStatus"] == "ready"
    assert at_place["sceneLocId"] == 2
    assert "30702" in at_place["taskRegistry"]
    visual_posts = [p for p in at_place["posts"] if p["url"] == "/visual/scenes"]
    assert len(visual_posts) == 1
    # The Tasks screen keeps the accepted task.
    assert at_tasks["hash"] == "#/tasks"
    assert "30702" in at_tasks["taskRegistry"]
    # The return restores the current-place context and the cached frame.
    assert returned["hash"] == "#/location/2"
    assert returned["placeBadgeText"] == "Вы здесь"
    assert returned["charLocId"] == 2
    assert returned["sceneStatus"] == "ready"
    assert returned["sceneLocId"] == 2
    assert r["returnedImg"] is True, (
        "the cached frame must be re-mounted on return")
    assert "30702" in returned["taskRegistry"]
    # No double requests: returning does not regenerate the scene.
    visual_posts = [p for p in returned["posts"] if p["url"] == "/visual/scenes"]
    assert len(visual_posts) == 1, (
        f"the return must not repeat /visual/scenes, got {len(visual_posts)}")
    _uh(results, "RETURN_CONTEXT")


def test_stale_scene_never_becomes_the_current_frame(results):
    r = _raw(results, "STALE_SCENE")
    at_place, at_chat = r["atPlace"], r["atChat"]
    assert at_place["hash"] == "#/location/2"
    assert at_place["placeBadgeText"] == "Вы здесь"
    assert at_place["stageImg"] is False, (
        "a stale location-1 image must not be installed at location 2")
    assert at_place["staleDelivered"] is True, (
        "the stale response must actually reach the app (non-trivial guard)")
    assert at_chat["chatNpcSelectValue"] == "2"
    _uh(results, "STALE_SCENE")


# ---------- 5. Failure matrix ----------

def test_refused_action_keeps_draft_and_fabricates_no_task(results):
    r = _raw(results, "ACTION_REFUSAL")
    refused, roundtrip, at_chat = r["refused"], r["roundtrip"], r["atChat"]
    assert refused["sceneAct"]["ok"] is False
    assert refused["sceneActDraft"] == "Поговорить с Фаром"
    assert "30702" not in refused["taskRegistry"]
    # The draft survives a navigation roundtrip.
    assert roundtrip["hash"] == "#/world"
    assert roundtrip["sceneActDraft"] == "Поговорить с Фаром"
    assert "30702" not in roundtrip["taskRegistry"]
    # Gameplay continues into the chat stage.
    assert at_chat["hash"] == "#/chat"
    assert at_chat["chatNpcSelectValue"] == "2"
    _uh(results, "ACTION_REFUSAL")


def test_flux_timeout_does_not_block_gameplay(results):
    r = _raw(results, "FLUX_TIMEOUT")
    pending, at_place, at_scene, at_chat = (
        r["pending"], r["atPlace"], r["atScene"], r["atChat"])
    # While the visual call is pending there is no ready image.
    assert pending["sceneStatus"] != "ready"
    assert r["pendingImg"] is False
    # After the failure the honest state remains and gameplay continues.
    assert at_place["sceneStatus"] != "ready"
    assert r["atPlaceImg"] is False
    visual_posts = [p for p in at_place["posts"] if p["url"] == "/visual/scenes"]
    assert len(visual_posts) == 1, "a failed visual call is not auto-retried"
    assert "30702" in at_scene["taskRegistry"], (
        "the scene action must work without a generated image")
    assert at_scene["sceneActDraft"] is None
    assert at_chat["hash"] == "#/chat"
    assert at_chat["chatNpcSelectValue"] == "2"
    assert at_chat["chatSessionId"] == "30703"
    msg_posts = [p for p in at_chat["posts"]
                 if p["url"] == "/dialogue/30703/message"]
    assert len(msg_posts) == 1
    _uh(results, "FLUX_TIMEOUT")


def test_autonomous_character_defers_manual_move_without_posting(results):
    r = _raw(results, "AUTONOMOUS")
    gated, back, retried = r["gated"], r["back"], r["retried"]
    # The AI acts itself: a manual move is deferred client-side (zero POSTs).
    assert gated["charControl"] == "AUTONOMOUS"
    assert gated["postCount"] == 0
    assert gated["charLocId"] == 1
    assert "30701" not in gated["taskRegistry"]
    assert gated["toastText"] == (
        "Персонаж действует сам — переключите режим в Профиле")
    assert back["hash"] == "#/world"
    # After the authoritative DIRECT transition the same send is one POST.
    assert retried["charControl"] == "DIRECT"
    move_posts = [p for p in retried["posts"] if p["url"] == "/actions"]
    assert len(move_posts) == 1
    _uh(results, "AUTONOMOUS")


def test_offline_move_fails_closed_and_reconnect_retry_works(results):
    r = _raw(results, "OFFLINE_RECONNECT")
    failed, back, retried = r["failed"], r["back"], r["retried"]
    assert failed["postCount"] == 1
    assert "30701" not in failed["taskRegistry"]
    assert failed["charLocId"] == 1
    assert failed["taskSubmitFeedback"] == (
        "Не удалось подтвердить принятие запроса. Проверьте связь")
    assert back["hash"] == "#/world"
    assert back["postCount"] == 1, (
        "the World visit must not retry the failed move")
    move_posts = [p for p in retried["posts"] if p["url"] == "/actions"]
    assert len(move_posts) == 2
    assert retried["charLocId"] == 2
    _uh(results, "OFFLINE_RECONNECT")


def test_stale_session_fails_closed_to_login(results):
    r = _raw(results, "STALE_SESSION")
    boot, logged_out = r["boot"], r["loggedOut"]
    assert boot["userId"] == 1
    assert logged_out["userId"] is None, "a 401 must drop the session"
    assert logged_out["hash"] == "#/login"
    assert "30701" not in logged_out["taskRegistry"]
    assert logged_out["postCount"] == 1
    _uh(results, "STALE_SESSION")


def test_empty_inventory_is_distinct_from_filter_empty(results):
    r = _raw(results, "EMPTY_INVENTORY")
    empty, filtered_empty, world = r["empty"], r["filteredEmpty"], r["world"]
    assert "Пусто — купите что-нибудь во Владивостоке" in (empty["appText"] or "")
    assert "Ничего не найдено" not in (empty["appText"] or "")
    assert empty["postCount"] == 0
    # Typing a filter in an empty bag keeps the empty-bag message.
    assert "Пусто — купите что-нибудь во Владивостоке" in (
        filtered_empty["appText"] or "")
    assert "Ничего не найдено" not in (filtered_empty["appText"] or "")
    assert world["hash"] == "#/world"
    assert world["hasWorldShell"] is True
    _uh(results, "EMPTY_INVENTORY")


# ---------- 6. Shared shell + preview isolation ----------

def test_shared_shell_parity_and_preview_does_not_change_worn(results):
    r = _raw(results, "SHELL_AND_PREVIEW")
    world, inventory, boot = r["world"], r["inventory"], r["boot"]
    pending, preview, inv = r["pending"], r["preview"], r["inv"]
    # The shared dock/shell is present on every pilot surface.
    assert world["hash"] == "#/world"
    assert world["hasWorldShell"] is True
    assert world["pillCount"] == 1
    assert inventory["hash"] == "#/inventory"
    assert inventory["hasInventoryShell"] is True
    assert inventory["pillCount"] == 1
    assert boot["hasShell"] is True
    assert boot["pillCount"] == 1
    assert world["hasAdminLink"] is False, "a player sees no admin gate"
    assert inventory["hasAdminLink"] is False
    # No user-facing mode vocabulary on the surfaces.
    for app_text in (world["appText"], inventory["appText"], boot["appText"]):
        assert "Вручную" not in (app_text or "")
        assert "Автономно" not in (app_text or "")
        assert "Режим не уточнён" not in (app_text or "")
    # Preview: one POST, no worn change, no outfit PATCH.
    assert pending["postCount"] == 1
    assert pending["hasPreviewImg"] is True
    assert pending["previewHidden"] is True, "no image before the answer lands"
    preview_posts = [p for p in pending["posts"] if p["url"] == "/visual/preview"]
    assert len(preview_posts) == 1
    assert not any(p["url"] == "/wear" for p in pending["posts"])
    assert not any(p["url"].endswith("/outfit") for p in pending["nonGetHead"])
    assert preview["hasPreviewImg"] is True
    assert preview["previewHidden"] is False
    assert (preview["previewSrc"] or "").startswith("data:image/png;base64,")
    # The wardrobe is untouched by the preview.
    assert "надето" not in (inv["jacketText"] or "")
    assert not any(p["url"] == "/wear" for p in preview["posts"])
    _uh(results, "SHELL_AND_PREVIEW")
