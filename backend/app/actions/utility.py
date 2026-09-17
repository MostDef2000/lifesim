from typing import Any, Dict, Optional, Tuple

from sqlalchemy.orm import Session

from app.actions.validators import validate
from app.config.config import Settings
from app.db.models import Character, CharacterNeeds


def choose_action(
    session: Session,
    world_id: str,
    character: Character,
    timestamp: int,
    settings: Settings
) -> Tuple[str, Dict[str, Any], Optional[int]]:
    """
    Utility AI: chooses the best action based on needs.
    Tie-break by registry order.
    Returns: (action_type, params, target_location_id)
    """
    from app.actions.registry import ACTION_REGISTRY

    best_action = "IDLE"
    best_score = -1.0
    best_params = {}
    best_loc = None

    # We iterate in the order of the registry for tie-breaking
    # Needs are read-only during scoring, query once before the loop.
    needs = session.query(CharacterNeeds).filter_by(character_id=character.id).one()

    # Per-decision read cache shared by all validate() calls in this loop.
    # A decision never mutates the world between branch validations, so
    # memoizing org/location/relationship lookups here is safe (R10).
    decision_ctx = {}

    for action_def in ACTION_REGISTRY:
        action_type = action_def.name

        # skip MOVE - it's only a precondition
        if action_type == "MOVE":
            continue

        ok, reason, needs_move, params = validate(
            session, world_id, character, action_type, timestamp, settings,
            needs=needs, ctx=decision_ctx,
        )

        if not ok:
            continue

        # Score = Σ (utility.weights[need] × (100 − need_value)) + base_utility_weight
        score = 0.0

        if action_type == "SLEEP":
            score += settings.utility.weights.energy * (100.0 - needs.energy)
            score += settings.actions.SLEEP.base_utility_weight
        elif action_type == "EAT":
            score += settings.utility.weights.hunger * (100.0 - needs.hunger)
            score += settings.actions.EAT.base_utility_weight
        elif action_type == "DRINK":
            score += settings.utility.weights.thirst * (100.0 - needs.thirst)
            score += settings.actions.DRINK.base_utility_weight
        elif action_type == "WORK":
            # WORK restores nothing in M1 (recovery_rates has no WORK key),
            # so it scores its base weight only. It wins over IDLE when
            # nothing else is needed, and never beats survival actions.
            score += settings.actions.WORK.base_utility_weight
        elif action_type == "BUY_ITEM":
            # Buying food restores hunger
            score += settings.utility.weights.hunger * (100.0 - needs.hunger)
            score += settings.actions.BUY_ITEM.base_utility_weight
        elif action_type == "SOCIALIZE":
            if settings.social.enabled:
                score += settings.utility.weights.social * (100.0 - needs.social)
                base_w = (
                    settings.actions.SOCIALIZE.base_utility_weight
                    if settings.actions.SOCIALIZE else 0.3
                )
                score += base_w
        elif action_type == "IDLE":
            score = settings.actions.IDLE.base_utility_weight

        if score > best_score:
            best_score = score
            best_action = action_type
            best_params = params
            best_loc = needs_move

    return best_action, best_params, best_loc
