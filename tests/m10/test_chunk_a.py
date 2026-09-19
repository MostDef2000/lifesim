"""M10 Chunk A tests (SPEC 010-weather, T4): core weather system."""
import os
import sys

from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from app.config.config import load_config  # noqa: E402
from app.db.models import (  # noqa: E402
    WeatherState,
    bootstrap,
    create_engine_factory,
)
from app.world.seed_world import seed_world  # noqa: E402

SETTINGS = None


def setup_module(module):
    global SETTINGS
    SETTINGS = load_config("config/default.yaml")


def make_settings(tmp_path, **weather_updates):
    return SETTINGS.model_copy(update={
        "persistence": SETTINGS.persistence.model_copy(
            update={"db_path": str(tmp_path / "w.db")}
        ),
        "weather": SETTINGS.weather.model_copy(update=weather_updates),
    })


def build(settings):
    engine = create_engine_factory(settings)
    with sessionmaker(bind=engine)() as session:
        bootstrap(engine, settings, seed=42)
        seed_world(session, settings, settings.world.world_id)
        session.commit()
    return engine


class TestGeneration:
    def test_deterministic(self, tmp_path):
        settings = make_settings(tmp_path)
        a = __import__("app.simulation.weather", fromlist=["generate_weather"]) \
            .generate_weather(10, 42, settings.weather)
        b = __import__("app.simulation.weather", fromlist=["generate_weather"]) \
            .generate_weather(10, 42, settings.weather)
        assert a == b
        c = __import__("app.simulation.weather", fromlist=["generate_weather"]) \
            .generate_weather(11, 42, settings.weather)
        assert (a.temperature, a.wind, a.precipitation) != \
               (c.temperature, c.wind, c.precipitation)

    def test_ranges(self, tmp_path):
        from app.simulation.weather import generate_weather

        settings = make_settings(tmp_path)
        for day in range(0, 400, 7):
            w = generate_weather(day, 42, settings.weather)
            assert -60 <= w.temperature <= 45
            assert 0 <= w.wind <= 15
            assert 0 <= w.precipitation <= 12
            assert 0 <= w.cloudiness <= 1
            assert w.visibility > 0
            assert w.source == "synthetic"

    def test_seasonal(self, tmp_path):
        """Winter (day 273-364) is colder than summer (day 91-182)."""
        from app.simulation.weather import generate_weather

        settings = make_settings(tmp_path)
        summer = [
            generate_weather(d, 42, settings.weather).temperature
            for d in range(120, 180, 10)
        ]
        winter = [
            generate_weather(d, 42, settings.weather).temperature
            for d in range(300, 360, 10)
        ]
        assert sum(summer) / len(summer) > sum(winter) / len(winter)


class TestRealDateMapping:
    def test_time_scale_mapping(self, tmp_path):
        """Sims-style: game day N maps to real start + N·(1440/time_scale) min,
        then minus year_lag."""
        from app.simulation.weather import real_date_for_game_day

        settings = make_settings(tmp_path)
        # default config: start 2026-09-16T00:00Z, time_scale=1 (headless:
        # 1 real min = 1 game min → 1440 real min per game day)
        d0 = real_date_for_game_day(0, settings)
        assert d0 == "2025-09-16"  # same calendar day, one year earlier
        d1 = real_date_for_game_day(1, settings)
        assert d1 == "2025-09-17"  # day 1 = next real date

        # Sims-style acceleration: time_scale=1440 → 1 real min per game day
        fast = settings.model_copy(update={
            "ticks": settings.ticks.model_copy(update={"time_scale": 1440.0}),
        })
        assert real_date_for_game_day(1, fast) == "2025-09-16"


class TestEngineEnsure:
    def test_days_produce_rows_and_event(self, tmp_path):
        from app.db.models import WorldEvent
        from app.simulation.engine import Engine, TickScheduler, WorldClock

        settings = make_settings(tmp_path)
        engine = build(settings)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            sim = Engine(WorldClock(), TickScheduler())
            sim.step(3 * 1440, session=session,
                     world_id=settings.world.world_id, settings=settings)
            session.commit()
            rows = session.query(WeatherState).order_by(WeatherState.day).all()
            # timestamp 4320 = start of day 3 → days 0..3 all ensured
            assert [r.day for r in rows] == [0, 1, 2, 3]
            events = session.query(WorldEvent).filter_by(
                event_type="WEATHER_CHANGED"
            ).all()
            assert len(events) == 4  # one per ensured day (0..3)

    def test_ensure_idempotent(self, tmp_path):
        from app.simulation.engine import Engine, TickScheduler, WorldClock

        settings = make_settings(tmp_path)
        engine = build(settings)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            sim = Engine(WorldClock(), TickScheduler())
            sim.step(2 * 1440, session=session,
                     world_id=settings.world.world_id, settings=settings)
            sim.step(30, session=session,
                     world_id=settings.world.world_id, settings=settings)
            session.commit()
            # 2*1440 → days 0,1,2 (timestamp 2880 = day 2); +30 min adds none
            assert session.query(WeatherState).count() == 3


class TestColdDecay:
    def test_frost_multiplies_hunger_decay(self, tmp_path):
        from app.characters.needs import apply_needs_decay
        from app.characters.player import create_player_character
        from app.db.models import CharacterNeeds
        from app.simulation.weather import need_decay_multiplier

        settings = make_settings(tmp_path)
        engine = build(settings)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            from app.api.auth import hash_password
            from app.db.models import User

            user = User(
                username="wtest", email="w@x.com",
                password_hash=hash_password("password123"),
                role="player", age_confirmed=True, created_at=0,
            )
            session.add(user)
            session.flush()
            create_player_character(
                session, settings, settings.world.world_id,
                user, "Погода Тест", "M", 30, 0,
            )
            session.commit()
            # synthetic weather for a frost-free sanity check first
            cold_row = WeatherState(
                world_id=settings.world.world_id, day=0,
                temperature=-15.0, wind=2.0, precipitation=0.0,
                cloudiness=0.2, visibility=20.0, source="synthetic",
            )
            warm_row = WeatherState(
                world_id=settings.world.world_id, day=1,
                temperature=20.0, wind=2.0, precipitation=0.0,
                cloudiness=0.2, visibility=20.0, source="synthetic",
            )
            session.add_all([cold_row, warm_row])
            session.commit()

            needs = session.query(CharacterNeeds).first()
            assert needs is not None
            cid = needs.character_id
            base_hunger = needs.hunger
            apply_needs_decay(session, settings.world.world_id, 0, 60, settings)
            cold_loss = base_hunger - session.query(CharacterNeeds).filter_by(
                character_id=cid).one().hunger
            assert need_decay_multiplier(cold_row, settings.weather) > 1.0
            assert need_decay_multiplier(warm_row, settings.weather) == 1.0
            assert cold_loss > 0


class TestKeystone:
    def test_disabled_no_rows_no_events(self, tmp_path):
        from app.db.models import WorldEvent
        from app.simulation.engine import Engine, TickScheduler, WorldClock

        settings = make_settings(tmp_path, enabled=False)
        engine = build(settings)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            sim = Engine(WorldClock(), TickScheduler())
            sim.step(2 * 1440, session=session,
                     world_id=settings.world.world_id, settings=settings)
            session.commit()
            assert session.query(WeatherState).count() == 0
            assert session.query(WorldEvent).filter_by(
                event_type="WEATHER_CHANGED").count() == 0


class TestInvariant:
    def test_weather_integrity(self, tmp_path):
        from app.simulation.engine import Engine, TickScheduler, WorldClock
        from app.simulation.invariants import run_invariant_checks

        settings = make_settings(tmp_path)
        engine = build(settings)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            sim = Engine(WorldClock(), TickScheduler())
            sim.step(1440, session=session,
                     world_id=settings.world.world_id, settings=settings)
            session.commit()
            results = run_invariant_checks(
                session, settings.world.world_id, settings
            )
        weather = next(r for r in results if r["name"] == "weather_integrity")
        assert weather["ok"], weather["details"]
