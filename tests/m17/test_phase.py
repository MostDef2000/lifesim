"""M17 tests (SPEC 017-electricity): phase behaviour, blueprint, keystone."""
import os
import sys

from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from app.config.config import load_config  # noqa: E402
from app.db.models import (  # noqa: E402
    Character,
    CharacterNeeds,
    WeatherState,
    WorldEvent,
    WorldObject,
    bootstrap,
    create_engine_factory,
)
from app.world.seed_world import seed_world  # noqa: E402

SETTINGS = None


def setup_module(module):
    global SETTINGS
    SETTINGS = load_config("config/default.yaml")


def make_settings(tmp_path, **elec_updates):
    from pathlib import Path

    tmp_path = Path(tmp_path)
    return SETTINGS.model_copy(update={
        "persistence": SETTINGS.persistence.model_copy(
            update={"db_path": str(tmp_path / "w.db")}
        ),
        "electricity": SETTINGS.electricity.model_copy(update=elec_updates),
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


def prep_house(session, settings, occupants=2):
    """Pick a house, move people in, give food. Returns house."""
    (
        session.query(Character)
        .filter_by(world_id=settings.world.world_id)
        .filter(Character.user_id.is_(None))
        .first()
    )
    # find any house location
    from app.db.models import Location

    loc = session.query(Location).filter_by(
        world_id=settings.world.world_id, type="house").first()
    npcs = (
        session.query(Character)
        .filter_by(world_id=settings.world.world_id)
        .filter(Character.user_id.is_(None))
        .limit(occupants)
        .all()
    )
    for n in npcs:
        n.home_location_id = loc.id
        n.location_id = loc.id
    food = (
        session.query(WorldObject)
        .filter(WorldObject.object_type == "food_bread",
                WorldObject.quantity > 0)
        .first()
    )
    if food is not None:
        food.location_id = loc.id
        food.condition = 100
        food_id = food.id
    else:
        food_id = None
    session.commit()
    return loc, npcs, food_id


class TestKeystone:
    def test_disabled_noop(self, tmp_path):
        from app.simulation.electricity import run_electricity_phase

        settings = make_settings(tmp_path)  # enabled=False
        engine = build(settings)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            prep_house(session, settings)
            assert run_electricity_phase(
                session, settings.world.world_id, 1, 1440, settings) == 0
            assert session.query(WorldEvent).filter_by(
                event_type="POWER_OUTAGE").count() == 0

    def test_blueprint_present(self):
        assert "generator" in SETTINGS.construction.costs
        bp = SETTINGS.construction.costs["generator"]
        assert bp["required_items"] == {"wood_pile": 3}
        assert bp["days"] == 2


class TestPhase:
    def test_deficit_outage_effects(self, tmp_path):
        from app.simulation.electricity import run_electricity_phase

        settings = make_settings(tmp_path, enabled=True)
        engine = build(settings)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            loc, npcs, food_id = prep_house(session, settings, occupants=2)
            session.commit()
            outages = run_electricity_phase(
                session, settings.world.world_id, 1, 1440, settings)
            session.commit()
            evs = session.query(WorldEvent).filter_by(
                event_type="POWER_OUTAGE").all()
            mine = [e for e in evs
                    if f'"location_id": {loc.id}' in e.payload]
            assert outages >= 1
            assert len(mine) == 1
            payload = mine[0].payload
            assert '"demand"' in payload and '"storm": false' in payload
            food = session.get(WorldObject, food_id)
            assert food.condition == 100 - settings.electricity.food_spoil_condition
            for n in npcs:
                needs = session.query(CharacterNeeds).filter_by(
                    character_id=n.id).one()
                assert needs.energy <= 100 - settings.electricity.energy_drain

    def test_enough_generators_silent(self, tmp_path):
        from app.simulation.electricity import run_electricity_phase

        settings = make_settings(tmp_path, enabled=True)
        engine = build(settings)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            loc, npcs, food_id = prep_house(session, settings, occupants=2)
            session.add(WorldObject(
                world_id=settings.world.world_id,
                location_id=loc.id, object_type="generator",
                quantity=2, condition=100, object_metadata="{}",
            ))
            session.commit()
            run_electricity_phase(
                session, settings.world.world_id, 1, 1440, settings)
            session.commit()
            evs = session.query(WorldEvent).filter_by(
                event_type="POWER_OUTAGE").all()
            assert all(f'"location_id": {loc.id}' not in e.payload
                       for e in evs)
            food = session.get(WorldObject, food_id)
            assert food.condition == 100

    def test_storm_outage(self, tmp_path):
        from app.simulation.electricity import run_electricity_phase

        settings = make_settings(
            tmp_path, enabled=True, outage_chance=1.0)
        engine = build(settings)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            loc, npcs, food_id = prep_house(session, settings, occupants=1)
            session.add(WorldObject(
                world_id=settings.world.world_id,
                location_id=loc.id, object_type="generator",
                quantity=2, condition=100, object_metadata="{}",
            ))
            session.add(WeatherState(
                world_id=settings.world.world_id, day=1,
                source="synthetic", real_date="2025-09-15",
                temperature=12.0, wind=20.0, precipitation=0.0,
                cloudiness=1.0, visibility=10.0,
            ))
            session.commit()
            run_electricity_phase(
                session, settings.world.world_id, 1, 1440, settings)
            session.commit()
            evs = session.query(WorldEvent).filter_by(
                event_type="POWER_OUTAGE").all()
            mine = [e for e in evs
                    if f'"location_id": {loc.id}' in e.payload]
            assert len(mine) == 1
            assert '"storm": true' in mine[0].payload

    def test_no_occupants_silent(self, tmp_path):
        from app.simulation.electricity import run_electricity_phase

        settings = make_settings(tmp_path, enabled=True)
        engine = build(settings)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            from app.db.models import Location

            empty = Location(
                world_id=settings.world.world_id, type="house",
                name="Пустой дом", capacity=1,
            )
            session.add(empty)
            session.commit()
            run_electricity_phase(
                session, settings.world.world_id, 1, 1440, settings)
            evs = session.query(WorldEvent).filter_by(
                event_type="POWER_OUTAGE").all()
            assert all(f'"location_id": {empty.id}' not in e.payload
                       for e in evs)
