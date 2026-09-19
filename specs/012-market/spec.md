# Спецификация функционала: 012 — Marketplace/Construction/Destruction (§74-76)

## Outcome

Игроки и NPC изменяют мир экономически и физически: (§74+) рынок персонажей — выставление своих предметов на продажу и покупка чужих с переводом денег через штатный ledger; (§75) строительство — задача с потреблением ресурсов и созданием нового объекта на локации; (§76) разрушение — объект с condition 0 уничтожается, событие сохраняется навсегда.

## Scope

- **Included**:
  - Таблица `market_offers` (39-я): id, world_id, seller_character_id, object_id (WorldObject, quantity≥1), price (Int, >0), status (active|sold|cancelled), buyer_character_id (nullable), created_at (game_ts), closed_at (nullable). Unique: object_id+status=active (один активный оффер на объект). Schema 0.9.0 → **0.10.0**.
  - События **MARKET_LISTED** (31), **MARKET_SOLD** (32), **CONSTRUCTED** (33), **OBJECT_DESTROYED** (34). Итого 34 типа.
  - Рынок (§74+; shop-магазин с фиксированными ценами уже есть с M1): сервис `app/market/marketplace.py`: list_object (свой предмет → offer active), buy (проверки: offer active, покупатель ≠ продавец, баланс ≥ price; economy_transfer reason="MARKET_SALE"; transfer_object продавец→покупатель; offer.status=sold), cancel. Деньги персонажа — существующий Account(owner_type="character").
  - Строительство (§75): конфиг `ConstructionConfig.costs` — map object_type → {required_items: {wood_pile: 2}, days: 2}. Сервис create_construction_task: проверяет ресурсы в инвентаре персонажа (WorldObject owner=character, quantity), резервирует (списывает при старте), создаёт CharacterTask type="CONSTRUCT" с params {location_id, object_type, days}; прогресс штатным catch-up (M1); по завершении — новый WorldObject на локации (quantity=1, condition=100, owner=character) + CONSTRUCTED.
  - Разрушение (§76): дневная проверка (фаза fire/движка) — WorldObject condition≤0 и не burned → destroyed (quantity=0, burn_state="intact"→"destroyed"? — нет: отдельного статуса не вводим; quantity=0 + событие OBJECT_DESTROYED «навсегда»). Пожар (011) уже доводит до burned — теперь и через износ. Износ MVP: construction-объекты сохраняют condition; износ при использовании — excluded.
  - Инвариант `market_integrity`: sold-офферы имеют buyer + транзакцию MARKET_SALE; active-офферы ссылаются на живые объекты quantity≥1; нет двух active на один object.
- **Excluded**: supply/demand цены (§74 «позже» — уже учтено в M7-excluded); NPC-автоторговля; аукционы/ставки; износ предметов при использовании; ремонт; стройка нескольких уровней (многоэтажность); demolition-команда игрока (только естественное разрушение по condition).
- **Protected boundaries**:
  - П1: деньги — только через economy_transfer (ledger закрыт инвариантом M2); предметы — через transfer_object/штатное списание.
  - П2: кейстоун — без рыночных офферов/строек отчёты байт-идентичны (события 31-34 существуют, но не эмитятся в default headless).
  - П4: n/a.

## Requirements
- **R1**: 39 таблиц, schema 0.10.0, 34 события (пины обновить честно).
- **R2**: market: list/cancel/buy — атомарно (flush+commit), оффер не активен после продажи.
- **R3**: buy: недостаток денег → 402/422-ошибка без изменений состояния (транзакция откатывается).
- **R4**: construction: ресурсы списываются при завершении (или резерв при старте — MVP: списание при старте, возврат при провале); завершение создаёт объект на указанной локации персонажа.
- **R5**: destruction: daily check; событие сохраняется навсегда (журнал не чистится).
- **R6**: API: POST /market/offers {object_id, price}; GET /market/offers?status=active; POST /market/offers/{id}/buy; POST /market/offers/{id}/cancel; POST /build {object_type} (на текущей локации персонажа). Все auth.
- **R7**: инвариант market_integrity зелёный.

## Non-Functional Requirements
- Performance: офферы индексируются по (world_id, status).
- Compatibility: schema 0.10.0; события 34; UI-интеграция рынка — excluded (API-первый, П2 headless).
- Security: list/cancel только владелец оффера; buy — любой аутентифицированный игрок с персонажем.

## Acceptance Evidence
- **AE1**: list→buy: деньги перешли (балансы до/после), предмет сменил владельца, offer=sold, события MARKET_LISTED+MARKET_SOLD.
- **AE2**: buy без денег → ошибка, состояние не изменилось (ledger-инвариант зелёный).
- **AE3**: cancel только продавцом.
- **AE4**: construction: с 2 wood_pile + days=2 → через 2 дня новый объект на локации, ресурсы списаны, CONSTRUCTED.
- **AE5**: destruction: объект с condition=0 после дневной фазы → quantity=0, OBJECT_DESTROYED в журнале.
- **AE6**: кейстоун: default headless — событий 31-34 нет, векторы m2-m4 неизменны.
- **AE7**: инвариант market_integrity зелёный после сценариев AE1-AE3.
