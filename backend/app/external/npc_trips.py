"""NPC external trips (013): utility-driven mainland travel, flag-gated.

`external.npc_utility` (default false — П2 keystone): each day, an NPC with
health < threshold and no active cooldown takes a TRAVEL_EXTERNAL task for a
treatment service (same pipeline as the player flow in M7).
"""
import json

from sqlalchemy.orm import Session

from app.db.models import (
    Character,
    CharacterHealth,
    CharacterTask,
    ExternalLocation,
    ExternalService,
    WorldEvent,
)


def run_npc_trip_phase(
    session: Session, world_id: str, day: int, settings
) -> int:
    """Enqueue mainland treatment trips for needy NPCs. Returns count."""
    if not settings.external.npc_utility:
        return 0
    cooldown_days = 30
    threshold = 40.0
    count = 0
    npcs = (
        session.query(Character)
        .filter_by(world_id=world_id)
        .filter(Character.user_id.is_(None), Character.alive)
        .all()
    )
    # deterministic single treatment target (lowest id)
    service = (
        session.query(ExternalService)
        .filter(ExternalService.service_type == "treatment")
        .order_by(ExternalService.id)
        .first()
    )
    if service is None:
        return 0
    ext_loc = session.get(ExternalLocation, service.external_location_id)
    for npc in npcs:
        health = (
            session.query(CharacterHealth)
            .filter_by(character_id=npc.id)
            .first()
        )
        if health is None or health.health >= threshold:
            continue
        # cooldown: last RETURNED of this npc
        last_return = (
            session.query(WorldEvent)
            .filter_by(
                world_id=world_id,
                event_type="TRAVEL_EXTERNAL_RETURNED",
                actor_id=npc.id,
            )
            .order_by(WorldEvent.id.desc())
            .first()
        )
        if last_return is not None:
            payload = json.loads(last_return.payload) if last_return.payload else {}
            if day - payload.get("day", day) < cooldown_days:
                continue
        # no overlapping trip
        active_trip = (
            session.query(CharacterTask)
            .filter_by(character_id=npc.id, task_type="TRAVEL_EXTERNAL")
            .filter(CharacterTask.status.in_(["planned", "active"]))
            .first()
        )
        if active_trip is not None:
            continue
        travel_cost = settings.external.travel_cost
        basket_cost = int(
            round(service.price * service.price_multiplier)
        )
        now = day * 1440
        task = CharacterTask(
            character_id=npc.id, priority=4, task_type="TRAVEL_EXTERNAL",
            target_id=None, status="planned", source="utility",
            parameters=json.dumps({
                "service_id": service.id,
                "location_id": ext_loc.id,
                "purpose": f"treatment ({ext_loc.name})",
                "travel_cost": travel_cost,
                "basket_cost": basket_cost,
            }),
            created_at=now, started_at=None, ends_at=now + settings.external.travel_minutes,
        )
        session.add(task)
        session.flush()
        count += 1
    return count


def update_service_multipliers(
    session: Session, world_id: str, day: int, settings
) -> None:
    """Supply/demand (013): yesterday's purchases raise the multiplier,
    then decay toward 1.0. Flag-gated (П2 keystone)."""
    if not settings.external.supply_demand:
        return
    import json as _json

    services = (
        session.query(ExternalService)
        .filter(ExternalService.world_id == world_id)
        .all()
    )
    yesterday = day - 1
    purchases: dict[tuple[int, str], int] = {}
    events = (
        session.query(WorldEvent)
        .filter_by(world_id=world_id, event_type="TRAVEL_EXTERNAL_RETURNED")
        .all()
    )
    for ev in events:
        payload = _json.loads(ev.payload) if ev.payload else {}
        if payload.get("day") != yesterday:
            continue
        for item in payload.get("items", []):
            key = (payload.get("service_id", 0), str(item.get("item_type", "")))
            purchases[key] = purchases.get(key, 0) + 1
    for svc in services:
        key = (svc.id, str(svc.item_type or ""))
        bought = purchases.get(key, 0)
        mult = svc.price_multiplier + 0.02 * bought
        mult = 1.0 + (mult - 1.0) * 0.95  # daily decay toward base
        svc.price_multiplier = round(max(0.8, min(1.5, mult)), 4)
    session.flush()
