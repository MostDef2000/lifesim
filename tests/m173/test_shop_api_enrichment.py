"""#173 — GET /market/offers read enrichment (additive display fields).

The shop screen (#/shop) renders item_name/seller_name from the offers
payload; the enrichment is read-only over the existing market read model:

- item_name resolution chain: object_metadata JSON «name» (non-empty
  string, for items minted with an explicit display name) → object_type
  (the authoritative row identity the inventory screen shows — all current
  stock carries metadata={}) → «Неизвестный предмет» (dangling offer,
  e.g. the object row was consumed to zero and deleted);
- seller_name: characters first_name + last_name (house pattern, same as
  GET /characters/{cid});
- malformed / non-dict metadata degrades gracefully (no 500, falls through
  the chain to object_type);
- additive only: the pre-existing keys (id/object_id/price/seller/status)
  are unchanged, no mutation happens (a read never flips offer status),
  and the status filter validation is untouched (bad status → 422).
"""

import os
import random
import sqlite3
import sys

import pytest
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from app.api.app import create_app  # noqa: E402
from app.config.config import load_config  # noqa: E402
from app.db.models import (  # noqa: E402
    Character,
    MarketOffer,
    WorldObject,
    bootstrap,
    create_engine_factory,
)
from app.world.seed_world import seed_world  # noqa: E402

SETTINGS = None
PW = "Correct-Horse-9!"


def setup_module(module):
    global SETTINGS
    SETTINGS = load_config("config/default.yaml")


def make_settings(tmp_path):
    return SETTINGS.model_copy(update={
        "persistence": SETTINGS.persistence.model_copy(
            update={"db_path": str(tmp_path / "w.db")}
        ),
    })


def build_world(settings, seed=42):
    engine = create_engine_factory(settings)
    with sessionmaker(bind=engine)() as session:
        bootstrap(engine, settings, seed=seed)
        seed_world(session, settings, settings.world.world_id)
        rng = random.Random(seed)
        from app.characters.generator import generate_population

        generate_population(session, settings, rng,
                            settings.world.world_id, 20)
        session.commit()
    return engine


def _register_and_login(client, username):
    client.post("/auth/register", json={
        "username": username, "email": f"{username}@x.com",
        "password": PW, "age_confirmed": True,
    })
    client.post("/auth/login", json={"username": username, "password": PW})


def _make_player(client, username, name):
    _register_and_login(client, username)
    r = client.post("/characters", json={"name": name, "sex": "M", "age": 30})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _give_item(factory, settings, cid, object_type="food_bread", qty=1,
               metadata=None):
    with factory() as s:
        loc = s.get(Character, cid).location_id
        from app.inventory import create_object

        obj = create_object(
            s, settings.world.world_id, object_type=object_type,
            quantity=qty, owner_character_id=cid, location_id=loc,
            metadata=metadata or {},
        )
        s.commit()
        return obj.id


def _list(client, oid, price=5):
    r = client.post("/market/offers", json={"object_id": oid, "price": price})
    assert r.status_code == 200, r.text
    return r.json()["offer_id"]


def _offer_for(client, oid):
    offers = client.get("/market/offers").json()
    return next(o for o in offers if o["object_id"] == oid)


@pytest.fixture()
def shop_world(tmp_path):
    """Seeded world + app/client + a player character (m157 harness shape)."""
    settings = make_settings(tmp_path)
    engine = build_world(settings)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app(settings, factory)
    client = TestClient(app)
    cid = _make_player(client, "b2shop", "Борис Орлов")
    return {
        "settings": settings, "app": app, "client": client,
        "factory": factory, "cid": cid, "engine": engine,
        "db_path": str(tmp_path / "w.db"),
    }


def test_offers_enriched_fields_additive(shop_world):
    settings, client, cid = (shop_world["settings"], shop_world["client"],
                             shop_world["cid"])
    factory = shop_world["factory"]
    # Plain stock item: metadata={} → object_type is the display name.
    oid = _give_item(factory, settings, cid, "food_bread")
    _list(client, oid)
    offer = _offer_for(client, oid)
    # Additive: every pre-existing key intact, untouched by the enrichment.
    assert offer["id"] and offer["object_id"] == oid and offer["price"] == 5
    assert offer["seller"] == cid and offer["status"] == "active"
    assert offer["item_name"] == "food_bread"
    assert offer["seller_name"] == "Борис Орлов"
    # Item minted with an explicit display name in metadata wins.
    oid2 = _give_item(factory, settings, cid, "hat",
                      metadata={"name": "Праздничный колпак"})
    _list(client, oid2, price=7)
    offer2 = _offer_for(client, oid2)
    assert offer2["item_name"] == "Праздничный колпак"
    assert offer2["seller_name"] == "Борис Орлов"


def test_offers_malformed_metadata_falls_through(shop_world):
    settings, client, cid = (shop_world["settings"], shop_world["client"],
                             shop_world["cid"])
    factory = shop_world["factory"]
    # Malformed JSON in the Text column must never 500 and must never mask
    # the row identity: the chain falls through to object_type.
    oid = _give_item(factory, settings, cid, "jacket")
    with factory() as s:
        s.get(WorldObject, oid).object_metadata = "{corrupt json"
        s.commit()
    _list(client, oid)
    assert _offer_for(client, oid)["item_name"] == "jacket"
    # Valid JSON but not an object → same fall-through.
    oid2 = _give_item(factory, settings, cid, "boots")
    with factory() as s:
        s.get(WorldObject, oid2).object_metadata = "[1, 2]"
        s.commit()
    _list(client, oid2)
    assert _offer_for(client, oid2)["item_name"] == "boots"
    # Non-string «name» is ignored, object_type stands.
    oid3 = _give_item(factory, settings, cid, "hat", metadata={"name": 42})
    _list(client, oid3)
    assert _offer_for(client, oid3)["item_name"] == "hat"


def test_offers_dangling_object_fallback(shop_world):
    client, cid = shop_world["client"], shop_world["cid"]
    factory, db_path = shop_world["factory"], shop_world["db_path"]
    settings = shop_world["settings"]
    oid = _give_item(factory, settings, cid, "food_bread")
    _list(client, oid)
    # consume_object deletes rows at quantity 0 — a listed object row can
    # genuinely disappear. Simulate exactly that (raw connection: the app
    # engine keeps PRAGMA foreign_keys=ON, and the dangling offer is the
    # state under test).
    raw = sqlite3.connect(db_path)
    try:
        raw.execute("PRAGMA foreign_keys=OFF")
        raw.execute("DELETE FROM world_objects WHERE id = ?", (oid,))
        raw.commit()
    finally:
        raw.close()
    r = client.get("/market/offers")
    assert r.status_code == 200, r.text
    assert _offer_for(client, oid)["item_name"] == "Неизвестный предмет"


def test_offers_enrichment_is_read_only(shop_world):
    client, cid = shop_world["client"], shop_world["cid"]
    factory, settings = shop_world["factory"], shop_world["settings"]
    oid = _give_item(factory, settings, cid, "jacket")
    offer_id = _list(client, oid)
    before = client.get("/market/offers").json()
    for _ in range(2):
        assert client.get("/market/offers").status_code == 200
    assert before == client.get("/market/offers").json()  # pure read
    with factory() as s:
        offer = s.query(MarketOffer).filter_by(id=offer_id).first()
        assert offer is not None and offer.status == "active"
        assert offer.price == 5


def test_offers_status_filter_validation_unchanged(shop_world):
    client, app = shop_world["client"], shop_world["app"]
    # Bad status → 422 "bad status", exactly as before the enrichment.
    r = client.get("/market/offers", params={"status": "bogus"})
    assert r.status_code == 422
    assert r.json()["detail"] == "bad status"
    # The other legal filters still work (empty results, enriched shape).
    for status in ("sold", "cancelled"):
        r = client.get("/market/offers", params={"status": status})
        assert r.status_code == 200
        assert r.json() == []
    # Unauthenticated reads stay rejected.
    anon = TestClient(app)
    assert anon.get("/market/offers").status_code == 401


def test_offers_seller_name_uses_seller_character(shop_world):
    settings, client = shop_world["settings"], shop_world["client"]
    factory, app = shop_world["factory"], shop_world["app"]
    # A SECOND player lists an item: seller_name must be the seller's name,
    # not the requester's.
    other = TestClient(app)
    other_cid = _make_player(other, "b2second", "Анна Ветрова")
    oid = _give_item(factory, settings, other_cid, "boots")
    _list(other, oid, price=9)
    offers = client.get("/market/offers").json()
    row = next(o for o in offers if o["object_id"] == oid)
    assert row["seller"] == other_cid
    assert row["seller_name"] == "Анна Ветрова"
