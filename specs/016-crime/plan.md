# План имплементации: 016-crime

## Архитектура
- models: Crime (41-я, Index ix_crimes_world_status), Character.prison_until_day; schema 0.13.0; EventType 36-40.
- config: CrimeConfig(enabled=false, theft_hunger_threshold=25, theft_money_threshold=50, theft_chance=0.15, vandalism_chance=0.05, detection_base=0.4, fine_theft=200, fine_vandalism=150, prison_days=7, prison_needs_decay_multiplier=0.3).
- seed_world: при crime.enabled — org «Полиция Рейнеке» (type=security, локация settlement) + аккаунт (5000) + leader = первый взрослый NPC (после generate_population; seed_world идёт до населения → leader назначается отдельным шагом в generate_population? проще: leader=None, резолвер берёт первого живого NPC как officer). Упрощение: officer = org.leader or первый NPC.
- app/crime/crime.py: run_crime_phase (commit+witness), run_police_phase (resolution+release).
- engine: фазы crime, police — ПОСЛЕ external_followups, ПЕРЕД needs (арест дня действует сразу).
- progress_tick: imprisonment guard в начале итерации персонажа.
- invariants: crime_integrity.

## Чанки
| Chunk | Содержание | Тесты |
|---|---|---|
| A | schema+config+seed+commit/witness | AE1-AE3 |
| B | police resolution+prison+инвариант | AE4-AE5 |
| C | пины (41/40/0.13.0), README, suite | AE1 |

## Риски
- progress_tick-guard: не сломать DIRECT/GUIDED-игроков (guard до control-mode веток).
- activate() не трогаем (PRISON — не задача).
- Пины: 40→41 таблиц, 35→40 событий, 0.12.0→0.13.0.
