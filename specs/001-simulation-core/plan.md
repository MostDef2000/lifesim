# Implementation Plan: M1 — Simulation Core (без AI)

## Current Behavior

Репозиторий на base_sha `def8f6f` (main) — initial scaffold: кода backend нет.
Есть только `docs/SPEC-V0.1.md` (учредительная спека), `specs/README.md`,
`.specify/` (шаблоны + конституция), `README.md` (стек: Python/FastAPI/
SQLAlchemy/Pydantic; таблица milestones, M1 = «без AI; цель — 20 NPC живут
7 игровых дней»). `delivery-pipeline.json` содержит gate `npm run check`,
который для Python-проекта невыполним (см. Risk Profile). Тестов и конфигов нет.

Контрактные источники: SPEC-V0.1 §8–9 (время/тики), §10–16 (персонажи), §18–26
(локации/объекты/ресурсы), §40–45 (actions/tasks/utility), §58–59 (event log),
§73–74 (economy/ledger), §96–99 (NPC generation/bootstrap), §100 (snapshots),
§101 (M1), §115 (структура репо), §116 (config), §117–119 (тесты/headless/seed);
конституция П1–П5.

## Proposed Design

Наименьшее полное ядро: один Python-пакет `backend/app` (modular monolith без
HTTP-слоя), SQLAlchemy 2.0 declarative поверх SQLite-файла (портируемые типы,
переход на PostgreSQL/PostGIS = connection string + dialect-типы), детерминированный
tick-движок на игровых минутах, action system с lifecycle/validators/Utility AI,
ledger-only экономика, append-only event log, seeded worldgen, headless CLI
с инвариантами §118. Модули M2+ (relationships, ai, visuals, auth, admin) не
создаются; пустые пакеты-заглушки отклонены (шум без контракта; границы
зафиксированы здесь и в spec.md Scope).

### Схема данных (таблицы M1)

Все записи состояния несут `world_id` (§7). Время — INTEGER игровых минут от
старта мира; деньги — INTEGER; JSON — TEXT/JSON-портируемый тип. Помеченные (†)
позиции — сознательные отступления/расширения относительно §115/§17/§74 с Reasons
в Correct-Course Check.

1. `worlds` — id TEXT PK ('reineke_001'), seed INTEGER, start_real TEXT, config_sha256 TEXT.
2. `world_clock` (§8) — world_id PK/FK, game_timestamp INTEGER, time_scale REAL,
   is_paused BOOLEAN, last_real_timestamp TEXT.
3. `schema_meta` † — key TEXT PK, value TEXT (версия схемы; замена Alembic в M1).
4. `locations` (§18) — id INTEGER PK, world_id, parent_id FK NULL, type TEXT
   (island|settlement|house|kitchen|shop|workshop|storage|well|pier), name TEXT,
   latitude/longitude REAL NULL, x/y/z NULL, capacity INTEGER NULL,
   access_level TEXT DEFAULT 'public'.
5. `location_links` † (расширение; рёбра движения) — id, world_id, from_location_id,
   to_location_id, travel_minutes INTEGER.
6. `characters` (§10) — id TEXT PK ('npc_0001'…: детерминизм + читаемость),
   world_id, account_id NULL, type TEXT DEFAULT 'npc', first_name, last_name,
   birth_date TEXT, age INTEGER, sex TEXT, gender_identity NULL, alive BOOLEAN,
   location_id FK, home_location_id FK, occupation_id FK NULL,
   created_at/updated_at (игровые минуты), death_game_timestamp NULL, death_cause NULL.
7. `character_profiles` (§11) — character_id PK/FK, biography TEXT (шаблон),
   birthplace, education, former_occupation, reason_for_arrival,
   religion_or_worldview NULL, life_goals NULL (колонка зарезервирована, M2), notes NULL.
8. `character_traits` (§12) — id, character_id FK, trait_key TEXT
   (sociability|discipline|risk_tolerance), value INTEGER (−100..+100).
9. `character_needs` (§13) — character_id PK/FK, hunger/thirst/energy/hygiene/
   comfort/social/safety/entertainment/privacy REAL 0–100, updated_at.
   M1-динамика: hunger, thirst, energy, social; остальные закреплены на 100 †.
10. `character_health` (§14) — character_id PK/FK, health REAL 0–100,
    body_temperature/stress REAL, pain/blood_loss/intoxication REAL (пин к 0 в M1).
11. `organizations` (§17) — id, world_id, name, type (community|business),
    leader_character_id NULL, location_id. Колонка money из §17 не создаётся † —
    балансы только в accounts (конституция П3).
12. `jobs` (§16) — id, organization_id, title, salary INTEGER (за игровой день),
    schedule TEXT (start_hour, end_hour), required_skills NULL (M2+), location_id.
13. `character_jobs` (§16) — id, character_id, job_id, started_at.
14. `accounts` (§73) — id, world_id, owner_type (character|organization|world),
    owner_id, balance INTEGER, created_at; UNIQUE(owner_type, owner_id).
15. `transactions` (§73) — id, world_id, game_timestamp, from_account_id NULL
    (NULL = mint при бутстрапе), to_account_id NULL (NULL = burn, в M1 не возникает),
    amount INTEGER CHECK(amount>0), reason TEXT, event_id FK NULL,
    balance_from_after NULL, balance_to_after NULL.
16. `world_objects` (§21) — id, world_id, object_type (food_bread|food_canned|…),
    subtype NULL, owner_character_id NULL, owner_organization_id NULL,
    location_id, condition INTEGER DEFAULT 100, quantity INTEGER,
    metadata JSON (nutrition для еды).
17. `resource_balances` (§23) † — id, world_id, resource_key ('water'),
    owner_type ('organization'), owner_id, quantity REAL. (M1 — только вода;
    еда — предметы по §24.)
18. `character_tasks` (§43) — id, character_id, priority INTEGER, task_type
    (SLEEP|EAT|DRINK|WORK|MOVE|BUY_ITEM|IDLE), target_id NULL, status
    (planned|active|completed|failed|cancelled|interrupted), source
    (routine|need|event|job), parameters JSON (путь, ends_at, nutrition_plan),
    created_at, started_at NULL, ends_at NULL, completed_at NULL.
    Уникальность active-задачи — на уровне приложения (портируемо; без partial index).
19. `world_events` (§58) — id INTEGER PK autoincrement (монотонный), world_id,
    game_timestamp, event_type (M1-набор из §59 + SALARY_PAID, SUPPLY_ARRIVED,
    PURCHASE, TASK_FAILED), actor_id NULL, target_id NULL, location_id NULL,
    payload JSON.
20. `world_snapshots` (§100) — id, world_id, game_timestamp, created_real TEXT,
    path TEXT (runs/<world>/snapshots/…), notes NULL.

Не создаются в M1 (пометка для M2+): relationships, relationship_events, memories,
character_goals, skills/character_skills, buildings (§20), shops/shop_inventory
(§74; магазин = организация + локация + stock в world_objects) †, users/auth,
laws, crimes, health_conditions, external_*, visual_*, ai_*.

### Схема модулей

```
backend/
  app/
    config/        # YAML → Pydantic-модель настроек (§116); без игровых констант
    db/            # engine/session (SQLite WAL, synchronous=NORMAL),
                   # models/ (таблицы выше), bootstrap create_all + schema_meta
    world/         # локации, location_links, ресурсные балансы, seed-данные мира (§18, §99)
    characters/    # модели, генератор NPC (seeded), needs decay, health/death (§10–14, §36, §96)
    economy/       # ledger.transfer() — единственная запись балансов; jobs; зарплаты; магазин (§73–74, §16)
    inventory/     # world_objects: transfer/consume + события (§21–24)
    events/        # append-only world_events API (§58)
    actions/       # реестр, lifecycle, validators, utility scoring (§40–45)
    simulation/    # clock, TickScheduler (minute/hour/day), engine.step(), CLI (§8–9, §118)
  tests/           # см. Test Design
  migrations/      # НЕ создаётся в M1 (сознательное отступление от §115)
config/default.yaml
pyproject.toml, .python-version, uv.lock
scripts/check.sh   # uv run ruff check . && uv run pytest -m "not slow"
```

Зависимости между модулями (запрет циклов): config ← db ← {world, characters,
economy, inventory, events, actions} ← simulation. events и economy не зависят
друг от друга (economy лишь ссылается на event_id). Ничто из characters/actions
не пишет balances и не пишет в world_events напрямую — только через economy/events API.

### Поток 1: Tick pipeline (R1, R2)

```
engine.step(1 игровая минута):
  1. needs tick: для каждого живого NPC (по возрастанию id):
     decay динамических потребностей по config; градуальное восстановление от
     active-задач (SLEEP→energy); пассивный social в общих локациях/на WORK;
     критические пороги → снижение health; health ≤ 0 → death path (R4).
  2. character tick: для каждого живого NPC:
     active-задача? → PROGRESS; завершение по ends_at → COMPLETE (мутации + EMIT)
     | FAIL (validator на завершении, timeout > max_duration) ;
     иначе planned-очередь → активация; иначе → Utility AI (R5): score валидных
     действий, tie-break по порядку реестра; преусловие локации → enqueue MOVE.
  3. граница часа → economy tick: (резерв под M2-экономику; в M1 — пустой хук).
  4. граница дня → daily tick: зарплаты через ledger (SALARY_PAID); поставка
     еды/воды (SUPPLY_ARRIVED); снапшот SQLite + world_snapshots (R11).
  5. flush/commit по persistence.commit_interval_game_minutes (по умолчанию 60).
```

### Поток 2: Action lifecycle (R5, пример BUY_ITEM)

```
REQUEST (Utility AI: hunger дефицит высок, деньги есть, магазин открыт, еды нет)
→ VALIDATE: персонаж жив/сознателен; локация = shop; баланс ≥ price (проверка без
  мутации); stock продавца > 0
→ RESERVE: активная задача = BUY_ITEM (одна на персонажа), ends_at = now + duration
→ START (status=active)
→ PROGRESS (character tick)
→ COMPLETE (атомарно в одной транзакции БД):
     ledger.transfer(npc_account → shop_account, price, reason='PURCHASE', event_id)
     inventory.transfer(food_item: shop_org → npc, location)
     EMIT: PURCHASE + ITEM_TRANSFERRED
| FAIL (нет денег/stock): событие TASK_FAILED, NPC пере-решает (kitchen-stock / IDLE)
```

### Headless CLI (R10)

`vl1 simulate --days N --population M --seed S [--config P] [--out P]`:
bootstrap мира (§99 без LLM-шага: world → locations/links → organizations+accounts →
resource_balances → jobs → NPCs (traits/profile/housing/job/inventory/mint-деньги) →
start clock) → цикл step() → проверка инвариантов §118 → JSON-отчёт (метаданные
включая config-sha256 и wall-duration; конечное состояние; события по типам;
статус каждого инварианта) → exit 0/2/3.

## Change Map

| Path | Change | Requirement |
|---|---|---|
| `specs/001-simulation-core/{spec,plan,tasks}.md` | new | конституция «Требуемые артефакты» |
| `pyproject.toml`, `.python-version`, `uv.lock` | new | R12, NFR Compatibility |
| `config/default.yaml` | new | R12, §116 |
| `backend/app/config/**` | new | R12 |
| `backend/app/db/**` (engine, models, bootstrap) | new | R6, R9, R11, §7, §8, §10–23, §43, §58, §100 |
| `backend/app/events/**` | new | R8, §58–59 |
| `backend/app/world/**` | new | R9, §18, §99 |
| `backend/app/characters/**` (generator, needs, health) | new | R3, R4, §10–14, §36, §96 |
| `backend/app/economy/**` (ledger, jobs, shop) | new | R6, §16, §73–74 |
| `backend/app/inventory/**` | new | R7, §21–24 |
| `backend/app/actions/**` (registry, lifecycle, validators, utility) | new | R5, §40–45 |
| `backend/app/simulation/**` (clock, scheduler, engine, cli) | new | R1, R2, R10, §8–9, §118–119 |
| `tests/**` (unit + integration, markers `slow`) | new | AE2–AE7, §117–119 |
| `scripts/check.sh` | new | AE8 |
| `README.md` (раздел «Запуск M1 headless») | update | AE1 |
| `backend/migrations/`, `backend/app/{auth,relationships,ai,visuals,admin}/` | not created | Scope Excluded (M2+) |

## Risk Profile

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Дрейф схемы SQLite→PostgreSQL (типы, JSON, транзакции) | средняя | средний | портируемые типы (INTEGER время/деньги, JSON-портируемый), world_id везде, без dialect-DDL; интеграционный тест остаётся на SQLite, PostGIS-переход — отдельная задача с прогоном тех же тестов |
| Плохая калибровка decay/весов → вымирание или деградация потребностей в плато | высокая | средний | все ставки в config; kitchen-stock как страховка от голодной спирали; инвариант смертности + AE7 (конфиг меняет поведение) |
| Потеря детерминизма (неявный порядок, wall-clock, RNG) | средняя | высокий | итерации по отсортированному id; runtime без RNG; tie-break по реестру; AE4 (хэш событий) |
| Производительность: 43 200 минут × 20 NPC медленно из-за БД | средняя | низкий | батч-коммиты (config, по умолчанию раз в игровой час), WAL; NFR ≤ 60 c с запасом |
| NPC «застревают» (взаимные блокировки на capacity, бесконечные задачи) | средняя | высокий | max_duration на каждый тип действия; timeout → FAIL → пере-решение; инвариант «нет застрявших» в отчёте; interrupt только IDLE (узкое правило) |
| Gate `delivery-pipeline.json` = `npm run check` невыполним для Python | высокая (уже факт) | средний | оркестратору заменить gate на `uv run ruff check . && uv run pytest -m "not slow"` (нужен рестарт OpenCode); до замены — фиксировать результаты команд из scripts/check.sh как evidence |
| Python 3.14: отсутствуют wheels (pydantic-core и пр.) | низкая | средний | fallback: `uv python install 3.12`, `.python-version` → 3.12; диапазон `>=3.12,<3.15` уже это допускает |
| Расширение schema в M2 сломает M1-миры | средняя | низкий | schema_meta с версией; снапшоты §100 позволяют воспроизводить старые прогоны; Alembic вводится при первом изменении живой схемы |

## Test Design

- Focused tests (unit, быстрые):
  - `tests/simulation/test_world_clock.py` — продвижение минут/часов/дней, is_paused, границы тиков (§117 world clock).
  - `tests/characters/test_needs_decay.py` — decay по config, clamp [0,100], восстановление от SLEEP/EAT/DRINK, влияние на health (§117 needs decay).
  - `tests/characters/test_death_state.py` — health→0: alive=false, задачи прекращены, имущество на склад с ITEM_TRANSFERRED, CHARACTER_DIED, NPC исключён из тиков (§117 death state, §36).
  - `tests/economy/test_ledger_conservation.py` — transfer-инварианты, amount>0, balance_after, запрет прямых мутаций (консервация после произвольных последовательностей) (§117 economy conservation, П3).
  - `tests/inventory/test_transfer_and_conservation.py` — передача/потребление/поставка, консервация по типам (§117 inventory transfer).
  - `tests/actions/test_validators.py` — по validator на каждое из 7 действий: happy path + каждый отказ (§117 action validation).
  - `tests/actions/test_move_travel.py` — MOVE: маршрут по графу, ends_at = Σ travel_minutes, CHARACTER_MOVED, capacity (§117 travel completion).
  - `tests/events/test_event_generation.py` — каждое значимое мутационное API оставляет событие с корректными полями (§117 event generation, П3).
  - `tests/config/test_config_load.py`, `tests/config/test_config_drives_sim.py` — валидация YAML и AE7.
  - `tests/characters/test_generator.py` — возраст ≥ 18, уникальность имён, seeded-воспроизводимость worldgen.
- Integration tests:
  - `tests/integration/test_headless_7_days_20npc.py` — §101: 20 NPC, 7 дней, 0 ошибок, инварианты pass.
  - `tests/integration/test_headless_30_days.py` — §118, marker `slow`.
  - `tests/integration/test_determinism.py` — AE4, одинаковый/разный seed (§119).
- Regression tests: полный набор из `scripts/check.sh` на финальном head; deterministic-хэш из AE4 включается в отчёт AE1.
- Manual or runtime evidence: артефакт прогона `runs/report_seed42_d30_p20.json` (AE1) прикладывается к результату задачи; при расхождении платформенных float — фиксация платформы в отчёте.

## Correct-Course Check

- SQLAlchemy+SQLite vs in-memory+snapshot: in-memory требует вручную построить
  сериализацию/восстановление/транзакции, которые БД даёт бесплатно, и не держит
  ledger-инвариант П3 так же дёшево; SQLite к тому же сразу закрывает §100/§120.17.
  Выбран SQLAlchemy+SQLite с батч-коммитами.
- Отдельные пакеты только для M1 vs пустые заглушки по §115: заглушки не несут
  контракта, гниют и размывают границу milestone; недостающие модули добавляются
  в M2+ без стоимости миграции, границы закреплены Change Map.
- create_all vs Alembic: схема новая, живых деплоев нет — create_all + schema_meta
  меньше и достаточно; Alembic вводится при первом изменении схемы живого мира
  (триггер зафиксирован в Risk Profile).
- Игро-минутная каденция character tick vs реальные 5–15 с из §9: §9 описывает
  realtime-реактивность возле игроков (M4); в headless M1 игровое время — единственная
  ось, и каденция 1 игровая минута покрывает все инварианты. Отклонение помечено,
  realtime-слой появится с player-режимом.
- organizations без money-колонки (вопреки иллюстрации §17): конституция П3
  требует ledger-only деньги; accounts — единственный владелец балансов.
- Отдельный `location_links` (нет в §115-списке): без рёбер MOVE не определим;
  минимальное расширение, не конфликтующее с будущей географией §19.
