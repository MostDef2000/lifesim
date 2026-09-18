# Задачи: 007-external

## Chunk A — Фундамент: схема, конфиг, сид, валидатор, исполнитель

- [x] T1: models: external_locations/external_services/external_contacts; schema 0.7.0; tests/db 36.
- [x] T2: события TRAVEL_EXTERNAL_DEPARTED/RETURNED (25→27); tests/m2/m4.
- [x] T3: ExternalConfig (catalog, travel_minutes/cost, npc_utility, contact_probability) + ActionsConfig.TRAVEL_EXTERNAL + default.yaml.
- [x] T4: seed_external_world: каталог из конфига; контакты по birthplace; детерминизм; wire в seed_world.
- [x] T5: validators: ветка TRAVEL_EXTERNAL (сервис/бюджет/порт/items/purpose; NPC-гейт npc_utility).
- [x] T6: lifecycle: complete_task TRAVEL_EXTERNAL (DEPARTED → transfers/purchase/heal → RETURNED); object_conservation: external_purchase accounting.
- [x] T7: tests/m7/test_chunk_a.py: сид (каталог, контакты детерминизм, игроки без контактов), validator-юниты, executor (деньги/инвентарь/health/события), conservation green.

## Chunk B — API

- [x] T8: GET /external; GET /characters/{cid}/contacts (403 чужому).
- [x] T9: POST /actions TRAVEL_EXTERNAL E2E (needs_move порт → поездка → возврат), негативы (неизвестный сервис/items на treatment/деньги/NPC-гейт).
- [x] T10: tests/m7/test_chunk_b.py.

## Chunk C — AE, документация, финал

- [x] T11: tests/m7/test_chunk_d.py: AE1''''' E2E (treatment), AE2''''' keystone, AE3''''' покупки+conservation, AE4''''' персистентность, AE5''''' негативы-сводка, AE6''''' config (каталог из конфига, npc_utility флаг).
- [x] T12: инвариант external_integrity в run_invariant_checks + негатив.
- [x] T13: README-инструкция, tasks tick, ruff + check.sh + полный suite, PR.
