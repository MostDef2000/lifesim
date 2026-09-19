# Спецификация функционала: 018 — Consent State (§31) и одежда (§28)

## Outcome

Основа социальных границ: явные разрешения на романтические взаимодействия (§31 — таблица `interaction_permissions`, категория romance; adult_interaction/private_scene — П4, исключены) и носимая одежда (§28 — слоты через `WorldObject.object_metadata`, портретный промпт учитывает worn-предметы). Всё под флагом `romance.enabled` (**default false** — П4-двойной гейт).

## Scope

- **Included**:
  - **Таблица `interaction_permissions`** (42-я): id, world_id, actor_character_id, target_character_id, interaction_category (romance), permission (requested|granted|declined), created_at, updated_at. Unique (actor, target, category). Schema 0.14.0.
  - **API**: POST `/interactions/romantic` {npc_id} — игрок → NPC: 403 при romance.enabled=false; deterministic NPC-решение (affection ≥ 40 → granted, иначе declined) сразу, row сохраняется. GET `/interactions/permissions` — список permission-строк игрока (входящие+исходящие). NPC-инициатива не реализуется (Excluded).
  - **Одежда (§28, MVP)**: WorldObject с object_type из `{jacket, boots, hat}` (уже в shop prices; добавляются в seed при отсутствии); object_metadata JSON: `{"slot": "upper_body|feet|head", "worn": bool}`. POST `/wear` {object_id} — toggle worn (только предметы владельца-игрока; 404/422 иначе). Слот не уникален (несколько надето — допустимо, MVP-упрощение зафиксировано).
  - **Портретный промпт**: `build_portrait_descriptor`/`build_prompt` добавляют `wearing: <список type по слотам>` ТОЛЬКО если есть worn-предметы у персонажа; при отсутствии — байт-идентичность (пины m6 не трогаются).
  - **Инвариант `consent_integrity`**: permission-строки только с валидными (actor≠target, category=romance, permission∈set); worn-предметы имеют slot.
- **Excluded**: adult_interaction/private_scene (П4); physical_contact; NPC-инициатива романтики; тело/поза/подробные слоты underwear/accessory (слоты в enum только); UI-виджеты одежды (только API+портрет); изменение Relationship-значений от consent (связь в будущих фазах).
- **Protected boundaries**:
  - П1: consent — отдельные строки БД, не выводится из текста LLM (§31 прямо требует).
  - П4: romance.enabled=false → API 403, таблица пуста, портреты байт-идентичны.
  - П2: одежда — только метаданные существующих WorldObject; новых таблиц кроме consent нет.

## Requirements
- **R1**: 42 таблицы (+interaction_permissions), schema 0.14.0, событий по-прежнему 41.
- **R2**: romance.enabled=false → POST /interactions/romantic 403; включён → deterministic решение по affection, row сохранена.
- **R3**: GET /interactions/permissions возвращает строки игрока (обе роли).
- **R4**: /wear toggle: только свои предметы, metadata обновляется (slot+worn), портрет содержит "wearing" при worn и байт-идентичен без него.
- **R5**: инвариант consent_integrity зелёный; пины честно (42/41/0.14.0).

## Non-Functional Requirements
- Performance: trivial (строки по API-запросу).
- Compatibility: schema 0.14.0; m6-пины промптов не меняются.
- Security: consent не выводим из LLM-текста (П1); worn — только владелец.

## Acceptance Evidence
- **AE1**: romance.enabled=false: POST /interactions/romantic → 403, таблица пуста, m6-промпты байт-идентичны.
- **AE2**: включён: affection≥40 → granted, <40 → declined; повторный запрос возвращает актуальное состояние (не дублирует row).
- **AE3**: GET /interactions/permissions видит и исходящие (actor), и входящие (target) строки.
- **AE4**: /wear на jacket: metadata {slot: upper_body, worn: true}, портрет-промпт содержит "wearing"; повторный /wear снимает; чужой предмет → 404.
- **AE5**: инвариант consent_integrity зелёный; пины 42 таблицы / schema 0.14.0.
