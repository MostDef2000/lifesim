from sqlalchemy.orm import Session

from app.characters.needs import apply_needs_decay
from app.config.config import Settings
from app.db.models import Character, CharacterNeeds


def test_needs_decay_basic(session: Session, settings: Settings, world_id: str):
    # Create a character
    char = Character(
        id="npc_test", world_id=world_id, first_name="T", last_name="T",
        birth_date="2000-01-01",
                     age=20, sex="M", alive=True, location_id=1, created_at=0, updated_at=0)
    session.add(char)
    session.flush()

    needs = CharacterNeeds(character_id=char.id, hunger=100.0, thirst=100.0, energy=100.0,
                           hygiene=100.0, comfort=100.0, social=100.0, safety=100.0,
                           entertainment=100.0, privacy=100.0, updated_at=0)
    session.add(needs)
    session.commit()

    # Decay for 10 minutes
    apply_needs_decay(session, world_id, 10, 10, settings)

    needs = session.query(CharacterNeeds).filter_by(character_id=char.id).one()

    # hunger_decay = 0.1 (default in config) -> 10 * 0.1 = 1.0
    assert needs.hunger == 99.0
    assert needs.social == 100.0 - (settings.needs.decay_rates.social * 10)

def test_needs_clamp_zero(session: Session, settings: Settings, world_id: str):
    char = Character(
        id="npc_test", world_id=world_id, first_name="T", last_name="T",
        birth_date="2000-01-01",
                     age=20, sex="M", alive=True, location_id=1, created_at=0, updated_at=0)
    session.add(char)
    session.flush()

    needs = CharacterNeeds(character_id=char.id, hunger=1.0, thirst=100.0, energy=100.0,
                           hygiene=100.0, comfort=100.0, social=100.0, safety=100.0,
                           entertainment=100.0, privacy=100.0, updated_at=0)
    session.add(needs)
    session.commit()

    # Decay for 1000 minutes to force 0
    apply_needs_decay(session, world_id, 1000, 1000, settings)

    needs = session.query(CharacterNeeds).filter_by(character_id=char.id).one()
    assert needs.hunger == 0.0
