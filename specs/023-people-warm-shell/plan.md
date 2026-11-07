# 023 — plan

## Подход

CSS-only правка блока People + SDD-трио; test-роль FIRST (2 новых файла m59),
CSS-writer тесты не правит. Без новых deps/сервера/схем, без build-гейта
(SPA); Doctor — перед первой записью в репозиторий (preflight уже PASS).

## Preflight (наблюдаемое родителем; preflight ≠ VERIFY)

- Реальный gate 476080ms; parent re-read
  `/tmp/opencode/lifesim-023-preflight-ipNwxQ/gates.json` 16:46:55Z:
  evidenceLevel=full, failed=false; secrets ok (0 файлов); check argv
  `bash scripts/check.sh` exit 0, timedOut=false — «1236 passed, 13
  deselected, 8825 warnings in 457.82s»; worktree-integrity ok.
- Preflight HEAD был ff72c25f9b113f12f47311bf6a19efcb5bd7b50c (OLD merged
  branch), но whole TREE = admitted main 323; parent git diff EMPTY до/после;
  head НЕ заявлять равным 323, прогон НЕ дублировать как VERIFY. Ветка
  `wave/59-people-warm-shell` создана родителем `git switch --no-track -c`
  DIRECT от 323d9c00; HEAD 323, clean, 16:48:40Z; 5 новых путей отсутствуют.
  Doctor PASS mixed (advisory assignment cache; needsReview[] healthy).

### T1. Тесты (test-роль, FIRST)

- `tests/m59_people_warm_shell/test_people_warm_shell.py` — Python
  source-пины для CI: source pins (блок #69, границы 2015/2123), profile
  equivalence (6 cream-значений = байты `.profile-shell`), People body
  invariants (paint background/color/--dim, flat panels, links, hero) и
  meter invariants (3 override-селектора дословно, `--w-rel-hostile`,
  отсутствие `--w-line` в блоке).
- `tests/m59_people_warm_shell/browser_acceptance.js` — native MCP browser-
  функция реального computed style/layout/состояний/контраста против
  ЛОКАЛЬНЫХ candidate-байтов: A1–A7 из spec.md; хэш байтов — WebCrypto; std
  server 127.0.0.1:8772 (webroot strip first `/static`); GET-фикстуры,
  non-GET+unknown abort, WS preconnect blocked; дампы —
  `/tmp/opencode/playwright-dumps/lifesim-023/`.
- Подлинный RED на базе 323: style-assertion (не setup-сбой); computed root
  может быть TRANSPARENT — собирать фактический non-cream стиль, без
  фиктивного baseline RGB.
- Независимый test-peer ДО freeze; после фактического RED + peer PASS родитель
  freeze ОБОИХ файлов (хэши). CODE-ордера после freeze запрещают оба новых +
  все старые тесты: fix product, not oracle.
- ИСПОЛНЕНО (baseline 2026-11-06 → freeze 2026-11-07T03:40:34.645213Z):
  оба файла написаны и сверены пирами; подлинный RED на базе 323 наблюдён
  фактически (source: 1 failed / 1 passed — cream-пины; браузер: 15 passed /
  7 failed из 22 — style 2, empty 2, contrast 2 expected-BASE blur-
  unsupported, error 390 legacy-overflow; unexpected/page/console errors [];
  baseline-final.json в playwright-dumps). FROZEN-хэши:
  `test_people_warm_shell.py` 8f55cee4eacd6b4a513f8fb3860d959bede4181d763da980dcd62042268647c5,
  `browser_acceptance.js` 81a43d72b178635f22f754ca7980dd4e8a5fb65fe117f375bcc7517822c7618e.
  Правка обоих файлов запрещена всем ролям.
  > SUPERSEDED (Амендмент A1, 2026-11-07): для `browser_acceptance.js` —
  исторический freeze 81a43d72… superseded ТОЛЬКО в окне ревизии test-роли
  (см. «T-oracle»); PY — заморожен навсегда.

### T-oracle. Амендмент A1 — окно ревизии JS-oracle (test-роль; admitted 2026-11-07)

- Допущение родителя (parent observed 2026-11-07T09:30:26Z): approved base
  323d9c00… + starting approved dirty CSS 19c1e068…; старый JS 81a43d72… —
  исторический freeze, superseded ТОЛЬКО этим окном ревизии; PY 8f55cee… —
  навсегда. Модифицируемые пути — ровно 5 (см. spec.md «Амендмент A1»);
  агрегат публикации — прежние 6 файлов.
- Последовательность (связующая): DOC amend → TEST JS fix → независимый
  peer → parent full-22 genuine geometry RED на CSS 19c1e068… → NEW JS
  freeze → CODE CSS (ВСЕ тесты запрещены) → focused/full + native 22 GREEN /
  world12 / UI → validator → commit/push/PR/CI/merge/lane-A deploy/prod
  probes.
- Скоуп ревизии — ровно 2 oracle-дефекта в `browser_acceptance.js`:
  (D1) канонизация дефолтного 180deg/`to bottom` (строгие цвета/альфы,
  reject неверного угла); (D2) correlated-gradient premul RGBA — композит
  КАЖДОГО фиксированного стопа OVER текущий НЕПРОЗРАЧНЫЙ аккумулятор, THEN
  union result box (НЕ независимый premul-RGB alpha hull до over). Обход
  предков outer→inner / bgcolor first / images bottom→top; fail на
  unsupported color/interpolation/non-opaque root; float box сохранён;
  сохранить 8 resolver + 3 guard self-checks, добавить negative-angle +
  correlated-gradient проверки. Все 22 кейса и пороги ≥4.5 normal / ≥3
  large — без изменений; без skip/xfail/threshold-relax. Реальное
  исполнение Node + подлинный native baseline RED обязательны.
- Предложения explore/architect (source-grounded; runtime-верификация
  ИСПОЛНЕНА — native 22/22 GREEN на CSS 7305fdb4…, strip-проба 0→446,
  см. T3): overflow 390 = root grid наследует глобальные 1fr 1.4fr
  (собственных треков у `.people-shell` нет) ⇒ минимальный CSS —
  `grid-template-columns: minmax(0,1fr)` внутри `.people-shell`; nested
  hero/detail-колонки, ≤1100 breakpoint, card 150, strip overflow auto —
  не трогаются.
- Parent pre-write guards (actual): 63/63 policy parity + doctor PASS
  (корректный cwd; первичный bad `--root` CLI-вызов был readonly и
  исправлен — не проектный сбой); сохранённый РЕАЛЬНЫЙ full gate на
  НЕИЗМЕНЁННОМ CSS 19c1e068…/frozen-байтах предыдущей фазы (1238 passed,
  13 deselected, 438.16s; secrets 6; integrity ok) — НЕ новый post-amend
  VERIFY.
- NEW JS freeze: после фактического RED + peer PASS родитель замораживает
  ревизованный `browser_acceptance.js` (новый хэш) ДО любой CSS-правки;
  PY не пересоздаётся.
  > ИСПОЛНЕНО (2026-11-07): ревизованный JS заморожен родителем —
  `browser_acceptance.js`
  2cf4c7fa03ccbf83979552f71c58b0db97c3800db7c0069544f50407782dae9a
  (frozen-tests-amendment.json, frozen_at 2026-11-07T10:38:55.464252+00:00;
  peer test-peer-02 PASS; PY 8f55cee… не пересоздавался); окно ревизии JS
  ЗАКРЫТО — все тесты снова запрещены всем ролям.

### T2. Код (CSS-writer, после freeze)

- style.css ТОЛЬКО блок People: cream-токены profile-эквивалент, paint
  background/color/--dim, flat panels, links/secondary buttons/hero,
  stale-комментарий; существующая `.people-card.sel` остаётся читаемой
  (scoped ink-override только при необходимости, не `::selection`); метры
  W4 дословно; `--w-line` в блоке не появляется; соседние байты нетронуты.
  app.js не меняется.
- Статус (2026-11-07, ФИНАЛ): code01 (3 хунка в блоке #69; hero/media
  скоупятся (0,2,0), `.people-card.sel` → `.people-shell .people-card.sel`
  (0,3,0) — значения не меняются) + code02 (минимальный reorder
  существующего `.people-shell button:hover` ПОСЛЕ `button:not(.primary)` —
  восстановление hover-акцента action-кнопок, погашенного равной
  специфичностью (0,2,1) вторичного правила; card hover (0,3,0) не
  страдал) — parent reconciled фактически: diff, границы байт-равны,
  geometry declaration sequence equal; независимый code-peer-03 PASS
  (полный хэш, exact reverse-move proof, frozen-хэши); родительская
  hover-проба на финале PASS TRUE («Поговорить» RGBA 120,96,64,.35 →
  RGB 201,138,46; Anna sel / Boris card hover — accent сохранены).
  Финальный CSS:
  19c1e068dc343f52a5510bbd81424ed98a0057f75f58f26196cf5e8520c24f9a.
  Амендмент A1 CODE-фаза (L023A-code-grid-01, 2026-11-07): единственная
  добавленная строка `grid-template-columns: minmax(0, 1fr);` внутри
  `.people-shell` (после `display: grid;`); diff vs
  /tmp/opencode/lifesim-023A-test-02/style.pre-geometry.css = ровно
  1 строка; CSS теперь
  7305fdb462138125a9facb33caef8ec2c65f0828ac379b595296a86e9b89a1ad;
  focused 99 passed; node --check app.js + browser_acceptance.js OK;
  frozen-хэши до/после неизменны (2cf4c7fa… / 8f55cee…). Parent
  reconciliation — code-peer-01 (ses_e4a62e064ffeRzL20WHABjTdNn) PASS;
  native 22/22 GREEN / world12 / UI наблюдены (см. T3, амендмент A1).

### T3. Верификация (из корня lifesim)

- `uv run pytest tests/m59_people_warm_shell tests/m69 tests/m173 tests/m176 tests/m244 tests/m269 tests/m270 -q`
- `node --check tests/m59_people_warm_shell/browser_acceptance.js`;
  `node --check backend/app/web/app.js` (неизменность).
- Полный gates: `node /home/agent/.config/opencode/skills/project-bootstrap/scripts/run-project-gates.mjs --root "$PWD" --all`
  (запускает declared `bash scripts/check.sh`: Ruff + pytest `-m 'not slow'`).
- `git diff --check && git status --short`. Без fake LSP/build. Браузерная
  ручная приёмка — отдельная истина, не подменяет CI.
- Focused-прогон css-write-01 (2026-11-07, локально): `uv run pytest
  tests/m59_people_warm_shell tests/m69 tests/m173 tests/m176 tests/m244
  tests/m269 tests/m270 -q` → 99 passed, 0 failed; `node --check`
  backend/app/web/app.js и tests/m59_people_warm_shell/browser_acceptance.js
  — OK; `git diff --check` чист.
- Full gate 01 (ИСТОРИЯ): COMPLETED на СТАРОМ кандидате CSS d1c053cb… —
  «1238 passed, 13 deselected, 8825 warnings, 433.99s», secrets ok,
  integrity ok. Не текущий VERIFY.
- Full gate 02 (ТЕКУЩИЙ, parent лично re-read
  `/tmp/opencode/lifesim-023-final-verify-lOOFau/gates.json`): COMPLETED
  на ФИНАЛЬНОМ CSS 19c1e068… — full/failed false, secrets ok (6 файлов),
  check exit 0 timedOut false 455796ms «1238 passed, 13 deselected,
  8825 warnings, 438.16s», integrity ok; хэш источника EXACT в течение
  всего gate, тесты FROZE unchanged (2026-11-07).
- Амендмент A1: gate 02 выше — сохранённый РЕАЛЬНЫЙ full gate на
  НЕИЗМЕНЁННОМ CSS 19c1e068…/frozen-байтах предыдущей фазы (теперь
  ИСТОРИЯ). Post-amend VERIFY ИСПОЛНЕН — full gate 03 на CSS 7305fdb4…
  (parent re-read /tmp/opencode/lifesim-023A-full-verify-eP9Vv5/gates.json):
  failed false, secrets ok (6 файлов), check exit 0 timedOut false
  «1238 passed, 13 deselected, 8825 warnings in 447.06s» (465474ms весь
  gate), integrity before==after, HEAD 323d9c00… не изменился.
- Браузерная приёмка амендмента (native, parent observed): 22/22 GREEN
  (amendment-candidate-final.json, served CSS 7305fdb4… / app 33d7bb96…,
  unexpected/page/console [], contrast 27 = 4 gradient + 23 solid);
  world m270 12/12 PASS (map 390 267.3125px); strip-проба
  (amendment-strip-probe.json): 390/344/790 auto, 5×150, scrollLeft
  0→446; UI-final-01 + ui-strip-02 (ses_e4a6139a9ffe3VWqOKPUZ0JsUR)
  desktop/mobile/strip PASS — свежие 7305-снимки people-amendment-*.png
  в shared/ (raw visual, без формальных WCAG-заявлений).
- Браузерная приёмка A1–A7 на финале: НЕ ПРОЙДЕНА (блок) — frozen suite
  candidate02 (ACTUAL CSS 19c1e068…, app 33d7bb96…): 22 кейса 17 passed /
  5 failed (2 style false-RED 180deg-сериализация, 2 contrast false-RED
  «Друг» консервативная граница 2.45, 1 РЕАЛЬНЫЙ 390 overflow 824/390 =
  BASE); oracle candidate-final.json; unexpected/page/console errors [].
  Автоматические команды T3 исполнены; full-browser acceptance остаётся
  RED до отдельно допущенной oracle-ревизии и geometry-remediation.
  > SUPERSEDED (Амендмент A1, 2026-11-07): снято ревизией oracle +
  geometry-фиксом; текущая браузерная приёмка — 22/22 GREEN на CSS
  7305fdb4… (см. «Браузерная приёмка амендмента» выше).
- Серверы приёмки остановлены родителем (8772/8771, ECONNREFUSED
  подтверждён, local-server-cleanup.json); SIGTERM-нотификации —
  ожидаемая очистка.

## Публикация (цепочка, оркестратор; не заявлять заранее)

Независимые test/code пиры + UI raw screens → publication validator → PR CI
(точный head) → merge → trusted main push CI (точный merged SHA) → deploy
validator, DEPLOY-TRIGGER один раз → workflow dispatch (полный SHA) → внешние
HTTP/security/static-byte пробы + тот же frozen browser на прод-транспорте;
без authenticated-account заявлений. Откат — полная база
323d9c005245a83702504b5a9b09d2bc45b94203.

Статус (2026-11-07): NOT RUN — BLOCKED. Пиры и raw visual review исполнены
(palette PASS / geometry REQUEST_CHANGES), publication validator не
запускался: frozen suite RED (2+2 false-RED + 1 реальный A4 overflow).
Commit/push/PR/CI/merge/deploy не выполнялись; Git-объекты не создаются до
снятия блоков (geometry-remediation + test-oracle revision — отдельная
scope-admission).

Амендмент A1 (2026-11-07): блок снят допуском; CODE+verify фазы завершены
и наблюдены (gate 03 на CSS 7305fdb4…, native 22/22, world12, UI+strip —
см. T3). Цепочка публикации неизменна: validator → commit/push/PR/CI/
merge → lane-A deploy → prod probes — NOT RUN (текущий шаг — publication
validation); production readiness не заявляется; старая стадия —
не production-ready до зелёной приёмки.

## Риски

CSS-каскад/специфичность (урок m107/m270 peer-fix); задел соседних блоков;
fixture false RED (только локальные candidate-байты, не прод); контраст на
cream при полупрозрачных панелях; правка тестов CSS-writer'ом запрещена
frozen-контрактом.
