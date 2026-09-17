"""M3 Chunk D: acceptance evidence AE1'-AE6' (spec 003).

T15b pins the M2 social-on vectors here (captured at Phase D start);
org-off mode must reproduce them byte-identically (AE5').
"""
import random
import time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.characters.generator import generate_population
from app.config.config import load_config
from app.db.models import (
    OrgLaw,
    Relationship,
    WorldEvent,
    bootstrap,
)
from app.policies.org import enact_initial_laws
from app.simulation.engine import Engine, TickScheduler, WorldClock
from app.simulation.report import build_report
from app.world.seed_world import seed_world
from app.world.social_seed import seed_social

# --- AE5' (ii): M2 social-on vectors, captured at Phase D start (seed 42) ---

M2_7D_VECTOR = {
    "CHARACTER_CREATED": 20,
    "CHARACTER_MOVED": 1094,
    "CONFLICT": 4,
    "ITEM_CONSUMED": 280,
    "ITEM_TRANSFERRED": 70,
    "PURCHASE": 70,
    "RELATIONSHIP_CHANGED": 22,
    "SALARY_PAID": 140,
    "SOCIAL_INTERACTION": 200,
    "SUPPLY_ARRIVED": 7,
    "TASK_COMPLETED": 2564,
}
M2_7D_RELS = [
    -60.0, -60.0, 8.0, 8.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0,
    16.0, 20.0, 20.0, 20.0, 20.0, 20.0, 20.0, 20.0, 20.0, 20.0, 20.0, 20.0,
    20.0, 40.0,
]
M2_30D_VECTOR = {
    "CHARACTER_CREATED": 20,
    "CHARACTER_MOVED": 4711,
    "CONFLICT": 4,
    "ITEM_CONSUMED": 1200,
    "ITEM_TRANSFERRED": 300,
    "PURCHASE": 300,
    "RELATIONSHIP_CHANGED": 43,
    "SALARY_PAID": 600,
    "SOCIAL_INTERACTION": 1080,
    "SUPPLY_ARRIVED": 30,
    "TASK_COMPLETED": 11259,
}
M2_30D_RELS = [
    -60.0, -60.0, 16.0, 18.0, 18.0, 18.0, 18.0, 88.0, 90.0, 90.0, 90.0, 90.0,
    100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0,
    100.0, 100.0, 100.0, 100.0,
]


def run_pipeline_org(seed, days, population, social_enabled, org_enabled,
                     db_path=":memory:"):
    settings = load_config("config/default.yaml")
    settings.social.enabled = social_enabled
    settings.org.enabled = org_enabled
    settings.persistence.db_path = db_path
    settings.world.initial_population = population

    engine = create_engine(f"sqlite:///{db_path}")
    session = Session(engine)

    bootstrap(engine, settings, seed)
    world_id = settings.world.world_id
    seed_world(session, settings, world_id)
    sim_rng = random.Random(seed)
    generate_population(session, settings, sim_rng, world_id, population)
    if social_enabled:
        seed_social(session, settings, world_id, sim_rng)
    if org_enabled:
        enact_initial_laws(session, world_id, settings, timestamp=0)

    clock = WorldClock()
    scheduler = TickScheduler()
    sim_engine = Engine(clock, scheduler)

    start_time = time.time()
    for _ in range(days):
        sim_engine.step(1440, session=session, world_id=world_id,
                        settings=settings)
    wall_time = time.time() - start_time
    session.commit()

    report = build_report(session, world_id, settings, wall_time, seed)
    return report, session, engine, wall_time, world_id


def rels_of(session, world_id):
    return sorted(round(r.affection, 1) for r in
                  session.query(Relationship).filter_by(world_id=world_id).all())


def org_laws_digest(session, world_id):
    return sorted(
        (law.organization_id, law.law_key, law.enacted_day)
        for law in session.query(OrgLaw).filter_by(world_id=world_id).all())


# --- AE5' (ii): M2 regression (T15b) ---

@pytest.mark.slow
def test_ae5_m2_regression_7d():
    report, session, engine, _, world_id = run_pipeline_org(
        42, 7, 20, True, False)
    assert report["events_by_type"] == M2_7D_VECTOR
    assert rels_of(session, world_id) == M2_7D_RELS
    assert "org" not in report
    assert org_laws_digest(session, world_id) == []
    session.close()


@pytest.mark.slow
def test_ae5_m2_regression_30d():
    report, session, engine, _, world_id = run_pipeline_org(
        42, 30, 20, True, False)
    assert report["events_by_type"] == M2_30D_VECTOR
    assert rels_of(session, world_id) == M2_30D_RELS
    assert "org" not in report
    session.close()


# --- AE2': 7-day org-on smoke ---

@pytest.mark.slow
def test_ae2_org_on_7d():
    report, session, engine, _, _ = run_pipeline_org(42, 7, 20, True, True)
    ev = report["events_by_type"]
    assert ev.get("SOCIAL_INTERACTION", 0) >= 14
    assert ev.get("ELECTION", 0) >= 2, "both orgs must hold day-7 elections"
    assert report["invariants_ok"]
    session.close()


# --- AE1': 30-day org-on acceptance run ---

@pytest.mark.slow
def test_ae1_org_on_30d():
    report, session, engine, wall, world_id = run_pipeline_org(
        42, 30, 20, True, True)
    ev = report["events_by_type"]

    assert wall < 180, f"performance guard: {wall:.1f}s"
    assert report["invariants_ok"], report["invariants"]
    assert report["population_alive"] == 20

    assert ev.get("ELECTION", 0) >= 8, "2 orgs x 4 terms"
    assert ev.get("ORG_DUES", 0) == 60, "2 orgs x 30 days"
    assert ev.get("ORG_FEAST", 0) >= 1
    assert ev.get("LAW_ENACTED", 0) >= 1
    assert ev.get("RECONCILIATION", 0) == 2, "one per seeded pair"
    assert ev.get("CONFLICT", 0) >= 2

    # Empirical observables (spec Risks: night_home volume, R2 leader churn)
    org_block = report["org"]
    leaders = {e["id"]: e["leader_id"] for e in org_block["organizations"]}
    elections = org_block["summary"]["elections"]
    print(f"\n[empirical] wall={wall:.1f}s night_home="
          f"{org_block['summary']['violations']} fines_total="
          f"{org_block['summary']['fines_total']} feasts="
          f"{org_block['summary']['feasts']} elections={elections} "
          f"leaders={leaders}")
    # R2 observability: leadership must be observable across the run
    assert elections > 0

    # No feud BELOW the reconciliation floor at the end (exactly -30.0 is
    # a legal resting state: a pair thawed to the floor).
    worst = min(rels_of(session, world_id))
    assert worst >= -30.0, f"relationship at {worst} below thaw floor"
    session.close()


# --- AE4': org-on determinism ---

@pytest.mark.slow
def test_ae4_org_determinism(tmp_path):
    db1 = str(tmp_path / "org_det1.db")
    db2 = str(tmp_path / "org_det2.db")
    r1, s1, _, _, w1 = run_pipeline_org(42, 7, 20, True, True, db1)
    r2, s2, _, _, w2 = run_pipeline_org(42, 7, 20, True, True, db2)

    assert r1["events_by_type"] == r2["events_by_type"]

    ev1 = sorted(
        (e.event_type, e.actor_id, e.target_id, e.game_timestamp, e.payload)
        for e in s1.query(WorldEvent).filter_by(world_id=w1).all())
    ev2 = sorted(
        (e.event_type, e.actor_id, e.target_id, e.game_timestamp, e.payload)
        for e in s2.query(WorldEvent).filter_by(world_id=w2).all())
    assert ev1 == ev2
    assert org_laws_digest(s1, w1) == org_laws_digest(s2, w2)
    assert rels_of(s1, w1) == rels_of(s2, w2)

    # Different seed -> different log.
    r3, s3, _, _, _ = run_pipeline_org(43, 7, 20, True, True,
                                       str(tmp_path / "org_det3.db"))
    assert r3["events_by_type"] != r1["events_by_type"]
    s1.close()
    s2.close()
    s3.close()


# --- AE6': config sensitivity ---

@pytest.mark.slow
def test_ae6_election_interval_sensitivity():
    r_base, s1, _, _, _ = run_pipeline_org(42, 14, 20, True, True)

    # Shorter interval -> strictly more elections over the same horizon.
    settings3 = load_config("config/default.yaml")
    settings3.social.enabled = True
    settings3.org.enabled = True
    settings3.persistence.db_path = ":memory:"
    settings3.world.initial_population = 20
    settings3.org.election_interval_days = 3

    engine3 = create_engine("sqlite:///:memory:")
    s3 = Session(engine3)
    bootstrap(engine3, settings3, 42)
    world3 = settings3.world.world_id
    seed_world(s3, settings3, world3)
    rng3 = random.Random(42)
    generate_population(s3, settings3, rng3, world3, 20)
    seed_social(s3, settings3, world3, rng3)
    enact_initial_laws(s3, world3, settings3, timestamp=0)
    eng3 = Engine(WorldClock(), TickScheduler())
    for _ in range(14):
        eng3.step(1440, session=s3, world_id=world3, settings=settings3)
    s3.commit()
    rep3 = build_report(s3, world3, settings3, 0.0, 42)

    base_elections = r_base["events_by_type"].get("ELECTION", 0)
    fast_elections = rep3["events_by_type"].get("ELECTION", 0)
    assert fast_elections > base_elections, (
        f"interval 3 must elect more often than 7: {fast_elections} vs "
        f"{base_elections}")
    s1.close()
    s3.close()
