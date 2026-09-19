# План имплементации: 012-market

## Архитектура
- models: MarketOffer (39-я таблица, индекс world+status); schema 0.10.0; EventType 31-34.
- `app/market/marketplace.py`: list_object, cancel_offer, buy_offer (economy_transfer MARKET_SALE + transfer_object), create_construction_task (резерв ресурсов, CharacterTask CONSTRUCT), complete_construction (вызывается из lifecycle по завершении задачи — хук в аналоге PURCHASE-ветки), daily_destruction_check.
- engine: destruction check в дневной фазе.
- lifecycle: ветка task_type=="CONSTRUCT" (по образцу PURCHASE: complete → списание/создание объекта/CONSTRUCTED).
- API: /market/*, /build (в closure после current_user).
- invariants: market_integrity.

## Чанки
| Chunk | Содержание | Тесты |
|---|---|---|
| A | schema 0.10.0 + события + marketplace.py (list/buy/cancel) | AE1-AE3, AE7 |
| B | construction (lifecycle CONSTRUCT) + destruction check + API | AE4-AE5, R6 |
| C | пины 34, README, tasks, полный suite | AE6 |

## Риски
- lifecycle CONSTRUCT-ветка: точный паттерн PURCHASE-ветки (fail_task при недостатке ресурсов).
- transfer_object для рынка: предмет у продавца в инвентаре → владелец меняется на покупателя (проверить сигнатуру transfer_object).
- Пины m2/m4: события 30→34.
