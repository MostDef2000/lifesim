# План реализации: M2 — Социальный слой (Social Layer)

## Этапы реализации

### Фаза 1: Схема и базовые данные (Schema & Seed)
1. **Обновление схемы**: 
   - Создание таблицы `relationships` (8 измерений, constraints `character_a < character_b`, UNIQUE).
   - Создание таблицы `relationship_events`.
   - Создание таблицы `organization_members`.
   - Обновление `schema_meta` с 0.1.0 до 0.2.0.
   - Стресс: используется СУЩЕСТВУЮЩАЯ колонка `character_health.stress`
     (`models.py:135`, pinned 0 в M1) — новая колонка НЕ добавляется.
   - *Точки интеграции*: `models.py:141-168` (по аналогии с neighborhood).
2. **Сидирование организаций**:
   - Реализация логики назначения лидеров организаций (lowest-id по `leader_titles`).
   - Наполнение `organization_members` при создании мира.
   - *Точки интеграции*: `seed_world.py:101-130`.
3. **Сидирование конфликтов**:
   - Генерация 2 случайных пар с `affection = -50` через seeded RNG.
   - *Точки интеграции*: `seed_world.py` (после создания персонажей).

### Фаза 2: Конфигурация и События (Config & Events)
1. **Конфигурация**:
   - Добавление секции `social:` в `default.yaml`.
   - Обновление `Settings` в `config.py` для поддержки новых параметров.
   - Обновление `actions.SOCIALIZE` и `needs.recovery_rates` в конфиге.
2. **Расширение контракта событий**:
   - Добавление `SOCIAL_INTERACTION`, `RELATIONSHIP_CHANGED`, `CONFLICT` в Enum `EventType`.
   - Реализация payload-структур для новых событий.
   - *Точки интеграции*: `events.py:10-20`.

### Фаза 3: Социальное действие и Utility AI (Social Action & Utility)
1. **Реализация SOCIALIZE**:
   - Создание валидатора: проверка `social < threshold`, поиск цели (сортировка по affection, порог отказа −60 — фикс достижимости CONFLICT по адверсариальному ревью, см. spec Q6).
   - Реализация логики MOVE в social hub, если цель не со-лоцирована.
   - *Точки интеграции*: `validators.py:36-81` (по паттерну EAT→kitchen).
2. **Интеграция в Utility AI**:
   - Обновление формулы score для учета социальной потребности.
   - Регистрация действия в реестре.
   - *Точки интеграции*: `utility.py:47-69`, `registry.py:32-34`.

### Фаза 4: Жизненный цикл и Динамика (Lifecycle & Dynamics)
1. **Обработка завершения задачи**:
   - Создание хука в `complete_task` для обработки эффектов `SOCIALIZE`.
   - Логика изменения `affection`: проверка по значению НА МОМЕНТ НАЧАЛА взаимодействия (affection_at_start < −30 → конфликт: −5.0 + стресс +5 обоим участникам; иначе +2.0).
   - Триггер события `RELATIONSHIP_CHANGED` при пересечении границ bands.
   - *Точки интеграции*: `lifecycle.py` (семантика catch-up).
2. **Восстановление потребности**:
   - Реализация восстановления `social` во время выполнения действия.

### Фаза 5: Инварианты, Отчёты и CLI (Invariants, Reports & CLI)
1. **Новые инварианты**:
   - Реализация `relationship_range`, `social_event_integrity`, `relationship_event_link`, `org_membership_integrity`.
   - Расширение `no_impossible_states` для проверки `stress`.
   - *Точки интеграции*: `invariants.py:245-248`.
2. **Отчётность и CLI**:
   - Добавление списка организаций и статистики по членам в финальный JSON-отчёт.
   - Реализация флага `--social` в `cli.py` для переопределения `social.enabled`.

## Матрица регрессии и тестирования

| Этап | Обязательные тесты M1 (AE5) | Новые проверки |
|---|---|---|
| Фаза 1 | Все (проверка совместимости схемы) | `test_schema_migration`, `test_org_seed` |
| Фаза 2 | Все | `test_event_payload_validation` |
| Фаза 3 | Все (при `social.enabled=false`) | `test_socialize_target_selection`, `test_utility_score` |
| Фаза 4 | Все (при `social.enabled=false`) | `test_affection_delta`, `test_conflict_trigger`, `test_band_crossing` |
| Фаза 5 | Все | `test_social_invariants`, `AE1` (full run), `AE4` (determinism) |

## Риски и смягчение
- **Performance**: Если прогон > 45 с → внедрение batch needs-fetch (триггер R10).
- **Determinism**: Использование строгого порядка сортировки NPC по id во всех циклах.
- **Regression**: Постоянный запуск M1-тестов с дефолтным конфигом на каждом этапе.
