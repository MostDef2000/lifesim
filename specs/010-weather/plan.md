# План имплементации: 010-weather

## Архитектура
- `app/simulation/weather.py`: WeatherSample (dataclass), generate_weather(day, seed, config) — чистая, sha256-поток; describe_weather(sample) → строка для UI/Flux.
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

## Риски
- step() фазы: убедиться, что ensure вызывается ОДИН раз за день (в дневной фазе), не ломает headless-пины (enabled default? — да, true, но кейстоун-миры не доходят до новых дней? — ДОХОДЯТ: 7 дней! Значит записи появятся → отчёты изменятся? Проверить m1-m4 пины: если пинят events_by_type — WEATHER_CHANGED добавит тип → закрытый set 28 в m2/m4-тестах (closed_set_27 → 28). Обновить структурные пины. Отчёт: событий станет больше — m1-m4 keystone-тесты пинят конкретные типы, не полное равенство? Проверить и обновить честно.)
