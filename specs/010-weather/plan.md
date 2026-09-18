# План имплементации: 010-weather

## Архитектура
- `app/simulation/weather.py`: WeatherSample (dataclass: поля + source + real_date), generate_weather(day, seed, config) — чистая, sha256-поток; **fetch_historical_weather(real_date, config)** — urllib GET Open-Meteo archive (контракт с monkeypatch, как Flux http-транспорт), кэш-файлы data/weather_cache/; **real_date_for_game_day(day, settings)** — start_real_timestamp + day·1440/time_scale мин → ISO дата −year_lag; get_or_create_weather(session, world_id, day, settings) — synthetic|historical по конфигу, фолбэк synthetic.
- models: WeatherState (38-я таблица, UniqueConstraint world_id+day); schema 0.9.0; EventType.WEATHER_CHANGED (28).
- engine.step: в начале дневной фазы — ensure_weather(session, world_id, day, settings) → запись+событие (если enabled).
- needs-фаза: множитель из get_or_create_weather (t<0 → cold_need_multiplier).
- API: GET /weather, GET /weather/history?days=.
- UI: шапка topbar — /weather текст; Flux descriptor: weather_str параметр (фолбэк «clear»).
- invariants: weather_integrity.

## Чанки
| Chunk | Содержание | Тесты |
|---|---|---|
| A | schema 0.9.0 + weather.py + engine ensure + needs-множитель + инвариант | test_chunk_a: детерминизм, ensure idempotent, события 28, cold-decay, кейстоун off |
| B | API + UI-шапка + Flux descriptor | test_chunk_b: API 401/200/history, дескриптор, grep UI |
| C | README, tasks tick, полный suite | — |

## Исследование источников (сделано)
- **Open-Meteo Historical Weather API** (ERA5): архив с 1940, daily-агрегаты (temperature_2m_mean/max/min, wind_speed_10m_max, precipitation_sum, cloud_cover_mean, wind_direction_10m_dominant), units-параметры (wind_speed_unit=ms), timezone, БЕЗ ключа, fair-use ~10k запросов/день, отставание ERA5 ~5 дней (год назад — всегда доступно). → **выбран для MVP**.
- Альтернативы: MET Norway frost (нужен ключ-аппликация), NOAA ISD (сырые CSV станций, VLK-аэропорт — обработка руками), NASA POWER (грубая сетка 0.5°, слабые осадки на побережье), RIHMI-WDC (RU, неудобный доступ). — отложены.
- Ограничение: в ERA5-архиве нет поля visibility → дериват из осадков/облачности (зафиксировано в spec).
- Сеть песочницы блокирует open-meteo (политика доменов) — живой smoke сделает владелец на VPS; тесты — monkeypatch-контракт.

## Риски
- step() фазы: ensure вызывается ОДИН раз за день (в дневной фазе); headless-пины: события станут 28 (WEATHER_CHANGED) — структурные тесты m2/m4 обновляются честно; m1-m4 keystone пины сверить (если пинят полный events_by_type — обновить с weather-типом).
- time_scale в headless-тестах: clock.step продвигает game-минуты напрямую; real_date-маппинг используется ТОЛЬКО в historical-источнике (synthetic его не требует) → headless без сети.
- Rate limit Open-Meteo: 1 fetch на игровой день максимум, кэш-файлы; батч-доfetch при пропуске дней (start_date..end_date одним запросом).
