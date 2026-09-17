"""M3 Chunk C: invariants (spec R9.1-R9.6) and the org report block (R10).

Healthy-world passes plus one induced violation per invariant, and the
R10 presence/absence contract for the report's "org" key.
"""
import json
import random

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import (
    Account,
    Character,
    Organization,
    OrganizationMember,
    OrgLaw,
    OrgLawViolation,
    Relationship,
    WorldEvent,
    bootstrap,
)
from app.economy import get_balance, open_account
from app.events.events import EventType
from app.policies.org import enact_initial_laws, run_daily_org_policies
from app.simulation.invariants import run_invariant_checks
from app.simulation.report import build_report

DAY = 1440


def build_world(settings, population=5):
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


def result_by_name(results, name):
    return next(r for r in results if r["name"] == name)


def test_invariants_healthy_world(default_settings):
    """All six org invariants pass on a healthy org-on world."""
    settings = default_settings
    session, world_id = build_world(settings)
    settings.org.enabled = True
    enact_initial_laws(session, world_id, settings, timestamp=0)
    run_daily_org_policies(session, world_id, settings, DAY)
    session.commit()

    results = run_invariant_checks(session, world_id, settings)
    for name in ["org_leader_valid", "law_consistency",
                 "law_violation_idempotency", "org_treasury_conservation",
                 "reconciliation_link", "election_consistency"]:
        r = result_by_name(results, name)
        assert r["ok"], f"{name}: {r['details']}"


def test_org_leader_valid_detects_violations(default_settings):
    settings = default_settings
    session, world_id = build_world(settings)
    settings.org.enabled = True
    org = community(session, world_id)
    second = session.query(OrganizationMember).filter_by(
        organization_id=org.id, character_id="npc_0002").first()
    second.role = "leader"  # now two leaders -> R9.1 violation
    session.commit()

    r = result_by_name(
        run_invariant_checks(session, world_id, settings), "org_leader_valid")
    assert r["ok"] is False


def test_law_consistency_detects_violations(default_settings):
    settings = default_settings
    session, world_id = build_world(settings)
    settings.org.enabled = True
    org = community(session, world_id)
    leader_id = leader_of(session, org).character_id
    session.add(OrgLaw(
        world_id=world_id, organization_id=org.id, law_key="rogue_law",
        enacted_by_character_id=leader_id, enacted_day=0, enacted_at=0))
    session.commit()

    r = result_by_name(
        run_invariant_checks(session, world_id, settings), "law_consistency")
    assert r["ok"] is False
    assert "not in catalog" in r["details"]


def test_violation_idempotency_detects_drift(default_settings):
    """The (org, law, char, day) duplicate branch is unreachable through
    normal inserts (DB UNIQUE, R8) — verify the insertable branches:
    a LAW_VIOLATION event without a row, and a fine_paid/LAW_FINE mismatch."""
    settings = default_settings
    session, world_id = build_world(settings)
    settings.org.enabled = True
    org = community(session, world_id)

    # Row with a fine but no LAW_FINE ledger entry -> sum mismatch.
    session.add(OrgLawViolation(
        world_id=world_id, organization_id=org.id, law_key="night_home",
        character_id="npc_0002", game_day=1, game_timestamp=DAY,
        fine_paid=2, event_id=None))
    # Event without a matching row.
    session.add(WorldEvent(
        world_id=world_id, game_timestamp=2 * DAY,
        event_type=EventType.LAW_VIOLATION.value, actor_id="npc_0003",
        payload=json.dumps({
            "organization_id": org.id, "law_key": "night_home",
            "fine": 2, "fine_paid": 2, "source_event_type": None})))
    session.commit()

    r = result_by_name(
        run_invariant_checks(session, world_id, settings),
        "law_violation_idempotency")
    assert r["ok"] is False
    assert "LAW_FINE" in r["details"] and "without row" in r["details"]


def test_treasury_conservation_detects_tampering(default_settings):
    settings = default_settings
    session, world_id = build_world(settings)
    settings.org.enabled = True
    org = community(session, world_id)
    acc = session.query(Account).filter_by(
        world_id=world_id, owner_type="organization",
        owner_id=str(org.id)).first()
    acc.balance += 100  # money appeared outside the ledger
    session.commit()

    r = result_by_name(
        run_invariant_checks(session, world_id, settings),
        "org_treasury_conservation")
    assert r["ok"] is False
    assert "balance" in r["details"]


def test_reconciliation_link_detects_drift(default_settings):
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
    rel = Relationship(
        world_id=world_id, character_a=min(a.id, b.id),
        character_b=max(a.id, b.id), affection=-60.0, updated_at=0)
    session.add(rel)
    session.commit()

    run_daily_org_policies(session, world_id, settings, DAY)
    session.commit()
    session.refresh(rel)
    assert rel.affection == pytest.approx(-30.0)

    # Simulate drift after the thaw.
    rel.affection = -45.0
    session.commit()

    r = result_by_name(
        run_invariant_checks(session, world_id, settings),
        "reconciliation_link")
    assert r["ok"] is False
    assert "affection" in r["details"]


def test_election_consistency_detects_stale_leader(default_settings):
    settings = default_settings
    session, world_id = build_world(settings)
    settings.org.enabled = True
    org = community(session, world_id)

    # Force a scheduled election on day 7 (get-or-create every pair at 10).
    chars = sorted(
        (c for c in session.query(Character).filter_by(world_id=world_id).all()),
        key=lambda c: c.id)
    for i in range(len(chars)):
        for j in range(i + 1, len(chars)):
            x, y = sorted([chars[i].id, chars[j].id])
            rel = session.query(Relationship).filter_by(
                world_id=world_id, character_a=x, character_b=y).first()
            if rel is None:
                session.add(Relationship(
                    world_id=world_id, character_a=x, character_b=y,
                    affection=10.0, updated_at=0))
            else:
                rel.affection = 10.0
    session.commit()

    run_daily_org_policies(session, world_id, settings, 7 * DAY)
    session.commit()
    assert len(org_payloads_safe(session, world_id)) >= 1

    # Simulate a stale leader row (winner ≠ leader) via a role swap.
    leader_row = leader_of(session, org)
    other_id = next(c.id for c in chars if c.id != leader_row.character_id)
    other_row = session.query(OrganizationMember).filter_by(
        organization_id=org.id, character_id=other_id).first()
    leader_row.role = "member"
    other_row.role = "leader"
    session.commit()

    r = result_by_name(
        run_invariant_checks(session, world_id, settings),
        "election_consistency")
    assert r["ok"] is False


def org_payloads_safe(session, world_id):
    return session.query(WorldEvent).filter_by(
        world_id=world_id, event_type=EventType.ELECTION.value).all()


def test_report_org_block_present_when_enabled(default_settings):
    settings = default_settings
    session, world_id = build_world(settings)
    settings.org.enabled = True
    org = community(session, world_id)
    enact_initial_laws(session, world_id, settings, timestamp=0)
    run_daily_org_policies(session, world_id, settings, DAY)
    session.commit()

    report = build_report(session, world_id, settings, 1.0, 42)
    assert "org" in report
    block = report["org"]
    assert block["organizations"], "expected at least one org entry"
    entry = next(e for e in block["organizations"] if e["id"] == org.id)
    assert set(entry.keys()) == {
        "name", "id", "leader_id", "leader_since_day", "member_count",
        "treasury_balance", "active_law", "dues_collected_total", "feasts",
        "elections"}
    assert entry["active_law"] is not None
    assert entry["dues_collected_total"] == 5
    acc = open_account(session, world_id, "organization", str(org.id))
    assert entry["treasury_balance"] == get_balance(session, acc.id)
    assert set(block["summary"].keys()) == {
        "elections", "violations", "fines_total", "fines_unpaid", "feasts",
        "reconciliations"}
    assert block["summary"]["violations"] == 0


def test_report_org_block_absent_when_disabled(default_settings):
    """AE5'(iii): reports of the disabled mode have no "org" key."""
    settings = default_settings
    session, world_id = build_world(settings)
    settings.org.enabled = False

    report = build_report(session, world_id, settings, 1.0, 42)
    assert "org" not in report
    assert "social" in report  # social block still present (M2 behavior)
