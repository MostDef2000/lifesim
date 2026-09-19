"""Crime/law/police (016, §32-35). Flag `crime.enabled` (default false).

Daily phases:
- run_crime_phase: theft/vandalism commits + witness detection/memory/report.
- run_police_phase: resolve reported crimes (fine via ledger or prison),
  release prisoners whose term ended.

All deterministic: rng streams keyed by (world_id, day, scope, index).
"""
import hashlib

from sqlalchemy.orm import Session

from app.db.models import (
    Account,
    Character,
    CharacterNeeds,
    Crime,
    Location,
    Memory,
    Organization,
    WorldEvent,
    WorldObject,
)
from app.events.events import EventType, log_event


def _rng_flag(world_id: str, day: int, scope: str, index: int) -> float:
    digest = hashlib.sha256(
        f"{world_id}:{day}:{scope}:{index}".encode()
    ).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def police_org(session: Session, world_id: str) -> Organization | None:
    return (
        session.query(Organization)
        .filter_by(world_id=world_id, type="security")
        .first()
    )


def police_location(session: Session, world_id: str, settings) -> Location | None:
    org = police_org(session, world_id)
    if org is not None and org.location_id is not None:
        return session.get(Location, org.location_id)
    return (
        session.query(Location)
        .filter_by(world_id=world_id, type="settlement")
        .first()
    )


def _officer_id(session: Session, world_id: str) -> str | None:
    org = police_org(session, world_id)
    if org is not None and org.leader_character_id:
        return org.leader_character_id
    npc = (
        session.query(Character)
        .filter_by(world_id=world_id)
        .filter(Character.user_id.is_(None), Character.alive)
        .order_by(Character.id)
        .first()
    )
    return npc.id if npc is not None else None


def _witness_and_report(
    session: Session, world_id: str, day: int, game_timestamp: int,
    crime: Crime, actor: Character, settings,
) -> None:
    """§34: NPCs present may notice; any witness files a report."""
    witnesses = (
        session.query(Character)
        .filter_by(world_id=world_id, location_id=actor.location_id)
        .filter(Character.user_id.is_(None), Character.alive,
                Character.id != actor.id)
        .all()
    )
    event = (
        session.query(WorldEvent)
        .filter_by(world_id=world_id, event_type="CRIME_COMMITED")
        .order_by(WorldEvent.id.desc())
        .first()
    )
    noticed = []
    for i, w in enumerate(witnesses):
        if _rng_flag(world_id, day, "witness", crime.id * 100 + i) \
                < settings.crime.detection_base:
            noticed.append(w)
            session.add(Memory(
                world_id=world_id, character_id=w.id,
                event_id=event.id if event else 0,
                memory_type="crime_witness", importance=8,
                emotional_valence=-0.6,
                summary=f"Видел(а), как {actor.first_name} {actor.last_name} "
                        f"{crime.crime_type} в день {day}",
                created_at=game_timestamp,
            ))
    if noticed:
        crime.status = "reported"
        log_event(
            session, world_id, game_timestamp, EventType.CRIME_REPORTED,
            actor_id=noticed[0].id,
            payload={"crime_id": crime.id, "crime_type": crime.crime_type,
                     "suspect": actor.id, "witnesses": [w.id for w in noticed]},
        )


def _maybe_steal(
    session: Session, world_id: str, day: int, game_timestamp: int,
    npc: Character, needs: CharacterNeeds, settings,
) -> None:
    from app.economy import get_balance

    if needs.hunger >= settings.crime.theft_hunger_threshold:
        return
    acc = (
        session.query(Account)
        .filter_by(world_id=world_id, owner_type="character", owner_id=npc.id)
        .first()
    )
    if acc is not None and get_balance(session, acc.id) \
            >= settings.crime.theft_money_threshold:
        return
    if _rng_flag(world_id, day, f"theft:{npc.id}", 0) \
            >= settings.crime.theft_chance:
        return
    food = (
        session.query(WorldObject)
        .filter(
            WorldObject.location_id == npc.location_id,
            WorldObject.quantity > 0,
            WorldObject.object_type.in_(
                ["food_stock", "food_bread", "food_canned",
                 "prepared_meal", "fish", "berries"]),
        )
        .order_by(WorldObject.id)
        .first()
    )
    if food is None:
        return
    object_id = food.id
    food.quantity = max(0, food.quantity - 1)  # row kept (M11 pattern)
    crime = Crime(
        world_id=world_id, crime_type="theft", actor_character_id=npc.id,
        location_id=npc.location_id, target_object_id=object_id,
        day=day, status="unreported", created_at=game_timestamp,
    )
    session.add(crime)
    session.flush()
    log_event(
        session, world_id, game_timestamp, EventType.CRIME_COMMITED,
        actor_id=npc.id,
        payload={"type": "theft", "object_id": object_id,
                 "object_type": food.object_type, "day": day},
    )
    _witness_and_report(
        session, world_id, day, game_timestamp, crime, npc, settings)


def _maybe_vandalize(
    session: Session, world_id: str, day: int, game_timestamp: int,
    npc: Character, needs: CharacterNeeds, settings,
) -> None:
    if needs.social >= settings.crime.vandalism_social_threshold:
        return
    if _rng_flag(world_id, day, f"vandalism:{npc.id}", 0) \
            >= settings.crime.vandalism_chance:
        return
    target = (
        session.query(WorldObject)
        .filter(
            WorldObject.location_id == npc.location_id,
            WorldObject.condition > 30,
        )
        .order_by(WorldObject.id)
        .first()
    )
    if target is None:
        return
    target.condition = max(0, target.condition - 30)
    crime = Crime(
        world_id=world_id, crime_type="vandalism",
        actor_character_id=npc.id, location_id=npc.location_id,
        target_object_id=target.id, day=day, status="unreported",
        created_at=game_timestamp,
    )
    session.add(crime)
    session.flush()
    log_event(
        session, world_id, game_timestamp, EventType.CRIME_COMMITED,
        actor_id=npc.id,
        payload={"type": "vandalism", "object_id": target.id, "day": day},
    )
    _witness_and_report(
        session, world_id, day, game_timestamp, crime, npc, settings)


def run_crime_phase(
    session: Session, world_id: str, day: int, game_timestamp: int, settings
) -> int:
    """Commit phase + witnesses. Returns number of crimes."""
    if not settings.crime.enabled:
        return 0
    npcs = (
        session.query(Character)
        .filter_by(world_id=world_id)
        .filter(Character.user_id.is_(None), Character.alive)
        .all()
    )
    count = 0
    for npc in npcs:
        if npc.prison_until_day is not None and npc.prison_until_day > day:
            continue
        needs = (
            session.query(CharacterNeeds)
            .filter_by(character_id=npc.id)
            .first()
        )
        if needs is None:
            continue
        before = len(session.new) + len(session.dirty)
        _maybe_steal(session, world_id, day, game_timestamp, npc, needs, settings)
        _maybe_vandalize(session, world_id, day, game_timestamp, npc, needs, settings)
        if len(session.new) + len(session.dirty) > before:
            count += 1
    session.flush()
    return count


def run_police_phase(
    session: Session, world_id: str, day: int, game_timestamp: int, settings
) -> dict:
    """§35 resolution: reported crimes -> fine (ledger) or prison;
    release prisoners whose term ended. Returns counters."""
    counters = {"fined": 0, "arrested": 0, "released": 0}
    if not settings.crime.enabled:
        return counters
    from app.economy import get_balance
    from app.economy import transfer as economy_transfer

    officer = _officer_id(session, world_id)

    # 1. Release prisoners whose term ended today (or earlier)
    prisoners = (
        session.query(Character)
        .filter_by(world_id=world_id)
        .filter(Character.alive,
                Character.prison_until_day.isnot(None),
                Character.prison_until_day <= day)
        .all()
    )
    for p in prisoners:
        p.prison_until_day = None
        p.location_id = p.home_location_id or p.location_id
        p.updated_at = game_timestamp
        log_event(
            session, world_id, game_timestamp, EventType.RELEASED,
            actor_id=p.id,
            payload={"day": day, "officer": officer},
        )
        counters["released"] += 1

    # 2. Resolve reported crimes
    org = police_org(session, world_id)
    reported = (
        session.query(Crime)
        .filter_by(world_id=world_id, status="reported")
        .order_by(Crime.id)
        .all()
    )
    for crime in reported:
        actor = session.get(Character, crime.actor_character_id)
        if actor is None or not actor.alive:
            crime.status = "resolved"
            crime.resolution = "fine"  # dead suspects: case closed
            continue
        fine = settings.crime.fine_theft if crime.crime_type == "theft" \
            else settings.crime.fine_vandalism
        char_acc = (
            session.query(Account)
            .filter_by(world_id=world_id, owner_type="character",
                       owner_id=actor.id)
            .first()
        )
        org_acc = (
            session.query(Account)
            .filter_by(world_id=world_id, owner_type="organization",
                       owner_id=str(org.id))
            .first()
        ) if org is not None else None
        if char_acc is not None and org_acc is not None \
                and get_balance(session, char_acc.id) >= fine:
            economy_transfer(
                session, world_id, game_timestamp,
                char_acc.id, org_acc.id, fine, reason="FINE_PAID",
            )
            crime.status = "resolved"
            crime.resolution = "fine"
            log_event(
                session, world_id, game_timestamp, EventType.FINE_PAID,
                actor_id=actor.id,
                payload={"crime_id": crime.id, "amount": fine,
                         "officer": officer},
            )
            counters["fined"] += 1
        else:
            prison_loc = police_location(session, world_id, settings)
            actor.prison_until_day = day + settings.crime.prison_days
            if prison_loc is not None:
                actor.location_id = prison_loc.id
            actor.updated_at = game_timestamp
            crime.status = "resolved"
            crime.resolution = "prison"
            log_event(
                session, world_id, game_timestamp, EventType.ARRESTED,
                actor_id=actor.id,
                payload={"crime_id": crime.id,
                         "release_day": actor.prison_until_day,
                         "officer": officer},
            )
            counters["arrested"] += 1
    session.flush()
    return counters
