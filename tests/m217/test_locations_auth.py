"""#217 — GET /locations/{loc_id} is auth-gated (presence-ids leak close).

Contract under test (spec 005 R8, revised by #217):
- anonymous → 401, JSON error body with a "detail" key (repo-wide API gate,
  same posture as /world/events, /characters/{cid}, /characters/by-user/*);
- authenticated → 200, key set EXACTLY {id, type, name, characters_here};
- unknown id with an authenticated client → 404 (the existence oracle
  survives the gate and stays behind it);
- NPC presence (characters_here) is readable by an authenticated user who
  owns NO character — the gate is authentication, not character ownership.

History: the endpoint shipped PUBLIC by M5 design (spec 005 R8 said
«/locations публичны»); characters_here discloses presence ids, so #217
added Depends(current_user) — same leak family as #215 (/world/events).
tests/m9/test_chunk_b.py::TestAuthGate pins the anonymous leg end-to-end;
this module pins anonymous-401 + the full authenticated response contract.
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
FACTORY = None

PW = "Correct-Horse-9!"

# the wire contract of GET /locations/{loc_id} (app.py get_location)
LOC_KEYS = {"id", "type", "name", "characters_here"}


def setup_module(module):
    global SETTINGS
    SETTINGS = load_config("config/default.yaml")


class TestLocationsAuthGate:
    def _build(self, tmp_path):
        from sqlalchemy.orm import sessionmaker

        from app.api.app import create_app
        from app.characters.generator import generate_population
        from app.world.seed_world import seed_world

        global FACTORY
        settings = SETTINGS.model_copy(update={
            "persistence": SETTINGS.persistence.model_copy(
                update={"db_path": str(tmp_path / "w217.db")}
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
            # this user deliberately owns NO character: the gate must be
            # authentication, not character ownership (#217, leg d)
            session.add(User(
                username="w217owner", email="w217a@x.com",
                password_hash=hash_password(PW),
                role="player", age_confirmed=True, created_at=0,
            ))
            session.commit()
        FACTORY = sessionmaker(bind=engine, expire_on_commit=False)
        app = create_app(settings, FACTORY)
        client = TestClient(app)
        r = client.post("/auth/login", json={
            "username": "w217owner", "password": PW})
        assert r.status_code == 200, r.text
        return client

    @pytest.fixture()
    def env(self, tmp_path):
        return self._build(tmp_path)

    def _populated_location(self):
        """(location_id, npc_id) of a seeded NPC — no owned character needed."""
        from app.db.models import Character

        with FACTORY() as s:
            npc = (
                s.query(Character)
                .filter(Character.id.like("npc_%"))
                .filter_by(alive=True)
                .order_by(Character.id)
                .first()
            )
            assert npc is not None, "seeded population must contain NPCs"
            return npc.location_id, npc.id

    def test_anonymous_get_is_401(self, env):
        """#217 gate: a fresh cookie-less client on the same app gets 401
        for an EXISTING location (presence ids are no longer disclosed),
        and the rejection is a structured JSON error (detail), not HTML."""
        loc_id, _ = self._populated_location()
        anon = TestClient(env.app)
        r = anon.get(f"/locations/{loc_id}")
        assert r.status_code == 401, r.text
        body = r.json()
        assert isinstance(body, dict), body
        assert "detail" in body, body

    def test_anonymous_unknown_id_is_401(self, env):
        """Gate-ordering pin: FastAPI resolves the auth dependency BEFORE
        the handler's existence check, so an anonymous caller gets 401 even
        for a nonexistent id (never the 404 oracle) — the gate does not
        disclose which location ids exist to unauthenticated callers."""
        anon = TestClient(env.app)
        r = anon.get("/locations/99999")
        assert r.status_code == 401, r.text
        body = r.json()
        assert isinstance(body, dict), body
        assert "detail" in body, body

    def test_authenticated_shape_pin(self, env):
        """Authorized caller keeps the exact read contract: 200 with the
        key set {id, type, name, characters_here}. VALUES are world-seed
        driven and deliberately not pinned — the key set is the contract."""
        loc_id, _ = self._populated_location()
        r = env.get(f"/locations/{loc_id}")
        assert r.status_code == 200, r.text
        body = r.json()
        assert set(body.keys()) == LOC_KEYS, body

    def test_unknown_id_is_404_authenticated(self, env):
        """The 404 existence oracle (spec 005 R8) survives the gate but is
        reachable only authenticated: unknown id → 404, never a 401 mixup."""
        r = env.get("/locations/99999")
        assert r.status_code == 404, r.text

    def test_npc_presence_visible_without_owned_character(self, env):
        """Auth required, ownership NOT required: a user who owns no
        character still reads presence ids at a populated location."""
        loc_id, npc_id = self._populated_location()
        r = env.get(f"/locations/{loc_id}")
        assert r.status_code == 200, r.text
        body = r.json()
        assert isinstance(body["characters_here"], list), body
        assert npc_id in body["characters_here"], body
