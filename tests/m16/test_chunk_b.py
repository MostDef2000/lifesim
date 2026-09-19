"""M16 Chunk B tests (SPEC 016-crime, T5): police resolution, prison."""
import json
import os
import sys

from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from app.config.config import load_config  # noqa: E402
from app.db.models import (  # noqa: E402
    Account,
    Character,
    Crime,
    Organization,
    WorldEvent,
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


def make_reported_crime(session, settings, balance):
    """Seed one reported theft by a chosen NPC."""

    npc = (
        session.query(Character)
        .filter_by(world_id=settings.world.world_id)
        .filter(Character.user_id.is_(None))
        .order_by(Character.id)
        .first()
    )
    acc = session.query(Account).filter_by(
        owner_type="character", owner_id=npc.id).first()
    acc.balance = balance
    crime = Crime(
        world_id=settings.world.world_id, crime_type="theft",
        actor_character_id=npc.id, location_id=npc.location_id,
        target_object_id=None, day=1, status="reported",
        created_at=1440,
    )
    session.add(crime)
    session.flush()
    return npc, crime


class TestPolice:
    def test_police_seeded_when_enabled(self, tmp_path):
        settings = make_settings(tmp_path, enabled=True)
        engine = build(settings)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            org = session.query(Organization).filter_by(
                type="security").one()
            assert "Полиция" in org.name
            acc = session.query(Account).filter_by(
                owner_type="organization", owner_id=str(org.id)).one()
            assert acc.balance == 5000

    def test_fine_paid_from_ledger(self, tmp_path):
        from app.crime.crime import run_police_phase

        settings = make_settings(tmp_path, enabled=True)
        engine = build(settings)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            npc, crime = make_reported_crime(session, settings, 1000)
            org = session.query(Organization).filter_by(
                type="security").one()
            org_acc = session.query(Account).filter_by(
                owner_type="organization", owner_id=str(org.id)).one()
            org_balance_before = org_acc.balance
            session.commit()
            counters = run_police_phase(
                session, settings.world.world_id, 2, 2880, settings)
            session.commit()
            assert counters["fined"] == 1
            resolved = session.get(Crime, crime.id)
            assert resolved.status == "resolved"
            assert resolved.resolution == "fine"
            acc = session.query(Account).filter_by(
                owner_type="character", owner_id=npc.id).one()
            assert acc.balance == 1000 - settings.crime.fine_theft
            assert session.query(Account).filter_by(
                owner_type="organization", owner_id=str(org.id)).one() \
                .balance == org_balance_before + settings.crime.fine_theft
            ev = session.query(WorldEvent).filter_by(
                event_type="FINE_PAID").one()
            assert json.loads(ev.payload)["amount"] == \
                settings.crime.fine_theft

    def test_prison_arrest_release(self, tmp_path):
        from app.crime.crime import run_police_phase

        settings = make_settings(
            tmp_path, enabled=True, prison_days=3)
        engine = build(settings)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            npc, crime = make_reported_crime(session, settings, 10)
            home = npc.location_id
            session.commit()
            counters = run_police_phase(
                session, settings.world.world_id, 2, 2880, settings)
            session.commit()
            assert counters["arrested"] == 1
            fresh = session.get(Character, npc.id)
            assert fresh.prison_until_day == 2 + 3
            assert session.get(Crime, crime.id).resolution == "prison"
            arest = session.query(WorldEvent).filter_by(
                event_type="ARRESTED").one()
            assert json.loads(arest.payload)["release_day"] == 5
            # day 3: still imprisoned (guard)
            from app.actions.lifecycle import progress_tick

            progress_tick(session, settings.world.world_id, 3 * 1440, settings)
            session.commit()
            assert session.get(Character, npc.id).prison_until_day == 5
            # day 5: release
            counters2 = run_police_phase(
                session, settings.world.world_id, 5, 5 * 1440, settings)
            session.commit()
            assert counters2["released"] == 1
            fresh = session.get(Character, npc.id)
            assert fresh.prison_until_day is None
            rel = session.query(WorldEvent).filter_by(
                event_type="RELEASED").one()
            assert rel.actor_id == npc.id
            assert fresh.location_id == home

    def test_prison_progress_tick_pinned(self, tmp_path):
        """§35: prisoner pinned to police location, no new decisions."""
        from app.crime.crime import police_location, run_police_phase

        settings = make_settings(
            tmp_path, enabled=True, prison_days=7)
        engine = build(settings)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            npc, _ = make_reported_crime(session, settings, 10)
            session.commit()
            run_police_phase(
                session, settings.world.world_id, 2, 2880, settings)
            session.commit()
            prison_loc = police_location(
                session, settings.world.world_id, settings)
            fresh = session.get(Character, npc.id)
            assert fresh.location_id == prison_loc.id
            # tasks: no new tasks while imprisoned
            from app.actions.lifecycle import progress_tick
            from app.db.models import CharacterTask

            progress_tick(
                session, settings.world.world_id, 4 * 1440, settings)
            session.commit()
            tasks = session.query(CharacterTask).filter_by(
                character_id=npc.id).all()
            # no new tasks created after arrest moment
            assert all(t.created_at <= 2880 for t in tasks)

    def test_dead_suspect_case_closed(self, tmp_path):
        from app.crime.crime import run_police_phase

        settings = make_settings(tmp_path, enabled=True)
        engine = build(settings)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            npc, crime = make_reported_crime(session, settings, 10)
            npc.alive = False
            session.commit()
            run_police_phase(
                session, settings.world.world_id, 2, 2880, settings)
            session.commit()
            resolved = session.get(Crime, crime.id)
            assert resolved.status == "resolved"


class TestInvariant:
    def test_crime_integrity_green(self, tmp_path):
        from app.crime.crime import run_police_phase
        from app.simulation.invariants import run_invariant_checks

        settings = make_settings(tmp_path, enabled=True)
        engine = build(settings)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            npc, crime = make_reported_crime(session, settings, 1000)
            session.commit()
            run_police_phase(
                session, settings.world.world_id, 2, 2880, settings)
            session.commit()
            results = run_invariant_checks(
                session, settings.world.world_id, settings)
            inv = next(
                r for r in results if r["name"] == "crime_integrity")
            assert inv["ok"], inv["details"]
