"""M4 Chunk D: acceptance evidence AE1''-AE6'' (spec 004-llm).

- AE1'': 30d llm-on(stub) run — invariants, memories, wall guard.
- AE2'': 7d llm-on — memory capture around conflicts + dialogue session.
- AE4'': determinism of llm-on(stub) runs.
- AE5'' (keystone): llm-off byte-identity with M1/M2/M3 pins; tables empty.
- AE6'': config sensitivity (budgets).
"""
import json
import random
import time

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.ai.worker import process_dialogue
from app.characters.generator import generate_population
from app.config.config import load_config
from app.db.models import (
    AiRequest,
    Character,
    DialogueTurn,
    Memory,
    WorldEvent,
    bootstrap,
)
from app.policies.org import enact_initial_laws
from app.simulation.engine import Engine, TickScheduler, WorldClock
from app.simulation.report import build_report
from app.world.seed_world import seed_world
from app.world.social_seed import seed_social
from tests.m3.test_chunk_d import M2_7D_RELS, M2_7D_VECTOR, M2_30D_RELS, M2_30D_VECTOR

DAY = 1440


def run_pipeline(seed, days, population, social, org, llm, db_path=":memory:"):
    settings = load_config("config/default.yaml")
    settings.social.enabled = social
    settings.org.enabled = org
    settings.llm = settings.llm.model_copy(update={"enabled": llm})
    settings.persistence.db_path = db_path
    settings.world.initial_population = population

    engine = create_engine(f"sqlite:///{db_path}")
    session = Session(engine)
    bootstrap(engine, settings, seed)
    world_id = settings.world.world_id
    seed_world(session, settings, world_id)
    sim_rng = random.Random(seed)
    generate_population(session, settings, sim_rng, world_id, population)
    if social:
        seed_social(session, settings, world_id, sim_rng)
    if org:
        enact_initial_laws(session, world_id, settings, timestamp=0)

    sim_engine = Engine(WorldClock(), TickScheduler())
    start = time.time()
    for _ in range(days):
        sim_engine.step(DAY, session=session, world_id=world_id,
                        settings=settings)
    wall = time.time() - start
    session.commit()
    report = build_report(session, world_id, settings, wall, seed)
    return report, session, engine, wall, world_id, settings


def rels_of(session, world_id):
    from app.db.models import Relationship
    return sorted(round(r.affection, 1) for r in
                  session.query(Relationship).filter_by(
                      world_id=world_id).all())


# --- AE5'' (keystone): llm-off byte-identity --------------------------------

def test_ae5_llm_off_m2_regression_7d():
    report, session, engine, _, wid, _ = run_pipeline(
        42, 7, 20, True, False, False)
    assert report["events_by_type"] == M2_7D_VECTOR
    assert rels_of(session, wid) == M2_7D_RELS
    assert "ai" not in report
    assert session.query(AiRequest).count() == 0
    assert session.query(Memory).count() == 0
    assert session.query(DialogueTurn).count() == 0
    session.close()


def test_ae5_llm_off_m2_regression_30d():
    report, session, engine, _, wid, _ = run_pipeline(
        42, 30, 20, True, False, False)
    assert report["events_by_type"] == M2_30D_VECTOR
    assert rels_of(session, wid) == M2_30D_RELS
    assert "ai" not in report
    assert session.query(AiRequest).count() == 0
    session.close()


def test_ae5_llm_off_m3_org_on_7d():
    """The M3 org-on profile must be unchanged by the M4 code: the decide
    hook and AI phase stay fully behind the llm gate."""
    report, session, engine, _, wid, _ = run_pipeline(
        42, 7, 20, True, True, False)
    ev = report["events_by_type"]
    assert ev.get("ELECTION", 0) >= 2
    assert ev.get("ORG_DUES", 0) == 14  # 2 orgs x 7 days
    assert ev.get("AI_DECISION", 0) == 0
    assert ev.get("MEMORY_CREATED", 0) == 0
    assert "ai" not in report
    assert session.query(AiRequest).count() == 0
    assert report["invariants_ok"]
    session.close()


# --- AE1'': 30d llm-on(stub) acceptance run ----------------------------------

def test_ae1_llm_on_30d():
    report, session, engine, wall, wid, _ = run_pipeline(
        42, 30, 20, True, True, True)
    ev = report["events_by_type"]

    assert wall < 180, f"performance guard: {wall:.1f}s"
    assert report["invariants_ok"], [
        r for r in report["invariant_results"] if not r["ok"]]
    assert report["population_alive"] == 20

    assert ev.get("MEMORY_CREATED", 0) >= 50, ev.get("MEMORY_CREATED")
    assert ev.get("MEMORY_CONSOLIDATED", 0) >= 1
    assert ev.get("CONFLICT", 0) >= 2
    print(f"\n[empirical] wall={wall:.1f}s MEMORY_CREATED="
          f"{ev.get('MEMORY_CREATED')} MEMORY_CONSOLIDATED="
          f"{ev.get('MEMORY_CONSOLIDATED')} AI_DECISION="
          f"{ev.get('AI_DECISION')} ai_block={report['ai']}")

    ai = report["ai"]
    by_status = ai["requests_by_status"]
    # No hung requests: everything terminal.
    assert by_status["pending"] == 0
    assert ai["memories"]["total"] > 0

    # Decisions recorded, and the M4 catalog is socialize_with.
    decided = session.query(WorldEvent).filter_by(
        world_id=wid, event_type="AI_DECISION").all()
    for d in decided:
        payload = json.loads(d.payload)
        assert payload["decision"] == "socialize_with"
        assert 0.0 <= payload["confidence"] <= 1.0
    session.close()


# --- AE2'': 7d memory + dialogue ---------------------------------------------

def test_ae2_llm_on_7d_memories_and_dialogue():
    report, session, engine, _, wid, pipe_settings = run_pipeline(
        42, 7, 20, True, True, True)
    ev = report["events_by_type"]
    assert ev.get("MEMORY_CREATED", 0) >= 2
    assert report["invariants_ok"]

    # Conflict participants remember the conflict.
    conflicts = session.query(WorldEvent).filter_by(
        world_id=wid, event_type="CONFLICT").all()
    assert conflicts, "seeded conflicts must exist"
    mem_types = {m.memory_type for m in
                 session.query(Memory).filter_by(world_id=wid).all()}
    # Raw rows may already be consolidated by the daily cycle (§57).
    assert "CONFLICT" in mem_types or "consolidated_CONFLICT" in mem_types

    # A dialogue session works end-to-end against the live world.
    a = session.query(Character).filter_by(world_id=wid, id="npc_0001").first()
    reply = process_dialogue(
        session, wid, pipe_settings, a.id,
        [{"role": "user", "content": "Как дела?"}], "ae2-sess",
        7 * DAY + 5)
    assert reply and reply.startswith("[stub]")
    turns = session.query(DialogueTurn).filter_by(
        world_id=wid, session_id="ae2-sess").all()
    assert len(turns) == 2
    session.close()


# --- AE4'': determinism -------------------------------------------------------

def test_ae4_llm_on_stub_determinism(tmp_path):
    db1, db2 = str(tmp_path / "d1.db"), str(tmp_path / "d2.db")
    r1, s1, _, _, w1, _ = run_pipeline(42, 7, 20, True, True, True, db1)
    r2, s2, _, _, w2, _ = run_pipeline(42, 7, 20, True, True, True, db2)

    assert r1["events_by_type"] == r2["events_by_type"]

    ev1 = sorted(
        (e.event_type, e.actor_id, e.target_id, e.game_timestamp, e.payload)
        for e in s1.query(WorldEvent).filter_by(world_id=w1).all())
    ev2 = sorted(
        (e.event_type, e.actor_id, e.target_id, e.game_timestamp, e.payload)
        for e in s2.query(WorldEvent).filter_by(world_id=w2).all())
    assert ev1 == ev2

    mem1 = sorted(
        (m.character_id, m.memory_type, m.importance, m.summary)
        for m in s1.query(Memory).filter_by(world_id=w1).all())
    mem2 = sorted(
        (m.character_id, m.memory_type, m.importance, m.summary)
        for m in s2.query(Memory).filter_by(world_id=w2).all())
    assert mem1 == mem2

    req1 = sorted(
        (r.task, r.priority, r.status, json.dumps(r.result, sort_keys=True))
        for r in s1.query(AiRequest).filter_by(world_id=w1).all())
    req2 = sorted(
        (r.task, r.priority, r.status, json.dumps(r.result, sort_keys=True))
        for r in s2.query(AiRequest).filter_by(world_id=w2).all())
    assert req1 == req2

    # Different seed -> different log.
    r3, s3, _, _, _, _ = run_pipeline(43, 7, 20, True, True, True,
                                      str(tmp_path / "d3.db"))
    assert r3["events_by_type"] != r1["events_by_type"]
    s1.close()
    s2.close()
    s3.close()


# --- AE6'': config sensitivity -------------------------------------------------

def test_ae6_budget_sensitivity():
    """A tighter max_per_day budget must strictly reduce processed work."""
    r_loose, _, _, _, _, _ = run_pipeline(42, 7, 20, True, True, True)
    done_loose = r_loose["ai"]["requests_by_status"]["done"]

    # Rerun with a tiny budget via a custom settings object.
    settings = load_config("config/default.yaml")
    settings.social.enabled = True
    settings.org.enabled = True
    settings.llm = settings.llm.model_copy(update={"enabled": True})
    settings.llm.budgets = settings.llm.budgets.model_copy(
        update={"max_per_day": 1})
    settings.persistence.db_path = ":memory:"
    settings.world.initial_population = 20
    engine = create_engine("sqlite:///:memory:")
    session = Session(engine)
    bootstrap(engine, settings, 42)
    wid = settings.world.world_id
    seed_world(session, settings, wid)
    rng = random.Random(42)
    generate_population(session, settings, rng, wid, 20)
    seed_social(session, settings, wid, rng)
    enact_initial_laws(session, wid, settings, timestamp=0)
    sim = Engine(WorldClock(), TickScheduler())
    for _ in range(7):
        sim.step(DAY, session=session, world_id=wid, settings=settings)
    session.commit()
    report = build_report(session, wid, settings, 0.0, 42)

    done_tight = report["ai"]["requests_by_status"]["done"]
    skipped_tight = report["ai"]["requests_by_status"]["skipped"]
    assert done_tight < done_loose
    assert skipped_tight > 0
    session.close()
