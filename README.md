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

