# План имплементации: 013-external2

## Архитектура
- Config: ExternalConfig += npc_utility (есть, false), supply_demand: bool=false; MessageConfig не нужен.
- models: ExternalService.price_multiplier (Float, 1.0); Message (40-я таблица, индекс to+world); schema 0.11.0; EventType.MESSAGE_SENT (35).
- `app/external/npc_trips.py`: run_npc_trip_phase(session, world_id, day, settings) — флаг+health+cooldown (последний RETURNED персонажа) → enqueue TRAVEL_EXTERNAL (treatment, как player-flow M7).
- Supply/demand: в fire/weather-фазный блок движка — update_service_multipliers (события вчерашнего дня); в lifecycle TRAVEL_EXTERNAL-ветку — цена ×multiplier (payload multiplier).
- dialogue.build_context: += npc_recent_trip (последний RETURNED); fallback_reply дополнение.
- Messages: `app/social/messages.py` (send/inbox/guard/auto-reply), API /messages*.
- invariants: messages_integrity.

## Чанки
| Chunk | Содержание | Тесты |
|---|---|---|
| A | npc_trips + supply/demand (колонка+фаза+цена) | AE1-AE3 |
| B | dialogue context + messages (модель+API+авто-ответ) + инвариант | AE4-AE6 |
| C | пины (40/35/0.11.0), README, tasks, полный suite | AE2 |

## Риски
- TRAVEL_EXTERNAL-ветка читает basket_cost из params — NPC-ветка формирует params как player-flow (service_id, travel_cost, basket_cost=цена лечения×multiplier).
- Пины 34→35 событий, 39→40 таблиц, 0.10.0→0.11.0.
- build_context-изменение: dialogue-тесты M4/M9 могут пинить контекст — проверить и обновить честно.
