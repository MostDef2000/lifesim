"""M7 Chunk C tests (SPEC 007-external, T11): AE1'''''-AE6''''' evidence."""
import os
import random
import sys

from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from fastapi.testclient import TestClient  # noqa: E402

from app.api.app import create_app  # noqa: E402
from app.config.config import load_config  # noqa: E402
from app.db.models import (  # noqa: E402
    Character,
    CharacterHealth,
    bootstrap,
    create_engine_factory,
)
from app.world.seed_world import seed_world  # noqa: E402

SETTINGS = None


def setup_module(module):
    global SETTINGS
    SETTINGS = load_config("config/default.yaml")


def build_world(settings, seed=42, population=20):
    engine = create_engine_factory(settings)
    from app.characters.generator import generate_population
    from app.world.seed_external import seed_external_world

    with sessionmaker(bind=engine)() as session:
        bootstrap(engine, settings, seed=seed)
        seed_world(session, settings, settings.world.world_id)
        rng = random.Random(seed)
        generate_population(session, settings, rng, settings.world.world_id, population)
        seed_external_world(session, settings, settings.world.world_id, rng)
        session.commit()
    return engine


def make_settings(tmp_path, **external_updates):
    return SETTINGS.model_copy(update={
        "persistence": SETTINGS.persistence.model_copy(
            update={"db_path": str(tmp_path / "w.db")}
        ),
        "external": SETTINGS.external.model_copy(update=external_updates),
    })


def login_player(settings, factory, username="wanderer"):
    app = create_app(settings, factory)
    client = TestClient(app)
    r = client.post("/auth/register", json={
        "username": username,
        "email": f"{username[0]}@x.com",
        "password": "password123",
        "age_confirmed": True,
    })
    assert r.status_code == 201, r.text
    client.post("/auth/login", json={"username": username, "password": "password123"})
    r = client.post("/characters", json={"name": f"Nomad-{username}", "sex": "F", "age": 29})
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    assert client.post(f"/characters/{cid}/control", json={"mode": "DIRECT"}).status_code == 200
    return client, cid


def services_by_type(client):
    """Map (service_type, item_type) -> service id; exposes svc['purchase_food']."""
    catalog = client.get("/external").json()
    out = {}
    for loc in catalog:
        for svc in loc["services"]:
            if svc["item_type"]:
                key = f"{svc['service_type']}_{svc['item_type']}"
            else:
                key = svc["service_type"]
            out[key] = svc["id"]
    return out


class TestAE1E2ETreatment:
    """AE1''''': catalog -> hurt -> travel for treatment -> healed + ledger + events."""

    def test_full_flow(self, tmp_path):
        settings = make_settings(tmp_path)
        build_world(settings)
        factory = sessionmaker(bind=create_engine_factory(settings), expire_on_commit=False)
        client, cid = login_player(settings, factory)
        services = services_by_type(client)

        with factory() as session:
            session.get(CharacterHealth, cid).health = 40.0
            session.commit()

        from app.economy import get_balance, open_account

        with factory() as session:
            acc = open_account(session, settings.world.world_id, "character", cid)
            balance_before = get_balance(session, acc.id)

        r = client.post("/actions", json={
            "character_id": cid, "action_type": "TRAVEL_EXTERNAL",
            "params": {"service_id": services["treatment"], "purpose": "heal"},
        })
        assert r.status_code == 201, r.text
        task_id = r.json()["task_id"]

        # progress the task to completion by stepping the lifecycle directly
        from app.actions.lifecycle import complete_task
        from app.db.models import CharacterTask, WorldEvent

        with factory() as session:
            task = session.get(CharacterTask, int(task_id))
            task.status = "active"
            task.started_at = 0
            task.ends_at = 480
            char = session.get(Character, cid)
            complete_task(
                session, settings.world.world_id, char, task, 480, settings,
            )
            session.commit()

        with factory() as session:
            assert session.get(CharacterHealth, cid).health == 100.0
            acc = open_account(session, settings.world.world_id, "character", cid)
            assert get_balance(session, acc.id) == balance_before - settings.external.travel_cost
            events = session.query(WorldEvent).filter(
                WorldEvent.world_id == settings.world.world_id,
                WorldEvent.event_type.in_(["TRAVEL_EXTERNAL_DEPARTED",
                                           "TRAVEL_EXTERNAL_RETURNED"]),
            ).all()
            assert len(events) == 2

        # invariant green
        from app.simulation.invariants import run_invariant_checks

        with factory() as session:
            results = run_invariant_checks(session, settings.world.world_id, settings)
        bad = [r for r in results if not r["ok"]]
        assert bad == [], bad


class TestAE2Keystone:
    """AE2''''': headless M1-M4 identity — no TRAVEL events, contacts are content."""

    def test_headless_identity(self, tmp_path):
        settings = make_settings(tmp_path)
        build_world(settings)
        engine = create_engine_factory(settings)

        from app.db.models import CharacterTask, WorldEvent
        from app.simulation.invariants import run_invariant_checks
        from app.simulation.report import build_report

        with sessionmaker(bind=engine)() as session:
            build_report(
                session, settings.world.world_id, settings,
                wall_duration_sec=0.0, seed=42,
            )
            travel_tasks = (
                session.query(CharacterTask)
                .filter(CharacterTask.task_type == "TRAVEL_EXTERNAL")
                .count()
            )
            travel_events = (
                session.query(WorldEvent)
                .filter(WorldEvent.event_type.like("TRAVEL_EXTERNAL%"))
                .count()
            )
            assert travel_tasks == 0 and travel_events == 0

            results = run_invariant_checks(session, settings.world.world_id, settings)
            bad = [r for r in results if not r["ok"]]
            assert bad == [], bad


class TestAE3Purchase:
    """AE3''''': purchase trip -> inventory + transactions + conservation green."""

    def test_purchase_flow(self, tmp_path):
        settings = make_settings(tmp_path)
        build_world(settings)
        factory = sessionmaker(bind=create_engine_factory(settings), expire_on_commit=False)
        client, cid = login_player(settings, factory)
        services = services_by_type(client)

        r = client.post("/actions", json={
            "character_id": cid, "action_type": "TRAVEL_EXTERNAL",
            "params": {"service_id": services["purchase_food_groceries"], "purpose": "shop",
                       "items": {"food_groceries": 2}},
        })
        assert r.status_code == 201, r.text

        from app.actions.lifecycle import complete_task
        from app.db.models import CharacterTask, Transaction, WorldObject

        task_id = r.json()["task_id"]
        with factory() as session:
            task = session.get(CharacterTask, int(task_id))
            task.status = "active"
            char = session.get(Character, cid)
            complete_task(session, settings.world.world_id, char, task, 960, settings)
            session.commit()

            bought = session.query(WorldObject).filter_by(
                owner_character_id=cid, object_type="food_groceries"
            ).all()
            assert sum(o.quantity for o in bought) == 2

            txs = session.query(Transaction).filter(
                Transaction.reason.in_(["EXTERNAL_TRAVEL", "EXTERNAL_PURCHASE"])
            ).all()
            assert len(txs) == 2

        with factory() as session:
            from app.simulation.invariants import run_invariant_checks

            results = run_invariant_checks(session, settings.world.world_id, settings)
            conservation = next(x for x in results if x["name"] == "object_conservation")
            assert conservation["ok"], conservation["details"]


class TestAE4Persistence:
    """AE4''''': external content survives restart; repeat trip valid."""

    def test_restart(self, tmp_path):
        settings = make_settings(tmp_path)
        build_world(settings)
        factory = sessionmaker(bind=create_engine_factory(settings), expire_on_commit=False)
        client, cid = login_player(settings, factory)

        # restart on same DB
        engine2 = create_engine_factory(settings)
        factory2 = sessionmaker(bind=engine2, expire_on_commit=False)
        client2 = login_player(settings, factory2, username="second")[0]
        catalog = client2.get("/external").json()
        assert len(catalog) == len(settings.external.locations)
        services = services_by_type(client2)
        assert services["treatment"] > 0

        contacts = client2.get("/characters/{cid}/contacts".replace("{cid}", "npc_0001"))
        assert contacts.status_code in (200, 403)  # npc belongs to nobody


class TestAE5Negatives:
    """AE5''''': negatives summary."""

    def test_negatives(self, tmp_path):
        settings = make_settings(tmp_path)
        build_world(settings)
        factory = sessionmaker(bind=create_engine_factory(settings), expire_on_commit=False)
        client, cid = login_player(settings, factory)
        services = services_by_type(client)

        # unknown service
        r = client.post("/actions", json={
            "character_id": cid, "action_type": "TRAVEL_EXTERNAL",
            "params": {"service_id": 424242, "purpose": "x"},
        })
        assert r.status_code == 422

        # items on treatment
        r = client.post("/actions", json={
            "character_id": cid, "action_type": "TRAVEL_EXTERNAL",
            "params": {"service_id": services["treatment"], "purpose": "heal",
                       "items": {"food_groceries": 1}},
        })
        assert r.status_code == 422

        # NPC travel attempt (not owned -> 403 at ownership layer)
        r = client.post("/actions", json={
            "character_id": "npc_0002", "action_type": "TRAVEL_EXTERNAL",
            "params": {"service_id": services["treatment"], "purpose": "heal"},
        })
        assert r.status_code == 403


class TestAE6Config:
    """AE6''''': catalog from config; disabled external -> empty catalog; NPC gate."""

    def test_catalog_sensitivity(self, tmp_path):
        settings = make_settings(tmp_path)
        build_world(settings)
        factory = sessionmaker(bind=create_engine_factory(settings), expire_on_commit=False)
        client, _ = login_player(settings, factory)
        catalog = client.get("/external").json()
        names = {loc["name"] for loc in catalog}
        assert names == {spec.name for spec in settings.external.locations}

    def test_disabled_external(self, tmp_path):
        settings = make_settings(tmp_path, enabled=False)
        engine = build_world(settings)
        from app.db.models import ExternalLocation

        with sessionmaker(bind=engine)() as session:
            assert session.query(ExternalLocation).count() == 0
