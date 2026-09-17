# План имплементации: 006-flux

## Архитектура

- **Пакет `backend/app/visual/`**: `transports.py` (протокол + Stub/Http + build_transport), `descriptor.py` (Scene Descriptor §67 + prompt builder §R3), `store.py` (файловый asset store), `__init__.py`. Чистые функции поверх сессии — без сайд-эффектов до записи.
- **API**: новые роуты внутри `app/api/app.py` (create_app) за гейтом `settings.visual.enabled` (503 при выключенном). Регистрация всегда, исполнение — гейт.
- **Модель**: `VisualAsset` в `models.py` (+1 таблица, schema 0.6.0); событие `VISUAL_ASSET_CREATED` в events.py (25-й).
- **Seed-стратегия**: детерминированный hash(world_id, cid, asset_type, поколение) → int seed → в row; воспроизводимость по метаданным.
- **Storage**: `settings.visual.storage_dir` (отн. путь от cwd, дефолт `data/visual_assets`), файлы `{asset_id}.png`, в БД — относительный storage_path (traversal-safe).

## Последовательность

| Chunk | Содержание | Тесты |
|---|---|---|
| A | schema 0.6.0, событие 25, config `visual:`, transports (stub bytes, http контракт), descriptor+prompt, store | test_chunk_a: юнит descriptor/prompt/stub-детерминизм/store-sanitize; структурные 33 таблицы/25 событий |
| B | /visual/* роуты: portraits (reuse §70), scenes (references), canonical, file, метаданные; ownership | test_chunk_b: полный CRUD-цикл, canon promote/reuse, 401/403/404/422/503 |
| C | AE1''''-AE6''' (E2E, keystone, детерминизм, персистентность, reuse-без-генерации, config), README, tasks tick, полный suite | полный suite + check.sh |

## Риски / решения

- **Stub PNG**: минимальный валидный PNG (заголовок + IHDR + IDAT zlib(raw RGBA мелкий)) — байты детерминированы содержимым; без внешних lib.
- **Seed из hash**: стабильный — `int.from_bytes(sha256(...)[:8])` (не Python hash — рандомизирован между процессами).
- **Reuse-проверка transport**: в тестах transport подменяется через `app.state.flux_transport_factory` (monkeypatch) со счётчиком вызовов.
- **П2**: `vl1 simulate` не импортирует visual (только api/app.py импортирует внутри create_app — и то лениво в роут-хендлерах).
