"""M12 Chunk A tests (SPEC 012-market, T2): market core."""
import json
import os
import sys

import pytest
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from app.config.config import load_config  # noqa: E402
from app.db.models import (  # noqa: E402
    Account,
    Character,
    MarketOffer,
    WorldObject,
    bootstrap,
    create_engine_factory,
)
from app.world.seed_world import seed_world  # noqa: E402

SETTINGS = None


def setup_module(module):
    global SETTINGS
    SETTINGS = load_config("config/default.yaml")


def make_settings(tmp_path):
    return SETTINGS.model_copy(update={
        "persistence": SETTINGS.persistence.model_copy(
            update={"db_path": str(tmp_path / "w.db")}
        ),
    })


def build(settings):
    import random as _r

    from app.characters.generator import generate_population

    engine = create_engine_factory(settings)
    with sessionmaker(bind=engine)() as session:
        bootstrap(engine, settings, seed=42)
        seed_world(session, settings, settings.world.world_id)
        generate_population(
            session, settings, _r.Random(42),
            settings.world.world_id, 20,
        )
        session.commit()
    return engine


def make_seller_buyer(session, settings):
    """Two characters with funded accounts + one owned item each."""
    from app.inventory import create_object

    seller = session.query(Character).filter_by(
        world_id=settings.world.world_id).first()
    buyer = session.query(Character).filter_by(
        world_id=settings.world.world_id).all()[1]
    for char, bal in [(seller, 500), (buyer, 500)]:
        acc = session.query(Account).filter_by(
            owner_type="character", owner_id=char.id).first()
        if acc is None:
            session.add(Account(
                id=f"acc_{char.id}", world_id=settings.world.world_id,
                owner_type="character", owner_id=char.id, balance=bal,
                created_at=0,
            ))
        else:
            acc.balance = bal
    item = create_object(
        session, settings.world.world_id, "wood_pile",
        location_id=seller.location_id, quantity=1,
        owner_character_id=seller.id,
    )
    session.commit()
    return seller, buyer, item


def balance(session, char):
    acc = session.query(Account).filter_by(
        owner_type="character", owner_id=char.id).one()
    return acc.balance


class TestListBuy:
    def test_full_sale_flow(self, tmp_path):
        from app.market.marketplace import buy_offer, list_object

        settings = make_settings(tmp_path)
        engine = build(settings)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            seller, buyer, item = make_seller_buyer(session, settings)
            offer = list_object(
                session, settings.world.world_id, 0, seller, item.id, 150
            )
            session.commit()
            ev = json.loads(
                session.query(
                    __import__("app.db.models", fromlist=["WorldEvent"])
                    .WorldEvent
                ).filter_by(event_type="MARKET_LISTED").one().payload
            )
            assert ev["price"] == 150

            buy_offer(session, settings.world.world_id, 10, buyer, offer.id)
            session.commit()
            assert offer.status == "sold"
            assert offer.buyer_character_id == buyer.id
            assert balance(session, seller) == 650
            assert balance(session, buyer) == 350
            sold_obj = session.get(WorldObject, item.id)
            assert sold_obj.owner_character_id == buyer.id
            sold_ev = session.query(
                __import__("app.db.models", fromlist=["WorldEvent"])
                .WorldEvent
            ).filter_by(event_type="MARKET_SOLD").one()
            assert json.loads(sold_ev.payload)["offer_id"] == offer.id

    def test_insufficient_funds_no_state_change(self, tmp_path):
        from app.market.marketplace import MarketError, buy_offer, list_object
        from app.simulation.invariants import run_invariant_checks

        settings = make_settings(tmp_path)
        engine = build(settings)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            seller, buyer, item = make_seller_buyer(session, settings)
            acc = session.query(Account).filter_by(
                owner_type="character", owner_id=buyer.id).one()
            acc.balance = 10
            session.commit()
            offer = list_object(
                session, settings.world.world_id, 0, seller, item.id, 150)
            session.commit()
            with pytest.raises(MarketError):
                buy_offer(session, settings.world.world_id, 10, buyer, offer.id)
            session.rollback()
            # nothing changed
            assert session.get(MarketOffer, offer.id).status == "active"
            assert balance(session, seller) == 500
            assert balance(session, buyer) == 10
            results = run_invariant_checks(
                session, settings.world.world_id, settings
            )
            market = next(
                r for r in results if r["name"] == "market_integrity")
            assert market["ok"], market["details"]

    def test_double_active_forbidden(self, tmp_path):
        from app.market.marketplace import MarketError, list_object

        settings = make_settings(tmp_path)
        engine = build(settings)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            seller, _, item = make_seller_buyer(session, settings)
            list_object(session, settings.world.world_id, 0, seller,
                        item.id, 100)
            with pytest.raises(MarketError):
                list_object(session, settings.world.world_id, 0, seller,
                            item.id, 100)


class TestGuards:
    def test_not_your_item(self, tmp_path):
        from app.market.marketplace import MarketError, list_object

        settings = make_settings(tmp_path)
        engine = build(settings)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            seller, buyer, item = make_seller_buyer(session, settings)
            with pytest.raises(MarketError):
                list_object(session, settings.world.world_id, 0, buyer,
                            item.id, 100)

    def test_cancel_only_seller(self, tmp_path):
        from app.market.marketplace import (
            MarketError,
            cancel_offer,
            list_object,
        )

        settings = make_settings(tmp_path)
        engine = build(settings)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            seller, buyer, item = make_seller_buyer(session, settings)
            offer = list_object(
                session, settings.world.world_id, 0, seller, item.id, 100)
            with pytest.raises(MarketError):
                cancel_offer(session, settings.world.world_id, 5, buyer,
                             offer.id)
            cancel_offer(session, settings.world.world_id, 5, seller, offer.id)
            assert session.get(MarketOffer, offer.id).status == "cancelled"

    def test_own_offer_forbidden(self, tmp_path):
        from app.market.marketplace import MarketError, buy_offer, list_object

        settings = make_settings(tmp_path)
        engine = build(settings)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            seller, _, item = make_seller_buyer(session, settings)
            offer = list_object(
                session, settings.world.world_id, 0, seller, item.id, 100)
            with pytest.raises(MarketError):
                buy_offer(session, settings.world.world_id, 10, seller,
                          offer.id)
