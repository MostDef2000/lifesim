"""#291 — API pins: the "text" field on the world-event wire.

- GET /world/events (authenticated): every row carries "text" — a
  non-empty string; for a known type (CHARACTER_CREATED, emitted by
  generate_population) it is a human phrase, never the raw code;
- the WS polling serializer (poll_events) includes "text" in every dict.

Seeded TestClient pattern from tests/m215/test_worldevents_auth.py
(bootstrap + seed_world + generate_population + login).
"""

import os
import random
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from tests.helpers import auth_client, make_user  # noqa: E402

from app.config.config import load_config  # noqa: E402
from app.db.models import bootstrap, create_engine_factory  # noqa: E402

SETTINGS = None


def setup_module(module):
    global SETTINGS
    SETTINGS = load_config("config/default.yaml")


def _build(tmp_path):
    from sqlalchemy.orm import sessionmaker

    from app.api.app import create_app
    from app.characters.generator import generate_population
    from app.world.seed_world import seed_world

    settings = SETTINGS.model_copy(update={
        "persistence": SETTINGS.persistence.model_copy(
            update={"db_path": str(tmp_path / "w291.db")}
        ),
    })
    engine = create_engine_factory(settings)
    with sessionmaker(bind=engine)() as session:
        bootstrap(engine, settings, seed=42)
        seed_world(session, settings, settings.world.world_id)
        generate_population(
            session, settings, random.Random(42),
            settings.world.world_id, 20,
        )
        user = make_user(session, "w291owner", "w291a@x.com", role="user")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app(settings, factory)
    client = auth_client(app, settings, user)
    return settings, factory, client


@pytest.fixture()
def env(tmp_path):
    return _build(tmp_path)


def test_rest_world_events_rows_carry_text(env):
    """Every /world/events row carries "text" — a non-empty string; for the
    known CHARACTER_CREATED type it is a human phrase, never the raw code."""
    settings, factory, client = env
    r = client.get("/world/events?since=0")
    assert r.status_code == 200, r.text
    rows = r.json()
    assert isinstance(rows, list) and rows, "seeded world must carry events"
    for row in rows:
        assert "text" in row, row
        assert isinstance(row["text"], str) and row["text"].strip(), row
    # known type: generate_population emits one CHARACTER_CREATED per NPC
    created = [row for row in rows if row["event_type"] == "CHARACTER_CREATED"]
    assert created, "seeded world must carry CHARACTER_CREATED events"
    for row in created:
        assert row["text"] != "CHARACTER_CREATED", row
        assert "Новый житель" in row["text"], row


def test_ws_poll_events_rows_carry_text(env):
    """The WS polling serializer (the live-feed path) includes "text" in
    every dict — same contract as the REST feed."""
    settings, factory, client = env
    from app.api.ws import poll_events

    wid = settings.world.world_id
    with factory() as s:
        polled = poll_events(s, wid, 0)
    assert polled, "seeded world must carry events"
    for row in polled:
        assert "text" in row, row
        assert isinstance(row["text"], str) and row["text"].strip(), row
    created = [row for row in polled if row["event_type"] == "CHARACTER_CREATED"]
    assert created, "seeded world must carry CHARACTER_CREATED events"
    for row in created:
        assert row["text"] != "CHARACTER_CREATED", row
