# Задачи: 010-weather

## Chunk A — Ядро
- [ ] T1: WeatherState модель, schema 0.9.0, WEATHER_CHANGED (28), структурные пины (m2/m4 28, tests/db 38).
- [ ] T2: weather.py (generate_weather/describe_weather + fetch_historical_weather + real_date_for_game_day + кэш), конфиг WeatherConfig (source/lat/lon/year_lag/cache_dir) + default.yaml.
- [ ] T3: engine ensure_weather + needs-множитель; инвариант weather_integrity.
- [ ] T4: tests/m10/test_chunk_a.py.

## Chunk B — API/UI/Flux
- [ ] T5: GET /weather + /weather/history.
- [ ] T6: UI-шапка погоды; Flux descriptor weather.
- [ ] T7: tests/m10/test_chunk_b.py (включая historical: monkeypatch urlopen — маппинг ms/%, year-lag дата, офлайн-фолбэк, кэш-файл).

## Chunk C — Финал
- [ ] T8: README, tasks tick, ruff + check.sh + полный suite, PR.
