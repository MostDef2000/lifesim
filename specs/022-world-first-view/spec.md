# 022 — World-first view (#59 audit: карта ниже фолда на мобильном)

## Цель

Первый визит авторизованного игрока на `#/world`: на 390×844 при scroll 0
видно ≥160 CSS px реальной карты при закрытом welcome-раскрытии; весь
контент карточки остаётся достижимым; desktop не деградирует. Map-primary —
уже одобренный канон; новой арт-постановки нет.

## Проблема (наблюдаемое, read-only audit 2026-11-06)

- 390×844, `#world`, fresh: `.map-card` top 1746px ⇒ **0px карты при scroll 0**;
  `.welcome-card` top 186 / height 1008 (фото 1-колонкой 240px×3/4, заголовок,
  копия, 3 подсказки, close, кнопка); `.world-shell` 2745; overflowX=false.
- Desktop 1440: welcome-карточка oversized для вьюпорта.
- Prod vs local static bytes идентичны (app `4189e272…af65f8f`,
  css `39e5b50b…41b69f12`) — gap реален и в проде. Claim об
  аутентифицированном прод-аккаунте НЕ делается (фикстуры GET).

## Скоуп (точные пути, ровно 7 в волне; здесь пишутся 3)

- `backend/app/web/app.js` — ТОЛЬКО `welcomeCard()`: компоновка.
- `backend/app/web/style.css` — ТОЛЬКО welcome/details-блок и мобильный
  порядок `.world-map-stage`.
- `tests/m270/test_world_first_view.py` (NEW, CI-истина pytest),
  `tests/m270/world_first_view.browser.js` (NEW, ручная браузерная истина).
- `specs/022-world-first-view/{spec,plan,tasks}.md` — этот трио.

## Исключения (не трогается)

Бэкенд/API/схемы/auth/poll/routes/weather/gameplay-механика, ассеты/шрифты/
финальная палитра, зависимости, прочие UI-экраны, транспорт/деплой-инфра,
ВСЕ существующие тесты (frozen), `.env`/секреты, `.opencode`/`.specify`
конфиг, AUDIT.md (историческая запись; новый receipt разрешает её без
переписывания). Пины m107 не ослабляются: `welcomeCard(),` — первый ребёнок
`.world-shell` (call-site между main и map-stage), счётчики литералов
(фото/alt/копия/3 tip/`"welcome-tips"`==1/dismiss), CSS-блок `.welcome-card`
до `/* #175: connection banner`, aspect-ratio 3/4, object-fit cover,
mobile 1-колонка, `:root` байты.

## Требования

- W1. `.welcome-card` остаётся первым ребёнком `.world-shell` (m107).
- W2. Видимо без раскрытия: заголовок «Кошка-консьержка», короткое
  приветствие, существующая кнопка «Я освоился».
- W3. Native `<details>` (изначально закрыт, без `open`) с явной русской
  `<summary>` содержит ОБА исходных фото + alt-тексты и все 3 подсказки +
  closing-текст — без изменений, доступно (клавиатура/скринридер).
- W4. Переключение раскрытия НЕ пишет флаг onboarding (ни
  `localStorage`, ни `S.welcomeDismissed`); существующие dismiss/persisted/
  denied read/write семантики неизменны (пины счётчиков m107: getItem==1,
  setItem==1, `vl1.welcome.done`==2, `S.welcomeDismissed`==2).
- W5. Фото: исходная геометрия (aspect 3/4, object-fit, mobile 1fr/240px).
- W6. Мобильный: map-stage первым существующим DOM-порядком (map →
  status → actions), status/actions ниже; desktop-stage без изменений.
- W7. Без clipping карты, nested scroll, DOM focus mismatch.

## Acceptance matrix

| # | Сценарий | Ожидание |
|---|----------|----------|
| A1 | 390×844 fresh, details закрыт, scroll 0, images loaded | реальная карта ≥160 CSS px видна |
| A2 | 390/1440 expanded | оба фото + все тексты достижимы; welcome может толкать карту вниз |
| A3 | клавиатура | expand/collapse details + dismiss доступны |
| A4 | 390/1440 × fresh/expanded/dismissed/persisted/denied read/denied write | семантика m107 неизменна |
| A5 | nav 9 вкладок / часы / карта / actions / scene | без изменений |
| A6 | 1440 fresh/expanded | desktop без деградации, без горизонтального overflow |

## Верификация (две отдельные истины)

- CI-истина: pytest `tests/m270/` (+ фокус m107/m121/m146/m149/m238/m241/
  m269), `node --check backend/app/web/app.js`, `bash scripts/check.sh`,
  реальный `run-project-gates.mjs --root <repo> --all`.
- Браузерная истина (ручная приёмка, НЕ подменяет CI): native MCP Playwright,
  реальная геометрия против ЛОКАЛЬНЫХ candidate-байтов (не прод-старых),
  GET-фикстуры, WS-guard.
- Базлайн RED на базе f580: тест ДОЛЖЕН дойти до карты и упасть «0 vs ≥160»,
  НЕ «селектор details не найден».

## Статус (2026-11-06, финальный pre-publication, doc-evidence-01)

- T1/T2 выполнены и сверены родителем; локальная верификация и полный
  VERIFY завершены (наблюдаемо родителем, gates.json `evidenceLevel=full`,
  `failed=false`): secrets ok (7 файлов); check = `bash scripts/check.sh`
  exit 0 — Ruff + non-slow pytest «1236 passed, 13 deselected, 8825
  warnings in 484.51s» (10:28:07.290Z, 503477ms; 13 deselected —
  существующий slow-контракт, без ослабления; 8825 — легаси-deprecations);
  worktree-integrity ok. Текущие 4 хэша (app/css/оба frozen-теста) =
  verify-артефакту (sha256sum -c 4/4 OK).
- Frozen-браузер (родитель, rerun 10:26:42Z, файл не менялся): ВСЕ 12
  кейсов true / allPass=true — map 390=267.3125px / 1440=210.8125px (≥160),
  summary open: 2 фото + 3 tips/тексты видимы, closedHidden=true,
  writes=0; dismiss/persisted/denied-read/denied-write 8/8 PASS; nav9/
  часы/без overflow; unexpectedAPI=[], pageErrors=[], 12 WS preconnect
  заблокированы. Static SHA = фактические candidate-байты (app 33d7bb96…,
  css 5417801f…). Локальные ассеты + GET-фикстуры — НЕ аутентифицированный
  прод-API/integration.
- Пиры: code-peer-02 (та же сессия ses_e4fa54574ffeTZ4doG0Exvya8E) —
  блокирующая находка 01 закрыта → PASS (история REQUEST_CHANGES 01
  сохранена); test-пиры PASS; UI L022-ui-final-01
  (ses_e504f6a6cffezUCEQgD2EjXnNh) — PASS по всем 6 owner-изображениям
  shared/lifesim-022/ (карта видна при закрытой карточке, expanded без
  потерь/overflow, desktop без изменений; неблокирующие заметки —
  events-loading timing/бледная полоса — вне кода; fixture-лимиты честны).
- ПЕНДИНГ публикации (не заявлять): T4 publication validator; T5
  commit/push/PR → green CI → merge → trusted main CI → lane-A deploy +
  пробы; T6 JOURNAL/KB. Одобрение владельца — «commit approved» на полные
  7 путей от базы f58015b5 (receipt в транскрипте родителя; метка времени
  хода владельца unknown). Этот DOCONLY-апдейт меняет только метаданные —
  full-gate claim на новые байты не делается (код/тесты байт-те же, что
  в verify-артефакте). Итоговое заявление — не «весь #59 закрыт», а
  конкретный world-first-view gap этой волны.

## Риски

Focus/disclosure-состояние при ре-рендерах (`renderWorld` replaceChildren),
случайная storage-запись при toggle, CSS-каскад/специфичность (урок m107
peer-fix), fixture false RED.
