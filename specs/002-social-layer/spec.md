# Спецификация функционала: M2 — Социальный слой (Social Layer)

## Outcome

Добавление социального взаимодействия в мир «ВЛ1: Рейнеке»: NPC теперь имеют долгосрочные взаимоотношения, могут общаться, ссориться и объединяться в организации. Реализовано новое действие `SOCIALIZE`, которое восстанавливает социальную потребность и влияет на уровень симпатии (`affection`). Система полностью детерминирована, расширяет контракт событий M1 и сохраняет полную совместимость с базовым ядром (при `social.enabled = false`).

## Scope

- **Included**:
  - Система взаимоотношений: таблица `relationships` с 8 измерениями (SPEC-V0.1 §29), динамика симпатии (`affection`), события изменения отношений (§30, §59:2809).
  - Социальное действие: `SOCIALIZE` с детерминированным выбором цели, проверкой порогов отказа (`refusal_threshold`) и восстановлением потребности (`social_recovery_per_minute`) (§102).
  - Интеграция с Utility AI: добавление веса `SOCIALIZE.base_utility_weight`, интеграция в реестр действий (`registry.py`) и гейт по порогу `social_action_threshold` (§102).
  - Контракт событий: расширение closed-set из 10 типов (M1) до 13 (добавлены `SOCIAL_INTERACTION`, `RELATIONSHIP_CHANGED`, `CONFLICT`).
  - Минимальные организации: автоматическое назначение лидеров при сиде, таблица `organization_members`, членство в общине и организациях работодателей (§30).
  - Базовые конфликты: триггер конфликта при `affection < -30` после завершения `SOCIALIZE`, увеличение стресса персонажа, фиксированный штраф к симпатии (§98:3554).
  - Инварианты: проверка диапазонов отношений [-100, 100], целостность связей событий и членства в организациях.
  - Конфигурация: новый раздел `social:` в YAML.
  - CLI: флаг `--social` для активации функционала в прогонах.
- **Excluded**:
  - LLM, генерация текстов диалогов, память NPC и сессии общения (M3).
  - Динамика по 7 измерениям отношений (кроме `affection`) — зафиксированы в 0 (отклонение от §10, путь к M3).
  - Романтика, брак, семейные связи, домохозяйства (M3).
  - Направленность отношений (в M2 пары симметричны, `character_a < character_b`) (M3).
  - Физическое насилие, преступления, полиция, месть, траур (M3).
  - Политика организаций: выборы, бюджеты, законы (M3).
  - Runtime-изменение членства в организациях (только при сиде).
  - REST/WebSocket API (M4), Flux/Визуализация (M5).
- **Note**: Данный этап закрывает отложенные в M1 задачи по обновлению отношений (§117) и созданию таблиц `relationships` / `relationship_events` (M1 plan.md:93).

## Requirements

- **R1: Схема взаимоотношений**. Создаётся таблица `relationships` для неупорядоченных пар (`character_a < character_b`), UNIQUE(`world_id`, `character_a`, `character_b`). 8 измерений (trust, affection, respect, fear, anger, attraction, romantic_interest, familiarity) в диапазоне [-100, 100]. В M2 динамическим является только `affection`, остальные зафиксированы в 0 (SPEC-V0.1 §29).
- **R2: События отношений**. Таблица `relationship_events` (id, world_id, character_a, character_b, event_type, impact, event_id FK world_events, game_timestamp) согласно §30.
- **R3: Действие SOCIALIZE**. 
  - Валидность: `social < social_action_threshold` (40.0) И наличие хотя бы одного живого NPC в той же локации.
  - Выбор цели (детерминирован): кандидаты (живые в той же локации) сортируются по (`-affection`, `character_id`). Выбирается первый, у которого `affection > refusal_threshold` (-60.0). Если подходящих нет → действие невалидно → переход к IDLE.
  - Примечание: сортировка по убыванию враждебности означает, что конфликтные пары из feud-диапазона (−60, −30] выбираются ПЕРВЫМИ — это сознательно: сидированные «старые конфликты» (§98) разыгрываются в первую очередь.
  - Длительность: 30 мин.
  - Эффект: восстановление `social` со скоростью `social_recovery_per_minute` (0.5/мин).
  - Предусловие локации: если цель не в той же локации, срабатывает MOVE в social hub (kitchen/settlement), по аналогии с EAT→kitchen (validators.py:36-81).
- **R4: Интеграция Utility AI**. 
  - Формула: `score = weights.social * (100 - social) + SOCIALIZE.base_utility_weight` (utility.py:47-69).
  - Позиция в реестре: после `BUY_ITEM`, перед `IDLE` (registry.py:32-34).
- **R5: Контракт событий**. 
  «The M1 event contract — closed set of 10 types (events.py:10-20, M1 spec R8) — is immutable in names and payload shapes. M2 extends it to a closed set of 13 by adding SOCIAL_INTERACTION, RELATIONSHIP_CHANGED, CONFLICT. Each new type names ≥1 invariant or report consumer. Further additions require a spec change.»
  - `SOCIAL_INTERACTION`: payload `{target_id, duration, affection_delta, affection_after}`.
  - `RELATIONSHIP_CHANGED`: пороги перехода (stranger → acquaintance +10, → friend +50, → conflicted -30) (§59:2809). Payload `{band, affection_before, affection_after}`.
  - `CONFLICT`: payload `{affection_before, affection_after, stress_delta}`.
- **R6: Организации (минимальные)**. 
  - Лидер: детерминированно выбирается NPC с наименьшим id, чья профессия входит в `leader_titles` организации, либо просто NPC с наименьшим id.
  - Хранение: таблица `organization_members` (organization_id, character_id, role 'leader'|'member', joined_at).
  - Членство: все NPC принадлежат общине, владельцы профессий — соответствующим организациям работодателей.
- **R7: Конфликты**. 
  - При завершении `SOCIALIZE`, если `affection` ЦЕЛИ НА МОМЕНТ НАЧАЛА взаимодействия < −30 (feud-диапазон (−60, −30]) → событие `CONFLICT`: `affection` снижается на 5.0, `character_health.stress` увеличивается на 5 (clamped [0, 100]) ОБОИМ участникам (актор и цель). Без урона здоровью и смерти.
  - Достижимость (адверсариальное ревью): refusal-порог −60 допускает выбор сидированных пар (−50); CONFLICT возникает у них при первой же ко-локации в хабе. Ниже −60 пары «заморожены» (полное избегание); примирение в M2 невозможно (M3).
  - Сидирование: 2 пары конфликтов (`affection = -50`) создаются при генерации мира через seeded RNG (детерминировано) (§98:3554).
  - Обычный delta: +2.0 при успешном взаимодействии, clamp [-100, 100] (диапазон — §29).
- **R8: Конфигурация**. Добавляется раздел:
  ```yaml
  social:
    enabled: false
    interaction_minutes: 30
    social_recovery_per_minute: 0.5
    social_action_threshold: 40.0
    refusal_threshold: -60.0
    initial_affection: 0.0
    initial_conflicts: 2
    hub_location_types: [kitchen, settlement]
  ```
  Дополнение к `actions.SOCIALIZE`: `{duration_minutes: 30, max_duration_minutes: 60, base_utility_weight: 0.3}`.
  Дополнение к `needs.recovery_rates`: `SOCIALIZE: {social: 0.5}`.
- **R9: Инварианты**. 
  - `relationship_range`: все 8 измерений ∈ [-100, 100], отсутствие self-pairs, `character_a < character_b`.
  - `social_event_integrity`: актор и цель события существуют в одном мире, актор ≠ цель, валидная локация.
  - `relationship_event_link`: каждый `relationship_events.event_id` существует в `world_events`.
  - `org_membership_integrity`: члены существуют; лидер организации является её членом.
  - `no_impossible_states`: расширение — `stress` ∈ [0, 100].
- **R10: Performance Trigger**. Если прогон 30д/20NPC (social-on) > 45 с (75% от NFR 60 с) или > 10k вызовов `choose_action` → обязательное внедрение batch needs-fetch.

## Non-Functional Requirements

- **Performance**: Wall-time ≤ 60 с для прогона 30д/20NPC с включенным `social.enabled = true`.
- **Determinism**: Два прогона с одним seed должны давать идентичную последовательность `world_events`.
- **Regression**: При `social.enabled = false` поведение мира должно быть идентично M1 (AE5). Все тесты M1 AE1-AE8 должны проходить без изменений.

## Acceptance Evidence

- **AE1**: `vl1 simulate --days 30 --population 20 --seed 42 --social` → exit 0; все новые инварианты пройдены; `SOCIAL_INTERACTION` ≥ 60, `CONFLICT` ≥ 2, `RELATIONSHIP_CHANGED` ≥ 10; population_alive == 20; wall-time ≤ 60 с.
- **AE2**: 7-дневный прогон/20 NPC (social-on) зелёный (`SOCIAL_INTERACTION` ≥ 14).
- **AE3**: Unit/интеграционные тесты обновлений отношений (§117): детерминированный delta, clamp ±100, отказ ниже -30, триггер конфликта при завершении, событие перехода между bands ровно один раз.
- **AE4**: Детерминизм: два идентичных seed → идентичный лог; разные seed → разные логи.
- **AE5**: Регрессия M1: дефолтный конфиг (social off) 30-дневный прогон воспроизводит вектор событий M1 в точности (SALARY_PAID == 600, SUPPLY_ARRIVED == 30, идентичная последовательность `world_events`); AE1-AE8 M1 пройдены.

## Risks

- **SOCIALIZE dominance**: При большом количестве NPC в поселении (до 200) действие `SOCIALIZE` может стать доминирующим, вытесняя другие потребности.
- **Feud-динамика**: конфликтные пары деградируют до −60 за ~2 стычки и замораживаются; примирение невозможно в M2 (осознанное ограничение, M3). Сортировка «самый ненавистный первым» концентрирует ранние SOCIALIZE на feud-парах — AE1 CONFLICT ≥ 2 ожидаемо достижим, но наблюдаемая картина первых дней будет конфликт-тяжёлой.
- **Performance headroom**: Текущий baseline M1 (50.06 с) оставляет мало запаса до лимита в 60 с; риск срабатывания триггера R10.
- **Pydantic defaults**: Требуется осторожное обновление моделей Pydantic для поддержки новых полей в `conftest`.
- **Float determinism**: Различия в вычислениях float на разных платформах могут повлиять на детерминизм отношений (смягчено использованием фиксированных дельт).
- **§10 Tension**: Зафиксировано отклонение от SPEC-V0.1 §10 (использование набора колонок вместо одного числа) для обеспечения пути развития в M3.

## Open Questions (все решены)

- Q1: `social.enabled` по умолчанию `false` (рекомендовано) или `true`. — **РЕШЕНО (владелец делегировал решение оркестратору): false**.
- Q2: Отложенный Alembic; `schema_meta` 0.1.0 → 0.2.0; сохранение `create_all` (M1 Q2) до первого живого деплоя в M4+. — **РЕШЕНО (владелец делегировал решение оркестратору): отложен, schema_meta 0.2.0**.
- Q3: Симметричные пары (по §29) сейчас против направленных отношений. — **РЕШЕНО (владелец делегировал решение оркестратору): симметричные**.
- Q4: Тяжесть конфликта: только рост стресса (рекомендовано) или также -1 к здоровью. — **РЕШЕНО (владелец делегировал решение оркестратору): только стресс**.
- Q5: Сидированные старые конфликты: 2 пары по -50 (по §98) или 0. — **РЕШЕНО (владелец делегировал решение оркестратору): 2 пары**.
- Q6: Refusal-порог −30 (исходный дизайн архитектора) против −60 (фикс достижимости CONFLICT по итогам адверсариального ревью: при −30 CONFLICT недостижим — сидированные пары −50 навсегда заблокированы выбором цели, единственная дельта +2.0 уводит от порога). — **РЕШЕНО (владелец делегировал решение оркестратору): −60**.
