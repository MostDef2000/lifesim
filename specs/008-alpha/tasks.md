# Задачи: 008-alpha

## Chunk A — Фундамент: схема, контролы, middleware

- [x] T1: models: AdminAuditLog; User.disabled; schema 0.8.0; tests/db 37.
- [x] T2: AdminConfig (registration_enabled/max_players/rate limits/backup) + default.yaml; tests/m2/m4 (таблицы/события не меняются — события 27 остаются).
- [x] T3: register-контролы (§85) + disabled-login guard в auth/app.
- [x] T4: rate-limit middleware (sliding window, auth/global окна, 429+Retry-After) + request-id middleware (X-Request-ID, лог).
- [x] T5: ws connections counter (app.state).
- [x] T6: tests/m8/test_chunk_a.py.

## Chunk B — Admin API

- [x] T7: require_role guard; GET /admin/overview (§83: users/players/NPC/tasks/events/clock/schema/uptime/ws).
- [x] T8: POST /admin/world/{pause,resume,timescale}; POST /admin/characters/{cid}/teleport; POST /admin/tasks/{id}/cancel; POST /admin/users/{id}/{disable,enable}; GET /admin/audit — всё с audit-строками (§84/86).
- [x] T9: инвариант admin_integrity (R11) + негатив-тест.
- [x] T10: tests/m8/test_chunk_b.py.

## Chunk C — Backups, AE, документация, финал

- [x] T11: `vl1 backup` (sqlite backup API + assets copy + retention) + tests.
- [x] T12: tests/m8/test_chunk_d.py: AE1'''''' E2E, AE2'''''' keystone, AE3'''''' регистрация, AE4'''''' бэкап, AE5'''''' негативы, AE6'''''' observability.
- [x] T13: README-раздел Alpha (admin API, backup runbook, rate limits, migration note), tasks tick, ruff + check.sh + полный suite, PR.
