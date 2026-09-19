"""M11 Chunk A tests (SPEC 011-fire, T3): fire core."""
import os
import sys

from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from app.config.config import load_config  # noqa: E402
from app.db.models import (  # noqa: E402
    Character,
    CharacterHealth,
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


def make_settings(tmp_path, **fire_updates):
    return SETTINGS.model_copy(update={
        "persistence": SETTINGS.persistence.model_copy(
            update={"db_path": str(tmp_path / "w.db")}
        ),
        "fire": SETTINGS.fire.model_copy(update=fire_updates),
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


def add_object(session, settings, loc_id, obj_type, flammability):
    obj = WorldObject(
        world_id=settings.world.world_id, object_type=obj_type,
        location_id=loc_id, quantity=1, object_metadata="{}",
        flammability=flammability, burn_state="intact",
    )
    session.add(obj)
    session.flush()
    return obj


def get_loc(session, settings, idx=0):
    from app.db.models import Location

    return (
        session.query(Location)
        .filter_by(world_id=settings.world.world_id)
        .order_by(Location.id)
        .all()[idx]
    )


class TestIgniteBurnout:
    def test_ignite_event_then_burnout(self, tmp_path):
        from app.simulation.fire import ignite_object, run_fire_phase

        settings = make_settings(tmp_path, burnout_days=2)
        engine = build(settings)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            loc = get_loc(session, settings)
            obj = add_object(session, settings, loc.id, "wood_pile", 0.9)
            ignite_object(session, settings.world.world_id, obj, 5, settings)
            session.commit()
            ev = session.query(WorldEvent).filter_by(
                event_type="OBJECT_BURNING").one()
            import json as _json

            assert _json.loads(ev.payload)["object_id"] == obj.id

            # day 5: still burning; day 7 (5+2): burned
            run_fire_phase(session, settings.world.world_id, 5, settings)
            assert session.get(WorldObject, obj.id).burn_state == "burning"
            run_fire_phase(session, settings.world.world_id, 7, settings)
            burned = session.get(WorldObject, obj.id)
            assert burned.burn_state == "burned"
            assert burned.quantity == 0
            ev = session.query(WorldEvent).filter_by(
                event_type="OBJECT_BURNED").one()
            assert _json.loads(ev.payload)["object_id"] == obj.id

    def test_ignite_idempotent(self, tmp_path):
        from app.simulation.fire import ignite_object

        settings = make_settings(tmp_path)
        engine = build(settings)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            loc = get_loc(session, settings)
            obj = add_object(session, settings, loc.id, "haystack", 0.9)
            ignite_object(session, settings.world.world_id, obj, 3, settings)
            ignite_object(session, settings.world.world_id, obj, 3, settings)
            assert (
                session.query(WorldEvent).filter_by(
                    event_type="OBJECT_BURNING").count()
            ) == 1


class TestSpread:
    def test_neighbour_ignites(self, tmp_path):
        from app.simulation.fire import ignite_object, run_fire_phase

        settings = make_settings(tmp_path, ignition_chance=0.9)
        engine = build(settings)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            loc = get_loc(session, settings)
            a = add_object(session, settings, loc.id, "wood_pile", 0.9)
            b = add_object(session, settings, loc.id, "haystack", 0.9)
            ignite_object(session, settings.world.world_id, a, 1, settings)
            session.commit()
            run_fire_phase(session, settings.world.world_id, 2, settings)
            # P = 0.9*0.9 = 0.81 → deterministic rng catches it
            assert session.get(WorldObject, b.id).burn_state == "burning"

    def test_non_flammable_never_spreads(self, tmp_path):
        from app.simulation.fire import ignite_object, run_fire_phase

        settings = make_settings(tmp_path, ignition_chance=0.9)
        engine = build(settings)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            loc = get_loc(session, settings)
            a = add_object(session, settings, loc.id, "wood_pile", 0.9)
            b = add_object(session, settings, loc.id, "anvil", 0.95)
            ignite_object(session, settings.world.world_id, a, 1, settings)
            session.commit()
            run_fire_phase(session, settings.world.world_id, 2, settings)
            # anvil not in flammable_types → never burns
            assert session.get(WorldObject, b.id).burn_state == "intact"


class TestDamage:
    def test_characters_on_location_take_damage(self, tmp_path):
        from app.simulation.fire import ignite_object, run_fire_phase

        settings = make_settings(tmp_path)
        engine = build(settings)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            loc = get_loc(session, settings)
            obj = add_object(session, settings, loc.id, "shed", 0.9)
            char = (
                session.query(Character)
                .filter_by(world_id=settings.world.world_id)
                .first()
            )
            char.location_id = loc.id
            session.commit()
            health0 = (
                session.query(CharacterHealth)
                .filter_by(character_id=char.id).one().health
            )
            ignite_object(session, settings.world.world_id, obj, 1, settings)
            session.commit()
            run_fire_phase(session, settings.world.world_id, 2, settings)
            health1 = (
                session.query(CharacterHealth)
                .filter_by(character_id=char.id).one().health
            )
            assert health1 < health0
            assert health1 == max(0.0, health0 - settings.fire.damage_per_day)


class TestWeatherCoupling:
    def test_spontaneous_only_hot_dry(self, tmp_path):
        from app.db.models import WeatherState
        from app.simulation.fire import run_fire_phase

        settings = make_settings(
            tmp_path, spontaneous_chance_per_day=5.0  # guarantees ignition
        )
        engine = build(settings)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            loc = get_loc(session, settings)
            obj = add_object(session, settings, loc.id, "haystack", 0.9)
            # hot dry day
            session.add(WeatherState(
                world_id=settings.world.world_id, day=3,
                temperature=31.0, wind=1.0, precipitation=0.0,
                cloudiness=0.1, visibility=20.0, source="synthetic",
            ))
            session.commit()
            run_fire_phase(session, settings.world.world_id, 3, settings)
            assert session.get(WorldObject, obj.id).burn_state == "burning"

    def test_no_spontaneous_in_rain(self, tmp_path):
        from app.db.models import WeatherState
        from app.simulation.fire import run_fire_phase

        settings = make_settings(
            tmp_path, spontaneous_chance_per_day=5.0
        )
        engine = build(settings)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            loc = get_loc(session, settings)
            obj = add_object(session, settings, loc.id, "haystack", 0.9)
            session.add(WeatherState(
                world_id=settings.world.world_id, day=3,
                temperature=31.0, wind=1.0, precipitation=9.0,
                cloudiness=0.95, visibility=8.0, source="synthetic",
            ))
            session.commit()
            run_fire_phase(session, settings.world.world_id, 3, settings)
            assert session.get(WorldObject, obj.id).burn_state == "intact"


class TestKeystone:
    def test_no_fires_default_reports_unchanged(self, tmp_path):
        """Default config: spontaneous_chance=0 → no fire events ever."""
        from app.simulation.engine import Engine, TickScheduler, WorldClock

        settings = make_settings(tmp_path)  # defaults
        engine = build(settings)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            sim = Engine(WorldClock(), TickScheduler())
            sim.step(2 * 1440, session=session,
                     world_id=settings.world.world_id, settings=settings)
            session.commit()
            types = {
                r[0] for r in session.query(WorldEvent.event_type).all()
            }
            assert "OBJECT_BURNING" not in types
            assert "OBJECT_BURNED" not in types


class TestInvariant:
    def test_fire_integrity(self, tmp_path):
        from app.simulation.fire import ignite_object
        from app.simulation.invariants import run_invariant_checks

        settings = make_settings(tmp_path)
        engine = build(settings)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            loc = get_loc(session, settings)
            obj = add_object(session, settings, loc.id, "wood_pile", 0.9)
            ignite_object(session, settings.world.world_id, obj, 1, settings)
            session.commit()
            results = run_invariant_checks(
                session, settings.world.world_id, settings
            )
        fire = next(r for r in results if r["name"] == "fire_integrity")
        assert fire["ok"], fire["details"]
