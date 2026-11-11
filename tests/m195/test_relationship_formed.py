"""#195 (first slice) — RELATIONSHIP_FORMED: dialogue creates the relationship.

When a dialogue encounter's affection application CREATES the canonical
Relationship row, the dialogue endpoint emits exactly ONE new WorldEvent
type RELATIONSHIP_FORMED (enum 44→45, appended after PARTNERSHIP_DECLARED)
and the POST response gains one additive top-level key
``relationship_formed`` = ``{"target_id": <npc_id>, "affection_delta":
<RULE delta>, "affection_after": <affection>}`` or ``null``.

Pinned here (the approved design contract — do not redesign):

- creation emits exactly one RELATIONSHIP_FORMED — parameterized over the
  fallback AND the stubbed-LLM source (the m156 seam); exactly one new
  relationship row for the pair;
- exact event contract: event_type string, world_id, game timestamp,
  actor_id = the player character, target_id = the NPC, location_id None;
  payload has EXACTLY the keys {target_id, affection_delta,
  affection_after}; no RelationshipEvent row; no other new WorldEvent of
  any type;
- exact response contract: ``relationship_formed`` equals the event
  payload; ``relationship_effect`` retains its exact existing shape;
  ``relationship_formed`` is null on cooldown-skip and on
  existing-relationship encounters;
- cooldown: a second exchange at the same game timestamp (same session)
  and a fresh session for the same pair (+1 game minute — the session id
  is derived from the game minute, so a distinct session id requires a
  distinct minute — still inside the 30-minute window) → no new event,
  null milestone; the by-ID exclusion (dialogue.py:69-77,100-114) is
  respected (the prior session's message stays visible to the window);
- existing relationship: an eligible encounter still applies affection,
  emits nothing, null milestone;
- creation is not band crossing: a pre-existing relationship whose
  encounter crosses a band boundary emits RELATIONSHIP_CHANGED (slice 2
  — the crossing/touch vectors moved to
  tests/m195/test_relationship_band_changed.py as emission tests);
- RULE-vs-actual delta: a creation case where the helper clamps
  (initial_affection=100.0) → the milestone payload/response carry the
  RULE delta while ``relationship_effect`` carries the honest
  post-clamp actual delta;
- canonical pair ordering: the player id (plr_*) sorts AFTER the npc id
  (npc_*) — the reversed lexical case — still resolves one canonical pair
  (character_a=npc, character_b=player); the event actor remains the
  player;
- /dialogue/start alone: no relationship, no event;
- rollback atomicity: a real request driven far enough to run the hook +
  log_event, then a forced failure before the endpoint commit (m186
  pattern: wrap apply_dialogue_encounter, run the real hook, then raise)
  → with a FRESH session: no relationship, no WorldEvent, no dialogue
  messages;
- enum/schema: len(EventType) == 45, "RELATIONSHIP_FORMED" in values,
  values unique, appended after PARTNERSHIP_DECLARED; schema version
  still "0.15.0" (no new table);
- wire: the WS polling serializer and the REST /world/events expose the
  new event with payload target_id without serializer changes;
- UI static pins: app.js contains the .chat-milestone creation guarded on
  r.relationship_formed, placed AFTER the renderMessages(hist.messages)
  call in the POST handler; the history rendering path contains no
  milestone synthesis (single creation site); #chat-rel-note
  construction unchanged.
"""

import json
import os
import random
import sys

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))


from tests.helpers import auth_client, make_user  # noqa: E402

from app.api.app import create_app  # noqa: E402
from app.config.config import load_config  # noqa: E402
from app.db.models import (  # noqa: E402
    Character,
    DialogueMessage,
    Relationship,
    RelationshipEvent,
    WorldClock,
    WorldEvent,
    bootstrap,
    create_engine_factory,
)
from app.events.events import EventType  # noqa: E402
from app.world.seed_world import seed_world  # noqa: E402
from app.world.social_seed import seed_social  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
APP_JS = os.path.join(ROOT, "backend/app/web/app.js")

SETTINGS = None


def setup_module(module):
    global SETTINGS
    SETTINGS = load_config("config/default.yaml")


def make_settings(tmp_path, initial_affection=None):
    # social.enabled=True (the shared SOCIALIZE rule operates on real
    # social settings); llm stays off by default (fallback path is the
    # baseline; the LLM-stub tests monkeypatch the seam for the stub).
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


def make_world(tmp_path, user="b195", seed=42, initial_affection=None):
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
    # #309: local register/login is gone — the user row is created directly
    # (make_user) and the client carries the app's own signed session
    # cookie (auth_client).
    with factory() as s:
        user_row = make_user(s, user, f"{user}@x.com")
    client = auth_client(app, settings, user_row)
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


def _start(client, npc_id):
    """One dialogue session per (user, npc, game-minute) — the server
    derives the id from all three, so a test never starts twice for the
    same pair at a frozen clock."""
    r = client.post("/dialogue/start", json={"npc_id": npc_id})
    assert r.status_code == 201, r.text
    return r.json()["session_id"]


def _msg(client, sid, content="Привет!"):
    r = client.post(f"/dialogue/{sid}/message", json={"content": content})
    assert r.status_code == 200, r.text
    return r.json()


def _advance_clock(factory, world_id, minutes):
    with factory() as s:
        clock = s.query(WorldClock).filter_by(world_id=world_id).first()
        clock.game_timestamp += minutes
        s.commit()


def _now_ts(factory, world_id):
    with factory() as s:
        return s.query(WorldClock).filter_by(world_id=world_id).first().game_timestamp


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


# ---------- a. Creation emits exactly one RELATIONSHIP_FORMED ----------

@pytest.mark.parametrize("llm_stub", [False, True], ids=["fallback", "llm-stub"])
def test_creation_emits_exactly_one_relationship_formed(tmp_path, monkeypatch, llm_stub):
    """A creation encounter emits exactly one RELATIONSHIP_FORMED and
    creates exactly one relationship row — for BOTH the fallback and the
    stubbed-LLM reply source (parity by construction: the hook runs once,
    after the source resolves)."""
    settings, factory, client, cid, npc_ids = make_world(tmp_path)
    wid = settings.world.world_id
    npc = npc_ids[0]
    assert _get_rel(factory, wid, cid, npc) is None

    if llm_stub:
        import app.api.dialogue as dialogue_mod

        monkeypatch.setattr(
            dialogue_mod, "llm_reply",
            lambda *a, **k: "Ответ через LLM-шов.",
        )

    before = _event_ids(factory, wid)
    body = _msg(client, _start(client, npc))
    new = _new_events(factory, wid, before)

    assert body["source"] == ("llm" if llm_stub else "fallback")
    formed = [e for e in new if e.event_type == "RELATIONSHIP_FORMED"]
    assert len(formed) == 1, "creation emits exactly one RELATIONSHIP_FORMED"
    # exactly one new relationship row for the pair
    lo, hi = sorted([cid, npc])
    with factory() as s:
        assert s.query(Relationship).filter_by(
            world_id=wid, character_a=lo, character_b=hi).count() == 1


# ---------- b. Exact event contract ----------

def test_event_contract_exact_fields_and_payload(tmp_path):
    """The emitted event carries the exact contract: type string, world,
    game timestamp, actor=player, target=npc, location None; the payload
    has EXACTLY {target_id, affection_delta, affection_after}; no
    RelationshipEvent row; no other new WorldEvent of any type."""
    settings, factory, client, cid, npc_ids = make_world(tmp_path)
    wid = settings.world.world_id
    npc = npc_ids[0]
    ts = _now_ts(factory, wid)
    before = _event_ids(factory, wid)

    body = _msg(client, _start(client, npc))
    new = _new_events(factory, wid, before)

    assert len(new) == 1, "creation emits exactly one WorldEvent of any type"
    ev = new[0]
    assert ev.event_type == "RELATIONSHIP_FORMED"
    assert ev.world_id == wid
    assert ev.game_timestamp == ts
    assert ev.actor_id == cid, "actor is the player character"
    assert ev.target_id == npc
    assert ev.location_id is None
    payload = json.loads(ev.payload)
    assert set(payload.keys()) == {"target_id", "affection_delta", "affection_after"}
    assert payload["target_id"] == npc
    assert payload["affection_delta"] == 2.0
    assert payload["affection_after"] == 2.0
    # no RelationshipEvent row (band-event bookkeeping stays in lifecycle)
    with factory() as s:
        assert s.query(RelationshipEvent).filter_by(world_id=wid).count() == 0
    # the response milestone equals the event payload
    assert body["relationship_formed"] == payload


# ---------- c. Exact response contract ----------

def test_response_contract_milestone_and_effect_shape(tmp_path):
    """relationship_formed is additive and equals the event payload;
    relationship_effect retains its exact existing shape; the milestone is
    null on a cooldown-skip."""
    settings, factory, client, cid, npc_ids = make_world(tmp_path)
    wid = settings.world.world_id
    npc = npc_ids[0]
    before = _event_ids(factory, wid)

    sid = _start(client, npc)
    body = _msg(client, sid)
    # old keys stay; relationship_formed is additive
    assert set(body.keys()) >= {
        "npc_reply", "suggested_responses", "session_id", "source",
        "relationship_effect", "relationship_formed",
    }
    # relationship_effect retains its exact existing shape
    assert body["relationship_effect"] == {
        "applied": True, "affection_delta": 2.0, "affection_after": 2.0,
    }
    # relationship_formed equals the event payload
    new = _new_events(factory, wid, before)
    assert len(new) == 1
    payload = json.loads(new[0].payload)
    assert body["relationship_formed"] == payload
    assert body["relationship_formed"] == {
        "target_id": npc, "affection_delta": 2.0, "affection_after": 2.0,
    }

    # cooldown-skip → null milestone
    body2 = _msg(client, sid)
    assert body2["relationship_effect"] == {"applied": False}
    assert body2["relationship_formed"] is None


# ---------- d. Cooldown ----------

def test_cooldown_same_session_no_event_null_milestone(tmp_path):
    """A second exchange at the same game timestamp (same session) → no
    new event, null milestone, affection unchanged."""
    settings, factory, client, cid, npc_ids = make_world(tmp_path)
    wid = settings.world.world_id
    npc = npc_ids[0]
    before = _event_ids(factory, wid)

    sid = _start(client, npc)
    first = _msg(client, sid)
    assert first["relationship_formed"] is not None
    after_first = _event_ids(factory, wid)
    assert len(after_first - before) == 1

    second = _msg(client, sid)
    assert second["relationship_effect"] == {"applied": False}
    assert second["relationship_formed"] is None
    assert _event_ids(factory, wid) == after_first, "cooldown: no new event"
    assert _get_rel(factory, wid, cid, npc)["affection"] == 2.0


def test_cooldown_fresh_session_same_pair_no_event(tmp_path):
    """A fresh session for the same pair (the session id is derived from
    the game minute, so +1 game minute — still inside the 30-minute
    window) must not re-apply: the prior session's message stays visible
    to the window query (the by-ID exclusion only hides the current
    session's own latest message)."""
    settings, factory, client, cid, npc_ids = make_world(tmp_path)
    wid = settings.world.world_id
    npc = npc_ids[0]

    sid1 = _start(client, npc)
    first = _msg(client, sid1)
    assert first["relationship_formed"] is not None
    after_first = _event_ids(factory, wid)

    _advance_clock(factory, wid, 1)  # distinct session id, still in window
    sid2 = _start(client, npc)
    assert sid2 != sid1
    second = _msg(client, sid2)
    assert second["relationship_effect"] == {"applied": False}
    assert second["relationship_formed"] is None
    assert _event_ids(factory, wid) == after_first, \
        "cross-session cooldown: no new event"
    assert _get_rel(factory, wid, cid, npc)["affection"] == 2.0


# ---------- e. Existing relationship ----------

def test_existing_relationship_applies_affection_emits_nothing(tmp_path):
    """An eligible encounter on a PRE-EXISTING relationship still applies
    affection but emits nothing and carries a null milestone."""
    settings, factory, client, cid, npc_ids = make_world(tmp_path)
    wid = settings.world.world_id
    npc = npc_ids[0]
    _set_affection(factory, wid, cid, npc, 60.0)
    before = _event_ids(factory, wid)

    body = _msg(client, _start(client, npc))
    assert body["relationship_effect"] == {
        "applied": True, "affection_delta": 2.0, "affection_after": 62.0,
    }
    assert body["relationship_formed"] is None, \
        "existing relationship → no formed milestone"
    assert _event_ids(factory, wid) == before, \
        "existing relationship → no WorldEvent"
    assert _get_rel(factory, wid, cid, npc)["affection"] == 62.0


# ---------- f. RULE-vs-actual delta at the clamp ----------

def test_rule_vs_actual_delta_at_clamp(tmp_path):
    """Creation with initial_affection=100.0: the helper clamps the write
    (100 + 2 → 100), so the RULE delta (2.0) differs from the honest
    post-clamp actual delta (0.0). The milestone payload/response carry
    the RULE delta; relationship_effect carries the actual delta."""
    settings, factory, client, cid, npc_ids = make_world(
        tmp_path, initial_affection=100.0)
    wid = settings.world.world_id
    npc = npc_ids[0]
    assert _get_rel(factory, wid, cid, npc) is None

    body = _msg(client, _start(client, npc))
    # relationship_effect: the honest post-clamp actual delta (0.0)
    assert body["relationship_effect"] == {
        "applied": True, "affection_delta": 0.0, "affection_after": 100.0,
    }
    # relationship_formed: the RULE delta (2.0) + the clamped affection
    assert body["relationship_formed"] == {
        "target_id": npc, "affection_delta": 2.0, "affection_after": 100.0,
    }
    rel = _get_rel(factory, wid, cid, npc)
    assert rel["affection"] == 100.0, "row clamped at 100"


# ---------- g. Canonical pair ordering (reversed lexical) ----------

def test_canonical_ordering_reversed_lexical_actor_is_player(tmp_path):
    """The player id (plr_*) sorts AFTER the npc id (npc_*) — the reversed
    lexical case. The canonical row is (character_a=npc, character_b=plr);
    the event actor remains the player, target the npc."""
    settings, factory, client, cid, npc_ids = make_world(tmp_path)
    wid = settings.world.world_id
    npc = npc_ids[0]
    # the reversed-lexical precondition: player id > npc id
    assert cid > npc, "fixture precondition: plr_* sorts after npc_*"
    before = _event_ids(factory, wid)

    body = _msg(client, _start(client, npc))
    rel = _get_rel(factory, wid, cid, npc)
    assert rel is not None
    assert (rel["character_a"], rel["character_b"]) == (npc, cid), \
        "canonical ordering: npc (a) < plr (b)"
    new = _new_events(factory, wid, before)
    assert len(new) == 1
    assert new[0].actor_id == cid, \
        "actor is the player even when it is character_b"
    assert new[0].target_id == npc
    assert body["relationship_formed"]["target_id"] == npc


# ---------- h. /dialogue/start alone ----------

def test_dialogue_start_alone_no_relationship_no_event(tmp_path):
    """/dialogue/start never applies the effect: an encounter is a message
    exchange, not a session start — no relationship, no event."""
    settings, factory, client, cid, npc_ids = make_world(tmp_path)
    wid = settings.world.world_id
    npc = npc_ids[0]
    before = _event_ids(factory, wid)

    r = client.post("/dialogue/start", json={"npc_id": npc})
    assert r.status_code == 201, r.text

    assert _get_rel(factory, wid, cid, npc) is None, \
        "start creates no relationship"
    assert _event_ids(factory, wid) == before, "start emits no event"


# ---------- i. Rollback atomicity ----------

def test_rollback_atomicity_no_partial_persistence(tmp_path, monkeypatch):
    """Drive a real request far enough to run the hook + log_event, then
    force a failure before the endpoint commit (m186 pattern: wrap
    apply_dialogue_encounter, run the real hook, then raise). With a FRESH
    session: no relationship, no WorldEvent, no dialogue messages."""
    settings, factory, client, cid, npc_ids = make_world(tmp_path)
    wid = settings.world.world_id
    npc = npc_ids[0]
    before = _event_ids(factory, wid)

    import app.api.dialogue as dialogue_mod

    real = dialogue_mod.apply_dialogue_encounter

    def boom(*a, **k):
        real(*a, **k)  # run the real hook (relationship + log_event)
        raise RuntimeError("injected failure before commit")

    monkeypatch.setattr(dialogue_mod, "apply_dialogue_encounter", boom)
    sid = _start(client, npc)
    with pytest.raises(RuntimeError):
        client.post(f"/dialogue/{sid}/message", json={"content": "Привет!"})

    # FRESH session assertions
    assert _get_rel(factory, wid, cid, npc) is None, \
        "rollback drops the created relationship"
    assert _event_ids(factory, wid) == before, \
        "rollback drops the emitted event"
    with factory() as s:
        assert s.query(DialogueMessage).filter_by(session_id=sid).count() == 0, \
            "rollback drops the flushed dialogue messages"


# ---------- j. Enum / schema ----------

def test_enum_45_and_membership():
    """The catalog grows 44→45: RELATIONSHIP_FORMED is appended after
    PARTNERSHIP_DECLARED, values stay unique."""
    assert len(EventType) == 45
    values = [e.value for e in EventType]
    assert "RELATIONSHIP_FORMED" in values
    assert len(values) == len(set(values)), "event type values must be unique"
    members = list(EventType.__members__)
    assert members[-1] == "RELATIONSHIP_FORMED"
    assert members[-2] == "PARTNERSHIP_DECLARED"


def test_schema_version_still_0150(tmp_path):
    """No new table → the schema version is unchanged (0.15.0)."""
    settings = make_settings(tmp_path)
    engine = create_engine_factory(settings)
    with sessionmaker(bind=engine)() as s:
        bootstrap(engine, settings, seed=42)
        version = s.execute(
            text("SELECT value FROM schema_meta WHERE key='version'")
        ).scalar()
        assert version == "0.15.0"


# ---------- k. Wire serializers ----------

def test_wire_serializers_expose_new_event(tmp_path):
    """The WS polling serializer and the REST /world/events expose the new
    event with payload target_id without serializer changes."""
    settings, factory, client, cid, npc_ids = make_world(tmp_path)
    wid = settings.world.world_id
    npc = npc_ids[0]

    _msg(client, _start(client, npc))

    from app.api.ws import _serialize_event, poll_events

    with factory() as s:
        ev = (
            s.query(WorldEvent)
            .filter_by(world_id=wid, event_type="RELATIONSHIP_FORMED")
            .one()
        )
        ser = _serialize_event(ev)
        polled = poll_events(s, wid, ev.id - 1)

    assert ser["event_type"] == "RELATIONSHIP_FORMED"
    assert ser["payload"]["target_id"] == npc
    assert ser["actor_id"] == cid
    assert ser["location_id"] is None
    assert any(
        p["event_type"] == "RELATIONSHIP_FORMED"
        and p["payload"]["target_id"] == npc
        for p in polled
    ), "poll_events must surface the new event for a prior cursor"

    # REST /world/events
    r = client.get("/world/events")
    assert r.status_code == 200, r.text
    feed = r.json()
    formed = [e for e in feed if e["event_type"] == "RELATIONSHIP_FORMED"]
    assert len(formed) == 1
    assert formed[0]["payload"]["target_id"] == npc
    assert formed[0]["actor_id"] == cid


# ---------- l. UI static pins ----------

def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def test_ui_chat_milestone_pins():
    js = _read(APP_JS)
    # The milestone node is created exactly once (a single creation site in
    # the POST handler — the history rendering path must never synthesize
    # it, so a second site would bump this count).
    assert js.count("chat-milestone") == 1
    # Guarded ONLY on the server's relationship_formed field.
    assert js.count("r.relationship_formed") == 1
    # The milestone text.
    assert "Начало отношений" in js
    # Placed AFTER the renderMessages(hist.messages) call in the POST
    # handler (the milestone is appended to the freshly rendered log).
    render_idx = js.index("renderMessages(hist.messages)")
    milestone_idx = js.index("chat-milestone")
    assert milestone_idx > render_idx, \
        "milestone must be appended after renderMessages"
    # #chat-rel-note construction unchanged (carry-forward m187 pins).
    assert js.count('"chat-rel-note"') == 1
    assert js.count("r.relationship_effect") == 1
