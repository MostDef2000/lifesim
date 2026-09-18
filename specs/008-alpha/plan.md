# План имплементации: 008-alpha

## Архитектура

- **Модели**: AdminAuditLog; User += `disabled` Column (Boolean, default False). Schema 0.8.0.
- **Конфиг**: AdminConfig {registration_enabled, max_players, rate_limit_enabled, global_rpm, auth_rpm, backup_dir, backup_keep}; Settings.admin; default.yaml.
- **auth.py**: check в register (settings.admin), login (user.disabled → 403).
- **app.py**: current_user → user.disabled check; require_role(role) factory; admin-роуты (overview/world/characters/tasks/users/audit); rate-limit middleware (sliding window dict[ip]→deque[ts]); request-id middleware (лог + заголовок).
- **ws.py**: app.state.ws_connections += / -= на accept/disconnect (для overview).
- **cli.py**: backup subcommand (+_run_backup): sqlite3.backup + shutil.copytree assets + retention sorted by mtime.
- **invariants.py**: admin_integrity (audit → admin-role users; disabled users без активных задач).

## Последовательность

| Chunk | Содержание | Тесты |
|---|---|---|
| A | schema 0.8.0 (admin_audit_log, users.disabled), AdminConfig, require_role + guards в auth, rate-limit middleware, request-id middleware, ws counter | test_chunk_a: юнит rate-limiter, register-контролы, disabled-login, request-id |
| B | admin-роуты (overview/pause/resume/timescale/teleport/cancel/disable+enable/audit) + инвариант admin_integrity | test_chunk_b |
| C | backup CLI, AE1-6, README-раздел Alpha/ops, tasks tick, полный suite | полный suite + check.sh |

## Риски / решения

- **users.disabled через create_all**: новая колонка не добавится в существующую БД (SQLite ALTER нужен вручную); тесты — свежие БД; прод-миграция документирована в README (ALTER TABLE users ADD COLUMN disabled...). Приемлемо для стадии.
- **Rate limiter и тесты**: окно 60s — в тестах подменяем window_sec через AdminConfig (rate_limit_window_sec добавить для тестируемости).
- **WS counter**: TestClient WebSocket — счётчик проверяется в AE (опц.).
- **Teleport**: мутирует location_id напрямую — единственная админ-операция с прямой правкой; фиксируется в audit (R6); игровой lifecycle не задействован (мир-журнал не фальсифицируем).
