# Спецификация функционала: 013 — External follow-ups (M7-наследование)

## Outcome

Закрыты четыре follow-up'а M7: (1) NPC сами ездят на материк по utility-нужде (флаг npc_utility, кейстоун-гейт); (2) supply/demand цены внешних сервисов; (3) память M4 о поездках в диалогах; (4) письма/телефон — контакты как канал связи.

## Scope

- **Included**:
  - **NPC-utility поездок** (флаг `external.npc_utility`, default **false** — П2): дневная фаза движка — NPC с health<40 и без кулдауна (30 дней) берёт TRAVEL_EXTERNAL-задачу на treatment-сервис ближайшей внешней локации (детерминированный выбор по id). Возврат — штатный конвейер M7 (DEPARTED→лечение→RETURNED).
  - **Supply/demand** (флаг `external.supply_demand`, default **false**): колонка `price_multiplier` (Float, default 1.0) у external_services; ежедневная фаза: multiplier += 0.02×покупки_вчера, затем decay ×0.95 к 1.0, cap ×1.5, floor ×0.8; покупка платит `round(price × multiplier)` (payload события включает multiplier). Schema 0.11.0 (колонка).
  - **Память о поездках** (без флага — чистое расширение контекста): `build_context` добавляет `npc_recent_trip` — summary последнего TRAVEL_EXTERNAL_RETURNED персонажа-NPC (destination/purpose/healed) из журнала; fallback-reply при наличии — добавляет фразу про поездку. LLM-контекст (§65) получает те же данные.
  - **Письма/телефон**: таблица `messages` (40-я): id, world_id, from_character_id, to_character_id, body (Text ≤2000), created_at, read_at (nullable). API (auth, персонаж игрока): POST /messages {to_character_id, body}, GET /messages (входящие, ?mark_read=true), GET /messages/sent. Гвард: получатель — живой персонаж, с которым есть Relationship ИЛИ co-location, иначе 403. NPC отвечает детерминированным шаблоном (по affection, как dialogue-fallback) сразу же — упрощение MVP зафиксировано. Событие **MESSAGE_SENT** (35). Schema 0.11.0.
  - Инвариант `messages_integrity`: body непустой ≤2000; from/to живые существующие персонажи мира.
- **Excluded**: NPC→NPC переписка (авто-ответы только NPC→игроку); доставка с задержкой (MVP: мгновенный авто-ответ); вложения/деньги в письмах; телефонные звонки как диалог-сессия; supply/demand на внутреннем shop-рынке (только внешние сервисы).
- **Protected boundaries**:
  - П1: поездки NPC — штатные TRAVEL_EXTERNAL-задачи; лечение — штатное; деньги — ledger.
  - П2: **кейстоун**: оба флага false (default) → ни одного нового события/колонки-эффекта; векторы m2-m4 байт-идентичны; build_context-дополнение не влияет на dialogue-контракт (fallback-строки детерминированы).
  - П4: n/a.

## Requirements
- **R1**: 40 таблиц, schema 0.11.0, 35 событий (пины честно).
- **R2**: npc_utility=true → health<40 NPC едет (детерминированно), лечится, возвращается; кулдаун 30 дней.
- **R3**: supply_demand=true → цена покупки = price×multiplier, multiplier растёт с покупками, затухает к 1.0, [0.8, 1.5].
- **R4**: build_context.npc_recent_trip присутствует при наличии RETURNED; отсутствует — ключа нет (контракт назад совместим).
- **R5**: письма: send/inbox/sent; гвард relationship|co-location; авто-ответ NPC детерминирован.
- **R6**: инвариант messages_integrity зелёный.

## Non-Functional Requirements
- Performance: дневные фазы O(NPC)/O(services); messages индекс (world_id, to_character_id).
- Compatibility: schema 0.11.0; события 35.
- Security: /messages только для персонажа владельца токена.

## Acceptance Evidence
- **AE1**: npc_utility=true: NPC с health<40 → через travel_minutes+duration возвращается вылеченным (DEPARTED+RETURNED), повтор не раньше кулдауна.
- **AE2**: кейстоун: оба флага false — headless векторы неизменны (AE6-style).
- **AE3**: supply_demand=true: после K покупок multiplier>1, цена растёт; без покупок decay к 1.0.
- **AE4**: build_context NPC, вернувшегося из поездки, содержит npc_recent_trip; NPC без поездок — не содержит.
- **AE5**: письма: send→inbox→авто-ответ NPC; гвард 403 на незнакомца; event MESSAGE_SENT.
- **AE6**: инвариант messages_integrity зелёный.
