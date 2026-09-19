"""M16 Chunk A tests (SPEC 016-crime, T3): schema, commit, witnesses."""
import json
import os
import sys

from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from app.config.config import load_config  # noqa: E402
from app.db.models import (  # noqa: E402
    Account,
    Character,
    CharacterNeeds,
    Crime,
    Memory,
    Organization,
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


def make_settings(tmp_path, **crime_updates):
    from pathlib import Path

    tmp_path = Path(tmp_path)
    return SETTINGS.model_copy(update={
        "persistence": SETTINGS.persistence.model_copy(
            update={"db_path": str(tmp_path / "w.db")}
        ),
        "crime": SETTINGS.crime.model_copy(update=crime_updates),
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


class TestKeystone:
    def test_disabled_noop_and_no_police(self, tmp_path):
        from app.crime.crime import run_crime_phase, run_police_phase

        settings = make_settings(tmp_path)  # crime.enabled=False
        engine = build(settings)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            assert run_crime_phase(
                session, settings.world.world_id, 1, 1440, settings) == 0
            assert run_police_phase(
                session, settings.world.world_id, 1, 1440, settings) == {
                "fined": 0, "arrested": 0, "released": 0}
            assert session.query(Organization).filter_by(
                type="security").count() == 0
            assert session.query(Crime).count() == 0

    def test_keystone_engine_noop(self, tmp_path):
        from app.simulation.engine import Engine, TickScheduler, WorldClock

        settings = make_settings(tmp_path)
        engine = build(settings)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            sim = Engine(WorldClock(), TickScheduler())
            sim.step(1440, session=session,
                     world_id=settings.world.world_id, settings=settings)
            session.commit()
            types = {
                r[0] for r in session.query(WorldEvent.event_type).all()}
            assert "CRIME_COMMITED" not in types


class TestCommit:
    def test_theft_when_starving_broke(self, tmp_path):
        from app.crime.crime import run_crime_phase

        settings = make_settings(tmp_path, enabled=True)
        engine = build(settings)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            npc = (
                session.query(Character)
                .filter_by(world_id=settings.world.world_id)
                .filter(Character.user_id.is_(None))
                .first()
            )
            session.query(CharacterNeeds).filter_by(
                character_id=npc.id).update({"hunger": 10.0})
            acc = session.query(Account).filter_by(
                owner_type="character", owner_id=npc.id).first()
            acc.balance = 10
            food = (
                session.query(WorldObject)
                .filter(WorldObject.object_type == "food_bread",
                        WorldObject.quantity > 0)
                .order_by(WorldObject.id)
                .first()
            )
            assert food is not None, "seed must contain bread"
            food.location_id = npc.location_id
            food.quantity = 5
            session.commit()
            food_id = food.id
            # force chance: set theft_chance=1.0 via settings copy
            settings2 = make_settings(
                str(tmp_path) + "_x", enabled=True, theft_chance=1.0)
            settings2.persistence.db_path = settings.persistence.db_path
            run_crime_phase(
                session, settings.world.world_id, 1, 1440, settings2)
            session.commit()
            crimes = session.query(Crime).filter_by(
                crime_type="theft", actor_character_id=npc.id).all()
            assert len(crimes) >= 1
            assert crimes[0].target_object_id == food_id
            ev = session.query(WorldEvent).filter_by(
                event_type="CRIME_COMMITED").first()
            assert json.loads(ev.payload)["type"] == "theft"

    def test_no_crime_when_rich_fed(self, tmp_path):
        from app.crime.crime import run_crime_phase

        settings = make_settings(
            tmp_path, enabled=True, theft_chance=1.0)
        engine = build(settings)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            for needs in session.query(CharacterNeeds).all():
                needs.hunger = 90.0
                needs.social = 90.0
            session.query(Account).filter_by(owner_type="character").update(
                {"balance": 5000})
            session.commit()
            run_crime_phase(
                session, settings.world.world_id, 1, 1440, settings)
            session.commit()
            assert session.query(Crime).count() == 0


class TestWitnesses:
    def test_witness_memory_and_report(self, tmp_path):
        from app.crime.crime import run_crime_phase

        settings = make_settings(
            tmp_path, enabled=True, theft_chance=1.0, detection_base=1.0)
        engine = build(settings)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            npcs = (
                session.query(Character)
                .filter_by(world_id=settings.world.world_id)
                .filter(Character.user_id.is_(None))
                .all()
            )
            thief, witness = npcs[0], npcs[1]
            witness.location_id = thief.location_id  # co-located
            session.query(CharacterNeeds).filter_by(
                character_id=thief.id).update({"hunger": 10.0})
            acc = session.query(Account).filter_by(
                owner_type="character", owner_id=thief.id).first()
            acc.balance = 10
            food = (
                session.query(WorldObject)
                .filter(WorldObject.object_type == "food_bread")
                .first()
            )
            food.location_id = thief.location_id
            food.quantity = 5
            session.commit()
            run_crime_phase(
                session, settings.world.world_id, 1, 1440, settings)
            session.commit()
            crime = session.query(Crime).filter_by(
                actor_character_id=thief.id).first()
            assert crime is not None
            assert crime.status == "reported"
            mem = session.query(Memory).filter_by(
                character_id=witness.id, memory_type="crime_witness").all()
            assert len(mem) >= 1
            assert "Видел" in mem[0].summary
            rep = session.query(WorldEvent).filter_by(
                event_type="CRIME_REPORTED").first()
            assert json.loads(rep.payload)["suspect"] == thief.id

    def test_no_witness_stays_unreported(self, tmp_path):
        from app.crime.crime import run_crime_phase

        settings = make_settings(
            tmp_path, enabled=True, theft_chance=1.0, detection_base=0.0)
        engine = build(settings)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            npcs = (
                session.query(Character)
                .filter_by(world_id=settings.world.world_id)
                .filter(Character.user_id.is_(None))
                .all()
            )
            thief = npcs[0]
            # move others away
            for other in npcs[1:]:
                other.location_id = (thief.location_id + 1) % 1000
            session.query(CharacterNeeds).filter_by(
                character_id=thief.id).update({"hunger": 10.0})
            acc = session.query(Account).filter_by(
                owner_type="character", owner_id=thief.id).first()
            acc.balance = 10
            food = (
                session.query(WorldObject)
                .filter(WorldObject.object_type == "food_bread")
                .first()
            )
            food.location_id = thief.location_id
            food.quantity = 5
            session.commit()
            run_crime_phase(
                session, settings.world.world_id, 1, 1440, settings)
            session.commit()
            crime = session.query(Crime).filter_by(
                actor_character_id=thief.id).first()
            assert crime.status == "unreported"
            assert session.query(WorldEvent).filter_by(
                event_type="CRIME_REPORTED").count() == 0
