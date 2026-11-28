# 026 — Plan (#367)

- В `app.js` вынести общий `gameSectionNav(active)` из World, применить к пилотному Inventory shell; сохранить PILL_TABS и legacy topbar остальных экранов.
- Использовать существующие #306 game tokens, #269 clock bootstrap, #304 task adapter и существующий poll для контекста; read-only helpers, scoped guards.
- В `style.css` добавить изолированные правила перед закреплённым conn-banner tail. Grid со скроллируемой областью и dock вне scroll area.
- Сохранить inventory renderer и действия; не менять маршруты/сеть вне stale guard.
- Добавить Node DOM behavioral test через pytest и static scope pins; затем реальный desktop/mobile browser smoke и полный quality gate.

Одна ветка и bounded draft PR #367. Без merge/deploy до подтверждения gates.
