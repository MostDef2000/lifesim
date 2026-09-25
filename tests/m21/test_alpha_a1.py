"""A1 Living Map LOD1 (issue #81) — API shape, coords determinism, UI pins."""
import os
import sys

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from app.api.app import create_app
from app.config.config import load_config
from app.db.models import (
    Character,
    CharacterTask,
    Location,
    bootstrap,
    create_engine_factory,
)
from app.world.seed_world import seed_world

SETTINGS = None


def setup_module(module):
    global SETTINGS
    SETTINGS = load_config("config/default.yaml")


@pytest.fixture()
def world(tmp_path):
    """Bootstrap + seed, then register/login + API-created player character."""
    settings = SETTINGS.model_copy(update={
        "persistence": SETTINGS.persistence.model_copy(
            update={"db_path": str(tmp_path / "w.db")}
        )
    })
    engine = create_engine_factory(settings)
    with sessionmaker(bind=engine)() as session:
        bootstrap(engine, settings, seed=42)
        seed_world(session, settings, settings.world.world_id)
        session.commit()
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app(settings, factory)
    client = TestClient(app)
    client.post("/auth/register", json={
        "username": "user1", "email": "u1@x.com",
        "password": "password123", "age_confirmed": True})
    client.post("/auth/login", json={
        "username": "user1", "password": "password123"})
    r = client.post("/characters", json={"name": "Test Hero", "sex": "M", "age": 25})
    assert r.status_code in (200, 201), f"character creation failed: {r.status_code} {r.text}"
    return settings, factory, client


def test_locations_endpoint(world):
    settings, factory, client = world
    res = client.get("/locations")
    assert res.status_code == 200
    locs = res.json()

    island = next(item for item in locs if item["type"] == "island")
    assert island["x"] is None

    settlement = next(item for item in locs if item["type"] == "settlement")
    assert settlement["x"] == 50.0
    assert "y" in settlement
    assert "parent_id" in settlement
    assert "occupants_count" in settlement
    # player character lives somewhere: at least one occupant overall
    assert sum(item["occupants_count"] for item in locs) >= 1


def test_house_coord_determinism(tmp_path):
    def seeded_house_coords(name):
        cfg = str(tmp_path / f"{name}.yaml")
        with open("config/default.yaml") as f:
            yaml.safe_dump(yaml.safe_load(f), open(cfg, "w"))
        settings = load_config(cfg)
        settings.persistence.db_path = str(tmp_path / f"{name}.db")
        engine = create_engine_factory(settings)
        with sessionmaker(bind=engine)() as session:
            bootstrap(engine, settings, seed=42)
            seed_world(session, settings, settings.world.world_id)
            session.commit()
            houses = (
                session.query(Location)
                .filter_by(type="house")
                .order_by(Location.id)
                .all()
            )
            return [(h.x, h.y) for h in houses]

    coords1 = seeded_house_coords("w1")
    coords2 = seeded_house_coords("w2")
    assert len(coords1) == 25  # 1 config house + 24 generated
    assert coords1 == coords2
    assert all(x is not None and y is not None for x, y in coords1)


def test_tasks_parameters(world):
    settings, factory, client = world
    with sessionmaker(bind=create_engine_factory(settings))() as session:
        char = session.query(Character).first()
        task = CharacterTask(
            character_id=char.id, priority=1, task_type="MOVE",
            status="planned", source="player",
            parameters='{"location_id": 10}', created_at=0,
        )
        session.add(task)
        session.commit()
        cid = char.id

    res = client.get(f"/characters/{cid}/tasks")
    assert res.status_code == 200
    planned = res.json()["planned"]
    assert len(planned) == 1
    assert planned[0]["parameters"] == {"location_id": 10}


def test_world_clock_fields(world):
    settings, factory, client = world
    res = client.get("/world")
    assert res.status_code == 200
    data = res.json()
    assert "time_scale" in data
    assert "is_paused" in data
    assert "game_timestamp" in data


def test_js_css_pins():
    with open("backend/app/web/app.js", "r") as f:
        js = f.read()
    assert "/static/map/base.jpg" in js
    assert "map-card" in js
    assert "map-badge" in js
    assert "time_scale" in js

    with open("backend/app/web/style.css", "r") as f:
        css = f.read()
    assert ".map-card" in css
    assert ".anchor.player" in css


def test_map_asset_exists():
    assert os.path.exists("backend/app/web/static/map/base.jpg")
    assert os.path.getsize("backend/app/web/static/map/base.jpg") < 1_000_000
