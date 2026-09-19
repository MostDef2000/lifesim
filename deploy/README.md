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
chmod 600 /opt/lifesim/.env  # владелец lifesim
```

## 3. systemd

```bash
sudo cp deploy/lifesim.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now lifesim
systemctl status lifesim
```

## 4. Caddy

```bash
sudo apt install caddy
# отредактируйте deploy/Caddyfile: замените example.com на ваш домен
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

WS (websockets) проксируется автоматически.

## 5. Проверка

```bash
curl -s http://127.0.0.1:8000/health && echo
curl -s http://127.0.0.1:8000/docs | head -1
curl -s https://<домен>/ | head -1   # после настройки DNS
```

UI — на `/`, OpenAPI — на `/docs`.

## 6. Smoke-тест реальной погоды (опционально, Рейнеке)

По умолчанию погода детерминированная (`weather__source=synthetic`). Для реальной погоды острова Рейнеке (42.98N, 132.55E) год назад:

```bash
echo "weather__source=historical" >> /opt/lifesim/.env
sudo systemctl restart lifesim
curl -s -H "Authorization: Bearer <token>" http://127.0.0.1:8000/weather
# ожидание: «(реальная YYYY-MM-DD)» в шапке UI; кэш-файлы в data/weather_cache/
```

Офлайн/сбой Open-Meteo → автоматический фолбэк в synthetic (в логе — warning).

## 7. Бэкап SQLite

```bash
sudo -u lifesim sqlite3 /opt/lifesim/data/lifesim.db ".backup '/opt/lifesim/backups/$(date +%F).db'"
```

Крон-строка (пример, 03:15 ежедневно): `15 3 * * * sqlite3 /opt/lifesim/data/lifesim.db ".backup /opt/lifesim/backups/\$(date +\%F).db"`

## 8. Обновление

```bash
cd /opt/lifesim && sudo -u lifesim git pull
sudo -u lifesim .venv/bin/pip install -e .
sudo systemctl restart lifesim
```

## П4-напоминание

Регистрация в бэкенде требует 18+ и `age_confirmed` — не отключайте.
