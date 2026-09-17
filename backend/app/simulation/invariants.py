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
    Relationship,
    RelationshipEvent,
    ResourceBalance,
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

    conservation_violations = []
    for otype, actual in type_rows:
        expected = (
            daily_stock.get(otype, 0)  # seed stock + per-day stock
            + days_elapsed * daily_stock.get(otype, 0)
            + char_count * initial_items_per_char.count(otype)
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
        import json
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

    return results
