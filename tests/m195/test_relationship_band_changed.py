"""#195 (slice 2) — RELATIONSHIP_CHANGED: the dialogue band crossing.

When a dialogue encounter's affection delta crosses a band boundary on a
PRE-EXISTING canonical Relationship row, the dialogue endpoint emits
exactly ONE new WorldEvent type RELATIONSHIP_CHANGED (an EXISTING enum
member — the enum stays 45, no new member) and the POST response gains
one additive top-level key ``relationship_band_changed`` = the event
payload ``{"band": <after-band>, "affection_before": <float>,
"affection_after": <float>}`` or ``null`` (always present on
POST /dialogue/{sid}/message success). Parity with the SOCIALIZE path
(lifecycle.py: the band classifier, the crossing detection on ACTUAL
before/after bands, the linked RelationshipEvent row).

Pinned here (the approved slice-2 design contract — do not redesign):

- crossing emits exactly one RELATIONSHIP_CHANGED — parameterized over
  the reachable upward vectors 8→10, 9→11 (stranger→acquaintance) and
  48→50, 49→51 (acquaintance→friend): pre-existing row, one eligible
  encounter → exactly one new WorldEvent of that type, exact payload
  key-set {band, affection_before, affection_after} with band = the
  AFTER band string, actor_id = the player, target_id = the NPC,
  location_id None; exactly one linked RelationshipEvent (canonical
  pair, event_id = the WorldEvent id, impact = the RULE delta +2.0);
  no other new WorldEvent of any type; no RELATIONSHIP_FORMED;
- no-crossing silence: existing row 0→2 (default settings) → no event,
  no RelationshipEvent, ``relationship_band_changed`` is None (strict
  access), relationship_effect shape unchanged;
- boundary-touch silence: existing row at −30 → −28 (stranger→stranger)
  → no event, null. Documented: conflicted→stranger via dialogue is
  mathematically unreachable (the −5 conflict path starts below −30 and
  stays below it; +2 from −30 stays in stranger);
- creation exclusivity: settings.social.initial_affection = 8 / 48 / 50
  / 100 → creation emits RELATIONSHIP_FORMED only,
  ``relationship_band_changed`` is None, zero RELATIONSHIP_CHANGED rows
  even though the after-value lands in / crosses a band (48→50). The
  documented asymmetry vs SOCIALIZE (whose lazy creation CAN emit
  CHANGED — pinned by the guard test below);
- clamp cannot cross: initial_affection=100 → FORMED with
  affection_after=100, RULE delta +2 vs actual 0, NO CHANGED
  (friend→friend);
- cooldown after crossing: a second exchange in the same window (same
  session AND fresh session +1 game minute) → relationship_effect
  {"applied": false}, null band field, no new events;
- rollback atomicity: a crossing request driven far enough to run the
  real hook, then a forced failure before the endpoint commit (the
  slice-1 pattern: wrap apply_dialogue_encounter — call the real hook,
  then raise; log_event is NOT mocked) → with a FRESH session:
  affection unchanged (8), no RELATIONSHIP_CHANGED, no RelationshipEvent
  row, no dialogue messages; a positive control (same setup, no forced
  failure) emits the event, so this test is RED on base, not trivially
  green;
- response matrix: ``relationship_band_changed`` equals the event
  payload on crossing; null on formation / cooldown /
  existing-no-crossing;
- UI static pins: app.js contains ``chat-band-change`` exactly once,
  guarded on ``r.relationship_band_changed``, placed AFTER the
  renderMessages(hist.messages) call in the POST handler (coexisting
  with the slice-1 .chat-milestone block — each class literal appears
  exactly once); the «Изменение отношений» literal present; the history
  rendering path synthesizes neither banner; #chat-rel-note
  construction untouched; style.css contains ``.chat-band-change``;
- classifier unit tests: ``affection_band`` (extracted into
  app.social.affection) — boundary table −100/−31→conflicted,
  −30/−29/9→stranger, 10/49→acquaintance, 50/99/100→friend. LOCAL
  import inside the tests: on base the helper does not exist yet, so
  only these tests error and module collection stays intact;
- enum/schema: len(EventType) == 45 still, "RELATIONSHIP_CHANGED"
  already a member, schema version still "0.15.0" (no new table);
- wire: the WS polling serializer and the REST /world/events expose the
  CHANGED event + payload without serializer changes;
- SOCIALIZE asymmetry GUARD (green on base): a SOCIALIZE creation with
  initial_affection=48 still emits its CHANGED on the 48→50
  creation-crossing — the band-machinery extraction must leave the
  SOCIALIZE path behavior-identical (tests/m2/test_chunk_d.py:257-311
  and tests/m3/test_chunk_d.py:29-60 stay green untouched).
"""

import json
import os
import random
import sys

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from fastapi.testclient import TestClient  # noqa: E402

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
STYLE_CSS = os.path.join(ROOT, "backend/app/web/style.css")

PW = "Correct-Horse-9!"
SETTINGS = None


def setup_module(module):
    global SETTINGS
    SETTINGS = load_config("config/default.yaml")


def make_settings(tmp_path, initial_affection=None):
    # social.enabled=True (the shared SOCIALIZE rule operates on real
    # social settings); llm stays off by default (fallback path is the
    # baseline).
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


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ---------- a. Crossing emits exactly one RELATIONSHIP_CHANGED ----------

@pytest.mark.parametrize(
    "start_aff, after_aff, band",
    [
        (8.0, 10.0, "acquaintance"),   # stranger → acquaintance
        (9.0, 11.0, "acquaintance"),
        (48.0, 50.0, "friend"),        # acquaintance → friend
        (49.0, 51.0, "friend"),
    ],
    ids=["8-10", "9-11", "48-50", "49-51"],
)
def test_crossing_emits_exactly_one_relationship_changed(
    tmp_path, start_aff, after_aff, band,
):
    """A pre-existing row whose encounter crosses a band boundary emits
    exactly one RELATIONSHIP_CHANGED with the exact SOCIALIZE-parity
    contract, plus exactly one linked RelationshipEvent (impact = the
    RULE delta). No other new WorldEvent of any type; no
    RELATIONSHIP_FORMED (the row already existed)."""
    settings, factory, client, cid, npc_ids = make_world(tmp_path)
    wid = settings.world.world_id
    npc = npc_ids[0]
    _set_affection(factory, wid, cid, npc, start_aff)
    ts = _now_ts(factory, wid)
    before = _event_ids(factory, wid)

    body = _msg(client, _start(client, npc))
    new = _new_events(factory, wid, before)

    changed = [e for e in new if e.event_type == "RELATIONSHIP_CHANGED"]
    assert len(changed) == 1, "crossing emits exactly one RELATIONSHIP_CHANGED"
    assert len(new) == 1, "no other new WorldEvent of any type"
    assert not [e for e in new if e.event_type == "RELATIONSHIP_FORMED"], \
        "a crossing on a pre-existing row is not a formation"

    ev = changed[0]
    assert ev.world_id == wid
    assert ev.game_timestamp == ts
    assert ev.actor_id == cid, "actor is the player character"
    assert ev.target_id == npc
    assert ev.location_id is None
    payload = json.loads(ev.payload)
    assert set(payload.keys()) == {"band", "affection_before", "affection_after"}
    assert payload == {
        "band": band,
        "affection_before": start_aff,
        "affection_after": after_aff,
    }

    # exactly one linked RelationshipEvent (lifecycle.py:518-535 parity)
    with factory() as s:
        rel_events = s.query(RelationshipEvent).filter_by(world_id=wid).all()
    assert len(rel_events) == 1, "exactly one linked RelationshipEvent"
    re_ = rel_events[0]
    lo, hi = sorted([cid, npc])
    assert (re_.character_a, re_.character_b) == (lo, hi), \
        "canonical pair on the RelationshipEvent row"
    assert re_.event_type == "RELATIONSHIP_CHANGED"
    assert re_.impact == 2.0, "impact is the RULE delta from the shared helper"
    assert re_.event_id == ev.id, "linked to the WorldEvent"
    assert re_.game_timestamp == ts

    # response: the additive key equals the event payload; the effect
    # shape is unchanged; no formation milestone
    assert body["relationship_band_changed"] == payload
    assert body["relationship_effect"] == {
        "applied": True, "affection_delta": 2.0, "affection_after": after_aff,
    }
    assert body["relationship_formed"] is None


# ---------- b. No-crossing silence ----------

def test_no_crossing_existing_row_silence(tmp_path):
    """Existing row 0 → 2 (default settings): stranger → stranger — no
    event, no RelationshipEvent, ``relationship_band_changed`` is None
    (strict access: the additive key must exist and be null),
    relationship_effect shape unchanged."""
    settings, factory, client, cid, npc_ids = make_world(tmp_path)
    wid = settings.world.world_id
    npc = npc_ids[0]
    _set_affection(factory, wid, cid, npc, 0.0)
    before = _event_ids(factory, wid)

    body = _msg(client, _start(client, npc))
    assert body["relationship_effect"] == {
        "applied": True, "affection_delta": 2.0, "affection_after": 2.0,
    }
    assert body["relationship_band_changed"] is None
    assert body["relationship_formed"] is None
    assert _event_ids(factory, wid) == before, "no WorldEvent"
    with factory() as s:
        assert s.query(RelationshipEvent).filter_by(world_id=wid).count() == 0


# ---------- c. Boundary-touch silence ----------

def test_boundary_touch_minus30_silence(tmp_path):
    """Existing row at −30 → −28: stranger → stranger (boundary touch) —
    no event, null. Documented asymmetry: conflicted → stranger via
    dialogue is mathematically unreachable — the conflict path (−5)
    requires aff < −30 to start, so it stays below −30 (−31 − 5 = −36),
    and +2 from −30 stays in stranger (−28)."""
    settings, factory, client, cid, npc_ids = make_world(tmp_path)
    wid = settings.world.world_id
    npc = npc_ids[0]
    _set_affection(factory, wid, cid, npc, -30.0)
    before = _event_ids(factory, wid)

    body = _msg(client, _start(client, npc))
    assert body["relationship_effect"] == {
        "applied": True, "affection_delta": 2.0, "affection_after": -28.0,
    }
    assert body["relationship_band_changed"] is None
    assert body["relationship_formed"] is None
    assert _event_ids(factory, wid) == before, "boundary touch emits nothing"
    with factory() as s:
        assert s.query(RelationshipEvent).filter_by(world_id=wid).count() == 0


# ---------- d. Creation exclusivity ----------

@pytest.mark.parametrize(
    "initial_affection", [8.0, 48.0, 50.0, 100.0],
    ids=["init-8", "init-48", "init-50", "init-100"],
)
def test_creation_never_emits_changed(tmp_path, initial_affection):
    """Creation emits RELATIONSHIP_FORMED only — even when the initial
    affection lands in / crosses a band (48 → 50 crosses
    acquaintance → friend on the very creation). Formation and crossing
    are mutually exclusive by construction; the documented asymmetry vs
    SOCIALIZE (whose lazy creation CAN emit CHANGED — the guard test
    below pins that SOCIALIZE side)."""
    settings, factory, client, cid, npc_ids = make_world(
        tmp_path, initial_affection=initial_affection)
    wid = settings.world.world_id
    npc = npc_ids[0]
    assert _get_rel(factory, wid, cid, npc) is None
    before = _event_ids(factory, wid)

    body = _msg(client, _start(client, npc))
    new = _new_events(factory, wid, before)

    assert len(new) == 1
    assert new[0].event_type == "RELATIONSHIP_FORMED"
    assert not [e for e in new if e.event_type == "RELATIONSHIP_CHANGED"], \
        "creation never emits RELATIONSHIP_CHANGED"
    assert body["relationship_formed"] is not None
    assert body["relationship_band_changed"] is None
    with factory() as s:
        assert s.query(RelationshipEvent).filter_by(world_id=wid).count() == 0


# ---------- e. Clamp cannot cross ----------

def test_clamp_cannot_cross(tmp_path):
    """initial_affection=100: the helper clamps 100 + 2 → 100 — the
    RULE delta is +2 but the actual change is 0, and the band is
    friend → friend: FORMED with affection_after=100, RULE delta 2.0 in
    the milestone, actual 0.0 in relationship_effect, NO CHANGED."""
    settings, factory, client, cid, npc_ids = make_world(
        tmp_path, initial_affection=100.0)
    wid = settings.world.world_id
    npc = npc_ids[0]
    before = _event_ids(factory, wid)

    body = _msg(client, _start(client, npc))
    assert body["relationship_effect"] == {
        "applied": True, "affection_delta": 0.0, "affection_after": 100.0,
    }
    assert body["relationship_formed"] == {
        "target_id": npc, "affection_delta": 2.0, "affection_after": 100.0,
    }
    assert body["relationship_band_changed"] is None
    new = _new_events(factory, wid, before)
    assert [e.event_type for e in new] == ["RELATIONSHIP_FORMED"], \
        "clamp: friend → friend, no CHANGED"
    with factory() as s:
        assert s.query(RelationshipEvent).filter_by(world_id=wid).count() == 0


# ---------- f. Cooldown after crossing ----------

def test_cooldown_same_session_after_crossing(tmp_path):
    """A second exchange at the same game timestamp (same session) after
    a crossing → no new event, null band field, affection unchanged."""
    settings, factory, client, cid, npc_ids = make_world(tmp_path)
    wid = settings.world.world_id
    npc = npc_ids[0]
    _set_affection(factory, wid, cid, npc, 8.0)
    before = _event_ids(factory, wid)

    sid = _start(client, npc)
    first = _msg(client, sid)
    assert first["relationship_band_changed"] is not None
    after_first = _event_ids(factory, wid)
    assert len(after_first - before) == 1

    second = _msg(client, sid)
    assert second["relationship_effect"] == {"applied": False}
    assert second["relationship_band_changed"] is None
    assert _event_ids(factory, wid) == after_first, "cooldown: no new event"
    assert _get_rel(factory, wid, cid, npc)["affection"] == 10.0
    with factory() as s:
        assert s.query(RelationshipEvent).filter_by(world_id=wid).count() == 1, \
            "only the crossing's RelationshipEvent exists"


def test_cooldown_fresh_session_after_crossing(tmp_path):
    """A fresh session for the same pair (+1 game minute — the session
    id is derived from the game minute, so a distinct session id
    requires a distinct minute — still inside the 30-minute window)
    after a crossing → no re-apply, no new event, null band field."""
    settings, factory, client, cid, npc_ids = make_world(tmp_path)
    wid = settings.world.world_id
    npc = npc_ids[0]
    _set_affection(factory, wid, cid, npc, 8.0)

    sid1 = _start(client, npc)
    first = _msg(client, sid1)
    assert first["relationship_band_changed"] is not None
    after_first = _event_ids(factory, wid)

    _advance_clock(factory, wid, 1)  # distinct session id, still in window
    sid2 = _start(client, npc)
    assert sid2 != sid1
    second = _msg(client, sid2)
    assert second["relationship_effect"] == {"applied": False}
    assert second["relationship_band_changed"] is None
    assert _event_ids(factory, wid) == after_first, \
        "cross-session cooldown: no new event"
    assert _get_rel(factory, wid, cid, npc)["affection"] == 10.0


# ---------- g. Rollback atomicity ----------

def test_rollback_atomicity_crossing(tmp_path, monkeypatch):
    """Crossing scenario, forced failure after the real hook runs (the
    slice-1 pattern: wrap apply_dialogue_encounter — call the real
    hook, then raise; log_event is NOT mocked). With a FRESH session:
    affection unchanged (8), no RELATIONSHIP_CHANGED, no
    RelationshipEvent row, no dialogue messages. Positive control: the
    same setup without the forced failure emits the event (so this test
    is RED on base, not trivially green)."""
    # --- forced failure
    settings, factory, client, cid, npc_ids = make_world(tmp_path)
    wid = settings.world.world_id
    npc = npc_ids[0]
    _set_affection(factory, wid, cid, npc, 8.0)
    before = _event_ids(factory, wid)

    import app.api.dialogue as dialogue_mod

    real = dialogue_mod.apply_dialogue_encounter

    def boom(*a, **k):
        real(*a, **k)  # run the real hook (affection + event + link)
        raise RuntimeError("injected failure before commit")

    monkeypatch.setattr(dialogue_mod, "apply_dialogue_encounter", boom)
    sid = _start(client, npc)
    with pytest.raises(RuntimeError):
        client.post(f"/dialogue/{sid}/message", json={"content": "Привет!"})

    # FRESH session assertions
    assert _get_rel(factory, wid, cid, npc)["affection"] == 8.0, \
        "rollback drops the crossing affection write"
    assert _event_ids(factory, wid) == before, \
        "rollback drops the emitted CHANGED event"
    with factory() as s:
        assert s.query(RelationshipEvent).filter_by(world_id=wid).count() == 0, \
            "rollback drops the linked RelationshipEvent row"
        assert s.query(DialogueMessage).filter_by(session_id=sid).count() == 0, \
            "rollback drops the flushed dialogue messages"

    # --- positive control (fresh world, no forced failure)
    monkeypatch.setattr(dialogue_mod, "apply_dialogue_encounter", real)
    ctrl_dir = tmp_path / "ctrl"
    ctrl_dir.mkdir()
    settings2, factory2, client2, cid2, npc_ids2 = make_world(
        ctrl_dir, user="b195ctrl")
    wid2 = settings2.world.world_id
    npc2 = npc_ids2[0]
    _set_affection(factory2, wid2, cid2, npc2, 8.0)
    before2 = _event_ids(factory2, wid2)

    body = _msg(client2, _start(client2, npc2))
    new = _new_events(factory2, wid2, before2)
    changed = [e for e in new if e.event_type == "RELATIONSHIP_CHANGED"]
    assert len(changed) == 1, \
        "positive control: the unforced crossing emits the event"
    assert body["relationship_band_changed"] is not None


# ---------- h. Response matrix ----------

def test_response_matrix_band_changed(tmp_path):
    """relationship_band_changed: equals the event payload on crossing;
    null on formation / cooldown / existing-no-crossing — all four in
    one world (distinct npc pairs, so cooldowns never interact)."""
    settings, factory, client, cid, npc_ids = make_world(tmp_path)
    wid = settings.world.world_id
    npc_cross, npc_form, npc_plain = npc_ids[0], npc_ids[1], npc_ids[2]
    _set_affection(factory, wid, cid, npc_cross, 8.0)
    _set_affection(factory, wid, cid, npc_plain, 0.0)
    before = _event_ids(factory, wid)

    # crossing → equals the event payload
    sid_cross = _start(client, npc_cross)
    body_cross = _msg(client, sid_cross)
    new = _new_events(factory, wid, before)
    changed = [e for e in new if e.event_type == "RELATIONSHIP_CHANGED"]
    assert len(changed) == 1
    assert body_cross["relationship_band_changed"] == json.loads(
        changed[0].payload)

    # formation → null
    body_form = _msg(client, _start(client, npc_form))
    assert body_form["relationship_formed"] is not None
    assert body_form["relationship_band_changed"] is None

    # existing no-crossing → null
    body_plain = _msg(client, _start(client, npc_plain))
    assert body_plain["relationship_band_changed"] is None

    # cooldown (second message on the crossing session) → null
    body_cd = _msg(client, sid_cross)
    assert body_cd["relationship_effect"] == {"applied": False}
    assert body_cd["relationship_band_changed"] is None


# ---------- i. UI static pins ----------

def test_ui_chat_band_change_pins():
    js = _read(APP_JS)
    css = _read(STYLE_CSS)
    # The band-change node is created exactly once (a single creation
    # site in the POST handler — the history rendering path must never
    # synthesize it, so a second site would bump this count).
    assert js.count("chat-band-change") == 1
    # Guarded ONLY on the server's relationship_band_changed field.
    assert js.count("r.relationship_band_changed") == 1
    # The banner text.
    assert js.count("Изменение отношений") == 1
    # role="status" on the banner (the creation line).
    band_idx = js.index("chat-band-change")
    assert 'role: "status"' in js[band_idx:band_idx + 160]
    # Placed AFTER the renderMessages(hist.messages) call in the POST
    # handler (coexisting with the slice-1 .chat-milestone block — each
    # class literal appears exactly once).
    render_idx = js.index("renderMessages(hist.messages)")
    assert band_idx > render_idx, \
        "band banner must be appended after renderMessages"
    assert js.count("chat-milestone") == 1
    assert js.count("r.relationship_formed") == 1
    # #chat-rel-note construction unchanged (carry-forward m187 pins).
    assert js.count('"chat-rel-note"') == 1
    assert js.count("r.relationship_effect") == 1
    # CSS: the banner is styled (existing tokens only — the m63 literal
    # counts stay green neighbors).
    assert ".chat-band-change" in css


# ---------- j. Classifier unit tests ----------

@pytest.mark.parametrize(
    "aff, band",
    [
        (-100.0, "conflicted"),
        (-31.0, "conflicted"),
        (-30.0, "stranger"),
        (-29.0, "stranger"),
        (9.0, "stranger"),
        (10.0, "acquaintance"),
        (49.0, "acquaintance"),
        (50.0, "friend"),
        (99.0, "friend"),
        (100.0, "friend"),
    ],
)
def test_affection_band_boundaries(aff, band):
    """The extracted pure classifier (slice-2 design decision 1):
    conflicted [−100,−30), stranger [−30,10), acquaintance [10,50),
    friend [50,100]. LOCAL import: on base the helper does not exist
    yet, so only these tests error and module collection stays intact."""
    from app.social.affection import affection_band

    assert affection_band(aff) == band


# ---------- k. Enum / schema ----------

def test_enum_still_45_changed_is_existing_member():
    """The enum stays 45: RELATIONSHIP_CHANGED is an EXISTING member
    (no new member in slice 2), values stay unique."""
    assert len(EventType) == 45
    values = [e.value for e in EventType]
    assert "RELATIONSHIP_CHANGED" in values
    assert len(values) == len(set(values)), "event type values must be unique"


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


# ---------- l. Wire serializers ----------

def test_wire_serializers_expose_changed_event(tmp_path):
    """The WS polling serializer and the REST /world/events expose the
    CHANGED event + payload without serializer changes."""
    settings, factory, client, cid, npc_ids = make_world(tmp_path)
    wid = settings.world.world_id
    npc = npc_ids[0]
    _set_affection(factory, wid, cid, npc, 8.0)

    _msg(client, _start(client, npc))

    from app.api.ws import _serialize_event, poll_events

    with factory() as s:
        ev = (
            s.query(WorldEvent)
            .filter_by(world_id=wid, event_type="RELATIONSHIP_CHANGED")
            .one()
        )
        ser = _serialize_event(ev)
        polled = poll_events(s, wid, ev.id - 1)

    assert ser["event_type"] == "RELATIONSHIP_CHANGED"
    assert ser["payload"] == {
        "band": "acquaintance", "affection_before": 8.0, "affection_after": 10.0,
    }
    assert ser["actor_id"] == cid
    assert ser["location_id"] is None
    assert any(
        p["event_type"] == "RELATIONSHIP_CHANGED"
        and p["payload"]["band"] == "acquaintance"
        for p in polled
    ), "poll_events must surface the CHANGED event for a prior cursor"

    # REST /world/events
    r = client.get("/world/events")
    assert r.status_code == 200, r.text
    feed = r.json()
    changed = [e for e in feed if e["event_type"] == "RELATIONSHIP_CHANGED"]
    assert len(changed) == 1
    assert changed[0]["payload"] == {
        "band": "acquaintance", "affection_before": 8.0, "affection_after": 10.0,
    }
    assert changed[0]["actor_id"] == cid


# ---------- m. SOCIALIZE asymmetry regression (GUARD) ----------

def test_socialize_creation_crossing_still_emits_changed_guard():
    """GUARD (green on base): SOCIALIZE's lazy creation CAN emit
    RELATIONSHIP_CHANGED — initial_affection=48 → +2 → 50 crosses
    acquaintance → friend on the very creation. This pins that the
    slice-2 band-machinery extraction leaves the SOCIALIZE path
    behavior-identical (the documented asymmetry vs dialogue, whose
    creation never emits CHANGED). Pattern copied from
    tests/m2/test_chunk_d.py::test_relationship_band_crossing."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    settings = load_config("config/default.yaml")
    settings.social.enabled = True
    settings.social.initial_affection = 48.0
    engine = create_engine("sqlite:///:memory:")

    with Session(engine) as session:
        bootstrap(engine, settings, 42)
        world_id = settings.world.world_id
        seed_world(session, settings, world_id)

        rng = random.Random(42)
        from app.characters.generator import generate_population

        generate_population(session, settings, rng, world_id, 4)
        seed_social(session, settings, world_id, rng)
        session.commit()

        chars = (
            session.query(Character)
            .filter_by(world_id=world_id)
            .order_by(Character.id)
            .all()
        )
        # A pair with NO seeded row (seed_social seeds 2 conflict pairs;
        # 4 characters → 6 pairs, so a row-less pair always exists).
        pair = None
        for i in range(len(chars)):
            for j in range(i + 1, len(chars)):
                a, b = sorted([chars[i].id, chars[j].id])
                exists = (
                    session.query(Relationship)
                    .filter_by(world_id=world_id, character_a=a, character_b=b)
                    .first()
                )
                if exists is None:
                    pair = (chars[i], chars[j])
                    break
            if pair is not None:
                break
        assert pair is not None, "a row-less pair must exist for creation"
        c1, c2 = pair

        from app.actions.lifecycle import complete_task
        from app.db.models import CharacterTask

        task = CharacterTask(
            character_id=c1.id,
            priority=1,
            task_type="SOCIALIZE",
            target_id=c2.id,
            status="STARTED",
            source="utility",
            parameters='{"target_id": "' + c2.id + '", "affection_at_start": 48.0}',
            created_at=0,
            started_at=0,
            ends_at=30,
        )
        session.add(task)
        session.commit()

        complete_task(session, world_id, c1, task, 30, settings)

        rel_events = (
            session.query(RelationshipEvent)
            .filter_by(world_id=world_id, event_type="RELATIONSHIP_CHANGED")
            .all()
        )
        assert len(rel_events) == 1, \
            "SOCIALIZE creation-crossing still emits exactly one CHANGED"
        world_event = session.query(WorldEvent).filter_by(
            id=rel_events[0].event_id).one()
        payload = json.loads(world_event.payload)
        assert payload == {
            "band": "friend", "affection_before": 48.0, "affection_after": 50.0,
        }
        assert rel_events[0].impact == 2.0
