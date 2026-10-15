# 020 — Character creator (#118)

## Цель

Создание персонажа — отдельный onboarding-экран, где внешний образ и биография
формируются до подтверждения создания. Симуляция остаётся source of truth;
visual generation — presentation layer.

## Состояние (что уже есть)

- Форма создания: `viewCreateCharacter` (app.js:292-319) → `POST /characters`
  (`CharacterIn`: name/sex/age/looks≤500/biography≤2000, app.py:288-294).
- #119 P1: `build_visual_profile()` в `backend/app/visual/descriptor.py` —
  детерминированный резолвер канонического образа из committed state
  (sex, age, traits, biography). Портрет генерируется постфактум (#191
  lazy-generation), preview до создания отсутствует.
- Reference upload: `AssetStore` (visual assets, storage_path) — хранит
  сгенерированные ассеты, привязан к существующим characters.
- Реference-фото реальной кошки (#107) — прецедент «canonical reference set»
  для identity, но это статика, не пользовательский upload.

## Фазы

### P1 — экран-креатор + биография-теги (без генерации)

Отдельный визуально выраженный экран создания (route `#/create`, вместо
короткой формы `viewCreateCharacter`):

- Базовые данные: имя, пол, возраст (как сейчас).
- **Structured appearance**: отдельные поля — лицо, глаза, нос, губы,
  волосы, цвет волос, кожа, рост, вес, телосложение, грудь, особенности
  (+ free-text appearance prompt). Поля дополняют free-text, не
  взаимоисключающие. Хранение: compact dictionary в `looks` (≤500) НЕ
  влезет — см. Data model.
- **Biography tag cloud**: нейтральный набор ингредиентов (профессии,
  происхождение, характер, мечты — без жёстких гендерных стереотипов;
  проверка набора — отдельный тест). Клик по тегу добавляет ingredient;
  связный draft биографии собирается шаблоном (не LLM — мир обязан работать
  без LLM). Draft полностью редактируем; свободный текст — first-class.

### P2 — preview-generation + canonical appearance

- **Preview до создания**: ephemeral endpoint `POST /visual/preview`
  (auth-only, без character) → structured appearance + free-text → тот же
  пайплайн #119 (guardrail → face → body → style → casting preset) →
  adapter `/generate`. Preview НЕ создаёт персонажа, НЕ пишет simulation
  state, НЕ пишет VisualAsset (или пишет с owner-scope и TTL-чисткой —
  решить на реализации).
- **Canonical при подтверждении**: structured appearance сериализуется в
  новый storage (см. Data model); `build_visual_profile()` получает
  appearance-поля как высший приоритет поверх trait-деривации.
- Negative prompt (#119 Phase 2b) — отдельно, lane B (адаптер+транспорт).

### P3 — visual reference upload (отдельный design)

- Storage/retention/privacy пользовательских референсов — решение до
  реализации (тикет явно откладывает схему). Референсы — только guidance,
  никогда simulation facts. Требует: owner-scope ассетов, retention-политику,
  решение по нескольким картинкам. В P3 входит отдельный мини-спек.

## Data model (черновик для реализации)

- `CharacterAppearance` (1:1 с Character, owner-scope): JSON-колонка или
  колонки-поля structured appearance + free-text prompt. Заполняется при
  создании; редактирование post-create — вне v1 (см. Open questions).
- Preview-запросы: stateless (передаём appearance в запросе), ephemeral
  результат в ответе; персистить только после подтверждения.

## Инварианты (не нарушать)

- Simulation Engine = truth; visual = presentation.
- Теги биографии и preview-генерация не меняют simulation state.
- Мир работает без LLM/visual (все фичи — graceful no-op).
- Reference images не создают скрытых характеристик.

## Open questions (решить до/во время реализации)

1. Редактирование appearance post-create: v1 — нет (canonical фиксируется
   при создании; изменение = follow-up #121-«appearance edit»).
2. Preview-лимиты: rate-limit как у прочих visual-эндпоинтов (adapter
   VRAM 12GB, см. KB 1611) — число preview/мин на юзера.
3. Тег-набор: единый список или полозависимый? v1 — единый нейтральный
   список + лёгкая сортировка по полу/возрасту (без блокировки).
4. `looks` (≤500) миграция: существующие персонажи без appearance-структуры
   → резолвер продолжает работать по trait-деривации (#119) — обратная
   совместимость обязательна.
