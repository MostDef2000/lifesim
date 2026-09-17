# Список задач: M3 — Политики организаций (Organization Policies)

## Схема, конфигурация, события (Фаза A)
- [ ] T1: Создать таблицы `org_laws` (UNIQUE(world_id, organization_id)) и `org_law_violations` (UNIQUE(world_id, organization_id, law_key, character_id, game_day)) в `models.py`; обновить `schema_meta` до 0.3.0. Готовность: Схема создаётся через `create_all`; обе таблицы с констрейнтами.
- [ ] T2: Добавить `OrgConfig` в `config.py` и секцию `org:` в `default.yaml` (enabled: false; все параметры spec R7 с дефолтами). Готовность: Конфиг без секции `org:` загружается с дефолтами; существующий minimal-settings тест проходит.
- [ ] T3: Реализовать флаг `--org` в `cli.py` по паттерну `--social`. Готовность: `--org` включает гейт (совместно с `--social`); без `--social` — неактивен.
- [ ] T4: Расширить `EventType` шестью типами (ELECTION, ORG_DUES, ORG_FEAST, LAW_ENACTED, LAW_VIOLATION, RECONCILIATION) с payload по spec R5. Готовность: Enum 13 → 19; структурный тест обновлён сознательно.
- [ ] T4b: Day-0 хук издания закона после `seed_social` в worldgen-флоу (cli.py:64-75), за гейтом R1, идемпотентный. Готовность: При worldgen org-on в `org_laws` есть активный закон community с enacted_day=0; при org-off — ноль мутаций.

## Механика (Фаза B)
- [ ] T5: Реализовать daily org-хендлер с гейтом R1 (выход до любых запросов) и фиксированным порядком: dues → выборы → издание закона → night_home → примирение → праздник. Готовность: Хендлер вызывается после SALARY/SUPPLY; при org-off — ноль запросов и мутаций.
- [ ] T6: Взносы: transfer `dues_per_day` по живым членам (id-ascending), silent-skip при нехватке, агрегатный `ORG_DUES` event на (org, day). Готовность: После 30д прогона ORG_DUES == 60; conservation казни сходится.
- [ ] T7: Выборы: при `day % interval == 0` и при мёртвом лидере (succession); score = Σ affection живых избирателей; tie → lowest id; смена ролей; `ELECTION` event с reason. Готовность: Юнит-тесты tie-break и succession; максимум одни выборы на org в день.
- [ ] T8: Издание закона: день 0 + начало срока; dot(traits лидера, enact_traits), tie → порядок каталога; upsert `org_laws` + `LAW_ENACTED` (без события при совпадении). Готовность: Новый лидер детерминированно меняет/сохраняет активный закон.
- [ ] T9: Закон `night_home`: проверка в daily-фазе; штрафы через ledger (silent-skip), `LAW_VIOLATION` + строка `org_law_violations`. Готовность: Дубли (org, law, char, day) невозможны (UNIQUE).
- [ ] T10: Хук `no_conflict` в CONFLICT-ветке `complete_task` (гейт первой строкой, внутри тела ветки; чтение активного закона community; штраф только при `law_key == 'no_conflict'`; UNIQUE-коллизия → skip): каждый участник — нарушитель, штраф, `LAW_VIOLATION`. Готовность: При org-off CONFLICT-профиль M2 байт-идентичен (AE5'); при активном `night_home` CONFLICT не штрафуется.
- [ ] T11: Примирение: пара ≤ −60 (id-ascending, ≤1/день/org), медиатор max aff-суммы, set −30, `RECONCILIATION` (target_id = null) + `RELATIONSHIP_CHANGED` + `relationship_events`. Готовность: Юнит-тест thaw ровно −30; 10д soak без ре-заморозки.
- [ ] T12: Праздник: `feast_interval_days`, при достатке казны — burn + social +20 живым членам (clamp), `ORG_FEAST`. Готовность: Нехватка средств → skip без события.

## Инварианты и отчёт (Фаза C)
- [ ] T13: Шесть инвариантов spec R9 за гейтом R1; `org_treasury_conservation` — полная ledger-реплей-проверка (PURCHASE/ORG_DUES/LAW_FINE inflow; SALARY/ORG_FEAST outflow). Готовность: Позитивные/негативные юнит-тесты (в т.ч. `org_leader_valid` падает при мёртвом лидере до succession); conservation сходится на 30д org-on прогоне.
- [ ] T14: Блок `org` в отчёте (spec R10): организации (лидер, казна, активный закон, выборы), summary (elections, violations, fines, feasts, reconciliations). Готовность: При org-off ключа `org` нет; при org-on все поля заполнены.

## Верификация и приёмка (Фаза D)
- [ ] T15: Юнит-механика (AE3'): tie-break, succession, fine-skip (dues/штраф/праздник), thaw −30, soak без ре-заморозки, conservation. Готовность: Все тесты AE3' пройдены.
- [ ] T15b: Зафиксировать M2 social-on векторы (7д/30д: events_by_type, отношения) в regression-fixture ДО изменений механики. Готовность: Fixture существует; тест сравнения org-off прогонов с fixture зелёный.
- [ ] T16: Dry-run эмпирика: объём штрафов `night_home` (M1-расписания), PURCHASE-выручка магазина, момент достижения feud-парами −60, смена лидеров на seed 42; фиксация AE-порогов. Готовность: Пороги AE1'/AE2' подтверждены или откалиброваны (прецедент M2 R10).
- [ ] T17: Интеграционные прогоны: AE1' (30д org-on), AE2' (7д), AE4' (детерминизм), AE5' (байт-идентичность всех векторов M1/M2 + нет ключа `org`), AE6' (config sensitivity). Готовность: AE1'-AE6' выполнены.
- [ ] T18: Финальный гейт: полный fast-набор + slow-набор + ruff + `scripts/check.sh`. Готовность: Все проверки пройдены; CI зелёный на PR.
