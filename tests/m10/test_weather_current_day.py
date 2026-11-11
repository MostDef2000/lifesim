"""SPEC 025: /weather current-day ensure quirk — regression tests.

These tests failed on base 014a16c before the SPEC 025 fix and now pin
the quirk:

get_or_create_weather (backend/app/simulation/weather.py) computes
start_day = max(day)+1, so when the requested day's row already exists
the catch-up loop is empty and the function returns None — contradicting
its docstring ("Returns the current day's row (or None if disabled)").
The engine ensures the current-day row every tick, so on a live world
GET /weather maps the current day to {"enabled": false}.

These tests pin the promised semantics:
- repeated ensure of an existing current-day row returns that row
  (no duplicate rows, no extra WEATHER_CHANGED events);
- repeated GET /weather stays enabled:true with identical values;
- disabled semantics unchanged (None, no rows).
"""
import os
import sys

import pytest
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from app.config.config import load_config  # noqa: E402
from app.db.models import (  # noqa: E402
    WeatherState,
    WorldEvent,
    bootstrap,
    create_engine_factory,
)
from app.world.seed_world import seed_world  # noqa: E402
from tests.helpers import auth_client, make_user  # noqa: E402

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


class TestRepeatedEnsure:
    def test_repeated_ensure_returns_existing_row(self, tmp_path):
        """AC(a): ensure day N, then ensure day N again → the same row
        (not None), WeatherState row count unchanged, WEATHER_CHANGED
        event count unchanged."""
        from app.simulation.weather import get_or_create_weather

        settings = make_settings(tmp_path)
        engine = build(settings)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            world_id = settings.world.world_id
            first = get_or_create_weather(session, world_id, 2, settings)
            session.commit()
            assert first is not None
            assert first.day == 2
            rows_before = session.query(WeatherState).count()
            events_before = session.query(WorldEvent).filter_by(
                event_type="WEATHER_CHANGED"
            ).count()

            second = get_or_create_weather(session, world_id, 2, settings)
            session.commit()

            # Failed on base 014a16c (pre-fix): second was None — the quirk this pins
            assert second is not None
            assert second.day == 2
            assert second.temperature == first.temperature
            assert session.query(WeatherState).count() == rows_before
            assert session.query(WorldEvent).filter_by(
                event_type="WEATHER_CHANGED"
            ).count() == events_before

    def test_disabled_returns_none_and_no_rows(self, tmp_path):
        """AC(c): weather disabled → None, no rows, no events."""
        from app.simulation.weather import get_or_create_weather

        settings = make_settings(tmp_path, enabled=False)
        engine = build(settings)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            world_id = settings.world.world_id
            assert get_or_create_weather(session, world_id, 0, settings) is None
            session.commit()
            assert session.query(WeatherState).count() == 0
            assert session.query(WorldEvent).filter_by(
                event_type="WEATHER_CHANGED"
            ).count() == 0


@pytest.fixture()
def world(tmp_path):
    settings = make_settings(tmp_path)
    engine = create_engine_factory(settings)
    with sessionmaker(bind=engine)() as session:
        bootstrap(engine, settings, seed=42)
        seed_world(session, settings, settings.world.world_id)
        # #309: local register/login gone — create the user directly and
        # sign the app's own session cookie.
        wx = make_user(session, "wx", "wx@x.com")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    from app.api.app import create_app

    app = create_app(settings, factory)
    client = auth_client(app, settings, wx)
    return settings, factory, client


class TestWeatherRoute:
    def test_second_get_stays_enabled_with_same_values(self, world):
        """AC(b): first GET /weather → 200 enabled:true with the full
        shape; second GET /weather → 200 enabled:true with the same
        day/temperature/description. Failed on base 014a16c (pre-fix):
        the second response was {"enabled": false}."""
        settings, factory, client = world
        first = client.get("/weather")
        assert first.status_code == 200
        data1 = first.json()
        assert data1["enabled"] is True
        for key in ["day", "temperature", "wind", "precipitation",
                    "cloudiness", "visibility", "source", "description"]:
            assert key in data1, key

        second = client.get("/weather")
        assert second.status_code == 200
        data2 = second.json()
        # Failed on base 014a16c (pre-fix): {"enabled": false}
        assert data2["enabled"] is True
        assert data2["day"] == data1["day"]
        assert data2["temperature"] == data1["temperature"]
        assert data2["description"] == data1["description"]
