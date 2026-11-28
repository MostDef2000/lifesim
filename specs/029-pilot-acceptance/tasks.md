# 029 — Tasks (#370)

- [x] Переиспользовать vm harness #307 (`tests/m307/journey_harness.py`): реальный production `app.js` с boot, канонические фикстуры; `tests/m307/**` не изменён.
- [x] 13 сценариев приёмочной матрицы #370 в `tests/m370/test_pilot_shell.py`: `MAP_INTENT`, `SHOP_CONFIRM`, `SHOP_INSUFFICIENT`, `INVENTORY_LOCAL`, `RETURN_CONTEXT`, `STALE_SCENE`, `ACTION_REFUSAL`, `FLUX_TIMEOUT`, `AUTONOMOUS`, `OFFLINE_RECONNECT`, `STALE_SESSION`, `EMPTY_INVENTORY`, `SHELL_AND_PREVIEW`.
- [x] Обойти ограничение harness'а (нет descendant-комбинаторов) явным хелпером `stageHasImage()`.
- [x] Ассерты только на наблюдаемых фактах (DOM, POST-тела, счётчики запросов, task registry); `unhandled == []` в каждом сценарии.
- [x] Никакой production-код не изменён; симуляция не тронута; новые actions/POI не выдуманы.
- [x] Spec-артефакты `specs/029-pilot-acceptance/{spec,plan,tasks}.md` (goal/scope/non-goals, Gherkin, canon refs #365/#366/#372/#354, #396/#397, verification commands).
- [x] `tests/m370/README.md` — что добавлено, как запускать, что не покрыто.
- [ ] Полный quality gate (`bash scripts/check.sh`) — перезапуск оркестратором.
- [ ] Browser viewport evidence (реальные Chromium-скриншоты пилотных поверхностей) — **NOT delivered**: browser fixture в `tests/m370` не написан; приёмка выполнена на vm harness.

## Evidence

- `uv run pytest tests/m370 -q` → **13 passed**.
- `uv run pytest tests/m307 tests/m370 -q` → **48 passed** (существующие #307-тесты не сломаны).
- `uv run pytest tests/m367 tests/m368 tests/m369 tests/m370 -q` → **17 passed, 2 skipped** (Chromium-skip, m367).
- `git diff --check` → OK (нет whitespace-ошибок).
- Browser viewport evidence: **NOT delivered** (browser fixture не написан).
- Полный gate будет перезапущен оркестратором.
