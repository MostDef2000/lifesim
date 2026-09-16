from sqlalchemy.orm import Session

from app.db.models import Location, LocationLink
from app.world.seed_world import find_path


def test_bfs_router_direct(seeded_session: Session, world_id: str):
    # Assume world is seeded. find_id for 'island' and 'settlement'
    db_session = seeded_session
    island = db_session.query(Location).filter_by(world_id=world_id, type="island").one()
    settlement = db_session.query(Location).filter_by(world_id=world_id, type="settlement").one()

    path, time = find_path(db_session, world_id, island.id, settlement.id)
    assert path == [island.id, settlement.id]
    assert time == 30

def test_bfs_router_multi_hop(seeded_session: Session, world_id: str):
    # island -> settlement -> shop
    db_session = seeded_session
    island = db_session.query(Location).filter_by(world_id=world_id, type="island").one()
    shop = db_session.query(Location).filter_by(world_id=world_id, type="shop").one()

    path, time = find_path(db_session, world_id, island.id, shop.id)
    # Expect [island, settlement, shop]
    assert path is not None
    assert len(path) == 3
    # 30 + 5 = 35
    assert time == 35

def test_bfs_router_unreachable(seeded_session: Session, world_id: str):
    # Create a disconnected location
    db_session = seeded_session
    loc = Location(world_id=world_id, type="void", name="Void", capacity=1)
    db_session.add(loc)
    db_session.flush()

    island = db_session.query(Location).filter_by(world_id=world_id, type="island").one()

    path, time = find_path(db_session, world_id, island.id, loc.id)
    assert path is None
    assert time is None

def test_bfs_router_determinism(seeded_session: Session, world_id: str):
    # Setup two paths of equal length
    # settlement -> shop (5 min)
    # settlement -> workshop (5 min)
    # Target: a new location 'hub' connected to both.

    db_session = seeded_session
    settlement = db_session.query(Location).filter_by(world_id=world_id, type="settlement").one()
    shop = db_session.query(Location).filter_by(world_id=world_id, type="shop").one()
    workshop = db_session.query(Location).filter_by(world_id=world_id, type="workshop").one()

    hub = Location(world_id=world_id, type="hub", name="Hub", capacity=10)
    db_session.add(hub)
    db_session.flush()

    # Links: shop -> hub (1 min), workshop -> hub (1 min)
    # If we go settlement -> shop -> hub vs settlement -> workshop -> hub
    # BFS should pick the one with lowest intermediate IDs.

    link1 = LocationLink(
        world_id=world_id, from_location_id=shop.id, to_location_id=hub.id, travel_minutes=1
    )
    link2 = LocationLink(
        world_id=world_id, from_location_id=workshop.id, to_location_id=hub.id, travel_minutes=1
    )
    db_session.add_all([link1, link2])
    db_session.flush()

    path, time = find_path(db_session, world_id, settlement.id, hub.id)

    # The path should be [settlement, min(shop.id, workshop.id), hub]
    expected_mid = min(shop.id, workshop.id)
    assert path == [settlement.id, expected_mid, hub.id]
