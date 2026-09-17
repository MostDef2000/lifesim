# Спецификация функционала: M5 — Player (§104): регистрация, создание персонажа, режимы управления, чат, web-api/WebSocket

## Outcome

Мир получает игроков: регистрация/аутентификация (email/username + PBKDF2-хэш + httpOnly session-cookie, §81), создание собственного персонажа (автономного цифрового человека, §1), три режима управления `AUTONOMOUS | GUIDED | DIRECT` (§60) с перехватом управления (§61), постановка целей текстом через intent-парсер с игровой валидацией (§62), прямые валидируемые действия (§80 POST /actions), диалоговый чат с NPC по пайплайну §63-65 (сессии/сообщения, варианты ответа + свободный текст), REST API §80 и WebSocket-поток §79. Headless-симуляция M1-M4 байт-идентична при отсутствии игроков (все новые пути — за гейтами владения/режима).

## Scope

- **Included**:
  - Таблицы: `users` (username/email уникальны, password_hash, role, age_confirmed, created_at), `character_goals` (goal_type, params JSON, status queued|active|done|failed|cancelled, deadline_day, source_text), `dialogue_sessions` (participants, location_id, started_at, ended_at, context JSON), `dialogue_messages` (session_id, sender: user|npc|system, content, created_at); колонки `characters.user_id` (nullable FK) и `characters.control_mode` (default AUTONOMOUS). Schema 0.4.0 → 0.5.0.
  - Auth: POST /auth/register (18+ gate — обязательное `age_confirmed`; П4), POST /auth/login, POST /auth/logout, GET /auth/me; PBKDF2-HMAC-SHA256 (100k итераций, per-user salt, stdlib); HMAC-подписанный токен (stdlib) в httpOnly cookie + Bearer-поддержка; роли: user (MVP; moderator/admin/developer — поле зарезервировано).
  - Создание персонажа: POST /characters — привязка `user_id`, имя уникально в мире, стартовые параметры через существующий генератор (профессия/жильё/деньги); максимум 1 персонаж на пользователя (MVP).
  - Режимы управления (§60-61): POST /characters/{id}/control {mode}; переход AUTONOMOUS → DIRECT останавливает текущую задачу (status='CANCELLED', событие CONTROL_CHANGED); в DIRECT utility-AI не создаёт задач персонажу; в GUIDED очередь целей игрока исполняется (преобразование в CharacterTask), остальное — как AUTONOMOUS; событие `CONTROL_CHANGED` (audit, П1).
  - Прямые действия: POST /actions {character_id, action_type, params} — валидация через существующий `validators.validate` (П1), создание CharacterTask(source='player'); GET /actions/{id} — статус задачи. Недоступное действие → 422 с причиной валидатора.
  - Цели (§62): POST /characters/{id}/goals {text} → intent-парсер (Tier-0 детерминированный: ключевые слова/сущности; при llm.enabled — через M4-транспорт) → structured goal {goal_type, params, deadline_day} → игровая валидация (существование локации/предметов/персонажа) → `character_goals` + событие `GOAL_QUEUED`. Каталог MVP: `travel_to` (исполнение — MOVE-задача), `socialize_with` (SOCIALIZE-задача), `acquire_items` (BUY_ITEM-задача на shop).
  - Чат (§63-65): POST /dialogue/start {npc_id} → сессия; POST /dialogue/{id}/message {content} → пайплайн §65 (детерминированный safety-гейт длины/пустоты; контекст: воспоминания M4, отношение, состояние мира; ответ NPC через M4-транспорт; при llm-off — детерминированный fallback-ответ, П2); GET /dialogue/{id} — история; 3 suggested responses (§64, детерминированные варианты) + свободный текст игрока. NPC-ответы не мутируют мир (MVP: suggestions — текст; auto-commit действий NPC — follow-up).
  - REST чтение (§80): GET /world, GET /world/events?since=, GET /characters/{id}, GET /characters/{id}/inventory, GET /locations/{id}.
  - WebSocket (§79): /ws?token= — поток новых world_events (cursor по id) + события CONTROL_CHANGED/GOAL_QUEUED/AI_DECISION; ping/pong; авторизация токеном.
  - Конфиг: раздел `api:` (enabled: false — headless по умолчанию; host/port/session_ttl_min/cookie_name); CLI `vl1 serve` (uvicorn, только при api.enabled).
  - События: closed set 22 → 24 (`CONTROL_CHANGED`, `GOAL_QUEUED`).
- **Excluded**:
  - PostgreSQL/PostGIS/Redis (ops-стек §107/VPS) — SQLite/SQLAlchemy; перенос — ops-этап.
  - Admin panel, роли moderator/admin/developer (§82-83) — поле role зарезервировано.
  - POST /visual/generate, portraits, Flux (§105).
  - Email-верификация, восстановление пароля, rate-limiting (production-hardening — ops-этап).
  - Реальные LLM-ответы NPC в CI (stub/fallback детерминированы; реальный транспорт — конфиг M4).
  - Автоисполнение извлечённых действий NPC из диалога (§65 commit valid actions — follow-up; фиксируется как событие-только).
  - several players per user, PvP-consent механики (§4 interaction_permissions — follow-up).
- **Protected boundaries**:
  - П1: любое действие игрока/NPC — через validators; ни один endpoint не мутирует деньги/инвентарь напрямую.
  - П2: мир жив без API и без LLM (api.enabled=false — headless путь не изменён ни байтом).
  - П4: 18+ gate на регистрации; возраст — серверное поле; свободный текст диалога не создаёт consent-обязательств.

## Requirements

- **R1: Гейты и байт-идентичность**. Весь API-слой активен только при `api.enabled` (приложение создаётся фабрикой; headless CLI не зависит от него). Симуляционное поведение изменяется ТОЛЬКО для персонажей с `user_id IS NOT NULL`: AUTONOMOUS — текущее поведение; DIRECT — choose_action не вызывается; GUIDED — цель игрока конвертируется в задачу, utility работает дальше. Персонажи без владельца — всегда AUTONOMOUS: прогоны M1-M4 (AE5''' keystone) байт-идентичны.
- **R2: Auth**. register: username 3-32 `[a-zA-Z0-9_]`, email валидный, password ≥ 8, `age_confirmed=true` (иначе 422); хэш PBKDF2-HMAC-SHA256, salt 16B, 100_000 итераций. login: проверка, токен (HMAC-SHA256, payload {user_id, role, exp}) в httpOnly cookie `vl1_session` + Accept Bearer. me/logout. Токен истекает (session_ttl_min, дефолт 720). Ownership: все персонаж-эндпоинты проверяют владение (403/404).
- **R3: Создание персонажа**. POST /characters {name, sex, age?} — age ≥ 18 (П4); генератор даёт профессию/жильё/деньги (реиспользование generator.py); `user_id` + `control_mode='AUTONOMOUS'`; лимит 1 персонаж/пользователь (409). Имя уникально в мире (409).
- **R4: Control modes**. POST /characters/{id}/control {mode} — валидные значения enum; смена пишется в `characters.control_mode` + событие `CONTROL_CHANGED` {from, to}; при → DIRECT текущая STARTED-задача (source != player) получает status='CANCELLED' (progress_tick игнорирует); при → AUTONOMOUS/GUIDED задачи игрока завершаются штатно. DIRECT: в progress_tick перед choose_action — если control_mode='DIRECT', задача не создаётся (character просто не получает новой utility-задачи; needs-тик работает).
- **R5: Direct actions**. POST /actions: {character_id, action_type, params}; action_type ∈ реестра; validate() → ok: CharacterTask(status='STARTED', source='player', started_at=now, ends_at=now+duration из конфига действия) + 201 с task id; fail: 422 {reason}. Ownership обязателен; DIRECT и GUIDED разрешают, AUTONOMOUS → 409 (персонаж сам решает; чтобы игрок вмешался — сначала перехват). GET /actions/{id}: {status, task_type, started_at, ends_at}.
- **R6: Цели**. Парсер Tier-0: нормализация текста → поиск сущностей (имена локаций, ключи предметов, имена персонажей) и глаголов («иди/съезди/отправься» → travel_to; «купи/добудь» → acquire_items; «поговори/познакомься» → socialize_with); при llm.enabled текст идёт через транспорт с schema_hint='goal'. Валидация: destination — существующая location; items — существующие ключи shop-инвентаря; target — живой персонаж. Невалидно → 422 {reason}. Цель в очереди: status='queued' + `GOAL_QUEUED`. Исполнение (GUIDED): при обработке персонажа первая queued-цель конвертируется в CharacterTask(source='goal') → status='active'; завершение задачи (успех/провал) → цель done/failed (события не дублируются). deadline_day — мягкое поле (не блокирует MVP, фиксируется в params).
- **R7: Чат**. POST /dialogue/start {npc_id}: NPC жив и существует (404/422) → dialogue_sessions (participants=[user_character, npc], location=user_character.location_id, context={}) → 201 session id. POST /dialogue/{id}/message {content}: content 1..2000; сохранение message(sender='user'); NPC-ответ через §65: memories (M4 select), relationship, world state → transport (llm-on) ИЛИ fallback (llm-off: детерминированный шаблон с именем NPC, П2) → message(sender='npc') + `suggested_responses` (3 детерминированных варианта: приветствие/вопрос о делах/прощание — адаптированные именами). Ответ возвращается в HTTP-ответе. GET /dialogue/{id}: {session, messages[]}. Чат не мутирует мир (кроме журнала).
- **R8: REST чтение**. GET /world: {id, day, clock, population}; GET /world/events?since=<id>&limit=: события по возрастанию id; GET /characters/{id}: публичные поля (id, name, job, location, needs, control_mode, owner: bool); GET /characters/{id}/inventory: предметы из items (owner_character_id); GET /locations/{id}: {id, type, name, characters_here[]}. Auth: /world и /locations публичны; /characters/* — владелец или публичный слепок (MVP: любой аутентифицированный видит публичный слепок; владелец видит needs).
- **R9: WebSocket**. GET /ws?token= — HMAC-проверка (401 при невалиде); после connect сервер шлёт {type:'hello', cursor}; далее поллит world_events id > cursor (интервал 1.0с) и шлёт пачками {type:'events', events:[...]}; клиент шлёт {type:'cursor', id} — сервер подтверждает {type:'cursor', id}. Отключение — чистое закрытие. Никаких мутаций по WS (MVP: только поток).
- **R10: Контракт событий** (22 → 24): `CONTROL_CHANGED` (actor = персонаж; payload {user_id, from, to}); `GOAL_QUEUED` (actor = персонаж; payload {goal_id, goal_type, params, deadline_day}). Consumer: WS-поток, отчёт/тесты.
- **R11: Инварианты** (2 новых, за гейтом владения — проверяются всегда, тривиально пусты без игроков): `player_integrity` — у персонажа с user_id владелец существует; control_mode ∈ enum; максимум 1 персонаж на пользователя; `goal_integrity` — goal_type ∈ каталога; status ∈ enum; character существует.

## Non-Functional Requirements

- **Security**: пароли — PBKDF2 (не хранятся в открытом виде); токены подписаны (secret из env `VL1_SECRET`, дефолт dev-значение с warning в лог при production-прогоне); ownership на всех мутирующих эндпоинтах; 18+ gate; свободный текст ≤ 2000 симв.
- **Reliability**: API-ошибки не влияют на цикл симуляции (отдельная фабрика приложения; симуляция — тот же процесс, но headless-путь идентичен); П2.
- **Performance**: чтение world/events — по курсору (limit ≤ 500); WS-поллинг 1с; 7д-прогон с API-фабрикой (не сервером) не замедляет симуляцию (keystone-тест).
- **Operability**: `vl1 serve` — единственная точка запуска; /auth/me для диагностики; все эндпоинты возвращают JSON-ошибки {detail}.
- **Compatibility**: schema 0.5.0 (create_all); события 22 → 24; CLI-флаги M1-M4 неизменны; отчёт без ключей игрока.

## Acceptance Evidence

- **AE1'''**: E2E через TestClient (llm-off, api-фабрика): register → login (cookie) → me → create character → control DIRECT (текущая utility-задача отменена, новых utility-задач нет) → POST /actions WORK (201) → задача исполняется движком (TASK_COMPLETED +1) → POST /actions в AUTONOMOUS → 409 → guided goal «иди на кухню» (queued + GOAL_QUEUED + конвертация в MOVE в GUIDED) → невалидная цель → 422 → dialogue/start + message → NPC-ответ (fallback, детерминированный) → GET /dialogue/{id} история → 403 при попытке управлять чужим персонажем.
- **AE2'''** (keystone): headless-прогоны M1 (org-off), M2 social-on, M3 org-on, M4 llm-on(stub) — events_by_type и пины байт-идентичны; control_mode/AUTONOMOUS по умолчанию; в отчётах нет ключа `player`.
- **AE3'''**: юнит: хэш/верификация пароля; подпись/истечение токена; register без age_confirmed → 422; короткий пароль → 422; дубль username/email → 409; невалидный action_type → 422; ownership → 403.
- **AE4'''**: персистентность: file-DB регистрация+персонаж → новое приложение на той же БД → login + GET /characters/{id} работают.
- **AE5'''**: WS: connect → hello(cursor) → шаг движка → события приходят пачкой с id > cursor; невалидный токен → 401/closed.
- **AE6'''**: config sensitivity: session_ttl_min → exp в токене; api.enabled=false → vl1 serve отказывается стартовать (exit ≠ 0).
