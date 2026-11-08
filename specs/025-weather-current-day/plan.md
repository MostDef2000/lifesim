# 025 — Plan

## Текущее поведение (evidence)

- `get_or_create_weather` (`backend/app/simulation/weather.py:136-199`):
  `last = max(day)` (147-151), `start_day = last+1` (152), цикл
  `range(start_day, day+1)` (154). Когда строка запрошенного дня уже
  существует, цикл пуст → `current` остаётся None (153, 199).
- Движок ensure'ит погоду каждый тик (`engine.py:68-76`) → на живом мире
  строка текущего дня всегда существует → `GET /weather`
  (`app.py:1741-1768`) отвечает `{"enabled": false}`.
- RED воспроизведён: `uv run pytest tests/m10/test_weather_current_day.py
  -v` на базе `014a16c` — `assert None is not None` (unit) и
  `assert False is True` (роут, второй GET).

## Fix approach

В `get_or_create_weather` сразу после проверки
`if not settings.weather.enabled: return None` (weather.py:143-144) —
ранний возврат существующей строки запрошенного дня:

```python
existing = (
    session.query(WeatherState)
    .filter_by(world_id=world_id, day=day)
    .first()
)
if existing is not None:
    return existing
```

- Индексированный запрос (uq_weather_world_day по (world_id, day)).
- До `max()`-запроса и цикла: creation-ветка, эмиссия WEATHER_CHANGED,
  идемпотентность и disabled-семантика не трогаются.
- Gap-кейс (day < max_day, строки нет) не обрабатывается — out of scope
  (spec.md, Exclusions).

## RED-first flow

1. RED: `tests/m10/test_weather_current_day.py` на базе — 2 failed /
   1 passed (зафиксировано в tasks.md T1).
2. Fix: только `backend/app/simulation/weather.py`.
3. GREEN: тот же файл — 3 passed.
4. Полный гейт: `bash scripts/check.sh`.

## Affected paths

- `backend/app/simulation/weather.py` — фикс (единственный файл продукта).
- `tests/m10/test_weather_current_day.py` — новый тестовый файл.
- `specs/025-weather-current-day/{spec,plan,tasks}.md` — это трио.

## Verification commands

- `uv run pytest tests/m10/test_weather_current_day.py -v`
- `uv run ruff check tests/m10/test_weather_current_day.py`
- `bash scripts/check.sh` (ruff + pytest -m 'not slow')

## Risks

- Ранний возврат добавляет один индексированный запрос на каждый ensure
  (каждый тик) — пренебрежимо (uq_weather_world_day).
- Gap-кейс остаётся сломанным по дизайну (задокументирован, недостижим в
  нормальной эксплуатации).
- Роут коммитит после ensure (`app.py:1754`) — ранний возврат не меняет
  транзакционную семантику.

## Test design

- Unit: повторный ensure дня 2 → та же строка, число строк WeatherState и
  число WEATHER_CHANGED не меняется; disabled → None / нет строк.
- API: первый GET /weather → 200 enabled:true (полная форма); второй GET →
  200 enabled:true, те же day/temperature/description.
- Самодостаточный файл (хелперы продублированы из test_chunk_a/b), без
  новых маркеров, быстрый.

## Rejected alternatives

- Фикс в роуте (app.py: мапить None → повторный запрос строки) — прячет
  нарушение контракта, оставляет возвращаемое значение сломанным для
  других вызывающих.
- Изменить цикл на `range(start_day, day+1) or [day]` — менее читаемо, не
  выражает намерение.
- Бэкилл gap-кейса — отдельная фича (семантика historical-источника).
