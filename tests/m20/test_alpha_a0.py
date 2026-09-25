"""
alpha A0 (#84): honest data contracts.

Tests: (a) balance owner/non-owner, (b) task buckets planned/active/
terminal, (c) inventory worn/slot exposure + malformed metadata,
(d) relationship summary in both pair orientations, (e) app.js pins.
"""
import json
import os
import sys

import pytest
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from app.api.app import create_app
from app.config.config import load_config
from app.db.models import (
    Account,
    Character,
    CharacterTask,
    Relationship,
    WorldObject,
    bootstrap,
    create_engine_factory,
)
from app.world.seed_world import seed_world

SETTINGS = None


def _mk_task(cid, tt, status, created_at, completed_at=None):
    return CharacterTask(character_id=cid, priority=1, task_type=tt,
                         status=status, source="system", parameters="{}",
                         created_at=created_at, completed_at=completed_at)


def _mk_obj(world_id, cid, otype, meta):
    return WorldObject(world_id=world_id, object_type=otype,
                       owner_character_id=cid, location_id=1, quantity=1,
                       object_metadata=meta, burn_state="intact",
                       flammability=0.1)


def setup_module(module):
    global SETTINGS
    SETTINGS = load_config("config/default.yaml")

@pytest.fixture()
def world(tmp_path):
    settings = SETTINGS.model_copy(update={
        "persistence": SETTINGS.persistence.model_copy(
            update={"db_path": str(tmp_path / "w.db")}
        )
    })
    engine = create_engine_factory(settings)
    with sessionmaker(bind=engine)() as session:
        bootstrap(engine, settings, seed=42)
        seed_world(session, settings, settings.world.world_id)
        session.commit()
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app(settings, factory)
    from fastapi.testclient import TestClient
    return settings, app, TestClient(app)

def test_balance_visibility(world):
    settings, app, client = world
    # User 1
    client.post("/auth/register", json={
        "username": "user1", "email": "u1@x.com", "password": "password123", "age_confirmed": True,
    })
    client.post("/auth/login", json={"username": "user1", "password": "password123"})
    r1 = client.post("/characters", json={"name": "Char1", "sex": "M", "age": 30})
    cid = r1.json()["id"]

    # Seed balance: character creation already provisions an Account row,
    # so update it (insert would violate the owner uniqueness constraint).
    with sessionmaker(bind=create_engine_factory(settings))() as session:
        acc = session.query(Account).filter_by(
            owner_type="character", owner_id=cid).first()
        assert acc is not None, "expected Account provisioned at creation"
        acc.balance = 1500
        session.commit()

    # Owner sees balance
    r_owner = client.get(f"/characters/{cid}")
    assert r_owner.status_code == 200
    assert r_owner.json()["balance"] == 1500

    # Non-owner doesn't see balance
    client.post("/auth/logout")
    client.post("/auth/register", json={
        "username": "user2", "email": "u2@x.com", "password": "password123", "age_confirmed": True,
    })
    client.post("/auth/login", json={"username": "user2", "password": "password123"})
    r_other = client.get(f"/characters/{cid}")
    assert r_other.status_code == 200
    assert "balance" not in r_other.json()

def test_tasks_buckets(world):
    settings, app, client = world
    client.post("/auth/register", json={
        "username": "user3", "email": "u@x.com", "password": "password123", "age_confirmed": True,
    })
    client.post("/auth/login", json={"username": "user3", "password": "password123"})
    r = client.post("/characters", json={"name": "C", "sex": "M", "age": 30})
    cid = r.json()["id"]

    with sessionmaker(bind=create_engine_factory(settings))() as session:
        # Planned
        t1 = _mk_task(cid, "T1", "planned", 10)
        # Active/Queued/STARTED
        t2 = _mk_task(cid, "T2", "active", 20)
        t3 = _mk_task(cid, "T3", "queued", 30)
        t4 = _mk_task(cid, "T4", "STARTED", 40)
        # Terminal
        t5 = _mk_task(cid, "T5", "completed", 0, completed_at=50)

        session.add_all([t1, t2, t3, t4, t5])
        session.commit()

    r_tasks = client.get(f"/characters/{cid}/tasks")
    assert r_tasks.status_code == 200
    data = r_tasks.json()

    assert len(data["planned"]) == 1
    assert data["planned"][0]["task_type"] == "T1"

    assert len(data["active"]) == 3
    types_active = {t["task_type"] for t in data["active"]}
    assert {"T2", "T3", "T4"}.issubset(types_active)

    assert len(data["recent_terminal"]) == 1
    assert data["recent_terminal"][0]["task_type"] == "T5"

def test_inventory_metadata(world):
    settings, app, client = world
    client.post("/auth/register", json={
        "username": "user3", "email": "u@x.com", "password": "password123", "age_confirmed": True,
    })
    client.post("/auth/login", json={"username": "user3", "password": "password123"})
    r = client.post("/characters", json={"name": "C", "sex": "M", "age": 30})
    cid = r.json()["id"]

    with sessionmaker(bind=create_engine_factory(settings))() as session:
        # Correct metadata
        o1 = _mk_obj(settings.world.world_id, cid, "Shirt",
                     json.dumps({"worn": True, "slot": "torso", "color": "red"}))
        # Not worn
        o2 = _mk_obj(settings.world.world_id, cid, "Hat",
                     json.dumps({"worn": False, "slot": "head"}))
        # Malformed
        o3 = _mk_obj(settings.world.world_id, cid, "Rock", "not-json")

        session.add_all([o1, o2, o3])
        session.commit()

    r_inv = client.get(f"/characters/{cid}/inventory")
    assert r_inv.status_code == 200
    items = r_inv.json()

    # Find Shirt
    shirt = next(i for i in items if i["object_type"] == "Shirt")
    assert shirt["worn"] is True
    assert shirt["slot"] == "torso"
    assert "color" not in shirt

    # Find Hat
    hat = next(i for i in items if i["object_type"] == "Hat")
    assert hat["worn"] is False
    assert hat["slot"] == "head"

    # Find Rock
    rock = next(i for i in items if i["object_type"] == "Rock")
    assert rock["worn"] is False
    assert rock["slot"] is None

def test_relationships_both_orientations(world):
    settings, app, client = world
    client.post("/auth/register", json={
        "username": "user3", "email": "u@x.com", "password": "password123", "age_confirmed": True,
    })
    client.post("/auth/login", json={"username": "user3", "password": "password123"})
    r = client.post("/characters", json={"name": "Me", "sex": "M", "age": 30})
    cid = r.json()["id"]

    with sessionmaker(bind=create_engine_factory(settings))() as session:
        # Other characters; ids derived from cid to satisfy the
        # character_a < character_b CHECK in both orientations.
        c_lo = Character(id="aaa_" + cid, world_id=settings.world.world_id,
                         first_name="NPC", last_name="One", age=30, sex="M",
                         birth_date="1996-01-01", created_at=0, updated_at=0,
                         location_id=1)
        c_hi = Character(id="zzz_" + cid, world_id=settings.world.world_id,
                         first_name="NPC", last_name="Two", age=30, sex="F",
                         birth_date="1996-01-01", created_at=0, updated_at=0,
                         location_id=1)
        session.add_all([c_lo, c_hi])
        session.commit()

        lo_id, hi_id = "aaa_" + cid, "zzz_" + cid
        # Me as A (cid < hi_id)
        rel1 = Relationship(world_id=settings.world.world_id,
                            character_a=cid, character_b=hi_id,
                            trust=10, affection=20, familiarity=30,
                            romantic_interest=40, updated_at=100)
        # Me as B (lo_id < cid)
        rel2 = Relationship(world_id=settings.world.world_id,
                            character_a=lo_id, character_b=cid,
                            trust=50, affection=60, familiarity=70,
                            romantic_interest=80, updated_at=200)

        session.add_all([rel1, rel2])
        session.commit()

    r_rel = client.get(f"/characters/{cid}/relationships")
    assert r_rel.status_code == 200
    data = r_rel.json()
    assert len(data) == 2

    # Check low-sorted NPC (row stored as (cid, hi) — wait: lo stored as (lo, cid))
    npc1 = next(i for i in data if i["other_id"] == lo_id)
    assert npc1["other_name"] == "NPC One"
    assert npc1["trust"] == 50

    # Check high-sorted NPC
    npc2 = next(i for i in data if i["other_id"] == hi_id)
    assert npc2["other_name"] == "NPC Two"
    assert npc2["trust"] == 10

    # Order check (updated_at desc): lo row has updated_at=200 > hi row's 100
    assert data[0]["other_id"] == lo_id

def test_app_js_string_pins():
    import os
    js_path = os.path.join(os.path.dirname(__file__), "../../backend/app/web/app.js")
    with open(js_path, encoding="utf-8") as f:
        js = f.read()

    assert "`/characters/${S.character.id}/tasks`" in js
    assert "надето" in js
    assert "Отношения:" in js
    assert "симпатия" in js
    assert "доверие" in js
    assert "—" in js # Check if needBar handles null/undefined
    assert "?? 100" not in js
