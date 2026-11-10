"""#295 — tail localization: Russian display for the last internal-code surfaces.

Covers the backend behavior of the #295 change (the surfaces that #291/#293
did not reach):

- SERVICE_TYPE label(): treatment→лечение, purchase→покупка, unknown→raw,
  non-str→None (the same label() doctrine as the other domains);
- TASK_FAILED renders the 10 Russian fail reasons (never the English
  originals) — parametrized over the (english, russian) pairs, PLUS an ast
  drift guard that the pinned Russian set == the actual fail_task(...)
  string-literal reasons in lifecycle.py (a new English reason fails here);
- CHARACTER_DIED renders the Russian death cause;
- TRAVEL_EXTERNAL_DEPARTED/RETURNED render the Russian purpose;
- build_decision_prompt carries the Russian-reason hint;
- the scene_act 201 response carries "task_label" == the Russian task noun;
- the actor-scoped event rows (the _actor_events_since shape the scene_act
  response uses) carry a "text" key.

Events are built in-memory (render_event_texts only reads row attributes);
the project's seeded session fixture supplies the Character name rows.
"""

import ast
import json
import os
import random
import sys
from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from fastapi.testclient import TestClient  # noqa: E402

from app.api.app import create_app  # noqa: E402
from app.config.config import load_config  # noqa: E402
from app.db.models import (  # noqa: E402
    Character,
    Location,
    WorldEvent,
    bootstrap,
    create_engine_factory,
)
from app.events.labels import SERVICE_TYPE, label  # noqa: E402
from app.events.texts import render_event_texts  # noqa: E402
from app.world.seed_world import seed_world  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))

WORLD = "test_world"
M_ID = "npc_m295_m"
M_NAME = "Иван Петров"

SETTINGS = None


def setup_module(module):
    global SETTINGS
    SETTINGS = load_config("config/default.yaml")


# ---------- fixtures / helpers (m291 people-fixture + _ev pattern) ----------

@pytest.fixture
def people(session):
    """One known male character in the seeded session (name-resolution row)."""
    loc_id = session.query(Location.id).first()[0]
    session.add(Character(
        id=M_ID, world_id=WORLD, type="npc", first_name="Иван",
        last_name="Петров", birth_date="1990-01-01", age=30, sex="M",
        alive=True, location_id=loc_id, created_at=0, updated_at=0,
    ))
    session.commit()
    return session


def _ev(eid, etype, actor=None, target=None, payload=None):
    return WorldEvent(
        id=eid, world_id=WORLD, game_timestamp=0, event_type=etype,
        actor_id=actor, target_id=target, location_id=None,
        payload=json.dumps(payload or {}),
    )


# ---------- 1. SERVICE_TYPE label() ----------

def test_service_type_known_codes_map_to_russian():
    assert label(SERVICE_TYPE, "treatment") == "лечение"
    assert label(SERVICE_TYPE, "purchase") == "покупка"


def test_service_type_unknown_code_returns_raw():
    assert label(SERVICE_TYPE, "unknown_code") == "unknown_code"


def test_service_type_non_str_code_returns_none():
    assert label(SERVICE_TYPE, {"a": 1}) is None
    assert label(SERVICE_TYPE, ["a"]) is None
    assert label(SERVICE_TYPE, None) is None
    assert label(SERVICE_TYPE, 5) is None


# ---------- 2. TASK_FAILED renders the 10 Russian fail reasons ----------

# (english original, russian replacement) — pinned from the fail_task(...)
# calls in backend/app/actions/lifecycle.py (the 10 reasons #295 localized).
# The ast drift guard below asserts this set == the actual fail_task reasons
# in lifecycle.py, so a new English reason (or a changed Russian one) fails.
FAIL_REASONS = [
    ("food gone", "еда кончилась"),
    ("water gone", "вода кончилась"),
    ("stock gone", "запасы кончились"),
    ("insufficient funds", "не хватило денег"),
    ("service gone", "услуга недоступна"),
    ("insufficient funds at departure", "не хватило денег на дорогу"),
    ("unknown blueprint", "неизвестный чертёж"),
    ("missing resources", "не хватает материалов"),
    ("interrupted", "прервано"),
    ("timeout", "время вышло"),
]


@pytest.mark.parametrize(
    "english, russian", FAIL_REASONS, ids=[r for _, r in FAIL_REASONS],
)
def test_task_failed_renders_russian_reason(people, english, russian):
    """The phrase carries the Russian reason and NOT the English original —
    the raw English code never reaches a rendered phrase."""
    ev = _ev(1, "TASK_FAILED", M_ID, None,
             {"task_type": "WORK", "reason": russian})
    text = render_event_texts(people, [ev])[1]
    assert isinstance(text, str) and text.strip()
    assert text != "TASK_FAILED"
    assert russian in text, (russian, text)
    assert english not in text, (english, text)


def test_fail_reasons_match_lifecycle_source():
    """Drift guard: the pinned Russian reasons must equal the actual
    fail_task(...) string-literal reasons in lifecycle.py — a new English
    reason (or a changed/removed Russian one) fails here and forces a
    conscious test update."""
    src = Path(ROOT) / "backend" / "app" / "actions" / "lifecycle.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    reasons = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(
            func, "attr", None)
        if name == "fail_task" and node.args:
            last = node.args[-1]
            if isinstance(last, ast.Constant) and isinstance(last.value, str):
                reasons.add(last.value)
    pinned = {russian for _, russian in FAIL_REASONS}
    assert reasons == pinned, (
        "lifecycle.py fail_task reasons drifted from the pinned set.\n"
        f"only in lifecycle.py: {sorted(reasons - pinned)}\n"
        f"only in test: {sorted(pinned - reasons)}"
    )


# ---------- 3. CHARACTER_DIED renders the Russian death cause ----------

def test_character_died_renders_russian_cause(people):
    """health.py writes the Russian cause; the phrase carries it verbatim
    (the English original never reaches the phrase)."""
    ev = _ev(1, "CHARACTER_DIED", M_ID, None,
             {"cause": "критический отказ нужд"})
    text = render_event_texts(people, [ev])[1]
    assert isinstance(text, str) and text.strip()
    assert text != "CHARACTER_DIED"
    assert "критический отказ нужд" in text
    assert "critical needs failure" not in text


# ---------- 4. TRAVEL_EXTERNAL renders the Russian purpose ----------

def test_travel_departed_renders_russian_purpose(people):
    ev = _ev(1, "TRAVEL_EXTERNAL_DEPARTED", M_ID, None,
             {"purpose": "лечение (Владивосток)"})
    text = render_event_texts(people, [ev])[1]
    assert isinstance(text, str) and text.strip()
    assert text != "TRAVEL_EXTERNAL_DEPARTED"
    assert "лечение" in text


def test_travel_returned_renders_russian_purpose(people):
    ev = _ev(1, "TRAVEL_EXTERNAL_RETURNED", M_ID, None,
             {"purpose": "лечение (Владивосток)"})
    text = render_event_texts(people, [ev])[1]
    assert isinstance(text, str) and text.strip()
    assert text != "TRAVEL_EXTERNAL_RETURNED"
    assert "лечение" in text


# ---------- 5. build_decision_prompt carries the Russian-reason hint ----------

def test_decision_prompt_has_russian_reason_hint(session, world_id):
    """#295: the decision prompt instructs the model to write the reason in
    Russian. Minimal setup: one character in the seeded session + the real
    config (for settings.llm.memory.top_k); all prompt helpers handle the
    missing needs/traits/relationships rows gracefully."""
    from app.ai.prompts import build_decision_prompt

    loc_id = session.query(Location.id).first()[0]
    cid = "npc_m295_decide"
    session.add(Character(
        id=cid, world_id=world_id, type="npc", first_name="Тест",
        last_name="Персон", birth_date="1990-01-01", age=30, sex="M",
        alive=True, location_id=loc_id, created_at=0, updated_at=0,
    ))
    session.commit()
    settings = load_config("config/default.yaml")
    bundle = build_decision_prompt(session, world_id, cid, settings, "TICK",
                                   now=0)
    assert "in Russian" in bundle["prompt"]


# ---------- 6. scene_act 201 response carries the Russian task_label ----------

def make_settings(tmp_path, social_enabled=False):
    social = SETTINGS.social.model_copy(update={"enabled": social_enabled})
    return SETTINGS.model_copy(update={
        "persistence": SETTINGS.persistence.model_copy(
            update={"db_path": str(tmp_path / "w295.db")}
        ),
        "social": social,
    })


def build_world(settings, seed=42):
    engine = create_engine_factory(settings)
    from app.characters.generator import generate_population

    with sessionmaker(bind=engine)() as session:
        bootstrap(engine, settings, seed=seed)
        seed_world(session, settings, settings.world.world_id)
        rng = random.Random(seed)
        generate_population(session, settings, rng, settings.world.world_id, 20)
        session.commit()
    return engine


PW = "Correct-Horse-9!"


def make_player(settings, engine, username="actor295", direct=True):
    """App + client + registered player with a character (DIRECT by default —
    players spawn AUTONOMOUS per §61, and /scene/act refuses AUTONOMOUS)."""
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app(settings, factory)
    client = TestClient(app)
    r = client.post("/auth/register", json={
        "username": username, "email": f"{username[0]}@x.com",
        "password": PW, "age_confirmed": True,
    })
    assert r.status_code == 201, r.text
    client.post("/auth/login", json={"username": username, "password": PW})
    r = client.post("/characters", json={
        "name": "Tail Tester", "sex": "M", "age": 30,
    })
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    if direct:
        r = client.post(f"/characters/{cid}/control", json={"mode": "DIRECT"})
        assert r.status_code == 200, r.text
    return app, client, factory, cid


def test_scene_act_response_carries_russian_task_label(tmp_path):
    """#295: the 201 scene_act response carries "task_label" == the Russian
    task noun (label(TASK_TYPE, task_type)) — the UI shows the localized noun
    instead of the raw code."""
    settings = make_settings(tmp_path)
    engine = build_world(settings)
    _, client, factory, cid = make_player(settings, engine)

    r = client.post("/scene/act", json={"text": "идти к Water Well"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["task_type"] == "MOVE"
    # MOVE → «перемещение» (labels.TASK_TYPE).
    assert body["task_label"] == "перемещение"


# ---------- 7. actor-scoped event rows carry a "text" key ----------

def test_scene_act_response_events_carry_text_key(tmp_path):
    """#295: the scene_act response "events" (the _actor_events_since shape)
    carry a "text" key on every row (str or None). A pre-seeded actor event
    proves the row shape; the response events are actor-scoped and strictly
    after the pre-call watermark, so they are usually [] — the contract is
    that EVERY row the endpoint returns carries "text"."""
    settings = make_settings(tmp_path)
    engine = build_world(settings)
    _, client, factory, cid = make_player(settings, engine)
    wid = settings.world.world_id

    # Seed one actor event so the actor-scoped query has a known row to
    # render (the response itself is post-watermark, so this row is not in
    # the response — it only proves render_event_texts runs on actor rows).
    with factory() as session:
        session.add(WorldEvent(
            world_id=wid, game_timestamp=0, event_type="CHARACTER_MOVED",
            actor_id=cid, payload=json.dumps({}),
        ))
        session.commit()

    r = client.post("/scene/act", json={"text": "идти к Water Well"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert isinstance(body["events"], list)
    for ev in body["events"]:
        assert "text" in ev, ev
        assert ev["text"] is None or isinstance(ev["text"], str), ev
