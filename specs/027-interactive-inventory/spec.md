# 027 — Interactive inventory cards (#368)

## Цель
Превратить инвентарь из статичного списка в рабочий экран: карточки предметов в сетке 3/2 колонки, локальный выбор и фильтры (ноль сетевых запросов), честный fallback для неизвестных типов, черновик продажи с валидацией и защита от double-submit / stale-ответов. Канон: утверждённый дизайн #366, токены #363, #396/#397 — никаких user-facing modes.

## Scope
- Сетка карточек: 3 колонки на desktop, 2 на mobile. Карточка = тип предмета, количество, состояние, локация, бейдж «Надето».
- Локальный выбор: клик по карточке → панель деталей; ноль сетевых запросов.
- Локальные фильтры: чипы категорий (Всё + 3 категории) и поиск; ноль сетевых запросов. Filter-empty = «Ничего не найдено» + «Сбросить фильтр»; истинно пустой инвентарь = «Пусто».
- Честный fallback: неизвестный/пустой `object_type` → «Неизвестный тип предмета»; неизвестная локация → raw id; `null` → «—».
- Черновик продажи: ввод цены сохраняет «0» как черновик; синхронизация в state на input; валидация «Цена должна быть целым числом ≥ 1», POST не уходит при нуле.
- Double-submit guard: кнопка disabled в полёте; повторный клик игнорируется.
- Stale generation guard: ответ из старой генерации не делает refetch, не показывает success toast, не мутирует state; busy очищается.
- Честные данные: нет пользователя → «Инвентарь недоступен», не сфабрикованное «Пусто».
- Пinned literals m154/m157/m173/m176/m367 сохранены.

## Не трогаем
Карта/POI, backend/API, экономика, управление, глобальные CSS-токены, WS/feed, остальные системные экраны. Никаких user-facing modes (#396/#397).

## Acceptance

```gherkin
Feature: Interactive inventory cards

  Rule: Local selection and filters issue zero network requests
    Scenario: Selecting a card
      Given the inventory is loaded with 3 items
      When the player clicks the "jacket" card
      Then the detail panel shows the selected item
      And no API request is issued

    Scenario: Category chip filter
      Given the inventory is loaded
      When the player clicks the "Провизия" chip
      Then only food items are shown
      And no API request is issued

    Scenario: Search filter
      Given the inventory is loaded
      When the player types "mystery" into the search
      Then only matching items are shown
      And no API request is issued

    Scenario: Filter-empty vs empty
      Given the search matches nothing
      When the player types "zzz"
      Then the screen shows "Ничего не найдено" and "Сбросить фильтр"
      Given the inventory is truly empty
      When the inventory renders
      Then the screen shows "Пусто" and not "Ничего не найдено"

  Rule: Honest fallback for unknown data
    Scenario: Unknown item type
      Given an item with object_type "mystery_thing"
      When the inventory renders
      Then the card shows "Неизвестный тип предмета"

    Scenario: Unknown location
      Given an item at location_id 9 (unknown)
      When the inventory renders
      Then the card shows "#9"
      Given an item with null location
      Then the card shows "—"

  Rule: Sell draft and guards
    Scenario: Draft "0" is preserved
      Given the sell price draft is "0"
      When the sell row renders
      Then the input shows "0"

    Scenario: Draft survives selection and filter re-renders
      Given the sell price draft is "0" for the jacket
      When the player changes the category filter and reselects the card
      Then the sell input still shows "0"

    Scenario: Zero price is rejected
      Given the sell price draft is "0"
      When the player submits the sell
      Then the toast "Цена должна быть целым числом ≥ 1" is shown
      And no POST is issued

    Scenario: Double-submit is blocked
      Given a sell request is in flight
      When the player clicks the sell button again
      Then the button is disabled and no second POST is issued

    Scenario: Stale response mutates nothing
      Given a sell response is in flight
      When the inventory is reloaded (new generation)
      And the stale response arrives
      Then no refetch, no success toast, and busy state is cleared
      And money, the worn flag and the cached inventory are unchanged

  Rule: Pinned shell behavior stays intact
    Scenario: Player sees the dock and honest footer
      Given the inventory renders for a player
      Then the dock has nine destinations with exactly one current
      And no Admin link is shown
      And the footer links to "#/appearance" and to "#/shop"
      And the screen notes that use goes through EAT/DRINK game actions

    Scenario: Failure offers retry
      Given the inventory request fails
      Then the screen offers "Повторить"

    Scenario: Admin gate
      Given the user role is admin
      Then the Admin link appears exactly once

  Rule: Honest data
    Scenario: Inventory unavailable
      Given the user is not authenticated
      When the inventory loads
      Then the screen shows "Инвентарь недоступен" and not "Пусто"
```

1. Сетка карточек 3 колонки (desktop) / 2 колонки (mobile); карточка показывает тип, количество, состояние, локацию и бейдж «Надето».
2. Выбор карточки и фильтры (чипы, поиск) — только локальные мутации state; ноль сетевых запросов.
3. Честные fallback: неизвестный тип, неизвестная/нулевая локация, «Инвентарь недоступен» вместо сфабрикованного «Пусто».
4. Черновик продажи сохраняет «0»; нулевая цена отклоняется без POST; double-submit и stale-ответы не мутируют state.
5. Pinned literals m154/m157/m173/m176/m367 сохранены; focused Node DOM harness (pytest) PASS; bash scripts/check.sh; acceptance evidence до merge.
6. Pinned shell: девять destinations, Admin gate, retry, footer links (#/appearance, #/shop), EAT/DRINK note — закреплены Node harness'ом (`tests/m368`).
