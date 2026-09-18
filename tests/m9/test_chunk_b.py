"""M9 Chunk B tests (SPEC 009-web, T6): UI-journey smoke via the exact API
endpoints the SPA calls, auth gate, XSS hygiene (AE1''''''-AE5'''''')."""
import os
import sys

import pytest
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from fastapi.testclient import TestClient  # noqa: E402

from app.config.config import load_config  # noqa: E402
from app.db.models import bootstrap, create_engine_factory  # noqa: E402
from app.world.seed_world import seed_world  # noqa: E402

SETTINGS = None


def setup_module(module):
    global SETTINGS
    SETTINGS = load_config("config/default.yaml")


@pytest.fixture()
def env(tmp_path):
    settings = SETTINGS.model_copy(update={
        "persistence": SETTINGS.persistence.model_copy(
            update={"db_path": str(tmp_path / "w.db")}
        ),
        "admin": SETTINGS.admin.model_copy(update={"rate_limit_enabled": False}),
    })
    engine = create_engine_factory(settings)
    import random

    from app.characters.generator import generate_population

    with sessionmaker(bind=engine)() as session:
        bootstrap(engine, settings, seed=42)
        seed_world(session, settings, settings.world.world_id)
        generate_population(
            session, settings, random.Random(42),
            settings.world.world_id, 20,
        )
        session.commit()
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    from app.api.app import create_app

    client = TestClient(create_app(settings, factory))
    # register + login + create character (what the SPA auth flow does)
    client.post("/auth/register", json={
        "username": "player", "email": "p@x.com",
        "password": "password123", "age_confirmed": True,
    })
    client.post("/auth/login", json={"username": "player", "password": "password123"})
    me = client.get("/auth/me").json()
    r = client.post("/characters", json={"name": "Ivy Stone", "sex": "F", "age": 27})
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    client.post(f"/characters/{cid}/control", json={"mode": "DIRECT"})
    return client, cid, me


class TestUIJourney:
    """The exact endpoint sequence viewWorld / viewChat / viewInventory use."""

    def test_world_screen_sequence(self, env):
        client, cid, me = env
        assert client.get("/world").status_code == 200
        me_char = client.get(f"/characters/{cid}")
        assert me_char.status_code == 200
        body = me_char.json()
        assert "needs" in body or "first_name" in body
        locs = client.get("/locations").json()
        assert len(locs) >= 3
        loc_detail = client.get(f"/locations/{locs[0]['id']}")
        assert loc_detail.status_code == 200
        assert "characters_here" in loc_detail.json()
        # events feed (polling fallback): bare list
        ev = client.get("/world/events?since=0")
        assert ev.status_code == 200
        assert isinstance(ev.json(), list)

    def test_actions_from_buttons(self, env):
        client, cid, _ = env
        r = client.post("/actions", json={
            "character_id": cid, "action_type": "WORK" })
        assert r.status_code == 201, r.text
        tid = r.json()["task_id"]
        assert client.get(f"/actions/{tid}").status_code == 200

    def test_chat_screen_sequence(self, env):
        client, cid, _ = env
        locs = client.get("/locations").json()
        npc_id = None
        for loc in locs:
            detail = client.get(f"/locations/{loc['id']}").json()
            npcs = [c for c in detail.get("characters_here", []) if str(c).startswith("npc_")]
            if npcs:
                npc_id = npcs[0]
                break
        sid = client.post("/dialogue/start", json={"npc_id": npc_id}).json()["session_id"]
        msg = client.post(f"/dialogue/{sid}/message", json={"content": "Привет!"})
        assert msg.status_code == 200
        assert msg.json()["npc_reply"]
        assert len(msg.json()["suggested_responses"]) == 3

    def test_inventory_screen(self, env):
        client, cid, _ = env
        r = client.get(f"/characters/{cid}/inventory")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_admin_tab_gating(self, env):
        client, cid, me = env
        assert client.get("/admin/overview").status_code == 403  # plain user


class TestAuthGate:
    def test_401_without_cookie(self, tmp_path):
        settings = SETTINGS.model_copy(update={
            "persistence": SETTINGS.persistence.model_copy(
                update={"db_path": str(tmp_path / "w.db")}
            ),
            "admin": SETTINGS.admin.model_copy(update={"rate_limit_enabled": False}),
        })
        engine = create_engine_factory(settings)
        with sessionmaker(bind=engine)() as session:
            bootstrap(engine, settings, seed=42)
            seed_world(session, settings, settings.world.world_id)
            session.commit()
        from app.api.app import create_app

        anon = TestClient(create_app(settings, sessionmaker(bind=engine)))
        # endpoints the SPA hits after login all reject anonymous callers
        # (/world/events is public by M5 design — the world journal is shared)
        for path in ["/auth/me", "/locations", "/admin/overview"]:
            assert anon.get(path).status_code == 401, path
        assert anon.get("/world/events?since=0").status_code == 200


class TestXsshYgiene:
    def test_no_innerhtml_for_data(self):
        """AE5'''''': app.js renders API data via textContent only."""
        path = os.path.join(
            os.path.dirname(__file__), "../../backend/app/web/app.js"
        )
        src = open(path, encoding="utf-8").read()
        assert "innerHTML" not in src
        assert "document.write" not in src
        assert "insertAdjacentHTML" not in src
