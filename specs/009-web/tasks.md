# Задачи: 009-web

## Chunk A — Статика и auth-флоу

- [ ] T1: backend/app/web/ (index.html, app.js, style.css); раздача / и /static в app.py.
- [ ] T2: JS: hash-роутер, api()-helper (credentials, 401→login, toast), login/register/logout формы (18+).
- [ ] T3: tests/m9/test_chunk_a.py: смоук статики, API-роуты не перекрыты.

## Chunk B — Мир, действия, инвентарь, профиль

- [ ] T4: world-вид: шапка (GET /world), needs-бары + деньги + задачи (GET /characters/{cid}), карта (GET /locations + POST MOVE), кнопки действий (POST /actions), goals (GUIDED).
- [ ] T5: inventory-вид (GET inventory + use/eat/drop), profile-вид (control mode, logout).
- [ ] T6: tests/m9/test_chunk_b.py: UI-journey смоук + 401-гейт.

## Chunk C — Чат, WS, admin, external, финал

- [ ] T7: chat-вид (NPC из characters_here, dialogue start/message, подсказки).
- [ ] T8: WS-лента (token из /auth/me? — токен в cookie; ws?token= из cookie недоступен — передать через /auth/me response? Проверить ws auth: verify_token(token) — токен нужен явно; MVP: /auth/me возвращает ws_token? — добавить поле ws_token в /auth/me) + polling-фолбэк.
- [ ] T9: admin-вкладка (overview/pause/resume/timescale/audit; роль-гейт), external-форма (GET /external, TRAVEL_EXTERNAL).
- [ ] T10: tests/m9/test_chunk_d.py: XSS-гигиена grep, ws_token в /auth/me, admin-смоук.
- [ ] T11: README-раздел Web (запуск, скриншот-чеклист ручного смоука), tasks tick, ruff + check.sh + полный suite, PR.
