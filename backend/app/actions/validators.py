from typing import Any, Dict, Optional, Tuple

from sqlalchemy.orm import Session

from app.config.config import Settings
from app.db.models import Character, CharacterJob, Location, Organization, WorldObject


def validate(
    session: Session,
    world_id: str,
    character: Character,
    action_type: str,
    timestamp: int,
    settings: Settings
) -> Tuple[bool, Optional[str], Optional[int], Dict[str, Any]]:
    """
    Validates if an action is possible for a character.
    Returns: (ok, reason, needs_move_location_id, params)
    """
    if not character.alive:
        return False, "Character is not alive", None, {}

    # Action-specific validation
    if action_type == "SLEEP":
        # valid if energy < 100
        from app.db.models import CharacterNeeds
        needs = session.query(CharacterNeeds).filter_by(character_id=character.id).one()
        if needs.energy >= 100:
            return False, "Energy already full", None, {}

        if character.location_id != character.home_location_id:
            return True, "Needs to move home to sleep", character.home_location_id, {}
        return True, None, None, {}

    elif action_type == "EAT":
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
        kitchen_loc = session.query(Location).filter_by(world_id=world_id, type="kitchen").first()
        if not kitchen_loc:
            return False, "No kitchen available in world", None, {}

        # Check if kitchen stock exists (owned by community org)
        community_org = (
            session.query(Organization)
            .filter_by(world_id=world_id, type="community")
            .first()
        )
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
        from app.db.models import CharacterNeeds, ResourceBalance
        needs = session.query(CharacterNeeds).filter_by(character_id=character.id).one()
        if needs.thirst >= 100:
            return False, "Thirst already full", None, {}

        # Community water ResourceBalance > 0
        community_org = (
            session.query(Organization)
            .filter_by(world_id=world_id, type="community")
            .first()
        )
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

        well_loc = session.query(Location).filter_by(world_id=world_id, type="well").first()
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
        shop_loc = session.query(Location).filter_by(world_id=world_id, type="shop").first()
        if not shop_loc:
            return False, "No shop available in world", None, {}

        # Shop org owns stock of any food type at shop location
        shop_org = session.query(Organization).filter_by(world_id=world_id, type="business").first()
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

    elif action_type == "IDLE":
        return True, None, None, {}

    return False, "Unknown action type", None, {}
