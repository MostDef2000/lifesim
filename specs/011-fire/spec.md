# Спецификация функционала: 011 — Fire (§72)

## Outcome

Объекты мира могут гореть: вероятностная модель (§72 — «физическая симуляция не нужна») — у объектов есть `flammability`, `burn_state`; горящий объект распространяет огонь на соседние горючие объекты локации, наносит вред находящимся рядом персонажам и уничтожается (dies). Первый emergent gameplay по спеке.

## Scope

- **Included**:
  - Поля у `world_objects`: `flammability` (Float 0-1, default по типу объекта из конфига), `burn_state` (String: intact|burning|burned, default intact). Schema 0.9.0 (колонки, не таблица — 38 остаётся).
  - `FireConfig`: enabled=true, ignition_chance (за тик-день для объектов с flammability≥high_threshold при контакте с горящим), spread_radius (MVP: та же локация), damage_per_day (burning объект наносит health-урон персонажам на локации), burnout_days (срок сгорания → burned; burned объект изымается из инвентаря-использования, ITEM_DESTROYED-событие).
  - Возгорание: (1) событие-триггер из мира (MVP: вероятностная самовоспламеняемость `spontaneous_chance_per_day` для flammability≥0.7 на открытых локациях типа outdoor при сухой жаркой погоде (§71-связка: temperature>28, precipitation=0, source row дня)); (2) API `POST /admin/fire/ignite` (admin/moderator, аудит §86) — ручной поджог для геймплея/тестов.
  - Распространение: раз в игровой день (дневная фаза движка) — для каждой локации с горящим объектом: соседние горючие объекты воспламеняются с P=ignition_chance×flammability; персонажи на локации получают урон damage_per_day (health-фаза §M1-механика, событие HEALTH_CHANGED уже есть); burn_state burning→burned по истечении burnout_days.
  - Событие **OBJECT_BURNING** (29) и **OBJECT_BURNED** (30) в журнал.
  - Инвариант `fire_integrity`: burn_state в {intact,burning,burned}; burned объекты не участвуют в действиях (проверка целостности inventory-ссылок).
  - API: `GET /fire/active` (auth) — список горящих объектов (location, days_burning).
- **Excluded**: физика (тепло/ветер-диффузия, §72 «не нужна»); тушение игроками (MVP: пожары выгорают сами); поджог игроками через действия; эффекты на строениях §75; погодное распространение между локациями.
- **Protected boundaries**:
  - П1: урон — через штатную health-механику; уничтожение — через штатное изъятие (inventory/event), без прямых мутаций.
  - П2: **кейстоун**: enabled=false (default для headless-мир до включения? — нет: enabled=true, но spontaneous_chance=0.0 в default → детерминизм сохраняется, пожары только через admin-ignite) — при отсутствии burn_state!=intact объектов отчёты байт-идентичны прежним.
  - П4: n/a.

## Requirements

- **R1**: колонки flammability/burn_state у world_objects; события 29/30.
- **R2**: вероятностная модель детерминирована от seed (rng потока дня), не от wall-clock.
- **R3**: погодная связка: самовоспламенение только при (temperature>28 AND precipitation==0) дня (§71→§72 интеграция).
- **R4**: распространение/урон/выгорание — дневная фаза `fire` движка; idempotent per day.
- **R5**: burned объекты: is_active=false, из инвентарей удаляются (ITEM_DESTROYED), дескриптор сцены их не показывает.
- **R6**: admin-ignite пишет audit (§86), работает на любом объекте.
- **R7**: GET /fire/active: [{object_id, location_id, type, days_burning}].
- **R8**: инвариант fire_integrity зелёный.

## Non-Functional Requirements
- **Performance**: дневной проход O(объектов горящих локаций).
- **Compatibility**: schema 0.9.0 (колонки), события 30, структурные пины обновить (38 таблиц — не меняется; события 28→30 в закрытых сетах).
- **Security**: admin-ignite за require_role admin/moderator.

## Acceptance Evidence
- **AE1**: admin-ignite → OBJECT_BURNING, через burnout_days → OBJECT_BURNED, объект is_active=false, инвентарь очищен.
- **AE2**: распространение: 2 горючих объекта на одной локации → за ≤ignition-дней второй загорается (детерминированный rng).
- **AE3**: урон: персонаж на локации горящего объекта получает health-урон за день.
- **AE4**: погодная связка: spontaneous при (t>28, rain=0) — детерминированно случается на тестовом seed; при дожде — не случается.
- **AE5**: кейстоун: без пожаров (default) отчёт M1-M4 байт-идентичен (events_by_type без 29/30 при отсутствии пожаров... — точнее: типы 29/30 существуют в enum (31 тип), но в векторах headless отсутствуют — проверка векторов неизменна).
- **AE6**: API: /fire/active пуст → после ignite содержит объект; 401/403 не-админ.
