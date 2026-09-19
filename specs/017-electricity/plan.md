# План имплементации: 017-electricity

## Архитектура
- config: ElectricityConfig (+default.yaml), construction.costs += generator.
- app/simulation/electricity.py: run_electricity_phase.
- engine: фаза после fire, до crime. events: POWER_OUTAGE = 41.
- Пины: события 40→41.

## Чанки
| Chunk | Содержание | Тесты |
|---|---|---|
| A | config+blueprint+фаза+событие | AE1-AE5 |
| B | пины, README, tick, suite | AE1 |
