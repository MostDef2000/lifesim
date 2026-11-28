# 029 — Pilot acceptance (#370)

## Цель
Cross-browser acceptance-приёмка пилотных поверхностей (общий shell + интерактивный инвентарь) одним pytest-модулем `tests/m370/test_pilot_shell.py`: 13 сценариев, переиспользующих реальный production vm harness из #307 (`tests/m307/journey_harness.py` — настоящий `backend/app/web/app.js` с boot) и канонические фикстуры, с ассертами только на наблюдаемых фактах (DOM-состояние, счётчики запросов, POST-тела, task registry). Никакой production-код не меняется; существующие #307-тесты не трогаются.

## Scope
- `tests/m370/test_pilot_shell.py` — 13 сценариев приёмочной матрицы #370:
  1. `MAP_INTENT` — выбор POI на карте ≠ MOVE; явная отправка — единственный поддерживаемый action (один `POST /actions` MOVE); прибытие подтверждается серверным чтением, а не клиентом.
  2. `SHOP_CONFIRM` — открытый confirm-панель не покупка; только явный confirm шлёт `POST /market/offers/9001/buy`; accepted ≠ completed — баланс/инвентарь меняет только авторитетное чтение.
  3. `SHOP_INSUFFICIENT` — отказ «insufficient_funds» ничего не меняет (баланс, инвентарь), confirm-панель остаётся, геймплей продолжается.
  4. `INVENTORY_LOCAL` — фильтр/поиск/выбор — локальные (ноль новых запросов); «Надеть» — один POST, повторный клик в полёте — no-op; worn state подтверждает только авторитетное чтение инвентаря.
  5. `RETURN_CONTEXT` — возврат на место сохраняет контекст (badge «Вы здесь»), статус задачи и кэшированный кадр сцены; повторный `/visual/scenes` не шлётся.
  6. `STALE_SCENE` — устаревший ответ сцены (локация 1) никогда не становится текущим кадром на локации 2, даже если дошёл до приложения.
  7. `ACTION_REFUSAL` — отказанный scene-act сохраняет черновик (переживает navigation roundtrip) и не фабрикует задачу.
  8. `FLUX_TIMEOUT` — Flux недоступен/таймаут: честное состояние без картинки, авто-ретраи нет, геймплей (действие, чат) продолжается.
  9. `AUTONOMOUS` — автономный персонаж действует сам: ручной MOVE откладывается клиентом (ноль POST, toast); после авторитетного перехода на DIRECT та же отправка — один POST.
  10. `OFFLINE_RECONNECT` — офлайн: неудачный MOVE остаётся на месте (один POST, честный feedback, задача не регистрируется); явный retry после восстановления работает.
  11. `STALE_SESSION` — 401 на MOVE: сессия сбрасывается, fail-closed на `#/login`, задача не регистрируется.
  12. `EMPTY_INVENTORY` — пустой рюкзак («Пусто — купите что-нибудь во Владивостоке») отличается от filter-empty («Ничего не найдено»); ввод фильтра в пустом рюкзаке не меняет сообщение.
  13. `SHELL_AND_PREVIEW` — общий shell (док, один pill, без admin-гейта для player, без user-facing mode-словаря) на map/inventory/appearance; preview — один POST, картинка скрыта до ответа, worn state и гардероб не мутируют.
- Ассерты только на наблюдаемых фактах: `__facts()` (DOM, `postCount`, POST-тела, `taskRegistry`, `fetchCompletions`), `invFacts()`, `shopFacts()`, `appearanceFacts()`, `shellFacts()`, `stageHasImage()`.
- В каждом сценарии: `unhandled(...) == []` — ни одна ошибка не уходит в void.

## Не трогаем (non-goals)
- **Никакой production-код не меняется**: `app.js`, `style.css`, backend, API, симуляция — нет.
- **Никакие новые actions/POI не выдуманы**: сценарии используют только существующие POI/задачи/предметы канонических фикстур #307 (локации 1/2, задачи 30701/30702, NPC Фар, шляпа 9001, jacket/bread/relic).
- Существующие `tests/m307/**` не изменяются — только импорт.
- Реальные браузерные viewport-скриншоты (Chromium) — не в scope этого модуля; приёмка — на vm harness.
- Никаких user-facing modes (#396/#397): ассерты проверяют их отсутствие на поверхностях.

## Canon refs
- #365 / #366 — общий игровой shell и интерактивный инвентарь (поверхности, принимаемые здесь).
- #372 — утверждённый дизайн пилота (shell, dock, destinations).
- #354 — journey-приёмка #307: vm harness и канонические фикстуры, переиспользуемые без изменений.
- #396 / #397 — никаких user-facing modes: на пилотных поверхностях нет «Вручную»/«Автономно»/«Режим не уточнён».

## Acceptance

```gherkin
Feature: Pilot acceptance — shared shell + interactive inventory

  Rule: Map intent
    Scenario: POI selection is not a move; the explicit send is one action; arrival is server-confirmed
      Given the player is at location 1 on the world map
      When the player selects the shop POI
      Then the POI is highlighted locally and zero POSTs are sent
      And the character is still at location 1
      When the player clicks "Пойти сюда"
      Then exactly one POST /actions MOVE to location 2 is sent
      And task 30701 is registered with server status planned/active
      And the character is still at location 1 (accepted is not arrived)
      When the server read confirms the move
      Then the character is at location 2
      And task 30701 server status is completed

  Rule: Shop
    Scenario: An open confirm is not a purchase; only the explicit confirm POSTs; only the authoritative read changes state
      Given the shop is open with balance 100 ₽ and no confirm panel
      When the player opens the buy sheet
      Then no POST is sent and the balance is still 100 ₽
      When the player confirms the purchase
      Then exactly one POST /market/offers/9001/buy is sent
      And the balance is still 100 ₽ (accepted is not completed)
      When the authoritative reads land (new balance, new inventory)
      Then the balance shows 50 ₽ and the hat appears in the sell list
      And the purchase POST is not repeated

    Scenario: Insufficient funds changes nothing and gameplay continues
      Given the buy confirm is refused with insufficient_funds
      Then the balance and inventory are unchanged
      And the status shows "Недостаточно средств" with a bridge to the goal screen
      And the confirm panel stays open
      When the player navigates to the world
      Then the shared world shell renders and gameplay continues

  Rule: Inventory
    Scenario: Filter and selection are local; wear is server-confirmed
      Given the inventory shows 3 cards with nothing selected
      When the player selects the jacket card
      Then the detail repaints from cache and zero new requests are sent
      When the player applies the "Провизия" category filter
      Then only the bread card is shown and zero new requests are sent
      When the player searches "relic"
      Then only the relic card is shown and zero new requests are sent
      When the player clicks "Надеть"
      Then exactly one POST /wear is sent and the button is disabled in flight
      And a second click is a no-op
      And the stale read does not mark the item worn
      When the authoritative inventory read confirms the wear
      Then the detail shows "надето" and the /wear POST is not repeated

  Rule: Return
    Scenario: Return preserves context, task status and frame freshness without double requests
      Given the player is at location 2 with the scene ready and task 30702 registered
      When the player visits the tasks screen
      Then task 30702 is still registered
      When the player returns to location 2
      Then the badge shows "Вы здесь" and the cached scene frame is re-mounted
      And task 30702 is still registered
      And /visual/scenes is not requested a second time

    Scenario: A stale scene response never becomes the current frame
      Given a scene request for location 1 is in flight
      When the player arrives at location 2
      And the stale location-1 response is delivered to the app
      Then no scene image is installed at location 2
      And gameplay (the chat stage) continues

  Rule: Failure matrix
    Scenario: A refused action keeps the draft and fabricates no task
      Given the scene action is refused by the server
      Then the typed draft "Поговорить с Фаром" survives
      And no task is registered
      When the player navigates away and back
      Then the draft still survives and no task is registered
      And gameplay continues into the chat stage

    Scenario: Flux unavailable does not block gameplay
      Given the scene visual call is pending
      Then no ready image is shown
      When the visual call fails with a 504 flux timeout
      Then the honest state remains (no image) and the call is not auto-retried
      And the scene action and the chat stage still work

    Scenario: An autonomous character defers the manual move without posting
      Given the character control is AUTONOMOUS
      When the player sends a manual move
      Then zero POSTs are sent, the character stays put and no task is registered
      And the toast says the character acts on its own
      When the authoritative read switches control to DIRECT
      Then the same send is exactly one POST /actions

    Scenario: An offline move fails closed and the reconnect retry works
      Given the move POST fails with a network error
      Then the character stays put, no task is registered
      And the feedback says the request could not be confirmed
      And visiting the world does not retry the failed move
      When the player explicitly retries after reconnect
      Then the move POST succeeds and the character arrives

    Scenario: A stale session fails closed to the login route
      Given the move POST is answered with 401
      Then the session is dropped and the route is #/login
      And no task is registered

    Scenario: An empty inventory is distinct from a filter-empty result
      Given the inventory is truly empty
      Then the screen shows "Пусто — купите что-нибудь во Владивостоке" and not "Ничего не найдено"
      When the player types a filter in the empty bag
      Then the empty-bag message is kept
      And the world shell still renders after navigation

  Rule: Shared shell parity
    Scenario: The shared shell is present on every pilot surface and the preview never changes worn state
      Given the world, inventory and appearance screens are visited
      Then each renders its game shell with one dock pill
      And a player sees no admin gate
      And no user-facing mode vocabulary ("Вручную"/"Автономно"/"Режим не уточнён") is shown
      When the player generates an appearance preview
      Then exactly one POST /visual/preview is sent and the image is hidden until the answer lands
      And no /wear POST and no outfit PATCH are sent
      When the preview image arrives
      Then it is shown as a data URL and the wardrobe worn state is unchanged
```

1. 13 сценариев приёмочной матрицы #370 реализованы в `tests/m370/test_pilot_shell.py` и PASS на реальном production `app.js` через vm harness #307.
2. Ассерты — только наблюдаемые факты (DOM, POST-тела, счётчики запросов, task registry); ни одна ошибка не уходит в void (`unhandled == []` в каждом сценарии).
3. Никакой production-код не изменён; симуляция не тронута; новые actions/POI не выдуманы.
4. Никаких user-facing modes (#396/#397) на пилотных поверхностях.
5. Реальные браузерные viewport-скриншоты — не в scope (см. `tests/m370/README.md`).
