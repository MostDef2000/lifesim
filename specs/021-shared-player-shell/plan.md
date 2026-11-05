# 021 — plan

## Подход

Минимальное изменение в двух существующих файлах + SDD-трио. Без новых
зависимостей, без сервера/схем, без тестовых правок (тесты — отдельный воркер).

### A. Навигация (#269)

`.tabs` получает `flex-wrap: wrap` (style.css, базовый блок .topbar/.tabs).
До правки min-content ширины nowrap-ленты = сумма всех кнопок → на 390px
переполнение; после — min-content = самая широкая кнопка, лента сжимается и
переносится внутри родительского `.topbar` (у него wrap уже был). Там, где
лента помещалась (десктоп), правило инертно — дрейфа нет.

### B. Часы — bootstrap мира для topbar

- Точка входа: хук в `route()` после auth/character-гейтов и после вычисления
  `placeMatch`, ДО диспетча (байт-пины m238 не тронуты). Условие:
  `S.character && !S.world && routes[hash] && routes[hash] !== viewWorld` —
  исключает `#/world`, неизвестный hash (fallback → viewWorld) и
  `#/location/*`, которые грузят /world сами (нет дублей при гонках маршрутов).
- Состояние: два модуль-левела объекта `{ user, charId }` — `worldGet`
  (in-flight, владелец joinable-слота) и `worldFailure` (последний отказ).
  НЕ поля S (пины m63/m173/m175/m258/m179 фиксируют хвост инициализатора S).
- Контекст сессии: ССЫЛКА `S.user` (login/logout/delete создают новый объект
  или null → старая сессия инвалидирована даже при том же id) + `S.character.id`
  (экранные обновления заменяют объект S.character на каждом чтении
  /characters — id отделяет реальную смену персонажа от same-ID refresh).
- Single-flight в пределах контекста: тот же контекст в полёте → coalesce;
  новый контекст → свой полёт. Слот снимает только владеющий полёт
  (`worldGet === flight`) — условный release не является ownership-guard'ом.
- Общий GET: `api()` для `path === "/world"` GET-вызовов присоединяет
  pending bootstrap-полёт того же контекста (`worldGet.promise`); joinable
  ТОЛЬКО полёт bootstrap'а — ре-диспетчи viewWorld сохраняют исторические
  свежие запросы (счётчики S9=2/S10=1/S11=3). Обёртка promise'а выставляет
  тело наружу только при актуальном владении+контексте, иначе `null`;
  CURRENT-отказы пробрасываются нормально, STALE — никогда не красят UI
  (toast/retry/метка). Граница null — В КОНСУМЕРЕ, сразу после
  присоединённого /world и ДО остальных запросов; гарантии консумеров
  РАЗНЫЕ: `viewWorld` захватывает контекст (user-ссылка + charId) и
  перепроверяет его перед общими записями; `viewLocation` сохраняет
  легаси-guard'ы (seq/hash/живые `!S.user || !S.character`); poll дропает
  только `null`/мёртвый `S.character` — ПОЛНОГО session-guard'а и guard'а
  свежести погоды у poll НЕТ (легаси-форма, вне скоупа). Общее: молчаливый
  дроп без pollFails++ и без общей записи/`.day`/метки отказа (S22).
- Свежесть публикации (S23): контекстный guard не отличает out-of-order
  завершения ОДНОГО контекста — более поздний по завершению, но старый по
  `game_timestamp` ответ не должен перезаписать уже опубликованный более
  новый мир. viewWorld и viewLocation пишут S.world/S.weather только при
  `!S.world || world.game_timestamp >= S.world.game_timestamp`; pollEvents
  тем же предикатом защищает ТОЛЬКО S.world + #clock (его day-boundary
  refetch погоды остаётся как в базе — S.weather свежим fetch'ем);
  экраны при этом рендерятся с актуальным общим состоянием (без зависшей
  «Загрузка…»). Bootstrap публикует только в пустой S.world — деградировать
  не может. Финализаторы bootstrap'а (успех/отказ) проверяют владение
  слотом РАННИМ return'ом ДО снятия слота/публикации/метки — в т.ч. когда
  владение потеряно в микротасковом зазоре между гейтом обёртки и
  обработчиком (S21).
- Ответ: дроп при (a) потере владения слотом, (b) смене контекста, (c) уже
  установленном S.world (не перезаписывать более свежий авторитетный ответ —
  viewWorld/viewLocation/pollEvents). Успех пишет S.world и обновляет #clock
  in-place (паттерн pollEvents).
- Отказ: флаг `worldFailure` = контекст; `clockPendingLabel()` (чистое
  чтение для topbar) показывает «н/д» вместо «…» пока контекст тот же и
  S.world пуст — метка переживает ре-рендеры. Новый запрос (следующий
  route()/reload) сбрасывает флаг — приоритет попытки/успеха. Без циклов.
- Валидация: 200-тело `/world` проверяется в `api()` до публикации
  (не-пустой не-массив объект, `game_timestamp` — конечное неотрицательное
  число); иначе throw «некорректный ответ мира» → честное «н/д»/retry.
- 401: БЕЗ `noGlobal401`. В `api()` узкий предикат STALE для bootstrap-полёта
  (приватный opt `worldBootstrap`, вырезается до fetch): смена
  сессии/персонажа в полёте ИЛИ потеря слота (`worldGet !== flight`) →
  отброс ДО глобальной мутации. CURRENT 401 и все прочие вызовы —
  fail-closed как в базе (`S.user = null` + reroute; пин m132 сохранён) с
  единственным добавлением fix03: сброс `S.world = null` (кэш мира не
  переживает сессию; то же в logout/delete) — путь НЕ байт-прежний.

### C. Контролы

- `.desire-shell textarea` (после пин-строки `.desire-shell .sugg:hover …`):
  фон `var(--w-panel-deep)`, текст `var(--w-text)`, граница
  `rgba(120, 96, 64, 0.35)` (вне chat-блока — его счётчики не задеты),
  placeholder `var(--w-dim)`, focus-граница `var(--w-accent)`. Правило
  `#desire-input { min-height: 120px; resize: vertical; }` не тронуто.
- `.chat-error`: только `color` → `#93301f`; граница и смысл `var(--warn)`;
  в chat-блоке не добавляется ни `rgba(120, 96, 64, 0.35)` (пин == 5), ни
  `#8a5a16` (пин == 3).

## Риски

- Гонка «bootstrap ↔ viewWorld» при быстром уходе на «Мир»: закрыта общим
  joinable-полётом (один GET при хэндоффе, S17/S18); без полёта каждый
  консьюмер грузит свежий — существующие refresh'ы миров/локаций/poll
  сохранены (граница: дедуп НЕ превращается в отключение обновлений).
- Genuinely-мёртвая сессия на cold entry: CURRENT 401 bootstrap'а
  деавторизует fail-closed как прежде (S13); при реально истёкшей сессии
  повторный /auth/me тоже 401 → login, без искусственного re-auth цикла.
- Специфичность CSS: правила scoped на `.desire-shell textarea` — глобальное
  правило контролов не тронуто (урок #59: shell-scoped specificity сразу).
- Неблокирующие известные ограничения (ревью fix06; легаси-поведение, вне
  скоупа): 401 legacy-потока upload/reference не сбрасывает `S.world`;
  метка «н/д» — одна попытка на route() (без цикла); malformed-тело в poll
  увеличивает легаси-счётчик conn-banner. Новых задач/правок не создаёт.

## Сверка с попытками (наблюдаемое)

- W1-code-01 (дочерняя сессия ses_e5a267e31ffep3O6UOhEmKGmXW) — НЕПОЛНО:
  только app.js +37 строк (глобальный busy-флаг, object-reference guard,
  DOM-only «н/д», без CSS и SDD).
- W1-code-02 — та же дочерняя сессия, ревизия: контекстные объекты,
  user-reference + charId, `clockPendingLabel`, `noGlobal401` (позже
  снят), хук исключает unknown-hash fallback. Отклонена кросс-ревью
  (2:0 REQUEST_CHANGES: current-401/дедуп).
- W1-code-fix-03 (attempt_id W1-code-fix-03) — частично: убран
  `noGlobal401`, узкий stale-предикат 401, S.world-инвалидация на деауте/
  logout/delete, общий joinable GET, валидация тела, ctx-guard viewWorld.
  Наблюдаемо воркером: m269 28/28; фокус 8 каталогов 93 passed. H3-прогон
  (test-peer) выявил S21/S22 — 2F/28P (оркестратор, resumption
  2026-11-04T23:04:23Z).
- W1-code-fix-04 (сессия ses_e59eedb81ffes7W9twKULyUsxH) — прервана
  владельцем посреди SDD: S21/S22 закрыты (ownership-гейт + wrapper-обёртка
  joinable-promise + locals-then-guard в viewWorld); наблюдаемо: m269 30/30,
  фокус 95 passed, node --check / git diff --check / ruff — PASS. SDD:
  spec частично (дубль C6), plan почти готов, tasks не начат. H4-прогон
  (test-роль W1-test05 добавил S23) выявил регрессию fix04: S23 —
  1F/30P (оркестратор, 2026-11-04T23:26:03Z): locals-then-guard в viewWorld
  публикует СТАРЫЙ мир при out-of-order завершении того же контекста.
- W1-code-fix-05 (сессия ses_e59eedb81ffes7W9twKULyUsxH, роль code) — S23
  закрыт guard'ом свежести (`game_timestamp`) в viewWorld/viewLocation/
  pollEvents + явный null-drop у всех трёх консьюмеров; SDD-трио завершено.
  Наблюдаемо воркером: m269 31/31, фокус 8 каталогов 96 passed, node
  --check / git diff --check / ruff — PASS, frozen-хэши тестов не изменились.
- W1-code-fix-06 (та же сессия ses_e59eedb81ffes7W9twKULyUsxH, роль code) —
  правка финальных находок ревью: финализаторы bootstrap'а проверяют
  владение слотом РАННИМ return'ом до снятия слота/публикации/метки
  (микротасковый зазор после гейта обёртки); null+контекст проверяются
  сразу после присоединённого /world ДО лишних await'ов во всех трёх
  консумерах (повторные проверки перед записями сохранены); late-continuation
  viewWorld читает захваченный ctxCharId, а не живой S.character.id;
  формулировки плана/спеки выровнены с фактическими гарантиями (poll
  защищает только S.world+часы). Наблюдаемо воркером: m269 31/31, фокус
  8 каталогов 96 passed, node --check / git diff --check / ruff — PASS,
  frozen-хэши тестов не изменились. Сверена оркестратором против
  app-blob b1ca74c71468db57f1028669d0c37bcd8a9e99ec.

## Сверка приёмки волны (наблюдаемое оркестратором, 2026-11-05)

- Полный gate: первый прогон sh_1a8ffdb24001pi1pvu3ule8Zws упал по
  timeout 120000 после m233 ~56% — инфраструктурный таймаут, НЕ тестовый
  отказ; повтор sh_1a902078c001b2YHRESJffEs9p (timeout 600000) завершён:
  Ruff — All checks passed; pytest 1231 passed / 13 deselected / 8825
  warnings / 442.31s. Нотификация наблюдена 2026-11-05 ~00:08Z;
  node --check / git diff --check — clean, frozen-хэши H4 неизменны
  (81e7dfcd… / 53266027…) — наблюдено оркестратором после прогона.
- Код-ревью W1-code-peer-final-01 (ses_e56ffc26effeP1unhEnGXt7WuO) —
  terminal PASS; статическая трассировка источника БЕЗ исполнения
  (execution policy-denied у инструмента ревьюера). Его замечание о
  «полном ctx-guard'е poll» не соответствует коду — фактические гарантии
  зафиксированы в этом плане (poll: null + живой S.character + guard
  S.world/часов).
- Test-peer W1-test-peer-03 (ses_e59efcfa4ffewr578ttCsijpR0) — PASS по
  frozen H4, статически; ослабленных ассертов не найдено, хэши совпали.
- Explorer-runtime ses_e56ff62baffe0TgmW4PkYw9zHb — частично (scratch
  permission deny); диагноз завершён: явный таймаут первого полного
  прогона; причина более раннего нативного прерывания — UNKNOWN.
- Браузерная приёмка — ВЫПОЛНЕНА (локальный REAL Playwright через MCP;
  API фикстурно-контролируемый, app/CSS реальные локальные, НЕ
  аутентифицированный прод): финальная 361-строчная scratch-функция
  (SHA256 4cdf81abd93f03d2b4b1de6db049a8fc6988c7e8f4f8443fe755187ac28b2959)
  — 15/15 сценариев PASS, allPass=true, pageErrors=[], unexpectedAPI[]
  (7 fresh-document cold routes — один GET /world без WS/event-poll;
  503→«н/д»→retry; held 401 → login + same-ID relogin без деавторизации;
  хэндофф pending bootstrap — один GET; 390/1440 без overflow, 9 вкладок
  доступны; desire/chat — вычисленные цвета совпали). UI-воркер
  W1-ui-final-01 (ses_e54c578bcffef8zhVXD46Q8e5X) — terminal PASS, 5
  скриншотов в shared/ (desire 390/1440 до/после, chat-error 390): 2 ряда
  навигации на мобильном, без desktop-дрейфа. Неблокирующее: focus-gold —
  computed-значение + CSS-контрастные тесты (1px визуально не различим);
  placeholder измерен ≥4.5:1; «unavailable» — тело ошибки фикстуры.
- Browser-fixture peer W1-test-browser-peer-02
  (ses_e56f17775ffeJRcY3bFuyYPdGi) — terminal static PASS ДО паузы
  (checkpoint), целевой хэш сверен, записей нет; собственный браузерный
  прогон пира — весь зелёный до паузы. Browser-prep W1-test-browser-01
  (ses_e5a250793ffeeu71PdOJedvb0g): холодные/сетапные ложные отказы
  чинились только в scratch; первый 344-строчный прогон — 15/15 PASS, но
  allPass=false (3 отсутствующих fixture-GET), финальные 361 строку —
  allPass=true.
- Питфолл (переиспользуемый): нативный MCP-playwright отклоняет filename
  вне своих разрешённых корней (/tmp/opencode/lifesim…); байт-идентичная
  копия в одобренный /tmp/opencode/playwright-dumps работает — права не
  менялись.
- Валидатор/pre-publication — единственная in-progress стадия; commit/
  push/PR, CI на merge-SHA, merge, lane-A деплой + прод-пробы, JOURNAL —
  ПЕНДИНГ; исходы пост-коммитных стадий фиксируются по факту в
  канонический issue/JOURNAL, не в этом pre-commit SDD.
