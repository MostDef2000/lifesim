# 025 — Tasks

### T1. RED-тесты (test-воркер)

- [x] `tests/m10/test_weather_current_day.py` создан (самодостаточный,
  хелперы продублированы из test_chunk_a/b).
- [x] RED на базе `014a16c`: `uv run pytest
  tests/m10/test_weather_current_day.py -v` → 2 failed / 1 passed:
  - `TestRepeatedEnsure::test_repeated_ensure_returns_existing_row` —
    `assert None is not None` (строка 88);
  - `TestWeatherRoute::test_second_get_stays_enabled_with_same_values` —
    `assert False is True` (строка 154, второй GET → `{"enabled": false}`);
  - `TestRepeatedEnsure::test_disabled_returns_none_and_no_rows` — passed
    (инвариант, зелёный на базе).
- [x] `uv run ruff check tests/m10/test_weather_current_day.py` — чисто.
- Доказательство: pytest-вывод в отчёте work order.

### T2. Фикс (backend-воркер)

- [x] `backend/app/simulation/weather.py`: ранний возврат существующей
  строки запрошенного дня после disabled-проверки (см. plan.md).
- Доказательство: 7-строчный ранний возврат в weather.py по plan.md; diff — только этот файл.

### T3. GREEN

- [x] `uv run pytest tests/m10/test_weather_current_day.py -v` → 3 passed.
- [x] `bash scripts/check.sh` зелёный (ruff + pytest -m 'not slow').
- Доказательство: focused 3 passed; полный гейт `bash scripts/check.sh` → ruff чисто, 1241 passed / 13 deselected.

### T4. PR → merge

- [ ] PR с `fix/weather-current-day-ensure`; зелёный CI; merge после
  зелёного CI (в рамках одобренного скоупа).

### T5. Lane-A deploy + прод-проба

- [ ] DEPLOY-TRIGGER (repo, полный 40-hex SHA из origin/main,
  acceptance-пробы, откат) → dispatch деплой-воркфлоу.
- [ ] Прод-проба: `GET /weather` на lf.mostdef.ru → 200 `enabled:true`
  (day/temperature/description на месте).

### T6. Close-out

- [ ] Итоговая запись в issue; запись в JOURNAL; трио синхронизировано с
  принятой реализацией.
- Доказательство: merge-коммит в main, вывод прод-пробы.
