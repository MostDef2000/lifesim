"""M3 Chunk B: org policy mechanics (spec R2/R3/R4/R6).

Covers: dues (+aggregate event, empty-org skip), scheduled/succession
elections + tie-break, law enactment on leader change, night_home fines,
no_conflict CONFLICT hook, reconciliation thaw (+no-refreeze), feast,
and the R1 gate (org disabled -> zero mutations).
"""
import json
import random

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import (
    Character,
    CharacterNeeds,
    CharacterTask,
    CharacterTrait,
    Location,
    Organization,
    OrganizationMember,
    OrgLaw,
    OrgLawViolation,
    Relationship,
    RelationshipEvent,
    WorldEvent,
    bootstrap,
)
from app.economy import get_balance, open_account
from app.events.events import EventType
from app.policies.org import enact_initial_laws, run_daily_org_policies

DAY = 1440


def build_world(settings, population=5):
    """Seed a small world with social layer on. Returns (session, world_id)."""
    settings.social.enabled = True
    engine = create_engine("sqlite:///:memory:")
    bootstrap(engine, settings, 42)
    Session = sessionmaker(bind=engine)
    session = Session()
    world_id = settings.world.world_id

    from app.characters.generator import generate_population
    from app.world.seed_world import seed_world
    from app.world.social_seed import seed_social

    seed_world(session, settings, world_id)
    rng = random.Random(42)
    generate_population(session, settings, rng, world_id, population)
    seed_social(session, settings, world_id, rng)
    session.commit()
    return session, world_id


def community(session, world_id):
    return session.query(Organization).filter_by(
        world_id=world_id, type="community").first()


def leader_of(session, org):
    return session.query(OrganizationMember).filter_by(
        organization_id=org.id, role="leader").first()


def org_payloads(session, world_id, etype, org_id=None):
    """(event, payload) pairs of `etype`, optionally filtered by org."""
    out = []
    for e in session.query(WorldEvent).filter_by(
            world_id=world_id, event_type=etype.value).all():
        p = json.loads(e.payload)
        if org_id is None or p.get("organization_id") == org_id:
            out.append((e, p))
    return out


def rel_of(session, world_id, x, y):
    a, b = sorted([x, y])
    return session.query(Relationship).filter_by(
        world_id=world_id, character_a=a, character_b=b).first()


def char_balance(session, world_id, char_id):
    acc = open_account(session, world_id, "character", char_id)
    return get_balance(session, acc.id)


def org_balance(session, world_id, org):
    acc = open_account(session, world_id, "organization", str(org.id))
    return get_balance(session, acc.id)


def set_traits(session, char_id, values):
    for key, value in values.items():
        t = session.query(CharacterTrait).filter_by(
            character_id=char_id, trait_key=key).first()
        if t is not None:
            t.value = value


def socialize_task(session, actor, target, ts):
    task = CharacterTask(
        character_id=actor.id,
        priority=1,
        task_type="SOCIALIZE",
        target_id=target.id,
        status="STARTED",
        source="utility",
        parameters=json.dumps(
            {"target_id": target.id, "affection_at_start": 0.0}),
        created_at=ts,
        started_at=ts,
        ends_at=ts + 30,
    )
    session.add(task)
    session.flush()
    return task


def test_gate_off_zero_mutations(default_settings):
    """R1: org disabled -> the handler is a no-op (byte-identity)."""
    settings = default_settings
    session, world_id = build_world(settings)
    settings.org.enabled = False  # R1 gate: org off (post-seed; seed needs social only)
    org = community(session, world_id)

    events_before = session.query(WorldEvent).filter_by(world_id=world_id).count()
    org_bal_before = org_balance(session, world_id, org)
    chars = session.query(Character).filter_by(world_id=world_id).all()
    bal_before = {c.id: char_balance(session, world_id, c.id) for c in chars}

    run_daily_org_policies(session, world_id, settings, DAY)
    session.commit()

    assert session.query(WorldEvent).filter_by(world_id=world_id).count() == events_before
    assert org_balance(session, world_id, org) == org_bal_before
    for c in chars:
        assert char_balance(session, world_id, c.id) == bal_before[c.id]
    assert session.query(OrgLaw).count() == 0
    assert session.query(OrgLawViolation).count() == 0


def test_dues_flow_and_aggregate_event(default_settings):
    """R3: dues transfer per alive member; aggregate event every day;
    all-broke day still emits the event with paid_members=0."""
    settings = default_settings
    session, world_id = build_world(settings)
    settings.org.enabled = True
    org = community(session, world_id)
    chars = [c for c in session.query(Character).filter_by(world_id=world_id).all()]

    org_bal_before = org_balance(session, world_id, org)
    bal_before = {c.id: char_balance(session, world_id, c.id) for c in chars}

    def expected_dues(char_id):
        # R3: dues per org membership (a member of N orgs pays N times).
        return -sum(
            1 for o in session.query(Organization).filter_by(world_id=world_id)
            for m in session.query(OrganizationMember).filter_by(
                organization_id=o.id, character_id=char_id)
            if session.query(Character).filter_by(id=m.character_id)
            .first().alive
        )

    run_daily_org_policies(session, world_id, settings, DAY)
    session.commit()

    evs = org_payloads(session, world_id, EventType.ORG_DUES, org.id)
    assert len(evs) == 1
    e, p = evs[0]
    assert e.actor_id is None  # R5: aggregate event has no actor
    assert p["amount_per_member"] == 1
    assert p["paid_members"] == 5
    assert p["skipped_members"] == 0
    assert p["total_collected"] == 5
    for c in chars:
        assert char_balance(session, world_id, c.id) \
            == bal_before[c.id] + expected_dues(c.id)
    assert org_balance(session, world_id, org) == org_bal_before + 5

    # Day 2: everyone broke -> silent skips, event still emitted.
    for c in chars:
        acc = open_account(session, world_id, "character", c.id)
        acc.balance = 0
    session.flush()

    run_daily_org_policies(session, world_id, settings, 2 * DAY)
    session.commit()

    evs = org_payloads(session, world_id, EventType.ORG_DUES, org.id)
    assert len(evs) == 2
    _, p = evs[1]
    assert p["paid_members"] == 0
    assert p["skipped_members"] == 5
    assert p["total_collected"] == 0


def test_org_without_alive_members_skipped(default_settings):
    """R2/R3: an org with zero alive members gets no dues, no event,
    no elections."""
    settings = default_settings
    session, world_id = build_world(settings)
    settings.org.enabled = True
    guild = Organization(world_id=world_id, name="Empty Guild", type="guild")
    session.add(guild)
    session.flush()
    dead = session.query(Character).filter_by(world_id=world_id).all()[0]
    dead.alive = False
    session.add(OrganizationMember(
        organization_id=guild.id, character_id=dead.id, role="leader", joined_at=0))
    session.commit()

    run_daily_org_policies(session, world_id, settings, DAY)
    session.commit()

    assert org_payloads(session, world_id, EventType.ORG_DUES, guild.id) == []
    assert org_payloads(session, world_id, EventType.ELECTION, guild.id) == []
    # Community org (4 alive members) still processed.
    comm = community(session, world_id)
    _, p = org_payloads(session, world_id, EventType.ORG_DUES, comm.id)[0]
    assert p["paid_members"] == 4

    # R3: no feast for the empty org on feast day either.
    run_daily_org_policies(session, world_id, settings, 14 * DAY)
    session.commit()
    assert org_payloads(session, world_id, EventType.ORG_FEAST, guild.id) == []
    assert len(org_payloads(
        session, world_id, EventType.ORG_FEAST, comm.id)) == 1


def test_elections_scheduled_tie_and_quiet_days(default_settings):
    """R2: scheduled election on day % 7 == 0; tie -> lowest id; no
    election on non-boundary days."""
    settings = default_settings
    session, world_id = build_world(settings)
    settings.org.enabled = True
    org = community(session, world_id)
    chars = sorted(
        (c for c in session.query(Character).filter_by(world_id=world_id).all()),
        key=lambda c: c.id)
    old_leader_id = leader_of(session, org).character_id

    # Craft a full symmetric tie: every pair exists at 10 -> equal scores.
    for i in range(len(chars)):
        for j in range(i + 1, len(chars)):
            rel = rel_of(session, world_id, chars[i].id, chars[j].id)
            if rel is None:
                session.add(Relationship(
                    world_id=world_id,
                    character_a=min(chars[i].id, chars[j].id),
                    character_b=max(chars[i].id, chars[j].id),
                    affection=10.0, updated_at=0))
            else:
                rel.affection = 10.0
    session.flush()

    x_id, y_id = chars[0].id, chars[1].id
    expected_winner = min(x_id, y_id)

    # Day 5: no election.
    run_daily_org_policies(session, world_id, settings, 5 * DAY)
    session.commit()
    assert org_payloads(session, world_id, EventType.ELECTION, org.id) == []
    assert leader_of(session, org).character_id == old_leader_id

    # Day 7: scheduled election, tie -> lowest id wins.
    run_daily_org_policies(session, world_id, settings, 7 * DAY)
    session.commit()

    evs = org_payloads(session, world_id, EventType.ELECTION, org.id)
    assert len(evs) == 1
    e, p = evs[0]
    assert p["reason"] == "scheduled"
    assert p["previous_leader_id"] == old_leader_id
    assert p["candidates_count"] == 5
    assert p["score"] == 40.0  # 4 voters x 10
    assert e.actor_id == expected_winner
    assert leader_of(session, org).character_id == expected_winner
    if old_leader_id != expected_winner:
        assert leader_of(session, org).role == "leader"


def test_succession_on_leader_death(default_settings):
    """R2: dead leader on a non-election day -> succession election."""
    settings = default_settings
    session, world_id = build_world(settings)
    settings.org.enabled = True
    org = community(session, world_id)
    leader_member = leader_of(session, org)
    dead_leader_id = leader_member.character_id

    leader_char = session.query(Character).filter_by(id=dead_leader_id).first()
    leader_char.alive = False
    leader_char.death_game_timestamp = DAY - 1
    session.commit()

    run_daily_org_policies(session, world_id, settings, 3 * DAY)
    session.commit()

    evs = org_payloads(session, world_id, EventType.ELECTION, org.id)
    assert len(evs) == 1
    e, p = evs[0]
    assert p["reason"] == "succession"
    assert p["previous_leader_id"] == dead_leader_id
    new_leader = leader_of(session, org)
    assert new_leader.character_id != dead_leader_id
    assert e.actor_id == new_leader.character_id
    # New leader must be alive (org_leader_valid invariant R9.1).
    assert session.query(Character).filter_by(
        id=new_leader.character_id).first().alive is True


def test_law_enactment_on_leader_change(default_settings):
    """R4: new leader enacts their dot-rule law; re-election of the same
    leader does not re-enact."""
    settings = default_settings
    session, world_id = build_world(settings)
    settings.org.enabled = True
    org = community(session, world_id)
    chars = sorted(
        (c for c in session.query(Character).filter_by(world_id=world_id).all()),
        key=lambda c: c.id)
    leader_id = leader_of(session, org).character_id

    # Incumbent traits -> night_home wins the dot rule.
    set_traits(session, leader_id, {
        "discipline": 1, "sociability": -1, "risk_tolerance": 1})
    enact_initial_laws(session, world_id, settings, timestamp=0)
    session.commit()
    law = session.query(OrgLaw).filter_by(organization_id=org.id).first()
    assert law.law_key == "night_home"

    # A different member with traits -> no_conflict wins the next term.
    winner = next(c for c in chars if c.id != leader_id)
    set_traits(session, winner.id, {
        "discipline": 1, "sociability": 1, "risk_tolerance": -1})
    for rel in session.query(Relationship).filter_by(world_id=world_id).all():
        rel.affection = 0.0
    for voter in chars:
        if voter.id == winner.id:
            continue
        rel = rel_of(session, world_id, voter.id, winner.id)
        if rel is None:
            rel = Relationship(
                world_id=world_id,
                character_a=min(voter.id, winner.id),
                character_b=max(voter.id, winner.id),
                affection=10.0, updated_at=0)
            session.add(rel)
        else:
            rel.affection = 10.0
    session.flush()

    run_daily_org_policies(session, world_id, settings, 7 * DAY)
    session.commit()

    law = session.query(OrgLaw).filter_by(organization_id=org.id).first()
    assert law.law_key == "no_conflict"
    assert law.enacted_by_character_id == winner.id
    assert law.enacted_day == 7

    evs = org_payloads(session, world_id, EventType.LAW_ENACTED, org.id)
    enacted_pairs = [(e, p) for e, p in evs if p["law_key"] == "no_conflict"]
    assert len(enacted_pairs) == 1
    assert enacted_pairs[0][1]["replaced_law_key"] == "night_home"
    assert enacted_pairs[0][0].actor_id == winner.id  # R5: actor = leader

    # Day 14: same winner re-elected -> same law -> no new LAW_ENACTED.
    run_daily_org_policies(session, world_id, settings, 14 * DAY)
    session.commit()
    evs = org_payloads(session, world_id, EventType.LAW_ENACTED, org.id)
    assert len([p for _, p in evs if p["law_key"] == "no_conflict"]) == 1
    assert session.query(OrgLaw).filter_by(organization_id=org.id).count() == 1


def test_night_home_fines(default_settings):
    """R4: away-from-home member is fined once per day; home members are
    clean; same-day re-run is idempotent (no double fine)."""
    settings = default_settings
    session, world_id = build_world(settings)
    settings.org.enabled = True
    org = community(session, world_id)
    leader_id = leader_of(session, org).character_id
    session.add(OrgLaw(
        world_id=world_id, organization_id=org.id, law_key="night_home",
        enacted_by_character_id=leader_id, enacted_day=0, enacted_at=0))
    session.commit()

    loc_ids = [loc.id for loc in session.query(Location).filter_by(
        world_id=world_id).all()]
    assert len(loc_ids) >= 2

    chars = session.query(Character).filter_by(world_id=world_id).all()
    away = next(c for c in chars if c.id != leader_id)
    assert away.home_location_id is not None
    other_locs = [lid for lid in loc_ids if lid != away.home_location_id]
    away.location_id = other_locs[0]
    session.commit()

    org_bal_before = org_balance(session, world_id, org)
    away_bal_before = char_balance(session, world_id, away.id)

    run_daily_org_policies(session, world_id, settings, DAY)
    session.commit()

    evs = org_payloads(session, world_id, EventType.LAW_VIOLATION, org.id)
    assert len(evs) == 1
    e, p = evs[0]
    assert e.target_id is None  # R5
    assert p["law_key"] == "night_home"
    assert p["fine"] == 2
    assert p["fine_paid"] == 2
    assert p["source_event_type"] is None

    rows = session.query(OrgLawViolation).filter_by(
        world_id=world_id, organization_id=org.id).all()
    assert len(rows) == 1
    assert rows[0].character_id == away.id
    assert rows[0].event_id is None
    assert rows[0].game_day == 1

    # org: +5 dues +2 fine; away: -1 dues -2 fine.
    assert org_balance(session, world_id, org) == org_bal_before + 7
    assert char_balance(session, world_id, away.id) == away_bal_before - 3

    # Same-day re-run: pre-query guard -> no second fine, no second row.
    run_daily_org_policies(session, world_id, settings, DAY)
    session.commit()
    assert session.query(OrgLawViolation).filter_by(
        world_id=world_id, organization_id=org.id).count() == 1
    assert len(org_payloads(session, world_id, EventType.LAW_VIOLATION, org.id)) == 1


def test_no_conflict_hook(default_settings):
    """R4: with no_conflict active, both CONFLICT participants are fined
    once per day with an event link; other laws / disabled org -> nothing."""
    from app.actions.lifecycle import complete_task

    settings = default_settings
    session, world_id = build_world(settings)
    settings.org.enabled = True
    org = community(session, world_id)
    leader_id = leader_of(session, org).character_id
    session.add(OrgLaw(
        world_id=world_id, organization_id=org.id, law_key="no_conflict",
        enacted_by_character_id=leader_id, enacted_day=0, enacted_at=0))
    session.commit()

    chars = sorted(
        (c for c in session.query(Character).filter_by(world_id=world_id).all()),
        key=lambda c: c.id)
    actor, target = chars[1], chars[2]
    target.location_id = actor.location_id
    rel = rel_of(session, world_id, actor.id, target.id)
    if rel is None:
        rel = Relationship(
            world_id=world_id, character_a=min(actor.id, target.id),
            character_b=max(actor.id, target.id),
            affection=-50.0, updated_at=0)
        session.add(rel)
    else:
        rel.affection = -50.0
    session.commit()

    org_bal_before = org_balance(session, world_id, org)

    task = socialize_task(session, actor, target, 0)
    complete_task(session, world_id, actor, task, 30, settings)
    session.commit()

    conflict_events = session.query(WorldEvent).filter_by(
        world_id=world_id, event_type=EventType.CONFLICT.value).all()
    assert len(conflict_events) == 1
    conflict_id = conflict_events[0].id

    evs = org_payloads(session, world_id, EventType.LAW_VIOLATION, org.id)
    assert len(evs) == 2
    for e, p in evs:
        assert p["law_key"] == "no_conflict"
        assert p["fine"] == 5
        assert p["fine_paid"] == 5
        assert p["source_event_type"] == "CONFLICT"
    assert {e.actor_id for e, _ in evs} == {actor.id, target.id}

    rows = session.query(OrgLawViolation).filter_by(
        world_id=world_id, organization_id=org.id, law_key="no_conflict").all()
    assert len(rows) == 2
    assert {r.character_id for r in rows} == {actor.id, target.id}
    assert {r.event_id for r in rows} == {conflict_id}
    assert org_balance(session, world_id, org) == org_bal_before + 10

    # Second conflict, same participants, same day -> once-per-day guard.
    task2 = socialize_task(session, actor, target, 40)
    complete_task(session, world_id, actor, task2, 70, settings)
    session.commit()
    assert session.query(WorldEvent).filter_by(
        world_id=world_id, event_type=EventType.CONFLICT.value).count() == 2
    assert len(org_payloads(session, world_id, EventType.LAW_VIOLATION, org.id)) == 2
    assert session.query(OrgLawViolation).filter_by(
        world_id=world_id, organization_id=org.id).count() == 2

    # Active law night_home -> conflicts produce no fines.
    session2, world_id2 = build_world(settings)
    org2 = community(session2, world_id2)
    leader2 = leader_of(session2, org2).character_id
    session2.add(OrgLaw(
        world_id=world_id2, organization_id=org2.id, law_key="night_home",
        enacted_by_character_id=leader2, enacted_day=0, enacted_at=0))
    c2 = sorted(session2.query(Character).filter_by(world_id=world_id2).all(),
                key=lambda c: c.id)
    a2, t2 = c2[1], c2[2]
    t2.location_id = a2.location_id
    r2 = rel_of(session2, world_id2, a2.id, t2.id)
    if r2 is None:
        session2.add(Relationship(
            world_id=world_id2, character_a=min(a2.id, t2.id),
            character_b=max(a2.id, t2.id), affection=-50.0, updated_at=0))
    else:
        r2.affection = -50.0
    session2.commit()
    task3 = socialize_task(session2, a2, t2, 0)
    complete_task(session2, world_id2, a2, task3, 30, settings)
    session2.commit()
    assert session2.query(WorldEvent).filter_by(
        world_id=world_id2, event_type=EventType.CONFLICT.value).count() == 1
    assert org_payloads(session2, world_id2, EventType.LAW_VIOLATION, org2.id) == []

    # Org disabled -> zero law writes (byte-identity of the M2 conflict path).
    settings.org.enabled = False
    session3, world_id3 = build_world(settings)
    org3 = community(session3, world_id3)
    c3 = sorted(session3.query(Character).filter_by(world_id=world_id3).all(),
                key=lambda c: c.id)
    a3, t3 = c3[1], c3[2]
    t3.location_id = a3.location_id
    r3 = rel_of(session3, world_id3, a3.id, t3.id)
    if r3 is None:
        session3.add(Relationship(
            world_id=world_id3, character_a=min(a3.id, t3.id),
            character_b=max(a3.id, t3.id), affection=-50.0, updated_at=0))
    else:
        r3.affection = -50.0
    session3.commit()
    task4 = socialize_task(session3, a3, t3, 0)
    complete_task(session3, world_id3, a3, task4, 30, settings)
    session3.commit()
    # The M2 conflict path stays untouched: zero law events, zero rows.
    assert org_payloads(session3, world_id3, EventType.LAW_VIOLATION, org3.id) == []
    assert session3.query(OrgLawViolation).count() == 0


def test_reconciliation_thaw_and_leader_guard(default_settings):
    """R6: first eligible feud pair thaws to target_affection with the full
    event set; leader-in-pair skips the day."""
    settings = default_settings
    session, world_id = build_world(settings)
    settings.org.enabled = True
    org = community(session, world_id)
    leader_id = leader_of(session, org).character_id
    chars = sorted(
        (c for c in session.query(Character).filter_by(world_id=world_id).all()
         if c.id != leader_id),
        key=lambda c: c.id)
    a, b = chars[0], chars[1]

    rel = rel_of(session, world_id, a.id, b.id)
    if rel is None:
        rel = Relationship(
            world_id=world_id, character_a=min(a.id, b.id),
            character_b=max(a.id, b.id), affection=-60.0, updated_at=0)
        session.add(rel)
    else:
        rel.affection = -60.0
    session.commit()

    run_daily_org_policies(session, world_id, settings, DAY)
    session.commit()

    session.refresh(rel)
    assert rel.affection == pytest.approx(-30.0)

    recs = org_payloads(session, world_id, EventType.RECONCILIATION, org.id)
    assert len(recs) == 1
    e, p = recs[0]
    assert e.target_id is None  # R5: pair lives in the payload
    assert sorted(p["pair"]) == sorted([a.id, b.id])
    assert p["mediator_id"] not in (a.id, b.id)
    assert p["affection_before"] == pytest.approx(-60.0)
    assert p["affection_after"] == pytest.approx(-30.0)

    changed = [e for e, _ in org_payloads(
        session, world_id, EventType.RELATIONSHIP_CHANGED, None)
        if e.actor_id in (a.id, b.id) and e.target_id in (a.id, b.id)]
    assert len(changed) == 1
    cp = json.loads(changed[0].payload)
    assert cp["band"] == "stranger"

    rows = session.query(RelationshipEvent).filter_by(
        world_id=world_id, character_a=min(a.id, b.id),
        character_b=max(a.id, b.id)).all()
    assert len(rows) == 1
    assert rows[0].event_id == changed[0].id
    assert rows[0].impact == pytest.approx(30.0)

    # No-refreeze (R6): 10 socialize completions keep affection >= -30.
    from app.actions.lifecycle import complete_task
    conflicts_before = session.query(WorldEvent).filter_by(
        world_id=world_id, event_type=EventType.CONFLICT.value).count()
    ts = 2 * DAY
    for _ in range(10):
        b.location_id = a.location_id
        session.commit()
        task = socialize_task(session, a, b, ts)
        complete_task(session, world_id, a, task, ts + 30, settings)
        session.commit()
        session.refresh(rel)
        assert rel.affection >= -30.0
        ts += 60
    assert rel.affection == pytest.approx(-10.0)
    assert session.query(WorldEvent).filter_by(
        world_id=world_id, event_type=EventType.CONFLICT.value).count() \
        == conflicts_before

    # Leader in the pair -> skip the day.
    session2, world_id2 = build_world(settings)
    org2 = community(session2, world_id2)
    l2 = leader_of(session2, org2)
    l2_char = session2.query(Character).filter_by(id=l2.character_id).first()
    others = sorted(
        (c for c in session2.query(Character).filter_by(world_id=world_id2).all()
         if c.id != l2.character_id),
        key=lambda c: c.id)
    x, y = others[0], others[1]
    rel2 = rel_of(session2, world_id2, x.id, y.id)
    if rel2 is None:
        rel2 = Relationship(
            world_id=world_id2, character_a=min(x.id, y.id),
            character_b=max(x.id, y.id), affection=-70.0, updated_at=0)
        session2.add(rel2)
    else:
        rel2.affection = -70.0
    # Make the LEADER one of the frozen pair by pairing leader with x.
    rel_lead = rel_of(session2, world_id2, l2_char.id, x.id)
    if rel_lead is None:
        rel_lead = Relationship(
            world_id=world_id2, character_a=min(l2_char.id, x.id),
            character_b=max(l2_char.id, x.id), affection=-65.0, updated_at=0)
        session2.add(rel_lead)
    else:
        rel_lead.affection = -65.0
    session2.commit()

    run_daily_org_policies(session2, world_id2, settings, DAY)
    session2.commit()

    # The leader pair (l2_char, x) is the FIRST eligible pair in (a, b)
    # order (npc ids ascending) -> the day is skipped entirely.
    recs2 = org_payloads(session2, world_id2, EventType.RECONCILIATION, org2.id)
    assert recs2 == []
    session2.refresh(rel_lead)
    assert rel_lead.affection == pytest.approx(-65.0)


def test_feast(default_settings):
    """R3: feast on day % 14 == 0 burns cost, boosts social (clamp 100),
    emits the event; broke treasury skips silently."""
    settings = default_settings
    session, world_id = build_world(settings)
    settings.org.enabled = True
    org = community(session, world_id)
    chars = session.query(Character).filter_by(world_id=world_id).all()

    needs = session.query(CharacterNeeds).filter_by(
        character_id=chars[0].id).first()
    needs.social = 95.0
    session.query(CharacterNeeds).filter_by(
        character_id=chars[1].id).first().social = 30.0
    session.commit()

    org_bal_before = org_balance(session, world_id, org)
    bal_before = {c.id: char_balance(session, world_id, c.id) for c in chars}
    social_before = {
        c.id: session.query(CharacterNeeds).filter_by(
            character_id=c.id).first().social
        for c in chars
    }

    run_daily_org_policies(session, world_id, settings, 14 * DAY)
    session.commit()

    evs = org_payloads(session, world_id, EventType.ORG_FEAST, org.id)
    assert len(evs) == 1
    _, p = evs[0]
    assert p["cost"] == 30
    assert sorted(p["boosted_members"]) == sorted(c.id for c in chars)

    # Dues +5 then feast -30.
    assert org_balance(session, world_id, org) == org_bal_before + 5 - 30
    for c in chars:
        dues = -sum(
            1 for o in session.query(Organization).filter_by(world_id=world_id)
            for m in session.query(OrganizationMember).filter_by(
                organization_id=o.id, character_id=c.id))
        assert char_balance(session, world_id, c.id) == bal_before[c.id] + dues

    needs = session.query(CharacterNeeds).filter_by(
        character_id=chars[0].id).first()
    assert needs.social == 100.0  # 95 + 20 clamped
    for c in chars[1:]:
        n = session.query(CharacterNeeds).filter_by(character_id=c.id).first()
        assert n.social == pytest.approx(
            min(100.0, social_before[c.id] + 20.0))
    # The mid-value member proves the boost actually applied (+20).
    mid = session.query(CharacterNeeds).filter_by(
        character_id=chars[1].id).first()
    assert mid.social == pytest.approx(50.0)

    # Broke treasury: dues bring +5, still < 30 -> no feast, no event.
    session2, world_id2 = build_world(settings)
    org2 = community(session2, world_id2)
    acc = open_account(session2, world_id2, "organization", str(org2.id))
    acc.balance = 0
    session2.commit()
    run_daily_org_policies(session2, world_id2, settings, 14 * DAY)
    session2.commit()
    assert org_payloads(session2, world_id2, EventType.ORG_FEAST, org2.id) == []


def test_no_conflict_fine_skipped_when_broke(default_settings):
    """R4: unpayable fine -> silent skip, fine_paid=0, row + event still
    recorded (violation happened regardless of ability to pay)."""
    from app.actions.lifecycle import complete_task

    settings = default_settings
    session, world_id = build_world(settings)
    settings.org.enabled = True
    org = community(session, world_id)
    leader_id = leader_of(session, org).character_id
    session.add(OrgLaw(
        world_id=world_id, organization_id=org.id, law_key="no_conflict",
        enacted_by_character_id=leader_id, enacted_day=0, enacted_at=0))
    session.commit()

    chars = sorted(
        (c for c in session.query(Character).filter_by(world_id=world_id).all()),
        key=lambda c: c.id)
    actor, target = chars[1], chars[2]
    target.location_id = actor.location_id
    # Broke actor.
    acc = open_account(session, world_id, "character", actor.id)
    acc.balance = 0
    rel = rel_of(session, world_id, actor.id, target.id)
    if rel is None:
        rel = Relationship(
            world_id=world_id, character_a=min(actor.id, target.id),
            character_b=max(actor.id, target.id),
            affection=-50.0, updated_at=0)
        session.add(rel)
    else:
        rel.affection = -50.0
    session.commit()

    task = socialize_task(session, actor, target, 0)
    complete_task(session, world_id, actor, task, 30, settings)
    session.commit()

    evs = org_payloads(session, world_id, EventType.LAW_VIOLATION, org.id)
    by_actor = {e.actor_id: p for e, p in evs}
    assert by_actor[actor.id]["fine_paid"] == 0  # silent skip
    assert by_actor[target.id]["fine_paid"] == 5

    rows = {r.character_id: r for r in session.query(OrgLawViolation).filter_by(
        world_id=world_id, organization_id=org.id).all()}
    assert rows[actor.id].fine_paid == 0
    assert rows[target.id].fine_paid == 5
    # Treasury only received the payable fine.
    assert org_balance(session, world_id, org) == 2000 + 5


def test_night_home_null_home_skipped(default_settings):
    """R4: NULL home_location_id -> the character is not a violator."""
    settings = default_settings
    session, world_id = build_world(settings)
    settings.org.enabled = True
    org = community(session, world_id)
    leader_id = leader_of(session, org).character_id
    session.add(OrgLaw(
        world_id=world_id, organization_id=org.id, law_key="night_home",
        enacted_by_character_id=leader_id, enacted_day=0, enacted_at=0))

    chars = session.query(Character).filter_by(world_id=world_id).all()
    homeless = next(c for c in chars if c.id != leader_id)
    loc_ids = [loc.id for loc in session.query(Location).filter_by(
        world_id=world_id).all()]
    other = next(lid for lid in loc_ids if lid != homeless.home_location_id)
    homeless.home_location_id = None
    homeless.location_id = other  # away from everywhere, but no home on record
    session.commit()

    run_daily_org_policies(session, world_id, settings, DAY)
    session.commit()

    assert org_payloads(session, world_id, EventType.LAW_VIOLATION, org.id) == []
    assert session.query(OrgLawViolation).filter_by(
        world_id=world_id, organization_id=org.id).count() == 0


def test_reconciliation_after_same_day_succession(default_settings):
    """R3 fixed order + R6: dead leader -> step 2 elects a successor in the
    SAME daily pass; step 5 then reconciles under the NEW leader (leader
    alive and outside the pair)."""
    settings = default_settings
    session, world_id = build_world(settings)
    settings.org.enabled = True
    org = community(session, world_id)
    leader_member = leader_of(session, org)
    dead_leader_id = leader_member.character_id
    leader_char = session.query(Character).filter_by(
        id=dead_leader_id).first()
    leader_char.alive = False
    leader_char.death_game_timestamp = DAY - 1

    chars = sorted(
        (c for c in session.query(Character).filter_by(world_id=world_id).all()
         if c.id != dead_leader_id),
        key=lambda c: c.id)
    a, b = chars[0], chars[1]
    winner = chars[-1]
    assert winner.id not in (a.id, b.id)

    rel = rel_of(session, world_id, a.id, b.id)
    if rel is None:
        rel = Relationship(
            world_id=world_id, character_a=min(a.id, b.id),
            character_b=max(a.id, b.id), affection=-60.0, updated_at=0)
        session.add(rel)
    else:
        rel.affection = -60.0

    # Make the successor deterministic: alive voters boost the winner.
    for voter in chars:
        if voter.id == winner.id:
            continue
        r = rel_of(session, world_id, voter.id, winner.id)
        if r is None:
            session.add(Relationship(
                world_id=world_id,
                character_a=min(voter.id, winner.id),
                character_b=max(voter.id, winner.id),
                affection=10.0, updated_at=0))
        else:
            r.affection = 10.0
    session.commit()

    run_daily_org_policies(session, world_id, settings, DAY)
    session.commit()

    # Step 2: succession installed the boosted winner.
    evs = org_payloads(session, world_id, EventType.ELECTION, org.id)
    assert len(evs) == 1
    assert evs[0][1]["reason"] == "succession"
    assert leader_of(session, org).character_id == winner.id

    # Step 5: reconciliation ran under the NEW leader.
    session.refresh(rel)
    assert rel.affection == pytest.approx(-30.0)
    recs = org_payloads(session, world_id, EventType.RECONCILIATION, org.id)
    assert len(recs) == 1
    assert sorted(recs[0][1]["pair"]) == sorted([a.id, b.id])
    assert recs[0][1]["mediator_id"] not in (a.id, b.id)
