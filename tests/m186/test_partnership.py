"""#182 — partnership mechanic: explicit mutual declaration («объявить пару»).

The player character proposes; the NPC agreement is a DETERMINISTIC rule
(canonical-pair Relationship.affection >= PARTNERSHIP_MIN_AFFECTION = 25,
both adults, neither side already partnered) — never an LLM call. On
agreement the Partnership row + PARTNERSHIP_DECLARED event + role
projections for BOTH characters commit in ONE transaction; derive-on-read
roles then honestly reflect «Партнёр {name}» on both sides.

Covers (binding work order):
- schema: version pin 0.15.0, tables 44, partnerships table with the
  canonical constraint pattern (CheckConstraint a<b, UniqueConstraint
  (world_id, character_a_id, character_b_id) — copied from Relationship);
- derive: partnership row → BOTH characters derive the partner role with
  title «Партнёр {имя}» and distinct sources («в паре с {полное имя}»);
  removing the row removes the role (derive stays truth); a character
  without a partnership has no partner role;
- endpoint happy path: affection 60 → accepted:true, row created,
  PARTNERSHIP_DECLARED in the feed with both names, ROLE_ACQUIRED ×2 with
  cause=partnership_declared + causal id — all in one transaction
  (rollback test: injected failure → nothing persists);
- decline: affection 10 → accepted:false with a reason, NO row, NO events;
  missing canonical Relationship row → affection 0 → decline;
- already partnered (either side) → 422 naming the side, no events;
- non-adult target → 422 validation error (NOT a decline);
- player target → 422 validation error (mirrors /dialogue/start);
- idempotency: the same pair cannot declare twice (422, no new rows);
- EventType count 44 + membership pins;
- UI pins: button literal, accepted/declined handling, hidden when
  «Партнёр» is in the roles list, all carry-forward chat pins intact.
"""
import os
import random
import subprocess
import sys

import pytest
from sqlalchemy import inspect as sqla_inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from fastapi.testclient import TestClient  # noqa: E402

from app.api.app import create_app  # noqa: E402
from app.config.config import load_config  # noqa: E402
from app.db.models import (  # noqa: E402
    Character,
    Partnership,
    Relationship,
    WorldEvent,
    bootstrap,
    create_engine_factory,
)
from app.events.events import EventType  # noqa: E402
from app.social.roles import derive_roles  # noqa: E402
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
    # social.enabled=True: membership rows are seeded (m156 pattern); the
    # partnership endpoint itself does not depend on the social flag, but
    # the world must be seeded the same way the UI exercises it.
    social = SETTINGS.social.model_copy(update={"enabled": True})
    return SETTINGS.model_copy(update={
        "persistence": SETTINGS.persistence.model_copy(
            update={"db_path": str(tmp_path / "w.db")}
        ),
        "social": social,
    })


def build_world(settings, seed=42):
    engine = create_engine_factory(settings)
    with sessionmaker(bind=engine)() as session:
        bootstrap(engine, settings, seed=seed)
        seed_world(session, settings, settings.world.world_id)
        rng = random.Random(seed)
        from app.characters.generator import generate_population

        generate_population(session, settings, rng, settings.world.world_id, 20)
        seed_social(session, settings, settings.world.world_id, rng)
        session.commit()
    return engine


@pytest.fixture()
def world(tmp_path):
    """Seeded world + app/client + a player character + one NPC target."""
    settings = make_settings(tmp_path)
    engine = build_world(settings)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app(settings, factory)
    client = TestClient(app)
    client.post("/auth/register", json={
        "username": "p182", "email": "p182@x.com",
        "password": PW, "age_confirmed": True,
    })
    client.post("/auth/login", json={"username": "p182", "password": PW})
    r = client.post("/characters", json={"name": "Борис Орлов", "sex": "M", "age": 30})
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    with factory() as s:
        npc = (
            s.query(Character)
            .filter_by(world_id=settings.world.world_id)
            .filter(Character.id.like("npc_%"))
            .order_by(Character.id)
            .first()
        )
        npc_id = npc.id
        npc2_id = (
            s.query(Character)
            .filter_by(world_id=settings.world.world_id)
            .filter(Character.id.like("npc_%"))
            .filter(Character.id != npc_id)
            .order_by(Character.id)
            .first()
            .id
        )
    return settings, app, client, factory, cid, npc_id, npc2_id, engine


def set_affection(factory, world_id, id1, id2, value):
    """Upsert the canonical Relationship row (character_a < character_b)."""
    a, b = sorted([id1, id2])
    with factory() as s:
        row = (
            s.query(Relationship)
            .filter_by(world_id=world_id, character_a=a, character_b=b)
            .first()
        )
        if row is None:
            s.add(Relationship(
                world_id=world_id, character_a=a, character_b=b,
                affection=value, updated_at=0,
            ))
        else:
            row.affection = value
        s.commit()


def make_partnership(factory, world_id, id1, id2):
    a, b = sorted([id1, id2])
    with factory() as s:
        s.add(Partnership(
            world_id=world_id, character_a_id=a, character_b_id=b, created_at=0,
        ))
        s.commit()


def npc_name(factory, npc_id):
    with factory() as s:
        c = s.get(Character, npc_id)
        return c.first_name, f"{c.first_name} {c.last_name}".strip()


# ---------- 1. schema ----------

def test_schema_version_and_tables(world):
    settings, _, _, factory, _, _, _, engine = world
    from sqlalchemy import text

    with factory() as s:
        version = s.execute(
            text("SELECT value FROM schema_meta WHERE key='version'")
        ).scalar()
    assert version == "0.15.0"
    tables = set(sqla_inspect(engine).get_table_names())
    # 020 P2: character_appearances (plan.md:26) — 44 -> 45, deliberate.
    # 020 P3: visual_references — 45 -> 46, deliberate.
    assert len(tables) == 46, f"Expected 46 tables, found {len(tables)}"
    assert "partnerships" in tables


def test_partnership_canonical_constraints(world):
    settings, _, _, factory, _, npc_id, npc2_id, engine = world
    wid = settings.world.world_id
    # CheckConstraint character_a_id < character_b_id (Relationship pattern).
    with factory() as s:
        s.add(Partnership(
            world_id=wid, character_a_id=npc2_id, character_b_id=npc_id,
            created_at=0,
        ))
        with pytest.raises(IntegrityError):
            s.flush()
        s.rollback()
    # UniqueConstraint (world_id, character_a_id, character_b_id).
    make_partnership(factory, wid, npc_id, npc2_id)
    with factory() as s:
        s.add(Partnership(
            world_id=wid, character_a_id=npc_id, character_b_id=npc2_id,
            created_at=1,
        ))
        with pytest.raises(IntegrityError):
            s.flush()
        s.rollback()


def test_partnership_db_level_singleton(world):
    """#182 review F2: each character appears in at most ONE partnership
    row — enforced by the unique indexes, not just endpoint checks. Two
    concurrent proposals to the same NPC produce DIFFERENT canonical pairs
    (the pair UniqueConstraint never fires); the per-side indexes do."""
    settings, _, _, factory, cid, npc_id, npc2_id, engine = world
    wid = settings.world.world_id
    with factory() as s:
        npc3_id = (
            s.query(Character)
            .filter_by(world_id=wid)
            .filter(Character.id.like("npc_%"))
            .filter(Character.id.notin_([npc_id, npc2_id]))
            .order_by(Character.id)
            .first()
            .id
        )

    make_partnership(factory, wid, cid, npc_id)  # (A,B) — ok
    with factory() as s:
        assert s.query(Partnership).filter_by(world_id=wid).count() == 1

    # (A,C): A is already a side of the existing row → unique index fires.
    with factory() as s:
        x, y = sorted([cid, npc2_id])
        s.add(Partnership(
            world_id=wid, character_a_id=x, character_b_id=y, created_at=1,
        ))
        with pytest.raises(IntegrityError):
            s.flush()
        s.rollback()

    # (D,B): B is already a side of the existing row → unique index fires.
    with factory() as s:
        x, y = sorted([npc2_id, npc_id])
        s.add(Partnership(
            world_id=wid, character_a_id=x, character_b_id=y, created_at=2,
        ))
        with pytest.raises(IntegrityError):
            s.flush()
        s.rollback()

    # (C,D): different pair, both characters free → ok.
    make_partnership(factory, wid, npc2_id, npc3_id)
    with factory() as s:
        assert s.query(Partnership).filter_by(world_id=wid).count() == 2


# ---------- 2. derive ----------

def test_derive_partner_role_both_sides(world):
    settings, _, _, factory, cid, npc_id, npc2_id, engine = world
    wid = settings.world.world_id
    player_first, player_full = npc_name(factory, cid)
    npc_first, npc_full = npc_name(factory, npc_id)

    # Without a row: no partner role on either side.
    assert "partner" not in [r["id"] for r in derive_roles_factory(factory, wid, cid)]
    assert "partner" not in [r["id"] for r in derive_roles_factory(factory, wid, npc_id)]

    make_partnership(factory, wid, cid, npc_id)
    player_roles = {r["id"]: r for r in derive_roles_factory(factory, wid, cid)}
    npc_roles = {r["id"]: r for r in derive_roles_factory(factory, wid, npc_id)}
    assert "partner" in player_roles and "partner" in npc_roles
    p_role = player_roles["partner"]
    n_role = npc_roles["partner"]
    assert p_role["title"] == f"Партнёр {npc_first}"
    assert n_role["title"] == f"Партнёр {player_first}"
    # Distinct, honest sources — each viewer sees the OTHER full name.
    assert p_role["source"] == f"в паре с {npc_full}"
    assert n_role["source"] == f"в паре с {player_full}"
    assert p_role["source"] != n_role["source"]
    assert p_role["reason"] == "взаимное объявление пары"
    assert n_role["reason"] == "взаимное объявление пары"

    # Removing the row removes the role (derive stays truth).
    with factory() as s:
        s.query(Partnership).filter_by(world_id=wid).delete()
        s.commit()
    assert "partner" not in [r["id"] for r in derive_roles_factory(factory, wid, cid)]
    assert "partner" not in [r["id"] for r in derive_roles_factory(factory, wid, npc_id)]


def derive_roles_factory(factory, world_id, cid):
    with factory() as s:
        return derive_roles(s, world_id, cid)


# ---------- 3. endpoint: happy path + atomicity ----------

def test_endpoint_happy_path(world):
    settings, app, client, factory, cid, npc_id, _, engine = world
    wid = settings.world.world_id
    set_affection(factory, wid, cid, npc_id, 60.0)

    r = client.post(f"/characters/{npc_id}/partnership")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["accepted"] is True
    assert body["partner"]["id"] == npc_id

    _, npc_full = npc_name(factory, npc_id)
    with factory() as s:
        rows = s.query(Partnership).filter_by(world_id=wid).all()
        assert len(rows) == 1
        a, b = sorted([cid, npc_id])
        assert (rows[0].character_a_id, rows[0].character_b_id) == (a, b)

        decl = s.query(WorldEvent).filter_by(
            world_id=wid, event_type="PARTNERSHIP_DECLARED").all()
        assert len(decl) == 1
        import json as _json

        payload = _json.loads(decl[0].payload)
        assert payload["initiator_id"] == cid
        assert payload["partner_id"] == npc_id
        assert payload["initiator_name"] and payload["partner_name"] == npc_full
        assert payload == {k: payload[k] for k in (
            "initiator_id", "initiator_name", "partner_id", "partner_name")}

        acquired = s.query(WorldEvent).filter_by(
            world_id=wid, event_type="ROLE_ACQUIRED").all()
        assert len(acquired) == 2
        assert {ev.actor_id for ev in acquired} == {cid, npc_id}
        for ev in acquired:
            p = _json.loads(ev.payload)
            assert p["cause"] == "partnership_declared"
            assert p["cause_event_id"] == decl[0].id
            assert p["role"] == "partner"

        # Derive reflects the role for BOTH characters now.
        assert "partner" in [r["id"] for r in derive_roles(s, wid, cid)]
        assert "partner" in [r["id"] for r in derive_roles(s, wid, npc_id)]


def test_endpoint_atomic_rollback(world, monkeypatch):
    """One transaction: an injected failure after the row+event land rolls
    EVERYTHING back (close() without commit discards)."""
    settings, app, client, factory, cid, npc_id, _, engine = world
    wid = settings.world.world_id
    set_affection(factory, wid, cid, npc_id, 60.0)

    import app.social.roles as roles_mod

    def boom(*a, **kw):
        raise RuntimeError("injected failure")

    monkeypatch.setattr(roles_mod, "project_role_events", boom)
    with pytest.raises(RuntimeError):
        client.post(f"/characters/{npc_id}/partnership")

    with factory() as s:
        assert s.query(Partnership).filter_by(world_id=wid).count() == 0
        assert s.query(WorldEvent).filter_by(
            world_id=wid, event_type="PARTNERSHIP_DECLARED").count() == 0
        assert s.query(WorldEvent).filter_by(
            world_id=wid, event_type="ROLE_ACQUIRED").count() == 0


# ---------- 4. declines and validation errors ----------

def test_endpoint_decline_low_affection(world):
    settings, app, client, factory, cid, npc_id, _, engine = world
    wid = settings.world.world_id
    set_affection(factory, wid, cid, npc_id, 10.0)

    r = client.post(f"/characters/{npc_id}/partnership")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["accepted"] is False
    assert body["reason"]

    with factory() as s:
        assert s.query(Partnership).filter_by(world_id=wid).count() == 0
        assert s.query(WorldEvent).filter_by(
            world_id=wid, event_type="PARTNERSHIP_DECLARED").count() == 0
        assert s.query(WorldEvent).filter_by(
            world_id=wid, event_type="ROLE_ACQUIRED").count() == 0


def test_endpoint_decline_missing_relationship_row(world):
    """No canonical Relationship row → affection treated as 0 → decline."""
    settings, app, client, factory, cid, npc_id, _, engine = world
    wid = settings.world.world_id
    with factory() as s:
        a, b = sorted([cid, npc_id])
        assert s.query(Relationship).filter_by(
            world_id=wid, character_a=a, character_b=b).first() is None

    r = client.post(f"/characters/{npc_id}/partnership")
    assert r.status_code == 200
    assert r.json()["accepted"] is False
    with factory() as s:
        assert s.query(Partnership).filter_by(world_id=wid).count() == 0


def test_already_partnered_either_side(world):
    settings, app, client, factory, cid, npc_id, npc2_id, engine = world
    wid = settings.world.world_id
    set_affection(factory, wid, cid, npc_id, 60.0)
    set_affection(factory, wid, cid, npc2_id, 60.0)

    # Player partners with npc_id.
    assert client.post(f"/characters/{npc_id}/partnership").json()["accepted"] is True

    # (a) initiator side already partnered → 422 naming the INITIATOR.
    r = client.post(f"/characters/{npc2_id}/partnership")
    assert r.status_code == 422
    assert "initiator" in r.json()["detail"]

    # (b) target side already partnered → 422 naming the TARGET
    # (fresh player is free; npc_id is taken).
    client.post("/auth/register", json={
        "username": "p182b", "email": "p182b@x.com",
        "password": PW, "age_confirmed": True,
    })
    client.post("/auth/login", json={"username": "p182b", "password": PW})
    r = client.post("/characters", json={"name": "Анна Ветрова", "sex": "F", "age": 28})
    assert r.status_code == 201, r.text
    cid2 = r.json()["id"]
    set_affection(factory, wid, cid2, npc_id, 60.0)
    r = client.post(f"/characters/{npc_id}/partnership")
    assert r.status_code == 422
    assert "target" in r.json()["detail"]

    # No new facts: one partnership, one declaration event.
    with factory() as s:
        assert s.query(Partnership).filter_by(world_id=wid).count() == 1
        assert s.query(WorldEvent).filter_by(
            world_id=wid, event_type="PARTNERSHIP_DECLARED").count() == 1


def test_non_adult_target_is_validation_error(world):
    """Non-adult target → 422 validation error (П4 convention age >= 18),
    NOT a deterministic decline."""
    settings, app, client, factory, cid, npc_id, _, engine = world
    wid = settings.world.world_id
    set_affection(factory, wid, cid, npc_id, 60.0)
    with factory() as s:
        npc = s.get(Character, npc_id)
        npc.age = 16
        s.commit()

    r = client.post(f"/characters/{npc_id}/partnership")
    assert r.status_code == 422
    assert "adult" in r.json()["detail"]
    with factory() as s:
        assert s.query(Partnership).filter_by(world_id=wid).count() == 0


def test_player_target_validation_error(world):
    """Target is another player's character → 422 (mirrors /dialogue/start)."""
    settings, app, client, factory, cid, npc_id, _, engine = world
    wid = settings.world.world_id
    client.post("/auth/register", json={
        "username": "p182c", "email": "p182c@x.com",
        "password": PW, "age_confirmed": True,
    })
    client.post("/auth/login", json={"username": "p182c", "password": PW})
    r = client.post("/characters", json={"name": "Игорь Тихий", "sex": "M", "age": 40})
    assert r.status_code == 201, r.text
    cid2 = r.json()["id"]

    # The ORIGINAL player proposes to the second player's character.
    set_affection(factory, wid, cid, cid2, 60.0)
    client.post("/auth/login", json={"username": "p182", "password": PW})
    r = client.post(f"/characters/{cid2}/partnership")
    assert r.status_code == 422
    assert "player character" in r.json()["detail"]

    # Self-target is a distinct 422.
    r = client.post(f"/characters/{cid}/partnership")
    assert r.status_code == 422
    assert "yourself" in r.json()["detail"]


def test_idempotency_same_pair_cannot_declare_twice(world):
    settings, app, client, factory, cid, npc_id, _, engine = world
    wid = settings.world.world_id
    set_affection(factory, wid, cid, npc_id, 60.0)

    assert client.post(f"/characters/{npc_id}/partnership").json()["accepted"] is True
    r = client.post(f"/characters/{npc_id}/partnership")
    assert r.status_code == 422
    with factory() as s:
        assert s.query(Partnership).filter_by(world_id=wid).count() == 1
        assert s.query(WorldEvent).filter_by(
            world_id=wid, event_type="PARTNERSHIP_DECLARED").count() == 1


# ---------- 5. EventType catalog pins ----------

def test_event_type_count_44_and_membership():
    assert len(EventType) == 44
    assert "PARTNERSHIP_DECLARED" in EventType.__members__
    # #61 v2 D2 neighbors stay.
    assert "ROLE_ACQUIRED" in EventType.__members__
    assert "ROLE_REVOKED" in EventType.__members__


# ---------- 6. UI pins ----------

def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def test_ui_button_and_outcome_pins():
    js = _read(APP_JS)
    # The proposal button: one construction site, one literal, warm scope.
    assert js.count('id: "chat-partner-btn"') == 1
    assert js.count('"Объявить пару"') == 1
    # Endpoint call + accepted/declined handling.
    assert js.count("${targetNpc}/partnership") == 1
    assert js.count("if (r.accepted) {") == 1
    assert js.count('"Вы теперь пара"') == 1
    # Accepted → chat re-render (roles line re-derives via the SAME single
    # fetch — the m156 literal stays count 1).
    assert js.count("renderChat(curSession, locName);") == 1
    assert js.count("api(`/characters/${npcSel.value}/roles`)") == 1
    # Button hides when the derive list contains the partner role — gated
    # on the role ID (review F4: stable id, not a fragile title prefix).
    assert js.count('(r.roles || []).some((x) => x.id === "partner")') == 1
    # F1: the selection survives re-renders (persisted + restored).
    assert js.count("S.chatNpcId = npcSel.value;") == 1
    assert js.count("if (S.chatNpcId && S.chatNpcs.includes(S.chatNpcId)) {") == 1
    # Error path: ONE inline chat-error construction site (#63 pattern).
    assert js.count('class: "chat-error"') == 1
    assert js.count('log.querySelectorAll(".chat-error").forEach(n => n.remove())') == 1


def test_ui_carry_forward_pins():
    js = _read(APP_JS)
    # m63/m156/m170-era literals stay at their pinned counts.
    assert js.count('class: "grid chat-shell"') == 1
    assert js.count("VISUAL_FALLBACK") == 3  # const + 2 uses
    assert js.count("typingBubble.remove();") == 2
    assert js.count("renderChat(null, locName);") == 1
    assert js.count('class: "sugg", type: "button"') == 1
    assert js.count('el("span", { class: "sugg"') == 1
    assert js.count('"chat-status"') == 1
    assert js.count('"chat-roles"') == 1


def test_ui_css_pins():
    css = _read(APP_CSS)
    assert css.count(".chat-shell #chat-partner-btn {") == 1
    # m175 append-only tail + m63 section counts hold.
    assert css.rstrip().endswith(".conn-banner.hidden { display: none; }")
    assert css.count("--w-line:") == 4
    sec = css.split("issue #63: chat screen warm-family rework", 1)[1].split(
        "/* #175: connection banner", 1)[0]
    assert sec.count("border-radius: 50%") == 2
    assert css.count("issue #63: chat screen warm-family rework") == 1


def test_app_js_syntax_gate():
    result = subprocess.run(
        ["node", "--check", "backend/app/web/app.js"],
        capture_output=True, text=True, check=False, cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr
