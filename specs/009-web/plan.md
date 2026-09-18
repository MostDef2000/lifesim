# План имплементации: 009-web

## Архитектура

- **Раздача**: `backend/app/web/` — статика (index.html, app.js, style.css). В `app.py` в конце `create_app`: mounted routes `/` (FileResponse index.html) и `/static` (StaticFiles). НЕ монтируем до API-роутов.
- **app.js**: vanilla SPA — hash-навигация (#/login, #/world, #/chat, #/inventory, #/profile, #/admin); state = {user, character, world, ws}; `api()` helper (fetch credentials, 401 → login-вид, ошибка → toast); WS + polling-фолбэк; textContent-only рендер.
- **style.css**: grid- layout §78 (шапка / карта+панель / лента), тёмная тема, needs-бары.

## Последовательность

| Chunk | Содержание | Тесты |
|---|---|---|
| A | Раздача статики + index.html каркас + auth-флоу JS (login/register/logout, 401-гейт) | test_chunk_a: смоук статики (/, /static/*), API не перекрыт |
| B | world-вид: шапка/needs/деньги, карта+MOVE, действия, задачи; inventory-вид; profile+control mode | test_chunk_b: UI-journey API-смоук (регистрация→действия→инвентарь), 401 без cookie |
| C | chat + WS-лента (+fallback), admin-вкладка, external-форма; README-раздел; tasks tick; полный гейт | test_chunk_d: XSS-гигиена grep, WS event flow (существующий ws-тест остаётся) |

## Риски / решения

- **JS без тестов** (Playwright excluded): покрытие через HTTP-смоук тех же эндпоинтов + grep-тест XSS-гигиены; ручной смоук чеклист в README.
- **/characters NPC-список для чата**: отдельного «кто на локации» роута нет — MVP: выбор NPC из всех (id prefix npc_) через GET /world/events? Нет: используем GET /locations/{loc_id} (обитатели?) — проверить; fallback: UUID-агностик список из /characters/{cid} (same-location info есть в ответе? — проверить поле).
- **CSP**: без заголовка (MVP), XSS-гигиена через textContent.
- **Форма external**: покупки items — простой textarea JSON (MVP), валидация сервером.
