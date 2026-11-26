# LifeSim / Reineke — LOD2 + LOD3 арт-проход (#308)

**Статус: artwork LOD2/LOD3 подтверждён владельцем 2026-11-22 как вход для реализации. Загрузчик тайлов, 28 тайлов (LOD1 8 + LOD2 12 + LOD3 8), browser QA и perf-квитанции интегрированы в PR #352; финальная географическая/дизайн-приёмка остаётся шагом владельца после merge.**

Этот пакет показывает два разных слоя детализации поверх известных размеров/координат острова. Файлы `docs/design/map-source/lod2-region.webp` и `lod3-settlement.png` — отдельные растрированные изображения, а не нарезанная `world-island.jpg`. LOD2 создан как концептуальный арт-проход; затем известные POI в сцене приблизительно совмещены с `coords.json` → `image_space` (ручное определение визуальных центров строений и локальная гладкая деформация). LOD3 с теми же world-space bounds создан из LOD2 с **отдельным high-resolution pass**: добавлены микродетали поверхностей и крыш; это НЕ второе независимо созданное изображение по проверенным чертежам.

Обратите внимание: **архитектура зданий, мелкие тропы и предметы на рисунке могут быть художественно выдуманы**. Они не основаны на достоверных footprints дорог/зданий из OSM и ещё не прошли приёмку автора. Это предупреждение относится к непроверенной выдуманной геометрии, ожидающей финальной приёмки, а не к утверждённому владельцем арту как входу для реализации. До проверки геометрии, coast/terrain continuity, фактического POI-to-pixel mapping и утверждения дизайна эти изображения не использовать как production truth. NPC, события, игрок, строительство и действия не запекаются в terrain.

## Содержимое

- `docs/design/map-source/region-concept-input.webp` — неизменённый концепт-референс, из которого формируется LOD2;
- `docs/design/map-source/lod2-region.webp` — Region / 1920×1080 / ROI `[320,240,960,540]` в координатах master 1600×900;
- `docs/design/map-source/lod3-settlement.webp` — Settlement / 1920×960 / ROI `[480,330,480,240]` в тех же координатах;
- `docs/design/map-tiles.json` — целевая сборочная спецификация 3 LOD;
- `backend/app/web/static/map/tiles/manifest.json` + 28 `.webp` — сборка из утверждённых владельцем исходников: LOD1 8 + LOD2 12 + LOD3 8 = 28, все слои `available=true`; loader интегрирован в `backend/app/web/app.js` (PR #352);
- `scripts/build_map_tiles.py` — нарезка master/LOD в 512×512 WebP с bleed 2 px;
- `scripts/make_lod_art.py` — воспроизводимая регистрация LOD2 и отдельный детализационный проход LOD3, Pillow/Numpy/OpenCV/SciPy;
- `scripts/test_build_map_tiles.py`, `scripts/test_lod_art.py` — 4 теста;
- `docs/design/map-preview.html` — debug-вьюер на Canvas (LOD переключение, viewport culling, pan, wheel zoom, точки, счётчик видимых/загруженных тайлов);
- `lod2-lod3-comparison.jpg`, `lod2-preview.jpg`, `lod3-preview.jpg` — скриншоты арта без игровых NPC/маркерных данных;
- `artwork/registration.json` — выбранные контрольные точки и предупреждения.

## Как проверить без игры

В корне распакованного пакета:

```bash
python -m http.server 8000
```

Открыть `http://localhost:8000/docs/design/map-preview.html`, переключать LOD2 ↔ LOD3, масштабировать колёсиком и перетаскивать карту. Вьюер грузит только тайлы, пересекающие viewport. Новый loader уже интегрирован в `backend/app/web/app.js` (PR #352); companion browser QA зелёный на 390×844 и 1440×900.

## Как интегрировать в кодовый репозиторий **после ревью**

Скопировать `docs/design/map-source`, `docs/design/map-tiles.json`, `scripts/build_map_tiles.py` и тесты в соответствующие пути репозитория; в реальном репозитории `backend/app/web/static/map/base.png` уже есть. Установить build-time deps:

```bash
python -m pip install Pillow numpy opencv-python-headless scipy
# restore the approved PNG masters from tracked parts and SHA-256-verify them
python scripts/materialize_map_art.py
python scripts/build_map_tiles.py --spec docs/design/map-tiles.json --output backend/app/web/static/map/tiles --root .
python -m unittest discover -s scripts -p 'test_*map*.py'
```

При наличии `base.png` ожидаем **LOD1 8 + LOD2 12 + LOD3 8 = 28 WebP-тайлов**. Не забыть видимую атрибуцию `© OpenStreetMap contributors` вне terrain. Путь к манифесту: `/static/static/map/tiles/manifest.json` (при текущем static mount).

В этом пакете `local-build-spec.json` содержит единственное изменение относительно production spec: для отсутствующего локально LOD1 `required=false`. Превью рассчитано на это честное состояние, не создаёт фальшивый LOD1.

## Что НЕ сделано

1. Фактическое географическое наложение на master `base.png` не проверено: локальный контейнер не получил master-файл. Художественная основа утверждена владельцем, но соответствие крыш/дорог географии ещё не проверено. Совпадение POI по координатам не доказывает совпадения контуров крыш/дорог.
2. WebP-tile loader интегрирован в `app.js` (28 тайлов: LOD1 8 + LOD2 12 + LOD3 8, PR #352), но статический JPEG остаётся fallback-андерлеем; его удаление и полная замена не входят в этот PR.
3. LOD3 — детализированный производный растр, а не полностью независимо нарисованный/проверенный поселок, что **не удовлетворяет финальному DoD #308**.
4. Companion browser QA на 390×844 / 1440×900 зелёный (PR #352); измерения FPS/памяти и сетевые замеры в живой игре остаются открытыми.

## Результат и решение

Этот пакет — deliverable для дизайн-приёмки и технического spike. Не мерджить в качестве production-LOD до геопроверки художником, замены неточных объектов и интеграции culling loader. Текущее назначение — облегчить принятие решений по композиции, стилю, масштабу и бюджету загрузки.

### Binary package / build receipts

Review-source PNG masters are preserved in the separate ChatGPT ZIP artifact. The GitHub draft PR stores visually equivalent high-quality WebP sources and 20 preview tiles; the preview tile bytes are from PNG before re-encoding sources to WebP and may differ on rebuild. Run tile builder again from approved masters to rebaseline production tiles.


## Owner approval and PNG source transport — 2026-11-22

Именно оригинальные `lod2-region.png` (1920×1080, bounds `[320,240,960,540]`) и `lod3-settlement.png` (1920×960, bounds `[480,330,480,240]`) утверждены как исходный арт. Они сохранены без изменения байтов в `docs/design/map-source/source-parts/*.partNN`; восстановление `python scripts/materialize_map_art.py` проверяет полный SHA-256/размеры, а `scripts/build_map_tiles.py` режет их в WebP. Для production обязательно собрать также LOD1 из `base.png` и заменить preview manifest; вопросы image→screen transform, реальной географии и performance остаются открытыми. Детали — `docs/design/map-source/README.md`, Issue #308.
