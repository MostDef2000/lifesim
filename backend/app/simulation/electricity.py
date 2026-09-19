"""Electricity (017, §26): house demand, generators, storm outages.

Flag `electricity.enabled` (default false). Stateless daily computation:
no new tables/columns; effects via object condition and CharacterNeeds.
"""
import hashlib

from sqlalchemy.orm import Session

from app.db.models import (
    Character,
    CharacterNeeds,
    Location,
    WeatherState,
    WorldObject,
)
from app.events.events import EventType, log_event

_FOOD_TYPES = (
    "food_stock", "food_bread", "food_canned", "prepared_meal",
    "fish", "berries",
)


def _storm_outage_flag(world_id: str, day: int, location_id: int) -> float:
    digest = hashlib.sha256(
        f"{world_id}:{day}:outage:{location_id}".encode()
    ).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def run_electricity_phase(
    session: Session, world_id: str, day: int, game_timestamp: int, settings
) -> int:
    """§26: compute supply/demand per house; deficits log POWER_OUTAGE,
    spoil food and drain occupant energy. Returns outage count."""
    if not getattr(settings.electricity, "enabled", False):
        return 0
    cfg = settings.electricity
    weather = (
        session.query(WeatherState)
        .filter_by(world_id=world_id, day=day)
        .first()
    )
    storm = (
        weather is not None
        and (
            (weather.wind or 0.0) >= cfg.storm_wind_speed
            or (weather.precipitation or 0.0) >= cfg.storm_precipitation
        )
    )
    houses = (
        session.query(Location)
        .filter_by(world_id=world_id, type="house")
        .all()
    )
    outages = 0
    for house in houses:
        occupants = (
            session.query(Character)
            .filter_by(world_id=world_id, home_location_id=house.id)
            .filter(Character.alive)
            .all()
        )
        if not occupants:
            continue
        demand = cfg.demand_per_occupant * len(occupants)
        generators = (
            session.query(WorldObject)
            .filter_by(world_id=world_id, location_id=house.id,
                       object_type="generator")
            .filter(WorldObject.quantity > 0)
            .all()
        )
        supply = sum(
            g.quantity * cfg.generator_capacity * (g.condition / 100.0)
            for g in generators
        )
        if storm and _storm_outage_flag(
                world_id, day, house.id) < cfg.outage_chance:
            supply = 0.0
        if supply >= demand:
            continue
        outages += 1
        log_event(
            session, world_id, game_timestamp, EventType.POWER_OUTAGE,
            actor_id=None,
            payload={"location_id": house.id, "demand": demand,
                     "supply": round(supply, 1), "storm": storm},
        )
        # food spoilage (same channel as §76 destruction / fire)
        for food in (
            session.query(WorldObject)
            .filter_by(world_id=world_id, location_id=house.id)
            .filter(WorldObject.quantity > 0,
                    WorldObject.object_type.in_(_FOOD_TYPES))
            .all()
        ):
            food.condition = max(0, food.condition - cfg.food_spoil_condition)
        # occupant energy drain (heating/appliances down)
        for occ in occupants:
            needs = (
                session.query(CharacterNeeds)
                .filter_by(character_id=occ.id)
                .first()
            )
            if needs is not None:
                needs.energy = max(
                    0.0, needs.energy - cfg.energy_drain)
    session.flush()
    return outages
