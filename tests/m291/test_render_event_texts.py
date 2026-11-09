"""#291 — human-readable Russian texts for world events.

Contract under test (backend/app/events/texts.py, display-time rendering
from the stored row — no schema change):

- render_event_texts(session, events) -> {WorldEvent.id: str};
- every one of the 45 EventType values renders a non-empty human phrase
  (never the raw code); actor-bearing types name the actor;
- unknown event_type string -> the raw code (fallback);
- a template that raises -> the raw code (fallback), never an exception;
- malformed payload data (invalid JSON, JSON null/list, None, missing
  keys) never raises — the phrase just loses its clause (historical rows
  pre-date payload keys);
- a character id that resolves to no Character row renders as «кто-то» —
  a raw id never reaches the phrase;
- PARTNERSHIP_DECLARED prefers the payload name snapshots
  (initiator_name/partner_name) over id resolution;
- gendered verbs follow Character.sex ("M"/"F", case-insensitive,
  anything else reads masculine);
- ONE batched Character query per page (no N+1).

Events are built in-memory (render_event_texts only reads row attributes);
the project's seeded session fixture supplies the Character name rows.
"""

import json

import pytest
from sqlalchemy import event as sa_event

from app.db.models import Character, Location, WorldEvent
from app.events.events import EventType
from app.events.texts import render_event_texts

WORLD = "test_world"

M_ID = "npc_m291_m"
F_ID = "npc_m291_f"
F2_ID = "npc_m291_f2"  # lowercase sex "f" — case-insensitivity
X_ID = "npc_m291_x"    # unknown sex — default masculine
M_NAME = "Иван Петров"
F_NAME = "Мария Смирнова"


@pytest.fixture
def people(session):
    """Known characters in the seeded session: male, female, lowercase-f,
    and an unknown-sex row (the gender-agreement vectors)."""
    loc_id = session.query(Location.id).first()[0]
    rows = [
        (M_ID, "Иван", "Петров", "M"),
        (F_ID, "Мария", "Смирнова", "F"),
        (F2_ID, "Ольга", "Кузнецова", "f"),
        (X_ID, "Пётр", "Волков", "X"),
    ]
    for cid, fn, ln, sex in rows:
        session.add(Character(
            id=cid, world_id=WORLD, type="npc", first_name=fn,
            last_name=ln, birth_date="1990-01-01", age=30, sex=sex,
            alive=True, location_id=loc_id, created_at=0, updated_at=0,
        ))
    session.commit()
    return session


def _ev(eid, etype, actor=None, target=None, payload=None, raw_payload=None):
    return WorldEvent(
        id=eid, world_id=WORLD, game_timestamp=0, event_type=etype,
        actor_id=actor, target_id=target, location_id=None,
        payload=(raw_payload if raw_payload is not None
                 else json.dumps(payload or {})),
    )


# (event_type, actor_id, target_id, payload, expected substring) — one
# representative row per EventType value (45 total, pinned below).
CASES = [
    ("CHARACTER_CREATED", M_ID, None,
     {"name": M_NAME, "age": 30, "job": "farmer"}, "Новый житель"),
    ("CHARACTER_MOVED", M_ID, None, {}, "переехал"),
    ("CHARACTER_DIED", M_ID, None, {"cause": "illness"}, "умер"),
    ("ITEM_TRANSFERRED", None, None,
     {"object_type": "food_bread", "quantity": 2,
      "from_owner": {"character": M_ID}, "to_owner": {"character": F_ID}},
     "Передача"),
    ("ITEM_CONSUMED", M_ID, None,
     {"object_type": "food_bread", "quantity": 1}, "ест"),
    ("SALARY_PAID", M_ID, None, {"amount": 40}, "зарплату"),
    ("SUPPLY_ARRIVED", None, F_ID,
     {"resource_key": "water", "amount": 50}, "Поставка"),
    ("PURCHASE", M_ID, None, {"price": 10}, "купил"),
    ("TASK_FAILED", M_ID, None,
     {"task_type": "repair", "reason": "no parts"}, "не справился"),
    ("TASK_COMPLETED", M_ID, None, {"task_type": "repair"}, "справился"),
    ("CONSTRUCTED", M_ID, None, {"object_type": "house"}, "построил"),
    ("SOCIAL_INTERACTION", M_ID, F_ID,
     {"target_id": F_ID, "duration": 30}, "пообщались"),
    ("RELATIONSHIP_CHANGED", M_ID, F_ID,
     {"band": "friend", "affection_before": 10, "affection_after": 20},
     "отношение"),
    ("RELATIONSHIP_FORMED", M_ID, F_ID,
     {"target_id": F_ID, "affection_delta": 2.0, "affection_after": 2.0},
     "стали ближе"),
    ("CONFLICT", M_ID, F_ID, {}, "Ссора"),
    ("ELECTION", M_ID, None,
     {"organization_id": 1, "score": 12, "previous_leader_id": F_ID},
     "возглавил"),
    ("ORG_DUES", None, None,
     {"organization_id": 1, "total_collected": 100,
      "paid_members": 5, "skipped_members": 2}, "Взносы"),
    ("ORG_FEAST", M_ID, None,
     {"organization_id": 1, "cost": 50, "boosted_members": [F_ID]}, "Пир"),
    ("LAW_ENACTED", M_ID, None,
     {"law_key": "no_theft", "organization_id": 1}, "закон"),
    ("LAW_VIOLATION", M_ID, None,
     {"law_key": "no_theft", "fine": 20, "fine_paid": False}, "нарушил"),
    ("RECONCILIATION", M_ID, None,
     {"pair": [M_ID, F_ID], "mediator_id": M_ID}, "Примирение"),
    ("ROLE_ACQUIRED", M_ID, None,
     {"character_id": M_ID, "title": "leader"}, "получил роль"),
    ("ROLE_REVOKED", M_ID, None,
     {"character_id": M_ID, "title": "leader"}, "лишился"),
    ("PARTNERSHIP_DECLARED", M_ID, F_ID,
     {"initiator_id": M_ID, "partner_id": F_ID,
      "initiator_name": M_NAME, "partner_name": F_NAME}, "объявили пару"),
    ("MESSAGE_SENT", M_ID, None, {"to": F_ID}, "Сообщение"),
    ("AI_DECISION", M_ID, None,
     {"decision": "EAT", "reason": "hunger"}, "решение"),
    ("CONTROL_CHANGED", M_ID, None,
     {"from": "AUTONOMOUS", "to": "DIRECT"}, "режим управления"),
    ("GOAL_QUEUED", M_ID, None, {"goal_type": "build_house"}, "поставил цель"),
    ("VISUAL_ASSET_CREATED", M_ID, None,
     {"asset_type": "portrait"}, "визуальный ассет"),
    ("MEMORY_CREATED", M_ID, None, {"memory_type": "episodic"}, "запомнил"),
    ("MEMORY_CONSOLIDATED", None, None,
     {"groups": 3, "removed": 5, "character_ids": [M_ID]}, "Обобщение памяти"),
    ("TRAVEL_EXTERNAL_DEPARTED", M_ID, None, {"purpose": "trade"}, "уехал"),
    ("TRAVEL_EXTERNAL_RETURNED", M_ID, None,
     {"purpose": "trade", "items": {"food_bread": 2}}, "вернулся"),
    ("WEATHER_CHANGED", None, None,
     {"temperature": 15.4, "wind": 3.2, "precipitation": True},
     "Погода изменилась"),
    ("OBJECT_BURNING", None, None, {"object_type": "house"}, "Пожар"),
    ("OBJECT_BURNED", None, None, {"object_type": "house"}, "Сгорело"),
    ("OBJECT_DESTROYED", None, None, {"object_type": "bridge"}, "Разрушено"),
    ("MARKET_LISTED", M_ID, None, {"price": 15}, "выставил"),
    ("MARKET_SOLD", M_ID, None, {"price": 15, "seller": F_ID}, "продавец"),
    ("POWER_OUTAGE", None, None,
     {"location_id": 3, "demand": 10, "supply": 5, "storm": True},
     "Отключение света"),
    ("CRIME_COMMITED", M_ID, None,
     {"type": "theft", "object_type": "food_bread"}, "украл"),
    ("CRIME_REPORTED", M_ID, None,
     {"crime_type": "theft", "suspect": F_ID, "witnesses": [M_ID]}, "сообщил"),
    ("FINE_PAID", M_ID, None, {"amount": 20, "officer": F_ID}, "штраф"),
    ("ARRESTED", M_ID, None, {"release_day": 5, "officer": F_ID}, "арестован"),
    ("RELEASED", M_ID, None, {"officer": F_ID}, "освобождён"),
]


# ---------- 0. the table itself covers the full catalog ----------

def test_table_covers_all_45_event_types():
    """The parametrized table is the coverage proof: it must name every
    EventType value exactly once (45) — a dropped/renamed row fails here."""
    values = {e.value for e in EventType}
    assert len(values) == 45
    assert {c[0] for c in CASES} == values
    assert len(CASES) == len({c[0] for c in CASES}), "no duplicate rows"


# ---------- 1. every known type renders a human phrase ----------

@pytest.mark.parametrize(
    "etype, actor, target, payload, substring",
    CASES, ids=[c[0] for c in CASES],
)
def test_known_type_renders_human_text(
    people, etype, actor, target, payload, substring
):
    texts = render_event_texts(people, [_ev(1, etype, actor, target, payload)])
    text = texts[1]
    assert isinstance(text, str) and text.strip(), (etype, text)
    assert text != etype, f"{etype} rendered as the raw code: {text!r}"
    assert substring in text, (etype, text)
    if actor:
        name = M_NAME if actor == M_ID else F_NAME
        assert name in text, (etype, text)


# ---------- 2. fallbacks ----------

def test_unknown_event_type_returns_raw_code(people):
    ev = _ev(1, "TOTALLY_UNKNOWN_TYPE", M_ID, None, {})
    assert render_event_texts(people, [ev])[1] == "TOTALLY_UNKNOWN_TYPE"


def test_template_exception_falls_back_to_raw_code(people, monkeypatch):
    """A template that raises must not propagate — the row falls back to
    the raw code (the historical-row safety net)."""
    import app.events.texts as texts_mod

    def boom(ev, p, names):
        raise RuntimeError("injected template failure")

    monkeypatch.setitem(texts_mod.TEMPLATES, "CHARACTER_MOVED", boom)
    ev = _ev(1, "CHARACTER_MOVED", M_ID, None, {})
    assert render_event_texts(people, [ev])[1] == "CHARACTER_MOVED"


@pytest.mark.parametrize(
    "raw", ["{not json", "null", "[]", None],
    ids=["invalid-json", "json-null", "json-list", "none"],
)
def test_malformed_payload_never_raises(people, raw):
    """Invalid/absent payload data: no exception, and a KNOWN type still
    renders its phrase (the clause is lost, the type is NOT demoted to the
    raw code) — the actor clause survives payload loss."""
    ev = _ev(1, "CHARACTER_MOVED", M_ID, None, raw_payload=raw)
    text = render_event_texts(people, [ev])[1]
    assert isinstance(text, str) and text.strip()
    assert text != "CHARACTER_MOVED"
    assert M_NAME in text


def test_all_none_fields_still_render_string(people):
    """No actor, no target, empty payload: still a string, never the raw
    code — the unresolved subject reads «кто-то»."""
    ev = _ev(1, "CHARACTER_MOVED", None, None, raw_payload="{}")
    text = render_event_texts(people, [ev])[1]
    assert isinstance(text, str) and text.strip()
    assert text != "CHARACTER_MOVED"
    assert "кто-то" in text


# ---------- 3. name resolution ----------

def test_unresolved_actor_renders_kto_to_never_raw_id(people):
    ev = _ev(1, "CHARACTER_MOVED", "npc_ghost_42", None, {})
    text = render_event_texts(people, [ev])[1]
    assert "кто-то" in text
    assert "npc_ghost_42" not in text


def test_unresolved_target_renders_kto_to(people):
    ev = _ev(1, "SOCIAL_INTERACTION", M_ID, "npc_ghost_7",
             {"target_id": "npc_ghost_7", "duration": 10})
    text = render_event_texts(people, [ev])[1]
    assert M_NAME in text
    assert "кто-то" in text
    assert "npc_ghost_7" not in text


def test_partnership_uses_payload_name_snapshots(people):
    """PARTNERSHIP_DECLARED carries name snapshots: with UNRESOLVABLE ids
    the snapshots are what reach the phrase (and the raw ids never do)."""
    ev = _ev(1, "PARTNERSHIP_DECLARED", "npc_ghost_a", "npc_ghost_b",
             {"initiator_id": "npc_ghost_a", "partner_id": "npc_ghost_b",
              "initiator_name": "Анна", "partner_name": "Борис"})
    text = render_event_texts(people, [ev])[1]
    assert "Анна" in text and "Борис" in text
    assert "npc_ghost_a" not in text and "npc_ghost_b" not in text


# ---------- 4. gender agreement ----------

def test_gender_female_feminine(people):
    text = render_event_texts(
        people, [_ev(1, "TASK_COMPLETED", F_ID, None, {"task_type": "repair"})]
    )[1]
    assert "справилась" in text
    assert "справился" not in text


def test_gender_male_masculine(people):
    text = render_event_texts(
        people, [_ev(1, "TASK_COMPLETED", M_ID, None, {"task_type": "repair"})]
    )[1]
    assert "справился" in text
    assert "справилась" not in text


def test_gender_case_insensitive_lowercase_f(people):
    text = render_event_texts(
        people, [_ev(1, "TASK_COMPLETED", F2_ID, None, {"task_type": "repair"})]
    )[1]
    assert "справилась" in text
    assert "справился" not in text


def test_gender_unknown_sex_defaults_masculine(people):
    text = render_event_texts(
        people, [_ev(1, "TASK_COMPLETED", X_ID, None, {"task_type": "repair"})]
    )[1]
    assert "справился" in text
    assert "справилась" not in text


# ---------- 5. retroactivity (historical rows) ----------

def test_historical_row_minimal_payload_still_renders(people):
    """A row written by an old emitter (payload missing the keys the
    current emitters write) still gets a human text at display time —
    not the raw code."""
    ev = _ev(1, "TASK_COMPLETED", M_ID, None, raw_payload="{}")
    text = render_event_texts(people, [ev])[1]
    assert isinstance(text, str) and text.strip()
    assert text != "TASK_COMPLETED"
    assert M_NAME in text


# ---------- 6. one batched name query per page ----------

def test_one_batched_character_query_per_page(people):
    """A page of 5 events issues exactly ONE characters SELECT — the
    batched-lookup contract (no N+1 per event)."""
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    engine = people.bind
    sa_event.listen(engine, "before_cursor_execute", capture)
    try:
        events = [_ev(i, "CHARACTER_MOVED", M_ID, None, {})
                  for i in range(1, 6)]
        texts = render_event_texts(people, events)
    finally:
        sa_event.remove(engine, "before_cursor_execute", capture)
    assert len(texts) == 5
    char_selects = [s for s in statements if "characters" in s.lower()]
    assert len(char_selects) == 1, statements


# ---------- 7. review-fix regression pins ----------
# Each test below pins a specific behavior that the review pass fixed in
# backend/app/events/texts.py. Against the pre-fix code these assertions
# fail (the row demotes to the raw code, or a literal «None» leaks, or the
# subject reads «кто-то» where the actor's name should render, or the
# plural/snapshot/chunk contract is violated) — see the per-test docstrings.

def test_unhashable_payload_value_renders_phrase(people):
    """A truthy NON-STRING payload id (a list / a dict) must not demote the
    row to the raw code. The two halves pin the two fallback shapes:
    - MESSAGE_SENT "to" has NO column fallback — a list-valued "to" reads
      «кто-то» (the raw element never reaches the phrase);
    - ROLE_ACQUIRED "character_id" DOES fall through to the actor column —
      a dict-valued character_id renders the ACTOR's name (NOT «кто-то»).
    Pre-fix, the dict won the `or` over the column id and the subject read
    «кто-то» (or the row demoted to the raw code for unhashable ids)."""
    # MESSAGE_SENT with a list-valued "to"
    ev = _ev(1, "MESSAGE_SENT", M_ID, None, {"to": ["x"]})
    text = render_event_texts(people, [ev])[1]
    assert isinstance(text, str) and text.strip()
    assert text != "MESSAGE_SENT"
    assert "Сообщение" in text
    assert M_NAME in text
    assert "кто-то" in text
    assert "x" not in text  # the raw list element never reaches the phrase

    # ROLE_ACQUIRED with a dict-valued character_id: the payload id is
    # non-str, so it falls through to the actor column — the actor's name
    # renders (NOT «кто-то», NOT the raw code).
    ev2 = _ev(2, "ROLE_ACQUIRED", M_ID, None, {"character_id": {"a": 1}})
    text2 = render_event_texts(people, [ev2])[2]
    assert isinstance(text2, str) and text2.strip()
    assert text2 != "ROLE_ACQUIRED"
    assert M_NAME in text2
    assert "кто-то" not in text2
    assert "a" not in text2  # the raw dict key never reaches the phrase


def test_reconciliation_malformed_ids_no_repr(people):
    """RECONCILIATION with a non-str mediator_id falls back to the ACTOR
    name; a non-str pair entry keeps the template's "?" placeholder — never
    «кто-то», never a Python repr. The resolvable pair entry still renders
    its name."""
    ev = _ev(1, "RECONCILIATION", M_ID, None,
             {"mediator_id": {"a": 1}, "pair": [F_ID, {"x": 1}]})
    text = render_event_texts(people, [ev])[1]
    assert isinstance(text, str) and text.strip()
    assert text != "RECONCILIATION"
    assert "Примирение" in text
    assert M_NAME in text      # mediator falls back to the actor name
    assert F_NAME in text      # the resolvable pair entry renders
    assert "?" in text         # the malformed pair entry keeps the placeholder
    assert "кто-то" not in text
    assert "None" not in text
    assert "{" not in text     # no Python repr garbage


def test_role_malformed_character_name_dropped(people):
    """ROLE_ACQUIRED with a non-str character_name and NO resolvable ids:
    the dict-valued name snapshot is dropped — the subject reads «кто-то»,
    never a Python repr."""
    ev = _ev(1, "ROLE_ACQUIRED", "npc_ghost_role", None,
             {"character_name": {"a": 1}})
    text = render_event_texts(people, [ev])[1]
    assert isinstance(text, str) and text.strip()
    assert text != "ROLE_ACQUIRED"
    assert "кто-то получил новую роль" in text
    assert "None" not in text
    assert "{" not in text     # no Python repr garbage


@pytest.mark.parametrize(
    "payload", [{}, {"from": "DIRECT"}],
    ids=["empty", "from-only"],
)
def test_control_changed_missing_fields_no_none(people, payload):
    """CONTROL_CHANGED with a missing from/to must not leak the literal
    «None» — the phrase reads «изменён режим управления»."""
    ev = _ev(1, "CONTROL_CHANGED", M_ID, None, payload)
    text = render_event_texts(people, [ev])[1]
    assert isinstance(text, str) and text.strip()
    assert text != "CONTROL_CHANGED"
    assert "None" not in text
    assert "изменён режим управления" in text
    assert M_NAME in text


def test_role_missing_title_no_none(people):
    """ROLE_ACQUIRED/ROLE_REVOKED with no title AND no role must not leak
    «None» — the phrase reads «получил новую роль» / «лишился роли»."""
    ev_a = _ev(1, "ROLE_ACQUIRED", M_ID, None, {"character_id": M_ID})
    text_a = render_event_texts(people, [ev_a])[1]
    assert "None" not in text_a
    assert "новую роль" in text_a
    assert M_NAME in text_a

    ev_r = _ev(2, "ROLE_REVOKED", M_ID, None, {"character_id": M_ID})
    text_r = render_event_texts(people, [ev_r])[2]
    assert "None" not in text_r
    assert "лишился роли" in text_r
    assert M_NAME in text_r


def test_infinity_amount_drops_clause(people):
    """A numeric payload field that is Infinity (json emits the bare
    «Infinity» token, json.loads reads it back as inf) must drop its clause —
    never crash, never demote the row to the raw code."""
    # SALARY_PAID amount=Infinity -> the «: N монет» clause is dropped
    ev = _ev(1, "SALARY_PAID", M_ID, None, {"amount": float("inf")})
    text = render_event_texts(people, [ev])[1]
    assert isinstance(text, str) and text.strip()
    assert text != "SALARY_PAID"
    assert "зарплату" in text
    assert M_NAME in text
    assert "монет" not in text  # монета/монеты/монет all contain «монет»

    # WEATHER_CHANGED temperature=Infinity -> the «N°C» clause is dropped
    ev2 = _ev(2, "WEATHER_CHANGED", None, None, {"temperature": float("inf")})
    text2 = render_event_texts(people, [ev2])[2]
    assert isinstance(text2, str) and text2.strip()
    assert text2 != "WEATHER_CHANGED"
    assert "Погода изменилась" in text2
    assert "°C" not in text2


@pytest.mark.parametrize(
    "amount, word",
    [(1, "монета"), (2, "монеты"), (5, "монет"),
     (11, "монет"), (21, "монета"), (101, "монета")],
    ids=["1", "2", "5", "11", "21", "101"],
)
def test_plural_money_forms(people, amount, word):
    """Russian plural agreement on the money clause: 1 монета / 2 монеты /
    5 монет / 11 монет / 21 монета / 101 монета."""
    ev = _ev(1, "SALARY_PAID", M_ID, None, {"amount": amount})
    text = render_event_texts(people, [ev])[1]
    assert f"{amount} {word}" in text, text


@pytest.mark.parametrize(
    "groups, word",
    [(1, "группа"), (3, "группы"), (5, "групп")],
    ids=["1", "3", "5"],
)
def test_plural_groups(people, groups, word):
    """MEMORY_CONSOLIDATED plural agreement: 1 группа / 3 группы / 5 групп."""
    ev = _ev(1, "MEMORY_CONSOLIDATED", None, None, {"groups": groups})
    text = render_event_texts(people, [ev])[1]
    assert f"+{groups} {word}" in text, text


def test_partnership_snapshot_precedence(people):
    """PARTNERSHIP_DECLARED: payload name snapshots take PRECEDENCE over
    DB-resolved names — with BOTH resolvable ids AND snapshots, the snapshot
    strings win (the existing pin only covers the unresolvable fallback)."""
    ev = _ev(1, "PARTNERSHIP_DECLARED", M_ID, F_ID,
             {"initiator_id": M_ID, "partner_id": F_ID,
              "initiator_name": "Анна", "partner_name": "Борис"})
    text = render_event_texts(people, [ev])[1]
    assert "Анна" in text and "Борис" in text
    # the DB-resolved names must NOT appear (the snapshots won)
    assert M_NAME not in text
    assert F_NAME not in text
    assert "Иван" not in text and "Мария" not in text
    assert "Петров" not in text and "Смирнова" not in text


def test_chunked_name_load_select_count_no_exception(people):
    """A page whose payload references MORE than _NAME_CHUNK (400) character
    ids must still render without error — the lookup is chunked (sorted)
    into multiple SELECTs and unioned. 450 ids -> exactly 2 SELECTs
    (400 + 50). This pins the CHUNKED SELECT COUNT and that no exception is
    raised; it does NOT assert name resolution (MEMORY_CONSOLIDATED does not
    render names in its phrase). The small-page pin in §6 asserts exactly 1
    SELECT."""
    ids = [f"npc_chunk_{i:04d}" for i in range(450)]
    # include the 4 seeded (resolvable) characters so the chunked SELECTs
    # return real rows across the chunks, not just empty results
    ids[0] = M_ID
    ids[1] = F_ID
    ids[2] = F2_ID
    ids[3] = X_ID
    ev = _ev(1, "MEMORY_CONSOLIDATED", None, None,
             {"groups": 2, "character_ids": ids})
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    engine = people.bind
    sa_event.listen(engine, "before_cursor_execute", capture)
    try:
        texts = render_event_texts(people, [ev])
    finally:
        sa_event.remove(engine, "before_cursor_execute", capture)
    text = texts[1]
    assert isinstance(text, str) and text.strip()
    assert text != "MEMORY_CONSOLIDATED"
    assert "Обобщение памяти" in text
    char_selects = [s for s in statements if "characters" in s.lower()]
    assert len(char_selects) == 2, statements
