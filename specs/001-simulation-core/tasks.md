# Tasks: M1 — Simulation Core (без AI)

## Implementation

- [x] T1: Скэффолд проекта: `pyproject.toml` (requires-python `>=3.12,<3.15`,
      deps: sqlalchemy>=2.0, pydantic>=2.7, pyyaml>=6; dev: pytest>=8, ruff),
      `.python-version` (3.14; при недостающих wheels — fallback 3.12 через
      `uv python install 3.12`), `uv.lock`, `scripts/check.sh`, каркас пакетов
      `backend/app/{config,db,world,characters,economy,inventory,events,actions,simulation}`
      (без заглушек-пустышек), pytest-маркеры (`slow`), ruff-конфиг. (R12; §115, §116)
      Готовность: `uv sync` собирает окружение; `uv run pytest -m "not slow"` зелёный на 0 тестах; ruff чист.
- [x] T2: Config + DB-слой: Pydantic-модель настроек с валидацией (world, ticks,
      needs, utility, actions, economy, movement, population, persistence,
      invariants), загрузка `config/default.yaml`; SQLAlchemy engine (SQLite WAL),
      модели таблиц 1–5 (worlds, world_clock, schema_meta, locations,
      location_links), bootstrap create_all. (R12, R9-частично; §116, §18)
      Готовность: тест `tests/config/test_config_load.py` (валидные/невалидные конфиги); бутстрап создаёт файл БД со схемой.
- [x] T3: Clock + scheduler + event log: игровые минуты как INTEGER, границы
      minute/hour/day, is_paused; append-only API `events.log`; `engine.step(1)`
      с детерминированным порядком вызовов. (R1, R2, R8; §8–9, §58)
      Готовность: `tests/simulation/test_world_clock.py`, `tests/events/test_event_generation.py` зелёные (§117 world clock, event generation).
- [x] T4: Economy ledger: accounts, transactions, `ledger.transfer()` как
      единственная точка записи балансов (amount>0, balance_after, event_id),
      mint при бутстрапе; organizations, jobs, character_jobs. (R6; §73, §16–17, П3)
      Готовность: `tests/economy/test_ledger_conservation.py` — консервация Σ(balances)=Σ(mint)+Σ(дельт), отказ при amount≤0 и недостатке средств (§117 economy conservation).
- [x] T5: Персонажи: models (characters, character_profiles, character_traits,
      character_needs, character_health), детерминированный генератор NPC
      (seeded RNG, имена из config-списков, пол, возраст 18+ с серверной
      проверкой, traits −100..+100, templated-профиль, жильё по capacity,
      профессия), needs decay + пассивные восстановления, health-decay от
      критических потребностей, death path (alive=false, освобождение задач,
      имущество на склад общины, CHARACTER_DIED). (R3, R4; §10–14, §36, §96, П4)
      Готовность: `tests/characters/test_needs_decay.py`, `tests/characters/test_death_state.py`, `tests/characters/test_generator.py` зелёные (§117 needs decay, death state).
- [x] T6: Inventory/resources: world_objects (transfer/consume с событиями),
      resource_balances (вода), еда с nutrition в metadata, стартовые
      инвентари/stock магазина/вода поселения, daily-поставка SUPPLY_ARRIVED.
      (R7; §21–25, §74)
      Готовность: `tests/inventory/test_transfer_and_conservation.py` — консервация предметов и воды при передачах/потреблении/поставках (§117 inventory transfer).
- [x] T7: Action system: реестр 7 действий (SLEEP, EAT, DRINK, WORK, MOVE,
      BUY_ITEM, IDLE), lifecycle REQUEST→VALIDATE→RESERVE→START→PROGRESS→
      COMPLETE→EMIT / FAIL-CANCEL-INTERRUPT, validators по каждому действию,
      tasks-очередь (одна active на персонажа), Utility AI (score из config-весов,
      tie-break по порядку реестра, interrupt только IDLE при критическом дефиците),
      тайм-ауты max_duration → FAIL. (R5; §40–45)
      Готовность: `tests/actions/test_validators.py` — happy path + все отказы для каждого действия (§117 action validation).
- [x] T8: Локации и движение: seed-данные мира (иерархия §18: остров → поселение →
      дома/кухня/магазин/мастерская/склад/колодец/причал), граф location_links с
      travel_minutes из config, BFS-маршрут, MOVE-задача с прибытием по времени и
      CHARACTER_MOVED, проверка capacity. (R9, R5-движение; §18, §99)
      Готовность: `tests/actions/test_move_travel.py` — прибытие точно в Σ travel_minutes, отказ при отсутствии пути/переполненной локации (§117 travel completion).
- [x] T9: Daily/hourly handlers + снапшоты: зарплаты через ledger (SALARY_PAID),
      поставка еды/воды (SUPPLY_ARRIVED), дневной SQLite-снапшот + world_snapshots,
      commit-интервал из config. (R6, R11; §73, §100, §116)
      Готовность: unit-тест daily-обработчиков (зарплата ровно раз в день; повторный вызов идемпотентен); снапшот-файл создаётся и открывается.
- [x] T10: Headless CLI + инварианты + отчёт: `vl1 simulate --days N --population M
      --seed S [--config P] [--out P]`; bootstrap по §99 (без LLM-шага); инварианты
      §118 (отрицательные балансы, консервация предметов/воды, застрявшие задачи,
      смертность+причины, невозможные состояния); JSON-отчёт (config-sha256,
      wall-duration, конечное состояние, события по типам, статус инвариантов);
      exit-коды 0/2/3. (R10; §118–119, §99)
      Готовность: ручной прогон AE1 `uv run vl1 simulate --days 30 --population 20 --seed 42` даёт exit 0 и pass по всем инвариантам.

## Verification

- [x] T11: Прогнать фокусные и интеграционные тесты, приложить результаты:
      `tests/integration/test_headless_7_days_20npc.py` (AE2),
      `tests/integration/test_headless_30_days.py` + `test_determinism.py`
      (AE3, AE4; marker slow), полный unit-набор AE5–AE7. (AE2–AE7)
- [x] T12: Прогнать обязательные quality gates на финальном head: `scripts/check.sh`
      (`uv run ruff check . && uv run pytest -m "not slow"`) и полный pytest с slow;
      зафиксировать выводы в артефактах; эскалировать починку gate в
      `delivery-pipeline.json` оркестратору (сейчас `npm run check`). (AE8)
- [x] T13: Независимое ревью от другой модели семьи (правило AGENTS.md:25),
      закрыть замечания; сверить финальный diff с approved scope и трассировкой
      R/AE в spec.md. (DoD)

## Definition of Done

- [ ] Approved scope matches the final diff.
- [ ] Requirements trace to implementation and tests. (R1–R12 → задачи T1–T10 → тесты AE2–AE7)
- [ ] Required quality gates pass on the final head. (AE8)
- [ ] No unresolved material review findings remain. (T13)
- [ ] Documentation and operational evidence are updated where required.
      (README-раздел запуска M1; отчёт AE1 в evidence прогона)
