# План имплементации: 014-player-ux

## Архитектура
- models: Character.looks (String(500)), Character.biography (Text); schema 0.12.0.
- player.py: create_player_character(..., looks=None, biography=None); валидация длины на уровне API (CharacterIn-проверка → 422).
- app.py: CharacterIn += looks/biography; /me += поля; проброс в create_player_character.
- UI app.js: viewCreateCharacter += два textarea (Внешность/Биография) → body POST; viewWorld/renderWorld += портрет-блок (canonical при загрузке; кнопка генерации; blob→objectURL).
- Пины: только schema-строки 0.11.0→0.12.0 (таблицы/события не меняются).

## Чанки
| Chunk | Содержание | Тесты |
|---|---|---|
| A | backend: колонки+API+`/me` | AE1-AE2 |
| B | UI: форма+портрет | AE3-AE4 |
| C | пины/README/tasks/полный suite | AE2 |

## Риски
- /me-пин в m2/m9 может фиксировать набор полей — обновить честно.
- m9-UI-тесты структурные — дополнить в m14, не трогая m9.
