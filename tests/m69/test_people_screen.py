"""#69 increment 1 — «Люди» screen: `respect` additive read + UI pins.

Backend change under test (already implemented in the working tree):
``GET /characters/{cid}/relationships`` ADDS one key per item — ``respect``
(``Relationship.respect``, signed float -100..100 per the model's
CheckConstraint). Everything else about the item is untouched, and the route
stays owner-only (``current_user`` + ``_owned_character``: 401 anonymous,
403 foreign authenticated caller).

Fixture: m67-style TestClient world (tests/m67/test_profile_read_model.py) —
load_config(default.yaml), own sqlite engine per test, bootstrap + seed_world
+ generate_population(20) + seed_social, owner User row via hash_password(PW)
logged in through /auth/login (cookie), reader client registered + logged in
through /auth/*.

Respect oracle notes (verified against the seed code, not assumed):
- seed_social (world/social_seed.py:116-142) is the ONLY Relationship writer
  in the seed path; it sets affection=-50 and leaves ``respect`` at the model
  default 0.0. So no seeded row is negative → the negative-respect case is
  set up explicitly via the Relationship model (work-order branch 2).
- The seeded-row test proves the endpoint echoes the DB value (not an
  invented default) by re-reading the row from the session and comparing
  exactly; the model-set test pins a NEGATIVE value (-37.5) end-to-end.

UI pins (m157/m158/m175/m176 house style — exact literal counts over
backend/app/web/app.js, update deliberately, never weaken):
- ``"Портрет недоступен"`` is pinned == 2, NOT 1 as the work order's recon
  guessed: the implementation renders the fallback at TWO sites (portrait
  fetch catch + ``S.visualDisabled`` branch, app.js:3163/3168). Both are
  genuine; the pin matches actual behavior.
- ``"Как мир тебя видеть"``-style heading: the comment at app.js:3073 uses
  guillemets («»), so the pinned literal INCLUDES the straight double quotes
  and matches only the ``el("h2", {}, ...)`` string — exactly 1.
"""

import os
import random

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.api.app import create_app
from app.api.auth import hash_password
from app.characters.generator import generate_population
from app.characters.player import create_player_character
from app.config.config import load_config
from app.db.models import (
    Character,
    Relationship,
    User,
    bootstrap,
    create_engine_factory,
)
from app.world.seed_world import seed_world
from app.world.social_seed import seed_social

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
APP_JS = os.path.join(ROOT, "backend/app/web/app.js")

PW = "Correct-Horse-9!"

# (b) EXACT emitted key set, verified against the route dict literal
# (api/app.py get_character_relationships): the 7 pre-#69 keys + respect.
PREVIOUS_KEYS = {
    "other_id", "other_name", "trust", "affection", "familiarity",
    "romantic_interest", "updated_at",
}
ITEM_KEYS = PREVIOUS_KEYS | {"respect"}

SETTINGS = None


def setup_module(module):
    global SETTINGS
    SETTINGS = load_config(os.path.join(ROOT, "config/default.yaml"))


def make_settings(tmp_path):
    # social.enabled=True mirrors the m61/m67 fixture: seed_social is
    # authoritative for the Relationship rows we assert on.
    social = SETTINGS.social.model_copy(update={"enabled": True})
    return SETTINGS.model_copy(update={
        "persistence": SETTINGS.persistence.model_copy(
            update={"db_path": str(tmp_path / "w69.db")}
        ),
        "social": social,
    })


def build_world(settings, seed=42):
    engine = create_engine_factory(settings)
    with sessionmaker(bind=engine)() as session:
        bootstrap(engine, settings, seed=seed)
        seed_world(session, settings, settings.world.world_id)
        rng = random.Random(seed)
        generate_population(session, settings, rng, settings.world.world_id, 20)
        seed_social(session, settings, settings.world.world_id, rng)
        session.commit()
    return engine


@pytest.fixture()
def world(tmp_path):
    """Seeded world + player character + owner/reader/anon clients + factory."""
    settings = make_settings(tmp_path)
    engine = build_world(settings)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    wid = settings.world.world_id
    with factory() as s:
        user = User(
            username="m69owner", email="m69@x.com",
            password_hash=hash_password(PW), age_confirmed=True, created_at=0,
        )
        s.add(user)
        s.commit()
        pc = create_player_character(
            s, settings, wid, user,
            name="Борис Орлов", sex="M", age=30, game_timestamp=0,
        )
        s.commit()
        pc_id = pc.id
        # The route is owner-only (_owned_character), so to echo a SEEDED
        # NPC↔NPC row the owner must own one endpoint. Assign character_a of
        # the first seeded row (id order = seeding order, deterministic).
        seeded = (
            s.query(Relationship)
            .filter(Relationship.world_id == wid)
            .order_by(Relationship.id)
            .first()
        )
        assert seeded is not None, "seed_social produced no Relationship rows"
        npc = s.get(Character, seeded.character_a)
        npc.user_id = user.id
        s.commit()
    app = create_app(settings, factory)
    owner = TestClient(app)
    r = owner.post("/auth/login", json={"username": "m69owner", "password": PW})
    assert r.status_code == 200, r.text
    reader = TestClient(app)
    r = reader.post("/auth/register", json={
        "username": "m69reader", "email": "m69r@x.com",
        "password": PW, "age_confirmed": True,
    })
    assert r.status_code == 201, r.text
    r = reader.post("/auth/login", json={"username": "m69reader", "password": PW})
    assert r.status_code == 200, r.text
    anon = TestClient(app)  # never logs in
    return {
        "settings": settings, "factory": factory, "pc_id": pc_id,
        "owner": owner, "reader": reader, "anon": anon, "world_id": wid,
    }


def _rel_item(world, cid, other_id):
    """Owner GET /characters/{cid}/relationships → the item about other_id."""
    r = world["owner"].get(f"/characters/{cid}/relationships")
    assert r.status_code == 200, r.text
    items = r.json()
    assert isinstance(items, list)
    matches = [i for i in items if i["other_id"] == other_id]
    assert len(matches) == 1, f"expected exactly one item for {other_id}: {items}"
    return matches[0]


# ---------- (a) owner: respect echoed from the DB, seeded and negative ----------


def test_people_meter_overrides_beat_global_need_fill():
    """#69: signed-meter colors are shell-scoped (0,3,1) so the global
    `.need .bar > div` green fill cannot repaint them (peer-review blocker
    fix); hostile red reuses the existing --w-rel-hostile token."""
    css = open(os.path.join(ROOT, "backend/app/web/style.css"), encoding="utf-8").read()
    assert css.count(".people-shell .need .bar > .people-mid") == 1
    assert css.count(".people-shell .need .bar > .people-fill {") == 1
    assert css.count(".people-shell .need .bar > .people-fill.neg") == 1
    assert "#c96a6a" not in css
    assert css.count("var(--w-rel-hostile)") >= 2

def test_owner_relationships_echo_seeded_respect(world):
    """Seeded NPC↔NPC row: the item's respect == the Relationship row value."""
    with world["factory"]() as s:
        row = (
            s.query(Relationship)
            .filter(Relationship.world_id == world["world_id"])
            .order_by(Relationship.id)
            .first()
        )
        expected = {
            "trust": row.trust, "affection": row.affection,
            "respect": row.respect, "familiarity": row.familiarity,
            "romantic_interest": row.romantic_interest,
            "updated_at": row.updated_at,
        }
        a_id, b_id = row.character_a, row.character_b
    # character_a was assigned to the owner in the fixture (owner-only route).
    item = _rel_item(world, a_id, b_id)
    for key, value in expected.items():
        assert item[key] == value, f"{key}: response {item[key]!r} != DB {value!r}"
    # Documented oracle branch: seed_social leaves respect at the model
    # default 0.0 — the NEGATIVE case is covered by the next test via the
    # model, exactly as the work order prescribes.
    assert expected["respect"] == 0.0


def test_owner_relationships_negative_respect_from_model(world):
    """No seeded negative-respect row exists → set one up via the model.

    Canonical order constraint is character_a < character_b; ids are
    npc_NNNN / plr_NNNN so 'npc_...' < 'plr_...' → a=npc, b=player.
    """
    with world["factory"]() as s:
        npc = (
            s.query(Character)
            .filter(Character.world_id == world["world_id"],
                    Character.id.like("npc_%"), Character.user_id.is_(None))
            .order_by(Character.id)
            .first()
        )
        assert npc is not None
        s.add(Relationship(
            world_id=world["world_id"],
            character_a=npc.id, character_b=world["pc_id"],
            trust=12.5, affection=-5.0, respect=-37.5,
            familiarity=44.0, romantic_interest=3.25, updated_at=100,
        ))
        s.commit()
        npc_id = npc.id
    item = _rel_item(world, world["pc_id"], npc_id)
    assert item["respect"] == -37.5  # negative value survives the round trip
    assert item["trust"] == 12.5
    assert item["affection"] == -5.0
    assert item["familiarity"] == 44.0
    assert item["romantic_interest"] == 3.25
    assert item["updated_at"] == 100


# ---------- (b) additive contract: EXACT item key set ----------

def test_relationship_item_key_set_is_previous_plus_respect(world):
    """Every item's keys == the 7 pre-#69 keys + 'respect', nothing else."""
    with world["factory"]() as s:
        row = (
            s.query(Relationship)
            .filter(Relationship.world_id == world["world_id"])
            .order_by(Relationship.id)
            .first()
        )
        a_id = row.character_a
    r = world["owner"].get(f"/characters/{a_id}/relationships")
    assert r.status_code == 200, r.text
    items = r.json()
    assert items, "seeded world should yield at least one relationship"
    for item in items:
        assert set(item.keys()) == ITEM_KEYS
    # Pin the previous set too: if the route ever drops/renames a pre-#69
    # key this fails even if respect is present (additive, not replacement).
    assert ITEM_KEYS - PREVIOUS_KEYS == {"respect"}


# ---------- (c) UI pins over backend/app/web/app.js (read as text) ----------

def _js():
    with open(APP_JS, "r", encoding="utf-8") as f:
        return f.read()


def test_viewpeople_function_defined_once():
    assert _js().count("function viewPeople") == 1


def test_people_route_wired_into_registry_once():
    assert _js().count('"#/people": viewPeople') == 1


def test_people_topbar_tab_present_once():
    # The tab pair ["#/people", "Люди"] in topbar(); "Люди" appears nowhere else.
    assert _js().count('"Люди"') == 1


def test_portrait_fallback_literal_exactly_two_sites():
    # == 2, not the recon-guessed 1: catch branch + S.visualDisabled branch
    # (app.js:3163/3168). Removing either fallback fails this pin.
    assert _js().count("Портрет недоступен") == 2


def test_roles_heading_literal_once():
    # Straight double quotes exclude the «guillemets» mention in the
    # explanatory comment (app.js:3073) — only el("h2", {}, "...") matches.
    assert _js().count('"Как мир тебя видит"') == 1


def test_meter_labels_present():
    js = _js()
    assert js.count("Доверие") >= 1
    assert js.count("Романтика") >= 1


# ---------- (d) owner-only guard: respect never reaches a foreign caller ----------

def test_relationships_anonymous_is_401_no_respect(world):
    r = world["anon"].get(f"/characters/{world['pc_id']}/relationships")
    assert r.status_code == 401, r.text
    assert "respect" not in r.text


def test_relationships_foreign_user_is_403_no_respect(world):
    """Route is owner-only (current_user + _owned_character), so the guard is
    the 403/401 itself — no item body, hence no respect leak. Actual behavior
    pinned, no invented gating."""
    r = world["reader"].get(f"/characters/{world['pc_id']}/relationships")
    assert r.status_code == 403, r.text
    assert "respect" not in r.text
    # Even a nonexistent cid must not leak: 404 before any row is read.
    r = world["reader"].get("/characters/plr_9999/relationships")
    assert r.status_code == 404, r.text
    assert "respect" not in r.text
