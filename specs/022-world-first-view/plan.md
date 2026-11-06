# 022 — plan

## Подход

Минимальное изменение двух существующих файлов + SDD-трио. Тест-роль
работает ПЕРВЫМ (2 новых файла m270), код-роль тесты не правит. Без новых
зависимостей, без сервера/схем.

### T1. Тесты (test-роль, FIRST)

- `tests/m270/test_world_first_view.py` — Python source-пины + unit для CI:
  компоновка `welcomeCard()` (heading/greeting/dismiss вне `<details>`;
  details без `open`, русская summary, фото+tips+close внутри), toggle без
  записи флага (пины счётчиков m107), CSS-пины (details-блок в welcome-
  секции до `/* #175`, aspect 3/4, object-fit, mobile 1fr), мобильный
  map-first grid-areas.
- `tests/m270/world_first_view.browser.js` — native MCP browser-функция
  реальной геометрии против ЛОКАЛЬНЫХ candidate-байтов (не прод): A1–A6
  из spec.md (390×844 fresh closed → map ≥160px при scroll 0; expanded →
  контент достижим; dismissed/persisted/denied read/denied write; 1440;
  nav9/clock/actions/scene). Без новых зависимостей.
- Базлайн RED на базе f580: дойти до карты, падение «0 vs ≥160».
- Независимый test-peer ДО freeze; после фактического базлайн-RED + peer
  PASS оркестратор freeze ОБОИХ новых тест-файлов (хэши). Все последующие
  CODE-ордера запрещают оба новых + все старые тесты.

### T2. Код (code-роль, после freeze)

- app.js `welcomeCard()`: heading + короткое приветствие + dismiss видимы;
  `<details>` (без `open`) с русской `<summary>` оборачивает
  `welcome-photos` + `welcome-tips` + `welcome-close`; toggle — чисто UI,
  ноль записей; литералы m107 не дублируются (счётчики ==1/==3/==1);
  dismiss-логика (S.welcomeDismissed + setItem + node.remove) не тронута.
- style.css: компактный welcome/details-блок (summary-строка, отступы) —
  в существующем welcome-секции до `/* #175` (пин порядка); мобильный
  map-first: в mobile-медиа `.world-map-stage` grid-template-areas →
  `map/card/pills` (существующий DOM-порядок, без DOM-перестановок);
  desktop grid-areas `card/pills/map` не меняется; `:root` байты нетронуты.

### T3. Верификация кода

- `node --check backend/app/web/app.js`; `node --check tests/m270/world_first_view.browser.js`.
- `uv run pytest tests/m270/ tests/m107/ tests/m121/ tests/m146/ tests/m149/ tests/m238/ tests/m241/ tests/m269/ -q`.
- `bash scripts/check.sh`; реальный `run-project-gates.mjs --root <repo> --all`;
  `git diff --check && git status --short`.
- Браузерная ручная приёмка — отдельная истина (не подменяет CI-pytest и
  наоборот). После lane-A деплоя: HTTP/security/static-byte пробы +
  браузер против прод-ассетов (fixture-кейсы). Без заявлений об
  authenticated API.

### Откат

Полный SHA `f58015b5013b8d14298c48742866b3cca5011c01`.

## Риски

- Focus/disclosure при ре-рендерах: раскрытие не обязано переживать
  `replaceChildren`, но не должно ломать клавиатурный фокус; dismiss вне
  details — всегда доступен.
- Случайная запись флага при toggle — запрещена требованием W4 (пины
  счётчиков setItem/getItem ловят).
- CSS-каскад: shell-scoped специфичность (урок #59/m107 peer-fix);
  details-правила не должны задеть другие `details` экрана.
- Fixture false RED: браузерный тест — только локальные candidate-байты;
  прод-байты не подменять локальными и наоборот.

## Сверка с попытками (подготовка, наблюдаемое)

- L022-architect-admission-01 (child ses_e504f6aa4ffeZ6WJaYg2Sq0IX5) —
  CONFIRM reconciled: минимальный дизайн подтверждён на той же базе f580.
- L022-validator-prepare-01 (child ses_e4fdebbe1ffeB2Trz9nHJ0Y57x) —
  guarded PASS: все branch-гарды выполнены родителем; ветка
  `wave/270-world-first-view` создана реальным `git switch -c` от чистого f580.
- Родительский preflight: doctor PASS (SDD on 1 valid gate; per-project
  assignment cache missing — ADVISORY, не provider failure); реальный
  `run-project-gates.mjs --all` завершён 448093ms;
  `/tmp/opencode/lifesim-022-preflight-2GiglV/gates.log` — ровно 5 строк:
  `EVIDENCE LEVEL: full`; `PASS secrets (0ms, head=?)`; `PASS check
  (448093ms, head=f58015b5013b)`; `PASS worktree-integrity`; `RESULT PASSED`.
  ВАЖНО: preflight НЕ является VERIFY-успехом; raw pytest-счётчик из этого
  прогона неизвестен — не выдумывать (никаких «1231»).
- Capacity global needs Review[]; наблюдаемая метка UTC 09:11:30Z
  (метаданные); остальные времена unknown — не изобретать.
- L022-doc-01 (child ses_e4fdca61effeIOIDpYzy49sMHc, DOCONLY) — трио
  создано; parent read + reconciled.
- T1 (test-роль, до freeze): попытки 01/02 — артефакт не создан
  (MAXSTEPS / пустой возврат; target absent подтверждён родителем);
  L022-test-python-01 (ses_e4fcc600cffe4SEhZ5Hli9QL5a) — Python 98 строк →
  peer-01 (ses_e4fc798c4ffezml7CuDmz59eqS) F1–F3 (no-open пин, слабый
  map-first, длина summary) → python-02 — 100 строк → peer-02 PASS.
  L022-test-browser-01 (ses_e5049d6f2ffeeNnqd1hXykcDNk) — 416 строк →
  peer-01 (ses_e4fbe717dffeJ4c54dQim46AXM) PASS + рекомендации
  (photo-wait, detour) → browser-02 харденинг, peer-02 PASS; первый
  MCP-прогон — SETUP-сбой (Node crypto import, не RED) → browser-03 —
  заменён только hash-адаптер на WebCrypto, peer-03 PASS, source-binding
  сохранён (итог 439 строк). Времена unknown.
- Базлайн RED (родитель, финальный файл, локальные BASE-байты + GET-фикстуры,
  не прод): браузер 12 кейсов — case1-390 mapVisiblePx=0 (тест дошёл до
  карты), case2 — нет disclosure; 8 storage-кейсов PASS; Python 4F/1P.
  Freeze родителем 2026-11-06T10:08:11Z; хэши — в tasks.md.
- L022-code-01 (code) — минимальный фикс по frozen-контракту:
  app.js welcomeCard() reorder в native `<details>` (без `open`), dismiss
  байт-прежний; style.css — scoped details/summary правила + mobile
  map-first grid-areas в существующем 1100px-запросе. Наблюдаемо воркером:
  node --check ×2 PASS; фокус pytest 79 passed; git diff --check PASS;
  frozen-хэши не изменились. Parent read + reconciled (primary).
- Родительский браузерный прогон кандидата code-01 (native MCP, локальные
  candidate-байты: static app 8439e531…/css 26c8834e… совпали с файлами;
  GET-фикстуры, WS заблокирован; не прод): 12 кейсов запущены — геометрия
  390 mapVisiblePx=267.3125 ≥160 PASS; все 8 storage-кейсов PASS; ОБА
  case2 FAIL только closedHidden=false после реального keyboard
  open→close (expanded проходил полностью, writes=0); allPass=false;
  unexpectedAPI=[], pageErrors=[]. НЕ green. Read-only DOM-проверка
  (fresh closed 1440): details.open=false, computed display grid, фото —
  offsetParent=SECTION, layout-прямоугольники ~2×2.656px, checkVisibility
  false → закрытое тело создаёт боксы.
- L022-code-peer-01 (ses_e4fa54574ffeTZ4doG0Exvya8E) — REQUEST_CHANGES:
  единственный дефект — bounded CSS (закрытый grid-details создаёт
  анонимные боксы); остальные W1–W6/скоуп диффа — PASS; явный фикс:
  `.world-shell .welcome-details:not([open]) > :not(summary)
  { display: none; }` в существующей welcome-секции.
- L022-code-02 (code) — guard пира добавлен дословно; трим комментариев
  01 до одной why-строки каждый (app/mobile/details), ссылки «#270»
  убраны (test-namespace, не issue); логики больше не менялось.
  Наблюдаемо воркером: node --check ×2 PASS; фокус pytest 79 passed;
  frozen-хэши OK; git diff --check PASS; source SHA app 33d7bb96…,
  css 5417801f…. Parent read + reconciled.
- L022-code-peer-02 (та же сессия ses_e4fa54574ffeTZ4doG0Exvya8E) —
  независимое чтение фактического диффа — блокирующая находка 01 ЗАКРЫТА
  → PASS; история REQUEST_CHANGES 01 сохранена; comment-cleanup
  завершён. Время unknown.
- Финальный frozen-браузерный прогон (родитель, 10:26:42Z, 439-строчный
  файл не менялся): ВСЕ 12 кейсов true / allPass=true — map 390=267.3125 /
  1440=210.8125 (≥160); summary open: 2 фото + 3 tips/тексты, closedHidden
  =true, writes=0; dismiss/persisted/denied-read/denied-write 8/8 PASS;
  nav9/часы/без overflow; unexpectedAPI=[], pageErrors=[], 12 WS
  preconnect заблокированы; static SHA = candidate app 33d7bb96…/css
  5417801f…. Локальные ассеты + GET-фикстуры, НЕ аутентифицированный прод.
- Полный VERIFY (родитель, sh_1b066c2e0001mTHG1vPQSGTbhR → gates.json,
  evidenceLevel=full, failed=false): secrets ok (7 файлов); check argv
  `bash scripts/check.sh` exit 0 — «1236 passed, 13 deselected, 8825
  warnings in 484.51s» (10:28:07.290Z, 503477ms; 13 deselected —
  существующий slow-контракт, без ослабления; 8825 — легаси-deprecations,
  не новый отказ); worktree-integrity ok (до/после идентичны). 4 текущих
  хэша = verify-артефакту (sha-c 4/4). Preflight 448093ms остаётся
  preflight; этот прогон — VERIFY.
- UI L022-ui-final-01 (ses_e504f6a6cffezUCEQgD2EjXnNh) — PASS: raw-read
  всех 6 owner-изображений shared/lifesim-022/ (before/after world 390/
  1440, after-expanded 390/1440): карта видна при закрытой карточке,
  expanded — весь контент сохранён, без overflow, desktop-порядок без
  изменений. Неблокирующие заметки (events-loading timing, бледная
  существующая полоса) — вне кода; DOM-сверка родителя: world-secondary
  панели (журнал 70px при пустых tasks-фикстурах, портрет 237px, scene
  154px, events h179 со всеми 4 fixture-метками) — нового source-gap
  нет, правки не требуют. Клавиатура доказана родительским браузером,
  не визуальным воркером. Fixture-лимиты честны: НЕ аутентифицированный
  прод.
- Post-deploy probe-адаптер (test-роль, ТОЛЬКО /tmp/opencode/
  playwright-dumps/lifesim-022-prod/capture.js, SHA bbca0f41…):
  производная frozen-набора (4 транспортные привязки: BASE https, OUT
  prod-tmp, без Python/static prefix-strip, hash-пути /static/app.js|
  style.css); byte-identity функциональных ассертов подтверждена
  родителем (diff + reverse-normalization); peer
  L022-prod-adapter-peer-01 (ses_e4fbe717dffeJ4c54dQim46AXM) PASS
  (guarded read-only transport). В 7-файловый product-дифф НЕ входит;
  готовый post-deploy probe-интерфейс на будущее — НЕ деплой-заявление;
  прод-исполнения/PR/merge/деплоя ещё не было.
- ПЕНДИНГ публикации (не заявлять): T4 publication validator; T5
  commit/push/PR → green CI → merge → trusted main CI → lane-A deploy +
  пробы; T6 JOURNAL/KB (пост-коммитные факты — в канонический #59 +
  workspace JOURNAL по факту). Одобрение владельца — «commit approved»
  на полные 7 путей от базы f58015b5 (receipt в транскрипте родителя;
  метка времени хода владельца unknown). Этот DOCONLY-апдейт — только
  метаданные; full-gate claim на новые байты не делается (код/тесты
  байт-те же, что в verify-артефакте).
