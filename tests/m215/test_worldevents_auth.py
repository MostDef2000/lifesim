"""#215 — GET /world/events is auth-gated (world journal close).

Contract under test (spec 009 AE4, revised by #215):
- anonymous → 401, JSON error body with a "detail" key (repo-wide API gate,
  same posture as /locations, /characters/{cid}, /characters/by-user/*);
- authenticated → 200, bare list; every row's key set EXACTLY
  {id, event_type, actor_id, location_id, day, game_timestamp, payload};
- rows come back id-ascending; since is an id-cursor (id > since) and limit
  caps the page — the pre-#215 polling contract must survive the gate.

History: the feed shipped PUBLIC by M5 design ("the world journal is
shared"); payloads ride player identity fields, so #215 added
Depends(current_user). tests/m9/test_chunk_b.py::TestAuthGate pins the
anonymous leg end-to-end; this module pins anonymous-401 + the full
authenticated response contract.
"""
import os
import random
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from app.api.auth import hash_password  # noqa: E402
from app.config.config import load_config  # noqa: E402
from app.db.models import User, bootstrap, create_engine_factory  # noqa: E402

SETTINGS = None

PW = "Correct-Horse-9!"

# the wire contract of one /world/events row (app.py get_world_events)
ROW_KEYS = {"id", "event_type", "actor_id", "location_id",
            "day", "game_timestamp", "payload"}


def setup_module(module):
    global SETTINGS
    SETTINGS = load_config("config/default.yaml")


class TestWorldEventsAuthGate:
    def _build(self, tmp_path):
        from sqlalchemy.orm import sessionmaker

        from app.api.app import create_app
        from app.characters.generator import generate_population
        from app.characters.player import create_player_character
        from app.world.seed_world import seed_world

        settings = SETTINGS.model_copy(update={
            "persistence": SETTINGS.persistence.model_copy(
                update={"db_path": str(tmp_path / "w215.db")}
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
            session.add(User(
                username="w215owner", email="w215a@x.com",
                password_hash=hash_password(PW),
                role="player", age_confirmed=True, created_at=0,
            ))
            session.commit()
            user_a = session.query(User).filter_by(username="w215owner").one()
            create_player_character(
                session, settings, settings.world.world_id,
                user_a, "Валда Тест", "F", 30, 0,
            )
            session.commit()
        app = create_app(settings, sessionmaker(bind=engine))
        client = TestClient(app)
        r = client.post("/auth/login", json={
            "username": "w215owner", "password": PW})
        assert r.status_code == 200, r.text
        return client

    @pytest.fixture()
    def env(self, tmp_path):
        return self._build(tmp_path)

    def test_anonymous_get_is_401(self, env):
        """#215 gate: a fresh cookie-less client on the same app gets 401,
        and the rejection is a structured JSON error (detail), not HTML."""
        anon = TestClient(env.app)
        r = anon.get("/world/events?since=0")
        assert r.status_code == 401, r.text
        body = r.json()
        assert isinstance(body, dict), body
        assert "detail" in body, body

    def test_authenticated_shape_pin(self, env):
        """Authorized caller keeps the full feed contract: bare list, exact
        per-row key set, id-ascending. Payload VALUES are world-seed driven
        and deliberately not pinned — the key set is the contract."""
        r = env.get("/world/events?since=0")
        assert r.status_code == 200, r.text
        rows = r.json()
        assert isinstance(rows, list)
        assert rows, "bootstrapped+seeded world must carry events"
        for row in rows:
            assert set(row.keys()) == ROW_KEYS, row
        ids = [row["id"] for row in rows]
        assert ids == sorted(ids), ids

    def test_cursor_and_limit_still_work_authenticated(self, env):
        """The gate must not break the SPA poll loop: since=<first id> with
        limit=1 returns the NEXT row only (id > since), never the cursor row."""
        full = env.get("/world/events?since=0").json()
        assert len(full) >= 1, full
        first_id = full[0]["id"]
        r = env.get(f"/world/events?since={first_id}&limit=1")
        assert r.status_code == 200, r.text
        rows = r.json()
        assert isinstance(rows, list)
        assert len(rows) <= 1, rows  # limit=1 caps the page
        for row in rows:
            assert row["id"] > first_id, row
        # a fresh seeded world has >1 event, so the cursor must actually move
        assert len(full) > 1, full
        assert len(rows) == 1, rows
        assert rows[0]["id"] == full[1]["id"], (rows, full[1])
