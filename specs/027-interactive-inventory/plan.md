# 027 — Plan (#368)

- В `app.js` инвентарная область: `invView` (selectedId, category, search), `invCache` (items, listed), `invActionBusy` (Set активных действий), `invActionGen` (счётчик генераций для stale guard).
- `invPanelShell` — оболочка панели; `invTypeLabel` — подпись типа предмета с честным fallback «Неизвестный тип предмета»; `invFilteredItems` — локальная фильтрация (чипы + поиск); `invFilterBar` — чипы категорий и поиск; `invSelectedPanel` — панель деталей выбранного предмета.
- `invSellRow` — черновик цены продажи, валидация «≥ 1», double-submit guard (disabled в полёте) и stale generation guard; в harness извлекается, а не stub'ится, потому что guards живут именно там. `invItemRow` и `invDetail` тоже извлекаются целиком (не stub'ятся, в отличие от harness #367): клик по карточке и панель деталей упражняются через `renderInventory` — этого требует покрытие #368 (локальный выбор, unknown type в DOM, disabled-состояния в полёте).
- Сетка карточек 3/2 колонки в `style.css` — изолированные правила перед закреплённым conn-banner tail; глобальные токены #363 не менять.
- Pinned literals m154/m157/m173/m176/m367 сохраняются; m154 «Надето» duplicate устранён в реализации.
- Node DOM behavioral test через pytest: `tests/m368/inventory_cards.node.js` + `tests/m368/test_inventory_cards.py`. Извлечение: `const INV_WEARABLE_TYPES` + `function invCategoryRu(` .. `/* ---------- #65` (плюс общие #367-примитивы `appendKids`/`RETRY_HINT`/`svgIcon`). Команды верификации: `node --check tests/m368/inventory_cards.node.js`; `node tests/m368/inventory_cards.node.js`; `uv run pytest tests/m368 -q`; `uv run pytest tests/m154 tests/m173 tests/m157 tests/m176 tests/m367 tests/m368 -q`; `git diff --check`; `bash scripts/check.sh` (перезапуск оркестратором).
- Затем полный quality gate: `bash scripts/check.sh` (перезапуск оркестратором).

Одна ветка `feat/368-interactive-inventory` и bounded draft PR #368. Без merge/deploy до подтверждения gates.
