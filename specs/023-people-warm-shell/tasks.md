# 023 — tasks

## Порядок исполнения (test-first)

- [x] T1 (test-роль, ПЕРВЫЙ): ТОЛЬКО 2 новых файла
      `tests/m59_people_warm_shell/test_people_warm_shell.py` +
      `browser_acceptance.js`; независимый test-peer; подлинный RED на базе
      323 (style-assertion, не setup-сбой), наблюдаем родителем; freeze
      ОБОИХ хэшей родителем ДО любой CSS-правки — ИСПОЛНЕНО, заморожено
      2026-11-07T03:40:34.645213Z (хэши — plan.md T1; правка запрещена).
- [x] T2 (CSS-writer, после freeze): style.css — только блок People
      (#69→#107) по W1–W7; ВСЕ тесты (новые и старые) запрещены —
      fix product, not oracle; app.js не трогается — ИСПОЛНЕНО: code01 +
      code02 (hover-reorder) parent-reconciled 2026-11-07 (diff, границы
      байт-равны, geometry declaration sequence equal; code-peer-03 PASS;
      hover-проба родителя на финале PASS TRUE). Финальный CSS 19c1e068….
- [ ] T3: верификация + браузерная приёмка A1–A7 — автоматические команды
      ИСПОЛНЕНЫ (focused 99 passed; full gate 02 COMPLETED на финальном
      CSS 19c1e068…: «1238 passed, 13 deselected, 8825 warnings, 438.16s»,
      secrets ok 6, integrity ok; gate 01 d1c053… — история), НО полная
      браузерная приёмка A1–A7 НЕ ПРОЙДЕНА: frozen suite candidate02 —
      17 passed / 5 failed (2 style false-RED 180deg-сериализация, 2
      contrast false-RED «Друг» консервативная граница 2.45, 1 РЕАЛЬНЫЙ
      error 390 overflow 824/390 = BASE; oracle candidate-final.json).
      Блок: geometry-remediation + отдельно допущенная test-oracle
      revision; чекбокс не закрывается.
- [ ] T4 (ревью): независимые code-peer + test-peer + UI raw screens;
      затем publication validator — code-peer-03 PASS; UI-final-02 raw
      visual DONE (desktop 1440×900 palette PASS / mobile 390×844
      geometry REQUEST_CHANGES); publication validator NOT RUN (блок).
- [ ] T5 (оркестратор): commit/push/PR → green CI (точный head) → merge →
      trusted main CI (точный merged SHA) → DEPLOY-TRIGGER + lane-A deploy
      (полный SHA) → внешние пробы + frozen browser на прод-транспорте;
      не заявлять выполненным заранее — NOT RUN, BLOCKED (frozen suite
      RED + A4; Git-объекты не создаются).
- [ ] T6: JOURNAL/KB-запись волны.

## Амендмент A1 — порядок исполнения (admitted 2026-11-07)

Модифицируемые пути — ровно 5 (PY не входит): `style.css` (People-блок),
`browser_acceptance.js` (test-роль), 3 SDD. Связующая последовательность:
DOC amend → TEST JS fix → независимый peer → parent full-22 genuine geometry
RED на CSS 19c1e068… → NEW JS freeze → CODE CSS (ВСЕ тесты запрещены) →
focused/full + native 22 GREEN / world12 / UI → validator → commit/push/PR/
CI/merge/lane-A deploy/prod probes. Гарды: без геометрии до RED/freeze; без
правки тестов «до зелёного»; без skip/xfail/threshold-relax; все 22 кейса и
пороги ≥4.5 normal / ≥3 large сохраняются; PY 8f55cee… — навсегда frozen;
старый JS 81a43d72… — исторический freeze, superseded только этим окном.

- [x] A0 (doc): амендмент записан в трио (L023A-doc-admit-01, DOCONLY;
      только 3 SDD) — parent read фактических файлов/diff'ов, reconciled.
- [x] A1 (test-роль): ревизия `browser_acceptance.js` — ровно 2
      oracle-дефекта (D1 180deg/`to bottom`-канонизация; D2
      correlated-gradient композитинг OVER непрозрачный аккумулятор), все
      22 кейса/пороги сохранены; независимый peer; parent full-22 genuine
      geometry RED на CSS 19c1e068…; NEW JS freeze — ИСПОЛНЕНО test-ролью:
      D1+D2 исправлены (write01 915 → write02), peer-01 REQUEST_CHANGES
      разрешён (кросс-ревью 2:0, premult-affine подтверждён, квадратика
      отозвана), test-peer-02 PASS, parent RED наблюдён (20/22, style-390 +
      error-390), NEW freeze 2cf4c7fa… 2026-11-07T10:38:55.464252+00:00 —
      закрыто родителем по фактическим наблюдениям.
- [x] A2 (CSS-writer): `style.css` — ТОЛЬКО root-track constraint
      (`grid-template-columns: minmax(0,1fr)` внутри `.people-shell`); ВСЕ
      тесты запрещены — ИСПОЛНЕНО (L023A-code-grid-01, 2026-11-07): ровно
      1 строка `grid-template-columns: minmax(0, 1fr);` после
      `display: grid;` в `.people-shell`; diff vs pre-geometry = 1 строка;
      CSS 7305fdb4…; focused 99 passed; node --check обоих JS OK;
      frozen-хэши неизменны; parent reconciliation — code-peer-01 PASS.
- [x] A3: верификация — focused/full + native 22 GREEN / world12 / UI —
      ИСПОЛНЕНО (parent observed, 2026-11-07): full gate 03 на CSS
      7305fdb4… (failed false, secrets ok 6, check exit 0 timedOut false,
      1238 passed / 13 deselected / 8825 warnings 447.06s, integrity
      before==after, HEAD 323 unchanged); native 22/22 GREEN
      (amendment-candidate-final.json, app 33d7bb96…, unexpected/page/
      console [], contrast 27 = 4 gradient + 23 solid); world 12/12 (map
      390 267.3125px); strip-проба 0→446; UI desktop/mobile/strip PASS
      (people-amendment-*.png в shared/).
- [ ] A4 (оркестратор): validator → commit/push/PR/CI/merge/lane-A deploy →
      prod probes — ЕДИНСТВЕННЫЙ открытый пункт: publication validation
      NOT RUN; production readiness не заявляется; #59 — partial wave,
      whole issue не закрывается.

## Журнал попыток (наблюдаемое; времена, где не указано — unknown)

- BACKLOG-волна (все reconciled, время unknown): local-explore-01
  ses_e4e5bfb1effeMFqKu044RZScbC — source orientation; role-explore-01
  ses_e4e599dd7ffetSpBe92wjMaDf5 — outscope 61/195 source audit;
  ui-explore-01 ses_e4e56878bffeprdyzO4Rolc0b8 — People gap; architect-01
  ses_e4e52ac0affeh26AtB2PxD4dfr — READY exact 6 People only.
- L023-test-design-01 — ses_e4e46d17bffeK0lWK478ulLTbY — DESIGN READY,
  реальные GET-формы, NO writes — reconciled — время unknown.
- L023-validator-prepare-01 — ses_e4e3df63effep9RTah396dE75p — guarded
  PASS, branch-гарды выполнены — reconciled — время unknown.
- L023-preflight (родитель) — реальный gate 476080ms; gates.json re-read
  16:46:55Z: full/false, secrets ok/0, check exit 0 «1236 passed, 13
  deselected, 8825 warnings 457.82s», integrity ok; HEAD был ff72c25f (OLD
  merged branch), TREE = admitted 323, diff EMPTY — preflight ≠ VERIFY,
  head как 323 не заявлять (детали — plan.md). Ветка DIRECT от 323, clean
  16:48:40Z; Doctor PASS mixed (advisory assignment cache; needsReview[]).
- L023-doc-01 — doc (DOCONLY) — трио specs/023 создано (220 строк); parent
  read + reconciled (все 3 файла + git diff: только 3 SDD untracked) — время unknown.
- L023-doc-02 — doc (DOCONLY) — 3 узких wording-фикса; parent reconciled
  по фактическим read — время unknown.
- L023-test-write-01…04 — ses_e4e46d17bffeK0lWK478ulLTbY — возвращено,
  reconciled по фактическим read/node — время unknown.
- L023-test-peer-01…04 — ses_e4e1db16fffeo6TDPHFRYiqBsj — reconciled по
  фактическому diff — время unknown.
- L023-test-05/06 — ECONNRESET; файлов нет (проверено родителем);
  blocked (provider error, no result) — not completed.
- L023-test-07 — ses_e4beefee8ffeXDnAbnBSfqJXLT — возвращено, reconciled:
  3 хунка + фактический self-check/байт-хэши — время unknown.
- L023-overflow-explore-01 — ses_e4de94e22ffeZ7jhYRCAuFhM1X — readonly BASE,
  вывод intrinsic-grid inference (без свежего браузера) — время unknown.
- L023-ui-candidate-01 — ses_e4be89a1dffetZNq4OPxNyg3Sg — после первичного
  permission-блока загружено 8 изображений из workspace/shared/lifesim-023
  (не /tmp); визуальная палитра PASS, геометрия REQUEST_CHANGES; caveat
  маппинга путей снят последующим ui-final-02 (подтверждённый read-path).
- L023-code-peer-01 — ses_e4be14645ffeeut7ZXGuQzDlyt — returned PASS для
  candidate01 (d1c053…) до hover-фикса; parent reconciled независимый
  отчёт (свой hash-diff + отчёт); нашёл low F1 Talk-hover (parent ACTUAL
  подтвердил); его claim «ALL card hovers cancelled» был неверен —
  скорректирован (action-only, см. spec/plan).
- L023-code-peer-02 — тот же child ses_e4be14645ffeeut7ZXGuQzDlyt —
  followup финального hover-review ПОЗЖЕ blocked: vendor monthly quota;
  нового вердикта НЕТ, not completed; PASS code-peer-01 остаётся
  действительным (без инвалидации); blind retry не выполнялся.
- L023-code-peer-03 — ses_e4bd546e5ffeWrM6oLhGMkXAvV — PASS (fresh
  code-role readonly): полный хэш, exact reverse-move proof, byte
  prefix/suffix == base, geometry unchanged, все frozen-хэши; same-role
  независимый peer для узкого CSS-move допустим.
- L023-oracle-architecture-01 — ses_e4bdfa8cfffePiyR1qPh1npHTG — readonly;
  source-алгебра hero-opacity math: контраст >= 4.677 (parent python
  computed); НЕ whole-screen и НЕ frozen-suite PASS.
- L023-css-write-01 — CSS-кандидат (3 хунка, блок #69) — parent reconciled
  фактически 2026-11-07 (diff/границы байт-равны, geometry declaration
  sequence equal, focus 99, full review).
- L023-css-write-02 — hover-reorder: 1 перенос существующего
  `.people-shell button:hover` после `button:not(.primary)`, без новых
  селекторов/значений/геометрии — parent reconciled 2026-11-07 (parent
  diff + code-peer-03 + hover-проба PASS TRUE на финале 19c1e068…).
- L023-gate-02 (родитель) — COMPLETED на финальном CSS 19c1e068…:
  full/failed false, secrets ok 6, check exit 0 timedOut false 455796ms
  «1238 passed, 13 deselected, 8825 warnings 438.16s», integrity ok;
  хэш источника EXACT весь gate, тесты FROZE unchanged (2026-11-07;
  точное время суток не наблюдаемо). Gate 01 (d1c053…) — история.
- L023-ui-final-02 — ses_e4be89a1dffetZNq4OPxNyg3Sg — 2 raw изображения
  раздельно, read-path подтверждён: desktop people-final-desktop.png
  1440×900 palette PASS; mobile people-final-mobile.png 390×844 geometry
  REQUEST_CHANGES (clip hero/meters, тот же BASE overflow); хэши копий
  def7040583… / 6c5504269e…; БЕЗ формальных WCAG/whole-UI заявлений.
- L023-doc-final-03 — doc (DOCONLY) — финализация трио фактическими
  parent-наблюдениями — parent ACTUAL read reconciled 2026-11-07 (все 3
  документа + хэши + статус-артефакты).
- L023-doc-final-04 — doc (DOCONLY) — узкая правка журнала (только
  tasks.md): раздельные записи code-peer-01/02 по попыткам; reconciliation
  родителя фиксируется в чате (superseded Амендментом A1 / L023A-doc-admit-01,
  2026-11-07).
- L023A-explore-01 — ses_e4aaa3341ffezVQCVaysx7g1fZ — readonly — READY
  source-grounded proposals: root.grid.people-shell наследует глобальные
  1fr 1.4fr / собственных треков нет ⇒ минимальный CSS
  `grid-template-columns:minmax(0,1fr)` внутри `.people-shell`; oracle
  180deg-канонизация + correlated-gradient композитинг — reconciled
  (parent actual reports + source evidence) — 2026-11-07.
- L023A-architect-01 — ses_e4aa8c34cffeyGuCuUIPLOUkNp — readonly — READY
  proposals: W5-исключение только root-track (остальная геометрия/
  breakpoint-декларации нетронуты); oracle-обход предков outer→inner,
  bgcolor first / images bottom→top; 8 resolver + 3 guard self-checks +
  negative-angle/correlated-gradient проверки; Node-исполнение; подлинный
  native RED — reconciled (parent actual reports + source evidence) —
  2026-11-07.
- L023A-doc-admit-01 — doc (DOCONLY) — амендмент A1 записан в
  трио (spec/plan/tasks; только эти 3 файла); parent read reconciled
  (parent observed) — 2026-11-07.
- L023A-test-phase (test-роль, окно ревизии) — D1+D2 ревизия
  `browser_acceptance.js`: write01 (915-ревизия) → write02 финал
  2cf4c7fa…; peer-01 REQUEST_CHANGES (2 parser-хола, stale header,
  квадратичная математика) разрешён кросс-ревью 2:0 (premult-affine
  подтверждён — PRIMARY по CSS Images §3.4.2; квадратичное straight-RGBA
  возражение опровергнуто и отозвано автором); validator-oracle-01
  ses_e4a88bd51ffeTYgbOa2q8U3MDO math PASS
  (freeze-блок снят parser/header-фиксом); финальный test-peer-02
  ses_e4a70cce4ffeMz1ESF7AAAugi4 PASS (Node-чеки/пробы/хэши); parent
  наблюдал self-чеки, D1/D2 reversion-bite, parser 19/19, guards/пороги
  22 без изменений; genuine geometry RED на 19c1e068… (20/22 pass,
  style-390 + error-390 824>390; contrast 27/27; unexpected/page/console
  []); NEW freeze 2cf4c7fa… frozen_at 2026-11-07T10:38:55.464252+00:00
  (frozen-tests-amendment.json) — reconciled (freeze-файл + хэши
  прочитаны code-ролью) — 2026-11-07.
- L023A-code-grid-01 — ТЕКУЩАЯ — CSS-writer — единственная строка
  `grid-template-columns: minmax(0, 1fr);` внутри `.people-shell`
  (после `display: grid;`); diff vs
  /tmp/opencode/lifesim-023A-test-02/style.pre-geometry.css = 1 строка;
  CSS 7305fdb462138125a9facb33caef8ec2c65f0828ac379b595296a86e9b89a1ad;
  focused 99 passed; node --check app.js + browser_acceptance.js OK;
  frozen-хэши до/после неизменны; parent reconciliation — reconciled
  (фактический diff/freeze/code-peer-01 PASS/native 22/22 GREEN —
  parent observed) — 2026-11-07.
- L023A-code-peer-01 — ses_e4a62e064ffeRzL20WHABjTdNn — PASS (fresh
  code-role readonly): ровно одна строка grid, frozen-хэши, скоуп
  сохранён; флаг DOC-опечатки «premult-affine опровергнут» (reversed) —
  исправлен L023A-doc-verify-final-01 — 2026-11-07.
- L023A-verify (родитель) — full gate 03 на CSS 7305fdb4…
  (/tmp/opencode/lifesim-023A-full-verify-eP9Vv5/gates.json: failed
  false, secrets ok 6, check exit 0 timedOut false, 1238 passed /
  13 deselected / 8825 warnings 447.06s / 465474ms весь gate, integrity
  before==after, HEAD 323 unchanged); native 22/22 GREEN
  (amendment-candidate-final.json, app 33d7bb96…, unexpected/page/console
  [], contrast 27 = 4 gradient + 23 solid, HTTP — только intentional
  fixture 404/500 по докам кейсов); world 12/12 (map 390 267.3125px);
  strip-проба (amendment-strip-probe.json): viewport/page 390, client
  344 / scroll 790, 5×150, scrollLeft 0→446 — 2026-11-07.
- L023A-ui-final-01 + ui-strip-02 — ses_e4a6139a9ffe3VWqOKPUZ0JsUR —
  desktop PASS / mobile visible PASS; followup strip: последовательный
  raw-read каждого нового пути → PASS (0→446); свежие 7305-снимки
  people-amendment-{desktop,mobile,mobile-full,mobile-strip-end}.png в
  shared/ (старые people-final-* сейчас в shared отсутствуют; причина
  не установлена; исторические записи сохранены);
  raw visual, без формальных WCAG/whole-UI заявлений — 2026-11-07.
- L023A-doc-verify-final-01 — doc (DOCONLY) — исправление перевёрнутой
  math-формулировки (premult-affine подтверждён; квадратичное
  straight-RGBA возражение опровергнуто/отозвано) + запись фактической
  локальной приёмки в трио; только 3 SDD; parent read reconciled
  (parent observed) — 2026-11-07.

## Acceptance ТЕКУЩЕЙ стадии

- [x] Трио specs/023 создано (L023-doc-01, DOCONLY; только эти 3 файла).
- [x] T1 тесты m59 — написаны, подлинный RED наблюдён, заморожены родителем
      (frozen-хэши — plan.md T1, 2026-11-07T03:40:34.645213Z).
- [x] T2 CSS People block — ИСПОЛНЕНО локально: code01 + code02
      parent-reconciled (diff/границы/geometry-sequence; code-peer-03
      PASS; hover-проба PASS TRUE). Финальный CSS 19c1e068….
- [ ] T3 верификация + браузерная приёмка — автоматические команды DONE
      (focused 99; full gate 02 на финальном CSS: 1238 passed, 438.16s),
      НО A1–A7 full-browser НЕ ПРОЙДЕНЫ: frozen suite RED 17/22 (2+2
      false-RED, 1 реальный A4 390 overflow 824/390 = BASE). Блок не
      снят; чекбокс открыт.
- [ ] T4 ревью — code-peer-03 PASS + UI-final-02 raw visual DONE
      (palette PASS / geometry REQUEST_CHANGES); publication validator
      NOT RUN (блок).
- [ ] T5 публикация/деплой — NOT RUN, BLOCKED (Git-объекты/публикация не
      создаются); T6 JOURNAL/KB — pending (housekeeping этой волной не
      предпринимался, чужой JOURNAL не трогался). Итог — конкретный
      people-warm-shell gap волны #59, не «весь #59 закрыт».

Амендмент A1: приёмка амендмента — A0–A4 (см. «Амендмент A1 — порядок
исполнения»); чекбоксы закрывает родитель по фактическим наблюдениям;
старая стадия остаётся не production-ready до зелёной приёмки. Текущее
состояние (2026-11-07): A0–A3 закрыты (локальная приёмка наблюдена),
A4 — единственный открытый пункт (publication validation NOT RUN).

## Точка возобновления

- Текущая фаза: локальная запись завершена (doc-final-03 reconciled;
  doc-final-04 — узкая правка журнала, только tasks.md); в работе ничего;
  исходный скоуп реализован полностью, OVERALL BLOCKED не изменён.
  > SUPERSEDED: L023A-doc-admit-01 (2026-11-07) — «следующий шаг — НОВАЯ
  scope-admission» ИСПОЛНЕНА, admission состоялась (parent observed
  2026-11-07T09:30:26Z); см. «Амендмент A1 — порядок исполнения».
- Следующий шаг — НОВАЯ scope-admission ТОЛЬКО для: (1) geometry-
  remediation (A4 390×844 width 824, legacy overflow = BASE) и (2)
  test-oracle revision frozen-кейсов (2 style + 2 contrast false-RED) с
  fresh RED/review/freeze. Generic «continue» допускает ТОЛЬКО исходный
  скоуп; пороги A1–A7 и frozen-файлы не ослабляются.
  > SUPERSEDED: L023A-doc-admit-01 (2026-11-07) — допущено; текущая фаза
  ниже.
- Текущая фаза (Амендмент A1): амендмент допущен (approved base 323d9c00… +
  starting CSS 19c1e068…; JS 81a43d72… — исторический freeze, superseded
  окном ревизии); TEST-фаза ЗАВЕРШЕНА (D1+D2, peer-02 PASS, parent RED,
  NEW JS freeze 2cf4c7fa… 2026-11-07T10:38:55Z, окно ревизии ЗАКРЫТО);
  CODE CSS исполнен L023A-code-grid-01 (1 строка root-track в
  `.people-shell`, CSS 7305fdb4…, focused 99 passed, node --check OK,
  frozen-хэши неизменны); локальная верификация ЗАВЕРШЕНА и наблюдена
  (code-peer-01 PASS; full gate 03 на CSS 7305fdb4…; native 22/22 GREEN;
  world 12/12; UI+strip PASS) — LOCAL ACCEPTED. Текущая фаза —
  публикация: validator → commit/push/PR/CI/merge → lane-A deploy →
  prod probes — NOT RUN (единственный открытый пункт A4); production
  readiness не заявляется; #59 — partial wave. Старая стадия —
  не production-ready до зелёной приёмки; пороги A1–A7 и frozen-PY не
  ослабляются.

## Вне скоупа (осознанно)

Source JS/API/relations/roles/mechanics/state/layout/breakpoints, ассеты/
шрифты/финальная палитра (issue #55; §48 provisional переиспользуется), deps,
`:root`/`--w-line`-дефиниции, connection-banner tail, прочие UI-экраны,
транспорт/деплой-инфра, ВСЕ существующие тесты, `.env`/секреты, JOURNAL/
shared, GitHub issue edit.

Амендмент A1: единственные исключения из этого списка — окно ревизии
`browser_acceptance.js` (test-роль) и root-track constraint в People-блоке
`style.css`; всё остальное вне скоупа остаётся вне скоупа.
