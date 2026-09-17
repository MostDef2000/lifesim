# Задачи: 006-flux

## Chunk A — Фундамент: схема, transports, descriptor, store

- [x] T1: schema 0.6.0: таблица visual_assets (§68: scene_descriptor JSON, canonical, storage_path, seed, model, lora...); tests/db 32→33.
- [x] T2: событие VISUAL_ASSET_CREATED (24→25) + tests/m2/m4 счётчики.
- [x] T3: конфиг `visual:` (enabled false, transport stub, base_url, model, lora, storage_dir, image_size, timeout_sec, default_weather) + default.yaml.
- [x] T4: app/visual/transports.py: FluxTransport протокол, StubFluxTransport (детерминированный PNG из seed+sha256(prompt)), HttpFluxTransport (POST /generate), build_transport.
- [x] T5: app/visual/descriptor.py: build_scene_descriptor (R2, детерминизм), build_portrait_descriptor, build_prompt (R3).
- [x] T6: app/visual/store.py: save/read bytes, path sanitize, ensure storage_dir.
- [x] T7: tests/m6/test_chunk_a.py: stub-детерминизм (равные входы → равные байты; разные seed → разные), PNG-магия, descriptor-состав/порядок, prompt-детерминизм, store roundtrip+traversal, http-контракт (monkeypatch).

## Chunk B — API: портреты, сцены, канон, чтение

- [x] T8: роуты /visual/portraits/{cid} (R5: canonical-reuse → reused:true без генерации; repeat-генерации с новым seed), 503-гейт, ownership.
- [x] T9: роут /visual/scenes (R6: descriptor + references из canonical-портретов), /visual/assets/{id}/canonical (R7).
- [x] T10: чтение: /visual/assets/{id}, /visual/assets/{id}/file (PNG), /visual/characters/{cid}/portrait.
- [x] T11: tests/m6/test_chunk_b.py: полный цикл портрета (201→201→promote→200 reused), сцена с references, файл/magic, 503/401/403/404/422.

## Chunk C — AE-свидетельства, документация, финал

- [x] T12: tests/m6/test_chunk_d.py: AE1'''' E2E; AE2'''' keystone (headless-идентичность, пустая visual_assets); AE3'''' детерминизм/воспроизводимость; AE4'''' персистентность (новое приложение на той же БД+storage); AE5'''' reuse без вызова transport (счётчик); AE6'''' config (http transport контракт, 502 при недоступности, 503 при disabled).
- [x] T13: инвариант visual_asset_integrity (R10) в run_invariant_checks + негатив-тест.
- [x] T14: README-инструкция: включение visual, запуск с реальным Flux (base_url/model/lora), границы MVP.
- [x] T15: tasks tick, ruff + check.sh + полный suite, PR.

## Эмпирические пороги

- AE1'''': полный сценарий на stub за < 10с wall.
- AE2'''': events_by_type идентичны пинам M1-M4; visual_assets пуст в headless.
- AE3'''': sha256(stub-байтов) равен для равных (prompt, seed).
