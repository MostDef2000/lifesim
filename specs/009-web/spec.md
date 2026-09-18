# Спецификация функционала: M9 — Web Client (§77-78): браузерный MVP-интерфейс

## Outcome

Игра играется из браузера без curl: регистрация/логин (18+), создание персонажа, основной экран §78 (время/статус, карта, состояние/задачи/отношения, лента событий), действия (WORK/EAT/SLEEP/перемещение/TRAVEL_EXTERNAL), диалог с NPC, инвентарь. Клиент — одностраничный статический SPA (HTML+JS, без сборки и внешних CDN), обслуживается FastAPI из готового API (§80). WebSocket (§79) — live-лента событий; при недоступности WS — polling-фолбэк.

## Scope

- **Included**:
  - Статика: `backend/app/web/` (index.html, app.js, style.css); FastAPI отдаёт `/` → index.html + `/static/*`. Route `/` требует auth? Нет: `/` — лендинг-редирект по сессии (нет cookie → форма входа; есть → /world-вид).
  - Экраны (вкладки одного SPA):
    - **login/register** (§77): формы → POST /auth/register|login; age_confirmed чекбокс (18+, П4); ошибка инлайн.
    - **world** (§78 MVP): шапка (game day/time, пауза из /world), панель персонажа (needs-бары из /characters/{id}, живость, деньги), задачи (planned/active из /characters/{id}/tasks… — фактический эндпоинт: /actions активные? — используем существующие GET /characters/{id} + /characters/{id}/inventory + events), карта (список локаций из /locations, текущая подсвечена, MOVE кликом), лента событий (WS §79, fallback: GET /world/events?since=).
    - **chat** (§77): выбор NPC (список из /characters NPC на той же локации? — MVP: выбор из /characters?per=… фильтр id like npc_), POST /dialogue/start, сообщения, suggested_responses кликабельны.
    - **inventory** (§77): GET /characters/{id}/inventory таблицей; действия USE/EAT/DROP через существующие POST /characters/{id}/inventory/{oid}/use|eat|drop (проверить фактические роуты).
    - **actions** (§77 в /world): кнопки WORK/EAT/SLEEP/DRINK/SOCIALIZE → POST /actions; TRAVEL_EXTERNAL — форма (сервис из GET /external, purpose, items) — Порт как текущая локация.
    - **profile** (§77): username/email/role, control mode переключатель (AUTONOMOUS/DIRECT/GUIDED), выход.
    - **admin** (§77): только для role admin/developer (скрыт иначе): /admin/overview таблицей + pause/resume/timescale кнопки + audit. Moderator — overview/audit read-only.
  - WS-клиент (§79): подключение /ws?token=..., рендер events в ленту; авто-reconnect; fallback polling каждые 5s если WS не открылся.
  - Полировка: fetch с credentials (cookie), обработка 401 (редирект на login), 422/409/403 → toast.
- **Excluded**: карта-граф с координатами (MVP — список локаций), погода (§71 не сделан), Flux-визуалы в профиле (кнопка POST /visual/generate — есть в API, показываем asset_id без картинки? — excluded: показ), mobile-адаптация (минимальный media-query допустим), i18n (RU-тексты), сборщики/фреймворки, unit-тесты JS (ручнаяSmoke через API-тесты; Playwright excluded).
- **Protected boundaries**:
  - П1: клиент ничего не считает сам — только вызывает существующий API; никакого дублирования бизнес-логики.
  - П2: статика не влияет на headless (tests/m1-m4); новые роуты — только раздача файлов.
  - П4: register-форма требует age_confirmed (сервер уже валидирует).

## Requirements

- **R1: Раздача статики**. `GET /` → index.html (200); `/static/app.js`, `/static/style.css` → 200; отсутствующие файлы — 404. Не ломает существующие API-роуты (порядок регистрации: статика в конце).
- **R2: Auth-флоу**. register (18+ чекбокс обязателен) → auto-login (существующий эндпоинт возвращает cookie) → переход к world-виду. 401 из любого API-вызова → показ login-формы.
- **R3: Мир на экране**. После login: шапка с day/мин (из GET /world), needs-бары (hunger/thirst/energy/social — 0-100), деньги, список задач, локации списком с подсветкой текущей, клик по локации → POST /actions MOVE (или needs_move-подсказка от 422).
- **R4: Действия**. Кнопки базовых действий; ответ 201 → тост «задача принята»; 422/409 → текст detail.
- **R5: Чат**. Диалог с NPC: старт сессии, отправка сообщения, отображение ответа + подсказок (клик = отправить). История — в рамках сессии (память M4 excluded).
- **R6: Инвентарь**. Таблица предметов (тип/состояние); кнопки существующих действий над предметом; пустой инвентарь — заглушка.
- **R7: События live**. WS подключается после login; события добавляются в ленту (тип/актор/время); при disconnect — reconnect с backoff; polling-фолбэк GET /world/events?since=cursor.
- **R8: Профиль**. Данные юзера, control mode (GET/POST /characters/{id}/control), logout.
- **R9: Admin-вкладка**. Видима только role ∈ {moderator, admin, developer}; moderator — read-only (overview + audit); admin — + pause/resume/timescale/disable. 403 от API скрывает вкладку.
- **R10: Внешние поездки**. Форма TRAVEL_EXTERNAL: выбор сервиса (GET /external), purpose, items (для purchase); ошибки валидатора показываются (needs_move — «иди к пирсу» кнопкой MOVE к порту).

## Non-Functional Requirements

- **Security**: никаких секретов в статике; cookie httpOnly используется как есть; XSS — только textContent/эскейпинг (без innerHTML для пользовательских данных); CSP-заголовок опционально.
- **Reliability**: fetch-ошибки → тост, не ломают UI; WS reconnect ≤ 3 попыток затем polling.
- **Performance**: без сборки; один JS-файл ≤ ~1000 строк; polling интервал 5s.
- **Compatibility**: современные браузеры (ES2020, fetch, WebSocket); RU-язык.

## Acceptance Evidence

- **AE1''''''**: HTTP-смоук через TestClient: `/` 200 + содержит форму логина; `/static/app.js` 200; полный user-journey через API-вызовы, которые дергает UI (register→create→actions→dialogue→inventory) — уже покрыто, но добавляем сквозной тест «UI-сценарий» теми же ручками.
- **AE2''''''**: keystone: headless M1-M4 пин-идентичность (статика не влияет).
- **AE3''''''**: стата-роуты не перекрывают API: /world, /characters/... отвечают как прежде (смоук).
- **AE4''''''**: auth-гейт: без cookie GET /world/events → 401 (UI показывает логин).
- **AE5''''''**: XSS-гигиена: app.js не использует innerHTML для данных API (проверка grep-тестом).
- **AE6''''''**: admin-вкладка: overview отдаёт поля для admin-роли; для user-роли UI скрывает (проверка логики в JS — тестом не покрывается, ручной смоук в README).
