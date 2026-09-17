# Спецификация функционала: M3 — Политики организаций (Organization Policies)

## Outcome

Организации M2 становятся живыми институтами: лидеры переизбираются детерминированным голосованием членов, казна организации наполняется взносами и штрафами и тратится на праздники общины, лидер издаёт законы из каталога с механическими последствиями (штрафы через ledger), а замороженные feud-пары (−60, «замораживаются» с M2) получают путь к примирению через медиацию лидера. Вся механика детерминирована и находится за гейтом `org.enabled` (по умолчанию выключено): при выключенном гейте профили событий M1/M2 байт-идентичны.

## Scope

- **Included**:
  - Выборы лидеров организаций: фиксированный интервал, электорат из живых членов, scoring по сумме affection, tie-break по id; досрочные выборы при смерти лидера (succession).
  - Казна организации: переиспользование существующего `Account(owner_type='organization')`; ежедневные взносы членов (`ORG_DUES`), приток штрафов, трата на праздник общины (`ORG_FEAST`).
  - Законы: каталог из 2 законов (`no_conflict`, `night_home`), community-организация, один активный закон на организацию, издание лидером (детерминированное trait-правило), штрафы через ledger, идемпотентность через DB UNIQUE.
  - Примирение feud-пар: лидер организует медиацию (медиатор — член с максимальной суммой affection к паре), разовый переход −60 → −30, события `RECONCILIATION` + `RELATIONSHIP_CHANGED`.
  - Контракт событий: расширение closed-set с 13 (M2) до 19 типов.
  - Инварианты: 6 новых проверок (валидность лидера, консистентность законов, идемпотентность штрафов, сохранность казны, связь примирений, консистентность выборов).
  - Конфигурация: раздел `org:` в YAML, CLI-флаг `--org`.
  - Схема: 2 новые таблицы (`org_laws`, `org_law_violations`), `schema_meta` 0.2.0 → 0.3.0.
- **Excluded**:
  - LLM, диалоги, память NPC (SPEC-V0.1 §103 — переносится в следующий цикл `004-llm`, решение владельца 2026-09-17).
  - Романтика, брак, семейные связи, домохозяйства (отложено из M2).
  - Направленность отношений (пары остаются `character_a < character_b`).
  - Физическое насилие, преступления, полиция, месть, траур (отложено из M2; закон `no_conflict` штрафует существующие CONFLICT, новых видов насилия нет).
  - Городская политика (§109).
  - Глаголы закона «не знать» и «выступать против» (§776-777) — нет модели осведомлённости/несогласия на этом масштабе (документированное упрощение).
  - Runtime-вступление/выход из организаций (членство только при сиде, M2).
  - Отзыв лидера в середине срока (только succession по смерти).
  - Субсидирование ежедневных поставок из казны (follow-up кандидат).
- **Note**: Этап закрывает отложенные в M2 пункты «Политика организаций: выборы, бюджеты, законы (M3)» (002 spec.md:25) и примирение feud-пар (002 spec.md:59,102).

## Requirements

- **R1: Гейт**. Вся org-механика активна только при `settings.org.enabled AND settings.social.enabled` (члены организаций существуют только при включённом social — social_seed.py:30-31). При выключенном гейте org-хендлер выходит ДО любых запросов, открытия счёта или мутаций; в отчёте нет ключа `org`; новые события не эмитятся. Контракт байт-идентичности: профили M1 (org-off) и M2 social-on (org-off) неизменны.
- **R2: Выборы**.
  - Каденция: `election_interval_days: 7`; выборы в daily-фазе при `day > 0 and day % interval == 0` (30-дневный прогон: дни 7, 14, 21, 28; 7-дневный: день 7 включительно).
  - Электорат и кандидаты: все живые члены организации (инкумбент допущен). Организация без живых членов: ни выборов, ни взносов, ни праздника; в `org_leader_valid` мёртвый лидер допустим только если в организации нет живых членов.
  - Правило голосования: `score(c) = Σ affection(v, c)` по живым избирателям `v ≠ c` (свой голос исключён; float-сумма, отрицательные значения допустимы). Победитель — max score; tie → наименьший `character_id` (проектная конвенция tie-break).
  - Эффект: смена ролей в `organization_members` (старый лидер → `member`, победитель → `leader`) + событие `ELECTION`.
  - Succession: если текущий лидер мёртв и день НЕ планово-выборный — досрочные выборы в этот день (`reason: "succession"`). В планово-выборный день проходит ровно одни плановые выборы (succession в этот день не применяется). Идемпотентность — максимум одни выборы на организацию в день.
  - Полномочия лидера (единственные поведенческие отличия в M3): выбор каталогового закона в начале срока (R4) и организация примирений (R6). Без направленной власти, без LLM.
- **R3: Казна**.
  - Хранение: существующий `Account(owner_type='organization')` (models.py:180-188, заминтован 5000 при сиде); единственный денежный путь — ledger (constitution P3).
  - Взносы: daily-фаза, ПОСЛЕ зарплат; каждый живой член платит `dues_per_day: 1` через `economy.transfer(reason='ORG_DUES')`; нехватка средств — silent skip (паттерн daily.py:89-91). Агрегатный `ORG_DUES` event эмитится КАЖДЫЙ день для КАЖДОЙ организации, даже если все члены скипнулись (`paid_members=0`): `{organization_id, amount_per_member, paid_members, skipped_members, total_collected}`.
  - Праздник: для КАЖДОЙ организации (как взносы), каждые `feast_interval_days: 14`, если баланс казны ≥ `feast_cost: 30` → `economy.burn(30, reason='ORG_FEAST')`, каждому живому члену `social +20.0` (clamp 100), событие `ORG_FEAST` (actor = лидер). Нехватка средств → skip без события. Организация без живых членов — праздник не проводится.
  - Фиксированный порядок daily org-хендлера: (1) взносы → (2) плановые выборы ИЛИ succession (ровно одно из) → (3) издание закона новым лидером → (4) проверка `night_home` → (5) примирение → (6) праздник. Хендлер размещается в `run_daily_handlers` ДО early-return «нет community-организации» (daily.py:100-101), чтобы org-механика не терялась в вырожденных мирах.
- **R4: Законы**.
  - Каталог определён в конфиге (не в БД), применяется только к community-организации, один активный закон на организацию.
  - **`no_conflict`** (fine: 5): нарушение = участие в событии `CONFLICT`; каждый участник — нарушитель; штраф `economy.transfer(reason='LAW_FINE')` в казну общины (silent skip при нехватке) + `LAW_VIOLATION` event. Максимум один штраф на персонажа на закон в день (DB UNIQUE, R8; коллизия UNIQUE → и штраф, и событие скипаются).
  - **`night_home`** (fine: 2): на дневной границе каждый живой персонаж community с `location_id != home_location_id` — нарушитель; штраф 2 (тот же механизм). Проверка в daily-фазе (headless-цикл шагает целыми сутками — «часовая» проверка фактически совпадает с полуночью). `home_location_id` гарантированно не NULL у созданных генератором NPC (generator.py:102); NULL → персонаж скипается (не нарушитель).
  - Издание: при worldgen (день 0, хук после `seed_social` в worldgen-флоу cli.py:64-75, за гейтом R1) и в начале каждого нового срока лидер выбирает закон из каталога, максимизирующий `dot(traits лидера, law.enact_traits)` по существующим ключам трейтов (`sociability, discipline, risk_tolerance`; трейты целочисленны — dot детерминирован); tie → первый по порядку каталога. Совпадение с активным законом → без изменений и без события. Иначе upsert `org_laws` + `LAW_ENACTED` event `{organization_id, law_key, replaced_law_key}` (actor = лидер).
  - Покрытие глаголов §774-778: «соблюдать» (неявно), «нарушать» (штраф), «пытаться изменить» (переиздание в начале срока); «не знать»/«выступать против» — out of scope.
- **R5: Контракт событий** (closed set 13 → 19; каждое называет ≥1 consumer — конвенция M2 R5):
  - `ELECTION`: actor = победитель, target = предыдущий лидер; payload `{organization_id, previous_leader_id, score, candidates_count, reason: scheduled|succession}` — `score` = float-сумма affection по живым избирателям (отрицательная допустима); consumer: `election_consistency`, отчёт.
  - `ORG_DUES`: actor = null (агрегат); payload `{organization_id, amount_per_member, paid_members, skipped_members, total_collected}`; consumer: `org_treasury_conservation`, отчёт.
  - `ORG_FEAST`: actor = лидер; payload `{organization_id, cost, boosted_members}`; consumer: `org_treasury_conservation`, отчёт.
  - `LAW_ENACTED`: actor = лидер; payload `{organization_id, law_key, replaced_law_key}`; consumer: `law_consistency`, отчёт.
  - `LAW_VIOLATION`: actor = нарушитель, target = null; payload `{organization_id, law_key, fine, fine_paid, source_event_type}` — для `night_home` `source_event_type = null` (нет события-источника); consumer: `law_violation_idempotency`, отчёт.
  - `RECONCILIATION`: actor = медиатор, target = null (пара в payload); payload `{organization_id, pair: [a, b], mediator_id, affection_before, affection_after}`; consumer: `reconciliation_link`, отчёт.
  - Конвенция: `actor_id`/`target_id` — character ids или null; идентификатор организации всегда в payload.
- **R6: Примирение feud-пар**.
  - В daily org-хендлере, community-организация, максимум 1 пара на организацию в день, первая подходящая в порядке `(character_a, character_b)` id-ascending.
  - Пара: оба живы, оба члены community, `affection <= -60.0` (ровно замороженное состояние; −60.0 не проходит refusal-фильтр `> -60` — пара инертна).
  - Медиатор: живой член `m ∉ {a, b}`, максимизирующий `aff(m,a) + aff(m,b)`; tie → lowest id. Нет медиатора, либо лидер мёртв/сам в паре → skip дня (retry завтра; без состояния, без событий).
  - Эффект: `affection := target_affection` (−30.0) разово; событие `RECONCILIATION` + стандартный `RELATIONSHIP_CHANGED` (band-crossing conflicted → stranger) + строка `relationship_events` по конвенциям lifecycle.py:327-345 (хук SOCIALIZE здесь не срабатывает — это не завершение действия).
  - Отсутствие ре-эскалации (доказано по коду): при `affection = −30` CONFLICT-ветка недостижима (требуется `< −30` на момент начала, lifecycle.py:269), aggravated-seek пару не выбирает (фильтр `aff < −30`, validators.py:248), refusal пермиссивен (−30 > −60) → размороженная пара монотонно улучшается; повторное замораживание невозможно без нового сидированного конфликта. Одно примирение на эпизод заморозки — по построению.
  - Инвариант `social_event_integrity` изменений НЕ требует: он фильтрует только CONFLICT/SOCIAL_INTERACTION (invariants.py:368-370) и не смотрит RELATIONSHIP_CHANGED; корректность примирения покрывает `reconciliation_link` (R9.5).
- **R7: Конфигурация**. Добавляется раздел:
  ```yaml
  org:
    enabled: false
    election_interval_days: 7
    dues_per_day: 1
    feast_interval_days: 14
    feast_cost: 30
    feast_social_boost: 20.0
    reconciliation:
      enabled: true
      target_affection: -30.0
    laws:
      enabled: true
      catalog:
        no_conflict: { fine: 5, enact_traits: { discipline: 1, sociability: 0, risk_tolerance: -1 } }
        night_home:  { fine: 2, enact_traits: { discipline: 1, sociability: -1, risk_tolerance: 1 } }
  ```
  - `OrgConfig` (pydantic) со всеми дефолтами; `Settings.org: OrgConfig`. YAML без секции `org:` загружается с дефолтами (старые конфиги валидны).
  - CLI: `--org` (store_true) по паттерну `--social` (cli.py:29,46-48), применяется через `model_copy` рядом с `--social`. `--org` без `--social` → org неактивен (документировано, без ошибки).
- **R8: Схема** (0.2.0 → 0.3.0, `create_all` сохраняется):
  - `org_laws`: id, world_id, organization_id, law_key, enacted_by_character_id, enacted_day, enacted_at; UNIQUE(world_id, organization_id) — один активный закон, upsert при переиздании.
  - `org_law_violations`: id, world_id, organization_id, law_key, character_id, game_day, game_timestamp, fine_paid (int, 0 при skip), event_id (FK world_events, **nullable** — NULL для `night_home`-нарушений, у которых нет события-источника); UNIQUE(world_id, organization_id, law_key, character_id, game_day) — идемпотентность «один штраф в день» живёт в констрейнте, не в коде.
  - НЕТ таблицы выборов (текущий лидер = `organization_members.role`; история = `world_events`), НЕТ treasury-таблицы (Account), НЕТ новых колонок в `relationships`.
  - Сознательные обновления структурных тестов: количество таблиц 23 → 25, версия схемы, `len(EventType)` 13 → 19.
- **R9: Инварианты** (все за гейтом R1, паттерн invariants.py:427):
  1. `org_leader_valid`: ровно один `role='leader'` на организацию И лидер жив (усиление M2-проверки, которая не проверяла живость).
  2. `law_consistency`: ≤1 строки `org_laws` на организацию; `law_key` ∈ каталога конфига; издатель — член организации.
  3. `law_violation_idempotency`: нет дублей (org, law, char, day); каждое `LAW_VIOLATION` событие имеет строку; каждая `no_conflict`-строка имеет event link (`night_home`-строки — event_id = NULL); `Σ fine_paid` == `Σ LAW_FINE` транзакций.
  4. `org_treasury_conservation`: полная ledger-реплей-проверка по каждой org-казне (constitution P3: ledger — единственный денежный путь): `balance == initial_mint + Σ inflow − Σ outflow`, где inflow-причины: `PURCHASE` (выручка магазина, lifecycle.py:183-192), `ORG_DUES`, `LAW_FINE`; outflow: `SALARY` (зарплаты платятся ИЗ org-счёта, daily.py:68-81), `ORG_FEAST`. Формула обязательна полная: магазин получает выручку BUY_ITEM, зарплаты платятся из org-счетов — упрощённая формула «mint + dues + fines − feasts» даёт false-fail.
  5. `reconciliation_link`: у пары из `RECONCILIATION` есть relationship-строка; `affection_after == target_affection`; `affection_before <= −60`; пара — живые члены одной организации.
  6. `election_consistency`: последний `ELECTION` организации ⇒ победитель == текущая leader-строка; победитель был живым членом на момент выборов (проверка: `death_game_timestamp` победителя отсутствует или > timestamp выборов).
- **R10: Отчёт**. Новый верхнеуровневый блок `org` в JSON-отчёте, присутствует **только** при `org.enabled && social.enabled` (в выключенном режиме ключа `org` нет — AE-ассерт):
  ```json
  "org": {
    "organizations": [
      {"name", "id", "leader_id", "leader_since_day", "member_count", "treasury_balance",
       "active_law", "dues_collected_total", "feasts",
       "elections": [{"day", "winner", "previous_leader", "reason"}]}
    ],
    "summary": {"elections", "violations", "fines_total", "fines_unpaid", "feasts", "reconciliations"}
  }
  ```
  `treasury_balance` читается из org-счёта (единственный источник истины, R3).
- **R11: Performance**. Org-механика живёт в daily-фазе, сложность O(организации × члены) в день; org-overhead при 20 NPC пренебрежим (30д social-on = 64-67 с на dev, M2 R10). Guard: 30д org-on ≤ 180 с на dev-машине. NFR для эталонной машины: `wall-time(30д org-on) ≤ wall-time(30д social-on) + 10%` — относительная формулировка (у absolute 60 с на эталонной машине нет запаса даже для M2: reference social-on ≈ 54-64 с); калибровка на эталонной машине — в T16 (прецедент M2 R10).

## Non-Functional Requirements

- **Performance**: wall-time(30д/20NPC org-on) ≤ wall-time(30д social-on) + 10% на эталонной машине; dev-guard ≤ 180 с.
- **Determinism**: два прогона с одним seed → идентичные последовательности `world_events`, идентичные строки `org_laws` и `relationships`.
- **Regression**: при `org.enabled = false` поведение мира идентично M1 и M2: байт-идентичные векторы событий (M1 7д; M2 social-on 7д/30д — векторы фиксируются в regression-fixture на старте Фазы D), отчёт без ключа `org`, все тесты M1/M2 проходят без изменений (кроме трёх сознательных структурных апдейтов R8).

## Acceptance Evidence

- **AE1'**: `vl1 simulate --days 30 --population 20 --seed 42 --social --org` → exit 0; `invariants_ok`; `ELECTION` ≥ 8 (2 организации × 4 терма); `ORG_DUES` == 60 (2 × 30 дней); `ORG_FEAST` ≥ 1; `LAW_ENACTED` ≥ 1; `RECONCILIATION` == 2 (по одной на сидированную пару); `CONFLICT` ≥ 2 (ожидаемо 4); в конце НЕТ relationship с `affection < −30`; `population_alive == 20`; wall-time ≤ 180 с (guard).
- **AE2'**: 7-дневный org-on прогон зелёный; `SOCIAL_INTERACTION` ≥ 14; `ELECTION` ≥ 2 (граница дня 7, обе организации).
- **AE3'**: юнит-механика: tie-break выборов; succession после смерти лидера; fine-skip при нехватке средств (взносы, штрафы, праздник); thaw ровно в −30.0; отсутствие ре-заморозки (10-дневный soak после примирения); conservation казны.
- **AE4'**: детерминизм org-on: два идентичных 7-дневных прогона → идентичные `world_events` (тип, actor, timestamp, payload), идентичные `org_laws`, идентичные `relationships`; другой seed → другой лог.
- **AE5'** (keystone): байт-идентичность: (i) org-off 7д == M1-вектор (уже pinned в test_chunk_d.py:325-336); (ii) org-off + social-on 7д/30д == M2-векторам — **на старте Фазы D векторы M2 social-on (7д/30д: events_by_type, отношения) фиксируются в regression-fixture** (сейчас существуют только threshold-ассерты и комментарий test_chunk_d.py:86); (iii) в отчётах выключенного режима нет ключа `org`.
- **AE6'**: чувствительность к конфигу: изменение `election_interval_days` меняет число `ELECTION` событий.

## Risks

- **Казна — «ритуал, а не ограничение»** (честное ограничение масштаба): при 20 NPC приток ~+20/день против 30/14д праздник и ~5/штраф — seeded 5000 не под угрозой. Механика (dues/fines/feast/conservation) заложена; ограничение проявится с ростом масштаба. Документировано, не баг.
- **Объём штрафов `night_home`** зависит от того, как часто NPC вне дома в полночь при M1-расписаниях — замер dry-run в Chunk B до фиксации AE-порогов (прецедент M2 R10).
- **Смена лидера может не происходить** при дефолтных трейтах: dot-правило издания может стабильно выбирать один и тот же закон, а выборы — того же победителя. Эмпирическая проверка в Chunk D; если лидерство не наблюдаемо — перетюнить `enact_traits` каталога (цель R2: смена лидера должна быть наблюдаемой хотя бы в одном seed-прогоне).
- **CONFLICT-хук в горячем пути M2** (главный риск байт-идентичности): гейт `settings.org.enabled and laws.enabled` — первая строка хука; хук размещается ВНУТРИ conflict-ветки (после lifecycle.py:269/272, не до условия — иначе штрафовались бы и нормальные взаимодействия), читает активный закон community (`org_laws`) и штрафует только при `law_key == 'no_conflict'`; любая неконтролируемая мутация здесь ломает AE5'.
- **Pydantic defaults**: `OrgConfig` не должен менять существующие дефолты; guard — существующий minimal-settings тест (конфиг без `org:`).
- **Float determinism**: дельты фиксированные (как в M2); dot-правило издания — целочисленные трейты, сумма детерминирована.

## Open Questions (все решены)

- Q1: Гейт `org` требует `social`? — **РЕШЕНО (оркестратор; владелец делегировал tune-решения в M2 Q1-Q6): да, silent disable** (члены существуют только при social; без ошибки).
- Q2: Издание закона в день 0 (при worldgen)? — **РЕШЕНО: да** (штрафы применимы с дня 1; день-0 выборы не проводятся — лидер сидирован).
- Q3: Выборы на границе дня 7 в 7-дневном прогоне? — **РЕШЕНО: да** (`day > 0 && day % 7 == 0`).
- Q4: Эмпирика (объём штрафов `night_home`, объём PURCHASE-выручки магазина для conservation-проверки, момент достижения feud-парами −60, смена лидеров на seed 42)? — **РЕШЕНО: dry-run в Фазах B/D, AE-пороги фиксируются после замера** (прецедент M2 R10; conservation-формула фиксирована независимо от замера — она доказуемо полная).
- Q5: `social_event_integrity` при reconciliation-дельте +30.0? — **РЕШЕНО: изменений НЕ требуется** (адверсариальное ревью D5: инвариант фильтрует только CONFLICT/SOCIAL_INTERACTION, invariants.py:368-370, и не смотрит RELATIONSHIP_CHANGED; корректность примирения покрывает `reconciliation_link`).
- Q6: Нумерация вех: SPEC-V0.1 §103 (LLM) формально «Milestone 3» — конфликт с deferred-пакетом M2. — **РЕШЕНО (владелец, 2026-09-17): 003 = политики организаций; LLM переносится в 004-llm**.
