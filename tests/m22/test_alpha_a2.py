"""A2 Living Map Slice 2 (issue #85) — authoritative markers, LOD and presets."""
import os
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from app.api.app import create_app
from app.config.config import load_config
from app.db.models import Character, Location, bootstrap, create_engine_factory
from app.world.seed_world import seed_world

SETTINGS = None
LOW_MARKER_BUDGET = 120


def setup_module(module):
    global SETTINGS
    SETTINGS = load_config("config/default.yaml")


@pytest.fixture()
def world(tmp_path):
    settings = SETTINGS.model_copy(update={
        "persistence": SETTINGS.persistence.model_copy(
            update={"db_path": str(tmp_path / "w.db")}
        )
    })
    engine = create_engine_factory(settings)
    with sessionmaker(bind=engine)() as session:
        bootstrap(engine, settings, seed=42)
        seed_world(session, settings, settings.world.world_id)
        marker_location = (
            session.query(Location)
            .filter(
                Location.world_id == settings.world.world_id,
                Location.type != "island",
            )
            .order_by(Location.id)
            .first()
        )
        assert marker_location is not None
        session.add(Character(
            id="npc_a2_marker",
            world_id=settings.world.world_id,
            type="npc",
            first_name="Map",
            last_name="Resident",
            birth_date="2000-01-01",
            age=26,
            sex="F",
            alive=True,
            location_id=marker_location.id,
            created_at=0,
            updated_at=0,
            control_mode="AUTONOMOUS",
        ))
        session.commit()

    factory = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app(settings, factory)
    client = TestClient(app)
    client.post("/auth/register", json={
        "username": "a2user", "email": "a2@x.com",
        "password": "password123", "age_confirmed": True,
    })
    client.post("/auth/login", json={
        "username": "a2user", "password": "password123",
    })
    created = client.post("/characters", json={
        "name": "A2 Hero", "sex": "F", "age": 25,
    })
    assert created.status_code == 201
    return client


def test_locations_expose_authoritative_occupants(world):
    response = world.get("/locations")
    assert response.status_code == 200
    locations = response.json()

    occupants = [occupant for loc in locations for occupant in loc["occupants"]]
    assert occupants
    assert any(o["kind"] == "player" for o in occupants)
    assert any(o["kind"] == "npc" for o in occupants)

    for loc in locations:
        assert loc["occupants_count"] == len(loc["occupants"])
        for occupant in loc["occupants"]:
            assert set(occupant) == {"id", "name", "kind"}
            assert occupant["kind"] in {"player", "npc"}
            # A2 must not invent per-character simulation coordinates.
            assert "x" not in occupant
            assert "y" not in occupant


def test_low_mobile_seeded_marker_budget(world):
    """Seeded alpha contour stays well inside the documented Low-Mobile DOM budget.

    Low-Mobile clusters NPCs by authoritative location and caps relevant event
    groups at six, so this conservative upper bound is intentionally higher
    than the browser's actual rendered marker count.
    """
    locations = world.get("/locations").json()
    positioned = [
        loc for loc in locations
        if loc["x"] is not None and loc["y"] is not None and loc["type"] != "island"
    ]
    npc_locations = sum(
        1 for loc in positioned
        if any(o["kind"] == "npc" for o in loc["occupants"])
    )
    conservative_upper_bound = len(positioned) + npc_locations + 6 + 1
    assert conservative_upper_bound <= LOW_MARKER_BUDGET


def test_app_js_syntax_with_node():
    result = subprocess.run(
        ["node", "--check", "backend/app/web/app.js"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_a2_frontend_contract_pins():
    with open("backend/app/web/app.js", encoding="utf-8") as f:
        js = f.read()
    with open("backend/app/web/style.css", encoding="utf-8") as f:
        css = f.read()

    # Preserve the auth recursion guard and the existing static URL contour.
    assert 'if (path !== "/auth/me") route();' in js
    assert '"/static/static/map/world-island.jpg"' in js

    # LOD ends at Local and then hands off to the existing Scene block.
    assert 'mapLod: "island"' in js
    assert '["island", "region", "local"]' in js
    assert '"Открыть Scene"' in js
    assert 'document.getElementById("scene-view")' in js

    # Dynamic markers are grounded in authoritative location state.
    assert 'ev.location_id && visibleIds.has(ev.location_id)' in js
    assert '"data-location-id": String(loc.id)' in js
    assert 'const npcs = (loc.occupants || []).filter(o => o.kind === "npc")' in js
    assert "Presentation-only screen offset" in js

    # Weather/time and graphics presets alter presentation only.
    assert "S.weather.precipitation > 0.5" in js
    assert "S.weather.visibility < 1.0" in js
    assert '["high", "balanced", "low-mobile"]' in js
    assert "MAP_LOW_MARKER_BUDGET = 120" in js
    assert "mapReducedMotion" in js

    assert ".map-card.lod-region .map-stage" in css
    assert ".map-card.lod-local .map-stage" in css
    assert ".map-card.is-rain .map-rain" in css
    assert ".map-card.is-fog .map-fog" in css
    assert ".map-card.time-night .map-time-tint" in css
    assert ".map-card.quality-low-mobile" in css
    assert ".map-card.reduced-motion" in css
    assert "@media (prefers-reduced-motion: reduce)" in css
