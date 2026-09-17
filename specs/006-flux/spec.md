# Спецификация функционала: M6 — Flux (§105): портреты, сцены, хранилище, Visual Canon

## Outcome

Мир получает визуальный слой: генерация портретов персонажей и сцен локаций через Flux/LoRA (§15, §66), структурированное описание сцены (Scene Descriptor §67), реестр визуальных ассетов `visual_assets` (§68), канонические референсы (§69: лицо NPC, дом, интерьер) и переиспользование ассетов до генерации (§70). Headless-мир M1-M5 не изменён ни байтом: визуализация — аддитивный слой за гейтом `visual.enabled` (аналог П2); Flux не принимает игровых решений и не мутирует состояние мира (аналог П1).

## Scope

- **Included**:
  - Таблица `visual_assets` (§68): id, world_id, asset_type (`portrait|scene`), character_id nullable, location_id nullable, object_id nullable, scene_descriptor JSON (§67 — хранится структурированное описание, не только prompt), prompt, storage_path, seed, model, lora, canonical (bool), created_at. Schema 0.5.0 → 0.6.0 (33 таблицы).
  - Событие `VISUAL_ASSET_CREATED` (24 → 25, closed set) — audit при регистрации ассета.
  - Конфиг `visual:` (enabled: false, transport: stub|http, base_url, model, lora, storage_dir `data/visual_assets`, image_size, timeout_sec).
  - Транспорты (`backend/app/visual/transports.py`): протокол `FluxTransport.generate(prompt, seed, params) -> bytes`; `StubFluxTransport` — детерминированный PNG-плейсхолдер из (seed, sha256(prompt)) — CI-безопасный, без сети; `HttpFluxTransport` — POST на внутренний API Flux-сервера (контракт-тест с monkeypatch). `build_transport(settings.visual)`.
  - Scene Descriptor (§67): детерминированный сборщик из БД — {location{name,type}, characters[id,name], objects[type], weather, time{day,hour}, camera, event} — чистое чтение.
  - Prompt builder: descriptor → детерминированный текстовый промпт (портрет: «adult fictional character», П4; сцена: место, персонажи, объекты, время суток).
  - Asset store: сохранение/чтение байтов на локальный filesystem (`storage_dir/`, §5 «первоначально локальный filesystem»), санитизация путей.
  - Asset Reuse (§70): перед генерацией — поиск: портрет — существующий canonical-портрет персонажа (возврат без генерации); сцена — генерация всегда, но canonical-референсы персонажей сцены попадают в descriptor/prompt как reference.
  - Visual Canon (§69): `POST /visual/assets/{id}/canonical {canonical: bool}` — promote/demote; canonical=true для лица NPC/дома и т.п. — последующие генерации используют как reference.
  - REST: `POST /visual/portraits/{character_id}` (201, reuse→200), `POST /visual/scenes` {location_id, event_id?} → 201, `GET /visual/assets/{id}` (метаданные), `GET /visual/assets/{id}/file` (bytes, image/png), `GET /visual/characters/{character_id}/portrait` (canonical или 404). Auth: create — владелец персонажа (портрет) / любой аутентифицированный (сцена); чтение — аутентифицированные.
  - CLI: без изменений (`vl1 serve` поднимает и visual-роуты; headless не импортирует visual-модуль).
- **Excluded**:
  - Weather-система (§71), Fire (§72), Marketplace/Construction (§74-76) — descriptor.weather = детерминированный стаб из конфига («clear»).
  - Реальный Flux-инференс в CI (stub детерминирован; http-транспорт — контракт с monkeypatch, реальный сервер — конфиг владельца).
  - Веб-клиент (§77-78) — только API; LoRA-тренировка; S3/MinIO (локальный FS); вариантные генерации (портрет в 4 ракурсах); video.
  - Автогенерация визуала по событиям мира (поллеры/хуки) — только on-demand по API.
- **Protected boundaries**:
  - П1-аналог: визуальный контур читает мир и пишет только в visual_assets + storage_dir; ноль мутаций игровых таблиц.
  - П2-аналог: `visual.enabled: false` (дефолт) — роуты отвечают 503, transport не строится, headless-путь не тронут; keystone AE2'''' — байт-идентичность M1-M4.
  - П4: descriptor включает только живых совершеннолетних персонажей (18+ в мире гарантировано генератором); промпты фиксируют «adult fictional character» — никаких реальных людей.

## Requirements

- **R1: Гейты**. Все visual-роуты при `settings.visual.enabled=false` → 503 {detail}; фабрика transport строится лениво. Симуляция/CLI M1-M5 не затронуты.
- **R2: Scene Descriptor** (§67). `build_scene_descriptor(session, world_id, location_id, event_id=None, camera="wide")` → dict: location (id/name/type), characters (живые, находящиеся в локации: id/имя/пол/возраст), objects (WorldObject в локации: object_type), weather (settings.visual.default_weather), time (day/hour из WorldClock), camera, event (тип события + actor, если задан). Порядок списков — по id (детерминизм).
- **R3: Prompt builder**. Детерминированный шаблон: портрет — «Portrait of {name}, {age}-year-old {sex} adult fictional character, ...»; сцена — «{location_type} {location_name}, characters: [...], objects: [...], {weather} weather, {time_of_day} ...». Один и тот же descriptor → один и тот же prompt (байт-в-байт).
- **R4: Транспорты**. Протокол: `generate(prompt: str, seed: int, size: str) -> bytes`. Stub: bytes детерминированы (seed, sha256(prompt)) — одинаковый вход → одинаковый файл; Http: POST {base_url}/generate json {prompt, seed, size, model, lora} → response bytes; ошибки сети → ValueError("flux unavailable"). build_transport: stub|http.
- **R5: Генерация портрета**. `POST /visual/portraits/{cid}`: персонаж существует и жив (404/422); descriptor портрета (камера "portrait"); если есть canonical-портрет → 200 {asset, reused: true} без генерации (§70); иначе transport.generate(prompt, seed=settings-детерминированный (hash(world_id+cid+asset_type)), size) → файл → строка visual_assets (canonical=false) + событие → 201 {asset_id, reused: false}. Повторный вызов без канона → новая генерация (новый seed — hash(+existing_count)).
- **R6: Генерация сцены**. `POST /visual/scenes` {location_id, event_id?}: локация существует (404); descriptor сцены (R2); canonical-портреты персонажей сцены → в descriptor.references (§70); transport.generate → файл → visual_assets (character_id=NULL, location_id) + событие → 201.
- **R7: Канон** (§69). `POST /visual/assets/{id}/canonical {canonical}` — меняет флаг + commit. Канонический портрет используется как reference в последующих сценах с этим персонажем и не перегенерируется (R5).
- **R8: Чтение**. `GET /visual/assets/{id}` — метаданные (без байтов); `GET /visual/assets/{id}/file` — image/png из storage (404 если файла нет); `GET /visual/characters/{cid}/portrait` — canonical-портрет (404 если нет).
- **R9: Контракт событий** (24 → 25): `VISUAL_ASSET_CREATED` (actor_id = character или NULL; payload {asset_id, asset_type, canonical, storage_path}). Consumer: WS-поток M5, отчёт/тесты.
- **R10: Инвариант** (1 новый, за гейтом enabled): `visual_asset_integrity` — asset_type ∈ {portrait, scene}; canonical только у существующих строк; character_id/location_id валидны (существуют) когда не NULL; storage_path уникален.
- **R11: Ownership/безопасность**. Портрет: только владелец персонажа (или создание по решению владельца); сцена: любой аутентифицированный; файл-роут защищён auth; пути storage — только внутри storage_dir (защита от traversal: storage_path хранится относительным).

## Non-Functional Requirements

- **Determinism**: stub-байты и промпты — чистые функции входа; одинаковый запрос → одинаковый asset (кроме repeat-генераций R5 с другим seed).
- **Reliability**: отказ Flux-сервера → 502 {detail:"flux unavailable"} — мир и API продолжают работать (аналог П2).
- **Performance**: генерация — on-demand; файлы ≤ нескольких сотен КБ (stub ~1 КБ); descriptor — ≤ 5 запросов к БД.
- **Operability**: storage_dir создаётся при первом ассете; метаданные полностью описывают ассет (воспроизводимость по seed+prompt).
- **Compatibility**: schema 0.6.0 (create_all); события 25; CLI-флаги неизменны; отчёт без ключа visual.

## Acceptance Evidence

- **AE1''''**: E2E через TestClient (visual.enabled=true, stub): login → портрет NPC-владельца… точнее: игрок создаёт персонажа → портрет своего персонажа (201, reused=false) → повтор (201, новая генерация) → promote canonical → повтор (200, reused=true, байты идентичны) → сцена локации (201, descriptor содержит персонажа и objects, references содержит canonical-портрет) → GET file → PNG-магия \x89PNG → метаданные → 503 при enabled=false.
- **AE2''''** (keystone): headless-прогоны M1-M4 — events_by_type и пины байт-идентичны; таблица visual_assets пуста; в отчёте нет ключа visual; инвариант visual_asset_integrity зелёный (тривиально пуст).
- **AE3''''**: детерминизм: одинаковые (prompt, seed) → байт-идентичные файлы; разные seed → разные; descriptor одной сцены в двух прогонах идентичен.
- **AE4''''**: персистентность: ассет создан → новое приложение на той же БД+storage → GET file отдаёт те же байты; метаданные совпадают.
- **AE5''''**: reuse: после promote canonical — POST portraits возвращает reused=true без обращения к transport (transport подменён счётчиком вызовов).
- **AE6''''**: config sensitivity: transport=http → build_transport возвращает HttpFluxTransport (контракт с monkeypatch: POST ушёл, bytes вернулись); ошибка сети → 502; visual.enabled=false → 503 на всех роутах.
