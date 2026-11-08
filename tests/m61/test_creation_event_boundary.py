"""#61 C3 — creation-event boundary: role events are emitted ONLY at wired
role-source mutation sites (elections, crime report, partnership).

Two silence pins (existing behavior, passing guards — no RED expected):

1. Player creation (POST /characters, backend/app/characters/player.py):
   the world feed gains the CHARACTER_CREATED row for the new character and
   ZERO ROLE_ACQUIRED/ROLE_REVOKED for them — even though derive_roles right
   after creation yields newcomer (+ occupation, the job is set at
   creation). Character creation is not a role-source mutation site.

2. Seed membership (seed_world + seed_social): a freshly seeded world's org
   members DO derive org roles (authoritative membership rows exist), yet
   the total ROLE_* event count in world_events for the world is 0 —
   seeding is not a role-source mutation site either (seed_social emits no
   events by design).
"""

import os
import random
import sys

import pytest
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from fastapi.testclient import TestClient  # noqa: E402

from app.api.app import create_app  # noqa: E402
from app.config.config import load_config  # noqa: E402
from app.db.models import (  # noqa: E402
    OrganizationMember,
    WorldEvent,
    bootstrap,
    create_engine_factory,
)
from app.social.roles import derive_roles  # noqa: E402
from app.world.seed_world import seed_world  # noqa: E402
from app.world.social_seed import seed_social  # noqa: E402

PW = "Correct-Horse-9!"
SETTINGS = None


def setup_module(module):
    global SETTINGS
    SETTINGS = load_config("config/default.yaml")


def make_settings(tmp_path):
    # social + org both on: org membership rows are seeded (authoritative
    # source for org roles) and player creation can assign a job.
    social = SETTINGS.social.model_copy(update={"enabled": True})
    org = SETTINGS.org.model_copy(update={"enabled": True})
    return SETTINGS.model_copy(update={
        "persistence": SETTINGS.persistence.model_copy(
            update={"db_path": str(tmp_path / "w.db")}
        ),
        "social": social,
        "org": org,
    })


def build_world(settings, seed=42):
    engine = create_engine_factory(settings)
    with sessionmaker(bind=engine)() as session:
        bootstrap(engine, settings, seed=seed)
        seed_world(session, settings, settings.world.world_id)
        rng = random.Random(seed)
        from app.characters.generator import generate_population

        generate_population(session, settings, rng, settings.world.world_id, 20)
        seed_social(session, settings, settings.world.world_id, rng)
        session.commit()
    return engine


def _register_and_login(client, username):
    client.post("/auth/register", json={
        "username": username, "email": f"{username}@x.com",
        "password": PW, "age_confirmed": True,
    })
    client.post("/auth/login", json={"username": username, "password": PW})


@pytest.fixture()
def world(tmp_path):
    """Seeded world (social+org on) + app/client + a player character."""
    settings = make_settings(tmp_path)
    engine = build_world(settings)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app(settings, factory)
    client = TestClient(app)
    _register_and_login(client, "c3player")
    r = client.post("/characters", json={"name": "Вера Соловьёва", "sex": "F", "age": 28})
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    return settings, app, client, factory, cid, engine


# ---------- 1. Player creation: CHARACTER_CREATED, zero ROLE_* ----------

def test_player_creation_emits_character_created_but_no_role_events(world):
    settings, _app, client, factory, cid, _engine = world
    wid = settings.world.world_id

    # The creation is real: derive_roles right after creation is NON-EMPTY
    # (newcomer + occupation — the job is assigned at creation).
    with factory() as s:
        roles = derive_roles(s, wid, cid)
    assert roles, "derive must be non-empty right after creation"
    ids = {r["id"] for r in roles}
    assert "newcomer" in ids and "occupation" in ids

    # The world feed gains the CHARACTER_CREATED row for the new character…
    feed = client.get("/world/events", params={"limit": 500}).json()
    created = [e for e in feed
               if e["event_type"] == "CHARACTER_CREATED" and e["actor_id"] == cid]
    assert len(created) == 1
    assert created[0]["payload"]["name"] == "Вера Соловьёва"

    # …and ZERO ROLE_* events for that character: creation is not a
    # role-source mutation site, so the derived newcomer/occupation roles
    # must not be announced.
    role_events = [e for e in feed
                   if e["event_type"] in ("ROLE_ACQUIRED", "ROLE_REVOKED")
                   and e["actor_id"] == cid]
    assert role_events == []
    # Belt and braces: the DB agrees (no feed-pagination blind spot).
    with factory() as s:
        assert s.query(WorldEvent).filter_by(
            world_id=wid, event_type="CHARACTER_CREATED",
            actor_id=cid).count() == 1
        for etype in ("ROLE_ACQUIRED", "ROLE_REVOKED"):
            assert s.query(WorldEvent).filter_by(
                world_id=wid, event_type=etype, actor_id=cid).count() == 0


# ---------- 2. Seed membership: roles derive, zero ROLE_* events ----------

def test_seeded_world_membership_derives_roles_but_emits_no_role_events(tmp_path):
    settings = make_settings(tmp_path)
    engine = build_world(settings)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    wid = settings.world.world_id

    # Seeded org members DO derive org roles (authoritative membership rows
    # exist — seed_social seeds leaders + members).
    with factory() as s:
        member = s.query(OrganizationMember).first()
        assert member is not None, "seeding must create org membership rows"
        roles = derive_roles(s, wid, member.character_id)
    assert roles, "derive must be non-empty for a seeded org member"
    assert any(r["id"] in ("org_leader", "org_member") for r in roles)

    # …yet seeding is not a role-source mutation site: the total ROLE_*
    # event count in world_events for the world is 0.
    with factory() as s:
        # Sanity: the feed is not empty — silence is by design, not a
        # broken emitter.
        assert s.query(WorldEvent).filter_by(world_id=wid).count() > 0
        for etype in ("ROLE_ACQUIRED", "ROLE_REVOKED"):
            assert s.query(WorldEvent).filter_by(
                world_id=wid, event_type=etype).count() == 0
