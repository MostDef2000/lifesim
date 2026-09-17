# План имплементации: 005-player

## Архитектура

- **Слой API**: `backend/app/api/` — отдельный пакет: `app.py` (фабрика `create_app(settings, session_factory)`), `auth.py` (хэш/токен/зависимость `current_user`), `routes/` (auth, characters, actions, goals, dialogue, world, ws). FastAPI + Pydantic v2 схемы в `schemas.py`. Тесты — starlette TestClient (через httpx), без поднятия сервера.
- **Движок**: симуляционный цикл НЕ трогается, кроме трёх точек гейта владения (R1): (1) progress_tick перед `choose_action` — DIRECT → без задачи, GUIDED → конвертация queued-цели в задачу; (2) complete_task/fail_task — завершение goal-задачи обновляет цель; (3) control-эндпоинт отменяет STARTED-задачу. Все изменения за условием `control_mode != 'AUTONOMOUS' or user_id is not None` — персонажи без владельца байт-идентичны.
- **Intent-парсер**: `app/api/intent.py` — Tier-0 детерминированный (нормализация, словари сущностей из БД, словари глаголов); при `llm.enabled` — через `app.ai` транспорт с подсказкой 'goal'; результат одинаковой структуры {goal_type, params}.
- **Чат**: `app/api/dialogue.py` — пайплайн §65: контекст (memories M4 + relationship + location) → transport ИЛИ fallback-шаблон; suggested_responses — детерминированные 3 варианта.
- **WS**: `app/api/ws.py` — endpoint /ws: HMAC-токен, поллинг world_events по курсору (1.0с), пачки {type:'events'}. Тест — TestClient.websocket_connect + шаг движка из потока (file-DB, check_same_thread=False).

## Последовательность

| Chunk | Содержание | Тесты |
|---|---|---|
| A | Зависимости (fastapi/uvicorn/httpx), schema 0.5.0 (users, character_goals, dialogue_sessions, dialogue_messages, characters.user_id/control_mode), события 22→24, auth.py (PBKDF2, HMAC-токен), фабрика приложения, routes/auth, routes/characters POST, конфиг `api:`, CLI `vl1 serve` | test_chunk_a: register/login/me/logout, 18+, дубли, хэш/токен юнит, создание персонажа+лимит |
| B | control_mode семантика (отмена задачи, DIRECT-гейт, GUIDED-конвертация), routes/control, routes/actions POST/GET, character_goals + intent.py + GOAL_QUEUED/CONTROL_CHANGED | test_chunk_b: intercept, DIRECT без utility-задач, guided goal→task→done, 422/409/403 |
| C | routes/world, events, inventory, locations, dialogue.py (пайплайн §65 + fallback), dialogue_sessions/messages | test_chunk_c: чтение-эндпоинты, чат fallback детерминизм, история, невалиды |
| D | ws.py, AE1'''-AE6''' (E2E, keystone байт-идентичность, персистентность, WS, config sensitivity), README-инструкция запуска, tasks tick | полный suite + check.sh |

## Риски / решения

- **SQLite + многопоточность WS-теста**: file-DB с `check_same_thread=False`, WAL; шаги движка в тесте — короткие и синхронизированные через polling курсора. Fallback: юнит-тест сериализации + контракт-тест connect/auth без живого поллинга.
- **FastAPI-зависимости в CI**: uv sync --frozen — обновить pyproject/lock в Chunk A (первым коммитом, чтобы gate не сломался).
- **Токен без новых зависимостей**: HMAC-SHA256 (stdlib hmac/hashlib), payload base64url JSON, exp.
- **П2**: `api.enabled: false` по умолчанию; `vl1 serve` без флага/конфига — отказ (exit 2); headless-пути не импортируют FastAPI (ленивые импорты внутри app.py).
