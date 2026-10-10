"""Block 1 — #88 [A6] Dialogue alpha + #89 [A7] Social Roles v1 (one PR).

The issue bodies are the specs of record (gh issue view 88 / 89; the
1-issue-1-PR rule is owner-overridden for this block, tracked in #83
issuecomment-6089997917).

Covers:

#89 Social Roles v1:
- derive_roles: deterministic derive-on-read from authoritative rows ONLY
  (created_at / character_jobs.started_at / organization_members.joined_at /
  crimes rows) — no LLM, no new tables, no invented roles.
- Catalog: newcomer (< 7 days in world), occupation (job title), org_leader /
  org_member (membership.role), offender (reported|resolved crime).
- EXCLUDED: partner — no authoritative marriage/partnership source exists in
  the schema; Relationship.romantic_interest is a sentiment scalar, not a
  partnership fact ("unsupported/fuzzy role is not invented").
- No-false-positive: absent source row → role absent (newcomer after
  threshold; unreported crime → offender absent; no job → no occupation).
- Distinct from internal traits (role ids never overlap trait keys).
- Traceability = source-row timestamps: every role source row carries its own
  authoritative timestamp (created_at/started_at/joined_at/day), so a role
  change IS a source-row change — pinned here (derive-on-read has no event
  log; the module documents this explicitly).
- GET /characters/{cid}/roles: public read model (any authenticated user),
  401 unauth / 404 unknown / 200 {roles:[{id,title,source,reason}]}.
- Dialogue context consumes roles (build_context gains npc_roles /
  player_roles; the llm seam receives them).
- Profile hook «Роли: …».

#88 Dialogue alpha:
- POST /dialogue/{sid}/message response gains "source": "llm"|"fallback"
  (backward-compatible addition — no other API change).
- Fallback path works with llm disabled; stubbed llm seam returns source llm.
- Dialogue is journal-only: NO relationship mutation from plain chat (no fake
  delta) — Relationship rows byte-identical before/after N messages.
- suggested_responses present as shortcuts.
- UI byte-pins: free-text input always visible; suggested reply click FILLS
  the input and NEVER sends; «Печатает…» + ticking elapsed-seconds counter;
  error path keeps the typed draft (restore, clear only after success);
  retry hint + fallback visibility; roles in profile; canonical NPC portrait
  via /visual/characters/{cid}/portrait with graceful placeholder.
- Every A3/A4/A5 carry-forward pin intact (innerHTML 0, hash-profile 3,
  scene-view 3, «К карте» 2, media blocks byte-exact, …).
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
    Character,
    CharacterJob,
    CharacterTrait,
    Crime,
    Job,
    Organization,
    OrganizationMember,
    Relationship,
    WorldClock,
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
    # social.enabled=True: org membership rows (leader/member) are seeded by
    # seed_social — the authoritative source for org roles (#89). Default
    # config ships social off; m156 needs the populated membership table.
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
    """Seeded world (memberships included) + app/client + a player."""
    settings = make_settings(tmp_path)
    engine = build_world(settings)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app(settings, factory)
    client = TestClient(app)
    client.post("/auth/register", json={
        "username": "b1reader", "email": "b1@x.com",
        "password": PW, "age_confirmed": True,
    })
    client.post("/auth/login", json={"username": "b1reader", "password": PW})
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
    return settings, app, client, factory, cid, npc_id, engine


def _roles_of(factory, world_id, cid):
    from app.social.roles import derive_roles

    with factory() as s:
        return derive_roles(s, world_id, cid)


def _set_clock(factory, world_id, ts):
    with factory() as s:
        clock = s.query(WorldClock).filter_by(world_id=world_id).first()
        clock.game_timestamp = ts
        s.commit()


# ---------- 1. derive_roles: catalog + no-false-positive (#89) ----------

def test_newcomer_present_at_world_start_and_absent_after_threshold(world):
    settings, _, _, factory, cid, npc_id, _ = world
    wid = settings.world.world_id
    # World clock at 0, npc created_at=0 → 0 days in world < 7 → newcomer.
    roles = _roles_of(factory, wid, npc_id)
    ids = [r["id"] for r in roles]
    assert "newcomer" in ids
    newcomer = next(r for r in roles if r["id"] == "newcomer")
    assert newcomer["title"] == "Новичок"
    assert newcomer["source"] == "недавно на острове"
    assert newcomer["reason"]
    # No false positive after the threshold (8 full days in world).
    _set_clock(factory, wid, 8 * 1440)
    assert "newcomer" not in [r["id"] for r in _roles_of(factory, wid, npc_id)]
    # Boundary: exactly 7 days → no longer a newcomer (deterministic edge).
    _set_clock(factory, wid, 7 * 1440)
    assert "newcomer" not in [r["id"] for r in _roles_of(factory, wid, npc_id)]
    # Still under the threshold → present (6 days).
    _set_clock(factory, wid, 6 * 1440)
    assert "newcomer" in [r["id"] for r in _roles_of(factory, wid, npc_id)]


def test_occupation_role_from_job_and_absent_without_job(world):
    settings, _, _, factory, cid, npc_id, _ = world
    wid = settings.world.world_id
    roles = _roles_of(factory, wid, npc_id)
    occ = [r for r in roles if r["id"] == "occupation"]
    assert len(occ) == 1
    with factory() as s:
        job = s.get(Job, s.get(Character, npc_id).occupation_id)
        # source names the authoritative job title
        assert job.title in occ[0]["source"]
        assert occ[0]["title"]
        assert occ[0]["reason"]
    # Known seeded title maps to the documented Russian role title.
    crafter = (
        factory().query(Job).filter_by(title="Crafter").order_by(Job.id).first()
    )
    if crafter is not None:
        with factory() as s:
            npc = s.get(Character, npc_id)
            npc.occupation_id = crafter.id
            for cj in s.query(CharacterJob).filter_by(character_id=npc_id):
                cj.job_id = crafter.id
            s.commit()
        roles = _roles_of(factory, wid, npc_id)
        occ = next(r for r in roles if r["id"] == "occupation")
        assert occ["title"] == "Крафтер"
    # No job rows → occupation role absent (no false positive).
    with factory() as s:
        npc = s.get(Character, npc_id)
        npc.occupation_id = None
        s.query(CharacterJob).filter_by(character_id=npc_id).delete()
        s.commit()
    assert "occupation" not in [r["id"] for r in _roles_of(factory, wid, npc_id)]


def test_org_leader_and_member_roles_from_membership(world):
    settings, _, _, factory, cid, npc_id, _ = world
    wid = settings.world.world_id
    with factory() as s:
        org = s.query(Organization).order_by(Organization.id).first()
        # Ensure one leader + one member in this org (authoritative rows).
        leader_row = (
            s.query(OrganizationMember)
            .filter_by(organization_id=org.id, role="leader")
            .first()
        )
        if leader_row is None:
            leader_row = OrganizationMember(
                organization_id=org.id, character_id=npc_id,
                role="leader", joined_at=0,
            )
            s.add(leader_row)
        member_char = (
            s.query(Character)
            .filter(Character.id.like("npc_%"), Character.id != npc_id)
            .order_by(Character.id)
            .first()
        )
        member_row = (
            s.query(OrganizationMember)
            .filter_by(character_id=member_char.id, organization_id=org.id)
            .first()
        )
        if member_row is None:
            member_row = OrganizationMember(
                organization_id=org.id, character_id=member_char.id,
                role="member", joined_at=0,
            )
            s.add(member_row)
        s.commit()
        org_name = org.name
    leader_roles = _roles_of(factory, wid, leader_row.character_id)
    lr = [r for r in leader_roles if r["id"] == "org_leader"]
    assert len(lr) == 1
    assert org_name in lr[0]["title"]
    assert org_name in lr[0]["source"]
    assert "leader" in lr[0]["reason"]
    member_roles = _roles_of(factory, wid, member_row.character_id)
    mr = [r for r in member_roles if r["id"] == "org_member"]
    assert len(mr) == 1
    assert org_name in mr[0]["title"]
    assert "member" in mr[0]["reason"]
    # A leader is not also granted org_member for the same org.
    assert "org_member" not in [r["id"] for r in leader_roles]


def test_offender_role_reported_only(world):
    """Public roles: an UNREPORTED crime is not a public fact — the offender
    role appears only for reported/resolved authoritative crime rows."""
    settings, _, _, factory, cid, npc_id, _ = world
    wid = settings.world.world_id
    with factory() as s:
        s.add(Crime(
            world_id=wid, crime_type="theft", actor_character_id=npc_id,
            location_id=s.get(Character, npc_id).location_id,
            day=1, status="unreported", created_at=0,
        ))
        s.commit()
    # Unreported → absent (no false positive from a secret fact).
    assert "offender" not in [r["id"] for r in _roles_of(factory, wid, npc_id)]
    with factory() as s:
        s.add(Crime(
            world_id=wid, crime_type="vandalism", actor_character_id=npc_id,
            location_id=s.get(Character, npc_id).location_id,
            day=2, status="reported", created_at=2 * 1440,
        ))
        s.commit()
    roles = _roles_of(factory, wid, npc_id)
    off = [r for r in roles if r["id"] == "offender"]
    assert len(off) == 1
    assert "vandalism" in off[0]["reason"]
    assert off[0]["source"]


def test_roles_deterministic_same_state_same_roles(world):
    settings, _, _, factory, cid, npc_id, _ = world
    wid = settings.world.world_id
    a = _roles_of(factory, wid, npc_id)
    b = _roles_of(factory, wid, npc_id)
    assert a == b
    # A fresh read transaction sees the same derivation.
    c = _roles_of(factory, wid, npc_id)
    assert a == c
    # Roles are plain data with the fixed catalog shape.
    for r in a:
        assert set(r.keys()) == {"id", "title", "source", "reason"}


def test_roles_distinct_from_internal_traits(world):
    settings, _, _, factory, cid, npc_id, _ = world
    wid = settings.world.world_id
    with factory() as s:
        trait_keys = {
            t.trait_key
            for t in s.query(CharacterTrait).filter_by(character_id=npc_id)
        }
    assert trait_keys, "seeded character must have internal traits"
    role_ids = {r["id"] for r in _roles_of(factory, wid, npc_id)}
    assert role_ids and not (role_ids & trait_keys)


def test_partner_role_excluded_no_authoritative_source(world):
    """#89: «unsupported/fuzzy role is not invented». The schema has NO
    marriage/partnership source; high romantic_interest is a sentiment
    scalar, so partner must never be derived. The module documents the
    exclusion as data."""
    import app.social.roles as roles_module

    assert "partner" in roles_module.EXCLUDED_ROLES
    assert roles_module.EXCLUDED_ROLES["partner"]
    settings, _, _, factory, cid, npc_id, _ = world
    wid = settings.world.world_id
    roles = _roles_of(factory, wid, npc_id)
    assert "partner" not in [r["id"] for r in roles]


def test_role_traceability_via_source_row_timestamps(world):
    """#89 «role changes are traceable»: derive-on-read has no event log —
    the change log IS the authoritative source rows, each carrying its own
    timestamp (characters.created_at, character_jobs.started_at,
    organization_members.joined_at, crimes.day). Pinned here so the model
    stays honest instead of inventing a mutation-heavy role store."""
    settings, _, _, factory, cid, npc_id, _ = world
    wid = settings.world.world_id
    with factory() as s:
        char = s.get(Character, npc_id)
        assert char.created_at is not None
        cj = s.query(CharacterJob).filter_by(character_id=npc_id).first()
        assert cj is not None and cj.started_at is not None
        mem = s.query(OrganizationMember).filter_by(character_id=npc_id).first()
        assert mem is not None and mem.joined_at is not None
        expected_org_role = f"org_{mem.role}"  # leader → org_leader, member → org_member
    ids = {r["id"] for r in _roles_of(factory, wid, npc_id)}
    assert {"newcomer", "occupation", expected_org_role} <= ids


# ---------- 2. GET /characters/{cid}/roles read model ----------

def test_roles_endpoint_auth_and_unknown(world):
    settings, app, client, factory, cid, npc_id, _ = world
    # The fixture client is logged in — drop the cookie for the 401 check.
    client.post("/auth/logout")
    r = client.get(f"/characters/{npc_id}/roles")
    assert r.status_code == 401
    client.post("/auth/login", json={"username": "b1reader", "password": PW})
    r = client.get("/characters/who-is-this/roles")
    assert r.status_code == 404


def test_roles_endpoint_public_shape(world):
    settings, app, client, factory, cid, npc_id, _ = world
    r = client.get(f"/characters/{npc_id}/roles")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["character_id"] == npc_id
    assert isinstance(body["roles"], list)
    for role in body["roles"]:
        assert set(role.keys()) == {"id", "title", "source", "reason"}
        assert role["id"] and role["title"] and role["source"]
    # Public read model: ANOTHER authenticated user may read public roles.
    client.post("/auth/register", json={
        "username": "b1other", "email": "b1o@x.com",
        "password": PW, "age_confirmed": True,
    })
    client.post("/auth/login", json={"username": "b1other", "password": PW})
    r2 = client.get(f"/characters/{npc_id}/roles")
    assert r2.status_code == 200
    assert r2.json()["roles"] == body["roles"]


# ---------- 3. Dialogue context consumes roles (#89 → #88) ----------

def test_build_context_includes_public_roles(world):
    settings, app, client, factory, cid, npc_id, _ = world
    from app.api.dialogue import build_context
    from app.social.roles import derive_roles

    with factory() as s:
        user_char = s.get(Character, cid)
        npc = s.get(Character, npc_id)
        ctx = build_context(s, settings.world.world_id, user_char, npc)
    assert ctx["npc_roles"] == derive_roles(factory(), settings.world.world_id, npc_id)
    assert isinstance(ctx["player_roles"], list)
    # Existing context keys stay intact (m5 pins the relationship shape).
    assert set(ctx["relationship"].keys()) == {"trust", "affection", "respect"}
    assert "npc_name" in ctx and "npc_memories" in ctx


def test_message_source_fallback_when_llm_off(world):
    settings, app, client, factory, cid, npc_id, _ = world
    assert settings.llm.enabled is False
    sid = client.post("/dialogue/start", json={"npc_id": npc_id}).json()["session_id"]
    r = client.post(f"/dialogue/{sid}/message", json={"content": "Привет!"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "fallback"
    # Backward-compatible shape: old keys still present.
    assert body["npc_reply"] and body["session_id"] == sid
    assert len(body["suggested_responses"]) == 3


def test_message_source_llm_via_stubbed_seam_receives_roles(
    world, monkeypatch
):
    settings, app, client, factory, cid, npc_id, _ = world
    captured = {}

    def fake_llm_reply(session, settings_, world_id, row, history, context,
                       user_message):
        captured["context"] = context
        return "Ответ через LLM-шов."

    monkeypatch.setattr("app.api.dialogue.llm_reply", fake_llm_reply)
    sid = client.post("/dialogue/start", json={"npc_id": npc_id}).json()["session_id"]
    r = client.post(f"/dialogue/{sid}/message", json={"content": "Тест шва"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "llm"
    assert body["npc_reply"] == "Ответ через LLM-шов."
    # #89: the LLM seam receives the public roles in its context.
    assert isinstance(captured["context"].get("npc_roles"), list)
    assert isinstance(captured["context"].get("player_roles"), list)


def test_dialogue_never_mutates_relationship(world, monkeypatch):
    """#88: «no fake relationship delta from plain chat» — journal-only."""
    settings, app, client, factory, cid, npc_id, _ = world

    def fake_llm_reply(session, settings_, world_id, row, history, context,
                       user_message):
        return "ЛЛМ отвечает, но мир не трогает."

    monkeypatch.setattr("app.api.dialogue.llm_reply", fake_llm_reply)

    def rel_snapshot():
        with factory() as s:
            rows = s.query(Relationship).filter_by(world_id=settings.world.world_id).all()
            return len(rows), sorted(
                (r.character_a, r.character_b, r.trust, r.affection, r.respect)
                for r in rows
            )

    before = rel_snapshot()
    sid = client.post("/dialogue/start", json={"npc_id": npc_id}).json()["session_id"]
    for i in range(3):
        r = client.post(f"/dialogue/{sid}/message", json={"content": f"Сообщение {i}"})
        assert r.status_code == 200
    after = rel_snapshot()
    assert before == after, "plain chat must not touch Relationship rows"
    # Sanity: the dialogue did persist (journal exists, world untouched).
    hist = client.get(f"/dialogue/{sid}").json()
    assert len(hist["messages"]) == 6


# ---------- 4. UI byte-pins (#88 screen, #89 profile hook) ----------

def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def test_ui_chat_screen_new_pins():
    js = _read(APP_JS)
    # New chat screen ids — construction and live-node lookup.
    assert js.count('el("input", { id: "chat-msg-input", placeholder: "Сообщение…" })') == 1
    assert js.count('"chat-send"') == 1
    assert js.count('"chat-view"') == 1
    assert js.count('"chat-status"') == 1
    assert js.count('"chat-roles"') == 1
    assert js.count('"chat-visual"') == 1
    # Free-text input is ALWAYS visible: the composer sits outside any
    # session-only branch — the layout references it unconditionally.
    assert js.count('"chat-log"') == 1
    # Canonical NPC visual through the cookie-auth blob pattern.
    assert js.count("fetchPortraitBlob") == 5
    assert js.count("/visual/characters/${npcSel.value}/portrait") == 1
    assert js.count("api(`/characters/${npcSel.value}/roles`)") == 1
    # Relationship summary from the authoritative read model — one fetch
    # helper, invoked on render AND after each reply (string occurs once).
    assert js.count("/characters/${S.character.id}/relationships") == 1


def test_ui_suggested_replies_fill_never_send():
    js = _read(APP_JS)
    # Shortcut semantics: click fills the free-text input and focuses it.
    assert js.count("if (inp) { inp.value = s; inp.focus(); }") == 1
    # The old auto-send shape is gone.
    assert js.count("input.value = s; send();") == 0
    assert js.count(
        '"Подсказки заполняют поле ввода, свободный текст — основной"'
    ) == 1


def test_ui_thinking_state_non_frozen_ticker():
    js = _read(APP_JS)
    assert js.count("Печатает…") >= 2
    assert js.count("const t0 = Date.now();") == 1
    assert js.count("Math.round((Date.now() - t0) / 1000)") == 1
    # Ticker torn down on both success and error paths.
    assert js.count("clearInterval(tick);") == 2


def test_ui_error_keeps_draft_and_shows_retry_fallback():
    js = _read(APP_JS)
    # Draft restore in the catch path — typed text survives recoverable errors.
    assert js.count("input.value = draft;") == 1
    # Draft is cleared after a successful POST returns: once in the main
    # success path, once in the mid-flight-NPC-switch guard (the turn is
    # already logged server-side at that point — both are post-POST clears).
    assert js.count('input.value = "";') == 2
    assert js.index('input.value = "";') > js.index("/dialogue/${sid}/message")
    assert js.count('"Не удалось отправить — проверьте связь и повторите"') == 1
    # Fallback visibility when the LLM is unavailable.
    assert js.count('"LLM недоступен, отвечает fallback"') == 1


def test_ui_profile_roles_hook():
    js = _read(APP_JS)
    assert js.count("profile-roles") == 1
    assert js.count('api(`/characters/${S.character.id}/roles`)') == 1
    assert js.count('"Роли: " + (rolesText || "нет")') == 1


def test_ui_history_uses_game_time_and_sender_side():
    js = _read(APP_JS)
    assert js.count("dayTime(m.game_timestamp)") == 1
    assert js.count('m.sender === "npc" ? "npc" : ""') == 1


def test_ui_legacy_pins_intact():
    js = _read(APP_JS)
    css = _read(APP_CSS)
    assert js.count("innerHTML") == 0
    assert js.count('location.hash = "#/profile";') == 3
    assert js.count('location.hash === "#/world"') == 1
    assert js.count("scene-view") == 3
    assert js.count('document.getElementById("scene-view")') == 2
    assert js.count('"Осмотреть окрестности"') == 1
    assert js.count("«Осмотреть окрестности»") == 1
    assert js.count('"Осмотреть"') == 1
    assert js.count('"Открыть Scene"') == 1
    assert js.count('"К карте"') == 2
    assert js.count("S.scene = null;") >= 2
    assert js.count('S.scene.state === "loading"') >= 1
    assert js.count('"scene-act-input"') == 2
    assert js.count('"scene-act-go"') == 2
    assert js.count('"Осматриваю окрестности…"') == 1
    assert js.count('"Визуал недоступен, показываю данные"') == 1
    # m150: appended CSS rules must not disturb the pinned media blocks.
    grid_media = css.split("@media (max-width: 880px) {", 1)[1].split("\n}", 1)[0]
    assert grid_media == "\n  .grid { grid-template-columns: 1fr; }"
    small_media = css.split("@media (max-width: 520px) {", 1)[1].split("\n}", 1)[0]
    assert small_media == (
        "\n  .anchor .dot { width: 7px; height: 7px; }"
        "\n  .anchor .map-label { font-size: 9px; }"
    )


# ---------- 5. Implementation hygiene ----------

def test_roles_module_documents_exclusions_and_purity():
    """The module itself must carry the exclusion rationale and the
    derive-on-read traceability contract — a stranger reads it, not a chat."""
    import app.social.roles as roles_module

    src = inspect.getsource(roles_module)
    assert "EXCLUDED_ROLES" in dir(roles_module) or hasattr(
        roles_module, "EXCLUDED_ROLES"
    )
    assert "partner" in src
    assert "derive-on-read" in src.lower() or "derive_on_read" in src.lower()
    # Deterministic: derive_roles takes (session, world_id, character_id).
    sig = inspect.signature(roles_module.derive_roles)
    assert list(sig.parameters) == ["session", "world_id", "character_id"]
