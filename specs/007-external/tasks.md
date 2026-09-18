# Задачи: 007-external

## Chunk A — Фундамент: схема, конфиг, сид, валидатор, исполнитель

- [ ] T1: models: external_locations/external_services/external_contacts; schema 0.7.0; tests/db 36.
- [ ] T2: события TRAVEL_EXTERNAL_DEPARTED/RETURNED (25→27); tests/m2/m4.
- [ ] T3: ExternalConfig (catalog, travel_minutes/cost, npc_utility, contact_probability) + ActionsConfig.TRAVEL_EXTERNAL + default.yaml.
- [ ] T4: seed_external_world: каталог из конфига; контакты по birthplace; детерминизм; wire в seed_world.
- [ ] T5: validators: ветка TRAVEL_EXTERNAL (сервис/бюджет/порт/items/purpose; NPC-гейт npc_utility).
- [ ] T6: lifecycle: complete_task TRAVEL_EXTERNAL (DEPARTED → transfers/purchase/heal → RETURNED); object_conservation: external_purchase accounting.
- [ ] T7: tests/m7/test_chunk_a.py: сид (каталог, контакты детерминизм, игроки без контактов), validator-юниты, executor (деньги/инвентарь/health/события), conservation green.

## Chunk B — API

- [ ] T8: GET /external; GET /characters/{cid}/contacts (403 чужому).
- [ ] T9: POST /actions TRAVEL_EXTERNAL E2E (needs_move порт → поездка → возврат), негативы (неизвестный сервис/items на treatment/деньги/NPC-гейт).
- [ ] T10: tests/m7/test_chunk_b.py.

## Chunk C — AE, документация, финал

- [ ] T11: tests/m7/test_chunk_d.py: AE1''''' E2E (treatment), AE2''''' keystone, AE3''''' покупки+conservation, AE4''''' персистентность, AE5''''' негативы-сводка, AE6''''' config (каталог из конфига, npc_utility флаг).
- [ ] T12: инвариант external_integrity в run_invariant_checks + негатив.
- [ ] T13: README-инструкция, tasks tick, ruff + check.sh + полный suite, PR.
