"""Fire (011, §72): probabilistic burn model — no physics per spec.

Fire lifecycle: ignite → burning (spread to flammable neighbours on the same
location, damage characters present, burning → burned after burnout_days).
Deterministic: rng streams keyed by (seed, day, object). Default worlds have
spontaneous_chance_per_day=0.0 → byte-identical headless reports (П2).
"""
import hashlib
import struct

from sqlalchemy.orm import Session

from app.config.config import FireConfig
from app.db.models import Character, Location, WorldEvent, WorldObject
from app.events.events import EventType, log_event
from app.simulation.weather import WeatherState


def _rng_floats(key: bytes, count: int) -> list[float]:
    out = []
    counter = 0
    while len(out) < count:
        digest = hashlib.sha256(key + struct.pack("<I", counter)).digest()
        for i in range(0, 8, 4):
            (chunk,) = struct.unpack("<I", digest[i * 4 : (i + 1) * 4])
            out.append(chunk / 0xFFFFFFFF)
            if len(out) >= count:
                break
        counter += 1
    return out


def ignite_object(
    session: Session, world_id: str, obj: WorldObject, day: int, settings
) -> None:
    """Set an object burning (idempotent). Emits OBJECT_BURNING."""
    if obj.burn_state == "burning" or obj.burn_state == "burned":
        return
    obj.burn_state = "burning"
    obj.object_metadata = obj.object_metadata or "{}"
    log_event(
        session, world_id=world_id, event_type=EventType.OBJECT_BURNING,
        actor_id=None, location_id=obj.location_id,
        payload={"object_id": obj.id, "object_type": obj.object_type,
                 "day": day},
        game_timestamp=day * 1440,
    )
    session.flush()


def _destroy_object(session: Session, world_id: str, obj: WorldObject,
                    day: int) -> None:
    """Burned: deactivate (inventory endpoints exclude inactive objects)."""
    obj.burn_state = "burned"
    obj.condition = 0
    obj.quantity = 0  # inventory endpoints filter quantity > 0
    log_event(
        session, world_id=world_id, event_type=EventType.OBJECT_BURNED,
        actor_id=None, location_id=obj.location_id,
        payload={"object_id": obj.id, "object_type": obj.object_type,
                 "day": day},
        game_timestamp=day * 1440,
    )
    session.flush()


def run_fire_phase(
    session: Session, world_id: str, day: int, settings
) -> None:
    """Daily fire phase: spread, damage, burnout, spontaneous ignition."""
    fire_cfg: FireConfig = settings.fire
    if not fire_cfg.enabled:
        return

    # weather coupling (R3): spontaneous ignition needs hot + dry day
    weather_row = (
        session.query(WeatherState).filter_by(world_id=world_id, day=day).first()
    )
    hot_dry = (
        weather_row is not None
        and weather_row.temperature > 28
        and weather_row.precipitation == 0
    )

    burning = (
        session.query(WorldObject)
        .filter_by(world_id=world_id, burn_state="burning")
        .all()
    )
    burning_by_loc: dict[int, list[WorldObject]] = {}
    for obj in burning:
        burning_by_loc.setdefault(obj.location_id, []).append(obj)

    # 1. burnout + damage + spread per burning location
    for obj in burning:
        # burnout: ignited day is inferred from the OBJECT_BURNING event
        ignited_event = (
            session.query(WorldEvent)
            .filter_by(
                world_id=world_id, event_type=EventType.OBJECT_BURNING.value,
                location_id=obj.location_id,
            )
            .order_by(WorldEvent.id)
            .all()
        )
        import json as _json

        def _payload(e):
            return _json.loads(e.payload) if e.payload else {}

        ignited_day = next(
            (
                _payload(e).get("day", day)
                for e in ignited_event
                if _payload(e).get("object_id") == obj.id
            ),
            day,
        )
        if day - ignited_day >= fire_cfg.burnout_days:
            _destroy_object(session, world_id, obj, day)
            continue
        # damage characters on the same location
        chars = (
            session.query(Character)
            .filter_by(world_id=world_id, location_id=obj.location_id)
            .all()
        )
        for char in chars:
            from app.db.models import CharacterHealth

            if getattr(char, "alive", True) is False:
                continue
            health = (
                session.query(CharacterHealth)
                .filter_by(character_id=char.id)
                .first()
            )
            if health is None:
                continue
            health.health = max(
                0.0, health.health - fire_cfg.damage_per_day
            )
            session.flush()

    # 2. spread to flammable objects on burning locations
    for loc_id, _objs in list(burning_by_loc.items()):
        neighbours = (
            session.query(WorldObject)
            .filter_by(world_id=world_id, location_id=loc_id,
                       burn_state="intact")
            .all()
        )
        for nb in neighbours:
            if nb.object_type not in fire_cfg.flammable_types:
                continue
            p = fire_cfg.ignition_chance * nb.flammability
            r = _rng_floats(
                f"fire:spread:{world_id}:{day}:{nb.id}".encode(), 1
            )[0]
            if r < p:
                ignite_object(session, world_id, nb, day, settings)

    # 3. destruction (012 §76): condition<=0 → destroyed, event forever
    destroyed_candidates = (
        session.query(WorldObject)
        .filter_by(world_id=world_id, burn_state="intact")
        .filter(WorldObject.condition <= 0)
        .all()
    )
    for obj in destroyed_candidates:
        obj.quantity = 0
        log_event(
            session, world_id=world_id,
            event_type=EventType.OBJECT_DESTROYED,
            actor_id=None, location_id=obj.location_id,
            payload={"object_id": obj.id, "object_type": obj.object_type,
                     "day": day},
            game_timestamp=day * 1440,
        )
        session.flush()

    # 4. spontaneous ignition on hot dry days (outdoor locations)
    if hot_dry and fire_cfg.spontaneous_chance_per_day > 0.0:
        # open-air locations (Reineke map: island/pier/settlement/well...)
        outdoor = (
            session.query(Location)
            .filter_by(world_id=world_id)
            .filter(Location.type.in_(fire_cfg.outdoor_types))
            .all()
        )
        outdoor_ids = {loc.id for loc in outdoor}
        candidates = (
            session.query(WorldObject)
            .filter_by(world_id=world_id, burn_state="intact")
            .all()
        )
        for obj in candidates:
            if obj.location_id not in outdoor_ids:
                continue
            if obj.object_type not in fire_cfg.flammable_types:
                continue
            r = _rng_floats(
                f"fire:spont:{world_id}:{day}:{obj.id}".encode(), 1
            )[0]
            if r < fire_cfg.spontaneous_chance_per_day * obj.flammability:
                ignite_object(session, world_id, obj, day, settings)
