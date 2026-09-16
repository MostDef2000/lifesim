# ВЛ1: Рейнеке

**Проект строго 18+.**

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
- `.specify/memory/constitution.md` — конституция проекта
