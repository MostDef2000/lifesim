"""M4 Chunk C: AI invariants (R11) and the report `ai` block (R10)."""
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.ai import gateway as ai_gateway
from app.ai.worker import process_dialogue, run_ai_phase
from app.config.config import LlmConfig
from app.db.models import AiRequest, Character, DialogueTurn, Memory, WorldEvent, bootstrap
from app.events.events import EventType
from app.simulation.invariants import run_invariant_checks
from app.simulation.report import build_report

DAY = 1440


@pytest.fixture
def world(default_settings):
    default_settings.social.enabled = True
    default_settings.llm = LlmConfig(enabled=True)
    engine = create_engine("sqlite:///:memory:")
    bootstrap(engine, default_settings, 42)
    Session = sessionmaker(bind=engine)
    session = Session()
    import random

    from app.characters.generator import generate_population
    from app.world.seed_world import seed_world
    from app.world.social_seed import seed_social

    seed_world(session, default_settings, default_settings.world.world_id)
    rng = random.Random(42)
    generate_population(session, default_settings, rng,
                        default_settings.world.world_id, 5)
    seed_social(session, default_settings, default_settings.world.world_id,
                rng)
    chars = sorted(
        (c for c in session.query(Character).filter_by(
            world_id=default_settings.world.world_id).all() if c.alive),
        key=lambda c: c.id)
    chars[1].location_id = chars[0].location_id
    session.commit()
    return session, default_settings, engine


def two_alive(session, wid):
    chars = sorted(
        (c for c in session.query(Character).filter_by(
            world_id=wid).all() if c.alive), key=lambda c: c.id)
    return chars[0], chars[1]


def invariants_by_name(session, wid, settings):
    return {r["name"]: r for r in
            run_invariant_checks(session, wid, settings)}


# --- healthy world ----------------------------------------------------------

def test_ai_invariants_healthy(world):
    session, settings, engine = world
    wid = settings.world.world_id
    a, b = two_alive(session, wid)
    session.add(WorldEvent(
        world_id=wid, game_timestamp=100,
        event_type=EventType.CONFLICT.value, actor_id=a.id, target_id=b.id,
        payload=json.dumps({})))
    session.commit()
    ai_gateway.enqueue_decide(session, wid, settings, a.id, 1,
                              EventType.CONFLICT.value, 100)
    session.commit()
    run_ai_phase(session, wid, settings, DAY + 10)
    session.commit()

    results = invariants_by_name(session, wid, settings)
    for name in ("ai_request_integrity", "memory_integrity",
                 "dialogue_integrity"):
        assert results[name]["ok"], f"{name}: {results[name]['details']}"


def test_ai_invariants_gate_off(world):
    session, settings, engine = world
    wid = settings.world.world_id
    settings.llm = LlmConfig(enabled=False)
    results = invariants_by_name(session, wid, settings)
    for name in ("ai_request_integrity", "memory_integrity",
                 "dialogue_integrity"):
        assert results[name]["ok"]


# --- induced violations ------------------------------------------------------

def test_ai_request_integrity_detects_done_without_result(world):
    session, settings, engine = world
    wid = settings.world.world_id
    r = AiRequest(world_id=wid, character_id="npc_0001", task="decide",
                  context={}, priority="interactive", status="done",
                  created_at=0, processed_at=DAY, game_timestamp=0)
    session.add(r)
    session.commit()
    res = invariants_by_name(session, wid, settings)["ai_request_integrity"]
    assert res["ok"] is False
    assert "without result" in res["details"]


def test_ai_request_integrity_detects_stale_pending(world):
    session, settings, engine = world
    wid = settings.world.world_id
    r = AiRequest(world_id=wid, character_id="npc_0001", task="decide",
                  context={}, priority="interactive", status="pending",
                  created_at=0, game_timestamp=0)
    session.add(r)
    from app.db.models import WorldClock
    clock = session.query(WorldClock).filter_by(world_id=wid).first()
    clock.game_timestamp = 5 * DAY  # pending is 5 days old
    session.commit()
    res = invariants_by_name(session, wid, settings)["ai_request_integrity"]
    assert res["ok"] is False
    assert "pending older than a day" in res["details"]


def test_memory_integrity_detects_missing_event(world):
    session, settings, engine = world
    wid = settings.world.world_id
    session.add(Memory(
        world_id=wid, character_id="npc_0001", event_id=99999,
        memory_type="CONFLICT", importance=70, emotional_valence=-0.6,
        summary="orphan", created_at=0))
    res = invariants_by_name(session, wid, settings)["memory_integrity"]
    assert res["ok"] is False
    assert "event 99999 missing" in res["details"]


def test_memory_integrity_detects_bad_importance(world):
    session, settings, engine = world
    wid = settings.world.world_id
    a, _ = two_alive(session, wid)
    session.add(WorldEvent(
        world_id=wid, game_timestamp=100, event_type=EventType.CONFLICT.value,
        actor_id=a.id, target_id=None, payload=json.dumps({})))
    session.flush()
    ev = session.query(WorldEvent).filter_by(world_id=wid).first()
    session.add(Memory(
        world_id=wid, character_id=a.id, event_id=ev.id,
        memory_type="CONFLICT", importance=999, emotional_valence=-0.6,
        summary="bad", created_at=0))
    res = invariants_by_name(session, wid, settings)["memory_integrity"]
    assert res["ok"] is False
    assert "out of range" in res["details"]


def test_dialogue_integrity_detects_bad_role_and_gap(world):
    session, settings, engine = world
    wid = settings.world.world_id
    session.add(DialogueTurn(
        world_id=wid, session_id="s1", character_id="npc_0001",
        role="hacker", content="x", game_timestamp=10))
    res = invariants_by_name(session, wid, settings)["dialogue_integrity"]
    assert res["ok"] is False
    assert "invalid role" in res["details"]


def test_dialogue_integrity_detects_nonmonotone_session(world):
    session, settings, engine = world
    wid = settings.world.world_id
    a, _ = two_alive(session, wid)
    session.add(DialogueTurn(
        world_id=wid, session_id="s2", character_id=a.id, role="user",
        content="a", game_timestamp=100))
    session.add(DialogueTurn(
        world_id=wid, session_id="s2", character_id=a.id, role="assistant",
        content="b", game_timestamp=50))
    res = invariants_by_name(session, wid, settings)["dialogue_integrity"]
    assert res["ok"] is False
    assert "not monotone" in res["details"]


# --- report -------------------------------------------------------------------

def test_report_ai_block_present_when_enabled(world):
    session, settings, engine = world
    wid = settings.world.world_id
    a, b = two_alive(session, wid)
    session.add(WorldEvent(
        world_id=wid, game_timestamp=100,
        event_type=EventType.CONFLICT.value, actor_id=a.id, target_id=b.id,
        payload=json.dumps({})))
    session.commit()
    ai_gateway.enqueue_decide(session, wid, settings, a.id, 1,
                              EventType.CONFLICT.value, 100)
    session.commit()
    run_ai_phase(session, wid, settings, DAY + 10)
    process_dialogue(session, wid, settings, a.id,
                     [{"role": "user", "content": "hi"}], "s1", DAY + 20)
    session.commit()

    report = build_report(session, wid, settings, 1.0, 42)
    assert "ai" in report
    block = report["ai"]
    assert set(block.keys()) == {"requests_by_status", "decisions",
                                 "dialogue_turns", "memories"}
    assert block["requests_by_status"]["done"] >= 1
    assert block["decisions"]["applied"] >= 1
    assert block["dialogue_turns"] == 2  # user + assistant
    assert block["memories"]["total"] >= 0


def test_report_ai_block_absent_when_disabled(world):
    session, settings, engine = world
    wid = settings.world.world_id
    settings.llm = LlmConfig(enabled=False)
    report = build_report(session, wid, settings, 1.0, 42)
    assert "ai" not in report
