# План: M4 — AI Gateway / Queue / Worker, память, решения, диалог (§103)

## Текущее поведение

- Симуляция полностью детерминирована (M1-M3): решения NPC — Tier 0 (расписания, utility-эвристики, org-механика). Никакого LLM-кода в репозитории нет; конституция П2/П1 соблюдена конструктивно.
- Контур мира: `Engine.step` → daily-хендлеры (needs → SALARY/SUPPLY → org → invariants в отчёте), события — closed set 19 типов, отчёт без ключа `ai`, схема 0.3.0 (7 таблиц M1-M3: characters, locations, items, accounts, transactions, relationships+relationship_events, organizations+members+laws+violations).
- Конфиг: `Settings(world, ticks, needs, economy, social, org)`; тестовый конвент: голый `Settings()` валиден только с полным набором подсекций.

## Дизайн

### Модули (новые)

```text
backend/app/ai/
  __init__.py
  gateway.py      # enqueue: classify/decide/dialogue/summarize/memory_select -> ai_requests
  worker.py       # drain(budget) -> prompt -> transport -> validate -> done/failed/skipped
  transports.py   # LlmTransport protocol, StubTransport (детерминированный), OllamaTransport (urllib)
  prompts.py      # build_context(session, world_id, character_id, event) -> prompt (§54, только поля)
  schemas.py      # валидация ответов по задаче: decision/classify/dialogue/summarize
  memory.py       # capture (значимые события), consolidate (§57), select (top-K)
```

### Данные (новые таблицы, schema 0.4.0)

- `memories`: поля §55 (id, character_id, event_id FK, memory_type, importance, emotional_valence, summary, created_at, last_recalled_at).
- `ai_requests`: id, world_id, character_id, task, context JSON, priority, status, result JSON, error, created_at, processed_at, game_timestamp.
- `dialogue_turns`: id, session_id, world_id, character_id, role, content, game_timestamp, request_id.

### Потоки

1. **Decide-триггер**: lifecycle CONFLICT-хук (после существующего org-хука enforce_no_conflict) при llm-on ставит `decide`-запрос участникам (≤ 1/чел/день). Worker в daily-фазе: prompt (R6) → transport → schema-валидация → `AI_DECISION` (applied=true только если `decision` ∈ available_actions персонажа); исполнение — существующими механиками (П1).
2. **Память**: daily-фаза после worker-drain: capture по значимым событиям дня (детерминированная карта R8) → `MEMORY_CREATED`; консолидация (группы ≥ 2 → summarize → consolidated-запись, исходники удалены) → `MEMORY_CONSOLIDATED`.
3. **Диалог**: тесты + внешний вызов (головной CLI-режим `vl1 dialogue` НЕ вводится; интерактив — §104). Gateway-метод пишет ходы в `dialogue_turns`, ответ — через worker-пайплайн синхронно (interactive-приоритет, бюджет dialogue).

### Приоритеты и бюджеты

drain-порядок: critical → interactive → background (FIFO по id внутри); дневной бюджет `max_per_day` и per_task; исчерпание → `skipped` (запись сохраняется). Консолидация памяти — между interactive и background (фиксированный порядок daily-фазы).

## Change Map

| Файл | Изменение |
|---|---|
| `backend/app/db/models.py` | +3 таблицы (memories, ai_requests, dialogue_turns), schema_meta 0.4.0 |
| `backend/app/config/config.py` | +`LlmConfig` (enabled/transport/base_url/models/budgets/timeout/retry/memory) |
| `config/default.yaml` | +секция `llm:` (всё выключено по умолчанию) |
| `backend/app/events/events.py` | EventType 19 → 21 (AI_DECISION, MEMORY_CREATED, MEMORY_CONSOLIDATED) |
| `backend/app/ai/*` | новый пакет (gateway/worker/transports/prompts/schemas/memory) |
| `backend/app/actions/lifecycle.py` | CONFLICT-хук: enqueue decide (llm-гейт первой строкой, после org-хука) |
| `backend/app/simulation/daily.py` | вызов `run_ai_phase` после org-хендлера (за гейтом R1) |
| `backend/app/simulation/invariants.py` | +3 инварианта (R11) за гейтом R1 |
| `backend/app/simulation/report.py` | блок `ai` при llm-on (requests по статусам, decisions, memories) |
| `backend/app/simulation/cli.py` | флаг `--llm` |
| `tests/m4/*` | тесты чанков A-D (AE1''-AE6'') |

## Риски

- **Байт-идентичность (главный риск)**: CONFLICT-хук в горячем пути M2/M3 расширяется вторым вызовом — гейт `llm.enabled` первой строкой; регрессия — pinned-векторы test_chunk_d.py (AE5'').
- **П2-ползучесть**: «LLM нужна» соблазнительно воткнуть в Tier 0 пути — запрещено; decision-каталог M4 ограничен 3 социальными глаголами, исполнение только через validators.
- **Стаб-детерминизм**: StubTransport обязан отвечать детерминированно (без clock/random) — иначе AE4'' разваливается.
- **Wall-time**: обработка ≤ 40 запросов/день × 30 дней при stub — счёт запросов к БД линеен; guard 180s в AE1''.
- **П1-дрейф памяти**: memories — производный кэш; удаление исходников при консолидации допустимо (источник — world_events); инвариант memory_integrity следит за связностью.
- **Ollama-транспорт в CI**: сеть недоступна — юнит-тесты только на контракт (monkeypatch urlopen); реальный inference — ручной прогон владельца (документируется в plan/D-отчёте).

## Test strategy

- Юнит: schema-валидация ответов (валид/мусор/граничные), stub-детерминизм, приоритеты, бюджеты, memory capture/consolidate/select, диалог-журнал.
- Интеграция: 7д/30д llm-on(stub) прогоны (AE1''/AE2''), детерминизм (AE4''), keystone llm-off на всех pinned-векторах M1/M2/M3 (AE5''), config sensitivity (AE6'').
- Регрессия: полный fast-набор + slow (121 тестов M1-M3) зелёные до и после.

## Ручной прогон с реальной моделью (T17, вне CI)

1. Установить Ollama на домашнем AI-сервере (RTX 4070 Ti 12GB): `ollama pull qwen3:14b` (Tier 2), `ollama pull qwen3:4b` (Tier 1).
2. В `config/default.yaml` выставить: `llm.enabled: true`, `llm.transport: ollama`, `llm.base_url: http://localhost:11434` (OllamaTransport обращается к `POST {base_url}/api/chat`, format=json, stream=false).
3. Запуск: `vl1 simulate --days 7 --population 20 --seed 42 --social --org --llm` — решения/диалоги пойдут через модель; при недоступности сервера транспорт вернёт ошибку → retry 1 раз → request `failed`, мир продолжит детерминированный путь (П2 проверяется самим прогоном).
4. Проверка: отчёт `ai.requests_by_status` — доля `done`/`failed`; инварианты `ai_request_integrity`/`memory_integrity` в отчёте зелёные.
