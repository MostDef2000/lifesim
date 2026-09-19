import json
from typing import Any, Dict, List

from sqlalchemy import Float, func
from sqlalchemy.orm import Session

from app.db.models import (
    Account,
    Character,
    CharacterHealth,
    CharacterNeeds,
    CharacterTask,
    Location,
    Organization,
    OrganizationMember,
    OrgLaw,
    OrgLawViolation,
    Relationship,
    RelationshipEvent,
    ResourceBalance,
    Transaction,
    User,
    WorldEvent,
    WorldObject,
)


def run_invariant_checks(session: Session, world_id: str, settings) -> List[Dict[str, Any]]:


    """
    Runs the set of invariants defined in spec §118.
    Returns a list of results: {name, ok, details}.
    """
    results = []

    # a) no_negative_balances: min(Account.balance) >= 0
    min_balance = (
        session.query(func.min(Account.balance)).filter(Account.world_id == world_id).scalar()
    )
    ok_bal = min_balance is None or min_balance >= settings.invariants.min_balance
    results.append(
        {"name": "no_negative_balances", "ok": ok_bal, "details": f"min_balance: {min_balance}"}
    )

    # b) object_conservation: full per-type replay.
    # Every object-creation source is config-accounted:
    #   - seed stock: kitchen_stock + shop_stock (seed_world)
    #   - daily supply: kitchen_stock + shop_stock per day (daily handlers)
    #   - per-character initial items: settings.generation.initial_items
    # Consumption emits ITEM_CONSUMED with {object_type, quantity}.
    # Daily supplies are created via create_object (no per-item events), so
    # the day count comes from the water SUPPLY_ARRIVED day markers.
    days_elapsed = (
        session.query(func.count(func.distinct(WorldEvent.game_timestamp)))
        .filter(
            WorldEvent.world_id == world_id,
            WorldEvent.event_type == "SUPPLY_ARRIVED",
            func.json_extract(WorldEvent.payload, '$.resource_key') == "water",
        )
        .scalar() or 0
    )
    char_count = (
        session.query(func.count(Character.id))
        .filter(Character.world_id == world_id)
        .scalar() or 0
    )
    initial_items_per_char = list(getattr(settings.generation, "initial_items", []))
    daily_stock = dict(settings.economy.kitchen_stock)
    for item_type, qty in settings.economy.shop_stock.items():
        daily_stock[item_type] = daily_stock.get(item_type, 0) + qty

    type_rows = (
        session.query(WorldObject.object_type, func.sum(WorldObject.quantity))
        .filter(WorldObject.world_id == world_id)
        .group_by(WorldObject.object_type)
        .all()
    )
    consumed_rows = (
        session.query(
            func.json_extract(WorldEvent.payload, '$.object_type'),
            func.sum(func.cast(func.json_extract(WorldEvent.payload, '$.quantity'), Float)),
        )
        .filter(
            WorldEvent.world_id == world_id,
            WorldEvent.event_type == "ITEM_CONSUMED",
        )
        .group_by(func.json_extract(WorldEvent.payload, '$.object_type'))
        .all()
    )
    consumed_by_type = {otype: float(qty or 0) for otype, qty in consumed_rows}

    # M7: external purchases import new objects (TRAVEL_EXTERNAL_RETURNED
    # payload.items {item_type: qty}) — accounted as external inflow.
    external_rows = (
        session.query(
            WorldEvent.payload,
        )
        .filter(
            WorldEvent.world_id == world_id,
            WorldEvent.event_type == "TRAVEL_EXTERNAL_RETURNED",
        )
        .all()
    )
    external_bought: dict = {}
    import json as _json

    for (payload_json,) in external_rows:
        payload = _json.loads(payload_json) if payload_json else {}
        for item_type, qty in (payload.get("items") or {}).items():
            external_bought[item_type] = external_bought.get(item_type, 0) + qty

    conservation_violations = []
    for otype, actual in type_rows:
        expected = (
            daily_stock.get(otype, 0)  # seed stock + per-day stock
            + days_elapsed * daily_stock.get(otype, 0)
            + char_count * initial_items_per_char.count(otype)
            + external_bought.get(otype, 0)  # M7 external inflow
            - consumed_by_type.get(otype, 0.0)
        )
        # float tolerance for REAL-typed quantities
        if abs(float(actual) - float(expected)) > 1e-6:
            conservation_violations.append(
                f"{otype}: actual={actual} expected={expected}"
            )
    # Types never seeded but present (unaccounted creation) are covered above:
    # expected=0, actual>0 -> violation.
    ok_obj = not conservation_violations
    results.append(
        {
            "name": "object_conservation",
            "ok": ok_obj,
            "details": (
                f"days={days_elapsed}, chars={char_count}, violations: "
                + (", ".join(conservation_violations) if conservation_violations else "none")
            ),
        }
    )

    # c) water_conservation:
    # Variant: one-sided check (current_balance <= initial + Σ_supply).
    # Water is consumed by DRINK (no event) but added by SUPPLY_ARRIVED.
    current_water = (
        session.query(func.sum(ResourceBalance.quantity))
        .filter(ResourceBalance.world_id == world_id, ResourceBalance.resource_key == "water")
        .scalar() or 0.0
    )
    water_initial = settings.economy.water_initial_quantity
    water_supply = (
        session.query(func.sum(func.cast(func.json_extract(WorldEvent.payload, '$.amount'), Float)))
        .filter(
            WorldEvent.world_id == world_id,
            WorldEvent.event_type == "SUPPLY_ARRIVED",
            func.json_extract(WorldEvent.payload, '$.resource_key') == "water",
        )
        .scalar() or 0.0
    )
    ok_res = current_water >= 0 and current_water <= (water_initial + water_supply)
    results.append(
        {
            "name": "water_conservation",
            "ok": ok_res,
            "details": (
                f"current: {current_water}, initial: {water_initial}, "
                f"supply: {water_supply}"
            ),
        }
    )

    # d) no_stuck_tasks: no task exceeds max_duration or ends_at is in the past.
    latest_timestamp = (
        session.query(func.max(WorldEvent.game_timestamp))
        .filter(WorldEvent.world_id == world_id)
        .scalar()
        or 0
    )
    # Fallback to world_clock if no events
    if latest_timestamp == 0:
        from app.db.models import WorldClock
        latest_timestamp = (
            session.query(WorldClock.game_timestamp)
            .filter_by(world_id=world_id)
            .scalar() or 0
        )

    stuck_tasks = []
    active_tasks = (
        session.query(CharacterTask)
        .join(Character, CharacterTask.character_id == Character.id)
        .filter(Character.world_id == world_id, CharacterTask.status == "active")
        .all()
    )

    for task in active_tasks:
        # Check ends_at
        if task.ends_at is not None and task.ends_at < latest_timestamp:
            stuck_tasks.append(task.id)
            continue
        # Check max_duration
        if task.started_at is not None:
            duration = latest_timestamp - task.started_at
            # Get max_duration from settings for this task type
            try:
                # Use getattr since ActionsConfig is a Pydantic model, not a dict
                action_params = getattr(settings.actions, task.task_type, None)
                if action_params:
                    max_dur = action_params.max_duration_minutes
                    if duration > max_dur:
                        stuck_tasks.append(task.id)
            except Exception:
                pass

    ok_tasks = len(stuck_tasks) == 0
    results.append(
        {
            "name": "no_stuck_tasks",
            "ok": ok_tasks,
            "details": f"stuck_tasks_count: {len(stuck_tasks)}, latest_ts: {latest_timestamp}",
        }
    )

    # e) death_summary: fail if any dead character lacks a CHARACTER_DIED event.
    # ... (existing death_summary code) ...
    # (Wait, the previous block was long. I will append the social invariants at the end)
    dead_chars = (
        session.query(Character)
        .filter(Character.world_id == world_id, Character.alive.is_(False))
        .all()
    )

    all_deaths_explained = True
    death_causes = {}
    for c in dead_chars:
        # Check for CHARACTER_DIED event for this actor
        has_event = session.query(WorldEvent).filter(
            WorldEvent.world_id == world_id,
            WorldEvent.event_type == "CHARACTER_DIED",
            WorldEvent.actor_id == c.id
        ).first() is not None

        if not has_event:
            all_deaths_explained = False

        cause = c.death_cause or "unknown"
        death_causes[cause] = death_causes.get(cause, 0) + 1

    # R10: mortality must stay within the configured per-day threshold.
    # Rate = deaths / (population_total * elapsed_days), days >= 1.
    total_pop = (
        session.query(func.count(Character.id))
        .filter(Character.world_id == world_id)
        .scalar() or 0
    )
    elapsed_days = max(1, latest_timestamp // 1440)
    death_rate = (
        len(dead_chars) / (total_pop * elapsed_days) if total_pop else 0.0
    )
    max_death_rate = settings.invariants.max_death_rate_per_day
    within_mortality_threshold = death_rate <= max_death_rate

    results.append(
        {
            "name": "death_summary",
            "ok": all_deaths_explained and within_mortality_threshold,
            "details": {
                "dead_count": len(dead_chars),
                "causes": death_causes,
                "death_rate": round(death_rate, 4),
                "max_death_rate_per_day": max_death_rate,
                "all_deaths_explained": all_deaths_explained,
            },
        }
    )

    # f) no_impossible_states: needs/health in [0,100]; referential integrity.
    # 1. Needs/Health Range
    # Use a more robust check for all need fields
    needs_cols = [
        "hunger", "thirst", "energy", "social", "hygiene", "comfort",
        "safety", "entertainment", "privacy"
    ]
    needs_out_of_range = False
    for col in needs_cols:
        # Use getattr for dynamic column access on CharacterNeeds model
        col_attr = getattr(CharacterNeeds, col)
        min_val = session.query(func.min(col_attr)).filter(
            CharacterNeeds.character_id.in_(
                session.query(Character.id).filter(Character.world_id == world_id)
            )
        ).scalar()
        max_val = session.query(func.max(col_attr)).filter(
            CharacterNeeds.character_id.in_(
                session.query(Character.id).filter(Character.world_id == world_id)
            )
        ).scalar()
        if min_val is not None and (
            min_val < settings.invariants.needs_range[0]
            or max_val > settings.invariants.needs_range[1]
        ):
            needs_out_of_range = True
            break

    min_h = session.query(func.min(CharacterHealth.health)).filter(
        CharacterHealth.character_id.in_(
            session.query(Character.id).filter(Character.world_id == world_id)
        )
    ).scalar()
    max_h = session.query(func.max(CharacterHealth.health)).filter(
        CharacterHealth.character_id.in_(
            session.query(Character.id).filter(Character.world_id == world_id)
        )
    ).scalar()
    health_out_of_range = min_h is not None and (
        min_h < settings.invariants.health_range[0]
        or max_h > settings.invariants.health_range[1]
    )

    min_s = session.query(func.min(CharacterHealth.stress)).filter(
        CharacterHealth.character_id.in_(
            session.query(Character.id).filter(Character.world_id == world_id)
        )
    ).scalar()
    max_s = session.query(func.max(CharacterHealth.stress)).filter(
        CharacterHealth.character_id.in_(
            session.query(Character.id).filter(Character.world_id == world_id)
        )
    ).scalar()
    stress_out_of_range = min_s is not None and (
        min_s < 0 or max_s > 100
    )

    # 2. Referential Integrity
    # Characters -> Locations
    invalid_locs = session.query(Character.id).filter(
        Character.world_id == world_id,
        ~Character.location_id.in_(
            session.query(Location.id).filter(Location.world_id == world_id)
        ),
    ).all()

    # Tasks -> Characters
    invalid_tasks = session.query(CharacterTask.id).filter(
        ~CharacterTask.character_id.in_(
            session.query(Character.id).filter(Character.world_id == world_id)
        )
    ).all()

    ok_states = (
        not needs_out_of_range
        and not health_out_of_range
        and not stress_out_of_range
        and not invalid_locs
        and not invalid_tasks
    )
    results.append(
        {
            "name": "no_impossible_states",
            "ok": ok_states,
            "details": {
                "needs_range_ok": not needs_out_of_range,
                "health_range_ok": not health_out_of_range,
                "stress_range_ok": not stress_out_of_range,
                "invalid_locs_count": len(invalid_locs),
                "invalid_tasks_count": len(invalid_tasks),
            },
        }
    )


    # g) relationship_range: dimensions in [-100, 100], no self-pairs, a < b
    rel_rows = session.query(Relationship).filter(Relationship.world_id == world_id).all()
    rel_violations = []
    for r in rel_rows:
        if r.character_a == r.character_b:
            rel_violations.append(f"self-pair: {r.character_a}")
        if not (r.character_a < r.character_b):
            rel_violations.append(f"order violation: {r.character_a} {r.character_b}")
        for col in [
            "trust", "affection", "respect", "fear", "anger",
            "attraction", "romantic_interest", "familiarity",
        ]:
            val = getattr(r, col)
            if not (-100 <= val <= 100):
                rel_violations.append(f"{col} range: {val}")
    ok_rel_range = len(rel_violations) == 0
    results.append({
        "name": "relationship_range",
        "ok": ok_rel_range,
        "details": f"violations: {', '.join(rel_violations)}" if rel_violations else "none"
    })

    # h) social_event_integrity: CONFLICT and SOCIAL_INTERACTION checks
    social_events = session.query(WorldEvent).filter(
        WorldEvent.world_id == world_id,
        WorldEvent.event_type.in_(["CONFLICT", "SOCIAL_INTERACTION"])
    ).all()
    social_violations = []
    for e in social_events:
        payload = json.loads(e.payload)
        # Target id: canonical location is the event column; SOCIAL_INTERACTION
        # also carries it in the payload (CONFLICT does not — spec R5).
        target_id = e.target_id or payload.get("target_id")
        if not target_id or e.actor_id == target_id:
            social_violations.append(f"event {e.id}: invalid actor/target")
            continue
        # Check if relationship exists
        # Note: Relationship table uses a < b
        a, b = sorted([e.actor_id, target_id])
        rel = session.query(Relationship).filter(
            Relationship.world_id == world_id,
            Relationship.character_a == a,
            Relationship.character_b == b
        ).first()
        if not rel:
            social_violations.append(f"event {e.id}: no relationship row")
        elif e.event_type == "SOCIAL_INTERACTION":
            delta = payload.get("affection_delta")
            after = payload.get("affection_after")
            if delta not in [2.0, -5.0]:
                social_violations.append(f"event {e.id}: invalid delta {delta}")
            if not (-100 <= (after or 0) <= 100):
                social_violations.append(f"event {e.id}: invalid affection_after {after}")
    ok_soc_int = len(social_violations) == 0
    results.append({
        "name": "social_event_integrity",
        "ok": ok_soc_int,
        "details": f"violations: {', '.join(social_violations)}" if social_violations else "none"
    })

    # i) relationship_event_link: relationship_events.event_id references world_events
    rel_evs = session.query(RelationshipEvent).filter(RelationshipEvent.world_id == world_id).all()
    link_violations = []
    for re in rel_evs:
        if re.event_id is not None:
            exists = (
                session.query(WorldEvent)
                .filter(WorldEvent.id == re.event_id)
                .first()
                is not None
            )
            if not exists:
                link_violations.append(f"rel_event {re.id} -> missing world_event {re.event_id}")
    ok_rel_link = len(link_violations) == 0
    results.append({
        "name": "relationship_event_link",
        "ok": ok_rel_link,
        "details": f"violations: {', '.join(link_violations)}" if link_violations else "none"
    })

    # j) org_membership_integrity: 1 leader per org, every char in 1 community org
    if not settings.social.enabled:
        ok_org_int = True
        org_violations = []
    else:
        orgs = session.query(Organization).filter(Organization.world_id == world_id).all()
        org_violations = []
        for o in orgs:
            leaders = session.query(OrganizationMember).filter(
                OrganizationMember.organization_id == o.id,
                OrganizationMember.role == "leader"
            ).count()
            if leaders != 1:
                org_violations.append(f"org {o.name}: {leaders} leaders")

        community_orgs = [o.id for o in orgs if o.type == "community"]
        chars = session.query(Character).filter(Character.world_id == world_id).all()
        for c in chars:
            member_count = session.query(OrganizationMember).filter(
                OrganizationMember.character_id == c.id,
                OrganizationMember.organization_id.in_(community_orgs)
            ).count()
            if member_count != 1:
                org_violations.append(f"char {c.id}: {member_count} community memberships")
        ok_org_int = len(org_violations) == 0
    results.append({
        "name": "org_membership_integrity",
        "ok": ok_org_int,
        "details": f"violations: {', '.join(org_violations)}" if org_violations else "none"
    })

    # --- M3 org invariants (spec R9.1-R9.6) — gated by R1: org AND social ---
    org_gate = bool(getattr(settings, "org", None) and settings.org.enabled
                    and settings.social.enabled)

    # k) org_leader_valid: exactly one leader per org AND that leader alive.
    if not org_gate:
        ok_leader_valid = True
        leader_valid_violations = []
    else:
        orgs_m3 = session.query(Organization).filter(
            Organization.world_id == world_id).all()
        leader_valid_violations = []
        for o in orgs_m3:
            leaders = session.query(OrganizationMember).filter(
                OrganizationMember.organization_id == o.id,
                OrganizationMember.role == "leader"
            ).all()
            if len(leaders) != 1:
                leader_valid_violations.append(
                    f"org {o.name}: {len(leaders)} leader rows")
                continue
            leader_char = session.query(Character).filter_by(
                id=leaders[0].character_id).first()
            if leader_char is None or not leader_char.alive:
                leader_valid_violations.append(
                    f"org {o.name}: leader {leaders[0].character_id} not alive")
        ok_leader_valid = len(leader_valid_violations) == 0
    results.append({
        "name": "org_leader_valid",
        "ok": ok_leader_valid,
        "details": (f"violations: {', '.join(leader_valid_violations)}"
                    if leader_valid_violations else "none")
    })

    # l) law_consistency: <=1 org_laws row per org; law_key in the config
    # catalog; enacter is a member of the org.
    if not org_gate:
        ok_law_cons = True
        law_violations = []
    else:
        catalog_keys = set(settings.org.laws.catalog.keys())
        law_violations = []
        for o in session.query(Organization).filter(
                Organization.world_id == world_id).all():
            laws = session.query(OrgLaw).filter_by(
                world_id=world_id, organization_id=o.id).all()
            if len(laws) > 1:
                law_violations.append(f"org {o.name}: {len(laws)} law rows")
            for lw in laws:
                if lw.law_key not in catalog_keys:
                    law_violations.append(
                        f"org {o.name}: law {lw.law_key} not in catalog")
                member = session.query(OrganizationMember).filter_by(
                    organization_id=o.id,
                    character_id=lw.enacted_by_character_id).first()
                if member is None:
                    law_violations.append(
                        f"org {o.name}: enacter {lw.enacted_by_character_id} "
                        "not a member")
        ok_law_cons = len(law_violations) == 0
    results.append({
        "name": "law_consistency",
        "ok": ok_law_cons,
        "details": (f"violations: {', '.join(law_violations)}"
                    if law_violations else "none")
    })

    # m) law_violation_idempotency: no (org, law, char, day) duplicates;
    # every LAW_VIOLATION event has a row; no_conflict rows carry an event
    # link while night_home rows carry none; sum(fine_paid) == sum(LAW_FINE).
    if not org_gate:
        ok_viol_idem = True
        viol_violations = []
    else:
        viol_violations = []
        rows = session.query(OrgLawViolation).filter_by(
            world_id=world_id).all()
        seen = {}
        for r in rows:
            key = (r.organization_id, r.law_key, r.character_id, r.game_day)
            seen[key] = seen.get(key, 0) + 1
        for key, count in seen.items():
            if count > 1:
                viol_violations.append(f"duplicate violation row: {key}")
        events_v = session.query(WorldEvent).filter_by(
            world_id=world_id, event_type="LAW_VIOLATION").all()
        row_keys = set(seen.keys())
        for ev in events_v:
            payload = json.loads(ev.payload) if ev.payload else {}
            key = (payload.get("organization_id"), payload.get("law_key"),
                   ev.actor_id, ev.game_timestamp // 1440)
            if key not in row_keys:
                viol_violations.append(f"LAW_VIOLATION event without row: {key}")
        for r in rows:
            if r.law_key == "no_conflict" and r.event_id is None:
                viol_violations.append(
                    f"no_conflict row without event link: {r.id}")
            if r.law_key == "night_home" and r.event_id is not None:
                viol_violations.append(
                    f"night_home row with unexpected event link: {r.id}")
        fines_paid_sum = sum(r.fine_paid for r in rows)
        law_fine_sum = session.query(func.sum(Transaction.amount)).filter(
            Transaction.world_id == world_id,
            Transaction.reason == "LAW_FINE"
        ).scalar() or 0
        if fines_paid_sum != law_fine_sum:
            viol_violations.append(
                f"fine_paid sum {fines_paid_sum} != LAW_FINE sum {law_fine_sum}")
        ok_viol_idem = len(viol_violations) == 0
    results.append({
        "name": "law_violation_idempotency",
        "ok": ok_viol_idem,
        "details": (f"violations: {', '.join(viol_violations)}"
                    if viol_violations else "none")
    })

    # n) org_treasury_conservation: full ledger replay per org account
    # (constitution P3): balance == sum(inflows) - sum(outflows); the reason
    # set on org accounts is closed (mint / PURCHASE / ORG_DUES / LAW_FINE
    # in; SALARY / ORG_FEAST out).
    if not org_gate:
        ok_treasury = True
        treasury_violations = []
    else:
        treasury_violations = []
        allowed_in = {"Initial organization funds", "PURCHASE", "ORG_DUES",
                      "LAW_FINE", "MARKET_SALE"}
        allowed_out = {"SALARY", "ORG_FEAST"}
        for o in session.query(Organization).filter(
                Organization.world_id == world_id).all():
            acc = session.query(Account).filter_by(
                world_id=world_id, owner_type="organization",
                owner_id=str(o.id)).first()
            if acc is None:
                continue  # no treasury -> nothing to conserve
            txs = session.query(Transaction).filter(
                (Transaction.from_account_id == acc.id)
                | (Transaction.to_account_id == acc.id)).all()
            inflow = sum(t.amount for t in txs if t.to_account_id == acc.id)
            outflow = sum(t.amount for t in txs if t.from_account_id == acc.id)
            if acc.balance != inflow - outflow:
                treasury_violations.append(
                    f"org {o.name}: balance {acc.balance} != replay "
                    f"{inflow - outflow}")
            for t in txs:
                if t.to_account_id == acc.id and t.from_account_id is not None \
                        and t.reason not in allowed_in:
                    treasury_violations.append(
                        f"org {o.name}: unexpected inflow reason {t.reason}")
                if t.from_account_id == acc.id \
                        and t.reason not in allowed_out:
                    treasury_violations.append(
                        f"org {o.name}: unexpected outflow reason {t.reason}")
        ok_treasury = len(treasury_violations) == 0
    results.append({
        "name": "org_treasury_conservation",
        "ok": ok_treasury,
        "details": (f"violations: {', '.join(treasury_violations)}"
                    if treasury_violations else "none")
    })

    # o) reconciliation_link: each RECONCILIATION pair has a relationship row
    # at exactly target_affection, thawed from <= -60, both alive members of
    # the payload org.
    if not org_gate:
        ok_recon = True
        recon_violations = []
    else:
        recon_violations = []
        target_aff = settings.org.reconciliation.target_affection
        rec_events = session.query(WorldEvent).filter_by(
            world_id=world_id, event_type="RECONCILIATION").all()
        for ev in rec_events:
            payload = json.loads(ev.payload) if ev.payload else {}
            pair = payload.get("pair") or []
            if len(pair) != 2:
                recon_violations.append(f"RECONCILIATION {ev.id}: bad pair")
                continue
            a, b = sorted(pair)
            rel = session.query(Relationship).filter_by(
                world_id=world_id, character_a=a, character_b=b).first()
            if rel is None:
                recon_violations.append(f"RECONCILIATION {ev.id}: no row {pair}")
                continue
            if rel.affection != target_aff:
                recon_violations.append(
                    f"RECONCILIATION {ev.id}: affection {rel.affection} "
                    f"!= target {target_aff}")
            if payload.get("affection_before", 0.0) > -60.0:
                recon_violations.append(
                    f"RECONCILIATION {ev.id}: affection_before "
                    f"{payload.get('affection_before')} > -60")
            org_id = payload.get("organization_id")
            for cid in pair:
                char = session.query(Character).filter_by(id=cid).first()
                if char is None or not char.alive:
                    recon_violations.append(
                        f"RECONCILIATION {ev.id}: {cid} not alive")
                member = session.query(OrganizationMember).filter_by(
                    organization_id=org_id, character_id=cid).first()
                if member is None:
                    recon_violations.append(
                        f"RECONCILIATION {ev.id}: {cid} not in org {org_id}")
        ok_recon = len(recon_violations) == 0
    results.append({
        "name": "reconciliation_link",
        "ok": ok_recon,
        "details": (f"violations: {', '.join(recon_violations)}"
                    if recon_violations else "none")
    })

    # p) election_consistency: the last ELECTION of an org produced the
    # current leader row, and the winner was alive at election time.
    if not org_gate:
        ok_election = True
        election_violations = []
    else:
        election_violations = []
        for o in session.query(Organization).filter(
                Organization.world_id == world_id).all():
            last = session.query(WorldEvent).filter_by(
                world_id=world_id, event_type="ELECTION").filter(
                WorldEvent.payload.like(f'%"organization_id": {o.id}%')
            ).order_by(WorldEvent.id.desc()).first()
            if last is None:
                continue
            winner_id = last.actor_id
            leader_row = session.query(OrganizationMember).filter_by(
                organization_id=o.id, role="leader").first()
            if leader_row is None or leader_row.character_id != winner_id:
                election_violations.append(
                    f"org {o.name}: last ELECTION winner {winner_id} != "
                    f"leader {leader_row.character_id if leader_row else None}")
                continue
            winner = session.query(Character).filter_by(id=winner_id).first()
            if winner is None or (
                    winner.death_game_timestamp is not None
                    and winner.death_game_timestamp <= last.game_timestamp):
                election_violations.append(
                    f"org {o.name}: election winner {winner_id} not alive "
                    "at election time")
        ok_election = len(election_violations) == 0
    results.append({
        "name": "election_consistency",
        "ok": ok_election,
        "details": (f"violations: {', '.join(election_violations)}"
                    if election_violations else "none")
    })

    # --- M4 AI invariants (spec R11) — gated by R1: llm enabled ---
    ai_gate = bool(getattr(settings, "llm", None) and settings.llm.enabled)

    # ai_request_integrity: terminal statuses have processed_at + payload;
    # no pending older than the current day.
    if not ai_gate:
        ok_ai_req = True
        ai_req_violations = []
    else:
        from app.db.models import AiRequest as _AiReq
        from app.db.models import WorldClock as _WorldClock
        now_ts = None
        clock = session.query(_WorldClock).filter_by(world_id=world_id).first()
        if clock is not None:
            now_ts = clock.game_timestamp
        ai_req_violations = []
        for r in session.query(_AiReq).filter_by(world_id=world_id).all():
            if r.status in ("done", "failed", "skipped"):
                if r.processed_at is None:
                    ai_req_violations.append(
                        f"request {r.id}: terminal without processed_at")
                if r.status == "done" and r.result is None:
                    ai_req_violations.append(
                        f"request {r.id}: done without result")
                if r.status == "failed" and not r.error:
                    ai_req_violations.append(
                        f"request {r.id}: failed without error")
            elif r.status == "pending" and now_ts is not None:
                if now_ts - r.game_timestamp > 1440:
                    ai_req_violations.append(
                        f"request {r.id}: pending older than a day")
            elif r.status not in ("pending", "done", "failed", "skipped"):
                ai_req_violations.append(
                    f"request {r.id}: unknown status {r.status}")
        ok_ai_req = len(ai_req_violations) == 0
    results.append({
        "name": "ai_request_integrity",
        "ok": ok_ai_req,
        "details": (f"violations: {', '.join(ai_req_violations)}"
                    if ai_req_violations else "none")
    })

    # memory_integrity: every memory references a real event; importance in
    # 0..100; consolidated deletions leave no orphans (event_id resolvable
    # OR the row is a consolidated record derived from a logged batch).
    if not ai_gate:
        ok_mem = True
        mem_violations = []
    else:
        from app.db.models import Memory as _Mem
        mem_violations = []
        event_ids = {e.id for e in session.query(WorldEvent).filter_by(
            world_id=world_id).all()}
        for m in session.query(_Mem).filter_by(world_id=world_id).all():
            if not 0 <= m.importance <= 100:
                mem_violations.append(
                    f"memory {m.id}: importance {m.importance} out of range")
            if m.event_id not in event_ids:
                mem_violations.append(
                    f"memory {m.id}: event {m.event_id} missing")
        ok_mem = len(mem_violations) == 0
    results.append({
        "name": "memory_integrity",
        "ok": ok_mem,
        "details": (f"violations: {', '.join(mem_violations)}"
                    if mem_violations else "none")
    })

    # dialogue_integrity: roles valid, contents non-empty, session times
    # monotone.
    if not ai_gate:
        ok_dial = True
        dial_violations = []
    else:
        from app.db.models import DialogueTurn as _Dial
        dial_violations = []
        valid_roles = {"user", "assistant", "system"}
        for d in session.query(_Dial).filter_by(world_id=world_id).all():
            if d.role not in valid_roles:
                dial_violations.append(
                    f"turn {d.id}: invalid role {d.role}")
            if not d.content:
                dial_violations.append(f"turn {d.id}: empty content")
        per_session = {}
        for d in session.query(_Dial).filter_by(world_id=world_id).order_by(
                _Dial.id).all():
            per_session.setdefault(d.session_id, []).append(d.game_timestamp)
        for sid, times in per_session.items():
            if times != sorted(times):
                dial_violations.append(f"session {sid}: times not monotone")
        ok_dial = len(dial_violations) == 0
    results.append({
        "name": "dialogue_integrity",
        "ok": ok_dial,
        "details": (f"violations: {', '.join(dial_violations)}"
                    if dial_violations else "none")
    })

    # visual_asset_integrity (M6, R10): asset_type closed set; canonical flag
    # on real rows; character/location references valid when present;
    # storage paths unique. Gated on visual.enabled (П2-аналог).
    visual_gate = bool(getattr(settings, "visual", None) and settings.visual.enabled)
    if not visual_gate:
        results.append({
            "name": "visual_asset_integrity", "ok": True, "details": "visual disabled"
        })
    else:
        from app.db.models import Character as _Char
        from app.db.models import Location as _Loc
        from app.db.models import VisualAsset as _VA
        va_violations = []
        assets = session.query(_VA).filter_by(world_id=world_id).all()
        char_ids = {c.id for c in session.query(_Char).filter_by(world_id=world_id).all()}
        loc_ids = {
            loc.id for loc in session.query(_Loc).filter_by(world_id=world_id).all()
        }
        seen_paths = set()
        for a in assets:
            if a.asset_type not in ("portrait", "scene"):
                va_violations.append(f"asset {a.id}: bad type {a.asset_type}")
            if a.character_id is not None and a.character_id not in char_ids:
                va_violations.append(f"asset {a.id}: unknown character {a.character_id}")
            if a.location_id is not None and a.location_id not in loc_ids:
                va_violations.append(f"asset {a.id}: unknown location {a.location_id}")
            if a.storage_path in seen_paths:
                va_violations.append(f"asset {a.id}: duplicate path {a.storage_path}")
            seen_paths.add(a.storage_path)
        results.append({
            "name": "visual_asset_integrity",
            "ok": len(va_violations) == 0,
            "details": (f"violations: {', '.join(va_violations)}"
                        if va_violations else f"assets: {len(assets)}")
        })

    # external_integrity (M7, R10): contacts reference real characters and
    # external locations; services belong to locations; every RETURNED with
    # spent > 0 has matching transactions (ledger discipline).
    from app.db.models import ExternalContact as _EC
    from app.db.models import ExternalLocation as _EL
    from app.db.models import ExternalService as _ES
    from app.db.models import Transaction as _Tx
    ext_violations = []
    ext_char_ids = {
        c.id for c in session.query(Character).filter_by(world_id=world_id).all()
    }
    ext_locations = {
        el.id for el in session.query(_EL).filter_by(world_id=world_id).all()
    }
    ext_contacts = session.query(_EC).filter_by(world_id=world_id).all()
    for c in ext_contacts:
        if c.character_id not in ext_char_ids:
            ext_violations.append(f"contact {c.id}: unknown character {c.character_id}")
        if c.external_location_id is not None and c.external_location_id not in ext_locations:
            ext_violations.append(f"contact {c.id}: unknown external location")
    for s in session.query(_ES).filter_by(world_id=world_id).all():
        if s.external_location_id not in ext_locations:
            ext_violations.append(f"service {s.id}: unknown external location")
    # RETURNED spent -> transactions exist (EXTERNAL_TRAVEL / EXTERNAL_PURCHASE)
    ext_events = session.query(WorldEvent).filter_by(
        world_id=world_id, event_type="TRAVEL_EXTERNAL_RETURNED"
    ).all()
    for ev in ext_events:
        payload = _json.loads(ev.payload) if ev.payload else {}
        spent = int(payload.get("spent") or 0)
        if spent > 0:
            tx_count = (
                session.query(_Tx)
                .filter(
                    _Tx.world_id == world_id,
                    _Tx.game_timestamp == ev.game_timestamp,
                    _Tx.reason.in_(["EXTERNAL_TRAVEL", "EXTERNAL_PURCHASE"]),
                )
                .count()
            )
            if tx_count == 0:
                ext_violations.append(
                    f"event {ev.id}: spent {spent} without ledger transaction"
                )
    results.append({
        "name": "external_integrity",
        "ok": len(ext_violations) == 0,
        "details": (f"violations: {', '.join(ext_violations)}"
                    if ext_violations else f"contacts: {len(ext_contacts)}")
    })

    # admin_integrity (M8, R11): audit entries reference admin-role users;
    # disabled accounts have no live (planned/active) tasks.
    from app.db.models import AdminAuditLog as _AAL
    admin_violations = []
    admin_roles = {"admin", "developer"}
    users_by_id = {u.id: u for u in session.query(User).all()}
    for entry in session.query(_AAL).filter_by(world_id=world_id).all():
        actor = users_by_id.get(entry.admin_user_id)
        if actor is None or actor.role not in admin_roles:
            admin_violations.append(
                f"audit {entry.id}: actor {entry.admin_user_id} not admin"
            )
    from app.db.models import CharacterTask as _CT
    for u in session.query(User).filter(User.disabled == True).all():  # noqa: E712
        player_rows = session.query(Character).filter_by(user_id=u.id).all()
        player_ids = [c.id for c in player_rows]
        if player_ids:
            live = (
                session.query(_CT)
                .filter(
                    _CT.character_id.in_(player_ids),
                    _CT.status.in_(["planned", "active"]),
                )
                .count()
            )
            if live > 0:
                admin_violations.append(f"user {u.id}: disabled with live tasks")
    # weather_integrity (010, R8): one row per (world, day); valid ranges
    from app.db.models import WeatherState as _WS
    weather_violations = []
    weather_rows = session.query(_WS).filter_by(world_id=world_id).all()
    seen_days = set()
    for w in weather_rows:
        if w.day in seen_days:
            weather_violations.append(f"day {w.day}: duplicate row")
        seen_days.add(w.day)
        if not (-60 <= w.temperature <= 45):
            weather_violations.append(f"day {w.day}: temperature out of range")
        if not (0 <= w.cloudiness <= 1) or w.visibility < 0 or w.wind < 0:
            weather_violations.append(f"day {w.day}: field out of range")
        if w.source not in ("synthetic", "historical"):
            weather_violations.append(f"day {w.day}: unknown source")
    # crime_integrity (016, R6): resolved crimes have resolutions;
    # every fine resolution has a FINE_PAID event.
    from app.db.models import Crime as _Crime
    crime_violations = []
    crimes = session.query(_Crime).filter_by(world_id=world_id).all()
    fine_events = {
        (ev.actor_id, _json.loads(ev.payload).get("crime_id"))
        for ev in session.query(WorldEvent)
        .filter_by(world_id=world_id, event_type="FINE_PAID").all()
        if ev.payload
    }
    for c in crimes:
        if c.status == "resolved" and c.resolution not in ("fine", "prison"):
            crime_violations.append(f"crime {c.id}: bad resolution")
        if c.status == "resolved" and c.resolution == "fine":
            if (c.actor_character_id, c.id) not in fine_events:
                crime_violations.append(f"crime {c.id}: fine without FINE_PAID")
        if c.status == "reported" and c.resolution is not None:
            crime_violations.append(f"crime {c.id}: resolution before resolve")
    results.append({
        "name": "crime_integrity",
        "ok": len(crime_violations) == 0,
        "details": (f"violations: {', '.join(crime_violations)}"
                    if crime_violations else f"crimes: {len(crimes)}"),
    })

    # messages_integrity (013, R6): body domain; alive participants
    from app.db.models import Message as _Msg
    msg_violations = []
    msgs = session.query(_Msg).filter_by(world_id=world_id).all()
    alive_ids = {
        c.id for c in session.query(Character)
        .filter_by(world_id=world_id).all()
        if getattr(c, "alive", True)
    }
    for m in msgs:
        if not m.body or len(m.body) > 2000:
            msg_violations.append(f"message {m.id}: bad body length")
        if m.from_character_id not in alive_ids:
            msg_violations.append(f"message {m.id}: unknown/dead sender")
        if m.to_character_id not in alive_ids:
            msg_violations.append(f"message {m.id}: unknown/dead recipient")
    results.append({
        "name": "messages_integrity",
        "ok": len(msg_violations) == 0,
        "details": (f"violations: {', '.join(msg_violations)}"
                    if msg_violations else f"messages: {len(msgs)}")
    })

    # market_integrity (012, R7): offers domain + sold closure
    from app.db.models import MarketOffer as _MO
    market_violations = []
    active_objects: set[int] = set()
    offers = session.query(_MO).filter_by(world_id=world_id).all()
    for offer in offers:
        if offer.price <= 0:
            market_violations.append(f"offer {offer.id}: price<=0")
        if offer.status == "active":
            if offer.object_id in active_objects:
                market_violations.append(
                    f"offer {offer.id}: second active on object")
            active_objects.add(offer.object_id)
        if offer.status == "sold":
            if offer.buyer_character_id is None:
                market_violations.append(f"offer {offer.id}: sold w/o buyer")
            if offer.closed_at is None:
                market_violations.append(f"offer {offer.id}: sold w/o closed_at")
    results.append({
        "name": "market_integrity",
        "ok": len(market_violations) == 0,
        "details": (f"violations: {', '.join(market_violations)}"
                    if market_violations else f"offers: {len(offers)}")
    })

    # fire_integrity (011, R8): burn_state domain; burned → quantity 0
    from app.db.models import WorldObject as _WO
    fire_violations = []
    fire_rows = session.query(_WO).filter_by(world_id=world_id).all()
    for o in fire_rows:
        if o.burn_state not in ("intact", "burning", "burned"):
            fire_violations.append(f"object {o.id}: bad burn_state")
        if o.burn_state == "burned" and o.quantity != 0:
            fire_violations.append(f"object {o.id}: burned but quantity>0")
    results.append({
        "name": "fire_integrity",
        "ok": len(fire_violations) == 0,
        "details": (f"violations: {', '.join(fire_violations)}"
                    if fire_violations else "ok")
    })

    results.append({
        "name": "weather_integrity",
        "ok": len(weather_violations) == 0,
        "details": (f"violations: {', '.join(weather_violations)}"
                    if weather_violations else f"days: {len(weather_rows)}")
    })

    results.append({
        "name": "admin_integrity",
        "ok": len(admin_violations) == 0,
        "details": (f"violations: {', '.join(admin_violations)}"
                    if admin_violations else "ok")
    })

    return results
