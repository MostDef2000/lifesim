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
    assert settlement["x"] == 57.6
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
    # Pin the sparse RNG-free town grid: first the config-defined "home",
    # then 24 generated houses in row-major seed order from origin (42.0, 6.6).
    # 8x4 grid (3.9%/4.6% steps): 7 POI cells + 1 pier-clearance cell skipped.
    expected = [
        (45.9, 15.8),
        (42.0, 6.6), (45.9, 6.6), (49.8, 6.6), (53.7, 6.6),
        (57.6, 6.6), (65.4, 6.6), (69.3, 6.6),
        (42.0, 11.2), (45.9, 11.2), (49.8, 11.2), (53.7, 11.2),
        (61.5, 11.2), (65.4, 11.2), (69.3, 11.2),
        (42.0, 15.8), (49.8, 15.8), (65.4, 15.8), (69.3, 15.8),
        (42.0, 20.4), (45.9, 20.4), (53.7, 20.4), (57.6, 20.4),
        (65.4, 20.4), (69.3, 20.4),
    ]
    flat = [v for pair in coords1 for v in pair]
    expected_flat = [v for pair in expected for v in pair]
    assert flat == pytest.approx(expected_flat)


def test_production_config_mirrors_map_coords():
    """Prod config must carry default.yaml map coords: load_config reads only
    the given YAML (no merge), a missing block silently seeds all x/y NULL."""
    prod = load_config("config/production.yaml")
    assert prod.locations.coords is not None
    assert prod.locations.coords == SETTINGS.locations.coords


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
    assert "/static/map/world-island.jpg" in js
    assert "map-card" in js
    assert "map-badge" in js
    assert "time_scale" in js
    # anchors expose the location name to assistive tech (visible "House N"
    # labels stay suppressed, so houses rely on this aria-label)
    assert '"aria-label": l.name' in js
    # bottom-row anchors get the "above" class so their label flips
    assert '? "above" : ""' in js

    with open("backend/app/web/style.css", "r") as f:
        css = f.read()
    assert ".map-card" in css
    assert ".anchor.player" in css
    # labels hidden by default; revealed on hover/focus/current location
    label_rule = css.split(".anchor .map-label {", 1)[1].split("}", 1)[0]
    assert "display: none" in label_rule
    assert ".anchor:hover .map-label" in css
    assert ".anchor:focus-visible .map-label" in css
    assert ".anchor.here .map-label" in css
    # round-2 vision review: responsive single-column grid + compact anchors
    assert "@media (max-width: 880px)" in css
    grid_media = css.split("@media (max-width: 880px) {", 1)[1].split("\n}", 1)[0]
    assert ".grid { grid-template-columns: 1fr" in grid_media
    assert "@media (max-width: 520px)" in css
    small_media = css.split("@media (max-width: 520px) {", 1)[1].split("\n}", 1)[0]
    assert ".anchor .dot" in small_media
    assert "width: 7px" in small_media
    assert ".anchor .map-label" in small_media
    assert "font-size: 9px" in small_media
    # active/current anchor paints above the later-sibling house dots
    assert ".anchor.here {\n  z-index: 3;\n}" in css
    player_rule = css.split(".anchor.player {", 1)[1].split("}", 1)[0]
    assert "z-index: 1" in player_rule
    assert "pointer-events: none" in player_rule
    assert "cursor: default" in player_rule
    # labels for low anchors flip above the dot
    above_rule = css.split(".anchor.above .map-label {", 1)[1].split("}", 1)[0]
    assert "top: auto" in above_rule
    assert "bottom: 10px" in above_rule


def test_map_asset_exists():
    assert os.path.exists("backend/app/web/static/map/world-island.jpg")
    assert os.path.getsize("backend/app/web/static/map/world-island.jpg") < 1_000_000
    assert os.path.exists("backend/app/web/static/map/landing-island.jpg")
    assert os.path.getsize("backend/app/web/static/map/landing-island.jpg") < 1_000_000
