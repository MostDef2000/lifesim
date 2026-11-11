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

### Волна 4 — AI-черновики (creator drafting, issue #316)

Минимальный дизайн:
- `backend/app/characters/creator.py` — ЧИСТЫЙ модуль (без FastAPI/DB):
  границы полей, `parse_llm_json` (strict: без markdown-заборов,
  дубликатов ключей, обрезки), `validate_field_value`/`validate_draft`
  (19 ключей, типы, границы), тексты промптов + schema-подсказки.
- `backend/app/api/creator_routes.py` — `POST /characters/draft`:
  503-гейт (LLM off, ничего не потребляет) → 429 rate-limit
  (consume-on-admission, deque 5/мин на юзера) → 422 форма → 429 busy
  (один активный на юзера И на процесс, finally-release на каждом
  пути) → ОДИН LLM-вызов → строгая валидация → ответ. Транспорт-шов:
  `app.state.llm_transport_factory` (зеркало flux_transport_factory).
- `app.py`: CharacterIn += outfit_mode/custom_clothing; атомарный
  persist в той же транзакции создания (character.outfit до
  единственного commit; `_presentation` собирается только сервером);
  `create_app(creator_vertical_capability=False)` → app.state.
- `visual_routes.py`: preview += custom_clothing (та же валидация) +
  canvas (capability-гейт ДО транспорта; 512×768 только ON).
- `descriptor.py`: `decode_stored_appearance` (плоский JSON +
  зарезервированный `_presentation`), `validate_custom_clothing`,
  formal-фрагмент, ветка auto+custom в build_prompt.

Отклонённые альтернативы (и почему):
- Вложенный envelope appearance (`{"fields":…, "_presentation":…}` как
  единый клиентский объект) — ломает плоскую форму 12 полей и требует
  изменения всех мест чтения; зарезервированный ключ на плоском JSON
  даёт то же самое без изменения формы.
- Замена ручных полей «инспектором-черновиком» (LLM пишет напрямую в
  форму, человек только подтверждает) — нарушает поправку П2
  (предложения, человек решает) и статус ручного пути как first-class.
- Даунгрейд вертикального канваса до 384×512 — меньше VRAM, но лицо
  теряет читаемость на портрете; выбран 512×768 с capability-гейтом и
  STOP-условиями 019 вместо постоянного низкого разрешения.
- Negative-prompt-как-фикс для adherence на 512×768 — негативы уже в
  контракте #227, но для нового канваса не доказаны; не заменяют
  evidence-гейт 019.

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
