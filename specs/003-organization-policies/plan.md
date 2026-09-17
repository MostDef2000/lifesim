# План реализации: M3 — Политики организаций (Organization Policies)

## Этапы реализации

### Фаза A: Схема, конфигурация, CLI (Schema & Config)
1. **Схема**:
   - Таблица `org_laws` (UNIQUE(world_id, organization_id) — один активный закон).
   - Таблица `org_law_violations` (UNIQUE(world_id, organization_id, law_key, character_id, game_day) — идемпотентность штрафов в констрейнте).
   - `schema_meta` 0.2.0 → 0.3.0.
   - *Точки интеграции*: `backend/app/db/models.py` (по аналогии с `org_laws`-подобными таблицами M2), `models.py:328` (версия).
2. **Конфигурация**:
   - `OrgConfig` в `config.py` (все поля с дефолтами; гейт `enabled: false`); секция `org:` в `default.yaml`.
   - Каталог законов в конфиге: `no_conflict {fine: 5, enact_traits}`, `night_home {fine: 2, enact_traits}`.
   - *Точки интеграции*: `config.py:132-145` (паттерн `SocialConfig`).
3. **CLI**: флаг `--org` (store_true) рядом с `--social`, применение через `model_copy`. `--org` без `--social` → неактивен.
4. **События**: 6 новых `EventType` (ELECTION, ORG_DUES, ORG_FEAST, LAW_ENACTED, LAW_VIOLATION, RECONCILIATION) с payload-конвенциями spec R5 (13 → 19).
   - *Точки интеграции*: `backend/app/events/events.py:10-23`.
5. **Day-0 хук издания закона**: вызов после `seed_social` в worldgen-флоу (`cli.py:64-75`), за гейтом R1 (`org.enabled && social.enabled`), идемпотентный; при org-off — ноль мутаций. Без него `no_conflict` не штрафует конфликты дней 1-6 (дневные тики начинаются с timestamp 1440).

### Фаза B: Механика (Mechanics)
1. **Daily org-хендлер** — размещается в `run_daily_handlers` ДО early-return «нет community-организации» (daily.py:100-101), фиксированный внутренний порядок:
   1. Взносы (dues) — transfer по живым членам, агрегатный `ORG_DUES` event.
   2. Succession/плановые выборы — голосование `Σ affection`, tie → lowest id, смена ролей, `ELECTION`.
   3. Издание закона новым лидером — dot(traits, enact_traits), upsert `org_laws`, `LAW_ENACTED`.
   4. Проверка `night_home` — нарушители в полночь, штрафы, `LAW_VIOLATION`.
   5. Примирение — выбор пары ≤ −60, медиатор max aff-суммы, set −30, `RECONCILIATION` + `RELATIONSHIP_CHANGED` + `relationship_events`.
   6. Праздник — burn из казны, social +20 живым членам, `ORG_FEAST`.
   - *Точки интеграции*: `backend/app/simulation/daily.py:34-91` (после SALARY; паттерн silent-skip).
2. **CONFLICT-хук** (`no_conflict`): внутри существующей CONFLICT-ветки `complete_task` (ветка условия lifecycle.py:269, тело 272-297 — хук внутри тела, не до условия), гейт `org.enabled and laws.enabled` — **первой строкой**, затем чтение активного закона community (`org_laws`): штраф только при `law_key == 'no_conflict'`; коллизия UNIQUE → штраф и событие скипаются (риск байт-идентичности).
   - *Точки интеграции*: `backend/app/actions/lifecycle.py:272-297`.
3. **Гейт**: весь хендлер выходит до любых запросов/мутаций при `not (org.enabled and social.enabled)`.

### Фаза C: Инварианты и отчёт (Invariants & Report)
1. **6 инвариантов** (spec R9): `org_leader_valid` (усиление: + живость, толерантность к мёртвому лидеру при нуле живых членов), `law_consistency`, `law_violation_idempotency` (event link только для `no_conflict`-строк), `org_treasury_conservation` (**полная ledger-реплей-проверка**: balance == mint + Σ(PURCHASE, ORG_DUES, LAW_FINE) − Σ(SALARY, ORG_FEAST) — зарплаты платятся ИЗ org-счётов, магазин получает PURCHASE-выручку), `reconciliation_link`, `election_consistency` (живость победителя через `death_game_timestamp`).
   - `social_event_integrity` не меняется (не смотрит RELATIONSHIP_CHANGED, invariants.py:368-370).
   - *Точки интеграции*: `backend/app/simulation/invariants.py:23-457`.
2. **Отчёт**: блок `org` (spec R10), гейт = `org.enabled && social.enabled`; `treasury_balance` из Account.
   - *Точки интеграции*: `backend/app/simulation/report.py:62-95` (паттерн social-блока).

### Фаза D: AE-тесты и приёмка (Acceptance)
1. **Фиксация M2-векторов**: regression-fixture с точными social-on 7д/30д векторами (events_by_type, отношения) — снимаются ДО первого изменения механики; сейчас существуют только threshold-ассерты.
2. Юнит-механика (AE3'): tie-break выборов, succession, fine-skip, thaw −30, 10-д soak без ре-заморозки, conservation (включая PURCHASE-приток магазина).
2. AE1'/AE2' интеграционные прогоны (30д/7д org-on, seed 42).
3. AE4' детерминизм org-on (два прогона → идентичные логи).
4. AE5' байт-идентичность: org-off 7д == M1 вектор; org-off+social-on == M2 векторы; отчёт без `org`.
5. AE6' config sensitivity (интервал выборов).
6. Финальный гейт: `scripts/check.sh` + slow-набор.

## Матрица регрессии и тестирования

| Этап | Обязательные тесты M1/M2 (AE5') | Новые проверки |
|---|---|---|
| Фаза A | M1-вектор 7д байт-в-байт; M2 social-on векторы; minimal-settings конфиг | `test_schema_030`, `test_org_config_defaults`, `test_event_types_count` |
| Фаза B | Векторы при org-off (гейт хендлера и CONFLICT-хука) | `test_elections_tiebreak`, `test_succession`, `test_dues_skips`, `test_fine_skip_broke`, `test_thaw_exact_minus30`, `test_no_refreeze_soak` |
| Фаза C | Отчёт org-off без ключа `org` | `test_org_invariants_positive_negative`, `test_org_report_block` |
| Фаза D | AE5' (все векторы) | AE1', AE2', AE4', AE6', dry-run эмпирика (ночные отсутствия, смена лидеров) |

## Риски и смягчение
- **CONFLICT-хук в горячем пути M2**: гейт первой строкой; векторы M2 social-on гоняются после каждого изменения Фазы B.
- **Эмпирика до фиксации порогов**: dry-run в Фазе B (объём штрафов night_home) и Фазе D (смена лидеров); при отсутствии смены лидера — перетюнить `enact_traits` каталога (spec Risks).
- **Determinism**: строгая сортировка по id во всех циклах (члены, организации, пары, медиаторы); никаких runtime-RNG.
- **Pydantic defaults**: `OrgConfig` только добавляет поля; minimal-settings тест — guard.
