# Спецификация функционала: 014 — Player/UX (биография, внешность, портрет)

## Outcome

§27: при создании персонажа игрок задаёт внешность и биографию (опционально, сохраняются в БД, видны в профиле). Портрет в UI: кнопка «Сгенерировать портрет» вызывает штатный M6-конвейер и показывает PNG (graceful деградация при visual.enabled=false).

## Scope

- **Included**:
  - **Биография/внешность (§27)**: колонки `characters.looks` (String 500, nullable), `characters.biography` (Text, nullable). `CharacterIn` += `looks`/`biography` (optional; strip; ≤500/≤2000 иначе 422). `create_player_character` += параметры. `/me` возвращает looks/biography. Форма создания в UI — два textarea. Schema 0.12.0.
  - **Портрет в UI**: блок «Портрет» в виде мира: при загрузке — GET /visual/characters/{cid}/portrait (200 → показать, 404/403 → скрыть блок ошибки, показать кнопку); кнопка → POST /visual/portraits/{cid} → asset.id → GET /visual/assets/{id}/file (blob через fetch с Authorization → objectURL → <img>). Повторный клик — reuse-конвейер M6 (canonical) — бэкенд сам возвращает существующий.
  - Инвариант не нужен (колонки nullable, без событийного следа).
- **Excluded**: портреты NPC в UI; редактирование looks/bio после создания; загрузка пользовательских изображений; LLM-контекст из биографии (П2).
- **Protected boundaries**:
  - П1: n/a (без экономики).
  - П2: visual.enabled=false default → UI-флоу деградирует без ошибок; колонки nullable → seed/векторы не меняются; schema-пин 0.11.0→0.12.0 честно.
  - П4: n/a.

## Requirements
- **R1**: schema 0.12.0 (40 таблиц, 35 событий — пины таблиц/событий не меняются).
- **R2**: POST /characters с looks/biography сохраняет; без — null; >лимиты → 422.
- **R3**: /me возвращает looks/biography.
- **R4**: UI: форма принимает внешность/биографию; портрет-блок: показ canonical, генерация, graceful офф.

## Non-Functional Requirements
- Performance: n/a.
- Compatibility: 0.12.0.
- Security: blob-fetch с Authorization (img src не может нести заголовок).

## Acceptance Evidence
- **AE1**: создание с looks/bio → в БД и /me; лимиты 422.
- **AE2**: кейстоун: headless-векторы байт-идентичны (nullable-колонки).
- **AE3**: UI-строки: форма содержит textarea-подписи «Внешность»/«Биография»; JS содержит вызовы /visual/portraits и objectURL-флоу (структурные тесты m14, как m9).
- **AE4**: visual.enabled=false → POST /visual/portraits → 403, UI-контракт (обработчик ошибки) присутствует в JS.
