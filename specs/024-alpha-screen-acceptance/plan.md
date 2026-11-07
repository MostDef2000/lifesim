# 024 — Plan

## Текущее поведение (evidence)

- main `cbd174843196fa57a17125dba9d82bb1fd3a8384` задеплоен; прод
  (`lf.mostdef.ru`) отдаёт те же байты: style.css `7305fdb4…`,
  app.js `33d7bb96…` (сверено curl-хэшами 2026-11-07).
- Доставленные волны: desire #267/#268, shared shell #274, world
  first-view #275, people #276. Существующие сьюты зелёные через
  `bash scripts/check.sh` (ruff + pytest -m 'not slow').
- Каноническая семья в текущем билде: `#/world` (LOD-кнопки
  «Остров/Район/Локально», `#scene-view` всегда в world view),
  `#/chat` (`#chat-msg-input`), `#/inventory`, `#/tasks`, `#/desire`
  (`#desire-input`), `#/profile` (роли). Маршруты — app.js:168–190.

## Минимальный дизайн

- Скретч-харнесс `/tmp/opencode/lifesim-059-closeout/harness.js` —
  НЕ коммитится. Playwright `async (page) => {}` под нативный MCP
  `run_code` (модель исполнения People-сьюта
  `tests/m59_people_warm_shell/browser_acceptance.js`).
- Реальный локальный бэкенд: сид
  `PYTHONPATH=backend .venv/bin/python -m app.simulation.cli simulate
  --days 0 --population 20 --seed 42`, лаунчер `create_app` + uvicorn
  :8831 (NullPool), БД в /tmp. Реальный логин через UI.
- Кейсы per-criterion с try/catch continue-on-fail; fresh document per
  case; per-doc регистры unexpected/pageErrors/consoleErrors; WS закрыт
  pre-connect; fail-closed роутинг (BASE-origin GET/HEAD passthrough,
  остальное — abort; исключения: `/auth/login`, `/visual/scenes`).
- Контраст-оракул — read-only копия из People-сьюта (premultiplied sRGB
  interval arithmetic); overflow-оракул tolerance 0.
- S9: admin timescale 600× из отдельного браузерного контекста
  (единственная разрешённая мутация), восстановление 1×.
- S4: «Осмотреть окрестности» → POST `/visual/scenes` → 503 →
  fallback-стейт; Flux-ready образ недостижим без GPU — фиксируется как
  finding, не как тихий пропуск.

## Affected paths

- `specs/024-alpha-screen-acceptance/{spec,plan,tasks}.md` — коммитится.
- `shared/lifesim-059-closeout/**` — генерация, не коммитится.
- `/tmp/opencode/lifesim-059-closeout/**` — скретч (харнесс, стенд, БД).

## Risks

- S4 Flux-ready недостижим без GPU (visual.enabled=false → 503) →
  проверяется fallback-стейт, ready-образ фиксируется как finding.
- `/weather` на стенде может отвечать `{"enabled":false}` (квир
  `get_or_create_weather` на существующем дне) → S9/U8 деградируют до
  time-phase с честной фиксацией фактического состояния.
- Контраст-оракул отказывается считать text поверх `backdrop-filter`
  (world-экраны) → ожидаемый громкий RED-файндинг, не баг харнесса.
- Дрейф байтов локаль/прод исключён двойной хэш-проверкой.

## Test design

- Fail-closed чеки: pageerror/consoleError/unexpected request роняют
  кейс; никаких тихих проглатываний.
- Peer-проверка: независимый воркер воспроизводит прогон/кросс-чек.
- Независимое визуальное ревью скриншотов против референса (ui-воркер).

## Rejected alternatives

- Appearance/Settings repaint — вне канонического набора, не условие
  закрытия #59.
- Prod+fixtures на всю семью — слабее реальной локальной интеграции,
  хрупкие фикстуры на 9 экранов.
- Новый трекнутый тестовый фреймворк — overengineering.
- Правки продукта под приёмку — запрещены (acceptance-only).
