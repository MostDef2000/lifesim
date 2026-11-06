# 022 — tasks

## Порядок исполнения (test-first)

- [x] T1 (test-роль, ПЕРВЫЙ): `tests/m270/test_world_first_view.py` (100
      строк) + `tests/m270/world_first_view.browser.js` (439 строк).
      Подлинный базлайн RED на f580 (наблюдаемо родителем): браузер — все
      12 кейсов запущены, case1-390 mapVisiblePx=0 / geometryTarget=false
      (тест дошёл до карты, НЕ setup-сбой), case1-1440 pass=false,
      case2 (390/1440) — disclosure отсутствует → fail; остальные 8
      storage/dismiss/persisted/denied-кейсов PASS (reads=1, writes=0/1);
      unexpectedAPI=[], pageErrors=[], WS preconnect заблокирован.
      Python на базе: 4 failed / 1 passed (0.04s, без import-ошибок).
      Независимые пиры — PASS; freeze ОБОИХ файлов родителем
      2026-11-06T10:08:11Z (хэши в журнале ниже).
- [x] T2 (code-роль, после freeze): app.js `welcomeCard()` — существующие
      узлы переупорядочены в native `<details>` (без `open`), dismiss-хендлер
      байт-прежний; style.css — scoped welcome-details/summary правила в
      существующей welcome-секции + mobile map-first grid-areas в
      существующем 1100px-запросе. Follow-up code-02: closed-guard пира +
      трим комментариев (одна why-строка, без «#270»). Итоговый дифф —
      минимум хунков: app welcome + 8 CSS-строк, без новой логики/API.
      Родитель прочитал весь product-дифф — сверено.
- [x] T3: верификация завершена. Локально (воркер): node --check ×2,
      фокус pytest 79 passed, git diff --check. Полный VERIFY (родитель,
      sh_1b066c2e0001mTHG1vPQSGTbhR → gates.json, evidenceLevel=full,
      failed=false): secrets ok/7; check argv `bash scripts/check.sh`
      exit 0 — «1236 passed, 13 deselected, 8825 warnings in 484.51s»
      (13 deselected — существующий slow-контракт; 8825 — легаси-
      deprecations); worktree-integrity ok; 4 текущих хэша = verify-
      артефакту (sha-c 4/4). Frozen-браузер rerun (10:26:42Z): 12/12
      allPass=true; static SHA = candidate app 33d7bb96…/css 5417801f….
- [x] T4 (ревью): code-peer-01 REQUEST_CHANGES (история сохранена) →
      code-peer-02 PASS; test-пиры PASS; UI L022-ui-final-01 PASS
      (6 owner-изображений). ПЕНДИНГ: publication validator.
- [ ] T5 (оркестратор): commit/push/PR → green CI → merge (точный SHA) →
      trusted main CI → DEPLOY-TRIGGER + lane-A deploy (полный SHA) →
      HTTP/security/static-byte пробы + браузерные прод-ассет кейсы
      (probe-адаптер готов — см. журнал; прод-исполнения ещё не было).
      Не заявлять выполненным заранее.
- [ ] T6: JOURNAL/KB-запись волны.

## Журнал попыток (наблюдаемое; времена, где не указано — unknown)

- L022-architect-admission-01 — architect — child
  ses_e504f6aa4ffeZ6WJaYg2Sq0IX5 — CONFIRM reconciled — дизайн подтверждён
  на базе f580 — время unknown.
- L022-validator-prepare-01 — validator — child
  ses_e4fdebbe1ffeB2Trz9nHJ0Y57x — guarded PASS — branch-гарды родителя;
  `git switch -c` от чистого f580 — время unknown.
- L022-preflight (родитель) — doctor PASS (SDD on 1 valid gate; assignment
  cache missing = ADVISORY); run-project-gates --all 448093ms; gates.log —
  5 строк PASS (head=f58015b5013b) — preflight ≠ VERIFY; pytest-счётчик
  неизвестен — метка UTC 09:11:30Z (метаданные), остальное unknown.
- L022-doc-01 — doc (DOCONLY) — child ses_e4fdca61effeIOIDpYzy49sMHc —
  трио specs/022 создано; parent read + reconciled — время unknown.
- L022-test-01 — test — MAXSTEPS-остановка, артефакт не создан (target
  absent подтверждён родителем) — время unknown.
- L022-test-02 — test — пустой возврат, артефакт не создан (target absent
  подтверждён родителем) — время unknown.
- L022-test-python-01 — test — child ses_e4fcc600cffe4SEhZ5Hli9QL5a —
  Python пин-файл 98 строк — время unknown.
- L022-test-peer-01 — test-peer — child ses_e4fc798c4ffezml7CuDmz59eqS —
  находки F1 (нет пина «изначально закрыт») / F2 (слабый map-first) /
  F3 (длина summary) → взяты в правку — время unknown.
- L022-test-python-02 — та же python-сессия — исправленный файл 100 строк —
  время unknown.
- L022-test-peer-02 — test-peer — фактическое чтение файла — PASS —
  время unknown.
- L022-test-browser-01 — test — child ses_e5049d6f2ffeeNnqd1hXykcDNk —
  браузерный файл 416 строк — время unknown.
- L022-test-browser-peer-01 — peer — child ses_e4fbe717dffeJ4c54dQim46AXM —
  PASS; рекомендации photo-wait + detour (взяты в browser-02) — время unknown.
- L022-test-browser-02 — та же browser-сессия — харденинг рекомендаций;
  browser-peer-02 PASS — время unknown.
- Первый MCP-прогон браузер-файла — SETUP-сбой (Node crypto import
  неподдержан в нативной VM) — НЕ тестовый RED — время unknown.
- L022-test-browser-03 — заменён ТОЛЬКО hash-адаптер на WebCrypto
  (browser-peer-03 PASS); source-binding сохранён; итог 439 строк —
  время unknown.
- Базлайн RED (родитель; финальный 439-строчный файл; локальный прогон
  против ЛОКАЛЬНЫХ BASE-байтов app 4189e272…/css 39e5b50b…, GET-фикстуры,
  WS preconnect заблокирован; НЕ прод и не интеграционный тест): см. T1.
- Freeze (родитель) — 2026-11-06T10:08:11Z —
  `tests/m270/test_world_first_view.py` =
  ce61203862c1ff9efc971b34d8e4b804282a4439c33538284f8f0b1e498221f6;
  `tests/m270/world_first_view.browser.js` =
  41b8aff6b83e1b0fb02dcf11fdf40166b1b73341174f33b1336639737bdaaf09.
- L022-code-01 — code — минимальный фикс app.js/style.css по
  frozen-контракту; наблюдаемо воркером: node --check ×2 PASS, фокус
  pytest 79 passed, git diff --check PASS, frozen-хэши OK до/после.
  Parent read + reconciled (primary) — время unknown.
- Браузерный прогон кандидата code-01 (родитель, native MCP, локальные
  candidate-байты app 8439e531…/css 26c8834e… — совпали; GET-фикстуры, WS
  заблокирован; не прод): 12 кейсов; геометрия 390 = 267.3125px ≥160
  PASS; 8 storage-кейсов PASS; ОБА case2 FAIL только closedHidden=false
  после реального keyboard open→close (expanded проходил, writes=0);
  allPass=false; unexpectedAPI=[], pageErrors=[]. НЕ green — время unknown.
- DOM-проверка родителя (read-only, fresh closed 1440): details.open=false,
  computed display grid; фото offsetParent=SECTION, прямоугольники
  ~2×2.656px, checkVisibility false → закрытое тело создаёт боксы.
- L022-code-peer-01 — code-peer — child ses_e4fa54574ffeTZ4doG0Exvya8E —
  REQUEST_CHANGES: единственный дефект — closed grid-details создаёт
  анонимные боксы (bounded CSS); остальные W1–W6/скоуп — PASS; явный фикс:
  closed-guard `:not([open]) > :not(summary) { display: none; }` —
  время unknown.
- L022-code-02 — code — guard пира добавлен дословно в существующую
  welcome-секцию; трим комментариев 01 до одной why-строки
  (app/mobile/details), ссылки «#270» убраны (test-namespace, не issue);
  логика более не менялась. Наблюдаемо воркером: node --check ×2 PASS,
  фокус pytest 79 passed, frozen-хэши OK, git diff --check PASS; source
  SHA app 33d7bb96…, css 5417801f…. Parent read + reconciled — время unknown.
- L022-code-peer-02 — code-peer — та же сессия
  ses_e4fa54574ffeTZ4doG0Exvya8E — независимое чтение фактического диффа —
  блокирующая находка 01 ЗАКРЫТА → PASS; comment-cleanup завершён —
  время unknown.
- Финальный frozen-браузерный прогон (родитель, 10:26:42Z capture-start,
  файл не менялся): ВСЕ 12 кейсов true / allPass=true — map 390=267.3125 /
  1440=210.8125 (≥160); summary open: 2 фото + 3 tips/тексты, closedHidden
  =true, writes=0; dismiss/persisted/denied-read/denied-write 8/8 PASS;
  nav9/часы/без overflow; unexpectedAPI=[], pageErrors=[], 12 WS
  preconnect заблокированы; static SHA = candidate app 33d7bb96…/css
  5417801f…. Локальные ассеты + GET-фикстуры, НЕ аутентифицированный прод.
- Полный VERIFY (родитель, sh_1b066c2e0001mTHG1vPQSGTbhR → gates.json,
  evidenceLevel=full, failed=false): secrets ok/7; check argv
  `bash scripts/check.sh` exit 0 — «1236 passed, 13 deselected, 8825
  warnings in 484.51s» (10:28:07.290Z, 503477ms; 13 deselected —
  существующий slow-контракт; 8825 — легаси-deprecations); worktree-
  integrity ok. 4 текущих хэша = verify-артефакту (sha-c 4/4). Preflight
  448093ms — НЕ VERIFY; этот прогон — VERIFY.
- UI L022-ui-final-01 — UI — child ses_e504f6a6cffezUCEQgD2EjXnNh — PASS:
  raw-read всех 6 owner-изображений shared/lifesim-022/; карта видна при
  закрытой карточке, expanded без потерь/overflow, desktop без изменений;
  неблокирующие заметки (events-loading timing, бледная полоса) — вне
  кода; DOM-сверка родителя: world-secondary панели (журнал 70px/портрет
  237px/scene 154px/events h179) — нового gap нет. Клавиатура —
  родительский браузер. Fixture-лимиты честны — время unknown.
- Post-deploy probe-адаптер (test-роль, ТОЛЬКО
  /tmp/opencode/playwright-dumps/lifesim-022-prod/capture.js, SHA
  bbca0f41…): производная frozen-набора (4 транспортные привязки);
  byte-identity ассертов подтверждена родителем; peer
  L022-prod-adapter-peer-01 (ses_e4fbe717dffeJ4c54dQim46AXM) PASS
  (guarded read-only). В product-дифф не входит; готовый post-deploy
  probe-интерфейс, НЕ деплой-заявление; прод-исполнения ещё не было.
- Одобрение владельца: «commit approved» — полные 7 путей от базы f580
  (receipt в транскрипте родителя; метка времени хода владельца unknown).
- L022-doc-evidence-01 — ТЕКУЩАЯ — doc (DOCONLY) — финальные
  pre-publication факты записаны в трио; только метаданные, 4 хэша
  source/test неизменны; full-gate claim на новые байты не делается.
  Будущие ходы воркера НЕ само-заявлять reconciled — ledger обновляет
  родитель.

## Acceptance ТЕКУЩЕЙ стадии

- [x] Трио specs/022 создано (L022-doc-01; parent reconciled).
- [x] Тесты m270 (test-роль) — написаны, пиры PASS, подлинный RED на базе
      наблюдаем родителем, freeze 2026-11-06T10:08:11Z (хэши выше).
- [x] Код app.js/style.css (code-01 + follow-up code-02) — записан и
      сверен родителем (полный diff прочитан); code-peer-01
      REQUEST_CHANGES (история сохранена) → code-peer-02 PASS.
- [x] Полная локальная верификация (родитель): фокус 79 passed; полный
      VERIFY gates.json (evidenceLevel=full, failed=false; check argv
      `bash scripts/check.sh` exit 0 — 1236 passed, 13 deselected
      [существующий slow-контракт], 8825 легаси-warnings; secrets ok/7;
      worktree-integrity ok); frozen-браузер 12/12 allPass=true
      (rerun 10:26:42Z); static SHA = candidate app 33d7bb96…/css 5417801f….
- [x] UI-приёмка L022-ui-final-01 — PASS (6 owner-изображений;
      fixture-лимиты честны; клавиатура — родительский браузер).
- [ ] T4 publication validator — ПЕНДИНГ.
- [ ] T5 commit/push/PR → green CI → merge → trusted main CI → lane-A
      deploy + пробы — ПЕНДИНГ (probe-адаптер готов, прод-исполнения
      ещё не было).
- [ ] T6 JOURNAL/KB — ПЕНДИНГ. Пост-коммитные факты — в канонический #59 +
      workspace JOURNAL по факту; этот pre-commit SDD не претендует на
      будущее. Итог — конкретный world-first-view gap волны, не «весь
      #59 закрыт».

## Вне скоупа (осознанно)

Бэкенд/API/схемы/auth/poll/routes/weather/gameplay, ассеты/шрифты/финальная
палитра, зависимости, прочие UI-экраны, транспорт/деплой-инфра, все
существующие тесты, ОБА frozen-файла m270, AUDIT.md (исторический).
