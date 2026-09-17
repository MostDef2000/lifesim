# Спецификация функционала: M4 — AI Gateway / Queue / Worker, память, структурированные решения, диалог (§103)

## Outcome

Симуляция получает контур LLM (SPEC-V0.1 §103, §12-14, §47-58) без нарушения конституции: Backend никогда не обращается к модели напрямую — только через **AI Gateway** (5 методов §49); запросы проходят через **AI Queue** с тремя приоритетами (§51); **AI Worker** строит prompt-контекст (§54), вызывает транспорт, валидирует JSON (§53, §117) и возвращает результат; при этом мир обязан жить без LLM (П2): при `llm.enabled=false` профили M1/M2/M3 байт-идентичны, а все решения по умолчанию — Tier 0. Появляется память NPC (§55-57): таблица `memories` с детерминированной оценкой важности, ежедневной консолидацией и выборкой для prompt-контекста. Диалоговая способность (Tier 2) доступна через Gateway с журналом ходов; структурированные решения NPC проходят через Action Validator и не имеют права мутировать мир напрямую (П1).

## Scope

- **Included**:
  - AI Gateway: in-process сервис `app/ai/gateway.py` с методами `classify`, `decide`, `dialogue`, `summarize`, `memory-select` (соответствие §49; HTTP-экспозиция — исключение, см. Excluded).
  - AI Queue: таблица `ai_requests` (SQLite, аудируемость П1) с приоритетами `critical > interactive > background` (§51), FIFO внутри приоритета; enqueue-интерфейс Gateway.
  - AI Worker: `app/ai/worker.py` — pull job по приоритету → prompt-контекст (§54) → транспорт → валидация JSON → retry 1 раз → result/ошибка; дневной бюджет запросов по типам задач; обработка в daily-фазе симуляции.
  - Транспорты: `LlmTransport` протокол; `StubTransport` (детерминированный, дефолт — работает без сети и GPU, основа тестов и llm-on-без-модели); `OllamaTransport` (OpenAI-совместимый chat endpoint, urllib, конфигурируемый base_url/model). Новых зависимостей нет.
  - Структурированные решения: задача `decision` → schema `{decision, target_character_id?, confidence, reason}` (§53); результат валидируется против доступных действий существующими validators; невалидное/низкоуверенное решение игнорируется (мир продолжает детерминированный путь); принятые решения фиксируются событием `AI_DECISION`.
  - Tier-1 классификация: задача `classify` — оценка важности события (0-100) для памяти; детерминированная Tier-0 карта важности остаётся источником для значимых типов (смерть, конфликт, выборы, примирение, праздник, штраф).
  - Память (§55-57): таблица `memories` (поля §55), захват по значимым событиям, ежедневная консолидация (группировка → summarize → долгосрочная память; производные мелкие записи удаляются), `memory-select` — top-K по важности×свежести для prompt-контекста, `last_recalled_at` обновляется при выборке.
  - Диалог: метод `dialogue` — персонаж + история + контекст → валидируемый `{reply, intent?, confidence}`; журнал `dialogue_turns` (сессия, роль, содержание); NPC-NPC автодиалоги в цикле симуляции не запускаются (socialize остаётся Tier 0).
  - Контракт событий: closed set 19 → 21 (`AI_DECISION`, `MEMORY_CREATED`, `MEMORY_CONSOLIDATED`).
  - Конфигурация: раздел `llm:` (enabled: false, transport: stub, base_url/model, tier1/tier2, бюджеты, timeout, retry), CLI-флаг `--llm`.
  - Отчёт: блок `ai` при включённом режиме (requests по статусам, decisions, dialogues, memories).
  - Схема: 3 новые таблицы (`memories`, `ai_requests`, `dialogue_turns`), `schema_meta` 0.3.0 → 0.4.0.
- **Excluded**:
  - HTTP-экспозиция Gateway, web-api, WebSocket, игроки (§104, M5-цикл `005-player`).
  - Реальная LLM-инференс-модель в CI/тестах (сеть/GPU недоступны; интеграция с Ollama — конфигурируемая, тестируется на уровне контракта транспорта со стабом).
  - NPC-NPC автоматические LLM-диалоги в sim-цикле; романтический/18+ контент в диалогах (П4; валидация ответов — только структурная, контентная модерация диалогов — §104).
  - Формирование долгосрочных планов NPC через LLM (планирование остаётся M1-расписаниям; «план» из §46 — follow-up).
  - Эмбеддинги/векторный поиск памяти (§55 полей достаточно; семантический поиск — follow-up).
  - Ретроспективные «воспоминания» задним числом до внедрения (память пишется только с момента включения).
  -skills (§15→skills-система), портреты/сцены Flux (§105).
- **Protected boundaries**:
  - П1: LLM предлагает intent → Engine валидирует → БД фиксирует; решение LLM — событие, не мутация.
  - П2: при llm-off мир живёт полностью; llm-on без модели (stub) тоже полон и детерминирован.
  - П3: никакие денежные эффекты решений не идут мимо ledger.

## Requirements

- **R1: Гейт**. Весь AI-контур активен только при `settings.llm.enabled` (память — при `llm.enabled` независимо от social? — нет: память захватывает социальные события, значит `llm.enabled AND social.enabled` для социальных типов, а «нейтральные» значимые события (смерть, `death_game_timestamp`) — при одном `llm.enabled`). При выключенном гейте: worker не планируется, таблицы пусты, событий нет, в отчёте нет ключа `ai`; профили M1/M2/M3 байт-идентичны (keystone). CLI: `--llm` включает гейт по паттерну `--org`.
- **R2: AI Gateway**. Модуль `app/ai/gateway.py`, методы принимают `(session, world_id, character_id, task, context, priority)` → создают строку `ai_requests` (status='pending') и возвращают request_id. Прямых обращений к транспорту из Gateway нет (только Worker). Методы: `classify` (priority=background), `decide` (interactive), `dialogue` (interactive), `summarize` (background), `memory_select` (background). Дубликаты не дедуплицируются (бюджет ограничивает объём).
- **R3: AI Queue**. Таблица `ai_requests`: `id, world_id, character_id, task, context JSON, priority (critical|interactive|background), status (pending|done|failed|skipped), result JSON, error, created_at, processed_at, game_timestamp`. Выборка воркером: `priority IN (critical, interactive, background)` по возрастанию ранга, внутри — по `id` (FIFO). `skipped` — превышение бюджета (запись остаётся, обработка не выполняется).
- **R4: AI Worker**. Daily-фаза ПОСЛЕ org-хендлера, за гейтом R1, фиксированный порядок: (1) drain `critical`/`interactive` → (2) ежедневная консолидация памяти → (3) drain `background` (в пределах бюджета дня). Обработка job: prompt-контекст (R6) → transport.complete(prompt, schema_hint) → парсинг JSON → валидация по схеме задачи → status='done', result записан; невалидный JSON/исключение транспорта → один retry → status='failed', error записан, мир продолжает работу (никаких исключений наружу). Бюджет: `budgets.max_per_day` и `budgets.per_task` — при исчерпании новые pending этого типа в этот день получают `skipped`.
- **R5: Транспорты и structured output**. `LlmTransport.complete(prompt: str, schema_hint: str) -> str` (текст ответа). `StubTransport`: детерминированные ответы по задаче (decision → действие из `context.available_actions` с наибольшим utility-приоритетом в контексте; classify → детерминированная важность; dialogue → шаблонный reply; summarize → агрегат входных строк). `OllamaTransport`: POST `{base_url}/api/chat` (model, messages, format='json', stream=false), timeout из конфига; ошибки сети → исключение → retry → failed. Валидация ответа: `json.loads` + проверка схемы задачи (decision: `decision` из available_actions, `confidence` 0..1, `target_character_id` — валидный id или null; classify: `importance` 0..100; dialogue: непустой `reply` ≤ 2000 символов). Провал валидации = невалидный ответ (retry/failed).
- **R6: Prompt-контекст (§54)**. Builder `app/ai/prompts.py` — только поля: identity (имя, профессия), personality (traits), current needs, current goals (активная задача), current location, nearby characters (≤ 5, живые), important relationships (топ-3 по |affection|), recent memories (R8), current event, available actions. Никаких запросов «всей базы» (П1 + §54); ограничение объёма — детерминированное.
- **R7: Структурированные решения**. Триггеры enqueue `decide` (interactive): событие `CONFLICT` с участием персонажа (реакция на ЧП) — максимум 1 запрос на персонажа на день. Решение валидируется: `decision` ∈ available_actions персонажа на момент обработки; иначе решение игнорируется (status='done', но событие `AI_DECISION` не эмитится; в result пишется `applied: false, reason`). Применимое решение → `AI_DECISION` event (actor = персонаж, payload `{request_id, decision, target_character_id?, confidence, reason, applied}`) — само событие есть запись о решении; исполнение действия происходит существующими механиками M1-M3 (П1); в M4-цикле decision с ограниченным каталогом: {socialize_with, visit, work_overtime} — социально-безопасные глаголы, исполняемые существующими validators.
- **R8: Память (§55-57)**.
  - Захват: после значимых событий в daily-фазе создаются `memories` (character_id = actor ИЛИ target ИЛИ «свидетели» — участники события в радиусе локации): CONFLICT (importance 70), ELECTION (50), LAW_VIOLATION (40), RECONCILIATION (60), ORG_FEAST (30), смерть персонажа (90, всем живым знакомым). `memory_type` = имя события; `emotional_valence`: CONFLICT −0.6, RECONCILIATION +0.7, ORG_FEAST +0.5, смерть −0.9, прочее 0.0; `summary` — детерминированная однострочка из event payload (Tier 0, без LLM); `event_id` — ссылка на world_events.
  - Консолидация (§57, ежедневно): события дня персонажа группируются по `memory_type`; группы ≥ 2 записей → `summarize` (stub-детерминированный; при реальном транспорте — LLM) → новая запись `memory_type='consolidated_<type>'` с importance = max группы, `summary` — агрегат; исходные записи группы удаляются (производный кэш, П1 не нарушается — источник world_events). Событие `MEMORY_CONSOLIDATED` (aggregate, actor=null): payload `{character_id, groups, removed, created}` — максимум 1 на персонажа в день (только при факте консолидации).
  - Выборка (`memory_select`, §49): top-K (K=5) по `importance DESC, created_at DESC` с обновлением `last_recalled_at`; используется в prompt-контексте (R6).
- **R9: Диалог**. `dialogue(session, world_id, character_id, messages, priority='interactive')`: journal `dialogue_turns` (session_id uuid, character_id, role: user|assistant|system, content, game_timestamp, request_id?); ответ валидируется по R5; NPC-NPC автодиалогов нет. Диалог не мутирует мир (кроме журнала и опционального AI_DECISION, если worker применит intent — в M4-цикле intent диалога не исполняется: только запись).
- **R10: Контракт событий** (closed set 19 → 21):
  - `AI_DECISION`: actor = персонаж; payload `{request_id, decision, target_character_id, confidence, reason, applied}`; consumer: отчёт, тесты AE.
  - `MEMORY_CREATED`: actor = персонаж-владелец; payload `{event_id, memory_type, importance}`; consumer: memory-инвариант, отчёт.
  - `MEMORY_CONSOLIDATED`: actor = null; payload `{character_id, groups, removed, created}`; consumer: memory-инвариант, отчёт.
- **R11: Инварианты** (за гейтом R1, 3 новых):
  - `ai_request_integrity`: каждый `done`/`failed`/`skipped` имеет processed_at и результат/error; pending не старше текущего дня (висяков нет).
  - `memory_integrity`: каждый `memory.event_id` существует в world_events; importance 0..100; у удалённых записей есть консолидированный наследник (сумма created ≥ removed по дням).
  - `dialogue_integrity`: ходы одной сессии монотонны по времени; role ∈ {user, assistant, system}; content непуст.
- **R12: Конфигурация**. Раздел `llm:`: `enabled: false`, `transport: stub` (stub|ollama), `base_url: http://localhost:11434`, `model_tier1`, `model_tier2`, `budgets: {max_per_day: 40, per_task: {decide: 10, classify: 20, dialogue: 10, summarize: 10}}`, `timeout_sec: 30`, `retry: 1`, `memory: {top_k: 5, witnesses: true}`. Конфиг без секции `llm:` загружается с дефолтами (паттерн Pydantic M3).

## Non-Functional Requirements

- **Security**: LLM не получает секретов (ключей/путей хоста); prompt — только игровые поля (R6); ответы валидируются схемой до попадания в мир (R5); диалоги — журнал без исполнения (R9).
- **Reliability**: при ошибке транспорта/невалидном JSON мир продолжает детерминированную работу (retry 1 раз → failed, без исключений наружу); П2: llm-off/full-stub — полная функциональность симуляции.
- **Performance**: 30д/20NPC llm-on(stub) прогон ≤ 180s (guard, прецедент AE1'); бюджет ограничивает число обработок (дефолт ≤ 40/день).
- **Operability**: все запросы/ответы/ошибки — в `ai_requests` (аудит П1); статус в отчёте; transport/модель — конфиг.
- **Compatibility**: schema 0.3.0 → 0.4.0 (create_all, без миграций); closed-set событий расширяется с фиксацией структурного теста; отчёт M1/M2/M3 без llm — без изменений.

## Acceptance Evidence

- **AE1''**: `vl1 simulate --days 30 --population 20 --seed 42 --social --org --llm` → exit 0; `invariants_ok`; `MEMORY_CREATED` ≥ 50; `MEMORY_CONSOLIDATED` ≥ 1; все `ai_requests` в терминальном статусе; `AI_DECISION` ≥ 1 (CONFLICT-триггеры на seed 42 — по замеру dry-run, прецедент T16); wall ≤ 180s.
- **AE2''**: 7д llm-on(stub): SOCIAL-память создаётся у участников CONFLICT/RECONCILIATION; memory_integrity зелёный; диалоговая сессия (тест) — валидный reply, журнал записан.
- **AE3''**: юнит-механика: невалидный JSON → retry → failed (мир жив); приоритет drain (critical > interactive > background при смешанной очереди); бюджет → skipped; memory-select top-K + last_recalled_at; консолидация: группа ≥ 2 → consolidated-запись, исходники удалены; решение вне available_actions → applied=false, события нет.
- **AE4''**: детерминизм llm-on(stub): два идентичных 7д прогона → идентичные events/memories/ai_requests/relationships; другой seed → другой лог.
- **AE5''** (keystone): байт-идентичность llm-off: 7д/30д векторы M1 (events_by_type), M2 social-on и M3 org-on (`test_chunk_d.py` pins) неизменны; в отчётах нет ключа `ai`; таблицы ai/memories пусты.
- **AE6''**: чувствительность к конфигу: `budgets.max_per_day` меняет число обработанных запросов; `transport` switch stub→stub-variant меняет детерминированные ответы.
