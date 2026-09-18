"""M7 Chunk A tests (SPEC 007-external, T7): schema, seed, validator, executor."""
import os
import random
import sys

import pytest
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from app.config.config import load_config  # noqa: E402
from app.db.models import (  # noqa: E402
    Character,
    CharacterHealth,
    ExternalContact,
    ExternalLocation,
    ExternalService,
    WorldObject,
    bootstrap,
    create_engine_factory,
)
from app.world.seed_world import seed_world  # noqa: E402

SETTINGS = None


def setup_module(module):
    global SETTINGS
    SETTINGS = load_config("config/default.yaml")


def build(tmp_path, population=20):
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
        generate_population(session, settings, rng, settings.world.world_id, population)
        seed_external_world(session, settings, settings.world.world_id, rng)
        session.commit()
    return settings, engine


def _mk_user(session, username="pilot"):
    from app.api.auth import hash_password
    from app.db.models import User

    user = User(
        username=username, email=f"{username}@x.com",
        password_hash=hash_password("password123"),
        role="user", age_confirmed=True, created_at=0,
    )
    session.add(user)
    session.flush()
    return user


@pytest.fixture()
def world(tmp_path):
    settings, engine = build(tmp_path)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    return settings, factory


class TestSeed:
    def test_catalog_from_config(self, world):
        settings, factory = world
        with factory() as session:
            locations = session.query(ExternalLocation).order_by(ExternalLocation.id).all()
            assert len(locations) == len(settings.external.locations)
            assert {loc.ext_type for loc in locations} == {
                spec.ext_type for spec in settings.external.locations
            }
            services = session.query(ExternalService).all()
            assert len(services) == sum(len(s.services) for s in settings.external.locations)
            hospital = next(s for s in services if s.service_type == "treatment")
            assert hospital.heal_amount == 60 and hospital.price == 120

    def test_contacts_deterministic(self, world):
        settings, factory = world
        with factory() as session:
            npc_ids = [
                c.id for c in session.query(Character)
                .filter(Character.user_id.is_(None)).all()
            ]
            contacts = session.query(ExternalContact).all()
            assert len(contacts) >= len(npc_ids)  # >=1 per NPC (born in Vladivostok)
            assert {c.character_id for c in contacts} <= set(npc_ids)
            # types: first family, second friend
            by_char = {}
            for c in contacts:
                by_char.setdefault(c.character_id, []).append(c.contact_type)
            for types in by_char.values():
                assert types[0] == "family"
                assert all(t in ("family", "friend") for t in types)

    def test_players_have_no_contacts(self, tmp_path):
        settings, engine = build(tmp_path)
        from app.characters.player import create_player_character

        with sessionmaker(bind=engine)() as session:
            user = _mk_user(session, "solo")
            create_player_character(session, settings, settings.world.world_id, user,
                                    name="Solo", sex="M", age=30, game_timestamp=0)
            session.commit()
            contacts = session.query(ExternalContact).filter_by(character_id="plr_0001").count()
            assert contacts == 0


class TestValidator:
    def _validate(self, session, settings, character, params):
        from app.actions.validators import validate

        return validate(
            session, settings.world.world_id, character, "TRAVEL_EXTERNAL", 0,
            settings, params=params,
        )

    def _player_char(self, session):
        return session.query(Character).filter_by(id="plr_0001").first()

    def test_unknown_service(self, world):
        settings, factory = world
        from app.characters.player import create_player_character

        with factory() as session:
            user = _mk_user(session)
            create_player_character(session, settings, settings.world.world_id, user,
                                    name="P", sex="F", age=25, game_timestamp=0)
            session.commit()
            ok, reason, _, _ = self._validate(session, settings, self._player_char(session),
                                              {"service_id": 99999, "purpose": "x"})
            assert not ok and "Unknown external service" in reason

    def test_missing_purpose(self, world):
        settings, factory = world
        from app.characters.player import create_player_character

        with factory() as session:
            user = _mk_user(session)
            create_player_character(session, settings, settings.world.world_id, user,
                                    name="P", sex="F", age=25, game_timestamp=0)
            session.commit()
            service = session.query(ExternalService).filter_by(service_type="treatment").first()
            ok, reason, _, _ = self._validate(session, settings, self._player_char(session),
                                              {"service_id": service.id, "purpose": ""})
            assert not ok and "Purpose" in reason

    def test_treatment_needs_unfull_health(self, world):
        settings, factory = world
        from app.characters.player import create_player_character

        with factory() as session:
            user = _mk_user(session)
            create_player_character(session, settings, settings.world.world_id, user,
                                    name="P", sex="F", age=25, game_timestamp=0)
            session.commit()
            char = self._player_char(session)
            health = session.get(CharacterHealth, char.id)
            health.health = 100.0
            service = session.query(ExternalService).filter_by(service_type="treatment").first()
            ok, reason, _, _ = self._validate(session, settings, char,
                                              {"service_id": service.id, "purpose": "heal"})
            assert not ok and "Health already full" in reason

    def test_needs_move_to_pier(self, world):
        settings, factory = world
        from app.characters.player import create_player_character

        with factory() as session:
            user = _mk_user(session)
            create_player_character(session, settings, settings.world.world_id, user,
                                    name="P", sex="F", age=25, game_timestamp=0)
            session.commit()
            char = self._player_char(session)
            health = session.get(CharacterHealth, char.id)
            health.health = 40.0
            service = session.query(ExternalService).filter_by(service_type="treatment").first()
            ok, reason, needs_move, params = self._validate(
                session, settings, char,
                {"service_id": service.id, "purpose": "heal"},
            )
            # player home != pier -> needs_move
            assert ok and needs_move is not None and "pier" in reason
            assert params["travel_cost"] == settings.external.travel_cost

    def test_purchase_items_enforced(self, world):
        settings, factory = world
        from app.characters.player import create_player_character

        with factory() as session:
            user = _mk_user(session)
            create_player_character(session, settings, settings.world.world_id, user,
                                    name="P", sex="F", age=25, game_timestamp=0)
            session.commit()
            char = self._player_char(session)
            purchase = session.query(ExternalService).filter_by(
                service_type="purchase").order_by(ExternalService.id).first()
            # items on non-matching type
            ok, reason, _, _ = self._validate(session, settings, char, {
                "service_id": purchase.id, "purpose": "shop",
                "items": {"medicine": 2},
            })
            assert not ok and "does not offer" in reason
            # correct item, but qty as string
            ok, reason, _, _ = self._validate(session, settings, char, {
                "service_id": purchase.id, "purpose": "shop",
                "items": {purchase.item_type: 2},
            })
            # may fail only on funds; player starts with starting_balance
            assert ok, reason


class TestExecutor:
    def test_travel_treatment_full_flow(self, world):
        settings, factory = world
        from app.characters.player import create_player_character
        from app.economy import get_balance, open_account

        with factory() as session:
            user = _mk_user(session)
            create_player_character(session, settings, settings.world.world_id, user,
                                    name="P", sex="F", age=25, game_timestamp=0)
            session.commit()
            char = session.query(Character).filter_by(id="plr_0001").first()
            health = session.get(CharacterHealth, char.id)
            health.health = 40.0
            service = session.query(ExternalService).filter_by(service_type="treatment").first()
            acc = open_account(session, settings.world.world_id, "character", char.id)
            before = get_balance(session, acc.id)

            from app.actions.lifecycle import complete_task

            task = type("T", (), {})()
            task.id = 1
            task.character_id = char.id
            task.task_type = "TRAVEL_EXTERNAL"
            task.parameters = "{}"
            import json as _json

            params = {"service_id": service.id, "purpose": "heal",
                      "items": {}, "travel_cost": settings.external.travel_cost,
                      "basket_cost": 0,
                      "ext_location_id": service.external_location_id}
            task.parameters = _json.dumps(params)
            complete_task(session, settings.world.world_id, char, task,
                          1440, settings)
            session.commit()

            after = get_balance(session, acc.id)
            assert after == before - settings.external.travel_cost
            assert session.get(CharacterHealth, char.id).health == 100.0

            from app.db.models import WorldEvent
            events = session.query(WorldEvent).filter(
                WorldEvent.event_type.in_(["TRAVEL_EXTERNAL_DEPARTED",
                                           "TRAVEL_EXTERNAL_RETURNED"])
            ).all()
            assert len(events) == 2
            returned = next(e for e in events if e.event_type == "TRAVEL_EXTERNAL_RETURNED")
            import json as _json2

            payload = _json2.loads(returned.payload)
            assert payload["spent"] == settings.external.travel_cost
            assert payload["healed"] == 60.0

    def test_purchase_creates_inventory(self, world):
        settings, factory = world
        from app.characters.player import create_player_character

        with factory() as session:
            user = _mk_user(session)
            create_player_character(session, settings, settings.world.world_id, user,
                                    name="P", sex="F", age=25, game_timestamp=0)
            session.commit()
            char = session.query(Character).filter_by(id="plr_0001").first()
            service = session.query(ExternalService).filter_by(
                item_type="food_groceries").first()
            import json as _json

            from app.actions.lifecycle import complete_task

            params = {"service_id": service.id, "purpose": "shop",
                      "items": {"food_groceries": 2},
                      "travel_cost": settings.external.travel_cost,
                      "basket_cost": 2 * service.price,
                      "ext_location_id": service.external_location_id}
            task = type("T", (), {})()
            task.id = 2
            task.character_id = char.id
            task.task_type = "TRAVEL_EXTERNAL"
            task.parameters = _json.dumps(params)
            complete_task(session, settings.world.world_id, char, task,
                          1440, settings)
            session.commit()

            bought = session.query(WorldObject).filter_by(
                owner_character_id=char.id, object_type="food_groceries"
            ).all()
            assert sum(o.quantity for o in bought) == 2
            meta = _json.loads(bought[0].object_metadata)
            assert meta["source"] == "external_purchase"

            from app.db.models import Transaction
            txs = session.query(Transaction).filter(
                Transaction.reason.in_(["EXTERNAL_TRAVEL", "EXTERNAL_PURCHASE"])
            ).all()
            amounts = sorted(t.amount for t in txs)
            assert amounts == sorted([settings.external.travel_cost, 2 * service.price])

    def test_conservation_with_external_inflow(self, world):
        """object_conservation stays green with external purchases (AE3''''')."""
        settings, factory = world
        from app.characters.player import create_player_character

        with factory() as session:
            user = _mk_user(session)
            create_player_character(session, settings, settings.world.world_id, user,
                                    name="P", sex="F", age=25, game_timestamp=0)
            session.commit()
            char = session.query(Character).filter_by(id="plr_0001").first()
            service = session.query(ExternalService).filter_by(
                item_type="food_groceries").first()

            import json as _json

            from app.actions.lifecycle import complete_task

            params = {"service_id": service.id, "purpose": "shop",
                      "items": {"food_groceries": 3},
                      "travel_cost": settings.external.travel_cost,
                      "basket_cost": 3 * service.price,
                      "ext_location_id": service.external_location_id}
            task = type("T", (), {})()
            task.id = 3
            task.character_id = char.id
            task.task_type = "TRAVEL_EXTERNAL"
            task.parameters = _json.dumps(params)
            complete_task(session, settings.world.world_id, char, task,
                          1440, settings)
            session.commit()

            from app.simulation.invariants import run_invariant_checks

            results = run_invariant_checks(session, settings.world.world_id, settings)
            conservation = next(r for r in results if r["name"] == "object_conservation")
            assert conservation["ok"], conservation["details"]
            external = next(r for r in results if r["name"] == "external_integrity")
            assert external["ok"], external["details"]
