# Спецификация функционала: 015 — Deploy-kit (файлы в репо, БЕЗ реального деплоя)

## Outcome

Готовый комплект развёртывания «ВЛ1: Рейнеке» на VPS: systemd-юнит, Caddy-конфиг, .env-шаблон, runbook. По директиве владельца (issue #19): **реальный сервер, домен и LLM-подключение НЕ трогаем** — только файлы в репо и структурные тесты.

## Scope

- **Included**:
  - `deploy/lifesim.service`: systemd-юнит — `uvicorn app.api.app:create_app` (factory), WorkingDirectory=/opt/lifesim, EnvironmentFile=/opt/lifesim/.env, Restart=on-failure, User=lifesim, port 8000.
  - `deploy/Caddyfile`: reverse proxy 80/443 → 127.0.0.1:8000, gzip, статика без кэша-агрессии; WS (websockets) проксируется автоматически.
  - `deploy/.env.example`: LIFESIM_SECRET_KEY=changeme, LIFESIM_DB=data/lifesim.db, weather.source=synthetic (исторический — команда переключения в runbook), visual.enabled=false, llm.enabled=false, external.npc_utility=false, external.supply_demand=false, fire.spontaneous_chance_per_day=0.0.
  - `deploy/README.md` (runbook): установка uv+deps, создание .env, systemd enable/start, Caddy, проверка /health и /docs, smoke-тест погоды (переключение на historical + кэш), бэкап SQLite (sqlite3 .backup), обновление (git pull + systemctl restart).
  - Структурные тесты tests/m15: файлы существуют, юнит содержит required-ключи (ExecStart/EnvironmentFile/Restart), Caddyfile содержит reverse_proxy 127.0.0.1:8000, .env.example содержит все флаги-кейстоуны с П2-безопасными значениями.
- **Excluded**: реальное выполнение на сервере (sysadmin-хендофф отдельно), домен/HTTPS-сертификаты, Docker, CI-деплой, миграционные скрипты (schema bootstrap самодостаточен).
- **Protected boundaries**:
  - П1/П2: .env.example фиксирует все экспериментальные флаги выключенными — деплой по умолчанию воспроизводит headless-поведение.
  - П4: runbook напоминает про П4 (18+ регистрация уже в бэкенде).

## Requirements
- **R1**: все 4 файла в deploy/, непустые, с обязательными конструкциями.
- **R2**: .env.example — только placeholder-секреты (никаких реальных значений).
- **R3**: runbook покрывает: install, .env, systemd, caddy, health-check, weather smoke, backup, update.

## Non-Functional Requirements
- Security: placeholder `changeme` + предупреждение; юнит от непривилегированного юзера; Caddy — единственный публичный порт.
- Compatibility: путь /opt/lifesim зафиксирован в обоих файлах согласованно.

## Acceptance Evidence
- **AE1**: tests/m15 проверяют наличие и содержимое всех 4 файлов (ключи юнита, proxy-адрес, флаги .env, разделы runbook).
- **AE2**: grep по deploy/.env.example не находит реальных секретов (только changeme).
- **AE3**: README-раздел M15.
