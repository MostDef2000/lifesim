# 028 — Appearance shell (#369)

## Цель
Перенести экран образа в общий игровой shell: все состояния (loading/ready/empty/error/unavailable) рендерятся внутри `.appearance-game-shell` с одним доком и одним живым часом; девять destinations с одним активным, «На карту», hero-контекст (имя, локация, баланс), Admin gate, typed-черновик outfit prompt, переживающий re-render, preview, который никогда не мутирует гардероб, stale guard для preview-ответов и retry при ошибке. Канон: утверждённый дизайн #372, токены #363, #396/#397 — никаких user-facing modes.

## Scope
- Общий shell `appearanceGameShell`: loading, ready, empty, error, unavailable — всё внутри shell; один док, один живой час, legacy topbar не рендерится.
- Навигация: девять dock destinations, ровно один `aria-current="page"` (`#/appearance`), только именованные ссылки, «На карту» → `#/world`.
- Hero-контекст: имя, локация, баланс переживают перенос в shell.
- Admin gate: ссылка `#/admin` ровно один раз для admin, нет для player.
- Честные состояния: нет пользователя → «Образ недоступен» (не сфабрикованное «Ничего не надето»); пустой гардероб → «Гардероб пуст — загляните на »; ошибка → «Повторить» внутри shell.
- Typed-черновик: `APPEARANCE_DRAFT` (client-only, без web storage) — outfit prompt переживает navigation re-render.
- Preview: единственный `POST /visual/preview` (единственная appearance site), кнопка disabled в полёте, повторный клик — no-op, результат подписан «Предпросмотр — не сохранён», preview никогда не мутирует гардероб/worn state.
- Stale guard: `appearancePreviewGen` + `navEpoch`/session guards — ответ preview из старой генерации/вида не поднимает ghost error и не мутирует ничего.
- Pinned constraints m176 сохранены: 10 tests, `.appearance-shell` count 6, `mode: "full_body_outfit"` count 1, APPEARANCE_DRAFT client-only.

## Не трогаем
Карта/POI, backend/API, экономика, управление, глобальные CSS-токены, WS/feed, остальные системные экраны. Никаких user-facing modes (#396/#397).

## Acceptance

```gherkin
Feature: Appearance shell

  Rule: All states render inside the shared shell
    Scenario: Loading state
      Given the appearance view is loading
      Then the shell renders with the dock and one live clock
      And no legacy topbar is rendered

    Scenario: Ready state
      Given the appearance data is loaded
      Then the dock has nine destinations with exactly one current
      And the current destination is "#/appearance"
      And the hero shows the name, location and balance
      And the "Текущий образ" and "Гардероб" zones render

    Scenario: Session unavailable
      Given the user is not authenticated
      When the appearance loads
      Then the screen shows "Образ недоступен" and not "Ничего не надето"
      And the dock is still present
      And no read request is issued

    Scenario: Empty wardrobe
      Given the wardrobe is truly empty
      When the appearance renders
      Then the screen shows "Гардероб пуст — загляните на "
      And the shell is preserved

    Scenario: Error keeps the shell and offers retry
      Given the appearance request fails
      Then the shell is preserved with the dock
      And the screen offers "Повторить"

  Rule: Navigation
    Scenario: Back to World
      Given the appearance is ready
      Then the "На карту" link points to "#/world"

    Scenario: Admin gate
      Given the user role is admin
      Then the Admin link appears exactly once
      Given the user role is player
      Then no Admin link is shown

  Rule: Typed draft survives re-render
    Scenario: Outfit prompt draft
      Given the player types "красное платье для свидания" into the outfit prompt
      When the appearance re-renders (navigation)
      Then the prompt still shows the typed draft
      And no web storage is touched

  Rule: Preview isolation
    Scenario: One preview request
      Given the player clicks preview
      Then exactly one POST /visual/preview is in flight
      And the button is disabled while pending
      And a second click is a no-op

    Scenario: Result labelled unsaved
      Given the preview response arrives
      Then the preview image is shown
      And the caption says "Предпросмотр — не сохранён"
      And the actual wardrobe and worn state are unchanged

    Scenario: Stale preview is dropped
      Given a preview response is in flight
      When the player navigates away (new navEpoch)
      And the stale response arrives
      Then no ghost error is raised
      And nothing is mutated
```

1. Все состояния (loading/ready/empty/error/unavailable) рендерятся внутри общего shell с одним доком и одним живым часом; legacy topbar не рендерится.
2. Навигация: девять destinations, один активный, «На карту», Admin gate.
3. Typed-черновик `APPEARANCE_DRAFT` переживает re-render, client-only (без web storage).
4. Preview: единственная site, pending guard, подпись «не сохранён», никогда не мутирует гардероб; stale-ответы отбрасываются `appearancePreviewGen`/`navEpoch`.
5. Pinned constraints m176 сохранены (10 tests, `.appearance-shell` count 6, `mode: "full_body_outfit"` count 1); focused Node DOM harness (pytest) PASS; bash scripts/check.sh; acceptance evidence до merge.
6. Pinned shell: девять destinations, Admin gate, retry, hero-контекст — закреплены Node harness'ом (`tests/m369`).
