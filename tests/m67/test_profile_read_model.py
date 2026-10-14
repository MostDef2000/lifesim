"""#67 increment 1 — profile read model (GET /characters/{cid} owner block).

The backend change under test (already implemented in the working tree):
the owner-only branch of get_character ADDS five keys to the response —
``looks``, ``biography``, ``home_location_id``, ``traits`` (list of
{trait_key, value}, ordered by CharacterTrait.id == seeding order) and
``health`` ({health, stress}, present ONLY while the CharacterHealth row
exists). The change is additive: the non-owner response is untouched and
the pre-existing owner keys (balance/needs/job/control_mode/is_owner)
keep their semantics.

Fixture: m61-style TestClient world (tests/m61/test_roles_context.py) —
load_config(default.yaml), own sqlite engine per test, bootstrap +
seed_world + generate_population(20) + seed_social, owner User row via
hash_password(PW) logged in through /auth/login (cookie), reader client
registered + logged in as m67reader through /auth/*. The player character
is created with EXPLICIT looks/biography (the defaults are None, so a
non-None value proves the endpoint reads Character, not an invented
default).
"""

import os
import random

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.api.app import create_app
from app.api.auth import hash_password
from app.characters.generator import generate_population
from app.characters.player import create_player_character
from app.config.config import load_config
from app.db.models import (
    CharacterHealth,
    CharacterJob,
    Job,
    User,
    bootstrap,
    create_engine_factory,
)
from app.world.seed_world import seed_world
from app.world.social_seed import seed_social

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))

PW = "Correct-Horse-9!"
LOOKS = "test looks"
BIOGRAPHY = "test bio"
NEW_KEYS = ("looks", "biography", "home_location_id", "traits", "health")

SETTINGS = None


def setup_module(module):
    global SETTINGS
    SETTINGS = load_config(os.path.join(ROOT, "config/default.yaml"))


def make_settings(tmp_path):
    # social.enabled=True mirrors the m61 fixture: seed_social is authoritative
    # for org membership rows. Nothing here needs the LLM path.
    social = SETTINGS.social.model_copy(update={"enabled": True})
    return SETTINGS.model_copy(update={
        "persistence": SETTINGS.persistence.model_copy(
            update={"db_path": str(tmp_path / "w67.db")}
        ),
        "social": social,
    })


def build_world(settings, seed=42):
    engine = create_engine_factory(settings)
    with sessionmaker(bind=engine)() as session:
        bootstrap(engine, settings, seed=seed)
        seed_world(session, settings, settings.world.world_id)
        rng = random.Random(seed)
        generate_population(session, settings, rng, settings.world.world_id, 20)
        seed_social(session, settings, settings.world.world_id, rng)
        session.commit()
    return engine


@pytest.fixture()
def world(tmp_path):
    """Seeded world + player character with explicit looks/bio + two clients."""
    settings = make_settings(tmp_path)
    engine = build_world(settings)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    wid = settings.world.world_id
    with factory() as s:
        user = User(
            username="m67owner", email="m67@x.com",
            password_hash=hash_password(PW), age_confirmed=True, created_at=0,
        )
        s.add(user)
        s.commit()
        pc = create_player_character(
            s, settings, wid, user,
            name="Борис Орлов", sex="M", age=30, game_timestamp=0,
            looks=LOOKS, biography=BIOGRAPHY,
        )
        s.commit()
        # DB truth captured BEFORE any request — the endpoint must echo it.
        expected_home = pc.home_location_id
        job_row = (
            s.query(CharacterJob, Job)
            .join(Job, Job.id == CharacterJob.job_id)
            .filter(CharacterJob.character_id == pc.id)
            .first()
        )
        expected_job = job_row[1].title
        pc_id = pc.id
    app = create_app(settings, factory)
    owner = TestClient(app)
    r = owner.post("/auth/login", json={"username": "m67owner", "password": PW})
    assert r.status_code == 200, r.text
    reader = TestClient(app)
    r = reader.post("/auth/register", json={
        "username": "m67reader", "email": "m67r@x.com",
        "password": PW, "age_confirmed": True,
    })
    assert r.status_code == 201, r.text
    r = reader.post("/auth/login", json={"username": "m67reader", "password": PW})
    assert r.status_code == 200, r.text
    return settings, owner, reader, factory, pc_id, expected_home, expected_job


# ---------- (a) owner: the five additive keys, values echoed from the DB ----------

def test_owner_get_character_exposes_new_profile_keys(world):
    settings, owner, _, _, pc_id, expected_home, _ = world
    r = owner.get(f"/characters/{pc_id}")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["looks"] == LOOKS
    assert data["biography"] == BIOGRAPHY
    assert data["home_location_id"] == expected_home
    # create_player_character seeds one CharacterTrait(value=0) per configured
    # key, in trait_keys order; the endpoint orders by CharacterTrait.id.
    assert data["traits"] == [
        {"trait_key": k, "value": 0} for k in settings.population.trait_keys
    ]
    # create_player_character always seeds CharacterHealth(health=100, stress=0).
    assert data["health"] == {"health": 100.0, "stress": 0.0}


# ---------- (b) reader: additive contract — no new keys leak ----------

def test_reader_get_character_has_no_owner_keys(world):
    _, _, reader, _, pc_id, _, _ = world
    r = reader.get(f"/characters/{pc_id}")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["is_owner"] is False
    for key in NEW_KEYS:
        assert key not in data, f"owner-only key {key!r} leaked to a reader"


# ---------- (c) owner: pre-existing response keys keep their semantics ----------

def test_owner_get_character_still_has_pre_existing_keys(world):
    settings, owner, _, _, pc_id, _, expected_job = world
    r = owner.get(f"/characters/{pc_id}")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["balance"] == settings.economy.starting_balance
    assert data["needs"] and all(v == 100.0 for v in data["needs"].values())
    assert data["job"] == expected_job
    assert data["control_mode"] == "AUTONOMOUS"
    assert data["is_owner"] is True


# ---------- (d) health key is conditional on the CharacterHealth row ----------

def test_owner_health_key_omitted_when_row_missing(world):
    _, owner, _, factory, pc_id, _, _ = world
    with factory() as s:
        s.query(CharacterHealth).filter_by(character_id=pc_id).delete()
        s.commit()
    r = owner.get(f"/characters/{pc_id}")
    assert r.status_code == 200, r.text
    data = r.json()
    assert "health" not in data
    # The guard is health-scoped: the other four keys stay.
    for key in ("looks", "biography", "home_location_id", "traits"):
        assert key in data
