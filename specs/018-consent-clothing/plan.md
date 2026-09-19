# План имплементации: 018-consent-clothing

## Архитектура
- models: InteractionPermission (42-я, Unique actor+target+category, Check actor!=target); schema 0.14.0. Событий НЕ добавляем (41 остаётся).
- config: RomanceConfig(enabled=false) → settings.romance.
- app/social/consent.py: request_romantic(player_char, npc, settings) → deterministic (affection≥40 → granted/declined), upsert по unique-паре.
- API: POST /interactions/romantic, GET /interactions/permissions, POST /wear (в app.py рядом с characters-роутами).
- Одежда: WorldObject subtypes jacket/boots/hat — slot-map в app/social/clothing.py (JACKET→upper_body, BOOTS→feet, HAT→head); /wear toggle object_metadata JSON.
- Портрет: build_portrait_descriptor принимает session+character_id (или список worn) → хвост "wearing: ..."; без worn — строка байт-идентична (m6-пины).

## Чанки
| Chunk | Содержание | Тесты |
|---|---|---|
| A | models+config+consent.py+API | AE1-AE3 |
| B | clothing /wear+портрет | AE4 |
| C | инвариант+пины (42/0.14.0), README, suite | AE5, AE1 |

## Риски
- m6-пин промпта: менять build_prompt только через опциональный параметр worn_items=None (default → байт-идентичность).
- object_metadata JSON parse: предметы без metadata-ключей → default {"slot": None, "worn": False}.
- Relationship CHECK: consent-таблица не связана с relationships CHECK (own unique).
