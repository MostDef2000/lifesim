"""M13 Chunk A tests (SPEC 013-external2, T3): NPC trips + supply/demand."""
import json
import os
import sys

from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from app.config.config import load_config  # noqa: E402
from app.db.models import (  # noqa: E402
    Character,
    CharacterHealth,
    CharacterTask,
    ExternalService,
    WorldEvent,
    bootstrap,
    create_engine_factory,
)
from app.world.seed_world import seed_world  # noqa: E402

SETTINGS = None


def setup_module(module):
    global SETTINGS
    SETTINGS = load_config("config/default.yaml")


def make_settings(tmp_path, **external_updates):
    return SETTINGS.model_copy(update={
        "persistence": SETTINGS.persistence.model_copy(
            update={"db_path": str(tmp_path / "w.db")}
        ),
        "external": SETTINGS.external.model_copy(update=external_updates),
    })


def build(settings):
    import random as _r

    from app.characters.generator import generate_population
    from app.world.seed_external import seed_external_world

    engine = create_engine_factory(settings)
    with sessionmaker(bind=engine)() as session:
        bootstrap(engine, settings, seed=42)
        seed_world(session, settings, settings.world.world_id)
        generate_population(
            session, settings, _r.Random(42),
            settings.world.world_id, 20,
        )
        seed_external_world(
            session, settings, settings.world.world_id, _r.Random(7)
        )
        session.commit()
    return engine


class TestNpcUtilityTrips:
    def test_disabled_no_trips(self, tmp_path):
        from app.external.npc_trips import run_npc_trip_phase

        settings = make_settings(tmp_path)  # npc_utility=false default
        engine = build(settings)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            assert run_npc_trip_phase(
                session, settings.world.world_id, 1, settings) == 0
            assert session.query(CharacterTask).filter_by(
                task_type="TRAVEL_EXTERNAL").count() == 0

    def test_sick_npc_takes_trip(self, tmp_path):
        from app.external.npc_trips import run_npc_trip_phase

        settings = make_settings(tmp_path, npc_utility=True)
        engine = build(settings)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            npc = (
                session.query(Character)
                .filter_by(world_id=settings.world.world_id)
                .filter(Character.user_id.is_(None))
                .first()
            )
            session.query(CharacterHealth).filter_by(
                character_id=npc.id).update({"health": 15.0})
            session.commit()
            count = run_npc_trip_phase(
                session, settings.world.world_id, 1, settings)
            assert count >= 1
            task = session.query(CharacterTask).filter_by(
                character_id=npc.id, task_type="TRAVEL_EXTERNAL",
                source="utility").first()
            assert task is not None
            params = json.loads(task.parameters)
            assert params["purpose"].startswith("treatment")
            # service treatment exists
            svc = session.get(ExternalService, params["service_id"])
            assert svc.service_type == "treatment"

    def test_cooldown_prevents_repeat(self, tmp_path):
        from app.external.npc_trips import run_npc_trip_phase

        settings = make_settings(tmp_path, npc_utility=True)
        engine = build(settings)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            npc = (
                session.query(Character)
                .filter_by(world_id=settings.world.world_id)
                .filter(Character.user_id.is_(None))
                .first()
            )
            session.query(CharacterHealth).filter_by(
                character_id=npc.id).update({"health": 15.0})
            session.add(WorldEvent(
                world_id=settings.world.world_id,
                event_type="TRAVEL_EXTERNAL_RETURNED", actor_id=npc.id,
                payload=json.dumps({"day": 20, "items": []}),
                game_timestamp=20 * 1440,
            ))
            session.commit()
            # day 25: within cooldown of day-20 return
            assert run_npc_trip_phase(
                session, settings.world.world_id, 25, settings) == 0

    def test_keystone_engine_phases_noop(self, tmp_path):
        from app.simulation.engine import Engine, TickScheduler, WorldClock

        settings = make_settings(tmp_path)  # flags false
        engine = build(settings)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            sim = Engine(WorldClock(), TickScheduler())
            sim.step(1440, session=session,
                     world_id=settings.world.world_id, settings=settings)
            session.commit()
            types = {
                r[0] for r in session.query(WorldEvent.event_type).all()
            }
            assert "TRAVEL_EXTERNAL_DEPARTED" not in types
            mults = [
                s.price_multiplier
                for s in session.query(ExternalService).all()
            ]
            assert all(m == 1.0 for m in mults)


class TestSupplyDemand:
    def test_multiplier_up_then_decay(self, tmp_path):
        from app.external.npc_trips import update_service_multipliers

        settings = make_settings(tmp_path, supply_demand=True)
        engine = build(settings)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            svc = (
                session.query(ExternalService)
                .filter(ExternalService.item_type.isnot(None))
                .first()
            )
            assert svc is not None
            base_mult = svc.price_multiplier
            # purchase yesterday
            session.add(WorldEvent(
                world_id=settings.world.world_id,
                event_type="TRAVEL_EXTERNAL_RETURNED", actor_id="npc_0001",
                payload=json.dumps({
                    "day": 4, "service_id": svc.id,
                    "items": [{"item_type": svc.item_type, "qty": 3}],
                }),
                game_timestamp=4 * 1440,
            ))
            session.commit()
            update_service_multipliers(
                session, settings.world.world_id, 5, settings)
            assert svc.price_multiplier > base_mult
            m_after_purchase = svc.price_multiplier
            # no purchase next day → decay toward 1.0
            session.add(WorldEvent(
                world_id=settings.world.world_id,
                event_type="TRAVEL_EXTERNAL_RETURNED", actor_id="npc_0002",
                payload=json.dumps({"day": 5, "service_id": 0, "items": []}),
                game_timestamp=5 * 1440,
            ))
            session.commit()
            update_service_multipliers(
                session, settings.world.world_id, 6, settings)
            assert svc.price_multiplier < m_after_purchase
            assert svc.price_multiplier >= 0.8

    def test_capped_at_1_5(self, tmp_path):
        from app.external.npc_trips import update_service_multipliers

        settings = make_settings(tmp_path, supply_demand=True)
        engine = build(settings)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            svc = (
                session.query(ExternalService)
                .filter(ExternalService.item_type.isnot(None))
                .first()
            )
            svc.price_multiplier = 1.49
            session.add(WorldEvent(
                world_id=settings.world.world_id,
                event_type="TRAVEL_EXTERNAL_RETURNED", actor_id="npc_0001",
                payload=json.dumps({
                    "day": 4, "service_id": svc.id,
                    "items": [{"item_type": svc.item_type, "qty": 50}],
                }),
                game_timestamp=4 * 1440,
            ))
            session.commit()
            update_service_multipliers(
                session, settings.world.world_id, 5, settings)
            assert svc.price_multiplier <= 1.5

    def test_disabled_no_update(self, tmp_path):
        from app.external.npc_trips import update_service_multipliers

        settings = make_settings(tmp_path)  # supply_demand=false
        engine = build(settings)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            svc = session.query(ExternalService).first()
            svc.price_multiplier = 1.25
            session.commit()
            update_service_multipliers(
                session, settings.world.world_id, 5, settings)
            assert session.get(ExternalService, svc.id).price_multiplier == 1.25
