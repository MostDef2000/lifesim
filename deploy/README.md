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

### 3.1 Live tick driver (#151) — вместо cron-simulate

Начиная с #151 сервер двигает игровое время сам: lifespan API-рантайма
запускает тик-драйвер (`backend/app/api/ticker.py`) — каждые
`world.live_tick_interval_s` секунд реального времени он делает `Engine.step`
на заработанные `world_clock.time_scale` игровые минуты (is_paused — пауза,
`POST /admin/world/timescale` — скорость). **Внешний cron-`vl1 simulate`
больше не нужен и опасен** (два писателя в одну БД) — не настраивайте его.
Выключатель старого поведения: `world.live_tick_enabled: false` в YAML
(время стоит, как до #151).

## 4. systemd

```bash
sudo cp deploy/lifesim.service /etc/systemd/system/
sudo systemctl daemon-reload
# #112: pre-start check — production refuses to boot without the session
# secret (api.require_secret: true). Проверка-указатель, значение не выводим:
grep -q '^VL1_SECRET=' /opt/lifesim/.env || echo "VL1_SECRET не задан в /opt/lifesim/.env — lifesim не стартует (см. §2)"
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
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/docs   # 404 (#113: docs закрыты в проде)
curl -s http://127.0.0.1:8000/world && echo                          # мир
curl -s https://<домен>/ | head -1                                   # после DNS
curl -s http://127.0.0.1:8000/static/app.js | head -c 100            # статика под /static/
```

UI — на `/`, liveness — на `/health`. Swagger/redoc/openapi в проде закрыты
(#113, `api.docs: false`): `/docs`, `/redoc`, `/openapi.json` → 404, край
(Caddy) режет их тем же кодом.

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

## 9. Обновление (workflow-dispatch — штатный путь)

Штатное обновление прод-сервера — GitHub Actions workflow
`.github/workflows/deploy.yml` (ручной dispatch, адаптация проверенного
паттерна stroy #130). Ручная процедура ниже остаётся как **break-glass**.

Поток:

1. Владелец подтверждает деплой в dev-чате → оркестратор запускает workflow
   (`workflow_dispatch`) с input `deploy_sha` — полным 40-hex SHA коммита,
   уже влитого в `main`. Человеческий гейт — подтверждение в чате (решение
   владельца 2026-10-10), поэтому environment `production` — без protection
   rules.
2. Job `gate`: SHA валиден (40 hex) и является предком `origin/main`
   (`git merge-base --is-ancestor`); через GitHub API выбирается последний
   завершённый успешный `push`-run `ci.yml` для этого SHA, его джобы обязаны
   быть `success` (в `ci.yml` джобы `gate` и `slow`; `slow` на push не
   запускается — `skipped`, это принимается). Любая ошибка API — отказ
   (fail-closed).
3. Job `deploy` (concurrency-группа `lifesim-production`): один SSH-вызов с
   forced command `lifesim-deploy-v1 <sha>`. Обёртка на сервере
   (`/usr/local/sbin/lifesim-deploy-wrapper`, исходник —
   `deploy/deploy-wrapper`): flock single-flight → `git fetch` (origin
   зафиксирован на канонический URL) → проверка, что SHA лежит на
   first-parent-цепочке `origin/main` → запись previous-sha → бэкап SQLite
   (§8) → `git checkout --detach <sha>` → `.venv/bin/pip install -e .
   uvicorn` → `sudo systemctl restart lifesim` → пробы §6 (health с ретраем
   ~60 с, docs 404, static 200). Обёртка работает от пользователя `deploy`
   через ограниченный sudoers — root не предполагается (§9.1).
4. Успех: обёртка печатает `DEPLOYMENT_ACCEPTED sha=<sha> previous=<prev>`;
   job проверяет маркер и кладёт SSH-транскрипт в артефакты; на сервере
   пишется манифест `/opt/lifesim/deploy-state/deployment-manifest.json`
   (sha, timestamp, probes, previous_sha, путь к бэкапу).
5. Неудача после записи previous-sha: обёртка автоматически откатывается на
   previous sha (checkout + pip + restart + пробы) и печатает
   `DEPLOY_ROLLED_BACK sha=... previous=...`; job падает, транскрипт — в
   артефактах.

Ручной откат = повторный dispatch предыдущего SHA (он в манифесте и в строке
`DEPLOYMENT_ACCEPTED`). SHA должен оставаться на first-parent-цепочке `main`
и иметь зелёный push-run `ci.yml` — для штатных деплоев это всегда так;
после force-push истории — только break-glass.

Локальные правки отслеживаемых файлов на сервере (например, §7 правит
`config/production.yaml`) остаются в рабочем дереве и могут заблокировать
`git checkout --detach` на конфликтующем SHA — обёртка тогда безопасно
откатится и job упадёт с транскриптом. Переносите такие правки в репо
коммитом или снимайте их на сервере перед деплоем.

### 9.1 Bootstrap (однократно, Lane B)

Bootstrap выполняется вручную владельцем/сисадмином с root-доступом, один
раз. Обёртка никогда не предполагает root — только эти гранты.

1. **Deploy-пользователь и каталоги:**

   ```bash
   sudo adduser --disabled-password --gecos "lifesim deploy" deploy
   sudo mkdir -p /opt/lifesim/deploy-state /opt/lifesim/backups
   sudo chown deploy:deploy /opt/lifesim/deploy-state
   sudo chown lifesim:lifesim /opt/lifesim/backups
   ```

2. **Обёртка** (копия с root-владельцем — НЕ symlink в чекаут: код обёртки
   не должен меняться из деплоя; при изменении `deploy/deploy-wrapper` в
   репо повторить шаг вручную):

   ```bash
   sudo install -m 0755 -o root -g root \
     /opt/lifesim/deploy/deploy-wrapper /usr/local/sbin/lifesim-deploy-wrapper
   ```

3. **Ключи**: отдельная пара для CI (`ssh-keygen -t ed25519 -f
   lifesim-deploy -C lifesim-deploy@ci`); публичный ключ — в
   `/home/deploy/.ssh/authorized_keys` с forced command (одной строкой):

   ```
   command="/usr/local/sbin/lifesim-deploy-wrapper",no-pty,no-agent-forwarding,no-X11-forwarding,no-port-forwarding ssh-ed25519 AAAA... lifesim-deploy@ci
   ```

4. **Sudoers** — `/etc/sudoers.d/lifesim-deploy` (root:root, 0440; перед
   установкой проверить `visudo -cf <файл>`):

   ```
   Defaults:deploy !requiretty
   deploy ALL=(lifesim) NOPASSWD: /usr/bin/git -C /opt/lifesim *
   deploy ALL=(lifesim) NOPASSWD: /opt/lifesim/.venv/bin/pip install *
   deploy ALL=(lifesim) NOPASSWD: /usr/bin/sqlite3 /opt/lifesim/data/lifesim.db *
   deploy ALL=(root) NOPASSWD: /usr/bin/systemctl restart lifesim
   deploy ALL=(lifesim) NOPASSWD: /usr/bin/rm -f -- /opt/lifesim/backups/pre-deploy-*.db
   ```

5. **origin**: обёртка принимает только канонический URL; проверьте/выставьте

   ```bash
   sudo -u lifesim git -C /opt/lifesim remote get-url origin
   # должно быть: https://github.com/MostDef2000/lifesim.git
   ```

6. **known_hosts для CI** (repo VARIABLE `LIFESIM_SSH_KNOWN_HOSTS`): снимите
   отпечаток на сервере (`ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`),
   из доверенной сессии возьмите строку вида
   `<значение секрета LIFESIM_HOST> ssh-ed25519 AAAA...` и сверьте отпечаток. `ssh-keyscan`
   вслепую запрещён (TOFU) — пиннинг только с проверкой отпечатка.

7. **GitHub secrets/variables** (Settings → Secrets and variables → Actions):

   | Имя | Тип | Значение |
   |---|---|---|
   | `LIFESIM_SSH_PRIVATE_KEY` | secret | приватный ключ деплоя (шаг 3) |
   | `LIFESIM_HOST` | secret | IP прод-сервера (только в GitHub secret, в репо не хранится) |
   | `LIFESIM_USER` | secret | `deploy` |
   | `LIFESIM_SSH_KNOWN_HOSTS` | variable | pinned host key (шаг 6) |

8. **Environment** `production` создаётся GitHub автоматически при первом
   запуске workflow; protection rules не настраиваются — человеческий гейт
   это подтверждение в dev-чате до dispatch (решение владельца 2026-10-10).

**Break-glass** (ручной путь, когда Actions недоступны). После деплоев через
workflow чекаут detached на задеплоенном SHA — сначала вернитесь на ветку:

```bash
cd /opt/lifesim && sudo -u lifesim git checkout main
sudo -u lifesim git pull
sudo -u lifesim .venv/bin/pip install -e .
sudo systemctl restart lifesim
```

## 10. Штатные операции

**Сброс 429 (rate-limit)**: `sudo systemctl restart lifesim`. Бакеты M8
in-memory per-process — рестарт обнуляет их. Безопасно: сессии живут в
HMAC-подписи (VL1_SECRET), а не в памяти; мир возобновляется с последнего
коммита тика (прерывается максимум текущий тик). До появления внешнего
хранилища лимитов это штатный способ.

**Строгий auth-бакет** (после PR #41): только `POST /auth/login` и
`POST /auth/register` (brute-force поверхность). `/auth/me` и `/auth/logout`
идут в глобальный бакет — легитимный перелогин после смены секрета не
блокируется.

## 11. Визуальный канал (worker, туннель, flux/pony)

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

## 12. LLM-канал (домашний ПК, ollama, туннель)

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
