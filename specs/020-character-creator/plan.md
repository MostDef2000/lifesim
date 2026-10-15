# 020 — plan

## Порядок реализации (после отдельного `commit approved` на #118)

### Волна 1 — P1 (креатор-экран + биография-теги)
1. `backend/app/api/app.py`: ничего. Frontend-only волна:
   `app.js` — `viewCreateCharacter` → полноценный экран (route `#/create`),
   structured appearance form (13 полей + free-text), bio tag cloud
   (статический список в JS-массиве), draft-сборщик (шаблонная склейка
   ингредиентов, редактируемая textarea).
2. `style.css`: `.creator-shell` блок (паттерн .profile-shell/.people-shell:
   токен-зеркала, specific selectors против глобальных `.need .bar > div`,
   1100px collapse). Урок #69/#107: shell-scoped specificity сразу.
3. Подтверждение → `POST /characters` (без appearance-структуры в P1 —
   free-text склеивается в `looks` как сейчас; structured JSON ждёт P2).
4. Тесты: `tests/m120` — UI-пины (литералы, структура формы), тег-список
   нейтральный (стереотип-проверка: гендерные слова в общих тегах
   отсутствуют), draft-сборка детерминирована, пин-сьюты.

### Волна 2 — P2 (preview + canonical)
1. Миграция: `character_appearances` (1:1 Character, JSON-поля).
2. `POST /visual/preview` (auth-only, rate-limit, без sim state) →
   `build_visual_profile(appearance=...)` → adapter.
3. Резолвер: appearance-поля приоритетнее trait-деривации; без appearance —
   прежний путь (байт-совместимость с m119-пинами).
4. Подтверждение создания: сериализация appearance + canonical reference
   для последующих портретов/сцен (#191 lazy — использовать appearance).
5. Тесты: endpoint-контракт (exact key set), preview не пишет sim state,
   резолвер-приоритеты, регресс m119 (склейка канона без appearance
   байт-идентична).

### Волна 3 — P3 (reference upload)
1. Отдельный мини-спек: storage/retention/privacy (owner-scope, TTL,
   лимиты размера/количества), then реализация.

## Верификация каждой волны

- полный not-slow (базлайн волны 1: 799 + ~10-12 новых),
- ruff + node --check,
- пин-сьюты (m9/m21/m63/m69/m107/m147-m158/m162/m175/m119),
- runtime-приёмка в браузере (скрин в shared/),
- peer review (обязателен: UI+CSS-ловушки specificity дважды ловились),
- для P2: RED-authenticity на «preview не пишет sim state» (тест должен
  падать при намеренном добавлении записи).

## Риски

- Adapter VRAM: preview-лимиты обязательны (12GB, см. KB 1611).
- Specificity-ловушки CSS (дважды горели) — peer review + runtime-скрин.
- Пин-инфляция литералов: запрещённые литералы (m147-m158, m162, m175)
  не добавлять даже в комментарии.
- Гендерные стереотипы в тегах — тест-контракт.
- Обратная совместимость `looks` — регресс-тест m119.
