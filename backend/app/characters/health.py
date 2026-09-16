from sqlalchemy.orm import Session

from app.config.config import Settings
from app.db.models import (
    Character,
    CharacterHealth,
    CharacterNeeds,
    CharacterTask,
    Location,
    Organization,
    WorldObject,
)
from app.events.events import EventType, log_event
from app.inventory import transfer_object


def handle_death(
    session: Session, world_id: str, game_timestamp: int,
    character: Character, cause: str
):
    """
    Handles character death:
    - alive=False, death_game_timestamp, death_cause
    - cancel all tasks
    - transfer all owned objects to community storage
    - emit CHARACTER_DIED
    """
    character.alive = False
    character.death_game_timestamp = game_timestamp
    character.death_cause = cause

    # Cancel tasks
    tasks = (
        session.query(CharacterTask)
        .filter_by(character_id=character.id)
        .filter(CharacterTask.status != "completed")
        .all()
    )
    for task in tasks:
        task.status = "cancelled"

    # Find community storage location (type='storage') and the community organization
    storage_loc = session.query(Location).filter_by(world_id=world_id, type="storage").first()
    community_org = (
        session.query(Organization)
        .filter_by(world_id=world_id, type="community")
        .first()
    )

    if storage_loc:
        # Transfer all owned objects to the community (R4: имущество уходит на склад общины)
        owned_objects = session.query(WorldObject).filter_by(owner_character_id=character.id).all()
        for obj in owned_objects:
            transfer_object(
                session, world_id, game_timestamp,
                obj.id, obj.quantity,
                new_owner_character_id=None,
                new_owner_organization_id=community_org.id if community_org else None,
                location_id=storage_loc.id
            )

    log_event(
        session, world_id, game_timestamp, EventType.CHARACTER_DIED,
        actor_id=character.id,
        payload={"cause": cause}
    )
    session.flush()

def apply_health_decay(
    session: Session, world_id: str, game_timestamp: int,
    minutes: int, settings: Settings
):
    """
    Decrements health for characters with critical needs.
    Then processes deaths.
    """
    from app.characters.needs import iter_alive_characters

    characters = iter_alive_characters(session, world_id)

    decay_rate = settings.needs.health_decay_rate
    thresholds = settings.needs.critical_thresholds

    for char in characters:
        needs = session.query(CharacterNeeds).filter_by(character_id=char.id).one()
        health = session.query(CharacterHealth).filter_by(character_id=char.id).one()

        is_critical = (
            needs.hunger < thresholds["hunger"]
            or needs.thirst < thresholds["thirst"]
            or needs.energy < thresholds["energy"]
        )

        if is_critical:
            health.health = max(0.0, health.health - decay_rate * minutes)
            session.flush()

    process_deaths(session, world_id, game_timestamp)

def process_deaths(session: Session, world_id: str, game_timestamp: int):
    """
    Identifies characters with health <= 0 and calls handle_death.
    """
    from app.characters.needs import iter_alive_characters
    from app.db.models import CharacterHealth

    characters = iter_alive_characters(session, world_id)
    for char in characters:
        health = session.query(CharacterHealth).filter_by(character_id=char.id).one()
        if health.health <= 0:
            handle_death(session, world_id, game_timestamp, char, "critical needs failure")
            session.flush()
