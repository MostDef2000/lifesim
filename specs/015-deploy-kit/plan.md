# План имплементации: 015-deploy-kit

## Архитектура
- Только файлы + тесты. Backend не меняется; schema/events не меняются (пины прежние).
- Согласованность путей: /opt/lifesim в юните и runbook; 127.0.0.1:8000 в юните и Caddyfile.
- uvicorn factory-строка: `app.api.app:create_app` — проверить, что uvicorn принимает factory (create_app без аргументов? — factory с параметрами не годится: нужен обёрточный модуль `app.run:app` с settings из env). Решение: добавить минимальный `backend/app/run.py` (create_app() с load_config(env LIFESIM_CONFIG || config/default.yaml)) и ExecStart на него.

## Чанки
| Chunk | Содержание | Тесты |
|---|---|---|
| A | deploy/* 4 файла + app/run.py | AE1-AE2 |
| B | tests/m15 + README M15 + tick | AE3 |

## Риски
- create_app требует settings — run.py собирает из load_config; секрет из env (get_secret уже читает env/файл).
