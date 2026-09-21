# ВЛ1: Рейнеке

**Проект строго 18+.**

[![CI](https://github.com/MostDef2000/lifesim/actions/workflows/ci.yml/badge.svg)](https://github.com/MostDef2000/lifesim/actions/workflows/ci.yml)

> Браузерный persistent life-sim: небольшое общество автономных персонажей живёт в едином причинно-связанном мире на острове Рейнеке, а реальные игроки могут становиться его жителями, наблюдать за своим персонажем, задавать ему цели или брать прямое управление.

## Статус

Проект находится на стадии **ранней публичной alpha / alpha-play**.

Текущий мир: `reineke_001`.

В рабочий контур уже соединены:

- детерминированная симуляция мира;
- браузерный клиент;
- регистрация и игровые персонажи;
- автономные NPC;
- экономика, инвентарь и задачи;
- отношения, память и диалоги;
- внешний условно симулируемый Владивосток;
- историческая погода;
- пожары, строительство, рынок и преступность;
- локальная LLM через Ollama;
- генерация портретов и сцен через ComfyUI + Flux;
- deploy-контур Caddy + systemd;
- CI и secret scanning.

Главный архитектурный принцип:

> **Simulation Engine определяет факты мира. LLM предлагает намерения и реплики. Flux визуализирует уже зафиксированное состояние.**

LLM и генератор изображений не являются источником истины и не мутируют мир напрямую.

---

## Фактический стек

Это стек, который используется текущим кодом в `main`, а не первоначальная целевая архитектура из ранней спецификации.

| Слой | Сейчас |
|---|---|
| Backend | Python 3.12+ / FastAPI |
| ORM / validation | SQLAlchemy 2 / Pydantic 2 |
| Database | SQLite |
| Simulation | собственный deterministic simulation engine |
| Event history | `world_events` + snapshots |
| AI queue | таблица `ai_requests` в БД |
| LLM transport | Ollama |
| Production LLM | Qwen3 14B |
| Tier-1 model | Qwen3 4B |
| Frontend | vanilla JavaScript SPA, без build-step |
| Realtime | WebSocket + polling fallback |
| Visual generation | Flux через ComfyUI |
| Visual adapter | `deploy/comfy_adapter.py` |
| Reverse proxy / TLS | Caddy |
| Service manager | systemd |
| Tests | pytest |
| Lint | Ruff |
| Package/runtime | uv |

PostgreSQL/PostGIS, Redis и отдельный React/Vite frontend остаются возможным направлением масштабирования, но **сейчас не являются runtime-зависимостями MVP**.

---

## Архитектура

```text
Browser
   │
   ▼
Caddy / HTTPS
   │
   ▼
FastAPI
   │
   ├── Web UI
   ├── REST API
   ├── WebSocket
   │
   ├── Simulation Engine
   │      ├── characters / needs / tasks
   │      ├── economy / inventory
   │      ├── relationships / memory
   │      ├── weather / fire / crime
   │      └── world_events / snapshots
   │
   ├── SQLite
   │
   ├── Ollama transport
   │      └── Qwen3
   │
   └── Visual transport
          └── :7860 ComfyUI adapter
                 └── ComfyUI :8188
                        └── Flux + LoRA
```

Production предполагает, что LLM и GPU-визуализатор могут находиться на другой машине и быть доступны VPS через локальные туннели. Подробности — в `deploy/README.md`.

---

## Production flags

Актуальный `config/production.yaml`:

| Подсистема | Состояние |
|---|---|
| API / web | ON |
| регистрация | ON |
| LLM / Ollama | **ON** |
| visual / Flux | **ON** |
| внешний Владивосток | ON |
| historical weather | **ON** |
| fire | ON |
| construction | ON |
| social autonomous layer | OFF |
| organization simulation | OFF |
| electricity | OFF |
| romance | OFF |
| NPC external utility | OFF |

Некоторые выключенные системы уже реализованы и покрыты тестами, но пока не включены в production-мир.

### LLM

```yaml
llm:
  enabled: true
  transport: ollama
  base_url: http://localhost:11434
  model_tier1: qwen3:4b
  model_tier2: qwen3:14b
  timeout_sec: 120
```

LLM используется для диалогов, памяти и сложных решений. Обычная жизнь NPC не должна зависеть от постоянных LLM-вызовов.

### Visual

```yaml
visual:
  enabled: true
  transport: http
  base_url: http://127.0.0.1:7860
  model: flux
  lora: flux1-uncensored.safetensors
  storage_dir: data/visual_assets
  image_size: "512x512"
```

`HttpFluxTransport` говорит с простым контрактом `POST /generate`.  
`deploy/comfy_adapter.py` преобразует его в workflow ComfyUI.

---

## Что уже умеет игровой мир

### Персонажи

У персонажа хранятся и участвуют в симуляции:

- имя, возраст и пол;
- внешность и биография;
- дом и текущая локация;
- работа;
- traits;
- needs;
- здоровье;
- деньги;
- инвентарь;
- текущие задачи;
- режим управления;
- отношения;
- память.

Для player-character поддерживаются:

- `AUTONOMOUS`;
- `GUIDED`;
- `DIRECT`.

### Needs и повседневная жизнь

Симуляция включает, среди прочего:

- голод;
- жажду;
- энергию;
- социальную потребность;
- сон;
- еду и питьё;
- работу;
- движение между локациями;
- покупки;
- социализацию.

Routine-поведение рассчитывается обычным кодом и utility logic.

### Экономика

Есть:

- персональные и организационные accounts;
- ledger транзакций;
- зарплаты;
- магазин;
- предметы и инвентари;
- player-to-player market;
- строительство с расходом ресурсов.

### Социальный слой

Реализованы:

- отношения по нескольким параметрам;
- social actions;
- memories;
- dialogue journal;
- сообщения;
- organizations/policies;
- explicit interaction permissions;
- одежда и worn-state.

### Преступность

Реализован feature-gated контур:

- theft / vandalism;
- свидетели;
- сообщения о преступлении;
- полиция;
- штрафы;
- арест;
- тюремный срок;
- освобождение.

### Окружающая среда

Есть:

- игровое время;
- synthetic и historical weather;
- историческая погода для координат Рейнеке;
- fire simulation;
- разрушение объектов;
- construction;
- заготовленный electricity simulation.

---

## Владивосток

Владивосток на текущем этапе — **внешний условно симулируемый мир**, а не полноценная карта.

Персонаж может:

- поехать с острова в город;
- пройти лечение;
- купить товары и инструменты;
- потратить деньги через штатный ledger;
- привезти предметы обратно;
- иметь внешние контакты и биографические связи.

Основная задача:

`TRAVEL_EXTERNAL`

Типовой путь:

```text
Рейнеке
  ↓
причал
  ↓
external travel
  ↓
сервис Владивостока
  ↓
лечение / покупки / визит
  ↓
возвращение на остров
```

---

## AI-контур

AI не получает права напрямую записывать произвольные изменения мира.

```text
world state
   ↓
context builder
   ↓
LLM
   ↓
structured result
   ↓
validation
   ↓
existing game mechanics
   ↓
world mutation
```

Основные сущности:

- `ai_requests`;
- AI gateway;
- worker;
- structured response validation;
- per-task budgets;
- dialogue;
- memory capture;
- memory consolidation.

Если LLM недоступна, core simulation должна продолжать работать.

---

## Visual-контур

### Портрет

Из браузера:

```text
POST /visual/portraits/{character_id}
```

После успешной генерации UI назначает новый портрет canonical.

Canonical portrait:

- переживает перезагрузку страницы;
- используется как постоянный visual asset персонажа;
- может участвовать как reference в будущих сценах.

При повторной генерации UI снимает canonical с старого ассета и создаёт новый.

### Сцена

В world view есть on-demand генерация текущей сцены:

```text
POST /visual/scenes
{
  "location_id": ...
}
```

Scene Descriptor строится из committed world state:

- локация;
- персонажи;
- объекты;
- время;
- погода;
- событие;
- canonical references.

После этого Flux визуализирует сцену.

---

## Web UI

Текущий frontend находится в:

```text
backend/app/web/
├── index.html
├── app.js
└── style.css
```

Это SPA без отдельного Node/npm build pipeline.

Основные экраны:

- login / registration;
- создание персонажа;
- world;
- chat;
- inventory;
- profile;
- admin.

World view показывает:

- игровое время;
- текущую погоду;
- локации;
- needs;
- задачи;
- события;
- portrait;
- scene generation;
- действия персонажа;
- поездку во Владивосток.

В чате ближайшие NPC отображаются по имени, а не только по внутреннему `npc_id`.

---

## Локальный запуск

### Требования

- Python 3.12–3.14;
- `uv`.

Установка:

```bash
uv sync --frozen
```

### Headless simulation

```bash
uv run vl1 simulate --days 30 --population 20 --seed 42
```

Симулятор проверяет инварианты и может использоваться без web/LLM/Flux.

### Dev server

В `config/default.yaml` API по умолчанию может быть выключен. Для web-запуска включите нужные feature flags в dev-конфиге и затем:

```bash
uv run vl1 serve
```

По умолчанию сервер слушает локальный FastAPI endpoint, заданный в конфиге.

Production использует отдельный entrypoint:

```text
backend/app/run.py
```

и `config/production.yaml`.

---

## Production deploy

Deploy-файлы:

```text
deploy/
├── .env.example
├── Caddyfile
├── lifesim.service
├── comfy_adapter.py
└── README.md
```

Operational runbook находится в:

**`deploy/README.md`**

Там описаны:

- systemd;
- Caddy;
- TLS/domain;
- environment;
- backups;
- обновление приложения;
- Flux/ComfyUI tunnel;
- Ollama tunnel;
- Qwen3;
- диагностика LLM и visual channels.

Не коммитьте реальные секреты и `.env`.

---

## Проверки

Быстрый quality gate:

```bash
bash scripts/check.sh
```

Он выполняет:

```bash
uv run ruff check .
uv run pytest -m "not slow"
```

Slow acceptance suite:

```bash
uv run pytest -m slow -q
```

GitHub Actions запускает основной CI на push в `main` и на pull request.

Отдельно включён **gitleaks** для secret scanning.

---

## История реализации

| Этап | Реализовано |
|---|---|
| M1 / 001 | simulation core |
| M2 / 002 | social layer |
| M3 / 003 | organization policies |
| M4 / 004 | LLM gateway, worker, memory, dialogue |
| M5 / 005 | users, characters, control modes, API |
| M6 / 006 | Flux assets, portraits, scenes, canonical references |
| M7 / 007 | external Vladivostok |
| M8 / 008 | public-alpha operations/admin |
| M9 / 009 | browser web client |
| M10 / 010 | weather |
| M11 / 011 | fire |
| M12 / 012 | market, construction, destruction |
| M13 / 013 | external follow-ups/messages/supply-demand |
| M14 / 014 | appearance, biography, portrait UX |
| M15 / 015 | deploy kit |
| M16 / 016 | crime/law/police |
| M17 / 017 | electricity |
| M18 / 018 | consent state + clothing |
| M19 | ComfyUI adapter and Flux/Pony routing |

Актуальные change-specs находятся в `specs/`.

---

## Структура репозитория

```text
lifesim/
├── backend/
│   └── app/
│       ├── actions/
│       ├── ai/
│       ├── api/
│       ├── characters/
│       ├── crime/
│       ├── economy/
│       ├── events/
│       ├── external/
│       ├── inventory/
│       ├── market/
│       ├── policies/
│       ├── simulation/
│       ├── social/
│       ├── visual/
│       ├── web/
│       └── world/
├── config/
│   ├── default.yaml
│   └── production.yaml
├── deploy/
├── docs/
├── scripts/
├── specs/
└── tests/
```

---

## Источники истины проекта

В порядке практического приоритета:

1. текущий Git `main` и tracked files;
2. `AGENTS.md`;
3. `.specify/memory/constitution.md`;
4. feature specs в `specs/<feature>/`;
5. `docs/SPEC-V0.1.md` как исходная архитектурная спецификация.

Если старая документация расходится с рабочим кодом, приоритет имеет текущее поведение `main`.

---

## Ключевые правила разработки

- сервер authoritative;
- значимые изменения состояния фиксируются в БД/event log;
- LLM предлагает, Simulation Engine проверяет и применяет;
- visual generation не влияет на игровую истину;
- feature flags не должны ломать deterministic headless baseline;
- реальные секреты не хранятся в Git;
- одна задача — одна свежая ветка и bounded PR;
- перед завершением проходят lint/tests;
- проект и регистрация — 18+.

---

## Документация

- `docs/SPEC-V0.1.md` — исходная техническая спецификация;
- `specs/` — feature specs / plans / tasks;
- `.specify/memory/constitution.md` — базовые архитектурные и quality-правила;
- `deploy/README.md` — production runbook;
- `AGENTS.md` — правила работы с репозиторием.
