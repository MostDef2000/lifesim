# Спецификация функционала: M7 — External Vladivostok (§106): поездки, внешние сервисы, контакты, больница

## Outcome

Остров перестаёт быть замкнутым миром: Владивосток присутствует как внешний условно симулируемый мир (§38) — `external_locations`/`external_services`/`external_contacts`. Персонажи (игрок и, за флагом, NPC) отправляются в поездку задачей `TRAVEL_EXTERNAL` (§39): порт → услуга (лечение в больнице, покупка товаров, визит к контакту) → возврат с обновлённым инвентарём/здоровьем, деньгами списанными через ledger, событиями. Биографические связи (§106) — контакты NPC, сгенерированные из биографии (birthplace вне острова). Голова без LLM: весь контур детерминированный.

## Scope

- **Included**:
  - Таблицы (3, schema 0.6.0 → 0.7.0, 36 таблиц):
    - `external_locations` (§38): id, world_id, name, ext_type (hospital|university|hardware_store|government|port|bank|supermarket), description.
    - `external_services`: id, world_id, external_location_id FK, service_type (treatment|purchase|visit|registration|transfer), item_type nullable (для purchase), price int (фиксированная на MVP §74-стиль), heal_amount float nullable (для treatment), duration_minutes.
    - `external_contacts`: id, world_id, character_id FK, contact_type (family|friend|colleague|official), name, external_location_id nullable, note.
  - Конфиг `external:`: catalog локаций+сервисов (data-driven), travel_minutes, travel_cost, npc_utility (default **false** — NPC не выбирают поездки в utility в MVP, см. П2), contact_probability, contact_types.
  - Сид `seed_external_world` (вызывается из `seed_world`): каталог из конфига → external_locations/services; контакты NPC по биографии (birthplace вне острова → 1-2 контакта: family/friend в городе; иначе с вероятностью contact_probability один colleague/official). Без событий (контент мира, не геймплей).
  - Действие `TRAVEL_EXTERNAL` (§39): ActionParams в ActionsConfig (duration_minutes = travel_minutes, max = 2×); params {service_id, purpose (≤64 симв.), items ({item_type: qty}, опц.)}.
  - Валидатор: жив; аккаунт существует; баланс ≥ travel_cost + корзина (для purchase: item_type найден в сервисе города); если локация персонажа ≠ порт → needs_move = порт (добраться сначала).
  - Исполнитель (complete_task): TRAVEL_EXTERNAL_DEPARTED {service_id, purpose} → экономика: transfer travel_cost (char → port/government account, reason EXTERNAL_TRAVEL); purchase: transfer сумма + создание предметов инвентаря персонажа (стандартный create/transfer-механизм, П1); treatment: health = min(100, health + heal_amount); visit: без материальных эффектов (MVP); TRAVEL_EXTERNAL_RETURNED {purpose, spent, items, healed}. Локация персонажа не меняется (вернулся туда же).
  - События: `TRAVEL_EXTERNAL_DEPARTED`, `TRAVEL_EXTERNAL_RETURNED` (25 → 27).
  - REST: `GET /external` (локации+сервисы, auth), `GET /characters/{cid}/contacts` (владелец), покупка/лечение — через штатный `POST /actions` (action_type=TRAVEL_EXTERNAL) — П1.
  - Инвариант `external_integrity`: контакты ссылаются на живых персонажей мира; сервисы на локации; для каждого RETURNED с spent>0 существует транзакция(ии); item-типы покупки существуют в сервисах.
- **Excluded**:
  - Физическая симуляция Владивостока (§38: «не нужна»); NPC-utility поездки (флаг npc_utility=false в MVP, интеграция в utility — follow-up); supply/demand цены; ежедневные/исторические данные (§71-стиль); educação/university-квесты; письма/телефон (contacts только для поездок и чтения).
  - Памяти M4 о поездке (follow-up; событие RETURNED уже в журнале).
- **Protected boundaries**:
  - П1: все материальные изменения поездки — через ledger transfer + inventory-механизм + needs/health поля; никаких прямых `money +=`.
  - П2: флаг npc_utility=false сохраняет байт-идентичность M1-M4 (utility не знает про TRAVEL_EXTERNAL); сид внешнего мира не эмитит событий; новые таблицы — контент.
  - П4: n/a (взрослый мир); treatment не «убивает» — health только растёт.

## Requirements

- **R1: Гейты/совместимость**. Новый контур не меняет headless-прогоны M1-M4 (keystone). TRAVEL_EXTERNAL доступен игрокам через POST /actions и NPC только при external.npc_utility=true (MVP false).
- **R2: Сид каталога** (§38). Из `external.locations` конфига: каждая запись → external_location + её services; item-типы purchase-сервисов должны существовать в `economy.prices` (fallback: цена сервиса). Конфиг-ошибка каталога → ValueError на сиде (fail-fast).
- **R3: Контакты**. Для NPC: birthplace профиля вне острова (≠ «остров…»/пусто) → 1-2 контакта (family/friend, name из генератора имён, external_location_id в город); иначе с вероятностью contact_probability → 1 colleague/official. Детерминировано от rng сида. Игроки (user_id NOT NULL) — без контактов (приезжие).
- **R4: Валидация поездки**. Сервис существует (иначе reject «Unknown external service»); purpose ≤ 64; items только для purchase-сервиса, qty 1..10, item_type совпадает с сервисом; баланс ≥ travel_cost + корзина; если не в порту — needs_move (порт), поездка стартует из порта после MOVE.
- **R5: Исполнение**. Порядок: DEPARTED → transfers/purchases/heal → RETURNED. Отказ в момент исполнения (деньги ушли в другом тике) → fail_task с причиной (штатный механизм, restore не применён).
- **R6: Покупки**. Каждый item_type×qty: transfer (цена×qty) + объекты инвентаря персонажа с object_type=item_type; RETURNED.payload.items = {item_type: qty}. Инвентарная консервация: объекты создаются через стандартный создатель объектов (учитывается object_conservation? — нет: это новые предметы из внешнего мира; в инварианте object_conservation внешний приход учитывается через EXTERNAL_IMPORT-маркер — см. R10).
- **R7: Лечение**. health += heal_amount (cap 100), RETURNED.payload.healed. Требование: health < 100 для валидации («Health already full»).
- **R8: Чтение API**. GET /external → [{location, services[]}]; GET /characters/{cid}/contacts → список контактов (403 чужому); оба auth.
- **R9: События** (25 → 27): DEPARTED (actor, payload {service_id, purpose, travel_cost}), RETURNED (actor, payload {purpose, spent, items, healed}). Closed set расширен; consumers: WS, отчёт.
- **R10: Инвариант** `external_integrity` (за гейтом enabled... — симуляционный, всегда активен как референсный, но на headless-мире тривиален): референциальность контактов/сервисов; RETURNED spent ↔ транзакции; покупки → предметы в инвентаре (qty по payload). Объектный приход оформляется `create_object` с source external (т.е. объектный conservation-инвариант M1 должен знать приход — проверяю реализацию: если M1-инвариант строгий, добавить accounting-категорию external_purchase в его ожидания).
- **R11: Ownership**. POST /actions TRAVEL_EXTERNAL — владелец персонажа (или NPC в автономном режиме); contacts — владелец; /external — любой аутентифицированный.

## Non-Functional Requirements

- **Determinism**: сид контактов/каталога — от rng сида мира; поездка — чистая функция параметров (цены фиксированные §74).
- **Performance**: поездка = 1 задача на персонажа; сид = O(локаций + NPC).
- **Operability**: каталог правится в конфиге; события дают полный аудит поездки.
- **Compatibility**: schema 0.7.0; события 27; ActionsConfig += TRAVEL_EXTERNAL (строгие схемы конфига обновить в default.yaml).

## Acceptance Evidence

- **AE1''''':** E2E API: register → character → GET /external (больница+супермаркет) → POST /actions TRAVEL_EXTERNAL (тreatment при health<100) → тики до завершения → health вырос, RETURNED в events, транзакция travel+treatment, баланс уменьшился.
- **AE2''''':** keystone: headless M1-M4 пин-идентичность (npc_utility=false); visual_assets и external_contacts — единственные новые строки (контакты — контент), events без TRAVEL_EXTERNAL_*.
- **AE3''''':** покупки: поездка с items {food_canned: 2} → по возврату 2 предмета в инвентаре, транзакции на сумму, conservation-инвариант зелёный (с учётом external прихода).
- **AE4''''':** персистентность: контакты и сервисы живут между рестартами; повторная поездка валидна.
- **AE5''''':** негативы: неизвестный сервис 422; items на treatment 422; не хватает денег 422 («Insufficient funds»); не в порту → MOVE сначала (needs_move); чужие contacts 403.
- **AE6''''':** config sensitivity: каталог из конфига отражён в GET /external; npc_utility=true включает выбор поездки в utility (юнит с подменой needs) — флаг-тест.
