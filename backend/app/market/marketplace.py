"""Marketplace (012, §74+): player-to-player offers with ledger sales.

Money moves only through economy_transfer (П1, ledger closed by M2
invariant); items move through transfer_object (ITEM_TRANSFERRED emitted).
"""

from sqlalchemy.orm import Session

from app.db.models import (
    Account,
    Character,
    MarketOffer,
    WorldObject,
)
from app.economy import get_balance
from app.economy import transfer as economy_transfer
from app.events.events import EventType, log_event
from app.inventory import transfer_object


class MarketError(Exception):
    def __init__(self, code: str, status: int = 422):
        self.code = code
        self.status = status
        super().__init__(code)


def list_object(
    session: Session, world_id: str, game_timestamp: int,
    seller: Character, object_id: int, price: int,
) -> MarketOffer:
    """Offer an owned item for sale (one active offer per object)."""
    if price <= 0:
        raise MarketError("price_must_be_positive")
    obj = session.query(WorldObject).filter_by(
        id=object_id, world_id=world_id).first()
    if obj is None or obj.owner_character_id != seller.id:
        raise MarketError("not_your_item", 403)
    if obj.quantity < 1:
        raise MarketError("item_unavailable", 409)
    existing = (
        session.query(MarketOffer)
        .filter_by(world_id=world_id, object_id=object_id, status="active")
        .first()
    )
    if existing is not None:
        raise MarketError("already_listed", 409)
    offer = MarketOffer(
        world_id=world_id, seller_character_id=seller.id,
        object_id=object_id, price=price, status="active",
        created_at=game_timestamp,
    )
    session.add(offer)
    session.flush()
    log_event(
        session, world_id, game_timestamp, EventType.MARKET_LISTED,
        actor_id=seller.id,
        payload={"offer_id": offer.id, "object_id": object_id, "price": price},
    )
    return offer


def cancel_offer(
    session: Session, world_id: str, game_timestamp: int,
    seller: Character, offer_id: int,
) -> None:
    offer = session.query(MarketOffer).filter_by(
        id=offer_id, world_id=world_id).first()
    if offer is None:
        raise MarketError("offer_not_found", 404)
    if offer.seller_character_id != seller.id:
        raise MarketError("not_your_offer", 403)
    if offer.status != "active":
        raise MarketError("offer_closed", 409)
    offer.status = "cancelled"
    offer.closed_at = game_timestamp
    session.flush()


def buy_offer(
    session: Session, world_id: str, game_timestamp: int,
    buyer: Character, offer_id: int,
) -> MarketOffer:
    """Buy an active offer: ledger transfer + ownership change, atomic."""
    offer = session.query(MarketOffer).filter_by(
        id=offer_id, world_id=world_id).first()
    if offer is None:
        raise MarketError("offer_not_found", 404)
    if offer.status != "active":
        raise MarketError("offer_closed", 409)
    if offer.seller_character_id == buyer.id:
        raise MarketError("own_offer", 409)

    buyer_acc = (
        session.query(Account)
        .filter_by(owner_type="character", owner_id=buyer.id)
        .one()
    )
    if get_balance(session, buyer_acc.id) < offer.price:
        raise MarketError("insufficient_funds", 402)

    obj = session.get(WorldObject, offer.object_id)
    if obj is None or obj.quantity < 1:
        raise MarketError("item_unavailable", 409)

    seller_acc = (
        session.query(Account)
        .filter_by(owner_type="character", owner_id=offer.seller_character_id)
        .one()
    )

    # 1. money (ledger, reason MARKET_SALE)
    economy_transfer(
        session, world_id, game_timestamp,
        buyer_acc.id, seller_acc.id, offer.price, reason="MARKET_SALE",
    )
    # 2. item (ownership + location)
    transfer_object(
        session, world_id, game_timestamp, offer.object_id, obj.quantity,
        new_owner_character_id=buyer.id,
        location_id=buyer.location_id,
    )
    # 3. close offer
    offer.status = "sold"
    offer.buyer_character_id = buyer.id
    offer.closed_at = game_timestamp
    log_event(
        session, world_id, game_timestamp, EventType.MARKET_SOLD,
        actor_id=buyer.id,
        payload={"offer_id": offer.id, "object_id": offer.object_id,
                 "price": offer.price, "seller": offer.seller_character_id},
    )
    return offer
