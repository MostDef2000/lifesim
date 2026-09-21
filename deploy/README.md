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
# Секрет сессий генерируется ТОЛЬКО на сервере и вписывается вручную:
sudo -u lifesim openssl rand -hex 32    # вывод — 64 hex-символа
sudo -u lifesim nano /opt/lifesim/.env  # строка VL1_SECRET= -> 64 hex
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
curl -s http://127.0.0.1:8000/health && echo                         # {"status":"ok"}
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/docs   # 200
curl -s http://127.0.0.1:8000/world && echo                          # мир
curl -s https://<домен>/ | head -1                                   # после DNS
```

UI — на `/`, OpenAPI — на `/docs`, liveness — на `/health`.

## 7. Smoke-тест реальной погоды (опционально, Рейнеке)

По умолчанию погода детерминированная (`weather.source: synthetic`). Для реальной
погоды острова Рейнеке (42.98N, 132.55E) год назад отредактируйте
`config/production.yaml` (`weather.source: historical`) и перезапустите сервис:

```bash
sudo -u lifesim sed -i 's/^  source: synthetic/  source: historical/' /opt/lifesim/config/production.yaml
sudo systemctl restart lifesim
curl -s -H "Authorization: Bearer <token>" http://127.0.0.1:8000/weather

ВНИМАНИЕ: `get_or_create_weather` идемпотентен — уже созданные дни НЕ
перегенерируются при смене источника. Очистите погоду перед переключением
(мир не страдает — погода детерминирована от даты):

```bash
sudo -u lifesim .venv/bin/python -c "import sqlite3; c = sqlite3.connect('/opt/lifesim/data/lifesim.db'); c.execute('DELETE FROM weather_state'); c.commit()"
sudo systemctl restart lifesim
```
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

## 8. Штатные операции

**Сброс 429 (rate-limit)**: `sudo systemctl restart lifesim`. Бакеты M8
in-memory per-process — рестарт обнуляет их. Безопасно: сессии живут в
HMAC-подписи (VL1_SECRET), а не в памяти; мир возобновляется с последнего
коммита тика (прерывается максимум текущий тик). До появления внешнего
хранилища лимитов это штатный способ.

**Строгий auth-бакет** (после PR #41): только `POST /auth/login` и
`POST /auth/register` (brute-force поверхность). `/auth/me` и `/auth/logout`
идут в глобальный бакет — легитимный перелогин после смены секрета не
блокируется.

## 10. Визуальный канал (worker, туннель, flux/pony)

Фаза 6 (issue #39). Цепь: `API (VPS) → 127.0.0.1:7860 (reverse-туннель)
→ адаптер (GPU-воркер sea-speed-worker) → ComfyUI :8188 → PNG`.

- Адаптер: `deploy/comfy_adapter.py` (stdlib, в репо; на воркере
  `~/lifesim/deploy/comfy_adapter.py`, sha256 сверять при обновлении).
  Контракт — `POST /generate {prompt, seed, size, model, lora}` → PNG.
  Модельный роутинг: `flux` → flux1-dev-fp8.safetensors,
  `pony` → ponyDiffusionV6XL.safetensors; pony получает booru-теги
  (score_9/8/7) в адаптере. LoRA safe-list: только
  `flux1-uncensored.safetensors`.
- Туннель: user-level юнит `lifesim-tunnel-flux` на воркере
  (ssh -R 127.0.0.1:7860 → VPS :8443, ключ tunnel_flux,
  `restrict,permitlisten` в authorized_keys VPS). Юниты:
  `lifesim-comfy-adapter`, `lifesim-tunnel-flux`
  (`systemctl --user`, linger включён).
- **Первичная модель: `flux` + `flux1-uncensored`** (реализм, решение
  владельца). Замер 512×512 (09.2026): warm 16–29 с, **VRAM 93.5%** —
  впритык. Рабочий размер приложения — 512×512 (`image_size`);
  для 768+ использовать pony.
- **Сбой → pony одной строкой** (коммит в репо, НЕ ручная правка на
  сервере): `visual.model: pony`, `visual.lora: ""`. Pony: ~3 с, низкий
  VRAM. Триггеры: 502/OOM на генерациях, деградация VRAM.
- **YOLO (sea-speed-worker-control)**: при текущем профиле (~162 MiB)
  трогать не нужно. При VRAM-конфликте — `systemctl stop
  sea-speed-worker-control` (root = владелец), после потока
  генераций — `start`.
- **Семантика переиспользования (§70)**: переиспользуется ТОЛЬКО
  canonical-портрет (`POST /visual/assets/{id}/canonical`). Без canonical
  каждый `POST /visual/portraits/{cid}` — новая платная генерация.
  Клиент обязан пинить canonical после первой генерации.

## 11. LLM-канал (домашний ПК, ollama, туннель)

Фаза 7 (issue #47). Цепь: `API (VPS, llm.transport=ollama, base_url
localhost:11434) → reverse-туннель (home-pc:11434 → VPS:11434, ключ
tunnel_llm, restrict,port-forwarding,permitlisten) → ollama (Windows,
qwen3:14b Q4_K_M ~9.3GB на G:\ollama\models, 4070 Ti 12GB)`.

- **Туннель поднимается вручную** (решение владельца): клик
  `C:\Users\mostd\lifesim\tunnel_llm.bat` (ssh -N -R, луп с
  перезапуском через 10с). Автозагрузки нет.
- **Перед сессией с LLM проверить**: на VPS
  `curl -s http://127.0.0.1:11434/api/tags` → qwen3:14b в списке.
- **Туннель down = LLM-функции не работают** (диалоги без ответа, AI-фаза
  пропускает запросы), мир не повреждается (гейт R1, бюджеты max_per_day 40).
- Контекст: `OLLAMA_CONTEXT_LENGTH=8192` (User-переменная на ПК).
- Live smoke 21.09.2026: реплика диалога 14.2с, timeout 120с, без
  `<thinking>` (format=json — грамматическая заграда).
- Диагностика падения: (1) ollama на ПК (tray, `ollama list`),
  (2) окно tunnel_llm.bat (нет ли Permission denied),
  (3) `curl 127.0.0.1:11434/api/tags` с VPS.
- Смена модели/кванта — только `ollama pull <модель>` + значение
  `model_tier2` в production.yaml коммитом в репо (не ручная правка).

## П4-напоминание

Регистрация в бэкенде требует 18+ и `age_confirmed` — не отключайте.
