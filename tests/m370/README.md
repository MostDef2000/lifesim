# tests/m370 — Pilot acceptance (#370)

Cross-browser acceptance-приёмка пилотных поверхностей (общий shell + интерактивный
инвентарь) одним pytest-модулем.

## Что добавлено

- `test_pilot_shell.py` — 13 сценариев приёмочной матрицы #370, переиспользующих
  реальный production vm harness из #307 (`tests/m307/journey_harness.py`:
  настоящий `backend/app/web/app.js` с boot, канонические фикстуры). Ассерты —
  только на наблюдаемых фактах: DOM-состояние, счётчики запросов, POST-тела,
  task registry; в каждом сценарии `unhandled == []`.

Сценарии:

1. `MAP_INTENT` — выбор POI ≠ MOVE; явная отправка — один `POST /actions` MOVE; прибытие подтверждает сервер.
2. `SHOP_CONFIRM` — открытый confirm не покупка; только явный confirm POSTs; accepted ≠ completed — меняет состояние только авторитетное чтение.
3. `SHOP_INSUFFICIENT` — отказ «insufficient_funds» ничего не меняет; геймплей продолжается.
4. `INVENTORY_LOCAL` — фильтр/поиск/выбор локальные (ноль запросов); «Надеть» — один POST, повторный клик — no-op; worn подтверждает сервер.
5. `RETURN_CONTEXT` — возврат сохраняет контекст, статус задачи и кэшированный кадр; `/visual/scenes` не повторяется.
6. `STALE_SCENE` — устаревший ответ сцены никогда не становится текущим кадром.
7. `ACTION_REFUSAL` — отказанный action сохраняет черновик; задача не фабрикуется.
8. `FLUX_TIMEOUT` — Flux недоступен/таймаут: честное состояние, геймплей продолжается.
9. `AUTONOMOUS` — автономный персонаж: ручной MOVE отложен (ноль POST); после DIRECT — один POST.
10. `OFFLINE_RECONNECT` — офлайн: MOVE остаётся на месте; явный retry после восстановления работает.
11. `STALE_SESSION` — 401: сессия сброшена, fail-closed на `#/login`.
12. `EMPTY_INVENTORY` — пустой рюкзак отличается от filter-empty.
13. `SHELL_AND_PREVIEW` — общий shell на map/inventory/appearance; preview не мутирует worn state.

Никакой production-код не изменён; `tests/m307/**` не тронут (только импорт).

## Как запускать

```bash
uv run pytest tests/m370 -q                      # 13 passed
uv run pytest tests/m307 tests/m370 -q            # 48 passed
uv run pytest tests/m367 tests/m368 tests/m369 tests/m370 -q   # 17 passed, 2 skipped
```

## Что НЕ покрыто

- **Реальные браузерные viewport-скриншоты** (Chromium): browser fixture в этом
  модуле не написан — приёмка выполнена на vm harness (реальный `app.js` в
  изолированной DOM-среде), а не в настоящем браузере. Визуальные
  viewport-пробы пилотных поверхностей — отдельная работа.
- Никакие новые actions/POI не выдуманы: сценарии используют только канонические
  фикстуры #307 (локации 1/2, задачи 30701/30702, NPC Фар, шляпа 9001).
