"""M7 Chunk B tests (SPEC 007-external, T10): /external, contacts, actions flow."""
import os
import random
import sys

import pytest
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from fastapi.testclient import TestClient  # noqa: E402

from app.api.app import create_app  # noqa: E402
from app.config.config import load_config  # noqa: E402
from app.db.models import bootstrap, create_engine_factory  # noqa: E402
from app.world.seed_world import seed_world  # noqa: E402

SETTINGS = None


def setup_module(module):
    global SETTINGS
    SETTINGS = load_config("config/default.yaml")


@pytest.fixture()
def world(tmp_path):
    """App + populated world (external seeded) + registered player w/ character."""
    settings = SETTINGS.model_copy(update={
        "persistence": SETTINGS.persistence.model_copy(
            update={"db_path": str(tmp_path / "w.db")}
        )
    })
    engine = create_engine_factory(settings)
    from app.characters.generator import generate_population
    from app.world.seed_external import seed_external_world

    with sessionmaker(bind=engine)() as session:
        bootstrap(engine, settings, seed=42)
        seed_world(session, settings, settings.world.world_id)
        rng = random.Random(42)
        generate_population(session, settings, rng, settings.world.world_id, 20)
        seed_external_world(session, settings, settings.world.world_id, rng)
        session.commit()

    factory = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app(settings, factory)
    client = TestClient(app)
    r = client.post("/auth/register", json={
        "username": "traveler",
        "email": "t@x.com",
        "password": "password123",
        "age_confirmed": True,
    })
    assert r.status_code == 201, r.text
    client.post("/auth/login", json={"username": "traveler", "password": "password123"})
    r = client.post("/characters", json={"name": "Ferryman", "sex": "M", "age": 33})
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    r = client.post(f"/characters/{cid}/control", json={"mode": "DIRECT"})
    assert r.status_code == 200, r.text
    return settings, factory, client, cid


def _service_ids(client):
    external = client.get("/external").json()
    services = {}
    for loc in external:
        for svc in loc["services"]:
            services[svc["service_type"]] = svc["id"]
    return services


class TestExternalAPI:
    def test_catalog(self, world):
        _, _, client, _ = world
        r = client.get("/external")
        assert r.status_code == 200
        catalog = r.json()
        types = {loc["ext_type"] for loc in catalog}
        assert "hospital" in types and "supermarket" in types
        hospital = next(loc for loc in catalog if loc["ext_type"] == "hospital")
        assert hospital["services"][0]["service_type"] == "treatment"

    def test_requires_auth(self, world):
        _, factory, _, _ = world
        settings, _, client, _ = world
        anon = TestClient(create_app(settings, factory))
        assert anon.get("/external").status_code == 401

    def test_contacts_owner_only(self, world):
        _, factory, client, cid = world
        settings, _, client2, _ = world
        r = client.get(f"/characters/{cid}/contacts")
        assert r.status_code == 200
        # another user sees 403
        other = TestClient(create_app(settings, factory))
        other.post("/auth/register", json={
            "username": "peeker",
            "email": "p@x.com",
            "password": "password123",
            "age_confirmed": True,
        })
        other.post("/auth/login", json={"username": "peeker", "password": "password123"})
        r2 = other.get(f"/characters/{cid}/contacts")
        assert r2.status_code == 403


class TestActionsFlow:
    def test_treatment_e2e(self, world):
        settings, factory, client, cid = world
        services = _service_ids(client)
        # hurt the character first (direct DB via factory session)
        from app.db.models import CharacterHealth

        with factory() as session:
            health = session.get(CharacterHealth, cid)
            health.health = 40.0
            session.commit()

        # not at pier -> first action request needs MOVE: the API auto-enqueues
        # with needs_move? No: POST /actions enqueues TRAVEL_EXTERNAL with
        # needs_move -> MOVE precondition is encoded in the validator return.
        r = client.post("/actions", json={
            "character_id": cid, "action_type": "TRAVEL_EXTERNAL",
            "params": {"service_id": services["treatment"], "purpose": "heal"},
        })
        assert r.status_code == 201, r.text
        task = r.json()
        assert task["task_type"] == "TRAVEL_EXTERNAL"

    def test_unknown_action_and_service(self, world):
        _, _, client, cid = world
        r = client.post("/actions", json={
            "character_id": cid, "action_type": "FLY_TO_MOON", "params": {},
        })
        assert r.status_code == 422
        r = client.post("/actions", json={
            "character_id": cid, "action_type": "TRAVEL_EXTERNAL",
            "params": {"service_id": 999999, "purpose": "x"},
        })
        assert r.status_code == 422
        assert "Unknown external service" in r.json()["detail"]

    def test_items_on_treatment_rejected(self, world):
        _, _, client, cid = world
        services = _service_ids(client)
        r = client.post("/actions", json={
            "character_id": cid, "action_type": "TRAVEL_EXTERNAL",
            "params": {"service_id": services["treatment"], "purpose": "heal",
                       "items": {"food_groceries": 1}},
        })
        assert r.status_code == 422
        assert "Only purchase services accept items" in r.json()["detail"]

    def test_insufficient_funds(self, world):
        settings, factory, client, cid = world
        services = _service_ids(client)
        # drain the account
        from app.economy import get_balance, open_account

        with factory() as session:
            acc = open_account(session, settings.world.world_id, "character", cid)
            get_balance(session, acc.id)
            session.query(type(acc)).filter_by(id=acc.id).update({"balance": 0})
            session.commit()
        r = client.post("/actions", json={
            "character_id": cid, "action_type": "TRAVEL_EXTERNAL",
            "params": {"service_id": services["treatment"], "purpose": "heal"},
        })
        assert r.status_code == 422
        assert "Insufficient funds" in r.json()["detail"]

    def test_npc_rejected(self, world):
        _, _, client, _ = world
        npc_id = "npc_0001"
        services = _service_ids(client)
        r = client.post("/actions", json={
            "character_id": npc_id, "action_type": "TRAVEL_EXTERNAL",
            "params": {"service_id": services["treatment"], "purpose": "heal"},
        })
        assert r.status_code == 403  # not owned by user
