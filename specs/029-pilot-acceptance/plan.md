# 029 — Plan (#370)

- **Переиспользование vm harness #307**: `tests/m370/test_pilot_shell.py` импортирует из `tests/m307/journey_harness.py` — реальный production `backend/app/web/app.js` (boot включён), канонические фикстуры (`CHAR1`, `CHAR1_AT_2`, `CHAR1_AUTONOMOUS`, `SCENE_ASSET_1`, `TASK_30701`, `TASK_30702`, `USER`), `_base`/`_journey_responses`/`_resp`/`_tasks_resp`, `facts`, `run_scenarios`, `unhandled`. `tests/m307/**` не изменяется — только импорт.
- **13 сценариев** (один `SCENARIOS`-entry + один тест-функций на сценарий): `MAP_INTENT`, `SHOP_CONFIRM`, `SHOP_INSUFFICIENT`, `INVENTORY_LOCAL`, `RETURN_CONTEXT`, `STALE_SCENE`, `ACTION_REFUSAL`, `FLUX_TIMEOUT`, `AUTONOMOUS`, `OFFLINE_RECONNECT`, `STALE_SESSION`, `EMPTY_INVENTORY`, `SHELL_AND_PREVIEW`. Фикстуры #370 (баланс 100/50, jacket/bread/relic/hat, offer 9001) — локальные константы модуля, встраиваются в vm-драйверы JSON-токенами (`__CHAR_AT_2__` и т.п.), никогда не интерполируются как Python.
- **Ограничение harness'а**: минимальный selector-движок harness'а не поддерживает descendant-комбинаторы (`.scene-image-host img` не работает). Обход — явный хелпер `stageHasImage()` в JS-драйвере: находит `.scene-image-host` и отдельно проверяет его `<img>`-ребёнка.
- **Ассерты только на наблюдаемых фактах**: `__facts()` (DOM, `postCount`, POST-тела, `taskRegistry`, `fetchCompletions`), локальные `invFacts()`/`shopFacts()`/`appearanceFacts()`/`shellFacts()`; в каждом сценарии `unhandled(...) == []`.
- **Никакой production-код не меняется**: `app.js`, `style.css`, backend, симуляция — нет; новые actions/POI не выдуманы (только канонические фикстуры #307).
- Команды верификации: `uv run pytest tests/m370 -q`; `uv run pytest tests/m307 tests/m370 -q`; `uv run pytest tests/m367 tests/m368 tests/m369 tests/m370 -q`; `git diff --check`.
- Затем полный quality gate: `bash scripts/check.sh` (перезапуск оркестратором).

Одна ветка `feat/370-pilot-acceptance` и bounded draft PR #370. Без merge/deploy до подтверждения gates.
