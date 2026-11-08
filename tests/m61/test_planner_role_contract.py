"""#61 C2 — the desire planner uses derived roles as a BOUNDED prerequisite.

Contract pinned here (behavior already exists and is correct; these are
passing guards, not RED-first):

``_public_role_opportunities`` (backend/app/desire/desire.py) is the
``public_role`` branch of the read-only opportunity planner. It must:

- (a) when the character HOLDS a derived org role (org_leader / org_member),
  emit exactly ONE opportunity ``role:{cid}`` titled
  «Ваша публичная роль: {title}» whose reason/source come from the derived
  role, with the read mechanic pointing at the authoritative
  ``/characters/{cid}/roles`` endpoint and the ``#/profile`` link;
- (b) when the character holds NO org role but an organization EXISTS, emit
  the honest intro pointer ``org:{org.id}`` «Познакомьтесь с …» for the
  FIRST org by id — a pointer, NOT an action to join;
- (c) when there is NO org and NO org role, emit NOTHING (no invented
  opportunity);
- (d) NON-org roles (newcomer / occupation / offender) never produce the
  ``role:`` opportunity — only org_leader / org_member qualify;
- (e) LIVE REFRESH: after an authoritative transition (a day-7 election
  flips leadership), the NEXT opportunities read reflects the NEW holder's
  role, and the desire row is still active;
- (f) READ-ONLY PURITY: repeated GETs change no row counts, emit no
  world_events, and leave the desire status unchanged.

The planner is read-only: it never writes rows, never emits events, and the
desire stays active across reads.
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
    Character,
    CharacterDesire,
    CharacterJob,
    Crime,
    Job,
    Organization,
    OrganizationMember,
    OrgLaw,
    OrgLawViolation,
    Partnership,
    Relationship,
    WorldEvent,
    WorldObject,
    bootstrap,
    create_engine_factory,
)
from app.policies.org import run_daily_org_policies  # noqa: E402
from app.social.roles import derive_roles  # noqa: E402
from app.world.seed_world import seed_world  # noqa: E402
from app.world.social_seed import seed_social  # noqa: E402

PW = "Correct-Horse-9!"
SETTINGS = None
DAY = 1440


def setup_module(module):
    global SETTINGS
    SETTINGS = load_config("config/default.yaml")


def make_settings(tmp_path):
    # social + org both on: org membership rows are seeded (authoritative
    # source for org roles) and the day-7 election policy can run.
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
    _register_and_login(client, "c2player")
    r = client.post("/characters", json={"name": "Борис Орлов", "sex": "M", "age": 30})
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    return settings, app, client, factory, cid, engine


def _community_org(factory, wid):
    with factory() as s:
        org = s.query(Organization).filter_by(world_id=wid, type="community").first()
        return org.id, org.name


def _first_org(factory, wid):
    """The first organization by id (the planner's intro-pointer target)."""
    with factory() as s:
        org = (
            s.query(Organization)
            .filter_by(world_id=wid)
            .order_by(Organization.id)
            .first()
        )
        return org.id, org.name


def _set_org_membership(factory, wid, cid, org_id, role):
    """Give the player an authoritative org membership (leader|member)."""
    with factory() as s:
        existing = s.query(OrganizationMember).filter_by(
            organization_id=org_id, character_id=cid).first()
        if existing is not None:
            existing.role = role
        else:
            s.add(OrganizationMember(
                organization_id=org_id, character_id=cid,
                role=role, joined_at=0))
        s.commit()


def _set_public_role_desire(client, cid):
    r = client.post(f"/characters/{cid}/desire",
                    json={"text": "хочу уважения и статуса в общине"})
    assert r.status_code == 201, r.text
    assert r.json()["desire"]["catalog_key"] == "public_role"


def _opps(client, cid):
    r = client.get(f"/characters/{cid}/desire/opportunities")
    assert r.status_code == 200, r.text
    return r.json()["opportunities"]


def _role_opps(opps, cid):
    return [o for o in opps if o["id"] == f"role:{cid}"]


def _org_opps(opps):
    return [o for o in opps if o["id"].startswith("org:")]


# ---------- (a) member / leader branch: exact opportunity shape ----------

def test_role_opportunity_for_org_member(world):
    settings, _app, client, factory, cid, _engine = world
    wid = settings.world.world_id
    org_id, org_name = _community_org(factory, wid)
    _set_org_membership(factory, wid, cid, org_id, "member")
    _set_public_role_desire(client, cid)

    opps = _opps(client, cid)
    role_opps = _role_opps(opps, cid)
    # Exactly ONE role opportunity, and NO org intro pointer (the role
    # branch takes precedence over the pointer when a role is held).
    assert len(role_opps) == 1
    assert _org_opps(opps) == []
    o = role_opps[0]
    assert o["id"] == f"role:{cid}"
    assert o["title"] == f"Ваша публичная роль: Участник «{org_name}»"
    # reason/source come from the DERIVED role (authoritative basis).
    with factory() as s:
        derived = next(r for r in derive_roles(s, wid, cid)
                       if r["id"] == "org_member")
    assert o["reason"] == derived["reason"]
    assert o["source"] == derived["source"]
    assert o["source"] == f"организация «{org_name}»"
    # The action is a READ of the authoritative roles endpoint.
    assert o["action"] == {"mechanic": "read",
                           "endpoint": f"/characters/{cid}/roles"}
    assert o["link"] == "#/profile"


def test_role_opportunity_for_org_leader(world):
    settings, _app, client, factory, cid, _engine = world
    wid = settings.world.world_id
    # A fresh org the player LEADS (the player is in no other org, so this
    # is their only org role → the leader title is what surfaces).
    with factory() as s:
        org = Organization(world_id=wid, name="Гильдия М61",
                           type="guild", location_id=None)
        s.add(org)
        s.flush()
        s.add(OrganizationMember(
            organization_id=org.id, character_id=cid,
            role="leader", joined_at=0))
        s.commit()
        new_org_name = org.name
    _set_public_role_desire(client, cid)

    opps = _opps(client, cid)
    role_opps = _role_opps(opps, cid)
    assert len(role_opps) == 1
    assert _org_opps(opps) == []
    o = role_opps[0]
    assert o["id"] == f"role:{cid}"
    assert o["title"] == f"Ваша публичная роль: Глава «{new_org_name}»"
    with factory() as s:
        derived = next(r for r in derive_roles(s, wid, cid)
                       if r["id"] == "org_leader")
    assert o["reason"] == derived["reason"]
    assert o["source"] == derived["source"] == f"организация «{new_org_name}»"
    assert o["action"] == {"mechanic": "read",
                           "endpoint": f"/characters/{cid}/roles"}
    assert o["link"] == "#/profile"


# ---------- (b) no org role + org exists: honest intro pointer ----------

def test_org_intro_pointer_when_no_org_role(world):
    settings, _app, client, factory, cid, _engine = world
    wid = settings.world.world_id
    # The player holds NO org role (created after seeding) but derives
    # newcomer + occupation — non-org roles that must NOT produce a role
    # opportunity. An organization exists → the intro pointer.
    with factory() as s:
        assert not any(r["id"] in ("org_leader", "org_member")
                       for r in derive_roles(s, wid, cid))
    org_id, org_name = _first_org(factory, wid)
    _set_public_role_desire(client, cid)

    opps = _opps(client, cid)
    assert _role_opps(opps, cid) == []  # no role opportunity
    org_opps = _org_opps(opps)
    assert len(org_opps) == 1
    o = org_opps[0]
    # The pointer targets the REAL first org by id.
    assert o["id"] == f"org:{org_id}"
    assert o["title"] == f"Познакомьтесь с «{org_name}»"
    assert o["source"] == "организация"
    # A pointer, NOT an action to join: read mechanic, roles endpoint.
    assert o["action"] == {"mechanic": "read",
                           "endpoint": f"/characters/{cid}/roles"}
    assert o["link"] == "#/profile"


# ---------- (c) no org + no org role: nothing invented ----------

def _remove_all_orgs(factory, wid):
    """Delete every organization (and the rows that reference them) so the
    world has no orgs — the 'no org' branch of the planner. FK order:
    characters.occupation_id → character_jobs → organization_members →
    jobs → organizations."""
    with factory() as s:
        for char in s.query(Character).filter_by(world_id=wid).all():
            char.occupation_id = None
        for obj in s.query(WorldObject).filter_by(world_id=wid).all():
            obj.owner_organization_id = None
        s.query(OrgLawViolation).delete()
        s.query(OrgLaw).delete()
        s.query(CharacterJob).delete()
        s.query(OrganizationMember).delete()
        s.query(Job).delete()
        s.query(Organization).filter_by(world_id=wid).delete()
        s.commit()


def test_no_org_no_role_zero_opportunities(world):
    settings, _app, client, factory, cid, _engine = world
    wid = settings.world.world_id
    _remove_all_orgs(factory, wid)
    with factory() as s:
        assert s.query(Organization).filter_by(world_id=wid).count() == 0
        # The player still derives non-org roles (newcomer) but no org role.
        assert not any(r["id"] in ("org_leader", "org_member")
                       for r in derive_roles(s, wid, cid))
    _set_public_role_desire(client, cid)

    opps = _opps(client, cid)
    # No org, no org role → the planner invents nothing.
    assert opps == []


# ---------- (d) non-org roles never produce the role opportunity ----------

def test_non_org_roles_do_not_produce_role_opportunity(world):
    settings, _app, client, factory, cid, _engine = world
    wid = settings.world.world_id
    # Give the player a REPORTED crime → the offender role (non-org). The
    # player already derives newcomer + occupation. Still no org role.
    with factory() as s:
        char = s.get(Character, cid)
        s.add(Crime(
            world_id=wid, crime_type="theft", actor_character_id=cid,
            location_id=char.location_id, day=1, status="reported",
            created_at=DAY))
        s.commit()
        roles = derive_roles(s, wid, cid)
        ids = {r["id"] for r in roles}
    # The player genuinely holds non-org roles…
    assert "newcomer" in ids and "occupation" in ids and "offender" in ids
    # …but none of them is an org role.
    assert not (ids & {"org_leader", "org_member"})

    # …and the partner role (non-org) is no exception: link cid with another
    # character via a Partnership row (canonical string order a < b).
    with factory() as s:
        other = s.query(Character).filter(Character.id != cid).first()
        a, b = sorted([cid, other.id])
        s.add(Partnership(
            world_id=wid, character_a_id=a, character_b_id=b, created_at=0))
        s.commit()
        ids = {r["id"] for r in derive_roles(s, wid, cid)}
    assert "partner" in ids

    org_id, _org_name = _first_org(factory, wid)
    _set_public_role_desire(client, cid)
    opps = _opps(client, cid)
    # No role opportunity from the non-org roles…
    assert _role_opps(opps, cid) == []
    # …and the org intro pointer is still the honest basis.
    assert [o["id"] for o in _org_opps(opps)] == [f"org:{org_id}"]


# ---------- (e) live refresh: an election flips the role on the next read ----------

def _make_player_community_leader(factory, settings, wid, cid):
    """Add the player to the community org and make them the deterministic
    day-7 election winner (everyone loves the player)."""
    org_id, _name = _community_org(factory, wid)
    _set_org_membership(factory, wid, cid, org_id, "member")
    with factory() as s:
        chars = (
            s.query(Character)
            .filter_by(world_id=wid, alive=True)
            .order_by(Character.id)
            .all()
        )
        for other in chars:
            if other.id == cid:
                continue
            a, b = sorted([cid, other.id])
            rel = s.query(Relationship).filter_by(
                world_id=wid, character_a=a, character_b=b).first()
            if rel is None:
                s.add(Relationship(
                    world_id=wid, character_a=a, character_b=b,
                    affection=100.0, updated_at=0))
            else:
                rel.affection = 100.0
        s.flush()
        # The day-7 scheduled election: the player (score 100·(n-1)) wins.
        run_daily_org_policies(s, wid, settings, 7 * DAY)
        s.commit()


def test_live_refresh_after_election(world):
    settings, _app, client, factory, cid, _engine = world
    wid = settings.world.world_id
    org_id, org_name = _community_org(factory, wid)

    # Before the election: the player is a plain member → org_member title.
    _set_org_membership(factory, wid, cid, org_id, "member")
    _set_public_role_desire(client, cid)
    before = _opps(client, cid)
    role_before = _role_opps(before, cid)
    assert len(role_before) == 1
    assert role_before[0]["title"] == (
        f"Ваша публичная роль: Участник «{org_name}»")

    # Authoritative transition: the day-7 election makes the player leader.
    _make_player_community_leader(factory, settings, wid, cid)
    with factory() as s:
        leader = s.query(OrganizationMember).filter_by(
            organization_id=org_id, role="leader").first()
        assert leader.character_id == cid

    # The NEXT opportunities read reflects the NEW role (org_leader).
    after = _opps(client, cid)
    role_after = _role_opps(after, cid)
    assert len(role_after) == 1
    assert role_after[0]["title"] == (
        f"Ваша публичная роль: Глава «{org_name}»")
    assert role_after[0]["action"] == {
        "mechanic": "read", "endpoint": f"/characters/{cid}/roles"}

    # The desire row is still active (the planner never abandons it).
    desire = client.get(f"/characters/{cid}/desire").json()["desire"]
    assert desire is not None
    assert desire["status"] == "active"
    assert desire["catalog_key"] == "public_role"


# ---------- (f) read-only purity: repeated GETs change nothing ----------

def test_planner_read_only_purity(world):
    settings, _app, client, factory, cid, _engine = world
    wid = settings.world.world_id
    org_id, _name = _community_org(factory, wid)
    _set_org_membership(factory, wid, cid, org_id, "member")
    _set_public_role_desire(client, cid)

    def snapshot():
        with factory() as s:
            return {
                "world_events": s.query(WorldEvent)
                                 .filter_by(world_id=wid).count(),
                "org_members": s.query(OrganizationMember).count(),
                "orgs": s.query(Organization).filter_by(world_id=wid).count(),
                "desires": s.query(CharacterDesire)
                            .filter_by(character_id=cid).count(),
                "desire_status": sorted(
                    d.status for d in s.query(CharacterDesire)
                    .filter_by(character_id=cid).all()),
            }

    before = snapshot()
    for _ in range(3):
        opps = _opps(client, cid)
        # The read is stable and non-empty (the role opportunity is present).
        assert len(_role_opps(opps, cid)) == 1
    after = snapshot()
    assert after == before, "planner must be read-only"
    # The desire is still active and unchanged.
    desire = client.get(f"/characters/{cid}/desire").json()["desire"]
    assert desire["status"] == "active"
