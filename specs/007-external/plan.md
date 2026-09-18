# План имплементации: 007-external

## Архитектура

- **Модели** (models.py): ExternalLocation, ExternalService, ExternalContact; schema 0.7.0.
- **Конфиг**: `ExternalConfig` (catalog: List[ExternalLocationSpec], travel_minutes=480, travel_cost=50, npc_utility=false, contact_probability=0.3); `ActionsConfig += TRAVEL_EXTERNAL: ActionParams` (duration 480 / max 960 / weight 0 — utility не выбирает при weight 0... проще: absence from utility registry = utility-словарь строится из ActionsConfig? — проверить выборку задач в choose_action; если registry автоматический, weight=0.0 исключает выбор).
- **Сид**: `app/world/seed_external.py::seed_external_world(session, settings, world_id, rng)` — вызов из `seed_world` (конец). Контакты: N имени из генератора (first/last pools) + birthplace-check по CharacterProfile.
- **Валидатор**: ветка `TRAVEL_EXTERNAL` в validators.py — сервис lookup, purpose/items/budget/port checks; params в задачу: {service_id, purpose, items, travel_cost, basket_cost, ext_location_name}.
- **Исполнитель**: ветка в lifecycle.complete_task: DEPARTED → transfers (economy.transfer char→government/port org account), purchase objects (create_object с owner_character_id), health update (CharacterHealth), RETURNED.
- **Object conservation (M1-инвариант)**: покупки создают новые объекты вне accounting категорий M1 → добавить в ожидания object_conservation категорию `external_purchase` = сумма qty из RETURNED событий (день X) — как daily supplies.
- **API**: /external + /characters/{cid}/contacts в app.py; actions — уже generic.

## Последовательность

| Chunk | Содержание | Тесты |
|---|---|---|
| A | schema 0.7.0 (+3), события 27, ExternalConfig+ActionsConfig.TRAVEL_EXTERNAL, seed_external_world, validator+executor, object_conservation accounting | test_chunk_a: сид каталога/контактов, validator-юниты, executor-юнит (деньги/инвентарь/health), conservation |
| B | /external, contacts API, actions-flow E2E через TestClient + тики, негативы | test_chunk_b |
| C | AE1-6, README, tasks tick, полный suite | полный suite + check.sh |

## Риски / решения

- **Utility-registry**: если choose_action перебирает ВСЕ ActionParams — TRAVEL_EXTERNAL попал бы в NPC-выбор; решение: `base_utility_weight: 0.0` + npc_utility=false гейт в ветке (если utility без флага — reject «External travel disabled for NPCs»); keystone AE2 подтверждает.
- **Порт-локация**: использовать существующую локацию типа `port` (seed_world уже создаёт? проверить; если нет — добавить в каталог сида острова, это внутренняя локация).
- **Object creation**: проверить сигнатуру create_object/transfer_object (inventory/__init__.py) — покупки создают объекты с owner_character_id в порт-локации? Нет: персонаж возвращается — объекты в его текущей локации.
- **Government account**: org type "government" существует? Проверить сида организаций; иначе transfer на port-org (business) аккаунт.
