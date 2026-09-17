# Задачи: 005-player

## Chunk A — Фундамент: deps, схема, auth, фабрика

- [x] T1: pyproject + lock: fastapi>=0.115, uvicorn>=0.30, httpx>=0.27; uv sync; gate зелёный (ruff/fast).
- [x] T2: schema 0.5.0: таблицы users, character_goals, dialogue_sessions, dialogue_messages; characters.user_id (FK users.id, nullable), characters.control_mode (String, default 'AUTONOMOUS'); tests/db/test_bootstrap.py 28→32.
- [x] T3: события 22→24: CONTROL_CHANGED, GOAL_QUEUED (+tests/m2/test_chunk_a.py).
- [x] T4: app/api/auth.py: PBKDF2-HMAC-SHA256 (100k, salt 16B), HMAC-токен {user_id, role, exp} (secret env VL1_SECRET, TTL session_ttl_min), cookie vl1_session httpOnly + Bearer.
- [x] T5: app/api/app.py: create_app(settings, session_factory), JSON-ошибки {detail}, ленивые импорты FastAPI.
- [x] T6: routes/auth: register (валидации R2 + age_confirmed), login, logout, me; routes/characters POST (R3: генератор, лимит 1, имя уникально, age≥18).
- [x] T7: конфиг `api:` (enabled false, host, port, session_ttl_min 720, cookie_name, ws_interval_s 1.0), cli.py `vl1 serve` (uvicorn, отказ при api.enabled=false).
- [x] T8: tests/m5/test_chunk_a.py: юнит хэш/токен; E2E register→login→me→logout; 18+/короткий пароль/дубли → 422/409; create character + лимит 409 + 403.

## Chunk B — Управление: control modes, direct actions, цели

- [x] T9: routes/control POST /characters/{id}/control (R4: enum, событие CONTROL_CHANGED, отмена STARTED source != player при → DIRECT).
- [x] T10: движок: DIRECT-гейт перед choose_action (без utility-задачи), GUIDED-конвертация первой queued-цели в CharacterTask(source='goal'), complete/fail обновляет цель (done/failed). Всё за гейтом user_id/control_mode.
- [x] T11: app/api/intent.py: Tier-0 парсер (travel_to/socialize_with/acquire_items), llm-путь с schema_hint='goal'; игровая валидация (location/items/npc); routes/goals POST + GOAL_QUEUED.
- [x] T12: routes/actions POST (R5: validate → task source='player', 422/409/403) + GET /actions/{id}.
- [x] T13: tests/m5/test_chunk_b.py: intercept отменяет задачу; DIRECT — utility-задач нет, needs тикают; guided: text→goal→task→done; невалидные цели/действия 422; AUTONOMOUS direct → 409; чужой персонаж 403.

## Chunk C — Чтение и чат

- [x] T14: routes/world: GET /world, /world/events?since&limit, /locations/{id}, /characters/{id} (публичный слепок/needs владельцу), /characters/{id}/inventory.
- [x] T15: app/api/dialogue.py: start/message/get (R7): safety-гейт (1..2000), контекст (memories/relationship/location), llm-путь через M4-транспорт, fallback-шаблон, 3 suggested_responses, журналирование dialogue_messages.
- [x] T16: tests/m5/test_chunk_c.py: все read-эндпоинты (владелец/чужой), чат: fallback детерминирован, история полная, сессия на мёртвого NPC → 422, пустое сообщение → 422.

## Chunk D — WebSocket, AE-свидетельства, финал

- [x] T17: routes/ws: /ws?token= (R9: hello+cursor, пачки events, ping/pong, 401).
- [x] T18: tests/m5/test_chunk_d.py: AE1''' E2E-сценарий; AE2''' keystone (пины M1-M4 байт-идентичны, control_mode default); AE3''' негативы; AE4''' персистентность file-DB; AE5''' WS; AE6''' config sensitivity (ttl→exp, serve-отказ).
- [x] T19: README-инструкция: локальный запуск (vl1 serve + curl/wscat примеры), границы MVP.
- [x] T20: tasks tick, полный suite + check.sh, PR.

## Эмпирические пороги

- AE1''': полный сценарий проходит на llm-off за < 60с wall.
- AE2''': events_by_type идентичны пинам M1/M2/M3/M4.
- AE5''': WS доставляет ≥ 1 пачку событий после 1 шага движка.
