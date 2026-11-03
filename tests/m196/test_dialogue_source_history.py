"""#196 — dialogue history `source` field (GET /dialogue/{sid}).

Contract under test (issue #196):

1. Every message in the GET /dialogue/{sid} `messages` list carries a
   `source` key — a backward-compatible addition: the pre-#196 keys
   (sender / content / suggested_responses / game_timestamp) stay intact.
2. NPC reply rows: source == "llm" when the LLM seam produced the reply,
   source == "fallback" when the LLM is unavailable (llm_reply None →
   fallback template). Per ROW, not per session: a mixed history carries
   both values.
3. User rows: source == "user".
4. Migration self-heal (repo convention: create_all + additive self-heal,
   no alembic — the #205 precedent, tests/m205/test_outfit_control.py):
   a DB created by the PREVIOUS schema (dialogue_messages without the
   source column) is healed at app STARTUP (the create_app lifespan seam)
   — the column is added (DEFAULT 'fallback') and pre-existing rows are
   backfilled sender-aware: legacy user rows 'user', legacy NPC rows
   'fallback' (conservative unknown-provenance default).
5. UI: a history NPC bubble whose row says source == "fallback" shows an
   honest fallback label — byte-pinned in the renderMessages region of
   backend/app/web/app.js (the m156 pin pattern).
"""

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
    DialogueSession,
    Location,
    User,
    bootstrap,
    create_engine_factory,
)
from app.world.seed_world import seed_world  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
APP_JS = os.path.join(ROOT, "backend/app/web/app.js")

SETTINGS = None


def setup_module(module):
    global SETTINGS
    SETTINGS = load_config("config/default.yaml")


@pytest.fixture()
def world(tmp_path):
    """Seeded world + app/client + a player (m5 fixture shape; llm off)."""
    settings = SETTINGS.model_copy(update={
        "persistence": SETTINGS.persistence.model_copy(
            update={"db_path": str(tmp_path / "w.db")}
        )
    })
    engine = create_engine_factory(settings)
    from app.characters.generator import generate_population
    from app.world.social_seed import seed_social

    with sessionmaker(bind=engine)() as session:
        bootstrap(engine, settings, seed=42)
        seed_world(session, settings, settings.world.world_id)
        rng = random.Random(42)
        generate_population(session, settings, rng, settings.world.world_id, 20)
        seed_social(session, settings, settings.world.world_id, rng)
        session.commit()
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app(settings, factory)
    client = TestClient(app)
    client.post("/auth/register", json={
        "username": "m196reader",
        "email": "m196@x.com",
        "password": "password123",
        "age_confirmed": True,
    })
    client.post("/auth/login", json={"username": "m196reader", "password": "password123"})
    r = client.post("/characters", json={"name": "Mira Source", "sex": "F", "age": 28})
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
    return settings, app, client, cid, npc_id, engine, factory


def _history(client, sid):
    r = client.get(f"/dialogue/{sid}")
    assert r.status_code == 200, r.text
    return r.json()["messages"]


# ---------- 1. GET /dialogue/{sid}: every message carries `source` ----------

def test_history_every_message_has_source_key(world):
    settings, app, client, cid, npc_id, engine, factory = world
    assert settings.llm.enabled is False  # llm-off: deterministic fallback
    sid = client.post("/dialogue/start", json={"npc_id": npc_id}).json()["session_id"]
    client.post(f"/dialogue/{sid}/message", json={"content": "Сообщение раз"})
    client.post(f"/dialogue/{sid}/message", json={"content": "Сообщение два"})
    messages = _history(client, sid)
    assert len(messages) == 4
    for m in messages:
        assert "source" in m, f"message missing 'source' key: {sorted(m.keys())}"
    # Backward-compatible addition: the pre-#196 keys stay intact.
    for m in messages:
        assert {"sender", "content", "suggested_responses", "game_timestamp"} <= set(m.keys())


# ---------- 2/3. Per-row source: user / npc(llm|fallback) ----------

def test_history_user_rows_source_user(world):
    _, _, client, cid, npc_id, *_ = world
    sid = client.post("/dialogue/start", json={"npc_id": npc_id}).json()["session_id"]
    client.post(f"/dialogue/{sid}/message", json={"content": "Привет!"})
    messages = _history(client, sid)
    user_rows = [m for m in messages if m["sender"] == "user"]
    assert len(user_rows) == 1
    assert user_rows[0]["source"] == "user"


def test_history_npc_rows_source_fallback_when_llm_off(world):
    settings, _, client, cid, npc_id, *_ = world
    assert settings.llm.enabled is False
    sid = client.post("/dialogue/start", json={"npc_id": npc_id}).json()["session_id"]
    client.post(f"/dialogue/{sid}/message", json={"content": "Привет!"})
    messages = _history(client, sid)
    npc_rows = [m for m in messages if m["sender"] == "npc"]
    assert len(npc_rows) == 1
    assert npc_rows[0]["source"] == "fallback"


def test_history_npc_rows_source_llm_when_llm_produced_reply(world, monkeypatch):
    def fake_llm_reply(session, settings_, world_id, row, history, context,
                       user_message):
        return "Ответ через LLM-шов."

    monkeypatch.setattr("app.api.dialogue.llm_reply", fake_llm_reply)
    _, _, client, cid, npc_id, *_ = world
    sid = client.post("/dialogue/start", json={"npc_id": npc_id}).json()["session_id"]
    r = client.post(f"/dialogue/{sid}/message", json={"content": "Тест шва"})
    assert r.status_code == 200, r.text
    assert r.json()["source"] == "llm"  # the POST flag (m156) agrees
    messages = _history(client, sid)
    npc_rows = [m for m in messages if m["sender"] == "npc"]
    assert len(npc_rows) == 1
    assert npc_rows[0]["source"] == "llm"
    assert npc_rows[0]["content"] == "Ответ через LLM-шов."


def test_history_mixed_sources_are_per_row(world, monkeypatch):
    """A session that starts with the LLM up and loses it mid-conversation
    carries BOTH values — source is a per-row fact, not a session flag."""
    calls = {"n": 0}

    def flaky_llm_reply(session, settings_, world_id, row, history, context,
                        user_message):
        calls["n"] += 1
        return "LLM-ответ" if calls["n"] == 1 else None  # 2nd call: LLM down

    monkeypatch.setattr("app.api.dialogue.llm_reply", flaky_llm_reply)
    _, _, client, cid, npc_id, *_ = world
    sid = client.post("/dialogue/start", json={"npc_id": npc_id}).json()["session_id"]
    client.post(f"/dialogue/{sid}/message", json={"content": "Раз"})
    client.post(f"/dialogue/{sid}/message", json={"content": "Два"})
    messages = _history(client, sid)
    assert [m["sender"] for m in messages] == ["user", "npc", "user", "npc"]
    assert [m["source"] for m in messages] == ["user", "llm", "user", "fallback"]


# ---------- 4. Migration self-heal (the #205 precedent) ----------

def _dialogue_columns(engine):
    with engine.connect() as conn:
        return [
            row[1] for row in conn.execute(
                text("PRAGMA table_info(dialogue_messages)"))
        ]


def test_heal_adds_source_to_pre_196_db_and_backfills_fallback(tmp_path):
    """A DB created by the PREVIOUS schema (dialogue_messages without the
    source column) is healed at app STARTUP — the create_app lifespan seam
    (the same seam #205 uses for characters.outfit): the column is added
    (DEFAULT 'fallback') and pre-existing rows are backfilled
    sender-aware — legacy user rows 'user', legacy NPC rows 'fallback'."""
    settings = SETTINGS.model_copy(update={
        "world": SETTINGS.world.model_copy(update={"live_tick_enabled": False}),
        "persistence": SETTINGS.persistence.model_copy(
            update={"db_path": str(tmp_path / "legacy.db")}
        ),
    })
    engine = create_engine_factory(settings)
    wid = settings.world.world_id
    with sessionmaker(bind=engine)() as session:
        bootstrap(engine, settings, seed=42)
        seed_world(session, settings, wid)
        location_id = session.query(Location).order_by(Location.id).first().id
        session.add(User(
            username="m196legacy", email="m196l@x.com",
            password_hash="legacy-hash", created_at=0,
        ))
        session.add(Character(
            id="m196_legacy_player", world_id=wid,
            first_name="Легионда", last_name="Игрок",
            birth_date="1990-01-01", age=36, sex="F", alive=True,
            location_id=location_id, created_at=0, updated_at=0,
        ))
        session.add(Character(
            id="m196_legacy_npc", world_id=wid,
            first_name="Легионда", last_name="НПС",
            birth_date="1990-01-01", age=40, sex="M", alive=True,
            location_id=location_id, created_at=0, updated_at=0,
        ))
        session.commit()
        user_id = session.query(User).filter_by(username="m196legacy").one().id
        # Pre-existing dialogue rows in the OLD shape (no source column).
        session.add(DialogueSession(
            id="m196_legacy_session", world_id=wid, user_id=user_id,
            character_id="m196_legacy_player", npc_id="m196_legacy_npc",
            location_id=location_id, started_at=0, ended_at=None, context={},
        ))
        # Flush the session row first: the message rows FK-reference it and
        # the engine enforces foreign_keys=ON at INSERT time.
        session.flush()
        session.add(DialogueMessage(
            session_id="m196_legacy_session", sender="user",
            content="legacy user msg", suggested_responses=None, game_timestamp=0,
        ))
        session.add(DialogueMessage(
            session_id="m196_legacy_session", sender="npc",
            content="legacy npc msg", suggested_responses=None, game_timestamp=0,
        ))
        session.commit()
    # Simulate the pre-#196 schema: drop the column if the current model
    # already creates it (on a pre-#196 checkout it is absent already).
    if "source" in _dialogue_columns(engine):
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE dialogue_messages DROP COLUMN source"))
    assert "source" not in _dialogue_columns(engine)

    # Heal at startup: the create_app lifespan (the TestClient context
    # manager triggers it — the m152 pattern). live_tick is off: the test
    # asserts the schema, not the tick driver.
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app(settings, factory)
    with TestClient(app):
        pass
    # Idempotent: a second startup must not fail on the healed column.
    with TestClient(app):
        pass

    assert "source" in _dialogue_columns(engine)
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT sender, source FROM dialogue_messages ORDER BY id"
        )).fetchall()
    # Sender-aware backfill: the heal adds the column with DEFAULT
    # 'fallback' (all legacy rows), then rewrites legacy user rows to
    # 'user' — the sender column is authoritative for them. Legacy NPC
    # rows stay 'fallback' (conservative unknown-provenance default).
    assert rows == [("user", "user"), ("npc", "fallback")]


# ---------- 5. UI byte-pins (the m156 pattern) ----------

def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def test_ui_history_fallback_label_in_render_messages():
    js = _read(APP_JS)
    # The label logic lives in the renderMessages region (between the
    # function and the next renderSugg helper).
    rm = js.index("function renderMessages")
    end = js.index("function renderSugg")
    region = js[rm:end]
    # The condition: a bubble whose row says source == "fallback".
    assert region.count('m.source === "fallback"') == 1
    # The honest label, exactly once in the file, inside the region.
    label = '"LLM недоступен — fallback"'
    assert js.count(label) == 1
    assert rm < js.index(label) < end
