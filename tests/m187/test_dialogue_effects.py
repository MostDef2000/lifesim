"""#183 — dialogue → relationship effects: encounter-based affection.

The binding design (orchestrator adjudication of #183, reusing the #61 D3
basis): one relationship delta per «encounter» (not per message) from HTTP
dialogue — the EXACT SOCIALIZE affection rule, extracted to
app.social.affection.apply_socialize_affection and shared by both call
sites. Affection-only (every other Relationship scalar is byte-untouched),
game-time cooldown of DIALOGUE_ENCOUNTER_COOLDOWN_MIN = 30 game minutes
per (character_id, npc_id) pair, atomic with the dialogue journal (one
commit), identical for the LLM and the fallback path (parity by
construction — the hook runs once after the reply source resolves), no
LLM tone steering, no WorldEvents in v1 (documented simplification — the
m158 zero-events pin stays valid), consent/romantic_interest untouched.

Pinned here:

- parity: LLM-stubbed path (monkeypatch app.api.dialogue.llm_reply, the
  m156 seam) vs fallback path — SAME delta, SAME response field;
- once per window: two quick exchanges → one delta, second says honestly
  {"applied": false};
- formula edges: 60 → +2.0; -40 → -5.0 (conflict path WITHOUT the
  SOCIALIZE side effects: no stress, no CONFLICT event, no AI request);
  clamp at 100 and -100 (honest 0 applied delta);
- lazy-create: no Relationship row → created with
  settings.social.initial_affection + delta, canonical ordering a < b;
- affection-only: trust/respect/fear/anger/attraction/romantic_interest/
  familiarity byte-identical;
- response field shape on both applied and not-applied;
- shared-helper assertion: lifecycle SOCIALIZE and the dialogue encounter
  both go through app.social.affection.apply_socialize_affection;
- UI pins: the #chat-rel-note literal (construction ==1) + its CSS block
  in the #63 warm section, carry-forward m63/m156/m175 pins intact.
"""

import inspect
import os
import random
import sys

import pytest
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from fastapi.testclient import TestClient  # noqa: E402

from app.api.app import create_app  # noqa: E402
from app.config.config import load_config  # noqa: E402
from app.db.models import (  # noqa: E402
    AiRequest,
    Character,
    CharacterHealth,
    Relationship,
    WorldClock,
    WorldEvent,
    bootstrap,
    create_engine_factory,
)
from app.world.seed_world import seed_world  # noqa: E402
from app.world.social_seed import seed_social  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
APP_JS = os.path.join(ROOT, "backend/app/web/app.js")
APP_CSS = os.path.join(ROOT, "backend/app/web/style.css")

PW = "Correct-Horse-9!"
SETTINGS = None


def setup_module(module):
    global SETTINGS
    SETTINGS = load_config("config/default.yaml")


def make_settings(tmp_path):
    # social.enabled=True (the shared SOCIALIZE rule operates on real
    # social settings); llm stays off by default (fallback path is the
    # baseline; the parity test monkeypatches the llm seam for the stub).
    social = SETTINGS.social.model_copy(update={"enabled": True})
    return SETTINGS.model_copy(update={
        "persistence": SETTINGS.persistence.model_copy(
            update={"db_path": str(tmp_path / "w.db")}
        ),
        "social": social,
    })


def make_world(tmp_path, user="b183", seed=42):
    """Seeded world + app/client + a player character + one npc id."""
    settings = make_settings(tmp_path)
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
            "others": {k: getattr(r, k) for k in (
                "trust", "respect", "fear", "anger", "attraction",
                "romantic_interest", "familiarity")},
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


# ---------- 1. Parity: LLM-stubbed path == fallback path ----------

def test_parity_llm_stub_vs_fallback(tmp_path, monkeypatch):
    """Both reply paths resolve the SAME encounter effect: identical
    affection delta and the same relationship_effect field (the hook runs
    once, after the source resolves — parity by construction)."""
    settings, factory, client, cid, npc_ids = make_world(tmp_path, "b183a")
    wid = settings.world.world_id
    npc_llm, npc_fb = npc_ids[0], npc_ids[1]

    import app.api.dialogue as dialogue_mod

    original_llm_reply = dialogue_mod.llm_reply
    monkeypatch.setattr(
        dialogue_mod, "llm_reply",
        lambda *a, **k: "Ответ через LLM-шов.",
    )

    for npc in (npc_llm, npc_fb):
        _set_affection(factory, wid, cid, npc, 60.0)

    body_llm = _msg(client, _start(client, npc_llm))
    # Restore the seam: llm.enabled=false → the original returns None →
    # the deterministic fallback answers the second exchange.
    monkeypatch.setattr(dialogue_mod, "llm_reply", original_llm_reply)
    body_fb = _msg(client, _start(client, npc_fb))

    assert body_llm["source"] == "llm"
    assert body_fb["source"] == "fallback"
    eff_llm, eff_fb = body_llm["relationship_effect"], body_fb["relationship_effect"]
    assert eff_llm == {"applied": True, "affection_delta": 2.0, "affection_after": 62.0}
    assert eff_fb == eff_llm
    assert _get_rel(factory, wid, cid, npc_llm)["affection"] == 62.0
    assert _get_rel(factory, wid, cid, npc_fb)["affection"] == 62.0


# ---------- 2. Once per window ----------

def test_second_quick_exchange_applies_nothing(tmp_path):
    """Two exchanges inside one cooldown window → exactly one delta; the
    second response honestly reports {"applied": False} (cooldown)."""
    settings, factory, client, cid, npc_ids = make_world(tmp_path, "b183b")
    wid = settings.world.world_id
    npc = npc_ids[0]
    _set_affection(factory, wid, cid, npc, 60.0)

    sid = _start(client, npc)
    first = _msg(client, sid)
    assert first["relationship_effect"] == {
        "applied": True, "affection_delta": 2.0, "affection_after": 62.0,
    }
    second = _msg(client, sid)
    assert second["relationship_effect"] == {"applied": False}
    assert _get_rel(factory, wid, cid, npc)["affection"] == 62.0
    # A different pair is unaffected — the window is per (character, npc).
    other = npc_ids[1]
    third = _msg(client, _start(client, other))
    assert third["relationship_effect"]["applied"] is True


def test_boundary_message_exactly_30_minutes_old_releases_window(tmp_path):
    """Pins the STRICT lower bound: a message exactly 30 game minutes old
    (game_timestamp == now_ts - 30) is OUT of the window — the window
    condition is `>` (strict), not `>=`. A +30 advance releases the
    encounter; the next exchange applies again."""
    settings, factory, client, cid, npc_ids = make_world(tmp_path, "b183c")
    wid = settings.world.world_id
    npc = npc_ids[0]
    _set_affection(factory, wid, cid, npc, 60.0)

    sid = _start(client, npc)
    first = _msg(client, sid)
    assert first["relationship_effect"]["applied"] is True

    # Advance exactly 30 game minutes — the prior exchange sits exactly on
    # the window edge (now_ts - 30). It is OUT (strict >), so the delta
    # applies again.
    _advance_clock(factory, wid, 30)
    edge = _msg(client, sid)
    assert edge["relationship_effect"]["applied"] is True
    assert _get_rel(factory, wid, cid, npc)["affection"] == 64.0


# ---------- 3. Formula edges ----------

def test_formula_edges_and_no_conflict_side_effects(tmp_path):
    """60 → +2.0 (normal); -40 → -5.0 (conflict path) with NONE of the
    SOCIALIZE conflict side effects (no stress, no CONFLICT event, no AI
    request); clamp at 100 and -100 — applied delta honestly 0."""
    settings, factory, client, cid, npc_ids = make_world(tmp_path, "b183c")
    wid = settings.world.world_id
    npc_norm, npc_conf, npc_top, npc_bottom = npc_ids[:4]

    # Normal path: 60 → 62.
    _set_affection(factory, wid, cid, npc_norm, 60.0)
    eff = _msg(client, _start(client, npc_norm))["relationship_effect"]
    assert eff["affection_delta"] == 2.0 and eff["affection_after"] == 62.0

    # Conflict path (affection < -30): -40 → -45, WITHOUT side effects.
    _set_affection(factory, wid, cid, npc_conf, -40.0)
    pair_ids = [cid, npc_conf]
    with factory() as s:
        health_before = sorted(
            (h.character_id, h.stress)
            for h in s.query(CharacterHealth).filter(
                CharacterHealth.character_id.in_(pair_ids))
        )
        events_before = s.query(WorldEvent).filter_by(world_id=wid).count()
        ai_before = s.query(AiRequest).count()
    eff = _msg(client, _start(client, npc_conf))["relationship_effect"]
    assert eff["affection_delta"] == -5.0 and eff["affection_after"] == -45.0
    with factory() as s:
        health_after = sorted(
            (h.character_id, h.stress)
            for h in s.query(CharacterHealth).filter(
                CharacterHealth.character_id.in_(pair_ids))
        )
        events_after = s.query(WorldEvent).filter_by(world_id=wid).count()
        ai_after = s.query(AiRequest).count()
    assert health_after == health_before, "no stress from the dialogue conflict path"
    assert events_after == events_before, "no WorldEvents from dialogue (v1)"
    assert ai_after == ai_before, "no LLM decide enqueue from dialogue"

    # Clamp at +100: normal path attempted, row does not move, delta 0.
    _set_affection(factory, wid, cid, npc_top, 100.0)
    eff = _msg(client, _start(client, npc_top))["relationship_effect"]
    assert eff == {"applied": True, "affection_delta": 0.0, "affection_after": 100.0}

    # Clamp at -100: conflict path attempted, row does not move, delta 0.
    _set_affection(factory, wid, cid, npc_bottom, -100.0)
    eff = _msg(client, _start(client, npc_bottom))["relationship_effect"]
    assert eff == {"applied": True, "affection_delta": 0.0, "affection_after": -100.0}


# ---------- 4. Lazy-create + canonical ordering ----------

def test_lazy_create_with_initial_affection_canonical_order(tmp_path):
    """No Relationship row → the encounter creates it: affection =
    settings.social.initial_affection + delta, canonical a < b ordering
    respected regardless of who is the player."""
    settings, factory, client, cid, npc_ids = make_world(tmp_path, "b183d")
    wid = settings.world.world_id
    assert settings.social.initial_affection == 0.0
    assert _get_rel(factory, wid, cid, npc_ids[0]) is None

    eff = _msg(client, _start(client, npc_ids[0]))["relationship_effect"]
    assert eff == {"applied": True, "affection_delta": 2.0, "affection_after": 2.0}

    rel = _get_rel(factory, wid, cid, npc_ids[0])
    assert rel is not None
    assert rel["affection"] == 2.0
    a, b = sorted([cid, npc_ids[0]])
    assert (rel["character_a"], rel["character_b"]) == (a, b)
    assert rel["updated_at"] == (
        factory().query(WorldClock).filter_by(world_id=wid).first().game_timestamp
    )


# ---------- 5. Affection-only ----------

def test_affection_only_other_scalars_byte_identical(tmp_path):
    """Across applied AND not-applied exchanges: trust/respect/fear/anger/
    attraction/romantic_interest/familiarity stay byte-identical."""
    settings, factory, client, cid, npc_ids = make_world(tmp_path, "b183e")
    wid = settings.world.world_id
    npc = npc_ids[0]
    _set_affection(factory, wid, cid, npc, 60.0)

    before = _get_rel(factory, wid, cid, npc)
    sid = _start(client, npc)
    _msg(client, sid)                           # applied
    _msg(client, sid)                           # not applied (cooldown)
    _advance_clock(factory, wid, 60)
    _msg(client, sid)                           # applied again
    after = _get_rel(factory, wid, cid, npc)

    assert before["others"] == after["others"]
    assert (before["character_a"], before["character_b"]) == (
        after["character_a"], after["character_b"])
    assert after["affection"] == 64.0


# ---------- 6. Response field shape ----------

def test_response_field_shape_backward_compatible(tmp_path):
    """Old keys stay; relationship_effect is additive with exactly the two
    documented shapes."""
    settings, factory, client, cid, npc_ids = make_world(tmp_path, "b183f")
    wid = settings.world.world_id
    npc = npc_ids[0]
    _set_affection(factory, wid, cid, npc, 0.0)

    sid = _start(client, npc)
    body = _msg(client, sid)
    assert set(body.keys()) >= {
        "npc_reply", "suggested_responses", "session_id", "source",
        "relationship_effect",
    }
    assert body["relationship_effect"] == {
        "applied": True, "affection_delta": 2.0, "affection_after": 2.0,
    }
    body2 = _msg(client, sid)
    assert body2["relationship_effect"] == {"applied": False}
    # History endpoint untouched (journal intact).
    hist = client.get(f"/dialogue/{sid}").json()
    assert len(hist["messages"]) == 4


# ---------- 7. Shared helper: both call sites ----------

def test_helper_returns_rule_delta_even_at_clamp(tmp_path):
    """Direct pin of the split semantics: the helper's RETURN is the rule
    delta (+2.0/-5.0 — the social_event_integrity contract for the
    SOCIAL_INTERACTION payload) even when the clamp ate the actual write;
    the row itself stays clamped at ±100. The honest 0 lives in the
    dialogue response (pinned in the HTTP tests above)."""
    from app.social.affection import apply_socialize_affection

    settings = make_settings(tmp_path)
    from app.db.models import create_engine_factory

    engine = create_engine_factory(settings)
    with sessionmaker(bind=engine)() as s:
        bootstrap(engine, settings, seed=42)
        wid = settings.world.world_id
        from app.characters.generator import generate_population
        from app.world.seed_world import seed_world

        seed_world(s, settings, wid)
        generate_population(s, settings, random.Random(42), wid, 2)
        s.commit()
        c1, c2 = s.query(Character).order_by(Character.id).all()[:2]

        rel = Relationship(
            world_id=wid, character_a=sorted([c1.id, c2.id])[0],
            character_b=sorted([c1.id, c2.id])[1], affection=100.0, updated_at=0)
        s.add(rel)
        s.commit()

        assert apply_socialize_affection(
            s, wid, c1.id, c2.id, 10, settings) == 2.0  # RULE delta
        s.flush()
        assert rel.affection == 100.0  # clamped write, row did not move


def test_socialize_and_dialogue_use_the_same_helper():
    """Import-level assertion: the SOCIALIZE branch and the dialogue
    encounter both route the affection mutation through
    app.social.affection.apply_socialize_affection — one rule, two call
    sites (parity is by construction, not by copy)."""
    import app.actions.lifecycle as lifecycle
    import app.api.dialogue as dialogue
    from app.social.affection import apply_socialize_affection

    lifecycle_src = inspect.getsource(lifecycle)
    dialogue_src = inspect.getsource(dialogue)
    assert "from app.social.affection import apply_socialize_affection" \
        in lifecycle_src
    assert "apply_socialize_affection(" in lifecycle_src, \
        "lifecycle SOCIALIZE must call the shared helper"
    assert "from app.social.affection import apply_socialize_affection" \
        in dialogue_src
    assert "apply_socialize_affection(" in dialogue_src, \
        "the dialogue encounter must call the shared helper"
    # The SOCIALIZE branch no longer carries its own inline delta math.
    assert "rel.affection + 2.0" not in lifecycle_src
    assert "rel.affection - 5.0" not in lifecycle_src
    # The dialogue module is where apply_dialogue_encounter lives.
    assert callable(dialogue.apply_dialogue_encounter)
    assert callable(apply_socialize_affection)


# ---------- 8. UI pins ----------

def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def test_ui_rel_note_pins():
    js = _read(APP_JS)
    css = _read(APP_CSS)
    # New literal, exactly one construction (NOT a reuse of any pinned
    # chat literal — chat-error / sugg / chat-status keep their counts).
    assert js.count('"chat-rel-note"') == 1
    # The notice is fed ONLY from the server's relationship_effect field.
    assert js.count("r.relationship_effect") == 1
    # «Симпатия +2»-style text: sign + integer, honest 0 on clamp.
    assert js.count("relNote.textContent = `Симпатия ${") == 1
    # Applied → shown; not applied → cleared + hidden; a FAILED send also
    # hides the note (no stale «Симпатия +2» outliving its reply — #183).
    assert js.count("relNote.hidden = false;") == 1
    assert js.count("relNote.hidden = true;") == 2
    # The node sits next to the relationship widget in the header info.
    assert js.count("subLine, rolesLine, relBox, relNote),") == 1
    # CSS: the warm-scope rule lives inside the #63 section, before the
    # #175 tail (append-only convention intact).
    sec = css.split("issue #63: chat screen warm-family rework", 1)[1].split(
        "/* #175: connection banner", 1)[0]
    assert css.count(".chat-shell #chat-rel-note {") == 1
    assert ".chat-shell #chat-rel-note" in sec
    # Carry-forward: no new token mirror, banner still the last rule.
    assert css.count("--w-line:") == 4
    assert css.rstrip().endswith(".conn-banner.hidden { display: none; }")
