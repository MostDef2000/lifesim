from typing import Iterator

from sqlalchemy.orm import Session

from app.config.config import Settings
from app.db.models import Character, CharacterNeeds


def iter_alive_characters(session: Session, world_id: str) -> Iterator[Character]:
    """
    Helper to iterate over all alive characters in a world, sorted by ID.
    """
    return (
        session.query(Character)
        .filter_by(world_id=world_id, alive=True)
        .order_by(Character.id)
        .all()
    )

def apply_needs_decay(
    session: Session, world_id: str, game_timestamp: int,
    minutes: int, settings: Settings
):
    """
    Decays hunger, thirst, energy, social for each alive character.
    Clamps [0, 100].

    Future: Restore-from-actions will be integrated here or as a separate call.
    """
    characters = iter_alive_characters(session, world_id)

    # Get decay rates from config
    rates = settings.needs.decay_rates

    for char in characters:
        needs = session.query(CharacterNeeds).filter_by(character_id=char.id).one()

        # Decay dynamic needs
        needs.hunger = max(0.0, needs.hunger - rates.hunger * minutes)
        needs.thirst = max(0.0, needs.thirst - rates.thirst * minutes)
        needs.energy = max(0.0, needs.energy - rates.energy * minutes)
        needs.social = max(0.0, needs.social - rates.social * minutes)

        needs.updated_at = game_timestamp
        session.flush()
