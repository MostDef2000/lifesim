"""#61 v2 — social roles as world-perceived LLM context.

Before this change, roles reached ONLY the sync dialogue context dict
(backend/app/api/dialogue.py build_context, pinned in tests/m156) — the NPC
decision prompt (backend/app/ai/prompts.py build_decision_prompt) and the
sync dialogue LLM prompt text (api/dialogue.py llm_reply) had zero role
injection. This module pins the fix:

- build_decision_prompt: a ``roles: <title, …>`` line directly after the
  identity line, built from the character's OWN derived roles (self-view,
  same semantics as GET /characters/{cid}/roles — see parity test), bounded
  to 5 titles via roles_prompt_fragment(limit=5). Absent when the character
  derives no roles → prompt BYTE-IDENTICAL to the pre-change builder output
  (pinned via SHA-256 snapshots taken on base 73590b2 BEFORE the change —
  worldgen is seed-deterministic).
- llm_reply: ``Роли: …`` (NPC, limit 3) and ``Роли собеседника: …`` (player,
  limit 3) lines after the identity line; byte-identical prompt when the
  context carries no roles. Values are REUSED from the context dict
  (build_context npc_roles/player_roles) — llm_reply never re-derives.
- Visibility parity: roles.py derive-on-read has NO visibility field — the
  derived set IS the public set (an unreported crime is a secret fact and
  never grants a role), i.e. exactly what GET /characters/{cid}/roles
  returns (app.py get_character_roles mirrors derive_roles unfiltered).
- Pins: single helper definition, single template literals, EventType count
  untouched (41 — no new events), fallback path bytes (П2) untouched.
"""

import hashlib
import inspect
import json
import os
import random
import sys

import pytest
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from fastapi.testclient import TestClient  # noqa: E402

from app.ai.memory import select_memories  # noqa: E402
from app.ai.prompts import (  # noqa: E402
    build_decision_prompt,
    roles_prompt_fragment,
)
from app.ai.transports import character_identity  # noqa: E402
from app.api.app import create_app  # noqa: E402
from app.api.dialogue import build_context, llm_reply  # noqa: E402
from app.config.config import load_config  # noqa: E402
from app.db.models import (  # noqa: E402
    Character,
    CharacterJob,
    Crime,
    DialogueSession,
    Job,
    Organization,
    OrganizationMember,
    User,
    WorldClock,
    bootstrap,
    create_engine_factory,
)
from app.events.events import EventType  # noqa: E402
from app.social.roles import derive_roles  # noqa: E402
from app.world.seed_world import seed_world  # noqa: E402
from app.world.social_seed import seed_social  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))

# Byte-identity ground truth: SHA-256 of the FULL roleless prompts, captured
# on base 73590b2 BEFORE the #61 change (same deterministic fixture as
# below: seed 42, population 20, social on, clock 8*1440, role source rows
# stripped, event "TICK", now=0, history [{user: "привет"}]). Any change to
# the roleless prompt bytes (whitespace drift included) breaks these.
DECISION_ROLELESS_SHA = (
    "2f8b98d99f87303e1231560428fd390f1971f50db09feebd519b046690ae2483"
)
DIALOGUE_ROLELESS_SHA = (
    "79148d3c114578fb174e726a2429dc31ef2077018f5e00563c34f2b413d95117"
)

PW = "Correct-Horse-9!"
SETTINGS = None


def setup_module(module):
    global SETTINGS
    SETTINGS = load_config("config/default.yaml")


def make_settings(tmp_path):
    # social.enabled=True: org membership rows are seeded by seed_social
    # (authoritative source for org roles). llm.enabled=True with the stub
    # transport (build_transport default) — llm_reply's prompt path runs,
    # deterministically, no network.
    social = SETTINGS.social.model_copy(update={"enabled": True})
    llm = SETTINGS.llm.model_copy(update={"enabled": True})
    return SETTINGS.model_copy(update={
        "persistence": SETTINGS.persistence.model_copy(
            update={"db_path": str(tmp_path / "w.db")}
        ),
        "social": social,
        "llm": llm,
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
    """Seeded world + player character + first seeded npc + app/client."""
    settings = make_settings(tmp_path)
    engine = build_world(settings)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    wid = settings.world.world_id
    with factory() as s:
        user = User(username="m61player", email="m61@x.com",
                    password_hash="x" * 20, age_confirmed=True, created_at=0)
        s.add(user)
        s.commit()
        from app.characters.player import create_player_character

        pc = create_player_character(
            s, settings, wid, user,
            name="Борис Орлов", sex="M", age=30, game_timestamp=0,
        )
        s.commit()
        npc = (
            s.query(Character)
            .filter_by(world_id=wid)
            .filter(Character.id.like("npc_%"))
            .order_by(Character.id)
            .first()
        )
        pc_id, npc_id = pc.id, npc.id
    app = create_app(settings, factory)
    client = TestClient(app)
    client.post("/auth/register", json={
        "username": "m61reader", "email": "m61r@x.com",
        "password": PW, "age_confirmed": True,
    })
    client.post("/auth/login", json={"username": "m61reader", "password": PW})
    return settings, app, client, factory, wid, pc_id, npc_id


def strip_roles(factory, world_id, cid):
    """Remove every authoritative role source row + age past the newcomer
    threshold → derive_roles must come back empty (no false positives)."""
    with factory() as s:
        char = s.get(Character, cid)
        char.occupation_id = None
        s.query(CharacterJob).filter_by(character_id=cid).delete()
        s.query(OrganizationMember).filter_by(character_id=cid).delete()
        s.query(Crime).filter_by(world_id=world_id, actor_character_id=cid).delete()
        clock = s.query(WorldClock).filter_by(world_id=world_id).first()
        clock.game_timestamp = 8 * 1440
        s.commit()
    with factory() as s:
        assert derive_roles(s, world_id, cid) == []


def many_roles(factory, world_id, cid):
    """Fabricate 6 derived roles: newcomer + occupation + org_leader + 2×
    org_member + offender (reported crime). Deterministic order per
    roles.py: newcomer, occupation, org roles by organization id, offender
    LAST — so the decision limit (5) drops exactly the offender title."""
    with factory() as s:
        char = s.get(Character, cid)
        char.created_at = 5 * 1440  # 3 days in world at clock 8*1440 → newcomer
        job = s.query(Job).order_by(Job.id).first()
        s.add(CharacterJob(character_id=cid, job_id=job.id, started_at=1440))
        char.occupation_id = job.id
        occ_title = {"Farmer": "Фермер", "Fisher": "Рыболов",
                     "Crafter": "Крафтер",
                     "Storekeeper": "Кладовщик"}.get(job.title, job.title)
        for i in range(3):
            org = Organization(
                world_id=world_id, name=f"Орг-{i}", type="cooperative",
                leader_character_id=cid if i == 0 else None,
            )
            s.add(org)
            s.flush()
            s.add(OrganizationMember(
                organization_id=org.id, character_id=cid,
                role="leader" if i == 0 else "member", joined_at=1440,
            ))
        s.add(Crime(
            world_id=world_id, crime_type="theft", actor_character_id=cid,
            location_id=char.location_id, day=1, status="reported",
            created_at=1440,
        ))
        s.commit()
    return occ_title


class _CaptureTransport:
    """Deterministic seam capture: records the prompt llm_reply sends."""

    def __init__(self, sink):
        self._sink = sink

    def complete(self, prompt, schema_hint):
        self._sink["prompt"] = prompt
        return json.dumps(
            {"reply": "ок", "intent": None, "confidence": 1.0})


# ---------- 1. roles_prompt_fragment (decision-side helper) ----------

def test_roles_prompt_fragment_contract():
    sig = inspect.signature(roles_prompt_fragment)
    assert list(sig.parameters) == ["session", "character_id", "limit"]


def test_roles_prompt_fragment_none_for_roleless_and_missing(world):
    settings, _, _, factory, wid, pc_id, npc_id = world
    strip_roles(factory, wid, pc_id)
    with factory() as s:
        assert roles_prompt_fragment(s, pc_id) is None
        # Unknown character → None as well (no invented roles line).
        assert roles_prompt_fragment(s, "who-is-this") is None


def test_roles_prompt_fragment_returns_roles_line(world):
    settings, _, _, factory, wid, pc_id, npc_id = world
    many_roles(factory, wid, pc_id)
    with factory() as s:
        titles = [r["title"] for r in derive_roles(s, wid, pc_id)]
        frag = roles_prompt_fragment(s, pc_id)
    # Default limit is 5 — the fragment caps the derived titles.
    assert len(titles) >= 6
    assert frag == "roles: " + ", ".join(titles[:5])


def test_roles_prompt_fragment_limit(world):
    settings, _, _, factory, wid, pc_id, npc_id = world
    many_roles(factory, wid, pc_id)
    with factory() as s:
        titles = [r["title"] for r in derive_roles(s, wid, pc_id)]
        assert len(titles) >= 4
        frag = roles_prompt_fragment(s, pc_id, limit=2)
    assert frag == "roles: " + ", ".join(titles[:2])


# ---------- 2. build_decision_prompt: roles line ----------

def test_decision_prompt_roles_line_after_identity(world):
    settings, _, _, factory, wid, pc_id, npc_id = world
    many_roles(factory, wid, pc_id)
    with factory() as s:
        bundle = build_decision_prompt(s, wid, pc_id, settings, "TICK", now=0)
    lines = bundle["prompt"].split("\n")
    assert lines[0].startswith("identity: ")
    with factory() as s:
        titles = [r["title"] for r in derive_roles(s, wid, pc_id)]
    # Decision limit is 5 — the line shows the first five derived titles.
    assert lines[1] == "roles: " + ", ".join(titles[:5])
    # The roles line sits between identity and the unchanged traits line.
    assert lines[2].startswith("personality: ")


def test_decision_prompt_roleless_byte_identical_to_pre_change(world):
    """No roles → prompt byte-identical to the pre-#61 builder output
    (full-prompt SHA-256 pin + structural no-drift checks)."""
    settings, _, _, factory, wid, pc_id, npc_id = world
    strip_roles(factory, wid, pc_id)
    with factory() as s:
        prompt = build_decision_prompt(
            s, wid, pc_id, settings, "TICK", now=0)["prompt"]
        # No roles line anywhere, and the roleless structure is exactly the
        # pre-change 12-line shape with no whitespace drift.
        assert "roles:" not in prompt
        lines = prompt.split("\n")
        assert len(lines) == 12
        assert lines[0].startswith("identity: ")
        assert lines[1].startswith("personality: ")
        assert lines[2].startswith("needs: ")
        assert lines[3].startswith("current_task: ")
        assert lines[4].startswith("current_location: ")
        assert lines[5] == "nearby_characters: []"
        assert lines[6] == "important_relationships: []"
        assert lines[7] == "recent_memories: []"
        assert lines[8] == "current_event: TICK"
        assert lines[9] == "available_actions: []"
        assert lines[10] == ""
        assert lines[11] == (
            'Decide the next action. Reply with strict JSON: {"decision": '
            'str, "target_character_id": str|null, "confidence": float, '
            '"reason": str}'
        )
        # Reconstruct the pre-change builder output from the same helpers —
        # must be byte-identical (this is the pin, not just the shape).
        from app.ai.prompts import (
            _goal_line,
            _needs_line,
            _traits_line,
            important_relationships,
            nearby_character_ids,
        )

        expected = "\n".join([
            f"identity: {character_identity(s, pc_id)}",
            _traits_line(s, pc_id),
            _needs_line(s, pc_id),
            _goal_line(s, pc_id),
            f"current_location: {s.get(Character, pc_id).location_id}",
            f"nearby_characters: "
            f"{json.dumps(nearby_character_ids(s, wid, pc_id))}",
            "important_relationships: "
            + json.dumps(important_relationships(s, wid, pc_id)),
            "recent_memories: "
            + json.dumps([m.summary for m in select_memories(
                s, wid, pc_id, settings.llm.memory.top_k, 0)]),
            "current_event: TICK",
            "available_actions: []",
            "",
            'Decide the next action. Reply with strict JSON: {"decision": '
            'str, "target_character_id": str|null, "confidence": float, '
            '"reason": str}',
        ])
        assert prompt == expected
    assert (
        hashlib.sha256(prompt.encode()).hexdigest() == DECISION_ROLELESS_SHA
    ), "roleless decision prompt drifted from the pre-change bytes"


def test_decision_prompt_roles_bounded_to_five(world):
    settings, _, _, factory, wid, pc_id, npc_id = world
    many_roles(factory, wid, pc_id)  # 6 derived roles
    with factory() as s:
        titles = [r["title"] for r in derive_roles(s, wid, pc_id)]
        assert len(titles) == 6
        bundle = build_decision_prompt(s, wid, pc_id, settings, "TICK", now=0)
    line = bundle["prompt"].split("\n")[1]
    assert line == "roles: " + ", ".join(titles[:5])
    # Offender is derived LAST → it is the title dropped by the limit.
    assert "Нарушитель" not in line
    assert line.count(", ") == 4


# ---------- 3. llm_reply prompt text (dialogue side) ----------

def _dialogue_setup(world, strip_npc=True, strip_player=True):
    settings, app, client, factory, wid, pc_id, npc_id = world
    if strip_npc:
        strip_roles(factory, wid, npc_id)
    if strip_player:
        strip_roles(factory, wid, pc_id)
    return settings, app, client, factory, wid, pc_id, npc_id


def _capture_llm_reply(monkeypatch, factory, settings, wid, pc_id, npc_id,
                       npc_roles=None, player_roles=None):
    """Build context (reusing build_context's derived role lists) and run
    llm_reply through a capturing transport seam."""
    with factory() as s:
        ctx = build_context(
            s, wid, s.get(Character, pc_id), s.get(Character, npc_id))
        if npc_roles is not None:
            ctx["npc_roles"] = npc_roles
        if player_roles is not None:
            ctx["player_roles"] = player_roles
        row = DialogueSession(
            id="m61sess", world_id=wid, user_id=1, character_id=pc_id,
            npc_id=npc_id, started_at=0, context={},
        )
        s.add(row)
        s.flush()
        captured = {}
        import app.ai.transports as transports

        monkeypatch.setattr(
            transports, "build_transport", lambda cfg: _CaptureTransport(
                captured))
        reply = llm_reply(
            s, settings, wid, row,
            [{"role": "user", "content": "привет"}], ctx, "привет",
        )
    assert reply == "ок"
    return captured["prompt"], ctx


def test_llm_reply_roles_lines_after_identity(monkeypatch, world):
    settings, _, _, factory, wid, pc_id, npc_id = world
    strip_roles(factory, wid, pc_id)  # player: no roles → no player line
    many_roles(factory, wid, npc_id)  # npc: seeded membership + fabrication
    prompt, ctx = _capture_llm_reply(
        monkeypatch, factory, settings, wid, pc_id, npc_id)
    lines = prompt.split("\n")
    npc_titles = [r["title"] for r in ctx["npc_roles"]]
    # npc_0001 carries a SEEDED membership («Community Council») on top of
    # the fabricated orgs/job/crime — the cap must hold regardless.
    assert len(npc_titles) >= 6
    # identity line first, then the NPC roles line (limit 3)…
    assert lines[0] == f"identity: {ctx['npc_name']}"
    assert lines[1] == "Роли: " + ", ".join(npc_titles[:3])
    assert lines[1].count(", ") == 2
    # …and NO player-roles line when the player derives nothing — the next
    # line is the unchanged persona text.
    assert lines[2].startswith("Ты — ")
    assert "Роли собеседника" not in prompt


def test_llm_reply_player_roles_line_and_order(monkeypatch, world):
    settings, _, _, factory, wid, pc_id, npc_id = world
    strip_roles(factory, wid, npc_id)
    many_roles(factory, wid, pc_id)  # player has roles (offender etc.)
    prompt, ctx = _capture_llm_reply(
        monkeypatch, factory, settings, wid, pc_id, npc_id)
    lines = prompt.split("\n")
    player_titles = [r["title"] for r in ctx["player_roles"]]
    # NPC has no roles → no «Роли:» line; the player line follows identity.
    assert "Роли: " not in prompt
    assert lines[0] == f"identity: {ctx['npc_name']}"
    assert lines[1] == "Роли собеседника: " + ", ".join(player_titles[:3])
    assert lines[2].startswith("Ты — ")


def test_llm_reply_both_roles_lines_order_and_limit(monkeypatch, world):
    settings, _, _, factory, wid, pc_id, npc_id = world
    many_roles(factory, wid, npc_id)
    many_roles(factory, wid, pc_id)
    prompt, ctx = _capture_llm_reply(
        monkeypatch, factory, settings, wid, pc_id, npc_id)
    lines = prompt.split("\n")
    npc_titles = [r["title"] for r in ctx["npc_roles"]]
    player_titles = [r["title"] for r in ctx["player_roles"]]
    assert lines[0] == f"identity: {ctx['npc_name']}"
    assert lines[1] == "Роли: " + ", ".join(npc_titles[:3])
    assert lines[2] == "Роли собеседника: " + ", ".join(player_titles[:3])
    assert lines[3].startswith("Ты — ")


def test_llm_reply_prompt_roleless_byte_identical_to_pre_change(
    monkeypatch, world,
):
    settings, _, _, factory, wid, pc_id, npc_id = world
    # No role source rows on either side → no roles lines at all.
    strip_roles(factory, wid, pc_id)
    strip_roles(factory, wid, npc_id)
    prompt, ctx = _capture_llm_reply(
        monkeypatch, factory, settings, wid, pc_id, npc_id)
    # No roles on either side → prompt must equal the pre-#61 text: no
    # roles lines, 6 lines, no whitespace drift.
    assert "Роли" not in prompt
    lines = prompt.split("\n")
    assert len(lines) == 6
    assert lines[0] == f"identity: {ctx['npc_name']}"
    assert lines[1] == (
        f"Ты — {ctx['npc_name']}, житель города. Отвечай кратко "
        "(1-3 предложения), в характерe персонажа, на русском языке."
    )
    assert lines[2] == (
        f"Контекст: местоположение {ctx['npc_location']}. "
        f"Отношение к собеседнику: доверие 0, симпатия 0."
    )
    assert lines[3] == "Воспоминания: нет"
    assert lines[4] == (
        'Последние сообщения: [{"role": "user", "content": "привет"}]'
    )
    assert lines[5] == "Сообщение игрока: привет"
    assert not prompt.endswith("\n")
    assert (
        hashlib.sha256(prompt.encode()).hexdigest() == DIALOGUE_ROLELESS_SHA
    ), "roleless dialogue prompt drifted from the pre-#61 bytes"


# ---------- 3b. Public-endpoint parity ----------

def test_dialogue_roles_line_matches_public_endpoint(world, monkeypatch):
    """The NPC's «Роли:» line surfaces exactly the roles the public read
    model returns: GET /characters/{cid}/roles returns derive_roles(...)
    unfiltered (app.py get_character_roles; roles.py has no visibility
    field — derive-on-read roles are public facts by construction), and
    llm_reply consumes build_context's npc_roles built from the same
    derive_roles call."""
    settings, app, client, factory, wid, pc_id, npc_id = world
    many_roles(factory, wid, npc_id)
    r = client.get(f"/characters/{npc_id}/roles")
    assert r.status_code == 200, r.text
    endpoint_titles = [role["title"] for role in r.json()["roles"]]
    assert endpoint_titles == [
        role["title"] for role in many_roles_read(factory, wid, npc_id)
    ]
    strip_roles(factory, wid, pc_id)
    prompt, ctx = _capture_llm_reply(
        monkeypatch, factory, settings, wid, pc_id, npc_id)
    lines = prompt.split("\n")
    assert lines[1] == "Роли: " + ", ".join(endpoint_titles[:3])


def many_roles_read(factory, world_id, cid):
    with factory() as s:
        return derive_roles(s, world_id, cid)


# ---------- 4. Implementation hygiene pins ----------

def test_single_helper_and_template_definitions():
    from app.ai import prompts
    from app.api import dialogue

    p_src = inspect.getsource(prompts)
    d_src = inspect.getsource(dialogue)
    assert p_src.count("def roles_prompt_fragment") == 1
    assert p_src.count('return "roles: " + ", ".join(titles)') == 1
    assert d_src.count("def _roles_titles") == 1
    assert d_src.count("Роли: ") == 1
    assert d_src.count("Роли собеседника: ") == 1
    # llm_reply REUSES the context lists (no re-derivation of roles there).
    assert "derive_roles" not in inspect.getsource(dialogue.llm_reply)


def test_no_new_events_and_fallback_untouched():
    # #61 adds context only — the event catalog stays at 41 (m2/m11 pins).
    assert len(EventType) == 41
    # Fallback path (П2) bytes untouched: templates and the trip tail stay.
    import app.api.dialogue as dialogue

    d_src = inspect.getsource(dialogue)
    assert d_src.count("Привет, {player}! Рад тебя видеть.") == 1
    assert d_src.count("Только что вернулся с материка: {trip}.") == 1
    assert "Роли" not in inspect.getsource(dialogue.fallback_reply)
