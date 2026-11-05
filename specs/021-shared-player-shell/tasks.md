# 021 — tasks

## Волна 1 — код (W1-code-01 → W1-code-fix-05)

- [x] app.js: хук в `route()` — bootstrap мира для часов на cold-entry
      не-world маршрутов; исключены `#/world`, `#/location/*` и
      unknown-hash fallback (сами грузят /world).
- [x] app.js: `hydrateWorldClock()` — single-flight В ПРЕДЕЛАХ контекста
      сессии (`{user-ссылка, charId}`), fire-and-forget, без feed/WS/poll.
- [x] app.js: stale-защиты — дроп по более новому полёту / смене контекста /
      установленному S.world; финализатор снимает только свой слот
      (ownership-гейт ДО снятия слота, публикации и метки отказа — S21).
- [x] app.js: `clockPendingLabel()` — честное «н/д» после отказа переживает
      ре-рендеры topbar; topbar остаётся чистым рендерером.
- [x] app.js: 401 БЕЗ `noGlobal401` — узкий STALE-предикат bootstrap-полёта
      в `api()` (смена контекста ИЛИ потеря слота `worldGet !== flight`)
      отбрасывает stale-ответ ДО глобальной деавторизации; CURRENT 401 —
      fail-closed как в базе + сброс `S.world` (fix03; S13/S14 зелёные).
- [x] app.js: общий joinable GET /world — `api()` присоединяет pending
      bootstrap-полёт того же контекста; обёртка promise'а отдаёт тело
      только при актуальном владении+контексте, иначе `null` (S17/S18/S22).
- [x] app.js: валидация тела 200 `/world` до публикации (S19/S20).
- [x] app.js: граница null/stale В КОНСУМЕРЕ — viewWorld, viewLocation и
      pollEvents дропают `null` (+ захваченный контекст / живой
      S.character) сразу после присоединённого /world ДО лишних await'ов,
      общей записи / `.day` / retry/toast/метки отказа — молча и без
      pollFails++; повторные проверки перед общими записями сохранены;
      финализаторы bootstrap'а — ранний return по владению слотом (S21/S22).
- [x] app.js: guard свежести публикации — viewWorld/viewLocation пишут
      S.world/S.weather, poll — только S.world+часы, лишь если нет уже
      опубликованного более нового мира (`game_timestamp`); экраны
      рендерятся с актуальным состоянием (S23).
- [x] app.js: граница null/stale в консумерах — null+контекст дропаются
      сразу после присоединённого /world ДО лишних await'ов; повторные
      проверки перед общими записями; финализаторы bootstrap'а — ранний
      return по владению слотом (S21/S22).
- [x] style.css: `.tabs { flex-wrap: wrap }` — нативный перенос вкладок.
- [x] style.css: scoped `.desire-shell textarea` (фон/текст/граница/
      placeholder/focus) на существующих provisional-токенах.
- [x] style.css: `.chat-error` текст `#93301f`, граница `var(--warn)`.
- [x] Пин-дисциплина: :root байт-идентичен, `--w-line:` == 4, conn-banner
      последним, chat-блок 0.35==5 / #8a5a16==3, m132/m238/m175 нетронуты.
- [x] Наблюдаемая верификация fix06 (воркер, 2026-11-04): node --check
      PASS; git diff --check PASS; `uv run ruff check .` — All checks
      passed; `uv run pytest tests/m269/ -q` → 31 passed; фокус 8 каталогов
      (m269,m63,m132,m150,m170,m173,m175,m238) → 96 passed; frozen-хэши
      тестов не изменились.

## Журнал попыток (наблюдаемое; времена, где не указано — unknown)

- W1-code-01 — НЕПОЛНО (дочерняя сессия ses_e5a267e31ffep3O6UOhEmKGmXW):
  app.js +37 (частичный bootstrap: глобальный busy, object-reference guard,
  DOM-only «н/д»), style.css и specs не тронуты. node --check / git diff
  --check — PASS (наблюдано оркестратором; время unknown).
- W1-code-02 — та же дочерняя сессия, ревизия: контекстные объекты,
  user-reference + charId, `clockPendingLabel`, `noGlobal401` (позже снят),
  хук исключает unknown-hash fallback. Отклонена кросс-ревью код-пиров
  (2:0 REQUEST_CHANGES: current-401/дедуп). База H2 на момент попытки:
  15F/13P (оркестратор).
- W1-code-fix-03 (attempt_id W1-code-fix-03, роль code, сессия
  ses_e59eedb81ffes7W9twKULyUsxH) — ЧАСТИЧНО: убран `noGlobal401`, узкий
  stale-предикат 401, S.world-инвалидация (деаут/logout/delete), общий
  joinable GET, валидация тела, ctx-guard viewWorld. Наблюдаемо воркером:
  m269 28/28; фокус 8 каталогов 93 passed. H3-прогон (test-peer) выявил
  S21/S22 — 2F/28P (оркестратор, resumption 2026-11-04T23:04:23Z); база
  H3 17F/13P (воркер).
- W1-code-fix-04 (та же сессия ses_e59eedb81ffes7W9twKULyUsxH, роль code) —
  ПРЕРВАНА владельцем посреди SDD: S21/S22 закрыты (ownership-гейт +
  wrapper-обёртка joinable-promise + locals-then-guard в viewWorld);
  наблюдаемо воркером до прерывания: m269 30/30, фокус 95 passed,
  node --check / git diff --check / ruff PASS. SDD: spec частично (дубль
  C6), plan почти готов, tasks не начат. H4-прогон: test-роль W1-test05
  создала H4-тесты (S23 добавлен test-ролью; оркестратор тесты НЕ
  писал) — база H4 17F/14P (воркер); после fix04 — 1F/30P, RED = S23
  (оркестратор, 2026-11-04T23:26:03Z): locals-then-guard в viewWorld
  публикует СТАРЫЙ мир при out-of-order завершении того же контекста.
- W1-code-fix-05 (сессия ses_e59eedb81ffes7W9twKULyUsxH, роль code) — S23
  закрыт guard'ом свежести + явный null-drop у трёх консьюмеров; SDD-трио
  завершено. Наблюдаемо воркером: m269 31/31, фокус 96 passed, node --check /
  git diff --check / ruff PASS, frozen-хэши тестов не изменились.
- W1-code-fix-06 (та же сессия ses_e59eedb81ffes7W9twKULyUsxH, роль code) —
  правка финальных находок ревью: финализаторы bootstrap'а — ранний return
  по владению слотом ДО снятия слота/публикации/метки; null+контекст
  проверяются сразу после присоединённого /world ДО лишних await'ов
  (повторные проверки перед записями сохранены); late-continuation
  viewWorld использует захваченный ctxCharId вместо живого S.character.id;
  формулировки spec/plan выровнены с фактическими гарантиями (poll
  защищает только S.world+часы). Наблюдаемо воркером: m269 31/31, фокус
  96 passed, node --check / git diff --check / ruff PASS, frozen-хэши
  тестов не изменились. Сверена оркестратором против app-blob
  b1ca74c71468db57f1028669d0c37bcd8a9e99ec; финальное код-ревью вернулось
  и сведено (доказательства: текущий diff + frozen-хэши + полный gate).
- W1-doc-evidence-01 (та же сессия ses_e59eedb81ffes7W9twKULyUsxH,
  роль code, DOCS-ONLY) — сверка наблюдаемой приёмки в SDD: полный gate
  (первый прогон sh_1a8ffdb24001pi1pvu3ule8Zws — инфраструктурный таймаут
  120000 после m233 ~56%, НЕ тестовый отказ; повтор sh_1a902078c001b2YHRESJffEs9p —
  Ruff All checks passed, pytest 1231 passed / 13 deselected / 8825
  warnings / 442.31s; нотификация ~2026-11-05T00:08Z), код-ревью
  W1-code-peer-final-01 PASS (статически, без исполнения; его утверждение
  о «полном ctx-guard'е poll» ложно и в документы НЕ внесено — poll
  фактически: null + живой S.character + guard S.world/часов), test-peer
  W1-test-peer-03 PASS по frozen H4. Гарантии консумеров в spec/plan
  уточнены per-consumer (viewWorld — захват+перепроверка контекста;
  viewLocation — легаси seq/hash/живые guard'ы; poll — БЕЗ полного
  session-guard'а и guard'а свежести погоды). Правок app.js/style.css/
  тестов нет.
- W1-doc-evidence-02 (ТЕКУЩАЯ, та же сессия ses_e59eedb81ffes7W9twKULyUsxH,
  роль code, DOCS-ONLY) — финальные pre-publication факты в SDD:
  браузерная приёмка ВЫПОЛНЕНА (локальный REAL Playwright, fixture-
  controlled API + реальные app/CSS, НЕ аутентифицированный прод;
  финальная 361-строчная scratch-функция SHA256 4cdf81abd93f03d2b4b1de6db049a8fc6988c7e8f4f8443fe755187ac28b2959 —
  15/15 PASS, allPass=true, pageErrors=[], unexpectedAPI[]); UI-воркер
  W1-ui-final-01 (ses_e54c578bcffef8zhVXD46Q8e5X) terminal PASS (5
  скриншотов в shared/); browser-fixture peer W1-test-browser-peer-02 —
  static PASS до паузы; browser-prep: 344-строчный прогон allPass=false
  (3 fixture-GET) → 361 строка allPass=true; MCP-playwright filename-питфолл
  записан кратко. Resumption 2026-11-05T10:21:22Z: app-blob b1ca74c7…,
  CSS-blob 1ec13bd6…, frozen-хэши H4 неизменны; origin/main всё ещё base;
  полный gate 1231 PASS валиден для тех же байтов — повтор не требуется.
  Правок app.js/style.css/тестов нет. Валидатор/pre-publication —
  единственная in-progress; commit/PR/CI/merge/деплой/пробы/JOURNAL —
  пендинг.

## Acceptance ТЕКУЩЕЙ волны (статус на 2026-11-05, doc-evidence-02)

- [x] Behavior-тесты `tests/m269/` — 23 behavior-сценария VM + 8 CSS/syntax
      = 31 pytest-тест; написаны test-ролью ВНУТРИ этой волны (H2–H4);
      код-роль тесты не правит (frozen-хэши неизменны). Test-peer
      W1-test-peer-03 — PASS (статически).
- [x] Полный gate (оркестратор): повтор sh_1a902078c001b2YHRESJffEs9p —
      Ruff All checks passed; pytest 1231 passed / 13 deselected / 8825
      warnings / 442.31s (первый прогон sh_1a8ffdb24001pi1pvu3ule8Zws —
      инфраструктурный таймаут 120000 после m233 ~56%, не тестовый отказ;
      диагноз explorer-runtime ses_e56ff62baffe0TgmW4PkYw9zHb; причина
      более раннего нативного прерывания — UNKNOWN).
- [x] Код-ревью W1-code-peer-final-01 (ses_e56ffc26effeP1unhEnGXt7WuO) —
      terminal PASS; статическая трассировка источника (исполнение
      policy-denied); 3 неблокирующих замечания записаны в plan.md §Риски.
- [x] Браузерная визуальная приёмка — ВЫПОЛНЕНА: локальный REAL Playwright
      (fixture-controlled API, реальные app/CSS, не аутентифицированный
      прод) — финальная 361-строчная scratch-функция (SHA256 4cdf81ab…):
      15/15 сценариев PASS, allPass=true, pageErrors=[], unexpectedAPI[];
      browser-fixture peer W1-test-browser-peer-02 — static PASS до паузы,
      целевой хэш сверен. Browser-prep W1-test-browser-01: холодные/сетапные
      ложные отказы чинились только в scratch (344 строки allPass=false →
      361 строка allPass=true); MCP-playwright filename-питфолд записан в
      plan.md.
- [x] UI-воркер W1-ui-final-01 (ses_e54c578bcffef8zhVXD46Q8e5X) — terminal
      PASS: 5 скриншотов в shared/ (desire 390/1440 до/после, chat-error
      390); 2 ряда навигации на мобильном, без desktop-дрейфа, тёплое
      поле, тёмно-красная ошибка чата; неблокирующие замечания — в plan.md.
- [ ] Валидатор / pre-publication — единственная in-progress стадия.
- [ ] Commit/push/PR; CI на merge-SHA; merge; lane-A деплой + прод-пробы —
      по общему контракту оркестратором; исходы пост-коммитных стадий —
      в канонический issue/JOURNAL по факту (не в pre-commit SDD).
- [ ] JOURNAL/KB-запись волны.
- Ожидание пользователя: НЕТ (та же одобренная цепочка).

## Вне скоупа (осознанно)

Сервер/API/схемы, ассеты/шрифты/зависимости, существующие спеки, ролевая/
диалоговая механика, визуальный пайплайн, семантика auth, прочие палитры
оболочек, финальный выбор палитры/типографики (#55), полный #59 (закрыт
только ограниченный остаток: desire-поле + chat-error в рамках #269).
