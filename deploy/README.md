# Runbook: развёртывание «ВЛ1: Рейнеке» (015-deploy-kit)

По директиве владельца этот kit — **файлы в репо**. Реальный сервер/домен/LLM не настраиваются в рамках issue #19.

## 1. Установка (VPS, Debian/Ubuntu)

```bash
sudo adduser --system --group --home /opt/lifesim lifesim
sudo -u lifesim git clone <repo-url> /opt/lifesim
cd /opt/lifesim
sudo -u lifesim python3 -m venv .venv
sudo -u lifesim .venv/bin/pip install -e . uvicorn
```

## 2. .env

```bash
cp deploy/.env.example /opt/lifesim/.env
sudo -u lifesim sh -c 'echo "VL1_SECRET=$(openssl rand -hex 32)" >> /opt/lifesim/.env'
sed -i 's|^LIFESIM_CONFIG=.*|LIFESIM_CONFIG=config/production.yaml|' /opt/lifesim/.env
chmod 600 /opt/lifesim/.env  # владелец lifesim
```

`LIFESIM_CONFIG` должен указывать на `config/production.yaml`: `load_config` читает
только YAML — переменные вида `api__enabled` кодом не оцениваются (см. комментарий
в шапке config/production.yaml).

## 3. Инициализация мира (один раз, до старта сервиса)

`vl1 serve`/`app.run` требуют существующий мир (AE6'''): БД создаёт только `vl1 simulate`.

```bash
cd /opt/lifesim
sudo -u lifesim .venv/bin/vl1 simulate --days 0 --population 20 --seed 42 --config config/production.yaml
# ожидание: JSON-отчёт с "invariants_ok": true; БД — data/lifesim.db
```

## 4. systemd

```bash
sudo cp deploy/lifesim.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now lifesim
systemctl status lifesim
```

## 5. Caddy

```bash
sudo apt install caddy
# отредактируйте deploy/Caddyfile: замените example.com на ваш домен
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

WS (websockets) проксируется автоматически. Сжатие — директива `encode gzip`
внутри site-блока (глобальная опция `gzip` в Caddy 2.6 не существует).

## 6. Проверка

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/docs   # 200
curl -s http://127.0.0.1:8000/world && echo                          # мир
curl -s https://<домен>/ | head -1                                   # после DNS
```

UI — на `/`, OpenAPI — на `/docs`. Эндпоинт `/health` в приложении пока не
реализован (включён в бэклог) — проверять по `/docs` и `/world`.

## 7. Smoke-тест реальной погоды (опционально, Рейнеке)

По умолчанию погода детерминированная (`weather.source: synthetic`). Для реальной
погоды острова Рейнеке (42.98N, 132.55E) год назад отредактируйте
`config/production.yaml` (`weather.source: historical`) и перезапустите сервис:

```bash
sudo -u lifesim sed -i 's/^  source: synthetic/  source: historical/' /opt/lifesim/config/production.yaml
sudo systemctl restart lifesim
curl -s -H "Authorization: Bearer <token>" http://127.0.0.1:8000/weather
# ожидание: «(реальная YYYY-MM-DD)» в шапке UI; кэш-файлы в data/weather_cache/
```

Офлайн/сбой Open-Meteo → автоматический фолбэк в synthetic (в логе — warning).
Переменная `weather__source` из .env кодом не читается — менять только в YAML.

## 8. Бэкап SQLite

```bash
sudo -u lifesim sqlite3 /opt/lifesim/data/lifesim.db ".backup '/opt/lifesim/backups/$(date +%F).db'"
```

Крон-строка (пример, 03:15 ежедневно): `15 3 * * * sqlite3 /opt/lifesim/data/lifesim.db ".backup /opt/lifesim/backups/\$(date +\%F).db"`

## 9. Обновление

```bash
cd /opt/lifesim && sudo -u lifesim git pull
sudo -u lifesim .venv/bin/pip install -e .
sudo systemctl restart lifesim
```

## П4-напоминание

Регистрация в бэкенде требует 18+ и `age_confirmed` — не отключайте.
