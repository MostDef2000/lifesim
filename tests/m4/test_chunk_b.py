"""M4 Chunk B: gateway enqueue rules, worker drain (priority/budget/retry),
memory capture/consolidation/select, dialogue sync path, decision apply."""
import json
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.ai import gateway as ai_gateway
from app.ai.memory import capture_memories, consolidate_memories, select_memories
from app.ai.transports import StubTransport
from app.ai.worker import process_dialogue, run_ai_phase
from app.config.config import LlmConfig
from app.db.models import (
    AiRequest,
    Character,
    CharacterTask,
    DialogueTurn,
    Memory,
    WorldEvent,
    bootstrap,
)
from app.events.events import EventType

DAY = 1440


@pytest.fixture
def world(default_settings):
    default_settings.social.enabled = True
    default_settings.org.enabled = True
    default_settings.llm = LlmConfig(enabled=True)
    engine = create_engine("sqlite:///:memory:")
    bootstrap(engine, default_settings, 42)
    Session = sessionmaker(bind=engine)
    session = Session()
    from app.characters.generator import generate_population
    from app.world.seed_world import seed_world
    from app.world.social_seed import seed_social

    seed_world(session, default_settings, default_settings.world.world_id)
    import random
    rng = random.Random(42)
    generate_population(session, default_settings, rng,
                        default_settings.world.world_id, 5)
    seed_social(session, default_settings, default_settings.world.world_id,
                rng)
    # Co-locate the first two alive characters so `socialize_with` is
    # available for decision tests.
    chars = sorted(
        (c for c in session.query(Character).filter_by(
            world_id=default_settings.world.world_id).all() if c.alive),
        key=lambda c: c.id)
    chars[1].location_id = chars[0].location_id
    session.commit()
    return session, default_settings


def alive_pair(session, world_id):
    chars = sorted(
        (c for c in session.query(Character).filter_by(
            world_id=world_id).all() if c.alive),
        key=lambda c: c.id)
    return chars[0], chars[1]


def conflict_event(session, world_id, a, b, ts):
    row = WorldEvent(
        world_id=world_id, game_timestamp=ts,
        event_type=EventType.CONFLICT.value, actor_id=a.id, target_id=b.id,
        payload=json.dumps(
            {"affection_before": 0.0, "affection_after": -5.0}))
    session.add(row)
    session.flush()
    return row


# --- gateway ---------------------------------------------------------------

def test_gate_off_enqueues_nothing(world):
    session, settings = world
    settings.llm = LlmConfig(enabled=False)
    a, b = alive_pair(session, settings.world.world_id)
    rid = ai_gateway.enqueue_decide(
        session, settings.world.world_id, settings, a.id, 1,
        EventType.CONFLICT.value, DAY)
    assert rid is None
    assert session.query(AiRequest).count() == 0


def test_decide_dedup_one_per_character_per_day(world):
    session, settings = world
    wid = settings.world.world_id
    a, b = alive_pair(session, wid)
    r1 = ai_gateway.enqueue_decide(session, wid, settings, a.id, 1,
                                   EventType.CONFLICT.value, 100)
    r2 = ai_gateway.enqueue_decide(session, wid, settings, a.id, 2,
                                   EventType.CONFLICT.value, 200)
    r3 = ai_gateway.enqueue_decide(session, wid, settings, a.id, 3,
                                   EventType.CONFLICT.value, DAY + 100)
    assert r1 is not None and r2 is None and r3 is not None


def test_priorities_recorded(world):
    session, settings = world
    wid = settings.world.world_id
    a, _ = alive_pair(session, wid)
    ai_gateway.enqueue_classify(session, wid, settings, a.id, 1,
                                "CONFLICT", 10)
    ai_gateway.enqueue_dialogue(session, wid, settings, a.id,
                                [{"role": "user", "content": "hi"}],
                                11, "s1")
    rows = session.query(AiRequest).order_by(AiRequest.id).all()
    assert rows[0].priority == "background"
    assert rows[1].priority == "interactive"


# --- worker ----------------------------------------------------------------

def test_worker_processes_decide_and_applies(world):
    session, settings = world
    wid = settings.world.world_id
    a, b = alive_pair(session, wid)
    ev = conflict_event(session, wid, a, b, 100)
    rid = ai_gateway.enqueue_decide(session, wid, settings, a.id, ev.id,
                                    EventType.CONFLICT.value, 100)
    session.commit()

    run_ai_phase(session, wid, settings, DAY + 10)
    session.flush()

    req = session.query(AiRequest).filter_by(id=rid).first()
    assert req.status == "done"
    assert req.result["decision"] == "socialize_with"
    assert req.result["target_character_id"] == b.id

    # AI_DECISION event + SOCIALIZE task (source='ai') created.
    evs = session.query(WorldEvent).filter_by(
        world_id=wid, event_type=EventType.AI_DECISION.value).all()
    assert len(evs) == 1
    assert json.loads(evs[0].payload)["applied"] is True
    task = session.query(CharacterTask).filter_by(
        character_id=a.id, source="ai").first()
    assert task is not None and task.task_type == "SOCIALIZE"


def test_worker_invalid_json_retries_then_fails(world):
    session, settings = world
    wid = settings.world.world_id
    a, _ = alive_pair(session, wid)
    rid = ai_gateway.enqueue_decide(session, wid, settings, a.id, 1,
                                    EventType.CONFLICT.value, 100)
    session.commit()

    with patch.object(StubTransport, "complete",
                      side_effect=["not json", "also not json"]):
        run_ai_phase(session, wid, settings, DAY + 10)

    req = session.query(AiRequest).filter_by(id=rid).first()
    assert req.status == "failed"
    assert req.error == "invalid JSON"
    assert session.query(WorldEvent).filter_by(
        world_id=wid, event_type=EventType.AI_DECISION.value).count() == 0


def test_worker_priority_order_fifo(world):
    session, settings = world
    wid = settings.world.world_id
    a, b = alive_pair(session, wid)
    # Enqueue background first, interactive second (id order).
    ai_gateway.enqueue_summarize(session, wid, settings, a.id,
                                 {"summaries": ["- CONFLICT at 10",
                                                "- CONFLICT at 20"]}, 100)
    ai_gateway.enqueue_decide(session, wid, settings, a.id, 1,
                              EventType.CONFLICT.value, 101)
    session.commit()

    processed = []
    orig = StubTransport.complete

    def spy(prompt, schema_hint):
        out = orig(StubTransport(), prompt, schema_hint)
        processed.append(schema_hint)
        return out

    with patch.object(StubTransport, "complete", side_effect=spy):
        run_ai_phase(session, wid, settings, DAY + 10)

    assert processed == ["decision", "summarize"]


def test_worker_budget_skips(world):
    session, settings = world
    wid = settings.world.world_id
    a, _ = alive_pair(session, wid)
    settings.llm.budgets.max_per_day = 1
    r1 = ai_gateway.enqueue_decide(session, wid, settings, a.id, 1,
                                   EventType.CONFLICT.value, 100)
    r2 = ai_gateway.enqueue_classify(session, wid, settings, a.id, 2,
                                     "CONFLICT", 101)
    session.commit()

    run_ai_phase(session, wid, settings, DAY + 10)
    req1 = session.query(AiRequest).filter_by(id=r1).first()
    req2 = session.query(AiRequest).filter_by(id=r2).first()
    assert req1.status == "done"
    assert req2.status == "skipped"
    assert req2.error == "budget: max_per_day"


def test_worker_per_task_budget(world):
    session, settings = world
    wid = settings.world.world_id
    a, _ = alive_pair(session, wid)
    settings.llm.budgets.per_task = {"decide": 1, "classify": 0,
                                     "dialogue": 10, "summarize": 10}
    settings.llm.budgets.max_per_day = 40
    r1 = ai_gateway.enqueue_decide(session, wid, settings, a.id, 1,
                                   EventType.CONFLICT.value, 100)
    r2 = ai_gateway.enqueue_decide(session, wid, settings, a.id, 2,
                                   EventType.CONFLICT.value, DAY + 100)
    session.commit()

    run_ai_phase(session, wid, settings, DAY + 10)
    run_ai_phase(session, wid, settings, 2 * DAY + 10)
    statuses = [session.query(AiRequest).filter_by(id=r).first().status
                for r in (r1, r2)]
    assert statuses == ["done", "skipped"]


def test_run_ai_phase_gate_off_noop(world):
    session, settings = world
    wid = settings.world.world_id
    settings.llm = LlmConfig(enabled=False)
    run_ai_phase(session, wid, settings, DAY)
    assert session.query(AiRequest).count() == 0
    assert session.query(Memory).count() == 0


# --- memory ----------------------------------------------------------------

def test_memory_capture_map_and_dedup(world):
    session, settings = world
    wid = settings.world.world_id
    a, b = alive_pair(session, wid)
    conflict_event(session, wid, a, b, DAY - 100)
    session.commit()

    created = capture_memories(session, wid, settings, DAY)
    assert created >= 2  # at least actor + target

    types = {m.memory_type for m in session.query(Memory).filter_by(
        world_id=wid).all()}
    assert "CONFLICT" in types
    # Second capture for the same day: no duplicates.
    created2 = capture_memories(session, wid, settings, DAY)
    assert created2 == 0


def test_memory_capture_death(world):
    session, settings = world
    wid = settings.world.world_id
    a, b = alive_pair(session, wid)
    ev = WorldEvent(
        world_id=wid, game_timestamp=DAY - 50,
        event_type=EventType.CHARACTER_DIED.value, actor_id=b.id,
        target_id=None,
        payload=json.dumps({"cause": "starvation"}))
    session.add(ev)
    session.commit()

    capture_memories(session, wid, settings, DAY)
    mems = session.query(Memory).filter_by(
        world_id=wid, memory_type="CHARACTER_DIED").all()
    assert len(mems) >= 1
    assert all(m.importance == 90 for m in mems)


def test_consolidation_merges_groups(world):
    session, settings = world
    wid = settings.world.world_id
    a, b = alive_pair(session, wid)
    for ts in (100, 200, 300):
        conflict_event(session, wid, a, b, ts)
    session.commit()

    capture_memories(session, wid, settings, DAY)
    before = session.query(Memory).filter_by(
        world_id=wid, character_id=a.id, memory_type="CONFLICT").count()
    assert before >= 2

    consolidated = consolidate_memories(session, wid, settings, DAY,
                                        StubTransport())
    assert consolidated >= 1
    a_confl = session.query(Memory).filter_by(
        world_id=wid, character_id=a.id, memory_type="CONFLICT").all()
    assert a_confl == []  # originals removed
    cons = session.query(Memory).filter_by(
        world_id=wid, character_id=a.id,
        memory_type="consolidated_CONFLICT").all()
    assert len(cons) == 1
    assert "CONFLICT" in cons[0].summary
    # MEMORY_CONSOLIDATED event emitted once.
    assert session.query(WorldEvent).filter_by(
        world_id=wid,
        event_type=EventType.MEMORY_CONSOLIDATED.value).count() == 1


def test_select_memories_topk_and_recall(world):
    session, settings = world
    wid = settings.world.world_id
    a, b = alive_pair(session, wid)
    ev1 = conflict_event(session, wid, a, b, 100)
    session.add(Memory(world_id=wid, character_id=a.id, event_id=ev1.id,
                       memory_type="CONFLICT", importance=70,
                       emotional_valence=-0.6, summary="s1", created_at=10))
    session.add(Memory(world_id=wid, character_id=a.id, event_id=ev1.id,
                       memory_type="ORG_FEAST", importance=30,
                       emotional_valence=0.5, summary="s2", created_at=20))
    session.commit()

    picked = select_memories(session, wid, a.id, 1, 999)
    assert len(picked) == 1
    assert picked[0].importance == 70
    assert picked[0].last_recalled_at == 999


# --- dialogue --------------------------------------------------------------

def test_dialogue_sync_path(world):
    session, settings = world
    wid = settings.world.world_id
    a, _ = alive_pair(session, wid)
    reply = process_dialogue(
        session, wid, settings, a.id,
        [{"role": "user", "content": "Привет!"}], "sess-1", 100)
    assert reply == "[stub] npc_0001 nods and listens."

    turns = session.query(DialogueTurn).filter_by(
        world_id=wid, session_id="sess-1").order_by(DialogueTurn.id).all()
    assert [t.role for t in turns] == ["user", "assistant"]
    req = session.query(AiRequest).filter_by(
        world_id=wid, task="dialogue").first()
    assert req.status == "done"


def test_dialogue_gate_off(world):
    session, settings = world
    wid = settings.world.world_id
    settings.llm = LlmConfig(enabled=False)
    a, _ = alive_pair(session, wid)
    reply = process_dialogue(
        session, wid, settings, a.id,
        [{"role": "user", "content": "hi"}], "s", 100)
    assert reply is None
    assert session.query(DialogueTurn).count() == 0
