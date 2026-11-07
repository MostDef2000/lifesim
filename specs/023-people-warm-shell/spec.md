# 023 — People warm shell (#59: экран людей в тёмной cool-палитре)

## Цель

`#/people` переведён на утверждённую provisional cream-палитру VISUAL-BIBLE
§48 — значения уже живущего `.profile-shell` (style.css 1821–1890). Только
CSS блока People; JS/DOM/API/механика/состояния не меняются; финальная
палитра (issue #55) — НЕ выбор этого изменения.

## Проблема (наблюдаемое, байты базы 323d9c00)

- `.people-shell` (блок `/* #69 */` 2015 → `/* #107 */` 2123) красит экран
  тёмными cool-токенами — 10 vars: `--w-bg:#0a1016`, `--w-panel:rgba(23,33,42,.72)`,
  `--w-panel-deep:rgba(12,19,25,.6)`, `--w-text:#dde5ec`, `--w-dim:#8fa1b1`,
  `--w-accent:#e2b45a` + warm-трио + `--w-rel-hostile:#e07070`; панели —
  blur(10px) + box-shadow + тёмный `border-color:var(--w-line)`; hero
  (2063–2070) — тёмный linear. Контейнер не красит background/color ⇒
  computed root может быть TRANSPARENT — baseline собираем фактический.
- Комментарий блока stale: обещает «mirror the warm custom properties»,
  а значения тёмные cool.
- Профиль-прецедент утверждён: cream 6 значений `#f3ecdf` /
  `rgba(255,253,248,.92)` / `rgba(236,224,204,.7)` / text `#2a2118` /
  dim `#6e5f4e` / accent `#c98a2e`; flat panels `rgba(120,96,64,.35)`;
  links `#8a5a16`; тёплый light hero.

## Скоуп (ровно 6 путей волны; здесь пишутся 3)

- `backend/app/web/style.css` — ТОЛЬКО блок People (#69 … до `/* #107 */`):
  stale-комментарий + scoped ink/link/button/border-фиксы; «selection» —
  существующая выбранная карточка `.people-card.sel` остаётся читаемой
  (ink-override только при необходимости), НЕ новый `::selection`.
- `tests/m59_people_warm_shell/test_people_warm_shell.py` (NEW, CI-истина).
- `tests/m59_people_warm_shell/browser_acceptance.js` (NEW, ручная браузерная истина).
- `specs/023-people-warm-shell/{spec,plan,tasks}.md` — этот трио.

> Амендмент A1 (admitted 2026-11-07): окно ревизии `browser_acceptance.js`
> (test-роль) и узкой CSS-правки — см. «Амендмент A1» ниже.

## Исключения (не трогается)

`app.js` (оба корня `.people-shell` уже на месте: empty ~5385–5390, populated
~5506–5520; selection chat/map/loading/state не меняются), `:root` байты;
`--w-line` в блоке НЕ определяется (пин счётчика токенов = 4, fallback #172
несёт то же значение); source JS/API/relations/roles/mechanics/state/layout/
breakpoints/ассеты/шрифты/deps; connection-banner tail; прочие UI-экраны;
ВСЕ существующие тесты (m69/m173/m176/m244/m269/m270 frozen); транспорт/
деплой-инфра; `.env`/секреты. Долгие имена и preexisting layout-баги — не
расширение скоупа; блокировать, если фикс нужен вне хунка.

> Амендмент A1: PY m59 — заморожен навсегда; `browser_acceptance.js` —
> ревизия только в окне test-роли (см. «Амендмент A1»).

## Требования

- W1. Дифф style.css — только блок #69 (2015→2123); соседние байты
  (profile до 2013, `/* #107 */` с 2123) нетронуты.
- W2. Cream-токены = утверждённые значения profile (6 выше); paint
  `background`/`color`/`--dim`; flat panels (без blur/shadow) с тёплым
  бордером `rgba(120,96,64,.35)`; links `#8a5a16`; secondary buttons тёплые;
  hero — тёплый light linear.
- W3. Stale-комментарий приведён в соответствие (блок теперь warm).
- W4. Подписанные метры: 3 override-селектора дословно (`.people-mid`/
  `.people-fill`/`.people-fill.neg`, пины m69 count==1); `--w-rel-hostile:
  #e07070`; геометрия — центр 0, negative влево, positive вправо, ±100.
- W5. Intentional strip scroll сохранён; responsive-геометрия (≤1100px) не
  меняется. (Амендмент A1 — единственное исключение: root-track constraint,
  см. «Амендмент A1»; все прочие geometry/breakpoint-декларации не меняются.)
- W6. JS/DOM/API/состояния не меняются; `node --check app.js` байт-прежний.
- W7. Profile/world не затронуты (включая существующий m270 first map).

## Acceptance matrix

| # | Сценарий | Ожидание |
|---|----------|----------|
| A1 | root computed | cream фон/текст, flat panels; НЕ тёмные унаследованные контролы |
| A2 | реальный composited text | контраст ≥4.5 normal / ≥3 large |
| A3 | метры signed | zero/positive/negative ±100: геометрия и текст верны |
| A4 | empty/populated/error-retry/long VALID names/portrait loaded (inert fixture SVG)/portrait 404 (существующий fallback) × 390×844 и 1440×900 | без деградации; без page overflow (strip scroll разрешён) |
| A5 | клавиатура | native focus, Enter выбирает |
| A6 | nav 9 вкладок + часы | без изменений |
| A7 | profile/world | без изменений; m270 first map остаётся зелёным |

## Амендмент A1 — ADMITTED (2026-11-07; parent observed 2026-11-07T09:30:26Z)

Scope-admission ДОПУЩЕНА родителем. Doc-receipt: approved base HEAD
323d9c005245a83702504b5a9b09d2bc45b94203 + starting approved dirty CSS
19c1e068dc343f52a5510bbd81424ed98a0057f75f58f26196cf5e8520c24f9a; старый JS
`browser_acceptance.js`
81a43d72b178635f22f754ca7980dd4e8a5fb65fe117f375bcc7517822c7618e — freeze
исторический, superseded ТОЛЬКО этим окном ревизии (не отменён, не «waived»);
PY `test_people_warm_shell.py`
8f55cee4eacd6b4a513f8fb3860d959bede4181d763da980dcd62042268647c5 — заморожен
НАВСЕГДА.

- Модифицируемые пути амендмента — ровно 5 (PY не входит): `style.css`
  (ТОЛЬКО People-блок), `tests/m59_people_warm_shell/browser_acceptance.js`
  (ТОЛЬКО test-роль в окне ревизии), `specs/023-people-warm-shell/`
  `{spec,plan,tasks}.md`. Агрегатная публикация полной волны — прежние
  6 файлов.
- Остаётся запрещённым: PY-тест и ВСЕ старые тесты, app.js/JS-приложение,
  API, прочий CSS, `:root`, w-line4, banner tail, шрифты/ассеты/deps,
  транспорт, секреты, JOURNAL/shared, Git-жизненный цикл, инсталлы,
  agents/browser/serverops.
- Связующая последовательность: DOC amend → TEST JS fix → независимый peer →
  parent full-22 genuine geometry RED на текущем CSS 19c1e068… → NEW JS
  freeze → CODE CSS (ВСЕ тесты запрещены) → focused/full + native 22 GREEN /
  world12 / UI → validator → commit/push/PR/CI/merge/lane-A deploy/prod
  probes.
- Жёсткие гарды: никакой геометрии до RED/freeze; правка тестов «до
  зелёного» запрещена; без skip/xfail/снижения порогов; все 22 кейса и
  пороги ≥4.5 normal / ≥3 large сохраняются; без финальной палитры/нового
  фреймворка.
- W5-исключение (единственное): root-track constraint
  `grid-template-columns: minmax(0,1fr)` внутри `.people-shell`
  (source-grounded: root grid наследует глобальные 1fr 1.4fr, собственных
  треков нет); nested hero/detail-колонки, ≤1100 breakpoint, card 150,
  strip overflow auto — нетронуты; runtime-верификация ИСПОЛНЕНА
  (native 22/22 GREEN на 7305fdb4…, strip-проба 0→446 — см. ниже).
- Ревизия oracle — ровно 2 дефекта (test-роль): (D1) канонизация дефолтного
  угла — Chrome сериализует computed gradient без дефолтных 180deg/`to
  bottom`; канонизировать, строгие цвета/альфы, reject неверного угла;
  (D2) correlated-gradient contrast — композит КАЖДОГО фиксированного стопа
  OVER текущий НЕПРОЗРАЧНЫЙ аккумулятор, THEN union result box (НЕ
  независимый premul-RGB alpha hull до over). Обход предков outer→inner,
  bgcolor first, images bottom→top; fail на unsupported
  color/interpolation/non-opaque root; float box сохранён; сохранить
  8 resolver + 3 guard self-checks, добавить negative-angle +
  correlated-gradient проверки; реальное исполнение Node и подлинный
  native baseline RED обязательны. Без relax порогов / удаления кейсов —
  все 22 остаются.
- Исполнение амендмента (2026-11-07, фактическое): TEST-фаза ЗАВЕРШЕНА —
  D1+D2 исправлены (write01 915-ревизия → write02 финал); peer-01
  REQUEST_CHANGES (2 parser-хола, stale header, неверная квадратичная
  математика) разрешён кросс-ревью 2:0 (premult-affine подтверждён —
  PRIMARY по CSS Images §3.4.2; квадратичное straight-RGBA возражение
  опровергнуто и отозвано автором); финальный test-peer-02
  (ses_e4a70cce4ffeMz1ESF7AAAugi4) PASS на ревизованном JS; parent
  genuine geometry RED на CSS 19c1e068… наблюдён фактически
  (amendment-baseline-final.json: 22 кейса, 20 pass, RED только style-390 +
  error-390 824>390; contrast 27/27 PASS; unexpected/page/console []).
  NEW JS freeze 2cf4c7fa03ccbf83979552f71c58b0db97c3800db7c0069544f50407782dae9a
  (frozen-tests-amendment.json, frozen_at 2026-11-07T10:38:55.464252+00:00;
  PY 8f55cee… неизменен; окно ревизии JS ЗАКРЫТО — все тесты снова
  запрещены всем ролям). CODE CSS исполнен (L023A-code-grid-01): ровно
  одна строка `grid-template-columns: minmax(0, 1fr);` внутри
  `.people-shell` (после `display: grid;`); diff vs style.pre-geometry.css
  = 1 строка; CSS теперь
  7305fdb462138125a9facb33caef8ec2c65f0828ac379b595296a86e9b89a1ad;
  focused 99 passed, node --check обоих JS OK, frozen-хэши до/после
  неизменны.
- Локальная верификация амендмента (2026-11-07, parent observed):
  full gate 03 на CSS 7305fdb4… (parent re-read
  /tmp/opencode/lifesim-023A-full-verify-eP9Vv5/gates.json) — failed
  false, secrets ok (6 файлов), check exit 0 timedOut false «1238
  passed, 13 deselected, 8825 warnings in 447.06s» (465474ms весь gate),
  integrity before==after, HEAD 323d9c00… не изменился; gate 02 (на CSS
  19c1e068…) — ИСТОРИЯ, текущий VERIFY — gate 03. Native 22/22 GREEN
  (amendment-candidate-final.json: served CSS 7305fdb4… / app 33d7bb96…,
  unexpected/page/console [], contrast 27 = 4 gradient + 23 solid;
  HTTP — только intentional fixture 404 portrait / 500 relationships по
  докам кейсов). World m270 native 12/12 PASS (map 390 267.3125px,
  unexpected API/page errors []). Strip-проба
  (amendment-strip-probe.json): viewport/page 390, strip client 344 /
  scroll 790, overflow auto, 5 карт по 150, scrollLeft 0→446 —
  intentional strip scroll сохранён. Code-peer-01
  (ses_e4a62e064ffeRzL20WHABjTdNn) PASS (ровно одна строка grid,
  frozen-хэши, скоуп сохранён); UI-final-01 + ui-strip-02
  (ses_e4a6139a9ffe3VWqOKPUZ0JsUR) desktop PASS / mobile visible PASS /
  strip PASS — свежие 7305-снимки people-amendment-*.png в shared/
  (raw visual, без формальных WCAG-заявлений). Публикация (validator →
  commit/push/PR/CI/merge → lane-A deploy → prod probes) — NOT RUN;
  production readiness не заявляется; #59 остаётся partial wave.

## Статус стадии (ФИНАЛ локальной стадии, 2026-11-07)

> Амендмент A1 (2026-11-07): стадия снова В РАБОТЕ в узком окне амендмента
> (TEST → CODE → verify → publish); статусы ниже — история исходной стадии;
> пункты блокеров «не допущено» — SUPERSEDED (см. «Амендмент A1»). Старая
> стадия остаётся НЕ production-ready до зелёной приёмки.
>
> Текущий статус амендмента (2026-11-07, после локальной верификации):
> LOCAL ACCEPTED — TEST/CODE/verify фазы завершены и наблюдены (gate 03,
> native 22/22, world 12/12, UI+strip, пиры — см. «Амендмент A1»);
> единственная открытая фаза — публикация (validator → commit/push/PR/
> CI/merge → lane-A deploy → prod probes), NOT RUN; production readiness
> не заявляется; #59 остаётся partial wave.

Сводная: T1 DONE (frozen); T2 DONE (локальная реализация, parent diff +
peer verified); T3 — автоматические команды DONE, полная браузерная
приёмка A1–A7 НЕ пройдена (frozen suite RED — блок); T4 — code-peer + raw
visual review DONE (palette PASS / geometry REQUEST_CHANGES), publication
validator NOT RUN (блок); T5 commit→deploy NOT RUN (блок); T6 KB —
pending. Локальный CSS-кандидат завершён; OVERALL acceptance/publication
BLOCKED. Git: HEAD 323d9c00, index clean, 6 dirty файлов (CSS + 3 SDD +
2 frozen tests); commit/push/PR/CI/merge/deploy НЕ выполнялись.

- T1 исполнен и заморожен родителем 2026-11-07T03:40:34.645213Z:
  `test_people_warm_shell.py` 8f55cee4eacd6b4a513f8fb3860d959bede4181d763da980dcd62042268647c5,
  `browser_acceptance.js` 81a43d72b178635f22f754ca7980dd4e8a5fb65fe117f375bcc7517822c7618e.
  Базлайн-RED на базе 323 наблюдён фактически: source-пины 1 failed /
  1 passed (cream-пины); браузер 22 кейса — 15 passed / 7 failed; дамп
  `/tmp/opencode/playwright-dumps/lifesim-023/baseline-final.json`.
- T2 DONE: code01 (3 хунка в блоке #69) + code02 (минимальный reorder
  существующего `.people-shell button:hover` ПОСЛЕ `button:not(.primary)`,
  равная специфичность 0,2,1) — parent reconciled фактически: diff,
  границы байт-равны, geometry declaration sequence equal; независимый
  code-peer-03 PASS (полный хэш, exact reverse-move proof, все frozen-
  хэши; same-role peer для узкого CSS-move допустим). Финальный CSS
  19c1e068dc343f52a5510bbd81424ed98a0057f75f58f26196cf5e8520c24f9a —
  хэш оставался EXACT в течение всего gate 02; тесты FROZE unchanged.
- Full gate 02 (ТЕКУЩИЙ; parent лично re-read
  `/tmp/opencode/lifesim-023-final-verify-lOOFau/gates.json`): COMPLETED
  на финальном CSS 19c1e068… — full/failed false, secrets ok (6 файлов),
  check exit 0 timedOut false 455796ms «1238 passed, 13 deselected,
  8825 warnings, 438.16s», integrity ok (2026-11-07; точное время суток
  не наблюдаемо — не фиксируем). Gate 01 (d1c053cb…, 433.99s) — ИСТОРИЯ,
  не текущий VERIFY.
- Браузер candidate02 (ФИНАЛ; frozen suite на ACTUAL CSS 19c1e068…, app
  33d7bb96b3d354b1a813685e82e48801c5e94309009ee7aaf3497a2d17566bf0):
  22 кейса — 17 passed / 5 failed; strict unexpected/page/console
  errors []; oracle сохранён
  `/tmp/opencode/playwright-dumps/lifesim-023/candidate-final.json`.
  Те же 5, что на candidate01 (история, идентичный профиль): 2 style
  false-RED (1440/390 — Chrome сериализует computed gradient без
  дефолтных 180deg; не дефект краски), 2 contrast false-RED (1440/390,
  только 13px «Друг» — консервативная НЕкоррелированная
  premul-channel-alpha нижняя граница 2.45, не измеренное ratio), 1
  РЕАЛЬНЫЙ error — 390 legacy overflow 824/390 (= BASE). FULL FROZEN
  SUITE ОСТАЁТСЯ RED независимо от алгебра-диагноза; all-22-green /
  whole-UI acceptance НЕ заявляется. Формальный контраст-бонд
  ≥4.6769923711852375 — source-алгебра ТОЛЬКО hero «Друг» (architect
  verified + parent python computed), не whole-screen.
- Hover-проба родителя на ФИНАЛЕ 19c1e068… (native; без правки тестов/
  фикстур): «Поговорить» border RGBA 120,96,64,.35 → после hover
  RGB 201,138,46; выбранная Anna — accent сохранён; невыбранная Boris —
  card hover accent; PASS TRUE. Уточнение: регрессия касалась ТОЛЬКО
  action-кнопок; `.people-shell .people-card:hover` (0,3,0) всегда бил
  secondary (0,2,1) — card hover не отменялся; code02 переупорядочил
  только общий hover для action-кнопок.
- World m270 native (неизменный) ПЕРЕПРОБОВАН на ФИНАЛЕ 19c1e068… +
  33d7…: 12/12 PASS, staticSha exact binding, unexpected API/page errors
  [], map 390 267.3125px, nav 9 / clock корректны — не перенос результата
  gate 01.
- UI-final-02 (ses_e4be89a1dffetZNq4OPxNyg3Sg): 2 raw изображения
  раздельно, read-path маппинг подтверждён (снимает caveat batch-загрузки
  ui-candidate-01): desktop
  `/home/mostdef/vibe/projects/ai-playground/shared/lifesim-023/people-final-desktop.png`
  1440×900 — визуальная палитра PASS, вписывается; mobile
  `…/people-final-mobile.png` 390×844 — геометрия REQUEST_CHANGES (clip
  hero/meters, тот же BASE overflow). Хэши копий родителя: desktop
  def7040583d3c32afa3ec86e82d0bbdf952de5856fe4874a4e0f7a46a25ee318,
  mobile 6c5504269e7cf03bf2dd124012a53f7cbd6849cb98d6aa13649028667abeb64c.
  БЕЗ формальных WCAG/whole-UI заявлений.
- Серверная гигиена: родитель остановил ТОЛЬКО свои stdlib-серверы 8772
  (pid 1429) / 8771 (pid 6903) с проверкой /proc command+cwd; порты
  ECONNREFUSED (111); `local-server-cleanup.json` в playwright-dumps.
  SIGTERM-нотификации фоновых серверов — ожидаемая очистка, не отказ
  gate. Мутаций серверной инфраструктуры/VPS нет.
- Блокеры OVERALL (не сняты этим доком): (1) frozen suite RED — 2 style +
  2 contrast false-RED требуют ОТДЕЛЬНО допущенной ревизии frozen-case
  oracle + fresh RED/review/freeze (не «тихая» правка тестов; текущие
  тест-файлы остаются запрещены всем ролям); (2) РЕАЛЬНЫЙ A4: 390×844
  page width 824 — предсуществующий legacy overflow (свидетельство
  2026-11-06/07, сводка baseline-final 2026-11-07), геометрия НЕ допущена,
  A4 не ослабляется. Пороги A1–A7 сохранены, без XFAIL/skip/waiver.
  Generic «continue» допускает ТОЛЬКО исходный скоуп.
  > SUPERSEDED (Амендмент A1, 2026-11-07): п. (1) — oracle-ревизия ДОПУЩЕНА
  (окно test-роли, см. «Амендмент A1»); п. (2) — geometry-remediation
  ДОПУЩЕНА в узкой форме (только root-track constraint). Сами факты
  frozen-suite RED и 390-overflow остаются исторической истиной.
- Резюме-точка возобновления: следующее — НОВАЯ scope-admission ТОЛЬКО
  для geometry-remediation И test-oracle revision. Текущая фаза: FINAL
  RECORD (этот док; после parent read — в работе ничего). Публикация
  заблокирована, не исполнена; стадия НЕ production-ready.
  > SUPERSEDED (Амендмент A1): admission состоялась 2026-11-07; текущая
  фаза — TEST (JS oracle revision) после parent doc read; code/
  верификация/публикация — pending (см. «Амендмент A1»).

## Верификация (две отдельные истины)

- CI-истина: pytest source-пины / profile-equivalence / People body+meter
  invariants; `node --check` обоих JS; полный gates (plan T3).
- Браузерная истина (ручная native MCP, НЕ CI-claim): реальный computed
  style/layout/состояния/контраст против ЛОКАЛЬНЫХ candidate-байтов; хэш
  байтов через WebCrypto (Node crypto в нативной VM не работает); std server
  127.0.0.1:8772 (webroot strip first `/static`); GET-фикстуры, non-GET+
  unknown abort, WS preconnect blocked; без креденшелов/мутаций/GPU/патчей.
  Базлайн RED на базе 323 — style-assertion по факту стиля, НЕ setup-сбой;
  non-cream computed собирается фактический, без фиктивного baseline RGB.
