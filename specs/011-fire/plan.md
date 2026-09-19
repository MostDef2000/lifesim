# План имплементации: 011-fire

## Архитектура
- models: WorldObject.flammability (Float, default from config per type), WorldObject.burn_state (String default "intact").
- `app/simulation/fire.py`: ignite_object(session, object_id, day, settings) → burning+event; run_fire_phase(session, world_id, day, settings) — spread (P=ignition_chance×flammability, rng от seed+day+object), damage (health), burnout (→burned, изъятие, ITEM_DESTROYED).
- engine: фаза "fire" после weather (day-boundary).
- API: POST /admin/fire/ignite (require_role admin+moderator, audit), GET /fire/active.
- invariants: fire_integrity.
- EventType: OBJECT_BURNING (29), OBJECT_BURNED (30).

## Чанки
| Chunk | Содержание | Тесты |
|---|---|---|
| A | колонки + fire.py (ignite/spread/damage/burnout) + фаза + инвариант | test_chunk_a: AE1-AE5 unit/integration |
| B | API + audit + структурные пины (31 тип) | test_chunk_b: AE6 |
| C | README, tasks tick, полный suite | — |

## Риски
- Структурные пины событий 28→30 (m2/m4 closed sets) — обновить честно.
- Ратчуп burnout при bulk-шагах: days_burning из (day - ignited_day), idempotent.
- health-урон: интегрировать в штатную health-механику (не отдельная таблица).
