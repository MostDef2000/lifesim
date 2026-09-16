from sqlalchemy.orm import Session

from app.config.config import Settings
from app.db.models import Account, Job, Location, LocationLink, Organization, ResourceBalance


def test_seed_world_locations(session: Session, settings: Settings, world_id: str):
    # No longer calling seed_world manually because 'session' fixture is now 'seeded_session'

    # Check defined locations
    locs = session.query(Location).filter_by(world_id=world_id).all()
    # Defined locations in config + houses
    expected_count = len(settings.locations.locations) + settings.locations.houses_count
    assert len(locs) == expected_count

    # Verify the settlement exists and is a parent to houses
    settlement = session.query(Location).filter_by(world_id=world_id, type="settlement").one()
    houses = session.query(Location).filter_by(world_id=world_id, type="house").all()
    # Use >= or check specifically for the count expected from seed_world
    assert len(houses) >= settings.locations.houses_count
    for house in houses:
        assert house.parent_id == settlement.id

def test_seed_world_links(session: Session, settings: Settings, world_id: str):
    # No longer calling seed_world manually

    # Verify a known link from config: island -> settlement
    # We find the IDs by name
    island = session.query(Location).filter_by(world_id=world_id, type="island").one()
    settlement = session.query(Location).filter_by(world_id=world_id, type="settlement").one()

    link_out = session.query(LocationLink).filter_by(
        world_id=world_id, from_location_id=island.id, to_location_id=settlement.id
    ).one()
    link_in = session.query(LocationLink).filter_by(
        world_id=world_id, from_location_id=settlement.id, to_location_id=island.id
    ).one()

    assert link_out.travel_minutes == 30
    assert link_in.travel_minutes == 30

def test_seed_world_organizations_and_accounts(session: Session, settings: Settings, world_id: str):
    # No longer calling seed_world manually

    # Check orgs
    orgs = session.query(Organization).filter_by(world_id=world_id).all()
    assert len(orgs) == len(settings.economy.organizations)

    # Check accounts: world + 1 per org
    accs = session.query(Account).filter_by(world_id=world_id).all()
    assert len(accs) == len(settings.economy.organizations) + 1

    # World account check
    world_acc = session.query(Account).filter_by(world_id=world_id, owner_type="world").one()
    assert world_acc.owner_id == "world_main"

def test_seed_world_water_balance(session: Session, settings: Settings, world_id: str):
    # No longer calling seed_world manually

    # Find community org
    community_org = session.query(Organization).filter_by(world_id=world_id, type="community").one()

    water_bal = session.query(ResourceBalance).filter_by(
        world_id=world_id,
        resource_key="water",
        owner_type="organization",
        owner_id=str(community_org.id)
    ).one()

    assert water_bal.quantity == settings.economy.water_initial_quantity

def test_seed_world_jobs(session: Session, settings: Settings, world_id: str):
    # No longer calling seed_world manually

    jobs = session.query(Job).all() # Remove world_id filter as Job doesn't have it

    assert len(jobs) == len(settings.population.jobs)
    for job in jobs:
        assert job.salary > 0

def test_seed_world_determinism(tmp_path, default_settings):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db.models import bootstrap
    from app.world.seed_world import seed_world

    def run_seeded_world(db_file):
        engine = create_engine(f"sqlite:///{db_file}")
        bootstrap(engine, default_settings, 42)
        Session = sessionmaker(bind=engine)
        session = Session()
        world_id = default_settings.world.world_id
        seed_world(session, default_settings, world_id)

        # Capture snapshots of the seeded state
        locs = (
            session.query(Location)
            .filter_by(world_id=world_id)
            .order_by(Location.id)
            .all()
        )
        loc_data = [(loc.id, loc.name, loc.type, loc.parent_id) for loc in locs]

        links = (
            session.query(LocationLink)
            .filter_by(world_id=world_id)
            .order_by(LocationLink.id)
            .all()
        )
        link_data = [
            (ln.id, ln.from_location_id, ln.to_location_id, ln.travel_minutes)
            for ln in links
        ]

        jobs = session.query(Job).order_by(Job.id).all()
        job_data = [(j.id, j.title, j.salary) for j in jobs]

        accs = (
            session.query(Account)
            .filter_by(world_id=world_id)
            .order_by(Account.id)
            .all()
        )
        acc_data = [(a.id, a.owner_type, a.owner_id) for a in accs]

        session.close()
        return loc_data, link_data, job_data, acc_data

    db1 = tmp_path / "world1.db"
    db2 = tmp_path / "world2.db"

    res1 = run_seeded_world(str(db1))
    res2 = run_seeded_world(str(db2))

    assert res1 == res2

