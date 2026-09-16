import json
from typing import Any, Dict, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.actions.utility import choose_action
from app.config.config import Settings
from app.db.models import (
    Account,
    Character,
    CharacterTask,
    Organization,
)
from app.economy import transfer as economy_transfer
from app.events.events import EventType, log_event
from app.inventory import adjust_resource, consume_object, transfer_object
from app.world.seed_world import find_path


def enqueue_task(
    session: Session,
    world_id: str,
    character: Character,
    task_type: str,
    source: str,
    params: Dict[str, Any],
    timestamp: int,
    settings: Settings
) -> Optional[CharacterTask]:
    """
    Adds a task to the character's planned queue.
    Rejects if an active task already exists.
    """
    active_task = session.query(CharacterTask).filter_by(
        character_id=character.id, status="active"
    ).first()
    if active_task:
        return None

    task = CharacterTask(
        character_id=character.id,
        priority=0, # Simplified for M1
        task_type=task_type,
        status="planned",
        source=source,
        parameters=json.dumps(params),
        created_at=timestamp
    )
    session.add(task)
    session.flush()
    return task

def activate(session: Session, character: Character, timestamp: int, settings: Settings):
    """
    Activates the next planned task for a character.
    """
    next_task = session.query(CharacterTask).filter_by(
        character_id=character.id, status="planned"
    ).order_by(CharacterTask.id).first()

    if not next_task:
        return None

    params = json.loads(next_task.parameters)
    duration = 0

    if next_task.task_type == "MOVE":
        # Duration is the precomputed total travel time along the path
        duration = params.get("total_minutes", 0)
    else:
        # duration_minutes from config
        duration = getattr(settings.actions, next_task.task_type).duration_minutes

    next_task.status = "active"
    next_task.started_at = timestamp
    next_task.ends_at = timestamp + duration
    session.flush()
    return next_task

def complete_task(
    session: Session,
    world_id: str,
    character: Character,
    task: CharacterTask,
    game_timestamp: int,
    settings: Settings
):
    """
    Performs the mutation logic for completed tasks.
    """
    params = json.loads(task.parameters)
    task_type = task.task_type

    if task_type == "SLEEP":
        # Restore already handled gradually in progress_tick
        pass

    elif task_type == "EAT":
        obj_id = params.get("object_id")
        if obj_id:
            # Guard: Ensure object exists and has quantity
            from app.db.models import WorldObject
            obj = session.query(WorldObject).get(obj_id)
            if not obj or obj.quantity < 1:
                fail_task(session, world_id, character, task, game_timestamp, "food gone")
                return

            consume_object(session, world_id, game_timestamp, obj_id, 1, character.id)

    elif task_type == "DRINK":
        from app.db.models import ResourceBalance
        community_org = (
            session.query(Organization)
            .filter_by(world_id=world_id, type="community")
            .first()
        )
        drink_amount = settings.economy.water_per_drink
        if community_org:
            # Guard: re-check balance at completion time (validator raced
            # with other characters in the same tick).
            water_balance = (
                session.query(ResourceBalance)
                .filter_by(
                    world_id=world_id, resource_key="water",
                    owner_type="organization", owner_id=str(community_org.id)
                )
                .first()
            )
            if not water_balance or water_balance.quantity < drink_amount:
                fail_task(session, world_id, character, task, game_timestamp, "water gone")
                return
            adjust_resource(
                session, world_id, "water", "organization", str(community_org.id),
                -drink_amount, game_timestamp
            )

    elif task_type == "WORK":
        # Material results handled by daily tick (salary)
        pass

    elif task_type == "MOVE":
        # Final path node
        path = params.get("path", [])
        if path:
            character.location_id = path[-1]
            log_event(
                session, world_id, game_timestamp, EventType.CHARACTER_MOVED,
                actor_id=character.id,
                payload={
                    "from": params.get("from"), "to": character.location_id,
                    "minutes": params.get("total_minutes")
                }
            )

    elif task_type == "BUY_ITEM":
        obj_id = params.get("object_id")
        price = params.get("price", 0)
        if obj_id:
            # Guards: re-check funds and stock at completion time (the
            # validator raced with other characters in the same tick).
            from app.db.models import WorldObject
            from app.economy import get_balance
            stock_obj = session.query(WorldObject).get(obj_id)
            if not stock_obj or stock_obj.quantity < 1:
                fail_task(session, world_id, character, task, game_timestamp, "stock gone")
                return

            char_acc = (
                session.query(Account)
                .filter_by(owner_type="character", owner_id=character.id)
                .one()
            )
            if get_balance(session, char_acc.id) < price:
                fail_task(session, world_id, character, task, game_timestamp, "insufficient funds")
                return

            shop_org = (
                session.query(Organization)
                .filter_by(world_id=world_id, type="business")
                .first()
            )
            shop_acc = (
                session.query(Account)
                .filter_by(owner_type="organization", owner_id=str(shop_org.id))
                .one()
            )

            economy_transfer(
                session, world_id, game_timestamp, char_acc.id, shop_acc.id,
                price, reason="PURCHASE"
            )

            # 2. Transfer object
            transfer_object(
                session, world_id, game_timestamp, obj_id, 1,
                new_owner_character_id=character.id, location_id=character.location_id
            )

            log_event(
                session, world_id, game_timestamp, EventType.PURCHASE,
                actor_id=character.id,
                payload={"object_id": obj_id, "price": price}
            )

    task.status = "completed"
    task.completed_at = game_timestamp
    log_event(
        session, world_id, game_timestamp, EventType.TASK_COMPLETED,
        actor_id=character.id,
        payload={"task_type": task_type}
    )
    session.flush()

def fail_task(
    session: Session, world_id: str, character: Character,
    task: CharacterTask, game_timestamp: int, reason: str
):
    task.status = "failed"
    log_event(
        session, world_id, game_timestamp, EventType.TASK_FAILED,
        actor_id=character.id,
        payload={"task_type": task.task_type, "reason": reason}
    )
    session.flush()

def _enqueue_decision(
    session: Session, world_id: str, char: Character,
    action_type: str, params: dict, needs_move_id, game_timestamp: int,
    settings: Settings
) -> None:
    """Enqueue the chosen action, with a MOVE precondition when needed."""
    if needs_move_id is not None:
        path, total_min = find_path(
            session, world_id, char.location_id, needs_move_id
        )
        if path:
            enqueue_task(
                session, world_id, char, "MOVE", "need",
                {
                    "path": path, "total_minutes": total_min,
                    "from": char.location_id
                },
                game_timestamp, settings
            )
            # The target action is enqueued after the MOVE and activates
            # only once the MOVE completes.
            enqueue_task(
                session, world_id, char, action_type,
                "need", params, game_timestamp, settings
            )
            return
        # Path not found, fallback to IDLE
        enqueue_task(
            session, world_id, char, "IDLE", "need", {},
            game_timestamp, settings
        )
        return
    enqueue_task(
        session, world_id, char, action_type,
        "need", params, game_timestamp, settings
    )


def _apply_gradual_restore(
    session: Session, char: Character, task: CharacterTask,
    window_start: int, window_end: int, settings: Settings
) -> None:
    """Apply need restore in bulk for [window_start, window_end).

    Idempotent: the task's parameters carry a `restored_until` marker so
    repeated progress_tick calls never double-apply the same window.
    Bulk application is equivalent to per-minute application because all
    recovery rates are constant over the window and clamps are monotone.
    """
    from app.db.models import CharacterNeeds

    params = json.loads(task.parameters)
    restored_until = params.get("restored_until", task.started_at)
    if window_end <= restored_until:
        return
    minutes = window_end - restored_until
    recovery = getattr(settings.needs.recovery_rates, task.task_type, None)
    if recovery:
        needs = session.query(CharacterNeeds).filter_by(character_id=char.id).one()
        for need_name, rate in recovery.items():
            current_val = getattr(needs, need_name)
            setattr(needs, need_name, min(100.0, current_val + rate * minutes))
    params["restored_until"] = window_end
    task.parameters = json.dumps(params)


def _advance_character(
    session: Session, world_id: str, char: Character,
    now: int, settings: Settings
) -> None:
    """Catch up one character to `now`: bulk restore, task boundaries,
    then activate/decide until the character has a future-dated active task.

    Completion takes precedence over timeout: a task whose ends_at has
    passed completed in time regardless of when the tick observes it.
    Timeout only fires when the configured duration itself exceeds the
    per-action max_duration (config drift safety net).

    Task chaining: tasks completed inside the past window finish AT their
    own ends_at, and their successors start at that completion time (not at
    `now`) — so a 1440-minute step advances the character's whole daily
    schedule back-to-back instead of one small task per step.
    """

    # Completion time of the most recent boundary processed in this catch-up
    # call; None until the first in-window completion. Decisions and
    # activations after a completion are dated at chain_time so consecutive
    # tasks are time-consistent. Always <= now by construction.
    chain_time: int | None = None

    for _ in range(1000):  # safety cap against pathological loops
        active_task = session.query(CharacterTask).filter_by(
            character_id=char.id, status="active"
        ).first()

        if active_task:
            # IDLE interrupt: only IDLE may be preempted by a better action
            if active_task.task_type == "IDLE" and now < active_task.ends_at:
                action_type, params, needs_move_id = choose_action(
                    session, world_id, char, now, settings
                )
                # Guard: only interrupt when the better action is actually
                # reachable. If the path does not exist, keep resting IDLE —
                # otherwise interrupt→IDLE-fallback→interrupt loops forever.
                if (
                    action_type != "IDLE"
                    and needs_move_id is not None
                    and find_path(
                        session, world_id, char.location_id, needs_move_id
                    )[0] is None
                ):
                    return
                if action_type != "IDLE":
                    fail_task(
                        session, world_id, char, active_task, now, "interrupted"
                    )
                    _enqueue_decision(
                        session, world_id, char, action_type, params,
                        needs_move_id, now, settings
                    )
                    continue

            # Bulk restore up to completion or now, whichever is earlier
            effective_end = min(now, active_task.ends_at)
            _apply_gradual_restore(
                session, char, active_task,
                active_task.started_at, effective_end, settings
            )

            if now >= active_task.ends_at:
                complete_task(
                    session, world_id, char, active_task,
                    active_task.ends_at, settings
                )
                chain_time = active_task.ends_at
                continue

            max_dur = getattr(settings.actions, active_task.task_type).max_duration_minutes
            if active_task.ends_at - active_task.started_at > max_dur:
                fail_task(session, world_id, char, active_task, now, "timeout")
                continue

            # Still in progress and healthy — done for this character
            return

        # No active task: start the next one at the last in-window boundary
        # (chain_time) so back-to-back chains stay time-consistent. Without
        # an in-window boundary, resume from the character's last recorded
        # task end (0 for a fresh character) so the first catch-up of a new
        # character reconstructs its schedule from genesis instead of
        # starting at `now` with already-collapsed needs.
        if chain_time is not None:
            start_at = chain_time
        else:
            last_end = (
                session.query(func.max(CharacterTask.ends_at))
                .filter(
                    CharacterTask.character_id == char.id,
                    CharacterTask.ends_at.isnot(None),
                )
                .scalar()
            )
            start_at = last_end if last_end is not None else 0
        activated = activate(session, char, start_at, settings)
        if activated:
            continue

        # Nothing planned: decide a new action and enqueue it
        action_type, params, needs_move_id = choose_action(
            session, world_id, char, start_at, settings
        )
        _enqueue_decision(
            session, world_id, char, action_type, params,
            needs_move_id, start_at, settings
        )


def progress_tick(session: Session, world_id: str, game_timestamp: int, settings: Settings):
    """
    Main progression logic for characters (catch-up semantics).

    Safe to call with arbitrary game_timestamp jumps: each character's
    active tasks are restored in bulk up to min(now, ends_at) and completed
    when due, then the next planned task activates or a new decision is
    made. Repeated calls with the same timestamp are no-ops thanks to the
    `restored_until` marker.
    """
    from app.characters.needs import iter_alive_characters

    for char in iter_alive_characters(session, world_id):
        _advance_character(session, world_id, char, game_timestamp, settings)

    session.flush()
