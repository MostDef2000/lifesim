# Задачи: 009-web

## Chunk A — Статика и auth-флоу

- [x] T1: backend/app/web/ (index.html, app.js, style.css); раздача / и /static в app.py.
- [x] T2: JS: hash-роутер, api()-helper (credentials, 401→login, toast), login/register/logout формы (18+).
- [x] T3: tests/m9/test_chunk_a.py: смоук статики, API-роуты не перекрыты.

## Chunk B — Мир, действия, инвентарь, профиль

- [x] T4: world-вид: шапка (GET /world), needs-бары + деньги + задачи (GET /characters/{cid}), карта (GET /locations + POST MOVE), кнопки действий (POST /actions), goals (GUIDED).
- [x] T5: inventory-вид read-only (use/eat/drop-эндпоинтов в API нет — механика через действия движка; зафиксировано в spec R6/plan), profile-вид (control mode, logout).
- [x] T6: tests/m9/test_chunk_b.py: UI-journey смоук + 401-гейт.

## Chunk C — Чат, WS, admin, external, финал

- [x] T7: chat-вид (NPC из characters_here, dialogue start/message, подсказки).
- [x] T8: WS-лента (token из /auth/me? — токен в cookie; ws?token= из cookie недоступен — передать через /auth/me response? Проверить ws auth: verify_token(token) — токен нужен явно; MVP: /auth/me возвращает ws_token? — добавить поле ws_token в /auth/me) + polling-фолбэк.
- [x] T9: admin-вкладка (overview/pause/resume/timescale/audit; роль-гейт), external-форма (GET /external, TRAVEL_EXTERNAL).
- [x] T10: tests/m9/test_chunk_d.py: XSS-гигиена grep, ws_token в /auth/me, admin-смоук.
- [x] T11: README-раздел Web (запуск, скриншот-чеклист ручного смоука), tasks tick, ruff + check.sh + полный suite, PR.
