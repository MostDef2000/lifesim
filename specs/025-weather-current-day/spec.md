# 025 — /weather: ensure текущего дня возвращает существующую строку

## Цель

Устранить квик `get_or_create_weather`: при уже существующей строке
запрошенного дня функция возвращает None вместо этой строки, хотя
docstring обещает «Returns the current day's row (or None if disabled)».
На живом мире `GET /weather` из-за этого фактически всегда отвечает
`{"enabled": false}`.

## Проблема (наблюдаемое)

- Движок вызывает `get_or_create_weather` каждый тик
  (`backend/app/simulation/engine.py:68-76`), поэтому на живом мире строка
  текущего дня всегда существует.
- Роут `GET /weather` (`backend/app/api/app.py:1741-1768`) мапит
  `row is None` в `{"enabled": false}` → погода в UI фактически всегда
  выключена.
- Квик зафиксирован в приёмке 024 как документированный RED-файндинг
  (`specs/024-alpha-screen-acceptance/plan.md`, Risks).

## Root cause

`backend/app/simulation/weather.py:136-199`: `start_day = max(day)+1`
(строка 152); при `day <= max_day` цикл `range(start_day, day+1)` пуст
(строки 153-154), `current` остаётся None (строка 199). Контракт функции
(docstring, строки 139-142) — вернуть строку текущего дня.

## Scope

- `backend/app/simulation/weather.py` — только `get_or_create_weather`
  (ранний возврат существующей строки запрошенного дня).
- `tests/m10/test_weather_current_day.py` — новый RED-тестовый файл.
- Это трио.

## Exclusions

- Gap-кейс: запрошенный день < max сохранённого дня, строка этого дня
  отсутствует. Недостижим в нормальной эксплуатации (движок гарантирует
  непрерывность до текущего дня; роут всегда запрашивает текущий день).
  Бэкилл прошлой погоды требует семантики historical-источника — отдельная
  фича.
- Изменения creation-ветки, эмиссии WEATHER_CHANGED, идемпотентности,
  disabled-семантики, роута `/weather/history`, UI.

## Requirements

- R1 (AC a) — повторный `get_or_create_weather` на существующей строке
  текущего дня возвращает эту строку (не None).
- R2 (AC b) — второй `GET /weather` отвечает 200 `enabled:true` с теми же
  day/temperature/description, что и первый.
- R3 (AC c) — инварианты сохраняются:
  - нет дубликатов WeatherState (uq_weather_world_day);
  - нет лишних WEATHER_CHANGED-событий при повторном ensure;
  - disabled → None / нет строк;
  - форма первого ответа не меняется (enabled:true +
    day/temperature/wind/precipitation/cloudiness/visibility/source/
    real_date/description).

## NFR

- Без изменения схемы, миграций и новых зависимостей.
- Тесты быстрые (гейт сьюта `-m "not slow"`), без новых маркеров.
- Детерминизм: synthetic-источник, без сети.

## Acceptance evidence

- RED: `uv run pytest tests/m10/test_weather_current_day.py -v` на базе
  `014a16c` — 2 failed (assertion-файлы, не collection), 1 passed.
- GREEN: та же команда после фикса — 3 passed.
- Полный гейт: `bash scripts/check.sh` (ruff + pytest -m 'not slow').
- Прод-проба: `GET /weather` на lf.mostdef.ru → `enabled:true`.
