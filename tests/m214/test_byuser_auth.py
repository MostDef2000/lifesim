"""#214 — GET /characters/by-user/{user_id} is auth-gated (IDOR close).

Contract under test:
- anonymous → 401 (repo-wide API gate, same as /locations, /characters/{cid});
- the path user_id must equal the session user's id: any foreign id —
  existing or not — gets a uniform 403 "not your user" (deliberate: no
  user-id enumeration; the endpoint never 404s);
- the payload is bootstrap-only: key set EXACTLY
  {id, name, alive, control_mode} — no looks, no biography, no outfit_mode
  (PII rides the owner-gated GET /characters/{cid}; the #205 review removed
  outfit_mode, #214 removes looks/biography with it).

History: the endpoint shipped WITHOUT the auth dependency and its docstring
claimed "authenticated user" — the #205 acceptance exposed the anonymous
leak (JOURNAL 2026-10-16), ticket #214 closed the gate.
"""
import os
import random
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from tests.helpers import auth_client, make_user  # noqa: E402

from app.config.config import load_config  # noqa: E402
from app.db.models import bootstrap, create_engine_factory  # noqa: E402

SETTINGS = None


def setup_module(module):
    global SETTINGS
    SETTINGS = load_config("config/default.yaml")


class TestByUserAuthGate:
    def _build(self, tmp_path):
        from sqlalchemy.orm import sessionmaker

        from app.api.app import create_app
        from app.characters.generator import generate_population
        from app.characters.player import create_player_character
        from app.world.seed_world import seed_world

        settings = SETTINGS.model_copy(update={
            "persistence": SETTINGS.persistence.model_copy(
                update={"db_path": str(tmp_path / "w214.db")}
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
            user_a = make_user(
                session, "w214owner", "w214a@x.com", role="player")
            user_b = make_user(
                session, "w214other", "w214b@x.com", role="player")
            create_player_character(
                session, settings, settings.world.world_id,
                user_a, "Айдор Тест", "F", 30, 0,
            )
            session.commit()
            # The commit above expires ORM attributes (expire_on_commit);
            # reload them while the session is still open so the detached
            # instances stay usable by auth_client after the with-block.
            session.refresh(user_a)
            session.refresh(user_b)
        app = create_app(settings, sessionmaker(bind=engine))
        client_a = auth_client(app, settings, user_a)
        me = client_a.get("/auth/me").json()
        client_b = auth_client(app, settings, user_b)
        return client_a, client_b, me["id"]

    @pytest.fixture()
    def world(self, tmp_path):
        return self._build(tmp_path)

    def test_anonymous_401(self, world):
        client_a, _, a_id = world
        anon = TestClient(client_a.app)
        r = anon.get(f"/characters/by-user/{a_id}")
        assert r.status_code == 401, r.text

    def test_own_payload_key_set(self, world):
        """Full key-set pin: the bootstrap payload carries exactly the four
        fields the SPA bootstrap consumes (app.js:141) — no PII keys."""
        client_a, _, a_id = world
        r = client_a.get(f"/characters/by-user/{a_id}")
        assert r.status_code == 200, r.text
        rows = r.json()
        assert len(rows) == 1, rows
        assert set(rows[0].keys()) == {"id", "name", "alive", "control_mode"}

    def test_cross_user_403(self, world):
        client_a, client_b, a_id = world
        r = client_b.get(f"/characters/by-user/{a_id}")
        assert r.status_code == 403, r.text
        assert r.json()["detail"] == "not your user"
