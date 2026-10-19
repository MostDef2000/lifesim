# 020 — tasks

## Волна 1 — P1 (креатор + биография-теги)

- [x] Экран `#/create` заменяет короткую форму `viewCreateCharacter`
      (визуально выраженный onboarding-блок).
- [x] Structured appearance form: 12 полей + free-text prompt (P1 —
      сохранение free-text в `looks`, structured ждёт P2-миграции).
- [x] Biography tag cloud: нейтральный список ингредиентов, клик добавляет
      ingredient, draft собирается шаблоном, полностью редактируем.
- [x] Теги не обязательны, свободный текст first-class.
- [x] Тест-контракт: тег-список без гендерных стереотипов.
- [x] Пин-дисциплина: 0 запрещённых литералов; честные bump'ы с трейлами.
- [x] Полный not-slow зелёный + peer review + runtime-скрин.

## Волна 2 — P2 (preview + canonical appearance)

- [ ] Миграция `character_appearances` (1:1, owner-scope).
- [ ] `POST /visual/preview`: auth-only, rate-limit, без sim-state записей.
- [ ] Резолвер: appearance > trait-деривация; без appearance — байт-равно
      прежнему канону (m119-регресс).
- [x] Подтверждение → canonical appearance persist; портреты/сцены (#191)
      используют его.
- [x] Preview rate-limit (VRAM 12GB).
- [x] Тесты: endpoint-контракт, no-sim-write (RED-proven), приоритет
      резолвера, обратная совместимость.

## Волна 3 — P3 (reference upload)

- [ ] Мини-спек storage/retention/privacy (решение владельца).
- [ ] Реализация после отдельного approval.

## Вне скоупа (тикет #118)

Sim-математика, traits/stats, NPC, диалоги, Goal/Desire, landing, concierge
cat, React/npm, деплой-инфраструктура, смена LLM/Flux модели.
