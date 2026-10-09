"""#138 — logout UX + self-service account deletion (m150).

Covers the issue's three surfaces:
- /auth/logout is a stateless cookie-clear (HMAC tokens, no session table) —
  the docstring must document that reality (TTL-bound outstanding tokens);
- POST /auth/account/delete {confirm: <username>} — 401 unauthenticated,
  409 on confirm mismatch, else explicit FK-ordered cascade delete + cookie
  clear + best-effort unlink of orphaned VisualAsset files AFTER the commit;
- UI danger zone on #/profile (el() only — innerHTML stays banned, m146 pins)
  plus the appended style.css rules that must not disturb the pinned media
  blocks (m146 byte-pins the first 880px/520px blocks).
"""

import os
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from app.api.app import create_app  # noqa: E402
from app.config.config import load_config  # noqa: E402
from app.db.models import (  # noqa: E402
    Character,
    CharacterHealth,
    CharacterJob,
    CharacterNeeds,
    CharacterProfile,
    CharacterTrait,
    DialogueMessage,
    DialogueSession,
    Relationship,
    User,
    VisualAsset,
    bootstrap,
    create_engine_factory,
)
from app.visual.store import AssetStore  # noqa: E402
from app.world.seed_world import seed_world  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
APP_PY = os.path.join(ROOT, "backend/app/api/app.py")
APP_JS = os.path.join(ROOT, "backend/app/web/app.js")
APP_CSS = os.path.join(ROOT, "backend/app/web/style.css")

PW = "Correct-Horse-9!"
SETTINGS = None


def setup_module(module):
    global SETTINGS
    SETTINGS = load_config("config/default.yaml")


@pytest.fixture()
def world(tmp_path):
    settings = SETTINGS.model_copy(update={
        "persistence": SETTINGS.persistence.model_copy(
            update={"db_path": str(tmp_path / "w.db")}
        )
    })
    engine = create_engine_factory(settings)
    with sessionmaker(bind=engine)() as session:
        bootstrap(engine, settings, seed=42)
        seed_world(session, settings, settings.world.world_id)
        session.commit()
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app(settings, factory)
    return settings, app, TestClient(app), factory


def _register_and_login(client, username):
    r = client.post("/auth/register", json={
        "username": username, "email": f"{username}@example.com",
        "password": PW, "age_confirmed": True,
    })
    assert r.status_code == 201, r.text
    uid = r.json()["id"]
    r = client.post("/auth/login", json={"username": username, "password": PW})
    assert r.status_code == 200, r.text
    return uid


def _make_character(client, name):
    r = client.post("/characters", json={"name": name, "sex": "M", "age": 30})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


# ---------- 1. logout: cookie clear + ok ----------

def test_logout_clears_cookie_and_reports_ok(world):
    settings, app, client, factory = world
    _register_and_login(client, "logout138")
    assert settings.api.cookie_name in client.cookies
    r = client.post("/auth/logout")
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert "max-age=0" in r.headers["set-cookie"].lower()
    r = client.get("/auth/me")
    assert r.status_code == 401


# ---------- 2. logout docstring documents stateless TTL reality ----------

def test_logout_documents_stateless_ttl_reality():
    src = _read(APP_PY)
    seg = src.split('@app.post("/auth/logout")', 1)[1].split(
        '@app.get("/auth/me"', 1)[0]
    assert '"""' in seg, "logout must carry a docstring"
    assert "stateless" in seg
    assert "TTL" in seg


# ---------- 3. delete account: auth gate ----------

def test_delete_account_requires_auth(world):
    settings, app, client, factory = world
    r = client.post("/auth/account/delete", json={"confirm": "nobody138"})
    assert r.status_code == 401


# ---------- 4. delete account: confirm mismatch ----------

def test_delete_account_wrong_confirm_is_409(world):
    settings, app, client, factory = world
    uid = _register_and_login(client, "carl138")
    r = client.post("/auth/account/delete", json={"confirm": "carl1"})
    assert r.status_code == 409
    assert r.json()["detail"] == "confirm does not match username"
    # untouched: still authenticated, still able to log in
    assert client.get("/auth/me").status_code == 200
    client.cookies.pop(settings.api.cookie_name)
    r = client.post("/auth/login", json={"username": "carl138", "password": PW})
    assert r.status_code == 200
    with factory() as session:
        assert session.get(User, uid) is not None


# ---------- 5. delete account: explicit cascade ----------

def test_delete_account_cascades_user_and_owned_rows(world):
    """Verified dependent rows: Character + Profile/Needs/Health/Job/Trait,
    Relationship pair, DialogueSession + its DialogueMessages, and the User
    row itself. (Player characters also carry an Account row — owner_id is a
    plain string, NOT an FK, so the ledger row is preserved by design.)"""
    settings, app, client, factory = world
    uid = _register_and_login(client, "june138")
    cid = _make_character(client, "June Test")
    wid = settings.world.world_id
    with factory() as session:
        npc = Character(
            id="npc_138test", world_id=wid, type="npc",
            first_name="Nora", last_name="Cascade",
            birth_date="1990-01-01", age=36, sex="F", alive=True,
            location_id=session.get(Character, cid).location_id,
            created_at=0, updated_at=0,
        )
        session.add(npc)
        rel_a, rel_b = sorted([cid, npc.id])
        session.add(Relationship(
            world_id=wid, character_a=rel_a, character_b=rel_b, updated_at=0,
        ))
        dsess = DialogueSession(
            id=f"dlg_test_{uid}", world_id=wid, user_id=uid,
            character_id=cid, npc_id=npc.id, started_at=0,
        )
        session.add(dsess)
        session.flush()
        session.add(DialogueMessage(
            session_id=dsess.id, sender="user", content="privet",
            game_timestamp=0,
        ))
        session.commit()

    r = client.post("/auth/account/delete", json={"confirm": "june138"})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True}
    assert "max-age=0" in r.headers["set-cookie"].lower()
    assert client.get("/auth/me").status_code == 401
    r = client.post("/auth/login", json={"username": "june138", "password": PW})
    assert r.status_code == 401
    with factory() as session:
        assert session.get(User, uid) is None
        assert session.get(Character, cid) is None
        assert session.get(Character, npc.id) is not None  # NPC outlives
        assert session.get(CharacterProfile, cid) is None
        assert session.get(CharacterNeeds, cid) is None
        assert session.get(CharacterHealth, cid) is None
        assert session.query(CharacterJob).filter_by(character_id=cid).count() == 0
        assert session.query(CharacterTrait).filter_by(character_id=cid).count() == 0
        assert session.query(Relationship).filter_by(
            character_a=rel_a, character_b=rel_b).count() == 0
        assert session.query(DialogueSession).filter_by(id=dsess.id).count() == 0
        assert session.query(DialogueMessage).filter_by(
            session_id=dsess.id).count() == 0


# ---------- 6. delete account without a character ----------

def test_delete_account_without_character_ok(world):
    settings, app, client, factory = world
    uid = _register_and_login(client, "bare138")
    r = client.post("/auth/account/delete", json={"confirm": "bare138"})
    assert r.status_code == 200, r.text
    assert client.get("/auth/me").status_code == 401
    with factory() as session:
        assert session.get(User, uid) is None


# ---------- 7. UI byte-pins: danger zone without XSS/pin damage ----------

def test_ui_profile_has_logout_and_danger_zone():
    js = _read(APP_JS)
    assert js.count('"Удалить аккаунт"') == 1
    assert js.count("/auth/account/delete") == 1
    assert js.count('"Выйти"') == 1
    assert js.count("innerHTML") == 0
    assert js.count('location.hash = "#/profile";') == 3


# ---------- 8. CSS byte-pins: appended rules, media blocks intact ----------

def test_css_danger_zone_styles_without_disturbing_m146_media_pins():
    css = _read(APP_CSS)
    assert "button.danger" in css
    assert ".danger-zone" in css
    grid_media = css.split("@media (max-width: 880px) {", 1)[1].split("\n}", 1)[0]
    assert grid_media == "\n  .grid { grid-template-columns: 1fr; }"
    small_media = css.split("@media (max-width: 520px) {", 1)[1].split("\n}", 1)[0]
    assert small_media == (
        "\n  .anchor .dot { width: 7px; height: 7px; }"
        "\n  .anchor .map-label { font-size: 9px; }"
    )


# ---------- 9. peer condition: asset files unlinked after commit ----------

def test_delete_account_removes_visual_asset_files(tmp_path):
    settings = SETTINGS.model_copy(update={
        "persistence": SETTINGS.persistence.model_copy(
            update={"db_path": str(tmp_path / "vis.db")}
        ),
        "visual": SETTINGS.visual.model_copy(
            update={"storage_dir": str(tmp_path / "vis")}
        ),
    })
    engine2 = create_engine_factory(settings)
    with sessionmaker(bind=engine2)() as session:
        bootstrap(engine2, settings, seed=42)
        seed_world(session, settings, settings.world.world_id)
        session.commit()
    factory2 = sessionmaker(bind=engine2, expire_on_commit=False)
    app2 = create_app(settings, factory2)
    client2 = TestClient(app2)

    _register_and_login(client2, "vis138")
    cid = _make_character(client2, "Vis Tester")
    store = AssetStore(settings.visual)
    with factory2() as session:
        asset = VisualAsset(
            world_id=settings.world.world_id, asset_type="portrait",
            character_id=cid, scene_descriptor={"shot": "portrait"},
            prompt="p", storage_path="", seed=1, model="stub", created_at="0",
        )
        session.add(asset)
        session.flush()
        relative = store.save(asset.id, b"png-bytes")
        asset.storage_path = relative
        session.commit()
        asset_id = asset.id
    assert store.exists(relative)

    r = client2.post("/auth/account/delete", json={"confirm": "vis138"})
    assert r.status_code == 200, r.text
    assert not store.exists(relative)
    with factory2() as session:
        assert session.query(VisualAsset).filter_by(id=asset_id).count() == 0
