# 027 — Tasks (#368)

- [x] Проверить current main и baseline contracts #363/#366/#396/#397.
- [x] Сетка карточек 3/2 колонки; карточка: тип, количество, состояние, локация, бейдж «Надето».
- [x] Локальный выбор: `invView.selectedId` + `invSelectedPanel`, ноль сетевых запросов.
- [x] Локальные фильтры: `invFilterBar` (чипы Всё + 3 категории) и поиск, `invFilteredItems`, ноль сетевых запросов; filter-empty «Ничего не найдено» + «Сбросить фильтр»; истинно пустой — «Пусто».
- [x] Честный fallback: `invTypeLabel` «Неизвестный тип предмета» (unknown/empty), неизвестная локация → raw id, null → «—».
- [x] Черновик продажи: «0» сохраняется как draft, синхронизация на input, нулевая цена отклоняется toast «Цена должна быть целым числом ≥ 1» без POST.
- [x] Double-submit guard: кнопка disabled в полёте, повторный клик не шлёт второй POST.
- [x] Stale generation guard: `invActionGen` — ответ старой генерации не делает refetch, не показывает success toast, busy очищается.
- [x] Честные данные: без пользователя — «Инвентарь недоступен», не «Пусто».
- [x] Pinned literals m154/m157/m173/m176/m367 сохранены.
- [x] Node DOM behavioral test через pytest: `tests/m368/inventory_cards.node.js` + `tests/m368/test_inventory_cards.py` (PASS; `invItemRow`/`invDetail` извлечены — карточка упражняется кликом через `renderInventory`).
- [x] Spec-артефакты `specs/027-interactive-inventory/{spec,plan,tasks}.md` (goal/scope/non-goals, Gherkin, canon refs #366/#363/#396/#397, pins и verification commands).
- [ ] Полный quality gate (`bash scripts/check.sh`) — перезапуск оркестратором.

## Evidence

- `bash scripts/check.sh`: 1895 passed / 1 failed (m154 «Надето» duplicate) → исправлено → pinned suites 80 passed / 2 skipped.
- Node harness: `PASS: selection, filters, unknown type, sell draft, double-submit, stale guards, pinned shell`.
- `uv run pytest tests/m368 -q` → 1 passed; `uv run pytest tests/m154 tests/m173 tests/m157 tests/m176 tests/m367 tests/m368 -q` → 81 passed, 2 skipped (Chromium-skip, m367).
- Bite-proof (base_sha `35f4802` = HEAD без рабочего дерева): тот же harness падает — `ReferenceError: invTypeLabel is not defined` (exit 1) и ранее `no selection explains selection`.
- `node --check tests/m368/inventory_cards.node.js` → OK; `git diff --check` → OK; `git status --short` → только `M backend/app/web/{app.js,style.css}` (не трогались) + untracked `tests/m368/`, `specs/027-interactive-inventory/`.
- Полный gate будет перезапущен оркестратором.
