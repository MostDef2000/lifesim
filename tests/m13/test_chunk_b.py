"""M13 Chunk B tests (SPEC 013-external2, T5): trip memory + messages."""
import json
import os
import sys

import pytest
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from fastapi.testclient import TestClient  # noqa: E402

from app.api.auth import hash_password  # noqa: E402
from app.config.config import load_config  # noqa: E402
from app.db.models import (  # noqa: E402
    Character,
    Relationship,
    User,
    WorldEvent,
    bootstrap,
    create_engine_factory,
)
from app.world.seed_world import seed_world  # noqa: E402

SETTINGS = None


def setup_module(module):
    global SETTINGS
    SETTINGS = load_config("config/default.yaml")


def make_settings(tmp_path):
    return SETTINGS.model_copy(update={
        "persistence": SETTINGS.persistence.model_copy(
            update={"db_path": str(tmp_path / "w.db")}
        ),
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


class TestTripMemory:
    def test_context_has_recent_trip(self, tmp_path):
        from app.api.dialogue import build_context

        settings = make_settings(tmp_path)
        engine = build(settings)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            chars = (
                session.query(Character)
                .filter_by(world_id=settings.world.world_id)
                .all()
            )
            npc = chars[0]
            player = chars[1]
            session.add(WorldEvent(
                world_id=settings.world.world_id,
                event_type="TRAVEL_EXTERNAL_RETURNED", actor_id=npc.id,
                payload=json.dumps({
                    "purpose": "treatment (Владивосток)",
                    "healed": True, "items": [],
                }),
                game_timestamp=2 * 1440,
            ))
            session.commit()
            ctx = build_context(
                session, settings.world.world_id, player, npc)
            assert "npc_recent_trip" in ctx
            assert "treatment" in ctx["npc_recent_trip"]
            assert "полечился" in ctx["npc_recent_trip"]

    def test_no_trip_no_key(self, tmp_path):
        from app.api.dialogue import build_context

        settings = make_settings(tmp_path)
        engine = build(settings)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            chars = (
                session.query(Character)
                .filter_by(world_id=settings.world.world_id)
                .all()
            )
            ctx = build_context(
                session, settings.world.world_id, chars[0], chars[1])
            assert "npc_recent_trip" not in ctx

    def test_fallback_mentions_trip(self, tmp_path):
        from app.api.dialogue import fallback_reply

        ctx = {"relationship": {"trust": 0, "affection": 10, "respect": 0},
               "npc_recent_trip": "treatment; полечился"}
        reply = fallback_reply(ctx, "Иван")
        assert "вернулся с материка" in reply
        assert "treatment" in reply


@pytest.fixture()
def env(tmp_path):
    settings = make_settings(tmp_path)
    engine = build(settings)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        session.add(User(
            username="pismo", email="p@x.com",
            password_hash=hash_password("password123"),
            role="player", age_confirmed=True, created_at=0,
        ))
        session.commit()
    from app.api.app import create_app

    client = TestClient(create_app(settings, factory))
    client.post("/auth/login", json={
        "username": "pismo", "password": "password123"})
    return settings, factory, client


class TestMessages:
    def _player_char(self, session, settings):
        from app.characters.player import create_player_character

        user = session.query(User).filter_by(username="pismo").one()
        return create_player_character(
            session, settings, settings.world.world_id,
            user, "Письмо Тест", "M", 30, 0,
        )

    def test_send_inbox_autoreply(self, env):
        settings, factory, client = env
        with factory() as session:
            char = self._player_char(session, settings)
            npc = (
                session.query(Character)
                .filter_by(world_id=settings.world.world_id)
                .filter(Character.user_id.is_(None))
                .first()
            )
            npc.location_id = char.location_id  # co-located → guard passes
            session.commit()
            npc_id = npc.id
        r = client.post("/messages", json={
            "to_character_id": npc_id, "body": "Привет, как дела?"})
        assert r.status_code == 200, r.text
        inbox = client.get("/messages").json()
        assert len(inbox["messages"]) == 1  # auto-reply from NPC
        reply = inbox["messages"][0]
        assert reply["from"] == npc_id
        assert "Письмо получил" in reply["body"]
        sent = client.get("/messages/sent").json()
        assert any(m["to"] == npc_id for m in sent)

    def test_guard_stranger_403(self, env):
        settings, factory, client = env
        with factory() as session:
            char = self._player_char(session, settings)
            npc = (
                session.query(Character)
                .filter_by(world_id=settings.world.world_id)
                .filter(Character.user_id.is_(None))
                .first()
            )
            session.commit()
            assert npc.location_id != char.location_id or True
            # force different location
            npc.location_id = (char.location_id + 1) % 1000
            npc_id = npc.id
            session.commit()
            # ensure no relationship
            assert session.query(Relationship).filter_by(
                character_a=char.id, character_b=npc.id).first() is None
        r = client.post("/messages", json={
            "to_character_id": npc_id, "body": "Эй!"})
        assert r.status_code == 403

    def test_relationship_guard_passes(self, env):
        settings, factory, client = env
        with factory() as session:
            char = self._player_char(session, settings)
            npc = (
                session.query(Character)
                .filter_by(world_id=settings.world.world_id)
                .filter(Character.user_id.is_(None))
                .first()
            )
            lo, hi = sorted([char.id, npc.id])
            session.add(Relationship(
                world_id=settings.world.world_id,
                character_a=lo, character_b=hi,
                affection=5.0, trust=0.0, respect=0.0, updated_at=0,
            ))
            session.commit()
            npc_id = npc.id
        r = client.post("/messages", json={
            "to_character_id": npc_id, "body": "Письмо знакомому"})
        assert r.status_code == 200

    def test_event_and_invariant(self, env):
        settings, factory, client = env
        with factory() as session:
            char = self._player_char(session, settings)
            npc = (
                session.query(Character)
                .filter_by(world_id=settings.world.world_id)
                .filter(Character.user_id.is_(None))
                .first()
            )
            npc.location_id = char.location_id
            session.commit()
            npc_id = npc.id
        client.post("/messages", json={
            "to_character_id": npc_id, "body": "Проверка"})
        with factory() as session:
            types = {
                r[0] for r in session.query(WorldEvent.event_type).all()
            }
            assert "MESSAGE_SENT" in types
            from app.simulation.invariants import run_invariant_checks

            results = run_invariant_checks(
                session, settings.world.world_id, settings)
        msg_inv = next(
            r for r in results if r["name"] == "messages_integrity")
        assert msg_inv["ok"], msg_inv["details"]

    def test_auth_required(self, env):
        settings, factory, _ = env
        from app.api.app import create_app

        fresh = TestClient(create_app(settings, factory))
        assert fresh.get("/messages").status_code == 401
