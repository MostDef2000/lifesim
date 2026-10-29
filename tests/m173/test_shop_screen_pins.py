"""#173 — Shop / Trade screen (#/shop), UI layer of design spec #73.

The issue body is the spec of record (gh issue view 173; design of record:
gh issue view 73 §3–§8, canonical reference panel 5 of the approved board
docs/4799ff9f-c4a8-44a0-9ac0-93d4d31b435c.png; visual language: VISUAL-BIBLE
+ the approved alpha family). Byte-pins follow the m157/m158/m170 house
style (exact literal counts — updated deliberately, never weakened):

- Route + navigation: "#/shop" registered exactly once, topbar tab «Рынок»
  exactly once, sitting BETWEEN Инвентарь and Дела (the desire tab keeps its
  place between Дела and Профиль), direct hash navigation resolves.
- States (#73 §3–§4): balance header (own balance from GET /characters/{cid},
  .world-money styling), browse list (item_name/price/seller_name per offer;
  OWN offers show «Ваше предложение» + cancel INSTEAD of a buy button), buy
  with an explicit confirm step that shows the price AND balance BEFORE the
  POST, result feedback via toast + authoritative refresh, sell form from
  owned items (positive-int price validation) + my-active-offers list with
  cancel, explicit empty states, loadRetryNode for transient failures,
  guarded refresh without hash yank-back.
- Errors: MarketError codes (marketplace.py) mapped to honest human text via
  SHOP_ERROR_TEXT; unmapped codes fall through RAW — nothing invented.
- Honest gaps (pinned so they cannot silently regress): NO services section
  (the sim has no service mechanic — v1 ships buy/sell only; #73's
  «services не маскируются» is honored vacuously and stated in the PR),
  no fake out-of-stock/fake-disable affordances, no client-side money math
  (balance always comes from the read model).
- Wiring: inventory keeps its sell rows and links over to #/shop.
- #73 completion slice: the shop links to Free Requests both ways — a
  standing «Цель →» link in the head panel and a distinct insufficient-funds
  state (honest mapped text + inline «Поставить цель →» bridge to #/desire);
  categories/stock stay NEEDS-BACKEND (no category field on market offers,
  no stock model).
- XSS hygiene: innerHTML stays == 0 file-wide; all rendering textContent-only.
"""

import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
APP_JS = os.path.join(ROOT, "backend/app/web/app.js")
APP_CSS = os.path.join(ROOT, "backend/app/web/style.css")


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def test_shop_screen_route_and_tab_pins():
    js = _read(APP_JS)
    # Route + tab + view, each exactly once — same shape as the m158
    # master-journey pins; direct hash navigation must resolve #/shop.
    assert js.count('"#/shop": viewShop') == 1
    assert js.count('["#/shop", "Рынок"]') == 1
    assert js.count("async function viewShop()") == 1
    assert js.count("function renderShop(") == 1
    assert js.count("function shopErrorText(") == 1
    assert js.count("function shopCancelBtn(") == 1
    assert js.count('location.hash = "#/shop";') == 1
    # Loader + compose() share the same active-tab bar: 2 call sites
    # (compose is the single re-render factor for browse/confirm/sell).
    assert js.count('topbar("#/shop")') == 2
    # Tab order (#173 work order): Рынок sits BETWEEN Инвентарь and Дела;
    # the desire tab keeps its place between Дела and Профиль.
    idx_inv = js.index('["#/inventory", "Инвентарь"]')
    idx_shop = js.index('["#/shop", "Рынок"]')
    idx_tasks = js.index('["#/tasks", "Дела"]')
    idx_desire = js.index('["#/desire", "Цель"]')
    idx_profile = js.index('["#/profile", "Профиль"]')
    assert idx_inv < idx_shop < idx_tasks
    assert idx_tasks < idx_desire < idx_profile


def test_shop_states_pins():
    js = _read(APP_JS)
    # Panels, each exactly once: header+balance, browse, buy confirm, sell.
    assert js.count('"shop-head"') == 1
    assert js.count('"shop-browse"') == 1
    assert js.count('"shop-confirm"') == 1
    assert js.count('"shop-sell"') == 1
    assert js.count('"shop-my"') == 1
    assert js.count('id: "shop-balance"') == 1
    assert js.count('id: "shop-sell-item"') == 1
    assert js.count('id: "shop-sell-price"') == 1
    assert js.count('id: "shop-sell-go"') == 1
    # Browse list: item/price/seller per offer; own offers are marked and
    # cancellable INSTEAD of buyable (honest state, no doomed affordance).
    assert js.count('"Ваше предложение"') == 1
    assert js.count('"Мои предложения"') == 1
    assert js.count('"Предложения"') == 1
    assert js.count('"Выставить предмет"') == 1
    # Buy confirm shows the price BEFORE the POST (design #73 §4-5); the
    # buy button is the ONLY buy site and the ONLY /buy call.
    assert js.count('"Подтвердите покупку"') == 1
    assert js.count("Купить за ${buy.price} ₽") == 1
    assert js.count("`/market/offers/${buy.id}/buy`") == 1
    assert js.count('/buy`') == 1
    assert js.count('/cancel`') == 1
    # Result feedback: toasts on success, then the authoritative refresh
    # (balance included) — the client never invents the new balance.
    assert js.count("await refreshShop();") == 3  # cancel + buy + create
    assert js.count("async function refreshShop()") == 1
    # Guarded refresh (no hash yank-back; refreshDesire pattern).
    assert js.count('if (location.hash !== "#/shop") return;') == 1
    assert js.count('location.hash === "#/shop"') == 1
    # Positive-int price validation, same rule/wording as the inventory row:
    # invSellRow toast + shop status line + SHOP_ERROR_TEXT entry = 3.
    assert js.count('"Цена должна быть целым числом ≥ 1"') == 3
    assert js.count("Math.floor(Number(price.value))") == 2
    # Explicit empty states.
    assert js.count('"Рынок пуст — никто ничего не продаёт. '
                    'Выставьте предмет ниже."') == 1
    assert js.count('"У вас нет активных предложений"') == 1


def test_shop_error_mapping_pins():
    js = _read(APP_JS)
    # MarketError codes (marketplace.py) → honest human text, each once.
    assert js.count("const SHOP_ERROR_TEXT = {") == 1
    for key in ('insufficient_funds: "Недостаточно средств"',
                'own_offer: "Это ваше предложение — его нельзя купить"',
                'offer_closed: "Предложение уже закрыто — куплено или отменено"',
                'offer_not_found: "Предложение не найдено"',
                'item_unavailable: "Предмет недоступен — предложение устарело"',
                'already_listed: "Этот предмет уже выставлен на рынок"',
                'not_your_item: "Можно выставлять только свои предметы"',
                'not_your_offer: "Отменять можно только свои предложения"',
                'price_must_be_positive: "Цена должна быть целым числом ≥ 1"'):
        assert js.count(key) == 1, key
    # Unmapped codes fall through RAW — no invented success/failure text.
    assert js.count("return SHOP_ERROR_TEXT[code] || code || "
                    '"Не удалось выполнить операцию";') == 1
    # Errors land BOTH in the persistent status line and the toast.
    assert js.count("status.textContent = shopErrorText(e);") == 3
    assert js.count("toast(shopErrorText(e), true);") == 3


def test_shop_endpoint_use_pins():
    js = _read(APP_JS)
    # One authoritative read set per view (offers + balance + owned items):
    # the active-offers literal has 3 sites — inventory read + viewShop +
    # refreshShop.
    assert js.count('api("/market/offers?status=active")') == 3
    assert js.count('api("/market/offers", {') == 2  # invSellRow + shop create
    # +1 (#65): viewAppearance joins the authoritative inventory reads.
    assert js.count("`/characters/${S.character.id}/inventory`)") == 4
    # Mutations go through the existing endpoints only; buy/cancel are
    # template-literal paths (each exactly once).
    assert js.count("body: { object_id: Number(sel.value), price: p },") == 1
    # Warm shell: the world token set mirrored on the shop grid container.
    assert js.count('class: "grid shop-shell"') == 1
    # #93 retry pattern: viewShop joins it (m158/m162 pin the count == 6).
    assert js.count("loadRetryNode(loading, () => viewShop());") == 1
    # Balance/prices render through the world money style: 1 pre-existing
    # world-strip site + 4 shop sites (header balance, confirm price,
    # browse row, my-offers row).
    assert js.count('class: "world-money"') == 5
    assert js.count('class: "world-money", id: "shop-balance"') == 1


def test_shop_inventory_link_pins():
    js = _read(APP_JS)
    # Inventory KEEPS its sell rows and links over to #/shop (no removal).
    # +1 (#65): the appearance wardrobe empty state links to Рынок too.
    assert js.count('href: "#/shop"') == 2
    assert js.count('href: "#/inventory"') == 1  # shop sell-panel empty state
    assert js.count('"inv-sell"') == 1  # inventory sell affordance untouched
    assert js.count("function invSellRow(") == 1


def test_shop_desire_bridge_pins():
    # #73 completion slice: the shop is linked to Free Requests both ways —
    # a standing head link (topbar vocabulary «Цель» + the app's arrow
    # convention, cf. «Описать желание →») and a distinct insufficient-funds
    # state whose status line keeps the honest mapped text and appends an
    # inline «Поставить цель →» bridge (lack of money → formulate a goal).
    # The RAW code is detected BEFORE mapping (same shape shopErrorText
    # uses); every other code keeps the generic path; the toast stays
    # mapped; balance still comes from the read model only.
    js = _read(APP_JS)
    start = js.index("function renderShop(")
    # Body slice: renderShop's full body — up to its tail re-render call
    # (the standalone `composeHome();` line; the inline call inside the
    # «Купить» onclick ends earlier and must NOT cut the slice).
    body = js[start:js.index("\n  composeHome();\n}", start)]
    # (a) The standing #/desire link lives inside the shop body, once
    # (mutation-verified: label mutation → RED → byte-identical restore).
    assert body.count('el("a", { href: "#/desire" }, "Цель →")') == 1
    # (b) The insufficient-funds branch compares the RAW code before
    # mapping, once inside the shop body (mutation-verified the same way).
    assert body.count(
        'String(e.detail ?? e.message ?? "") === "insufficient_funds"') == 1
    # (c) The inline bridge label, file-wide exactly once.
    assert js.count('"Поставить цель →"') == 1
    # File-wide #/desire hash links: 4 pre-existing (Дела empty state, Дела
    # standing link, profile hook, appearance prompt zone) + 2 new shop
    # sites (standing head link + insufficient-funds bridge) = 6 — same
    # total the m170 wiring pin now records.
    assert js.count('href: "#/desire"') == 6
    # The generic error path is preserved for every other code: the plain
    # status assignment stays at 3 sites (cancel + buy-else + sell), and
    # the mapped toast at 3 (cancel + buy + sell).
    assert js.count("status.textContent = shopErrorText(e);") == 3
    assert js.count("toast(shopErrorText(e), true);") == 3


def test_shop_honesty_negative_pins():
    js = _read(APP_JS)
    # XSS hygiene holds file-wide while the new block was added.
    assert js.count("innerHTML") == 0
    # NO fake services section on the shop screen: the sim has no
    # player-facing market service mechanic, so the screen ships buy/sell
    # only (#73 «services не маскируются», honored vacuously; gap stated in
    # PR). NOTE: loc.services on the world screen (scene-act options) is a
    # DIFFERENT, pre-existing surface — the negatives here pin only
    # shop-shaped service literals.
    assert js.count('"Услуги"') == 0
    assert js.count('"Сервисы"') == 0
    assert js.count("shop-service") == 0
    # No fake stock/scarcity affordances and no client-side money math.
    assert js.count('"Нет в наличии"') == 0
    assert js.count('"Распродано"') == 0
    assert js.count("balance -") == 0 and js.count("- price") == 0
    # No premature-create mechanism: the confirm panel is the only buy path
    # (assignments: set on «Купить», cleared on success / cancel).
    assert js.count("S.shopConfirm = ") == 3
    # #175 appended chatDraft after shopConfirm in the S initializer — the
    # closing brace moved; the init tail is pinned byte-exactly at its new
    # shape (the confirm field is still the single null initializer).
    # #177 appended bootCursor as the LAST property (own line, #177 comment
    # rides as the trailing comment) — tail updated to the new truthful
    # bytes (count stays == 1, strength unchanged).
    assert js.count("shopConfirm: null, chatDraft: \"\",\n"
                    "  bootCursor: Infinity };") == 1
    # Adversarial-review P0 fix: the confirm step reads LIVE S.shopConfirm
    # everywhere — composeHome branches on the live flag and confirmPanel
    # finds by the live flag. The render-time capture (const confirmId)
    # made «Купить» re-render the same browse panel forever (confirm panel
    # unreachable) — pinned == 0 so the dead-capture bug cannot return.
    assert js.count("if (S.shopConfirm != null) {") == 1
    assert js.count("offers.find((o) => o.id === S.shopConfirm)") == 1
    assert js.count("const confirmId = S.shopConfirm;") == 0
    # Cancel carries the same disabled-in-flight guard as buy/sell.
    assert js.count("if (btn.disabled) return;") == 1


def test_shop_css_pins():
    css = _read(APP_CSS)
    # Appended after the pinned blocks (m157/m170 pin those byte-exact) —
    # each shop rule exists exactly once.
    for rule in (".shop-shell {", ".shop-shell .panel {", ".shop-offers {",
                 ".shop-offer {", ".shop-item {", ".shop-price {",
                 ".shop-mine {", ".shop-balance {", ".shop-status {",
                 ".shop-actions {", ".shop-sell-form {"):
        assert css.count(rule) == 1, rule
    # Warm token mirror: same values as .desire-shell, self-contained.
    assert css.count("--w-line:") == 4  # :root + .world-shell + .desire + .shop
    # Dividers reuse the --w-line fallback landed in #172 (no new token).
    assert css.count("border: 1px solid var(--w-line);") >= 2
    # No service-shaped CSS either.
    assert css.count(".service") == 0
