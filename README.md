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
