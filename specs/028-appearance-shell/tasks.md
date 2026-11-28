# 028 — Tasks (#369)

- [x] Проверить current main и baseline contracts #363/#372/#396/#397.
- [x] Общий shell `appearanceGameShell`: loading/ready/empty/error/unavailable внутри shell, один док, один живой час, legacy topbar не рендерится.
- [x] Навигация: девять dock destinations, один активный (`#/appearance`), «На карту» → `#/world`, только именованные ссылки.
- [x] Hero-контекст: имя, локация, баланс переживают перенос в shell.
- [x] Admin gate: `#/admin` ровно один раз для admin, нет для player.
- [x] Честные состояния: «Образ недоступен» (не «Ничего не надето»), «Гардероб пуст — загляните на », «Повторить» внутри shell.
- [x] Typed-черновик `APPEARANCE_DRAFT`: outfit prompt переживает navigation re-render, client-only (без web storage).
- [x] Preview: единственный `POST /visual/preview`, кнопка disabled в полёте, повторный клик — no-op, подпись «Предпросмотр — не сохранён», никогда не мутирует гардероб/worn state.
- [x] Stale guard: `appearancePreviewGen` + `navEpoch`/session guards — stale preview-ответ не поднимает ghost error и не мутирует ничего.
- [x] Pinned constraints m176 сохранены: 10 tests, `.appearance-shell` count 6, `mode: "full_body_outfit"` count 1, APPEARANCE_DRAFT client-only.
- [x] Node DOM behavioral test через pytest: `tests/m369/appearance_shell.node.js` + `tests/m369/test_appearance_shell.py` (PASS).
- [x] Spec-артефакты `specs/028-appearance-shell/{spec,plan,tasks}.md` (goal/scope/non-goals, Gherkin, canon refs #372/#363/#396/#397, pins и verification commands).
- [ ] Полный quality gate (`bash scripts/check.sh`) — перезапуск оркестратором.

## Evidence

- m176: 16 passed (pinned constraints: `.appearance-shell` count 6, `mode: "full_body_outfit"` count 1, APPEARANCE_DRAFT client-only).
- Node harness: `PASS: shell states, navigation, draft survival, preview isolation, stale guard, retry`.
- `uv run pytest tests/m369 -q` → 1 passed; `uv run pytest tests/m176 tests/m367 tests/m368 tests/m369 -q` → 20 passed, 2 skipped (Chromium-skip, m367).
- `node --check tests/m369/appearance_shell.node.js` → OK; `git diff --check` → OK.
- Полный gate будет перезапущен оркестратором.
