# Спецификация функционала: 017 — Электричество (§26)

## Outcome

Дома потребляют электричество; генераторы (строятся через 012-конвейер) дают мощность; шторм (погода 010) может вызвать перебои; дефицит портит еду и бьёт по энергии жильцов. Флаг `electricity.enabled` (**default false** — П2).

## Scope

- **Included**:
  - **Конфиг** `ElectricityConfig`: enabled=false, demand_per_occupant=10, generator_capacity=10, storm_wind_speed=15.0, storm_precipitation=5.0, outage_chance=0.3, food_spoil_condition=25, energy_drain=10.
  - **Чертёж генератора** (012-construction): `construction.costs.generator = {required_items: {wood_pile: 3}, days: 2}` — безусловно (стройка только по явной команде).
  - **Фаза electricity** (daily, после fire): дом (type=house): demand = demand_per_occupant × жильцы; supply = Σ generator.quantity × capacity × (condition/100); шторм (WeatherState дня: wind_speed ≥ storm_wind ИЛИ precipitation ≥ storm_precip) → P=outage_chance → supply=0. Дефицит: событие **POWER_OUTAGE** (41) на локацию; food-объекты дома condition − food_spoil_condition; жильцы energy − energy_drain.
  - Stateless-расчёт (без новых таблиц/колонок) — schema 0.13.0 сохраняется.
- **Excluded**: сети/провода, счётчики, оплата кВт, панели, аккумуляция.
- **Protected boundaries**: П1 (порча через condition, энергия через needs); П2 (кейстоун: no-op при off); П4 n/a.

## Requirements
- **R1**: 41 событие, таблицы 41, schema 0.13.0 — пины событий честно.
- **R2**: генератор строится POST /build.
- **R3**: дефицит → POWER_OUTAGE + порча еды + energy-drain; достаток → тишина.
- **R4**: шторм + outage → supply=0.
- **R5**: enabled=false → no-op.

## Acceptance Evidence
- **AE1**: кейстоун no-op + векторы байт-идентичны.
- **AE2**: дом без генератора с жильцами/едой → POWER_OUTAGE + эффекты.
- **AE3**: достаточная мощность → тишина.
- **AE4**: шторм + outage → POWER_OUTAGE несмотря на генераторы.
- **AE5**: чертёж generator строится через /build.
