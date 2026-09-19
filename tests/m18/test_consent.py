"""M18 tests (SPEC 018-consent-clothing): consent §31 + clothing §28."""
import os
import sys

from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from app.api.auth import hash_password  # noqa: E402
from app.config.config import load_config  # noqa: E402
from app.db.models import (  # noqa: E402
    Character,
    InteractionPermission,
    User,
    bootstrap,
    create_engine_factory,
)
from app.world.seed_world import seed_world  # noqa: E402

SETTINGS = None


def setup_module(module):
    global SETTINGS
    SETTINGS = load_config("config/default.yaml")


def make_settings(tmp_path, **romance_updates):
    from pathlib import Path

    tmp_path = Path(tmp_path)
    return SETTINGS.model_copy(update={
        "persistence": SETTINGS.persistence.model_copy(
            update={"db_path": str(tmp_path / "w.db")}
        ),
        "romance": SETTINGS.romance.model_copy(update=romance_updates),
    })


def build(settings):
    import random as _r

    from app.characters.generator import generate_population

    engine = create_engine_factory(settings)
    with sessionmaker(bind=engine)() as session:
        bootstrap(engine, settings, seed=42)
        seed_world(session, settings, settings.world.world_id)
        generate_population(
            session, settings, _r.Random(42),
            settings.world.world_id, 20,
        )
        session.commit()
    return engine


class TestConsent:
    def _player(self, session, settings, username="laska"):
        from app.characters.player import create_player_character

        user = session.query(User).filter_by(username=username).one()
        return create_player_character(
            session, settings, settings.world.world_id,
            user, "Ласка Тест", "F", 28, 0,
        )

    def _npc(self, session, settings):
        return (
            session.query(Character)
            .filter_by(world_id=settings.world.world_id)
            .filter(Character.user_id.is_(None))
            .first()
        )

    def test_disabled_403_no_rows(self, tmp_path):
        settings = make_settings(tmp_path)  # enabled=False default
        engine = build(settings)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            session.add(User(
                username="laska", email="l@x.com",
                password_hash=hash_password("password123"),
                role="player", age_confirmed=True, created_at=0,
            ))
            session.commit()
            self._player(session, settings)
            session.commit()
        from app.api.app import create_app

        client = TestClient(create_app(settings, factory))
        client.post("/auth/login", json={
            "username": "laska", "password": "password123"})
        with factory() as session:
            npc = self._npc(session, settings)
            npc_id = npc.id
        r = client.post("/interactions/romantic", json={"npc_id": npc_id})
        assert r.status_code == 403
        with factory() as session:
            assert session.query(InteractionPermission).count() == 0

    def test_deterministic_decision_idempotent(self, tmp_path):
        settings = make_settings(tmp_path, enabled=True)
        engine = build(settings)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            session.add(User(
                username="laska", email="l@x.com",
                password_hash=hash_password("password123"),
                role="player", age_confirmed=True, created_at=0,
            ))
            session.commit()
            char = self._player(session, settings)
            session.commit()
            npc = self._npc(session, settings)
            # relationship with affection >= threshold -> granted
            from app.db.models import Relationship

            a, b = sorted([char.id, npc.id])
            rel = Relationship(
                world_id=settings.world.world_id, character_a=a,
                character_b=b, affection=50.0, updated_at=0,
            )
            session.merge(rel)
            session.commit()
            npc_id = npc.id
        from app.api.app import create_app

        client = TestClient(create_app(settings, factory))
        client.post("/auth/login", json={
            "username": "laska", "password": "password123"})
        r1 = client.post("/interactions/romantic", json={"npc_id": npc_id})
        assert r1.status_code == 200, r1.text
        assert r1.json()["permission"] == "granted"
        r2 = client.post("/interactions/romantic", json={"npc_id": npc_id})
        assert r2.json()["permission"] == "granted"
        with factory() as session:
            assert session.query(InteractionPermission).count() == 1

    def test_low_affection_declined_and_listing(self, tmp_path):
        settings = make_settings(tmp_path, enabled=True)
        engine = build(settings)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            session.add(User(
                username="laska", email="l@x.com",
                password_hash=hash_password("password123"),
                role="player", age_confirmed=True, created_at=0,
            ))
            session.commit()
            char = self._player(session, settings)
            session.commit()
            npc = self._npc(session, settings)
            npc_id = npc.id  # no relationship row -> affection 0 -> declined
            # incoming row: NPC actor -> player target
            session.add(InteractionPermission(
                world_id=settings.world.world_id,
                actor_character_id=npc_id,
                target_character_id=char.id,
                interaction_category="romance",
                permission="requested",
                created_at=0, updated_at=0,
            ))
            session.commit()
            char_id = char.id
        from app.api.app import create_app

        client = TestClient(create_app(settings, factory))
        client.post("/auth/login", json={
            "username": "laska", "password": "password123"})
        r = client.post("/interactions/romantic", json={"npc_id": npc_id})
        assert r.status_code == 200
        assert r.json()["permission"] == "declined"
        listing = client.get("/interactions/permissions").json()
        rows = listing["permissions"]
        assert len(rows) == 2
        pair = {(r["actor_character_id"], r["target_character_id"])
                for r in rows}
        assert (npc_id, char_id) in pair and (char_id, npc_id) in pair
