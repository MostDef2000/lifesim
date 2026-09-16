from sqlalchemy.orm import Session

from app.characters.health import apply_health_decay
from app.config.config import Settings
from app.db.models import (
    Character,
    CharacterHealth,
    CharacterNeeds,
    CharacterTask,
    Location,
    WorldObject,
)


def test_death_process(session: Session, settings: Settings, world_id: str):
    # Setup: character with low health
    char = Character(
        id="npc_die", world_id=world_id, first_name="D", last_name="D",
        birth_date="2000-01-01",
                     age=20, sex="M", alive=True, location_id=1, created_at=0, updated_at=0)
    session.add(char)
    session.flush()

    # Critical needs
    needs = CharacterNeeds(character_id=char.id, hunger=0.0, thirst=0.0, energy=0.0,
                           hygiene=100.0, comfort=100.0, social=100.0, safety=100.0,
                           entertainment=100.0, privacy=100.0, updated_at=0)
    session.add(needs)

    # Low health
    health = CharacterHealth(character_id=char.id, health=1.0, body_temperature=36.6, stress=0.0)
    session.add(health)

    # Active task
    task = CharacterTask(character_id=char.id, priority=1, task_type="WORK",
                         status="active", source="routine", parameters="{}", created_at=0)
    session.add(task)

    # Owned object
    obj = WorldObject(world_id=world_id, object_type="tool", quantity=1,
                      owner_character_id=char.id, location_id=1, object_metadata="{}")
    session.add(obj)

    # Storage location: use the one created by seed_world in the session fixture
    storage = session.query(Location).filter_by(world_id=world_id, type="storage").first()

    session.commit()

    # Trigger health decay: rate 0.01/min × 100 min = 1.0 -> health becomes 0
    apply_health_decay(session, world_id, 100, 100, settings)

    # Verify death state
    char = session.query(Character).filter_by(id="npc_die").one()
    assert char.alive is False
    assert char.death_cause == "critical needs failure"

    # Verify task cancelled
    task = session.query(CharacterTask).filter_by(character_id="npc_die").one()
    assert task.status == "cancelled"

    # Verify object transferred to storage
    obj = session.query(WorldObject).filter_by(object_type="tool").one()
    assert obj.owner_character_id is None
    assert obj.location_id == storage.id
