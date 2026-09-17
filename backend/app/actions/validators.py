from typing import Any, Dict, Optional, Tuple

from sqlalchemy.orm import Session

from app.config.config import Settings
from app.db.models import Character, CharacterJob, WorldObject


def validate(
    session: Session,
    world_id: str,
    character: Character,
    action_type: str,
    timestamp: int,
    settings: Settings,
    needs=None,
    ctx=None,
) -> Tuple[bool, Optional[str], Optional[int], Dict[str, Any]]:
    """
    Validates if an action is possible for a character.
    Returns: (ok, reason, needs_move_location_id, params)

    `needs` may be passed pre-fetched by the caller (choose_action reads it
    once per decision — R10 batch needs-fetch). When omitted, it is read
    lazily per branch, preserving the legacy standalone-call behavior.

    `ctx` is a per-decision cache dict shared across the validate() calls of
    one choose_action() (registry loop). It memoizes read-only lookups
    (organizations, typed locations, relationship affection). Safe because a
    single decision does not mutate the world between branch validations.
    Standalone calls without ctx keep the legacy one-query-per-branch path.
    """
    if not character.alive:
        return False, "Character is not alive", None, {}

    def _ctx_get(key, factory):
        if ctx is None:
            return factory()
        if key not in ctx:
            ctx[key] = factory()
        return ctx[key]

    def _org_by_type():
        from app.db.models import Organization as _Org
        return {
            org.type: org
            for org in session.query(_Org).filter_by(world_id=world_id).all()
        }

    def _loc_by_type():
        from app.db.models import Location as _Loc
        return {
            loc.type: loc
            for loc in session.query(_Loc).filter_by(world_id=world_id).all()
        }

    # Action-specific validation
    if action_type == "SLEEP":
        # valid if energy < 100
        if needs is None:
            from app.db.models import CharacterNeeds
            needs = session.query(CharacterNeeds).filter_by(character_id=character.id).one()
        if needs.energy >= 100:
            return False, "Energy already full", None, {}

        if character.location_id != character.home_location_id:
            return True, "Needs to move home to sleep", character.home_location_id, {}
        return True, None, None, {}

    elif action_type == "EAT":
        if needs is None:
            from app.db.models import CharacterNeeds
            needs = session.query(CharacterNeeds).filter_by(character_id=character.id).one()
        if needs.hunger >= 100:
            return False, "Hunger already full", None, {}

        # Deterministic rule:
        # 1. Check if character has food in their current location that they own.
        own_food = session.query(WorldObject).filter(
            WorldObject.world_id == world_id,
            WorldObject.location_id == character.location_id,
            WorldObject.owner_character_id == character.id,
            WorldObject.object_type.like("food%")
        ).order_by(WorldObject.id).first()

        if own_food:
            return True, None, None, {"object_id": own_food.id}

        # 2. Check community kitchen stock.
        kitchen_loc = _ctx_get("loc_by_type", _loc_by_type).get("kitchen")
        if not kitchen_loc:
            return False, "No kitchen available in world", None, {}

        # Check if kitchen stock exists (owned by community org)
        community_org = _ctx_get("org_by_type", _org_by_type).get("community")
        if not community_org:
            return False, "No community organization for kitchen stock", None, {}

        kitchen_food = session.query(WorldObject).filter(
            WorldObject.world_id == world_id,
            WorldObject.location_id == kitchen_loc.id,
            WorldObject.owner_organization_id == community_org.id,
            WorldObject.object_type.like("food%")
        ).order_by(WorldObject.id).first()

        if kitchen_food:
            if character.location_id != kitchen_loc.id:
                return True, "Needs to move to kitchen for community food", \
                    kitchen_loc.id, {"object_id": kitchen_food.id}
            return True, None, None, {"object_id": kitchen_food.id}

        return False, "No food available anywhere", None, {}

    elif action_type == "DRINK":
        from app.db.models import ResourceBalance
        if needs is None:
            from app.db.models import CharacterNeeds
            needs = session.query(CharacterNeeds).filter_by(character_id=character.id).one()
        if needs.thirst >= 100:
            return False, "Thirst already full", None, {}

        # Community water ResourceBalance > 0
        community_org = _ctx_get("org_by_type", _org_by_type).get("community")
        if not community_org:
            return False, "No community organization for water", None, {}

        water_balance = (
            session.query(ResourceBalance)
            .filter_by(
                world_id=world_id, resource_key="water",
                owner_type="organization", owner_id=str(community_org.id)
            )
            .first()
        )

        if not water_balance or water_balance.quantity < settings.economy.water_per_drink:
            return False, "No water available in community supply", None, {}

        well_loc = _ctx_get("loc_by_type", _loc_by_type).get("well")
        if not well_loc:
            return False, "No well available in world", None, {}

        if character.location_id != well_loc.id:
            return True, "Needs to move to well to drink", well_loc.id, {}
        return True, None, None, {}

    elif action_type == "WORK":
        job = session.query(CharacterJob).filter_by(character_id=character.id).first()
        if not job:
            return False, "Character has no job", None, {}

        from app.db.models import Job
        job_record = session.query(Job).filter_by(id=job.job_id).one()

        if job_record.location_id and character.location_id != job_record.location_id:
            return True, "Needs to move to workplace", job_record.location_id, {}
        return True, None, None, {}

    elif action_type == "MOVE":
        # Validator exists but utility skips it.
        # Only valid if a destination is provided in params (which is handled by the mover).
        return True, None, None, {}

    elif action_type == "BUY_ITEM":
        # At shop (needs_move=shop)
        shop_loc = _ctx_get("loc_by_type", _loc_by_type).get("shop")
        if not shop_loc:
            return False, "No shop available in world", None, {}

        # Shop org owns stock of any food type at shop location
        shop_org = _ctx_get("org_by_type", _org_by_type).get("business")
        if not shop_org:
            return False, "No business organization for shop", None, {}

        stock_item = session.query(WorldObject).filter(
            WorldObject.world_id == world_id,
            WorldObject.location_id == shop_loc.id,
            WorldObject.owner_organization_id == shop_org.id,
            WorldObject.object_type.like("food%")
        ).order_by(WorldObject.id).first()

        if not stock_item:
            return False, "Shop has no food stock", None, {}

        # Character balance >= price
        # Use economy.get_balance
        from app.economy import get_balance, open_account
        char_acc = open_account(session, world_id, "character", character.id)
        price = settings.economy.prices.get(stock_item.object_type, 0)
        if get_balance(session, char_acc.id) < price:
            return False, "Insufficient funds", None, {}

        if character.location_id != shop_loc.id:
            return True, "Needs to move to shop to buy item", \
                shop_loc.id, {"object_id": stock_item.id, "price": price}
        return True, None, None, {"object_id": stock_item.id, "price": price}

    elif action_type == "SOCIALIZE":
        from app.db.models import Relationship
        if needs is None:
            from app.db.models import CharacterNeeds
            needs = session.query(CharacterNeeds).filter_by(character_id=character.id).one()

        # Gating by social.enabled and need threshold
        if not settings.social.enabled:
            return False, "Social system disabled", None, {}
        if needs.social >= settings.social.social_action_threshold:
            return False, "Social need not high enough", None, {}

        # Prefetch: one query per decision instead of per candidate (N+1
        # elimination — performance trigger R10). Relationship affection is
        # memoized in the per-decision ctx when available.
        others = session.query(Character).filter(
            Character.world_id == world_id,
            Character.alive,
            Character.id != character.id
        ).all()

        def _aff_by_pair():
            return {
                (rel.character_a, rel.character_b): rel.affection
                for rel in session.query(Relationship)
                .filter_by(world_id=world_id)
                .all()
            }

        aff_by_pair = _ctx_get("aff_by_pair", _aff_by_pair)
        initial = settings.social.initial_affection

        def get_affection(target):
            a, b = sorted([character.id, target.id])
            return aff_by_pair.get((a, b), initial)

        def select_best(candidates):
            scored = sorted(
                ((get_affection(c), c.id, c) for c in candidates),
                key=lambda x: (-x[0], x[1]),
            )
            for aff, _cid, c in scored:
                if aff > settings.social.refusal_threshold:
                    return c, aff
            return None, None

        # Aggravated seek: an active feud (affection < -30, the conflict
        # boundary used by the completion hook) makes the character seek
        # their worst enemy out, wherever they are (spec §98 narrative:
        # seeded conflicts must play out; burnout via the refusal gate).
        feuds = [c for c in others if get_affection(c) < -30]
        best_target, best_affection = select_best(feuds)
        if best_target is not None:
            if best_target.location_id == character.location_id:
                return True, None, None, {
                    "target_id": best_target.id,
                    "affection_at_start": best_affection,
                }
            return True, "Seeking out an active feud partner", \
                best_target.location_id, {
                    "target_id": best_target.id,
                    "affection_at_start": best_affection,
                }

        # Co-located candidates first (no movement needed).
        co_located = [c for c in others if c.location_id == character.location_id]
        best_target, best_affection = select_best(co_located)
        if best_target is not None:
            return True, None, None, {
                "target_id": best_target.id,
                "affection_at_start": best_affection,
            }

        # Nobody acceptable here: seek the best candidate globally and move
        # to THEIR current location (spec amendment: target-seeking instead
        # of a generic hub — guarantees encounters; completion applies the
        # interaction to the decision-time target if still alive).
        best_target, best_affection = select_best(others)
        if best_target is None:
            return False, "No acceptable social targets", None, {}
        return True, "Needs to move to the chosen social target", \
            best_target.location_id, {
                "target_id": best_target.id,
                "affection_at_start": best_affection,
            }

    return False, "Unknown action type", None, {}
