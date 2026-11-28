# 026 — Tasks (#367)

- [x] Проверить current main и baseline contracts #269/#306/#313.
- [x] Общий dock World / Inventory, warm shell и контекст.
- [x] Inventory loading/ready/empty/error, retry и stale route guard.
- [x] Scoped CSS, без глобальных токенов и изменения карты.
- [x] Добавить focused test: девять destinations, loading/empty/ready/error, privileged gate, context, stale route.
- [x] Синтаксический разбор полного app.js и сценарии извлечённого production-кода в изолированном JavaScript runtime (PASS). Это не заменяет Node/pytest и браузер.
- [ ] Выполнить node --check, focused pytest и bash scripts/check.sh в checkout. Локальный контейнер не разрешает github.com; полный checkout недоступен.
- [ ] Реальный браузер 1440×900 / 390×844, keyboard/focus, screenshots before/after, CI/review.
- [ ] После прохождения gates — readiness/merge по проектному процессу. Deploy не разрешён.

## Evidence after first CI

- CI run 45994855071: 1894 passed, 2 failed (m173 inventory GET literal count; m175 shared loading-node census), 13 deselected. Restored the existing read/retry contracts in b1d325e; both source counts and the focused DOM scenarios now pass without changing existing tests.
- Added real Chromium fixture test at 1440×900 and 390×844. It loads the exact production component functions and full style.css from checkout, uses controlled GET-only data, checks the nine destinations, active/keyboard focus, geometry/overflow, loading/ready/empty/retry and stale route. This is component-level browser acceptance, not a full integrated World/Place/Inventory run. Browser and full CI results remain pending. Screenshots are generated in pytest temporary storage; never committed.
