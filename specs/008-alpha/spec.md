# Спецификация функционала: M8 — Public Alpha (§107): admin-инструменты, модерация, бэкапы, мониторинг, rate limits

## Outcome

Мир готов к публичному запуску: администратор видит состояние сервера (§83) и выполняет административные действия (§84: pause/resume, time scale, teleport, cancel task, disable account) с полным audit log (§86). Роли §82 enforced (user|moderator|admin|developer). Регистрация управляется конфигом (§85/§27: включение, лимит игроков; баны). Rate limits (§27) защищают auth-эндпоинты и API. `vl1 backup` делает консистентный снапшот БД + визуальных ассетов с retention (§87). Observability (§88): request_id в логах. Domain/HTTPS — VPS-ops (§26, вне репо).

## Scope

- **Included**:
  - Таблица `admin_audit_log` (§86): id, world_id (nullable), admin_user_id, action (строка: pause_world|resume_world|set_timescale|teleport_character|cancel_task|disable_account|enable_account|set_role), target_type, target_id, payload JSON, wall_created_at. Schema 0.7.0 → **0.8.0** (37 таблиц). Users += колонка `disabled` (Boolean, default false) — модерация аккаунтов.
  - Роли (§82): `require_role(...)` guard — moderator: GET /admin/overview; admin+developer: все admin-действия. Enforce в current_user: disabled → 403 «account disabled».
  - Регистрация-контролы (§85): AdminConfig {registration_enabled: true, max_players: 0 (unlimited), rate_limit_enabled: true, global_rpm: 120, auth_rpm: 10, backup_dir: "backups", backup_keep: 7}. Register: 403 «registration disabled», 409 «max players reached». invite_only — excluded (MVP: бесплатные открытые регистрации §107 "free registration").
  - Admin API (§83/§84):
    - `GET /admin/overview` — users count, players, NPCs, active tasks, events last hour, world clock (ts/day/paused/timescale), schema version, uptime_sec, ws_connections.
    - `POST /admin/world/pause` | `/resume` | `/admin/world/timescale {time_scale}` — WorldClock поля + audit.
    - `POST /admin/characters/{cid}/teleport {location_id}` — location_id валиден, alive; audit + CHARACTER_MOVED? (нет: телепорт — админ-операция, только audit; событие CHARACTER_MOVED не эмитим — мир-журнал остаётся игровым).
    - `POST /admin/tasks/{task_id}/cancel` — штатная отмена (status=cancelled, ends_at=now_ts как в control-переходе).
    - `POST /admin/users/{user_id}/disable` | `/enable` — флаг + audit; disabled юзер не логинится (403) и его токены отклоняются (current_user).
    - `GET /admin/audit?limit=` — последние записи audit log.
  - Rate limits (§27): ASGI middleware, in-memory sliding window per-IP: auth (/auth/*) auth_rpm, прочее global_rpm → 429 + Retry-After. Ограничение честно документировано (per-process; Redis excluded).
  - Observability (§88): HTTP middleware: X-Request-ID (генерируется, если клиент не прислал), лог-строка `request_id method path status duration_ms`; exception-handler не маскирует 500 (request_id в body).
  - Metrics (§89, MVP-подмножество): в `GET /admin/overview`: requests_total (по статус-классам), ws_connections (app.state счётчик WS-коннектов), events_last_hour, uptime. Полный список §89 (AI latency, DB latency) — excluded (нет AI worker/PG в стадии).
  - Backups (§87): `vl1 backup --config ...` — sqlite3 backup API → `{backup_dir}/world_<ts>.db` + копия `data/visual_assets` → `{backup_dir}/assets_<ts>/` + retention last N; exit 0/2. README-раздел ops: cron-совет, PostgreSQL/HTTPS — на VPS-этапе.
- **Excluded**: domain/HTTPS/TLS (VPS-ops, sysadmin handoff); PostgreSQL daily backup (SQLite стадия); invite_only whitelist; rollback erroneous state (manual ops); AI worker restart (нет отдельного worker в стадии); полный §89 (latency-метрики).
- **Protected boundaries**:
  - П1: admin-действия идут через штатные механизмы (cancel = как control-отмена; teleport — прямая правка location_id админом — допустимое исключение, зафиксировано в audit; мир-события не фальсифицируются).
  - П2: headless M1-M4 не затронут (admin-роуты только в API-фабрике; keystone AE2'''''').
  - П4: n/a.

## Requirements

- **R1: Гейты/совместимость**. Admin-роуты зарегистрированы всегда; guard по роли. Headless-идентичность сохранена (новая таблица пуста, events не меняются).
- **R2: Роли** (§82). require_role("moderator") → role ∈ {moderator, admin, developer}; require_role("admin") → {admin, developer}. Banned: current_user → 403; login → 403 «account disabled».
- **R3: Регистрация** (§85). disabled → 403; max_players (count users) достигнут → 409. Оба ответа детерминированы.
- **R4: Admin overview** (§83). Все поля детерминированы от БД; ws_connections из app.state (0 без WS).
- **R5: Мир-действия** (§84). pause/resume/timescale — валидация time_scale > 0 (иначе 422); каждое действие пишет admin_audit_log.
- **R6: Персонаж-действия** (§84). teleport: персонаж и локация существуют (404/422); cancel: задача существует и активна/planned (409 если terminal); disable/enable: user существует (404). Всё с audit.
- **R7: Audit** (§86). Каждое admin-действие → строка (payload с деталями: старое/новое значение). GET /admin/audit — moderator+.
- **R8: Rate limits** (§27). Sliding window 60s; auth-окно отдельно; 429 c Retry-After; middleware до роутов; X-Forwarded-For уважается (за reverse proxy); disabled в конфиге → выкл.
- **R9: Observability** (§88). Каждый запрос: request_id (входящий X-Request-ID или uuid4), лог-строка после ответа; ответ содержит X-Request-ID.
- **R10: Backups** (§87). `vl1 backup`: консистентный снапшот (sqlite3 Connection.backup), копия assets-директории (если есть), retention: удаляет старее keep; идемпотентен; НЕ трогает живую БД (только чтение).
- **R11: Инвариант** `admin_integrity`: audit-строки ссылаются на существующих admin-юзеров (role admin/developer); disabled-юзеры не имеют активных (non-terminal) задач, созданных после бана (простая проверка: у disabled юзера нет planned/active задач).

## Non-Functional Requirements

- **Security**: admin-роуты — 401/403 без роли; rate limit на auth_first-line; пароли/секреты не логируются.
- **Reliability**: middleware ошибки не роняют приложение; backup консистентен на живой БД (WAL).
- **Performance**: rate limit O(1) на запрос (deque per IP, prune); overview ≤ 10 запросов.
- **Compatibility**: schema 0.8.0; CLI: +backup subcommand; события мира не меняются (27).

## Acceptance Evidence

- **AE1''''''**: E2E admin: admin-юзер (role=admin в БД) → overview → pause → timescale → resume → teleport → cancel task → disable юзера → его login 403 → audit содержит все действия → rate limit: 11-й auth-запрос за минуту → 429.
- **AE2''''''**: keystone: headless M1-M4 пин-идентичность; admin_audit_log пуст; отчёт без admin-ключей.
- **AE3''''''**: регистрация-контролы: registration_enabled=false → 403; max_players=N → 409 на N+1.
- **AE4''''''**: бэкап: `vl1 backup` создаёт .db (открывается sqlite, таблицы читаются) + retention удаляет старые.
- **AE5''''''**: негативы: не-admin → 403 на все admin-роуты; moderator читает overview, но не может pause (403); disabled-юзер — 403 на API.
- **AE6''''''**: observability: X-Request-ID присутствует в ответе и в логе; несуществующий user/task → 404; timescale ≤ 0 → 422.
