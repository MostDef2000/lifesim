# ВЛ1: Рейнеке

**Проект строго 18+.**

[![CI](https://github.com/MostDef2000/lifesim/actions/workflows/ci.yml/badge.svg)](https://github.com/MostDef2000/lifesim/actions/workflows/ci.yml)

> браузерный persistent life-sim, в котором небольшое общество автономных ИИ-персонажей живёт в едином причинно-связанном мире, а реальные игроки могут становиться его жителями, наблюдать за жизнью своего персонажа или полностью управлять им.

## Стек

- **Backend**: Python / FastAPI (SQLAlchemy, Pydantic, WebSocket)
- **Database**: PostgreSQL + PostGIS
- **Cache / Queue**: Redis
- **Frontend**: TypeScript / React / Vite
- **LLM**: локальный LLM worker (RTX 4070 Ti) — только для сложных решений и диалогов
- **Flux + LoRA**: визуализация персонажей, объектов и сцен (отдельный сервер, внутренний API)
- **Архитектура**: server authoritative — сервер источник истины, LLM не мутирует мир напрямую

## Milestones

| Этап | Содержимое |
|---|---|
| M1 — Simulation Core | world clock, characters, locations, needs, tasks, movement, sleep, food, jobs, inventory, events (без AI; цель — 20 NPC живут 7 игровых дней) |
| M2 — Social Layer | relationships, social actions, organizations, basic conflicts |
| M3 — LLM | AI Gateway, AI Queue, AI Worker, structured decisions, dialogue, memory |
| M4 — Player | registration, character creation, guided mode, direct control, chat |
| M5 — Flux | character portraits, scene generation, asset storage, canonical visual references |
| M6 — External Vladivostok | travel, external shops, hospital, contacts, resource acquisition, biographical connections |
| M7 — Public Alpha | domain, HTTPS, free registration, admin tools, moderation, backups, monitoring, rate limits |

## Документация

- `docs/SPEC-V0.1.md` — полная техническая спецификация (v0.1)
- `specs/` — активные изменения (spec / plan / tasks)
33: - `.specify/memory/constitution.md` — конституция проекта
34: 
35: ## Запуск M1 headless
36: 
37: Для запуска симуляции без визуального интерфейса используйте команду:
38: `uv run vl1 simulate --days 30 --population 20 --seed 42`
39: 
40: Команда генерирует JSON-отчёт с результатами прогона и проверкой инвариантов.
41: Коды выхода: 0 — успешно, 2 — нарушение инвариантов, 3 — системная ошибка.


## M5: Веб-API и игроки (005-player)

Headless по умолчанию. Для запуска API:

1. Прогнать мир: `vl1 simulate --days 1 --population 20 --seed 42` (создаёт `data/lifesim.db`).
2. Включить API в `config/default.yaml`: `api.enabled: true`.
3. Запустить сервер: `vl1 serve` (по умолчанию http://127.0.0.1:8000).

Примеры:

```bash
curl -s -c jar -X POST localhost:8000/auth/register -H 'content-type: application/json' \
  -d '{"username":"alice","email":"a@x.com","password":"password123","age_confirmed":true}'
curl -s -c jar -X POST localhost:8000/auth/login -H 'content-type: application/json' \
  -d '{"username":"alice","password":"password123"}'
curl -s -b jar -X POST localhost:8000/characters -H 'content-type: application/json' \
  -d '{"name":"Alice Doe","sex":"F","age":30}'
curl -s -b jar -X POST localhost:8000/characters/plr_0001/control -H 'content-type: application/json' \
  -d '{"mode":"DIRECT"}'
curl -s -b jar -X POST localhost:8000/actions -H 'content-type: application/json' \
  -d '{"character_id":"plr_0001","action_type":"WORK"}'
curl -s -b jar -X POST localhost:8000/characters/plr_0001/goals -H 'content-type: application/json' \
  -d '{"text":"Купи инструменты"}'
curl -s -b jar -X POST localhost:8000/dialogue/start -H 'content-type: application/json' \
  -d '{"npc_id":"npc_0001"}'
```

WebSocket: `ws://127.0.0.1:8000/ws?token=<JWT из cookie vl1_session>` — поток world_events.
Секрет сессий: переменная окружения `VL1_SECRET` (в dev используется небезопасный дефолт).
Диалоги NPC: при `llm.enabled: false` — детерминированные fallback-ответы; с реальной моделью — через Ollama (см. specs/004-llm/plan.md).

## M6: Визуальная генерация Flux (006-flux)

Мир → визуальный слой: портреты персонажей, сцены локаций, реестр `visual_assets`, канонические референсы (§66-70). По умолчанию выключен (`visual.enabled: false`) — headless-мир не меняется.

1. Включить визуал: `visual.enabled: true` в `config/default.yaml`.
2. Транспорт: `visual.transport: stub` (детерминированные PNG-плейсхолдеры, CI) или `http` (реальный Flux-сервер: `base_url`, `model`, `lora`).
3. Запустить: `vl1 serve`.

Примеры:

```bash
# портрет своего персонажа (201; повтор без канона — новая генерация)
curl -s -b jar -X POST localhost:8000/visual/portraits/plr_0001
# назначить каноническим лицом (последующие генерации используют как reference §69-70)
curl -s -b jar -X POST localhost:8000/visual/assets/1/canonical -H 'content-type: application/json' -d '{"canonical":true}'
# повтор -> 200 {"reused": true} без вызова Flux
curl -s -b jar -X POST localhost:8000/visual/portraits/plr_0001
# сцена локации (канонические портреты персонажей сцены попадают в references)
curl -s -b jar -X POST localhost:8000/visual/scenes -H 'content-type: application/json' -d '{"location_id":1}'
# файл ассета
curl -s -b jar localhost:8000/visual/assets/1/file -o asset.png
```

Границы MVP: только on-demand генерация по API (без автогенерации по событиям), локальное хранилище `data/visual_assets/`, weather в сцене — стаб (`clear`), реальный Flux не делает игровых решений (§15).

## M7: Внешний Владивосток (007-external)

Владивосток — внешний условно симулируемый мир (§38): `external_locations`/`external_services`/`external_contacts`, без физической симуляции. Поездка — штатная задача `TRAVEL_EXTERNAL` (§39): порт → сервис → возврат. NPC-utility выключен (`external.npc_utility: false`) — headless-мир M1-M4 байт-идентичен.

```bash
# каталог внешних сервисов (больница/супермаркет/хозяйственный)
curl -s -b jar localhost:8000/external
# биографические контакты персонажа (владелец)
curl -s -b jar localhost:8000/characters/plr_0001/contacts
# поездка за лечением (сначала MOVE к пирсу, если не там — валидатор вернёт needs_move)
curl -s -b jar -X POST localhost:8000/actions -H 'content-type: application/json' \
  -d '{"character_id":"plr_0001","action_type":"TRAVEL_EXTERNAL","params":{"service_id":1,"purpose":"heal"}}'
# поездка за покупками (корзина через items, приходит инвентарём на возврате)
curl -s -b jar -X POST localhost:8000/actions -H 'content-type: application/json' \
  -d '{"character_id":"plr_0001","action_type":"TRAVEL_EXTERNAL","params":{"service_id":2,"purpose":"shop","items":{"food_groceries":2}}}'
```

Экономика поездки: транзит + покупки — через ledger (`EXTERNAL_TRAVEL`/`EXTERNAL_PURCHASE`), предметы — стандартный inventory-контур (П1). Каталог и цены — в `config/default.yaml` (`external:`). Инварианты: `external_integrity`, object_conservation учитывает внешний приход.

## M8: Public Alpha — админ-инструменты и эксплуатация (008-alpha)

Административный контур (§83-86): роли §82 (user|moderator|admin|developer), audit log `admin_audit_log` (schema 0.8.0, 37 таблиц), rate limits §27, бэкапы §87, request-id логирование §88.

```bash
# админ-действия (роль admin; moderator — только чтение overview/audit)
curl -s -b jar localhost:8000/admin/overview
curl -s -b jar -X POST localhost:8000/admin/world/pause
curl -s -b jar -X POST localhost:8000/admin/world/timescale -H 'content-type: application/json' -d '{"time_scale": 2.0}'
curl -s -b jar -X POST localhost:8000/admin/characters/plr_0001/teleport -H 'content-type: application/json' -d '{"location_id": 3}'
curl -s -b jar -X POST localhost:8000/admin/tasks/42/cancel
curl -s -b jar -X POST localhost:8000/admin/users/5/disable   # бан: логин 403, токены 403, задачи отменены
curl -s -b jar "localhost:8000/admin/audit?limit=100"

# бэкап (консистентный снапшот sqlite + visual assets, retention)
uv run python -m app.simulation.cli backup --backup-dir backups --keep 7
# cron: 0 4 * * * cd /srv/lifesim && uv run python -m app.simulation.cli backup
```

Эксплуатация:
- **Rate limits** (§27): per-IP sliding window — auth 10/мин, API 120/мин (429 + Retry-After); настройка в `admin:` конфига. In-memory (per-process); Redis — на этапе PostgreSQL.
- **Регистрация** (§85): `admin.registration_enabled` / `admin.max_players` (0 = без лимита).
- **Миграция на 0.8.0** для существующей БД: `ALTER TABLE users ADD COLUMN disabled BOOLEAN NOT NULL DEFAULT 0; CREATE TABLE admin_audit_log (...)` (см. models.py).
- **Domain/HTTPS** — на стороне VPS (§26): reverse proxy (Caddy/nginx) с TLS перед uvicorn.

## M9: Веб-клиент (009-web, §77-78)

Браузерный MVP без сборки: vanilla SPA (`backend/app/web/`), раздаётся FastAPI (`/` и `/static/*`). Экраны §77: login/register (18+), world (§78: время, needs-бары, карта с MOVE, действия, задачи, лента событий), chat (диалоги с NPC на локации), inventory, profile (control mode), admin (overview/pause/timescale/audit — по роли §82). События — live через WS (`/ws?token=` из `/auth/me`), при недоступности — polling-фолбэк.

```bash
uv run python -m app.simulation.cli serve --db data/world.db
# открыть http://127.0.0.1:8000/ → регистрация → создание персонажа → игра
```

Ручной смоук-чеклист: регистрация → персонаж создан → needs-бары живые → клик по локации = MOVE → кнопка WORK → событие в ленте → чат с NPC (3 подсказки) → инвентарь пуст → админ-вкладка скрыта для user-роли, видна admin'у → TRAVEL_EXTERNAL из мира (порт обязателен).

## M10: Погода (010-weather, §71)

Мир имеет погоду: на каждый игровой день — запись `weather_state` (temperature/wind/precipitation/cloudiness/visibility + `source`/`real_date`), событие `WEATHER_CHANGED`, инвариант `weather_integrity`, API `GET /weather` и `/weather/history?days=N` (auth), погода в шапке UI и в Flux-дескрипторе сцены. Холодные дни (t<0) ускоряют голод/энергию ×1.15.

Два источника (`weather.source` в конфиге):
- **synthetic** (default) — детерминированная генерация по seed+день (§71 «искусственная генерация»), headless-тесты не зависят от сети.
- **historical** — **реальная погода острова Рейнеке (42.98°N, 132.55°E) год назад** за календарный день, соответствующий игровому: день N → реальная дата `start_real_timestamp + N·1440/time_scale минут` − 1 год. Данные — Open-Meteo Historical Weather API (ERA5-архив, бесплатно, без ключа, tz Asia/Vladivostok). Кэш-ответы в `data/weather_cache/`; офлайн → фолбэк на synthetic; `real_date` в ответе API показывает, за какой день взята реальная погода.

```bash
# включить реальную погоду Рейнеке (на сервере):
weather: { source: historical }   # в config/default.yaml или env
```
