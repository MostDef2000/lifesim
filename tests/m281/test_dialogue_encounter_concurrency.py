"""#281 — dialogue encounter concurrency: two concurrent POSTs, same pair.

Investigation-first slice: pin the ACTUAL behavior of two concurrent
``POST /dialogue/{sid}/message`` requests for the same (character, npc) pair.
The explore analysis (GPT-6.1 Terra) predicted the race does NOT exist because
the endpoint's user-message INSERT+flush (app.py:2110-2115) acquires SQLite's
single-writer lock BEFORE the hook's cooldown check (dialogue.py:129-149),
serializing the two requests. This test verifies that empirically and pins the
OBSERVED behavior (never the prediction).

OBSERVED behavior (both cases, deterministic across repeated runs):

The endpoint is a sync ``def`` → FastAPI runs it in a threadpool, so two
concurrent requests run in two worker threads, each with its own DB session /
connection from the production engine factory (WAL + synchronous NORMAL +
foreign_keys ON + 15s busy timeout, check_same_thread=False). The endpoint's
statement ordering (app.py:2098-2163) is:

    reads → safety_check → session.get x2 → _world_now
    → session.add(user msg) + session.flush()   ← INSERT: acquires writer lock
    → build_context → history → llm_reply → fallback → suggested
    → apply_dialogue_encounter (the hook: cooldown check + delta + event)
    → session.add(npc msg) → session.commit()   ← releases writer lock

The coordination (threading.Event, NOT a barrier — a correct single-writer
implementation would deadlock on a barrier that requires both to hold progress
simultaneously) forces the interleaving:

    A: INSERT+flush (acquires writer lock) → build_context (signals a_flushed)
       → hook (waits for b_pre_insert, then a short window-widening sleep)
       → real hook (cooldown check sees only A's own flushed user message,
         excluded by ID → applied) → delta + event → commit (releases lock).
    B: (started only after a_flushed) reads → safety_check (signals
       b_pre_insert, records B's thread ident) → INSERT+flush (BLOCKS on A's
       writer lock — the 15s busy timeout makes it wait, not raise)
       → [unblocks after A's commit]
       → hook (cooldown check now sees A's COMMITTED user message:
         game_timestamp == now_ts > now_ts - 30, not excluded by ID
         → applied: False) → commit.

DIRECT PROOF OF BLOCKING (the mechanism, not just the outcome): the test
measures the wall-clock duration of B's FIRST session.flush() — the
user-message INSERT at app.py:2115 that contends with A's writer lock — via
SQLAlchemy before_flush/after_flush listeners on the Session class (guarded by
B's thread ident + a one-shot flag so B's later flushes — the hook's
log_event flush and the commit flush — never overwrite the first measurement).
A holds the writer lock for ~OVERLAP_SLEEP (0.3s) after b_pre_insert, and B's
first flush starts right after b_pre_insert, so an uncontended flush (~1-5ms)
is impossible in this window: the measured duration >= 0.15s is only reachable
if B's flush actually WAITED on A's lock. That flush-duration assertion is the
mechanism pin; the outcome assertions below pin the contract. B's cooldown
check runs strictly AFTER A's commit — it sees A's committed message and
skips. No double-apply, no double-event, no "database is locked" error.

Pinned here (the OBSERVED contract — do not redesign):

- (a) CREATION (no canonical row): exactly ONE RELATIONSHIP_FORMED WorldEvent,
  exactly ONE relationship row, affection = initial_affection (0.0) + one
  delta (+2.0) = 2.0. The first request (A) reports applied + the formed
  milestone; the second (B) reports ``{"applied": false}`` with null
  milestones. No RelationshipEvent row.
- (b) PRE-EXISTING row band-crossing (row at 8.0, stranger): exactly ONE
  RELATIONSHIP_CHANGED WorldEvent (+ its linked RelationshipEvent row),
  affection = 8.0 + one delta (+2.0) = 10.0, band = "acquaintance" (the
  AFTER band). A reports applied + the band_changed milestone; B reports
  ``{"applied": false}`` with null milestones. No RELATIONSHIP_FORMED.
- Both requests return 200 (the 15s busy timeout absorbs the lock wait — B
  never surfaces a sqlite3.OperationalError).
- B's first flush (the user-message INSERT) is measured to take >= 0.15s —
  the direct proof it blocked on A's writer lock (an uncontended flush is
  ~1-5ms; the window-widening sleep holds A's lock ~0.3s).

The LLM reply path is left at its deterministic fallback (llm off by default —
the m195 baseline); the hook runs once after the source resolves, so fallback
parity is the deterministic path. No cooldown logic is mocked: the real
``apply_dialogue_encounter`` runs for both requests. The only monkeypatches are
synchronization seams (safety_check / build_context / the hook wrapper) that
call the REAL functions and add Event signaling + a window-widening sleep —
they change no behavior.
"""

import os
import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import event
from sqlalchemy.orm import Session, sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from fastapi.testclient import TestClient  # noqa: E402

import app.api.dialogue as dialogue_mod  # noqa: E402
from app.api.app import create_app  # noqa: E402
from app.config.config import load_config  # noqa: E402
from app.db.models import (  # noqa: E402
    Character,
    Relationship,
    RelationshipEvent,
    WorldEvent,
    bootstrap,
    create_engine_factory,
)
from app.world.seed_world import seed_world  # noqa: E402
from app.world.social_seed import seed_social  # noqa: E402

PW = "Correct-Horse-9!"
SETTINGS = None

# Window-widener: A's hook sleeps this long (after B has started its
# INSERT+flush) so B's flush is provably in-flight (blocked on A's writer
# lock) before A commits. This is a window-widener on top of the Event
# coordination (the m189 slow-transport pattern), not the coordination itself.
OVERLAP_SLEEP = 0.3


def setup_module(module):
    global SETTINGS
    SETTINGS = load_config("config/default.yaml")


def make_settings(tmp_path, initial_affection=None):
    # social.enabled=True (the shared SOCIALIZE rule operates on real social
    # settings); llm stays off (fallback path is the deterministic baseline).
    social = SETTINGS.social.model_copy(update={"enabled": True})
    if initial_affection is not None:
        social = social.model_copy(
            update={"initial_affection": float(initial_affection)})
    return SETTINGS.model_copy(update={
        "persistence": SETTINGS.persistence.model_copy(
            update={"db_path": str(tmp_path / "w.db")}
        ),
        "social": social,
    })


def make_world(tmp_path, user="b281", seed=42, initial_affection=None):
    """Seeded world + app/client + a player character + the npc id list."""
    settings = make_settings(tmp_path, initial_affection=initial_affection)
    engine = create_engine_factory(settings)
    with sessionmaker(bind=engine)() as session:
        bootstrap(engine, settings, seed=seed)
        seed_world(session, settings, settings.world.world_id)
        rng = random.Random(seed)
        from app.characters.generator import generate_population

        generate_population(session, settings, rng, settings.world.world_id, 20)
        seed_social(session, settings, settings.world.world_id, rng)
        session.commit()
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app(settings, factory)
    client = TestClient(app)
    client.post("/auth/register", json={
        "username": user, "email": f"{user}@x.com",
        "password": PW, "age_confirmed": True,
    })
    client.post("/auth/login", json={"username": user, "password": PW})
    r = client.post("/characters", json={"name": "Борис Орлов", "sex": "M", "age": 30})
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    with factory() as s:
        npcs = (
            s.query(Character)
            .filter_by(world_id=settings.world.world_id)
            .filter(Character.id.like("npc_%"))
            .order_by(Character.id)
            .all()
        )
        npc_ids = [n.id for n in npcs]
    return settings, factory, client, cid, npc_ids


def _set_affection(factory, world_id, a, b, value):
    """Canonical-pair upsert (the fixture seeds no row for a fresh pair)."""
    lo, hi = sorted([a, b])
    with factory() as s:
        r = s.query(Relationship).filter_by(
            world_id=world_id, character_a=lo, character_b=hi).first()
        if r is None:
            r = Relationship(
                world_id=world_id, character_a=lo, character_b=hi,
                affection=float(value), updated_at=0,
            )
            s.add(r)
        else:
            r.affection = float(value)
        s.commit()


def _get_rel(factory, world_id, a, b):
    lo, hi = sorted([a, b])
    with factory() as s:
        r = s.query(Relationship).filter_by(
            world_id=world_id, character_a=lo, character_b=hi).first()
        if r is None:
            return None
        return {
            "character_a": r.character_a, "character_b": r.character_b,
            "affection": r.affection,
            "updated_at": r.updated_at,
        }


def _event_ids(factory, world_id):
    with factory() as s:
        return {e.id for e in s.query(WorldEvent.id).filter_by(world_id=world_id)}


def _new_events(factory, world_id, before_ids):
    """All WorldEvent rows added after the given id set (fresh session)."""
    with factory() as s:
        return [
            e for e in s.query(WorldEvent).filter_by(world_id=world_id).all()
            if e.id not in before_ids
        ]


def _rel_event_count(factory, world_id):
    with factory() as s:
        return s.query(RelationshipEvent).filter_by(world_id=world_id).count()


def _start(client, npc_id):
    """One dialogue session per (user, npc, game-minute) — the server derives
    the id from all three, so a test never starts twice for the same pair at a
    frozen clock."""
    r = client.post("/dialogue/start", json={"npc_id": npc_id})
    assert r.status_code == 201, r.text
    return r.json()["session_id"]


def run_concurrent_pair(client, sid, monkeypatch, overlap_sleep=OVERLAP_SLEEP):
    """Drive two concurrent POST /dialogue/{sid}/message requests with a
    coordinated interleaving (see module docstring). Returns
    (response_a, response_b, b_first_flush_duration) where responses are the
    raw starlette Response objects (check .status_code / .json()) and
    b_first_flush_duration is the measured wall-clock duration (seconds) of
    B's FIRST session.flush() — the user-message INSERT at app.py:2115 that
    contends with A's writer lock. A duration >= the window-widening sleep
    proves B's flush blocked on A's lock (an uncontended flush is ~1-5ms).

    The monkeypatches are synchronization seams only: each wrapper calls the
    REAL function and adds Event signaling (and, for A's hook, a
    window-widening sleep). The SQLAlchemy before_flush/after_flush listeners
    are measurement-only (they record B's first-flush duration and change no
    behavior). No cooldown logic is mocked.
    """
    real_safety_check = dialogue_mod.safety_check
    real_build_context = dialogue_mod.build_context
    real_hook = dialogue_mod.apply_dialogue_encounter

    a_flushed = threading.Event()    # A completed INSERT+flush (holds lock)
    b_pre_insert = threading.Event()  # B is about to INSERT+flush
    lock = threading.Lock()
    counts = {"safety_check": 0, "build_context": 0, "hook": 0}

    # B's thread ident + first-flush measurement. b_ident is written on B's
    # thread (in wrapped_safety_check) and read by the flush callbacks; the
    # ident + one-shot flag isolate B's FIRST flush from every other flush.
    b_ident = None
    b_flush_start = None
    b_flush_duration = None
    b_first_flush_measured = False

    def wrapped_safety_check(content):
        nonlocal b_ident
        with lock:
            counts["safety_check"] += 1
            n = counts["safety_check"]
        result = real_safety_check(content)
        if n == 2:  # B's call (A's is first — A is started first)
            b_ident = threading.get_ident()
            b_pre_insert.set()
        return result

    def wrapped_build_context(session, world_id, user_char, npc):
        with lock:
            counts["build_context"] += 1
            n = counts["build_context"]
        result = real_build_context(session, world_id, user_char, npc)
        if n == 1:  # A's call (first build_context = A; it runs right after
            # A's INSERT+flush, so A holds the writer lock here)
            a_flushed.set()
        return result

    def wrapped_hook(session, world_id, session_row, now_ts, settings):
        with lock:
            counts["hook"] += 1
            n = counts["hook"]
        if n == 1:  # A's hook (first hook = A; B's hook is after B's flush,
            # which is after A's commit)
            # A holds the writer lock from its INSERT+flush. Wait until B has
            # started its INSERT+flush (b_pre_insert), then widen the window
            # so B's flush is provably in-flight (blocked on A's lock) before
            # A commits.
            assert b_pre_insert.wait(timeout=10), \
                "B never reached its INSERT+flush (b_pre_insert not set)"
            time.sleep(overlap_sleep)
        return real_hook(session, world_id, session_row, now_ts, settings)

    # Measurement-only: record the wall-clock duration of B's FIRST flush
    # (the user-message INSERT at app.py:2115). Class-level listeners fire for
    # every session; the ident + one-shot guard isolate B's first flush so the
    # hook's log_event flush and the commit flush never overwrite it.
    def _before_flush(session, flush_context, instances):
        nonlocal b_flush_start
        if (b_ident is not None and threading.get_ident() == b_ident
                and not b_first_flush_measured):
            b_flush_start = time.perf_counter()

    def _after_flush(session, flush_context):
        nonlocal b_flush_duration, b_first_flush_measured
        if (b_ident is not None and threading.get_ident() == b_ident
                and not b_first_flush_measured and b_flush_start is not None):
            b_flush_duration = time.perf_counter() - b_flush_start
            b_first_flush_measured = True

    monkeypatch.setattr(dialogue_mod, "safety_check", wrapped_safety_check)
    monkeypatch.setattr(dialogue_mod, "build_context", wrapped_build_context)
    monkeypatch.setattr(dialogue_mod, "apply_dialogue_encounter", wrapped_hook)
    event.listen(Session, "before_flush", _before_flush)
    event.listen(Session, "after_flush", _after_flush)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            fut_a = pool.submit(
                client.post, f"/dialogue/{sid}/message", json={"content": "Привет!"})
            # Wait until A has completed its INSERT+flush (holds the writer
            # lock), THEN start B — so B's INSERT+flush is guaranteed to
            # contend with A.
            assert a_flushed.wait(timeout=10), \
                "A never completed its INSERT+flush (a_flushed not set)"
            fut_b = pool.submit(
                client.post, f"/dialogue/{sid}/message", json={"content": "Привет!"})
            resp_a = fut_a.result(timeout=30)
            resp_b = fut_b.result(timeout=30)
    finally:
        # Class-level listeners are GLOBAL — remove them so nothing leaks into
        # other tests.
        event.remove(Session, "before_flush", _before_flush)
        event.remove(Session, "after_flush", _after_flush)
    return resp_a, resp_b, b_flush_duration


# ---------- (a) Creation: no canonical row ----------

def test_concurrent_creation_single_event_single_row(tmp_path, monkeypatch):
    """Two concurrent POSTs for a fresh pair (no canonical Relationship row):
    exactly ONE RELATIONSHIP_FORMED, exactly ONE row, affection = 0.0 + one
    delta (+2.0) = 2.0. A (first, holds the lock) applies + emits; B (second,
    blocked on the lock) sees A's committed message → cooldown skip."""
    settings, factory, client, cid, npc_ids = make_world(tmp_path)
    wid = settings.world.world_id
    npc = npc_ids[0]
    assert _get_rel(factory, wid, cid, npc) is None, \
        "fixture precondition: no pre-existing row for the fresh pair"
    before = _event_ids(factory, wid)
    sid = _start(client, npc)

    resp_a, resp_b, b_flush = run_concurrent_pair(client, sid, monkeypatch)

    # Both requests succeed — the 15s busy timeout absorbs B's lock wait, so
    # B never surfaces a sqlite3.OperationalError ("database is locked").
    assert resp_a.status_code == 200, resp_a.text
    assert resp_b.status_code == 200, resp_b.text
    # MECHANISM: B's first flush (the user-message INSERT) was measured — and
    # it took >= 0.15s, only reachable if it WAITED on A's writer lock (an
    # uncontended flush is ~1-5ms; A holds the lock ~0.3s after b_pre_insert).
    assert b_flush is not None, "B's first flush was not measured"
    assert b_flush >= 0.15, \
        f"B's first flush too fast ({b_flush:.3f}s) — it did not block on A's lock"

    body_a = resp_a.json()
    body_b = resp_b.json()
    # A applied the delta (it went first and held the writer lock).
    assert body_a["relationship_effect"] == {
        "applied": True, "affection_delta": 2.0, "affection_after": 2.0,
    }
    assert body_a["relationship_formed"] == {
        "target_id": npc, "affection_delta": 2.0, "affection_after": 2.0,
    }
    assert body_a["relationship_band_changed"] is None
    # B saw the cooldown (A's committed user message is inside the window).
    assert body_b["relationship_effect"] == {"applied": False}
    assert body_b["relationship_formed"] is None
    assert body_b["relationship_band_changed"] is None

    # Exactly one RELATIONSHIP_FORMED WorldEvent (no double-event).
    new = _new_events(factory, wid, before)
    formed = [e for e in new if e.event_type == "RELATIONSHIP_FORMED"]
    assert len(formed) == 1, \
        f"expected exactly one RELATIONSHIP_FORMED, got {len(formed)}"
    # Exactly one relationship row, affection = 0.0 + 2.0 = 2.0 (one delta).
    rel = _get_rel(factory, wid, cid, npc)
    assert rel is not None, "the canonical row must exist"
    assert rel["affection"] == 2.0, \
        f"affection must be initial (0.0) + one delta (2.0), got {rel['affection']}"
    with factory() as s:
        lo, hi = sorted([cid, npc])
        assert s.query(Relationship).filter_by(
            world_id=wid, character_a=lo, character_b=hi).count() == 1, \
            "exactly one relationship row for the pair"
    # Creation emits no RelationshipEvent row.
    assert _rel_event_count(factory, wid) == 0


# ---------- (b) Pre-existing row: band crossing ----------

def test_concurrent_band_crossing_single_event(tmp_path, monkeypatch):
    """Two concurrent POSTs for a pair with a PRE-EXISTING row at 8.0
    (stranger band): the first request's +2.0 delta crosses to 10.0
    (acquaintance) → exactly ONE RELATIONSHIP_CHANGED (+ its linked
    RelationshipEvent row), affection = 10.0, band = "acquaintance". The
    second request (blocked on the lock) sees the cooldown → skip."""
    settings, factory, client, cid, npc_ids = make_world(tmp_path)
    wid = settings.world.world_id
    npc = npc_ids[0]
    _set_affection(factory, wid, cid, npc, 8.0)
    assert _get_rel(factory, wid, cid, npc)["affection"] == 8.0, \
        "fixture precondition: pre-existing row at 8.0 (stranger)"
    before = _event_ids(factory, wid)
    before_rel_events = _rel_event_count(factory, wid)
    sid = _start(client, npc)

    resp_a, resp_b, b_flush = run_concurrent_pair(client, sid, monkeypatch)

    assert resp_a.status_code == 200, resp_a.text
    assert resp_b.status_code == 200, resp_b.text
    # MECHANISM: B's first flush (the user-message INSERT) was measured — and
    # it took >= 0.15s, only reachable if it WAITED on A's writer lock (an
    # uncontended flush is ~1-5ms; A holds the lock ~0.3s after b_pre_insert).
    assert b_flush is not None, "B's first flush was not measured"
    assert b_flush >= 0.15, \
        f"B's first flush too fast ({b_flush:.3f}s) — it did not block on A's lock"

    body_a = resp_a.json()
    body_b = resp_b.json()
    # A applied the delta (8.0 → 10.0, crossing stranger→acquaintance).
    assert body_a["relationship_effect"] == {
        "applied": True, "affection_delta": 2.0, "affection_after": 10.0,
    }
    assert body_a["relationship_formed"] is None, \
        "pre-existing row → no formed milestone"
    assert body_a["relationship_band_changed"] == {
        "band": "acquaintance", "affection_before": 8.0, "affection_after": 10.0,
    }
    # B saw the cooldown.
    assert body_b["relationship_effect"] == {"applied": False}
    assert body_b["relationship_formed"] is None
    assert body_b["relationship_band_changed"] is None

    # Exactly one RELATIONSHIP_CHANGED WorldEvent (no double-event).
    new = _new_events(factory, wid, before)
    changed = [e for e in new if e.event_type == "RELATIONSHIP_CHANGED"]
    assert len(changed) == 1, \
        f"expected exactly one RELATIONSHIP_CHANGED, got {len(changed)}"
    # No RELATIONSHIP_FORMED (the row pre-existed).
    formed = [e for e in new if e.event_type == "RELATIONSHIP_FORMED"]
    assert len(formed) == 0, "pre-existing row → no RELATIONSHIP_FORMED"
    # Exactly one relationship row, affection = 8.0 + 2.0 = 10.0 (one delta).
    rel = _get_rel(factory, wid, cid, npc)
    assert rel is not None
    assert rel["affection"] == 10.0, \
        f"affection must be 8.0 + one delta (2.0) = 10.0, got {rel['affection']}"
    with factory() as s:
        lo, hi = sorted([cid, npc])
        assert s.query(Relationship).filter_by(
            world_id=wid, character_a=lo, character_b=hi).count() == 1, \
            "exactly one relationship row for the pair"
    # Exactly one new linked RelationshipEvent row.
    assert _rel_event_count(factory, wid) == before_rel_events + 1, \
        "exactly one new RelationshipEvent (linked to the CHANGED event)"
