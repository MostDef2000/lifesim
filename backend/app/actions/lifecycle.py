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

    elif task_type == "SOCIALIZE":
        from app.db.models import Character, Relationship

        # Target locked at decision time (validator enqueued the MOVE toward
        # them). The interaction applies at completion if the target is still
        # alive — physical co-location is a decision-time property; with
        # synchronized schedules the target may legally move during the
        # 30-minute window (known M2 simplification, documented in spec).
        target_id = params.get("target_id")
        target_char = (
            session.query(Character)
            .filter_by(id=target_id, world_id=world_id)
            .first()
            if target_id else None
        )
        if not target_char or not target_char.alive:
            # Enqueued target died: fall back to the best co-located alive
            # candidate; if none, complete as a no-op (restore already
            # applied, no events, no mutation).
            co_located = session.query(Character).filter(
                Character.world_id == world_id,
                Character.alive,
                Character.location_id == character.location_id,
                Character.id != character.id,
            ).all()
            scored = []
            for c in co_located:
                a, b = sorted([character.id, c.id])
                rel = session.query(Relationship).filter_by(
                    world_id=world_id, character_a=a, character_b=b
                ).first()
                aff = (
                    rel.affection if rel else settings.social.initial_affection
                )
                scored.append((aff, c.id, c))
            scored.sort(key=lambda x: (-x[0], x[1]))
            target_char = next(
                (c for aff, _cid, c in scored
                 if aff > settings.social.refusal_threshold),
                None,
            )
        if target_char is None:
            pass
        else:
            target_id = target_char.id
            # 1. Lazy row creation & Mutation
            a, b = sorted([character.id, target_id])
            rel = session.query(Relationship).filter_by(
                world_id=world_id, character_a=a, character_b=b
            ).first()

            if not rel:
                rel = Relationship(
                    world_id=world_id, character_a=a, character_b=b,
                    affection=settings.social.initial_affection, updated_at=game_timestamp
                )
                session.add(rel)
                session.flush()

            # affection_before from the live row (stale params ignored).
            affection_before = rel.affection

            # Normal path vs Conflict path — decided by the FRESH value.
            if affection_before >= -30:
                rel.affection = max(-100.0, min(100.0, rel.affection + 2.0))
                affection_delta = 2.0
            else:
                # Conflict path
                rel.affection = max(-100.0, min(100.0, rel.affection - 5.0))
                affection_delta = -5.0

                # Both participants get stress += 5.0
                from app.db.models import CharacterHealth
                for cid in [character.id, target_id]:
                    health = (
                        session.query(CharacterHealth)
                        .filter_by(character_id=cid)
                        .first()
                    )
                    if health is None:
                        continue  # defensive: participant without a health row
                    health.stress = max(0.0, min(100.0, health.stress + 5.0))

                conflict_event_id = log_event(
                    session, world_id, game_timestamp, EventType.CONFLICT,
                    actor_id=character.id, target_id=target_id,
                    payload={
                        "affection_before": affection_before,
                        "affection_after": rel.affection,
                        "stress_delta": 5.0
                    }
                )

                # M3 R4: enforce the no_conflict law (gate R1 inside — zero
                # DB access when org laws are disabled; byte-identity kept).
                from app.policies.org import enforce_no_conflict
                enforce_no_conflict(
                    session, world_id, settings, game_timestamp,
                    conflict_event_id, [character.id, target_id]
                )

                # M4 R7: conflict triggers an LLM decision request per
                # participant (gate R1 inside; <= 1 per character per day).
                from app.ai.gateway import enqueue_decide
                for cid in (character.id, target_id):
                    enqueue_decide(
                        session, world_id, settings, cid, conflict_event_id,
                        EventType.CONFLICT.value, game_timestamp
                    )

            affection_after = rel.affection
            rel.updated_at = game_timestamp

            # 2. Events
            # SOCIAL_INTERACTION
            log_event(
                session, world_id, game_timestamp, EventType.SOCIAL_INTERACTION,
                actor_id=character.id, target_id=target_id,
                payload={
                    "target_id": target_id,
                    "duration": task.ends_at - task.started_at,
                    "affection_delta": affection_delta,
                    "affection_after": affection_after
                }
            )

            # RELATIONSHIP_CHANGED (band crossing)
            def get_band(aff):
                if aff < -30:
                    return "conflicted"
                if aff < 10:
                    return "stranger"
                if aff < 50:
                    return "acquaintance"
                return "friend"

            band_before = get_band(affection_before)
            band_after = get_band(affection_after)
            if band_before != band_after:
                event_id = log_event(
                    session, world_id, game_timestamp, EventType.RELATIONSHIP_CHANGED,
                    actor_id=character.id, target_id=target_id,
                    payload={
                        "band": band_after,
                        "affection_before": affection_before,
                        "affection_after": affection_after
                    }
                )
                # Record in RelationshipEvent table
                from app.db.models import RelationshipEvent
                session.add(RelationshipEvent(
                    world_id=world_id, character_a=a, character_b=b,
                    event_type=EventType.RELATIONSHIP_CHANGED.value,
                    impact=affection_delta,
                    event_id=event_id,
                    game_timestamp=game_timestamp
                ))

    task.status = "completed"

    task.completed_at = game_timestamp
    _settle_goal_for_task(session, task, "done", game_timestamp)  # M5 R6
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
    _settle_goal_for_task(session, task, "failed", game_timestamp)  # M5 R6
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


# ---------- M5 (SPEC §104/R4/R6): player goals ----------

GOAL_ACTION_MAP = {
    "travel_to": "MOVE",
    "socialize_with": "SOCIALIZE",
    "acquire_items": "BUY_ITEM",
}


def _convert_queued_goal(
    session: Session, world_id: str, char: Character,
    game_timestamp: int, settings: Settings
):
    """
    GUIDED mode: convert the oldest queued player goal into a task chain.

    Returns the action CharacterTask or None (nothing queued / validation
    failed — the failed goal is settled and utility takes over).
    """
    from app.db.models import CharacterGoal

    goal = (
        session.query(CharacterGoal)
        .filter_by(world_id=world_id, character_id=char.id, status="queued")
        .order_by(CharacterGoal.id)
        .first()
    )
    if goal is None:
        return None

    action_type = GOAL_ACTION_MAP.get(goal.goal_type)
    if action_type is None:
        goal.status = "failed"
        goal.updated_at = game_timestamp
        return None

    # Game-fact validation for the goal (П1: same facts the validators
    # enforce; need thresholds are NOT applied — player intent overrides).

    if goal.goal_type == "travel_to":
        from app.db.models import Location
        dest_id = (goal.params or {}).get("destination_location_id")
        dest = session.get(Location, int(dest_id)) if dest_id else None
        if dest is None or dest.world_id != world_id:
            goal.status = "failed"
            goal.updated_at = game_timestamp
            return None
        path, total_min = find_path(session, world_id, char.location_id, dest.id)
        if not path:
            goal.status = "failed"
            goal.updated_at = game_timestamp
            return None
        action_task = enqueue_task(
            session, world_id, char, "MOVE", "goal",
            {"path": path, "total_minutes": total_min, "from": char.location_id},
            game_timestamp, settings,
        )

    elif goal.goal_type == "socialize_with":
        from app.db.models import Character as _Character
        target = (
            session.query(_Character)
            .filter_by(
                id=(goal.params or {}).get("target_character_id"),
                world_id=world_id,
            )
            .first()
        )
        if target is None or not target.alive or target.id == char.id:
            goal.status = "failed"
            goal.updated_at = game_timestamp
            return None
        action_task = enqueue_task(
            session, world_id, char, "SOCIALIZE", "goal",
            {"target_id": target.id}, game_timestamp, settings,
        )

    else:  # acquire_items
        from app.db.models import Location as _Location
        from app.db.models import Organization, WorldObject
        from app.economy import get_balance, open_account

        item_key = (goal.params or {}).get("item_key")
        shop_loc = (
            session.query(_Location)
            .filter_by(world_id=world_id, type="shop")
            .order_by(_Location.id)
            .first()
        )
        shop_org = (
            session.query(Organization)
            .filter_by(world_id=world_id, type="business")
            .order_by(Organization.id)
            .first()
        )
        stock = None
        if shop_loc is not None and shop_org is not None and item_key:
            stock = (
                session.query(WorldObject)
                .filter(
                    WorldObject.world_id == world_id,
                    WorldObject.location_id == shop_loc.id,
                    WorldObject.owner_organization_id == shop_org.id,
                    WorldObject.quantity >= 1,
                )
                .order_by(WorldObject.id)
                .all()
            )
            exact = [o for o in stock if o.object_type == item_key]
            if not exact:
                prefix = [o for o in stock if o.object_type.startswith(item_key)]
                stock = prefix or None
        if stock is None:
            goal.status = "failed"
            goal.updated_at = game_timestamp
            return None
        stock_obj = stock[0]
        price = settings.economy.prices.get(stock_obj.object_type, 0)
        acc = open_account(session, world_id, "character", char.id)
        if get_balance(session, acc.id) < price:
            goal.status = "failed"
            goal.updated_at = game_timestamp
            return None
        path, total_min = find_path(session, world_id, char.location_id, shop_loc.id)
        if path:
            enqueue_task(
                session, world_id, char, "MOVE", "goal",
                {"path": path, "total_minutes": total_min, "from": char.location_id},
                game_timestamp, settings,
            )
        else:
            goal.status = "failed"
            goal.updated_at = game_timestamp
            return None
        action_task = enqueue_task(
            session, world_id, char, "BUY_ITEM", "goal",
            {"object_id": stock_obj.id, "price": price},
            game_timestamp, settings,
        )

    goal.status = "active"
    goal.task_id = action_task.id if action_task is not None else None
    goal.updated_at = game_timestamp
    return action_task


def _settle_goal_for_task(
    session: Session, task: CharacterTask, status: str, game_timestamp: int
) -> None:
    """On task completion/failure, settle the owning player goal (R6)."""
    from app.db.models import CharacterGoal

    goal = session.query(CharacterGoal).filter_by(task_id=task.id).first()
    if goal is not None:
        goal.status = status
        goal.updated_at = game_timestamp


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
                    CharacterTask.status != "cancelled",  # M5: cancelled ≠ scheduled
                )
                .scalar()
            )
            start_at = last_end if last_end is not None else 0
        activated = activate(session, char, start_at, settings)
        if activated:
            continue

        # Nothing planned: M5 player control gates first (SPEC §104/R1/R4)
        if getattr(char, "user_id", None) is not None:
            if char.control_mode == "DIRECT":
                # Player commands directly; utility AI must not assign tasks.
                # Needs still decay via needs_tick; existing tasks finish.
                return
            if char.control_mode == "GUIDED":
                goal_task = _convert_queued_goal(
                    session, world_id, char, start_at, settings
                )
                if goal_task is not None:
                    continue
                # No queued goal: fall through to utility (GUIDED = goals + AI)

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
