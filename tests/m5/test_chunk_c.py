"""M5 Chunk C tests (SPEC 005-player, T16): read endpoints + dialogue/chat."""

import os
import sys

import pytest
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from app.api.app import create_app  # noqa: E402
from app.api.dialogue import (  # noqa: E402
    build_context,
    fallback_reply,
    safety_check,
    suggested_responses,
)
from app.config.config import load_config  # noqa: E402
from app.db.models import bootstrap, create_engine_factory  # noqa: E402
from app.world.seed_world import seed_world  # noqa: E402

SETTINGS = None


def setup_module(module):
    global SETTINGS
    SETTINGS = load_config("config/default.yaml")


@pytest.fixture()
def world(tmp_path):
    """World with one player character and populated NPC world."""
    settings = SETTINGS.model_copy(update={
        "persistence": SETTINGS.persistence.model_copy(
            update={"db_path": str(tmp_path / "w.db")}
        )
    })
    engine = create_engine_factory(settings)
    import random

    from app.characters.generator import generate_population
    from app.world.social_seed import seed_social

    with sessionmaker(bind=engine)() as session:
        bootstrap(engine, settings, seed=42)
        seed_world(session, settings, settings.world.world_id)
        rng = random.Random(42)
        generate_population(session, settings, rng, settings.world.world_id, 20)
        seed_social(session, settings, settings.world.world_id, rng)
        session.commit()
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app(settings, factory)
    from fastapi.testclient import TestClient

    client = TestClient(app)
    client.post("/auth/register", json={
        "username": "reader", "email": "r@x.com", "password": "password123", "age_confirmed": True,
    })
    client.post("/auth/login", json={"username": "reader", "password": "password123"})
    r = client.post("/characters", json={"name": "Rita Ora", "sex": "F", "age": 28})
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    # find an NPC id
    from app.db.models import Character
    with factory() as s:
        npc = (
            s.query(Character)
            .filter_by(world_id=settings.world.world_id)
            .filter(Character.id.like("npc_%"))
            .order_by(Character.id)
            .first()
        )
        npc_id = npc.id
    return settings, app, client, cid, npc_id, engine, factory


# ---------- Safety gate (unit) ----------

def test_safety_check():
    assert safety_check("") == "message must not be empty"
    assert safety_check("   ") == "message must not be empty"
    assert safety_check("x" * 2001) == "message must be at most 2000 chars"
    assert safety_check("привет!") is None


# ---------- Read endpoints (R8) ----------

def test_get_world(world):
    _, _, client, *_ = world
    r = client.get("/world")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == SETTINGS.world.world_id
    assert body["population"] >= 20
    assert "day" in body and "game_timestamp" in body


def test_get_world_events(world):
    _, _, client, *_ = world
    r = client.get("/world/events?limit=10")
    assert r.status_code == 200
    events = r.json()
    assert 0 < len(events) <= 10
    ids = [e["id"] for e in events]
    assert ids == sorted(ids)
    # since cursor
    r2 = client.get(f"/world/events?since={ids[0]}&limit=5")
    ids2 = [e["id"] for e in r2.json()]
    assert all(i > ids[0] for i in ids2)


def test_get_world_events_limit_cap(world):
    _, _, client, *_ = world
    r = client.get("/world/events?limit=9999")
    assert r.status_code == 200
    assert len(r.json()) <= 500


def test_get_character_public_vs_owner(world):
    _, _, client, cid, *_ = world
    r = client.get(f"/characters/{cid}")
    assert r.status_code == 200
    body = r.json()
    assert body["is_owner"] is True
    assert "needs" in body  # owner sees needs
    assert body["control_mode"] == "AUTONOMOUS"

    # second user sees public view without needs
    client.post("/auth/logout")
    client.post("/auth/register", json={
        "username": "viewer", "email": "v@x.com", "password": "password123", "age_confirmed": True,
    })
    client.post("/auth/login", json={"username": "viewer", "password": "password123"})
    r = client.get(f"/characters/{cid}")
    assert r.status_code == 200
    assert r.json()["is_owner"] is False
    assert "needs" not in r.json()


def test_get_character_inventory(world):
    _, _, client, cid, *_ = world
    r = client.get(f"/characters/{cid}/inventory")
    assert r.status_code == 200
    items = r.json()
    assert len(items) >= 1  # initial items
    types = {i["object_type"] for i in items}
    assert "clothing_basic" in types


def test_get_location(world):
    _, _, client, cid, npc_id, _, factory = world
    from app.db.models import Character

    with factory() as s:
        npc = s.get(Character, npc_id)
        loc_id = npc.location_id
    r = client.get(f"/locations/{loc_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == loc_id
    assert npc_id in body["characters_here"]
    r = client.get("/locations/99999")
    assert r.status_code == 404


# ---------- Dialogue / chat (R7, llm-off fallback) ----------

def test_dialogue_start_and_message(world):
    settings, _, client, cid, npc_id, *_ = world
    r = client.post("/dialogue/start", json={"npc_id": npc_id})
    assert r.status_code == 201, r.text
    sid = r.json()["session_id"]

    r = client.post(f"/dialogue/{sid}/message", json={"content": "Привет! Как дела?"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "[stub]" not in body["npc_reply"]  # llm-off → fallback template, not stub
    assert len(body["suggested_responses"]) == 3

    # fallback is deterministic
    client.post("/auth/logout")
    client.post("/auth/register", json={
        "username": "reader2", "email": "r2@x.com", "password": "password123", "age_confirmed": True,
    })
    client.post("/auth/login", json={"username": "reader2", "password": "password123"})
    client.post("/characters", json={"name": "Rita Ortega", "sex": "F", "age": 33})
    r2 = client.post("/dialogue/start", json={"npc_id": npc_id})
    sid2 = r2.json()["session_id"]
    r2 = client.post(f"/dialogue/{sid2}/message", json={"content": "Привет! Как дела?"})
    assert r2.json()["npc_reply"] == body["npc_reply"]


def test_dialogue_history(world):
    _, _, client, cid, npc_id, *_ = world
    sid = client.post("/dialogue/start", json={"npc_id": npc_id}).json()["session_id"]
    client.post(f"/dialogue/{sid}/message", json={"content": "Сообщение раз"})
    client.post(f"/dialogue/{sid}/message", json={"content": "Сообщение два"})
    r = client.get(f"/dialogue/{sid}")
    assert r.status_code == 200
    messages = r.json()["messages"]
    assert [m["sender"] for m in messages] == ["user", "npc", "user", "npc"]
    assert messages[0]["content"] == "Сообщение раз"
    assert messages[1]["suggested_responses"] is not None


def test_dialogue_empty_message_422(world):
    _, _, client, cid, npc_id, *_ = world
    sid = client.post("/dialogue/start", json={"npc_id": npc_id}).json()["session_id"]
    r = client.post(f"/dialogue/{sid}/message", json={"content": "   "})
    assert r.status_code == 422


def test_dialogue_dead_npc_422(world):
    settings, _, client, cid, npc_id, _, factory = world
    from app.db.models import Character

    with factory() as s:
        npc = s.get(Character, npc_id)
        npc.alive = False
        s.commit()
    r = client.post("/dialogue/start", json={"npc_id": npc_id})
    assert r.status_code == 422


def test_dialogue_unknown_npc_404(world):
    _, _, client, cid, *_ = world
    r = client.post("/dialogue/start", json={"npc_id": "npc_9999"})
    assert r.status_code == 404


def test_dialogue_requires_owned_character(world):
    _, _, client, cid, npc_id, *_ = world
    # user without a character
    client.post("/auth/logout")
    client.post("/auth/register", json={
        "username": "charless", "email": "ch@x.com", "password": "password123", "age_confirmed": True,
    })
    client.post("/auth/login", json={"username": "charless", "password": "password123"})
    r = client.post("/dialogue/start", json={"npc_id": npc_id})
    assert r.status_code == 422


def test_dialogue_foreign_session_404(world):
    _, _, client, cid, npc_id, *_ = world
    sid = client.post("/dialogue/start", json={"npc_id": npc_id}).json()["session_id"]
    client.post("/auth/logout")
    client.post("/auth/register", json={
        "username": "peeker", "email": "pk@x.com", "password": "password123", "age_confirmed": True,
    })
    client.post("/auth/login", json={"username": "peeker", "password": "password123"})
    r = client.get(f"/dialogue/{sid}")
    assert r.status_code == 404


# ---------- Dialogue module units ----------

def test_fallback_reply_affection_tiers():
    ctx_warm = {"relationship": {"affection": 60.0}, "npc_name": "Мария"}
    ctx_neutral = {"relationship": {"affection": 10.0}, "npc_name": "Пётр"}
    ctx_cold = {"relationship": {"affection": -60.0}, "npc_name": "Ганс"}
    warm = fallback_reply(ctx_warm, "Иван")
    neutral = fallback_reply(ctx_neutral, "Иван")
    cold = fallback_reply(ctx_cold, "Иван")
    assert warm != neutral != cold
    assert "Иван" in warm
    assert "Мария" not in warm  # template addresses the player


def test_suggested_responses_three():
    sugg = suggested_responses({"npc_name": "Мария"})
    assert len(sugg) == 3
    assert all(isinstance(s, str) and s for s in sugg)


def test_build_context_shape(world):
    settings, _, client, cid, npc_id, _, factory = world
    from app.db.models import Character

    with factory() as s:
        user_char = s.get(Character, cid)
        npc = s.get(Character, npc_id)
        ctx = build_context(s, settings.world.world_id, user_char, npc)
    assert ctx["npc_name"] == npc.first_name + " " + npc.last_name
    assert set(ctx["relationship"].keys()) == {"trust", "affection", "respect"}
    assert isinstance(ctx["npc_memories"], list)
