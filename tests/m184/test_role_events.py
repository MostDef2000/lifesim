"""m184 — #61 v2 D2: ROLE_ACQUIRED / ROLE_REVOKED world events.

Role SOURCE mutations emit narrative world events computed as the
set-difference of derive-on-read role identities before/after the
mutation (app/social/roles.py stays the ONLY source of truth — no
SocialRole store, no sync machinery). Pins:

- election with a NEW winner: ROLE_ACQUIRED(org_leader@org) for the
  winner + ROLE_REVOKED(org_leader@org) for the old leader, in the SAME
  transaction as the role flip (rollback drops both), plus the honest
  org_member side effects of the flip (old leader becomes a plain
  member, the winner stops being one);
- re-election of the INCUMBENT: the temporary demote/promote nets out
  to an empty diff → zero role events;
- crime report: ROLE_ACQUIRED offender@...; a repeat report of an
  already-reported crime: no duplicate event; resolution: the offender
  role is RETAINED (derive grants it for reported|resolved alike) → no
  ROLE_* event;
- identity: (role id, source entity) — org roles key by the org's ID
  (internal source_entity_id; m184 contract correction: Organization.name
  is not unique), other kinds by their source string — org_leader in
  TWO orgs, same name or not, yields two distinct identities; title/name
  aliasing is impossible;
- pins: EventType count == 45 with the two role-event names present
  (#182 added PARTNERSHIP_DECLARED, #195 added RELATIONSHIP_FORMED on top
  of the D2 pair);
- coverage: newcomer expiry is clock-driven (no mutation site) → emits
  nothing, pinned end-to-end (fast-forward clock + all daily phases).
"""
import json
import os
import random
import sys

import pytest  # noqa: F401
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from app.config.config import load_config  # noqa: E402
from app.crime.crime import _witness_and_report, run_police_phase  # noqa: E402
from app.db.models import (  # noqa: E402
    Character,
    Crime,
    Organization,
    OrganizationMember,
    Relationship,
    WorldClock,
    WorldEvent,
    bootstrap,
)
from app.events.events import EventType, log_event  # noqa: E402
from app.policies.org import run_daily_org_policies  # noqa: E402
from app.social.roles import (  # noqa: E402
    derive_roles,
    project_role_events,
    roles_snapshot,
)
from app.world.seed_world import seed_world  # noqa: E402
from app.world.social_seed import seed_social  # noqa: E402

SETTINGS = None
DAY = 1440


def setup_module(module):
    global SETTINGS
    SETTINGS = load_config("config/default.yaml")


def make_settings(default_settings, *, org=True, crime_updates=None):
    social = default_settings.social.model_copy(update={"enabled": True})
    updates = {"social": social}
    if org:
        updates["org"] = default_settings.org.model_copy(update={"enabled": True})
    if crime_updates is not None:
        updates["crime"] = default_settings.crime.model_copy(
            update=crime_updates)
    return default_settings.model_copy(update=updates)


def build_world(settings, population=5, seed=42):
    """Seed a small world with the social layer on. Returns (session, world_id)."""
    engine = create_engine("sqlite:///:memory:")
    bootstrap(engine, settings, seed)
    session = sessionmaker(bind=engine)()
    world_id = settings.world.world_id

    from app.characters.generator import generate_population

    seed_world(session, settings, world_id)
    generate_population(session, settings, random.Random(seed), world_id, population)
    seed_social(session, settings, world_id, random.Random(seed))
    session.commit()
    return session, world_id


def community(session, world_id):
    return session.query(Organization).filter_by(
        world_id=world_id, type="community").first()


def leader_of(session, org):
    return session.query(OrganizationMember).filter_by(
        organization_id=org.id, role="leader").first()


def role_events(session, world_id, etype):
    """(event, payload) pairs of one ROLE_* type, id order."""
    return [
        (e, json.loads(e.payload))
        for e in session.query(WorldEvent)
        .filter_by(world_id=world_id, event_type=etype)
        .order_by(WorldEvent.id)
        .all()
    ]


def org_events(session, world_id, etype, org):
    """(event, payload) pairs of `etype` for one organization's transitions."""
    return [
        (e, p) for e, p in role_events(session, world_id, etype)
        if p.get("organization_id") == org.id
    ]


def set_mutual_affection(session, world_id, char_id, others, value):
    """affection(v, char_id) = value for every pair (char_id, v)."""
    for other_id in others:
        if other_id == char_id:
            continue
        a, b = sorted([char_id, other_id])
        rel = session.query(Relationship).filter_by(
            world_id=world_id, character_a=a, character_b=b).first()
        if rel is None:
            rel = Relationship(
                world_id=world_id, character_a=a, character_b=b,
                affection=value, updated_at=0)
            session.add(rel)
        else:
            rel.affection = value
    session.flush()


def org_source(org):
    """The source string derive_roles builds for org roles (identity part)."""
    return f"организация «{org.name}»"


# ---------- 1. Election with a NEW winner ----------

def test_election_new_winner_emits_acquire_and_revoke(default_settings):
    settings = make_settings(default_settings)
    session, world_id = build_world(settings)
    org = community(session, world_id)
    old_leader_id = leader_of(session, org).character_id
    chars = sorted(
        (c for c in session.query(Character).filter_by(world_id=world_id).all()),
        key=lambda c: c.id)
    winner = next(c for c in chars if c.alive and c.id != old_leader_id)

    # Deterministic winner: everyone loves the challenger (score 400 vs ~0).
    set_mutual_affection(
        session, world_id, winner.id, [c.id for c in chars], 100.0)

    run_daily_org_policies(session, world_id, settings, 7 * 1440)
    session.flush()

    # NOTE: every org holds a scheduled election on day 7 — assertions are
    # scoped to THIS org's leadership transition (its source identity).
    comm_source = f"организация «{org.name}»"
    acquired = role_events(session, world_id, "ROLE_ACQUIRED")
    revoked = role_events(session, world_id, "ROLE_REVOKED")
    acq_comm = [(e, p) for e, p in acquired if p["source"] == comm_source]
    rev_comm = [(e, p) for e, p in revoked if p["source"] == comm_source]
    # Leadership flip, identity org_leader@<org>:
    assert any(e.actor_id == winner.id and p["role"] == "org_leader"
               for e, p in acq_comm)
    assert any(e.actor_id == old_leader_id and p["role"] == "org_leader"
               for e, p in rev_comm)
    # Honest diff side effects: the old leader becomes a plain member, the
    # winner stops being one (same org source, same transaction).
    assert any(e.actor_id == old_leader_id and p["role"] == "org_member"
               for e, p in acq_comm)
    assert any(e.actor_id == winner.id and p["role"] == "org_member"
               for e, p in rev_comm)
    # Exactly these four projections for this org, for exactly these two
    # characters.
    assert {(e.actor_id, p["role"]) for e, p in acq_comm + rev_comm} == {
        (winner.id, "org_leader"), (winner.id, "org_member"),
        (old_leader_id, "org_leader"), (old_leader_id, "org_member"),
    }
    # Payload conventions: title/source/reason snapshot, character id+name,
    # causal linkage to the org's ELECTION event; effective time is the
    # WorldEvent.game_timestamp column (the mutation's now).
    election = org_events(session, world_id, "ELECTION", org)
    assert len(election) == 1
    e_election, p_election = election[0]
    assert e_election.actor_id == winner.id
    assert e_election.game_timestamp == 7 * 1440
    for e, p in acq_comm + rev_comm:
        assert e.actor_id == p["character_id"]
        assert e.game_timestamp == 7 * 1440
        assert p["character_name"]
        assert p["title"] and p["reason"]
        assert p["cause"] == "election"
        assert p["cause_event_id"] == e_election.id
    # The winner's acquired identity is exactly org_leader@<org>.
    # m184 contract correction: identity keyed by org ID (source_entity_id),
    # name-keyed pin was stale.
    win_identity = ("org_leader", org.id)
    snap = roles_snapshot(session, world_id, winner.id)
    assert win_identity in snap
    assert snap[win_identity]["title"] == f"Глава «{org.name}»"
    # The source row actually flipped.
    assert leader_of(session, org).character_id == winner.id


def test_role_events_atomic_with_mutation_rollback(default_settings):
    """Same transaction: a rollback after the mutation drops the events too."""
    settings = make_settings(default_settings)
    session, world_id = build_world(settings)
    org = community(session, world_id)
    old_leader_id = leader_of(session, org).character_id
    chars = sorted(
        (c for c in session.query(Character).filter_by(world_id=world_id).all()),
        key=lambda c: c.id)
    winner = next(c for c in chars if c.alive and c.id != old_leader_id)
    set_mutual_affection(session, world_id, winner.id, [c.id for c in chars], 100.0)

    run_daily_org_policies(session, world_id, settings, 7 * 1440)
    session.flush()
    assert session.query(WorldEvent).filter_by(
        world_id=world_id, event_type="ROLE_ACQUIRED").count() > 0
    assert session.query(WorldEvent).filter_by(
        world_id=world_id, event_type="ROLE_REVOKED").count() > 0

    session.rollback()

    for etype in ("ROLE_ACQUIRED", "ROLE_REVOKED", "ELECTION"):
        assert session.query(WorldEvent).filter_by(
            world_id=world_id, event_type=etype).count() == 0
    assert leader_of(session, org).character_id == old_leader_id


# ---------- 2. Re-election of the incumbent ----------

def test_reelection_of_incumbent_emits_nothing(default_settings):
    """Re-election of the incumbent: the code temporarily demotes/promotes
    the SAME member row, so the set-diff across the whole resolve is empty
    → zero role events for this org's leadership.

    Scope note: EVERY org holds a scheduled election on day 7 (and the
    boosted incumbent may also win other orgs they belong to) — the no-op
    claim is asserted for THIS org's transition, which is the unit the
    identity tuple addresses."""
    settings = make_settings(default_settings)
    session, world_id = build_world(settings)
    org = community(session, world_id)
    incumbent_id = leader_of(session, org).character_id
    chars = session.query(Character).filter_by(world_id=world_id).all()
    set_mutual_affection(
        session, world_id, incumbent_id, [c.id for c in chars], 100.0)
    comm_source = f"организация «{org.name}»"
    roles_before = {
        k: v for k, v in roles_snapshot(session, world_id, incumbent_id).items()
        if k[1] == comm_source}

    run_daily_org_policies(session, world_id, settings, 7 * 1440)
    session.flush()

    # The community-org election DID happen (scheduled day, incumbent
    # re-elected)…
    elections = org_events(session, world_id, "ELECTION", org)
    assert len(elections) == 1
    e_election, _ = elections[0]
    assert e_election.actor_id == incumbent_id
    assert leader_of(session, org).character_id == incumbent_id
    # …but the temporary demote/promote of the SAME row nets out: no role
    # events carry this org as their source (other orgs' elections are
    # separate transitions with their own sources).
    acquired = role_events(session, world_id, "ROLE_ACQUIRED")
    revoked = role_events(session, world_id, "ROLE_REVOKED")
    assert all(p["source"] != comm_source for e, p in acquired + revoked)
    assert all(e.actor_id != incumbent_id or p["source"] != comm_source
               for e, p in acquired + revoked)
    # The incumbent's community-org derived roles are unchanged.
    roles_after = {
        k: v for k, v in roles_snapshot(session, world_id, incumbent_id).items()
        if k[1] == comm_source}
    assert roles_after == roles_before


# ---------- 3. Crime report / resolve ----------

def test_crime_report_acquire_no_dup_and_resolve_keeps_offender(default_settings):
    settings = make_settings(
        default_settings, org=False,
        crime_updates={"enabled": True, "detection_base": 1.0})
    session, world_id = build_world(settings)
    chars = sorted(
        (c for c in session.query(Character).filter_by(world_id=world_id).all()),
        key=lambda c: c.id)
    actor, witness = chars[0], chars[1]
    witness.location_id = actor.location_id
    session.flush()

    # Real flow: the commit event exists before the witness pass runs.
    log_event(session, world_id, DAY, EventType.CRIME_COMMITED,
              actor_id=actor.id, payload={"type": "theft", "day": 1})
    crime = Crime(
        world_id=world_id, crime_type="theft", actor_character_id=actor.id,
        location_id=actor.location_id, day=1, status="unreported",
        created_at=DAY)
    session.add(crime)
    session.flush()

    _witness_and_report(session, world_id, 1, DAY, crime, actor, settings)
    assert crime.status == "reported"

    acquired = role_events(session, world_id, "ROLE_ACQUIRED")
    assert len(acquired) == 1
    e, p = acquired[0]
    assert e.actor_id == actor.id
    assert p["role"] == "offender"
    assert p["title"] == "Нарушитель"
    assert p["character_id"] == actor.id
    assert p["cause"] == "crime_reported"
    report = session.query(WorldEvent).filter_by(
        world_id=world_id, event_type="CRIME_REPORTED").one()
    assert p["cause_event_id"] == report.id
    # Identity is source-based: exactly one offender identity, no aliasing.
    identity = ("offender", "зарегистрированное нарушение")
    assert identity in roles_snapshot(session, world_id, actor.id)

    # A second report of the already-reported crime: the diff is empty (the
    # role is identity-based and already derived) → no duplicate event.
    _witness_and_report(session, world_id, 1, DAY, crime, actor, settings)
    assert len(role_events(session, world_id, "ROLE_ACQUIRED")) == 1

    # Resolution does NOT revoke the offender (reported|resolved both grant
    # the role) → NO ROLE_* event; the role still derives afterwards.
    run_police_phase(session, world_id, 2, 2 * 1440, settings)
    assert crime.status == "resolved"
    assert role_events(session, world_id, "ROLE_REVOKED") == []
    assert len(role_events(session, world_id, "ROLE_ACQUIRED")) == 1
    assert identity in roles_snapshot(session, world_id, actor.id)


# ---------- 4. Identity: same role kind, two sources ----------

def test_identity_distinguishes_same_role_kind_from_two_orgs(default_settings):
    settings = make_settings(default_settings)
    session, world_id = build_world(settings)
    org1 = community(session, world_id)
    leader1 = leader_of(session, org1)
    cid = leader1.character_id

    org2 = Organization(
        world_id=world_id, name="Вторая община",
        type="community", location_id=org1.location_id)
    session.add(org2)
    session.flush()
    session.add(OrganizationMember(
        organization_id=org2.id, character_id=cid, role="leader", joined_at=0))
    session.flush()

    snap = roles_snapshot(session, world_id, cid)
    leader_identities = sorted(
        k for k in snap if k[0] == "org_leader")
    # Both identities present and DISTINCT — the source entity is part of
    # the identity, so single-org title-only aliasing is impossible. (The
    # seeded leader may legitimately lead several config orgs beyond these
    # two — presence is asserted, not exhaustive equality.)
    # m184 contract correction: identity keyed by org ID (source_entity_id),
    # name-keyed pin was stale. The fixture orgs have different names, so
    # the same-name aliasing case is covered separately by
    # test_same_name_orgs_do_not_alias_role_events (section 7).
    id1 = ("org_leader", org1.id)
    id2 = ("org_leader", org2.id)
    assert id1 in snap
    assert id2 in snap
    assert len(set(leader_identities)) == len(leader_identities)
    assert id1 != id2 and id1 in leader_identities and id2 in leader_identities

    # Demote in ONE org → exactly that identity is revoked (and the plain
    # member role of the same org acquired); the other org is untouched.
    leader1.role = "member"
    session.flush()
    events = project_role_events(
        session, world_id, cid, before=snap, cause="test")
    assert [(etype.value, p["role"], p["source"]) for etype, p in events] == [
        ("ROLE_ACQUIRED", "org_member", f"организация «{org1.name}»"),
        ("ROLE_REVOKED", "org_leader", f"организация «{org1.name}»"),
    ]


def test_project_role_events_is_noop_without_a_diff(default_settings):
    """Pure helper contract: identical before/current state → no events."""
    settings = make_settings(default_settings)
    session, world_id = build_world(settings)
    cid = session.query(Character).filter_by(world_id=world_id).first().id
    snap = roles_snapshot(session, world_id, cid)
    assert project_role_events(
        session, world_id, cid, before=snap, cause="x") == []


# ---------- 5. Pins ----------

def test_event_type_count_44_with_role_events():
    from app.events.events import EventType

    assert len(EventType) == 45
    assert "ROLE_ACQUIRED" in EventType.__members__
    assert "ROLE_REVOKED" in EventType.__members__


# ---------- 6. Coverage: newcomer expiry emits nothing ----------

def test_newcomer_expiry_emits_nothing(default_settings):
    """newcomer→permanent is clock-driven (derive: days < NEWCOMER_DAYS) and
    has NO mutation site — D2 wires only source-row mutations, so the
    boundary emits nothing BY DESIGN even though the derived set changes."""
    from app.crime.crime import run_crime_phase

    settings = make_settings(default_settings)
    session, world_id = build_world(settings)
    cid = session.query(Character).filter_by(world_id=world_id).first().id
    assert any(r["id"] == "newcomer" for r in derive_roles(session, world_id, cid))

    # Fast-forward the clock past the 7-day boundary; run every daily phase
    # for that tick — none of them is a role-source mutation site.
    clock = session.query(WorldClock).filter_by(world_id=world_id).one()
    clock.game_timestamp = 8 * 1440
    session.flush()
    assert run_crime_phase(session, world_id, 8, 8 * 1440, settings) == 0
    run_police_phase(session, world_id, 8, 8 * 1440, settings)
    run_daily_org_policies(session, world_id, settings, 8 * 1440)
    session.flush()

    # The role changed on read…
    assert all(r["id"] != "newcomer" for r in derive_roles(session, world_id, cid))
    # …and nothing was emitted: no acquisition, no revocation, anywhere.
    assert role_events(session, world_id, "ROLE_ACQUIRED") == []
    assert role_events(session, world_id, "ROLE_REVOKED") == []


# ---------- 7. Same-name orgs must not alias role identities (D2) ----------
#
# Organization.name is NOT unique (app/db/models.py). The D2 contract says a
# role's identity must include the source entity's org ID, not just the
# title/name. On base, _role_identity is (role id, source) where the org
# source string embeds the org NAME — so two same-named orgs alias to ONE
# identity in the before/after snapshot diff. These tests pin the contract
# (orgs are distinct entities) and are expected to be RED on base.

def _make_same_name_orgs(session, world_id, name, loc, members_by_org):
    """Create two orgs with the IDENTICAL `name`; members_by_org maps
    org_index (0|1) -> list of (character, role) rows. Returns (org_a, org_b)."""
    orgs = []
    for i in (0, 1):
        org = Organization(
            world_id=world_id, name=name, type="guild", location_id=loc)
        session.add(org)
        session.flush()
        for char, role in members_by_org[i]:
            session.add(OrganizationMember(
                organization_id=org.id, character_id=char.id,
                role=role, joined_at=0))
        orgs.append(org)
    session.flush()
    return orgs[0], orgs[1]


def test_same_name_orgs_do_not_alias_role_events(default_settings):
    """Two orgs with the IDENTICAL name: a leader elected in org A (X), then a
    leader elected in org B (Y). Contract: Y's leader acquisition is projected,
    X keeps their (org A) role — NO revocation of X's org-A leadership from B's
    election, and BOTH acquisitions appear.

    On base the aliased (name-keyed) identity is expected to lose X's org-A
    acquisition and/or X's org-B revocation — the test asserts the contract,
    not the defect.
    """
    settings = make_settings(default_settings)
    session, world_id = build_world(settings)
    chars = sorted(
        (c for c in session.query(Character).filter_by(world_id=world_id).all()),
        key=lambda c: c.id)
    X, Y, Z1, Z2, Z3 = chars[0], chars[1], chars[2], chars[3], chars[4]
    comm = community(session, world_id)
    name = "Община М184"  # unique: no seeded org uses this name

    # Org A: X (member), Z1 (leader), Z2 (member).  Org B: X (leader),
    # Y (member), Z3 (member).  X spans BOTH same-named orgs — the aliasing
    # unit. X is elected into A's leadership; Y is elected into B's.
    org_a, org_b = _make_same_name_orgs(
        session, world_id, name, comm.location_id,
        {0: [(X, "member"), (Z1, "leader"), (Z2, "member")],
         1: [(X, "leader"), (Y, "member"), (Z3, "member")]})
    org_a.leader_character_id = Z1.id
    org_b.leader_character_id = X.id
    session.flush()

    # Deterministic winners via affection (score = Σ affection from voters):
    #   Org A candidates {X, Z1, Z2}: X=200, Z1=100, Z2=100  -> X wins.
    #   Org B candidates {X, Y, Z3}:  Y=200, X=100, Z3=100   -> Y wins.
    set_mutual_affection(session, world_id, X.id, [Z1.id, Z2.id], 100.0)
    set_mutual_affection(session, world_id, Z1.id, [Z2.id], 0.0)
    set_mutual_affection(session, world_id, X.id, [Y.id], 100.0)
    set_mutual_affection(session, world_id, Y.id, [Z3.id], 100.0)
    set_mutual_affection(session, world_id, X.id, [Z3.id], 0.0)

    # Both orgs hold their scheduled day-7 election in one policy pass
    # (orgs processed by id: A first, then B).
    run_daily_org_policies(session, world_id, settings, 7 * 1440)
    session.flush()

    # The source rows actually flipped (scenario ran as intended).
    assert leader_of(session, org_a).character_id == X.id
    assert leader_of(session, org_b).character_id == Y.id

    source = f"организация «{name}»"
    acquired = role_events(session, world_id, "ROLE_ACQUIRED")
    revoked = role_events(session, world_id, "ROLE_REVOKED")
    acq_x = [(e, p) for e, p in acquired
             if e.actor_id == X.id and p["role"] == "org_leader"
             and p["source"] == source]
    acq_y = [(e, p) for e, p in acquired
             if e.actor_id == Y.id and p["role"] == "org_leader"
             and p["source"] == source]
    rev_x = [(e, p) for e, p in revoked
             if e.actor_id == X.id and p["role"] == "org_leader"
             and p["source"] == source]

    # Y's leader acquisition (org B) is projected.
    assert len(acq_y) == 1
    # X's leader acquisition (org A) is projected — the aliased identity must
    # not swallow it. (RED on base: the name-keyed identity already present
    # from org B makes the diff see no new org_leader for X.)
    assert len(acq_x) == 1
    # X keeps their org-A leadership: across the two elections X lost exactly
    # ONE org_leader role (org B's, legitimately) — so exactly one org_leader
    # revocation for X, and org A's leadership is NOT revoked. (RED on base:
    # the aliased identity survives via org A, so the org-B revocation is
    # lost and the count is 0.)
    assert len(rev_x) == 1


# ---------- 8. Rename stability: a rename is not a role-source mutation ----------

def test_rename_emits_nothing_and_next_election_diffs_on_new_name(default_settings):
    """An org with an elected leader is renamed, then a leadership transfer
    (day-7 election) triggers the role diff in that org. Contract: the rename
    itself emits NOTHING (it is not a wired role-source mutation), and the
    election produces exactly one revoke (old leader) + one acquire (new
    leader), both on the NEW name.

    RED discipline: this is expected to PASS on base — the per-site snapshot is
    taken fresh at the mutation, so a rename (which calls no
    project_role_events) is invisible to the diff. Kept as a contract pin that
    guards the fix (org-id-keyed identity must stay rename-stable too).
    """
    settings = make_settings(default_settings)
    session, world_id = build_world(settings)
    chars = sorted(
        (c for c in session.query(Character).filter_by(world_id=world_id).all()),
        key=lambda c: c.id)
    X, Y, Z1 = chars[0], chars[1], chars[2]
    comm = community(session, world_id)
    old_name = "Община Ренейм"  # unique: no seeded org uses this name
    org = Organization(
        world_id=world_id, name=old_name, type="guild",
        location_id=comm.location_id)
    session.add(org)
    session.flush()
    session.add(OrganizationMember(
        organization_id=org.id, character_id=X.id, role="leader", joined_at=0))
    session.add(OrganizationMember(
        organization_id=org.id, character_id=Y.id, role="member", joined_at=0))
    session.add(OrganizationMember(
        organization_id=org.id, character_id=Z1.id, role="member", joined_at=0))
    org.leader_character_id = X.id
    session.flush()

    # Deterministic winner: Y (score 200 vs X=100, Z1=100).
    set_mutual_affection(session, world_id, X.id, [Y.id], 100.0)
    set_mutual_affection(session, world_id, Y.id, [Z1.id], 100.0)
    set_mutual_affection(session, world_id, X.id, [Z1.id], 0.0)

    # Rename the org — a plain attribute write, NOT a role-source mutation.
    new_name = "Община Ренейм2"
    org.name = new_name
    session.flush()
    # The rename itself emits nothing.
    assert role_events(session, world_id, "ROLE_ACQUIRED") == []
    assert role_events(session, world_id, "ROLE_REVOKED") == []

    # The day-7 election transfers leadership X -> Y.
    run_daily_org_policies(session, world_id, settings, 7 * 1440)
    session.flush()
    assert leader_of(session, org).character_id == Y.id

    new_source = f"организация «{new_name}»"
    old_source = f"организация «{old_name}»"
    acquired = role_events(session, world_id, "ROLE_ACQUIRED")
    revoked = role_events(session, world_id, "ROLE_REVOKED")
    # No event references the old name: the rename emitted nothing and the
    # election diffed on the fresh name.
    assert all(p["source"] != old_source for e, p in acquired + revoked)
    rev_leader = [(e, p) for e, p in revoked
                  if p["role"] == "org_leader" and p["source"] == new_source]
    acq_leader = [(e, p) for e, p in acquired
                  if p["role"] == "org_leader" and p["source"] == new_source]
    # Exactly one revoke (old leader X) + one acquire (new leader Y).
    assert len(rev_leader) == 1 and rev_leader[0][0].actor_id == X.id
    assert len(acq_leader) == 1 and acq_leader[0][0].actor_id == Y.id


# ---------- 9. Non-org identity guard (must PASS on base) ----------

def test_non_org_offender_identity_unchanged_by_org_fix(default_settings):
    """Guard: the offender role is a NON-org, singleton identity
    (role id, source) — not org-keyed. The org-identity fix (adding the source
    entity's org id to org-derived identities) must leave non-org identities
    byte-identical. This re-runs the covered crime-report transition and pins
    the offender identity + event exactly as before. Must PASS on base.
    """
    settings = make_settings(
        default_settings, org=False,
        crime_updates={"enabled": True, "detection_base": 1.0})
    session, world_id = build_world(settings)
    chars = sorted(
        (c for c in session.query(Character).filter_by(world_id=world_id).all()),
        key=lambda c: c.id)
    actor, witness = chars[0], chars[1]
    witness.location_id = actor.location_id
    session.flush()

    log_event(session, world_id, DAY, EventType.CRIME_COMMITED,
              actor_id=actor.id, payload={"type": "theft", "day": 1})
    crime = Crime(
        world_id=world_id, crime_type="theft", actor_character_id=actor.id,
        location_id=actor.location_id, day=1, status="unreported",
        created_at=DAY)
    session.add(crime)
    session.flush()

    _witness_and_report(session, world_id, 1, DAY, crime, actor, settings)
    assert crime.status == "reported"

    acquired = role_events(session, world_id, "ROLE_ACQUIRED")
    assert len(acquired) == 1
    e, p = acquired[0]
    assert e.actor_id == actor.id
    assert p["role"] == "offender"
    assert p["source"] == "зарегистрированное нарушение"
    # The offender identity is a singleton (role id, source) — NOT org-keyed,
    # so the org-identity fix must not change it.
    identity = ("offender", "зарегистрированное нарушение")
    assert identity in roles_snapshot(session, world_id, actor.id)
