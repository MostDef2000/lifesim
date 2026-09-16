from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.config.config import Settings
from app.db.models import Character

# Type aliases for the registry
# validator: (session, world_id, character, action_type, ts, settings)
#   -> (ok, reason, needs_move, params)
ValidatorFn = Callable[
    [Session, str, Character, str, int, Settings],
    Tuple[bool, Optional[str], Optional[int], Dict[str, Any]],
]
# complete: (session, world_id, character, task, timestamp, settings) -> None
CompleteFn = Callable[[Session, str, Character, Any, int, Settings], None]


@dataclass
class ActionDefinition:
    name: str
    validator: ValidatorFn
    complete: Optional[CompleteFn] = None
    # 'home', 'kitchen', 'shop', 'well', 'workplace'
    required_location_type: Optional[str] = None
    params_builder: Optional[
        Callable[[Session, Character, Settings], Dict[str, Any]]
    ] = None


# Registry in explicit tie-break order (SLEEP, EAT, DRINK, WORK, MOVE,
# BUY_ITEM, IDLE). Completion mutations are dispatched by task_type inside
# lifecycle.complete_task, so no per-action complete fn is registered.
ACTION_REGISTRY: List[ActionDefinition] = []


def register_action(action: ActionDefinition) -> None:
    ACTION_REGISTRY.append(action)


def initialize_registry() -> None:
    from app.actions.validators import validate

    if ACTION_REGISTRY:
        return

    register_action(ActionDefinition("SLEEP", validate, None, "home"))
    register_action(ActionDefinition("EAT", validate, None, "kitchen"))
    register_action(ActionDefinition("DRINK", validate, None, "well"))
    register_action(ActionDefinition("WORK", validate, None, "workplace"))
    register_action(ActionDefinition("MOVE", validate, None, None))
    register_action(ActionDefinition("BUY_ITEM", validate, None, "shop"))
    register_action(ActionDefinition("IDLE", validate, None, None))


initialize_registry()
