# 028 — Plan (#369)

- В `app.js` appearance-область: `appearanceGameShell` — общий shell для всех состояний (loading/ready/empty/error/unavailable) с одним доком и одним живым часом; `APPEARANCE_DRAFT` — client-only typed-черновик (outfit prompt), без web storage.
- `appearancePreviewGen` — счётчик генераций для preview stale guard; `navEpoch`/session guards — ответ preview из старой генерации/вида не поднимает ghost error и не мутирует ничего.
- Preview: единственный `POST /visual/preview` (единственная appearance site), кнопка disabled в полёте, результат подписан «Предпросмотр — не сохранён», preview никогда не мутирует гардероб/worn state.
- `style.css` — изолированные правила shell перед закреплённым conn-banner tail; глобальные токены #363 не менять.
- Pinned constraints m176 сохраняются: 10 tests, `.appearance-shell` count 6, `mode: "full_body_outfit"` count 1, APPEARANCE_DRAFT client-only.
- Node DOM behavioral test через pytest: `tests/m369/appearance_shell.node.js` + `tests/m369/test_appearance_shell.py`. Извлечение: `function appendKids(` .. `/* ---------- routing`, `const RETRY_HINT` .. `/* ---------- #306`, `function svgIcon(` .. `/* ---------- world view`, `function gameSectionNav(` .. `/* #289`, `function weatherLabel(` .. `// #313`, `function inventoryContextText(` .. `function refreshInventoryContext(`, `/* ---------- #65:` .. `/* ---------- #173`. Команды верификации: `node --check tests/m369/appearance_shell.node.js`; `node tests/m369/appearance_shell.node.js`; `uv run pytest tests/m369 -q`; `uv run pytest tests/m176 tests/m367 tests/m368 tests/m369 -q`; `git diff --check`; `bash scripts/check.sh` (перезапуск оркестратором).
- Затем полный quality gate: `bash scripts/check.sh` (перезапуск оркестратором).

Одна ветка `feat/369-appearance-shell` и bounded draft PR #369. Без merge/deploy до подтверждения gates.
