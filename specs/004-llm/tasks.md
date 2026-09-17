# Задачи: M4 — AI Gateway / Queue / Worker, память, решения, диалог (§103)

## Фаза A — Каркас: схема, конфиг, события, транспорт

- [ ] T1: Таблицы `memories`, `ai_requests`, `dialogue_turns` (поля spec R3/R8/R9) в `models.py`; `schema_meta` 0.3.0 → 0.4.0. Готовность: create_all создаёт схему; FK memories.event_id → world_events.
- [ ] T2: `LlmConfig` в `config.py` + секция `llm:` в `default.yaml` (spec R12; enabled: false). Готовность: конфиг без секции `llm:` грузится с дефолтами; minimal-settings тест проходит.
- [ ] T3: EventType 19 → 21 (AI_DECISION, MEMORY_CREATED, MEMORY_CONSOLIDATED); структурный тест обновлён сознательно. Готовность: closed-set тест фиксирует 21.
- [ ] T4: `app/ai/transports.py`: протокол `LlmTransport.complete(prompt, schema_hint) -> str`; `StubTransport` (детерминированные ответы по задачам); `OllamaTransport` (urllib POST /api/chat, format='json', timeout). Готовность: stub-ответы детерминированы; ollama-контракт покрыт monkeypatch-тестом.
- [ ] T5: CLI-флаг `--llm` (паттерн `--org`). Готовность: `--llm` ставит `settings.llm.enabled = true`.

## Фаза B — Queue/Worker/память

- [ ] T6: `app/ai/gateway.py`: 5 методов enqueue (spec R2) → `ai_requests` (status='pending'). Готовность: юнит-тесты создают записи с корректными priority/task.
- [ ] T7: `app/ai/worker.py`: drain (priority → FIFO, spec R3/R4), prompt-контекст (`prompts.py`, spec R6), schema-валидация (`schemas.py`, spec R5), retry 1 раз → failed, бюджет → skipped, daily-порядок: interactive → консолидация → background. Готовность: юнит-тесты невалид-JSON/priority/budget.
- [ ] T8: `app/ai/memory.py`: capture (карта важности R8), consolidate (§57: группы ≥ 2 → summarize → consolidated, исходники удалены), select (top-K + last_recalled_at). Готовность: юнит-тесты capture/consolidate/select; события MEMORY_CREATED/CONSOLIDATED по R10.
- [ ] T9: Хук в daily-фазе: `run_ai_phase(session, world_id, settings, game_timestamp)` после org-хендлера, гейт R1 первой строкой; CONFLICT-хук в lifecycle.py: enqueue decide (≤1/чел/день, после org-хука). Готовность: llm-off — ноль запросов/мутаций; llm-on — очередь обрабатывается.

## Фаза C — Решения, инварианты, отчёт

- [ ] T10: Валидация решений (spec R7): `decision` ∈ available_actions → `AI_DECISION` (applied=true); иначе result.applied=false, события нет. Каталог M4: socialize_with, visit, work_overtime. Готовность: юнит-тесты обоих путей.
- [ ] T11: Инварианты R11 (ai_request_integrity, memory_integrity, dialogue_integrity) за гейтом R1. Готовность: позитив/негатив юнит-тесты.
- [ ] T12: Блок `ai` в отчёте (spec Outcome): requests по статусам, decisions (applied/ignored), dialogues, memories (created/consolidated/alive); при llm-off ключа нет. Готовность: тесты обоих режимов.

## Фаза D — Интеграция и приёмка

- [ ] T13: Юнит-механика AE3'': невалид-JSON → retry → failed; priority drain; бюджет → skipped; memory-select top-K; консолидация; решение вне available_actions. Готовность: все AE3'' тесты зелёные.
- [ ] T14: Regression-fixture: зафиксировать llm-off векторы (M1/M2/M3 pins уже в test_chunk_d.py — переиспользовать; добавить пустоту ai/memories таблиц и отсутствие ключа `ai`). Готовность: AE5'' тесты зелёные.
- [ ] T15: Интеграционные прогоны: AE1'' (30д llm-on stub), AE2'' (7д память/диалог), AE4'' (детерминизм stub), AE6'' (budget/transport sensitivity). Готовность: AE1''-AE6'' выполнены; wall-guard ≤ 180s.
- [ ] T16: Dry-run эмпирика: число CONFLICT-триггеров decide на seed 42; фиксация AE1'' порога AI_DECISION (прецедент T16 M3). Готовность: пороги подтверждены или откалиброваны.
- [ ] T17: Ручной прогон с реальной моделью (Ollama на машине владельца) — документированная инструкция в README/plan (не в CI). Готовность: инструкция зафиксирована; транспорт покрыт контрактом.
- [ ] T18: Финальный гейт: полный fast-набор + slow-набор + ruff + `scripts/check.sh`. Готовность: все проверки пройдены; CI зелёный на PR.

## Definition of Done

- AE1''-AE6'' выполнены; keystone (AE5'') — байт-идентичность M1/M2/M3 при llm-off.
- Конституция: П1 (решения — события, исполнение через validators), П2 (мир жив без LLM), П3 (нет денежных путей мимо ledger) — подтверждены тестами.
- `tasks.md` синхронизирован; спека/план отражают реализацию.
