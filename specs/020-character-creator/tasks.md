# 020 — tasks

## Волна 1 — P1 (креатор + биография-теги)

- [x] Экран `#/create` заменяет короткую форму `viewCreateCharacter`
      (визуально выраженный onboarding-блок).
- [x] Structured appearance form: 12 полей + free-text prompt (P1 —
      сохранение free-text в `looks`, structured ждёт P2-миграции).
- [x] Biography tag cloud: нейтральный список ингредиентов, клик добавляет
      ingredient, draft собирается шаблоном, полностью редактируем.
- [x] Теги не обязательны, свободный текст first-class.
- [x] Тест-контракт: тег-список без гендерных стереотипов.
- [x] Пин-дисциплина: 0 запрещённых литералов; честные bump'ы с трейлами.
- [x] Полный not-slow зелёный + peer review + runtime-скрин.

## Волна 2 — P2 (preview + canonical appearance)

- [ ] Миграция `character_appearances` (1:1, owner-scope).
- [ ] `POST /visual/preview`: auth-only, rate-limit, без sim-state записей.
- [ ] Резолвер: appearance > trait-деривация; без appearance — байт-равно
      прежнему канону (m119-регресс).
- [x] Подтверждение → canonical appearance persist; портреты/сцены (#191)
      используют его.
- [x] Preview rate-limit (VRAM 12GB).
- [x] Тесты: endpoint-контракт, no-sim-write (RED-proven), приоритет
      резолвера, обратная совместимость.

## Волна 3 — P3 (reference upload)

- [x] Мини-спек storage/retention/privacy (решение владельца).
- [x] Реализация после отдельного approval.

## Волна 4 — AI-черновики (creator drafting, issue #316)

Трассировка требование → доказательство (tests/creator_upgrade):

- [x] Контракт generate/variant, строгая схема, ОДИН LLM-вызов, zero DB
      writes → `test_draft_api.py` (schema shape; variant single-field;
      502-матрица: invalid JSON / неизвестный ключ / возраст вне границ /
      AI-nude; 503 LLM off; 429 после 5 — failures потребляют квоту,
      429 не потребляет; concurrency 429; 401; idea>500 → 422; ни одной
      записи в БД; busy-лок освобождён после исключения).
- [x] Storage: плоский appearance + зарезервированный `_presentation`,
      атомарный persist outfit+custom в транзакции создания, legacy-decode,
      `_presentation` не протекает во фрагменты/публичный ввод,
      границы validate_custom_clothing → `test_storage.py` (+ портрет
      читает сохранённый custom; отсутствие полей — прежнее поведение).
- [x] Precedence одежды и байт-контракты промпта: nude_framing неизменён,
      formal — новые байты, auto без custom — байт-идентичен, сцены
      игнорируют custom, worn > enum > auto+custom > пул →
      `test_prompt_contract.py`.
- [x] Canvas stages: absent/legacy → настроенный size; OFF → 422 до
      транспорта (транспорт не вызван); ON → "512x768" доходит до
      транспорта; ON+reference → 422; ON+legacy → настроенный size →
      `test_preview_canvas.py`.
- [x] Пин-обновления (superseded product decision, минимальные правки):
      m205:164/:187 (formal-фрагмент), m216:246 (атомарный outfit),
      m236:284 (formal-байты); m205:254 (worn-wins) — нетронут.

## Вне скоупа (тикет #118)

Sim-математика, traits/stats, NPC, диалоги, Goal/Desire, landing, concierge
cat, React/npm, деплой-инфраструктура, смена LLM/Flux модели.
