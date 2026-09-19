# Спецификация функционала: 016 — Преступность, закон, полиция (§32-35)

## Outcome

Детерминированный контур последствий: NPC могут красть еду при нищете/голоду и vandal'ить при подавленном настроении; свидетели (§34) запоминают и доносят; полиция (§35) разрешает репорты — штраф через ledger или тюрьма с таймером. Всё под флагом `crime.enabled` (**default false** — П2-кейстоун).

## Scope

- **Included**:
  - **Таблица `crimes`** (41-я): id, world_id, crime_type (theft|vandalism), actor_character_id, location_id, target_object_id (nullable), day, status (unreported|reported|resolved), resolution (nullable: fine|prison), created_at.
  - **Колонка `characters.prison_until_day`** (Integer nullable). Schema 0.13.0.
  - **События** (36-40): CRIME_COMMITED, CRIME_REPORTED, FINE_PAID, ARRESTED, RELEASED.
  - **Фаза commit+witness** (daily, после external_followups): theft — NPC: hunger<25 И balance<threshold И на локации есть food-объект → quantity-1, CRIME_COMMITED {type,theft}; vandalism — social<15, P=vandalism_chance → condition-30 случайному объекту локации. Свидетели: живые NPC той же локации (не актёр), каждый замечает с P=detection_base (rng sha256(world,day,actor,i)); свидетель → Memory (importance 8, «Witnessed ...») + если ≥1 свидетель → status=reported + CRIME_REPORTED.
  - **Фаза police resolution** (daily): org type="security" («Полиция Рейнеке», сеется ТОЛЬКО при crime.enabled; аккаунт + starting_balance 5000). Reported-преступления: штраф fine_<type> → если balance ≥ штраф: economy_transfer в аккаунт полиции, FINE_PAID, status=resolved; иначе арест: prison_until_day = day+prison_days, location=локация полиции, ARRESTED {release_day, officer=leader_character_id}. Лидер полиции — org.leader_character_id (первый взрослый NPC при сиддинге; полиц. роль в seed).
  - **Тюрьма**: пока day < prison_until_day: персонаж пинится в локацию полиции, решений не принимает (возврат до utility в _advance_character/progress_tick), декей нужд ×prison_needs_decay_multiplier (0.3 — «кормёжка», упрощение зафиксировано). День освобождения: prison_until_day=None, location=home, RELEASED.
  - **Закон (§32)** — конфиг-санкции (fine/prison в CrimeConfig по типу деяния); отдельной таблицы законов нет (MVP-упрощение зафиксировано).
- **Excluded**: NPC-полицейский как полноценный агент с utility-задачами (резолвер — daily-фаза от имени org, officer в payload); кражи у игроков-персонажей (только stock/shop объекты); суд/материалы дел; побеги; насильственные преступления (П4).
- **Protected boundaries**:
  - П1: штраф — только ledger (economy_transfer FINE_PAID); кража — через quantity-объекты; события — журнал.
  - П2: **кейстоун**: crime.enabled=false → фазы no-op, полиция не сеется, векторы байт-идентичны.
  - П4: насильственных преступлений нет; только имущественные.

## Requirements
- **R1**: 41 таблица (+crimes), schema 0.13.0, 40 событий, пины честно.
- **R2**: theft при голоде+нищете списывает объект, логирует CRIME_COMMITED.
- **R3**: свидетели с P=detection_base дают Memory + CRIME_REPORTED (0 свидетелей → unreported навсегда, дело не заводится).
- **R4**: полиция: достаточно денег → штраф (баланс полиции растёт), иначе арест+тюрьма+RELEASED в день освобождения.
- **R5**: заключённый не принимает решений и пинится в полиции; декей ×0.3.
- **R6**: инвариант crime_integrity: resolved-преступления имеют resolution; штрафы отражены в ledger (FINE_PAID-событие существует для каждой fine-резолюции).

## Non-Functional Requirements
- Performance: daily-фазы O(NPC+crimes); индекс (world_id, status) на crimes.
- Compatibility: schema 0.13.0, события 40.
- Security: n/a.

## Acceptance Evidence
- **AE1**: crime.enabled=false: фазы no-op, полиция не сеется, векторы m2-m4 байт-идентичны.
- **AE2**: голодный бедный NPC крадёт еду: объект уменьшен, CRIME_COMMITED; без свидетелей — unreported.
- **AE3**: свидетель: Memory создана, CRIME_REPORTED, статус reported.
- **AE4**: полиция: платёжеспособный вор — FINE_PAID + баланс полиции; неплатёжеспособный — ARRESTED + пин в полиции + RELEASED в release_day + возврат домой.
- **AE5**: инвариант crime_integrity зелёный.
