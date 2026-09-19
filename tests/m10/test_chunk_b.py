"""M10 Chunk B tests (SPEC 010-weather, T7): weather API, UI, Flux descriptor."""
import os
import sys

import pytest
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from fastapi.testclient import TestClient  # noqa: E402

from app.api.auth import hash_password  # noqa: E402
from app.config.config import load_config  # noqa: E402
from app.db.models import (  # noqa: E402
    User,
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


@pytest.fixture()
def world(tmp_path):
    settings = make_settings(tmp_path)
    engine = create_engine_factory(settings)
    with sessionmaker(bind=engine)() as session:
        bootstrap(engine, settings, seed=42)
        seed_world(session, settings, settings.world.world_id)
        session.add(User(
            username="wx", email="wx@x.com",
            password_hash=hash_password("password123"),
            role="player", age_confirmed=True, created_at=0,
        ))
        session.commit()
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    from app.api.app import create_app

    app = create_app(settings, factory)
    client = TestClient(app)
    client.post("/auth/login", json={"username": "wx", "password": "password123"})
    return settings, factory, client


class TestWeatherAPI:
    def test_requires_auth(self, world):
        settings, factory, _ = world
        from app.api.app import create_app

        fresh = TestClient(create_app(settings, factory))
        assert fresh.get("/weather").status_code == 401
        assert fresh.get("/weather/history").status_code == 401

    def test_current_day_fields(self, world):
        settings, factory, client = world
        data = client.get("/weather").json()
        assert data["enabled"] is True
        for key in ["day", "temperature", "wind", "precipitation",
                    "cloudiness", "visibility", "source", "description"]:
            assert key in data, key
        assert data["source"] == "synthetic"
        assert -60 <= data["temperature"] <= 45
        assert data["cloudiness"] <= 1

    def test_history_newest_first(self, world):
        settings, factory, client = world
        # ensure 3 days exist
        with factory() as session:
            for d in range(3):
                session.add(WeatherState(
                    world_id=settings.world.world_id, day=d,
                    temperature=10.0 - d, wind=1.0, precipitation=0.0,
                    cloudiness=0.2, visibility=20.0, source="synthetic",
                ))
            session.commit()
        hist = client.get("/weather/history?days=2").json()
        assert [r["day"] for r in hist] == [2, 1]
        assert client.get("/weather/history?days=99").status_code == 200


class TestHistoricalSource:
    def test_monkeypatched_fetch(self, world, tmp_path, monkeypatch):
        """Contract: mocked Open-Meteo response maps to fields (ms, 0-1)."""
        settings, factory, client = world
        import app.simulation.weather as wmod

        def fake_http_get(url, timeout):
            class FakeResp:
                def raise_for_status(self):
                    return None

                def json(self):
                    return {"daily": {
                        "temperature_2m_mean": [3.7],
                        "wind_speed_10m_max": [25.2],
                        "precipitation_sum": [1.4],
                        "cloud_cover_mean": [85.0],
                    }}
            return FakeResp()

        monkeypatch.setattr(wmod.httpx, "get", fake_http_get)
        hist_settings = make_settings(
            tmp_path, source="historical", cache_dir=str(tmp_path / "wc")
        )
        from app.simulation.weather import fetch_historical_weather

        sample = fetch_historical_weather("2025-09-16", hist_settings.weather)
        assert sample is not None
        assert sample.source == "historical"
        assert sample.temperature == 3.7
        assert sample.wind == 25.2  # ms requested via wind_speed_unit
        assert sample.precipitation == 1.4
        assert sample.cloudiness == 0.85  # percent → 0-1
        # cache file written (restart does not refetch)
        assert (tmp_path / "wc" / "2025-09-16.json").exists()
        monkeypatch.setattr(
            wmod.httpx, "get",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("refetch!")),
        )
        sample2 = fetch_historical_weather("2025-09-16", hist_settings.weather)
        assert sample2 == sample

    def test_offline_falls_back_to_synthetic(self, world, tmp_path, monkeypatch):
        settings, factory, client = world
        import app.simulation.weather as wmod

        def broken_get(url, timeout):
            raise ConnectionError("offline")

        monkeypatch.setattr(wmod.httpx, "get", broken_get)
        hist_settings = make_settings(
            tmp_path, source="historical", cache_dir=str(tmp_path / "wc2")
        )
        from app.simulation.weather import get_or_create_weather

        with factory() as session:
            row = get_or_create_weather(
                session, settings.world.world_id, 0, hist_settings
            )
            session.commit()
        assert row.source == "synthetic"  # graceful fallback, no crash

    def test_real_date_recorded(self, world, tmp_path, monkeypatch):
        settings, factory, client = world
        import app.simulation.weather as wmod

        monkeypatch.setattr(
            wmod.httpx, "get",
            lambda url, timeout: (_ for _ in ()).throw(ConnectionError()),
        )
        hist_settings = make_settings(
            tmp_path, source="historical", cache_dir=str(tmp_path / "wc3")
        )
        from app.simulation.weather import get_or_create_weather

        with factory() as session:
            row = get_or_create_weather(
                session, settings.world.world_id, 0, hist_settings
            )
            session.commit()
        # real_date maps even on fallback (day 0 → 2025-09-16 by config)
        assert row.real_date == "2025-09-16"


class TestFluxDescriptor:
    def test_weather_in_scene_prompt(self, world):
        settings, factory, client = world
        with factory() as session:
            from app.db.models import Location
            from app.visual.descriptor import build_prompt, build_scene_descriptor

            loc = session.query(Location).filter_by(
                world_id=settings.world.world_id).first()
            session.add(WeatherState(
                world_id=settings.world.world_id, day=0,
                temperature=-5.0, wind=2.0, precipitation=3.0,
                cloudiness=0.9, visibility=8.0, source="synthetic",
            ))
            session.commit()
            d = build_scene_descriptor(session, settings.world.world_id, loc.id)
            assert "снег" in d["weather"]  # t=-5 + precipitation>0.5 → снег
            prompt = build_prompt(d)
        assert "Погода" not in prompt  # raw sample string, not UI prefix

    def test_no_row_keeps_clear_byte_identity(self, world):
        settings, factory, client = world
        with factory() as session:
            from app.db.models import Location
            from app.visual.descriptor import build_scene_descriptor

            loc = session.query(Location).filter_by(
                world_id=settings.world.world_id).first()
            d = build_scene_descriptor(session, settings.world.world_id, loc.id)
            assert d["weather"] == "clear"  # keystone: pre-weather pin intact


class TestUI:
    def test_header_renders_weather(self, world):
        _, _, client = world
        js = client.get("/static/app.js").text
        assert "Погода: " in js
        assert '"/weather"' in js  # fetched on world view load
