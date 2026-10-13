"""#60 — LLM interpreter for the Desire layer (deterministic catalog stays
the fallback).

The issue body is the spec of record (gh issue view 60; LLM-seam pattern of
record: app.api.dialogue.llm_reply — Optional, None on ANY failure, caller
falls back). Specs/004-llm R13.

Covers:

- llm_interpret_desire / resolve_desire_interpretation (app.desire
  .llm_interpret): LLM-first when llm.enabled, deterministic catalog on ANY
  failure (disabled / transport build or complete exception / non-JSON /
  non-dict / catalog_key outside CATALOG). Whitelist enforced in code:
  title_ru always from CATALOG (a spoofed title_ru in the reply is ignored),
  matched_keywords [], honest ``interpreted_by: "llm"`` marker; the
  deterministic dict shape is UNCHANGED and carries NO interpreted_by
  (absence = catalog — keeps m157 pins and pre-#60 rows consistent).
  LLM-answered «unsupported» (catalog_key null) surfaces the sanitized LLM
  clarification (generic CLARIFICATION_UNSUPPORTED when empty) and is NOT
  re-classified. confidence_note sanitized: strip + collapse whitespace +
  max 200 chars.
- Endpoints: preview (POST /characters/{cid}/desire/interpret) and create
  (POST /characters/{cid}/desire) both go through the SAME resolver
  (state["settings"], the dialogue_message access pattern); the preview
  never creates a row; the create stores the LLM interpretation JSON and
  keeps the one-active-desire replace flow intact; with llm disabled (test
  default config) both endpoints behave exactly as before #60.
- Prod-norm bulletproofing: llm.enabled=true with an unavailable service
  (transport raises — the norm in prod) must fall back to the deterministic
  catalog on the real endpoint path too.
"""

import json
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
    CharacterDesire,
    bootstrap,
    create_engine_factory,
)
from app.desire.desire import interpret_desire  # noqa: E402
from app.desire.llm_interpret import (  # noqa: E402
    llm_interpret_desire,
    resolve_desire_interpretation,
)
from app.world.seed_world import seed_world  # noqa: E402
from app.world.social_seed import seed_social  # noqa: E402

PW = "Correct-Horse-9!"
SETTINGS = None


def setup_module(module):
    global SETTINGS
    SETTINGS = load_config("config/default.yaml")


def make_settings(tmp_path, llm_enabled=False):
    llm = SETTINGS.llm.model_copy(update={"enabled": llm_enabled})
    social = SETTINGS.social.model_copy(update={"enabled": True})
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


def _register_and_login(client, username):
    client.post("/auth/register", json={
        "username": username, "email": f"{username}@x.com",
        "password": PW, "age_confirmed": True,
    })
    client.post("/auth/login", json={"username": username, "password": PW})


@pytest.fixture()
def world(tmp_path):
    """Seeded world + app/client (llm DISABLED — test default config)."""
    settings = make_settings(tmp_path)
    engine = build_world(settings)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app(settings, factory)
    client = TestClient(app)
    _register_and_login(client, "m188player")
    r = client.post("/characters", json={"name": "Борис Орлов", "sex": "M", "age": 30})
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    return settings, app, client, factory, cid, engine


@pytest.fixture()
def world_llm(tmp_path):
    """Same, but the app runs with llm.enabled=true (fake transport per test)."""
    settings = make_settings(tmp_path, llm_enabled=True)
    engine = build_world(settings)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app(settings, factory)
    client = TestClient(app)
    _register_and_login(client, "m188llmplayer")
    r = client.post("/characters", json={"name": "Арина Ветрова", "sex": "F", "age": 28})
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    return settings, app, client, factory, cid, engine


class _FakeTransport:
    """Scripted transport: records purposes, pops replies, or raises.

    Replies are consumed in order; an extra call fails the test (pinned
    call count). Patched over app.ai.transports.build_transport — the exact
    module path llm_interpret imports lazily inside the function (mirrors
    dialogue.py's seam, m61 patching style).
    """

    def __init__(self, replies=None, error=None):
        self.replies = list(replies or [])
        self.error = error
        self.purposes = []
        self.prompts = []

    def complete(self, prompt, purpose):
        self.purposes.append(purpose)
        self.prompts.append(prompt)
        if self.error is not None:
            raise self.error
        assert self.replies, "unexpected extra transport.complete call"
        return self.replies.pop(0)


def _patch_transport(monkeypatch, transport):
    import app.ai.transports as transports

    monkeypatch.setattr(transports, "build_transport", lambda cfg: transport)
    return transport


def _llm_on():
    return SETTINGS.model_copy(update={
        "llm": SETTINGS.llm.model_copy(update={"enabled": True}),
    })


def _llm_off():
    return SETTINGS  # config/default.yaml: llm.enabled=false


# ---------- 1. Resolver unit behavior ----------

def test_disabled_short_circuits_before_transport(monkeypatch):
    """llm.enabled=false (test default config) → deterministic dict, and
    build_transport is NEVER called (patched to raise AssertionError)."""
    import app.ai.transports as transports

    def _must_not_build(cfg):
        raise AssertionError("build_transport called with llm disabled")

    monkeypatch.setattr(transports, "build_transport", _must_not_build)
    text = "хочу найти друзей"
    result = resolve_desire_interpretation(text, _llm_off())
    assert result == interpret_desire(text)
    assert result["catalog_key"] == "relationship"
    assert "interpreted_by" not in result  # deterministic shape unchanged
    assert llm_interpret_desire(text, _llm_off()) is None


def test_enabled_valid_reply_llm_dict(monkeypatch):
    reply = json.dumps({
        "catalog_key": "relationship",
        "confidence_note": "мечтает о близости",
        "clarification": "",
    })
    transport = _patch_transport(monkeypatch, _FakeTransport(replies=[reply]))
    # text deliberately has NO catalog keyword: the deterministic fallback
    # would answer «unsupported» — only the LLM path can produce this dict.
    result = resolve_desire_interpretation(
        "хочу, чтобы рядом всегда были те, кто дорог сердцу", _llm_on())
    assert transport.purposes == ["desire"]  # purpose arg pinned
    assert result == {
        "catalog_key": "relationship",
        "title_ru": "Отношения",  # from CODE
        "confidence_note": "мечтает о близости",
        "matched_keywords": [],
        "interpreted_by": "llm",
    }


def test_invented_domain_falls_back(monkeypatch):
    """catalog_key outside CATALOG (LLM invents «mayor») → deterministic
    result for that text, no interpreted_by."""
    reply = json.dumps({
        "catalog_key": "mayor", "confidence_note": "хочу стать мэром",
    })
    _patch_transport(monkeypatch, _FakeTransport(replies=[reply]))
    text = "хочу найти друзей"
    result = resolve_desire_interpretation(text, _llm_on())
    assert result == interpret_desire(text)
    assert "interpreted_by" not in result


def test_unhashable_catalog_key_falls_back(monkeypatch):
    """Review MAJOR: an unhashable catalog_key (list / dict) in the reply
    must degrade to the deterministic fallback — never raise TypeError from
    the whitelist check (``key not in CATALOG``), which was a proven 500 on
    both desire endpoints before the type-guard."""
    text = "хочу найти друзей"
    deterministic = interpret_desire(text)
    for bad_key in (["business"], {"domain": "business"}):
        reply = json.dumps({
            "catalog_key": bad_key, "confidence_note": "х",
        })
        _patch_transport(monkeypatch, _FakeTransport(replies=[reply]))
        result = resolve_desire_interpretation(text, _llm_on())
        assert result == deterministic, bad_key
        assert "interpreted_by" not in result


def test_llm_unsupported_surfaces_clarification(monkeypatch):
    """LLM-answered null is a usable answer: its clarification is surfaced,
    NOT the generic catalog one, and NOT re-classified deterministically."""
    reply = json.dumps({
        "catalog_key": None, "clarification": "хотите славы или покоя?",
    })
    _patch_transport(monkeypatch, _FakeTransport(replies=[reply]))
    result = resolve_desire_interpretation("хочу многого", _llm_on())
    assert result == {
        "catalog_key": None,
        "clarification": "хотите славы или покоя?",
    }


def test_llm_unsupported_empty_clarification_gets_generic(monkeypatch):
    from app.desire.desire import CLARIFICATION_UNSUPPORTED

    reply = json.dumps({"catalog_key": None, "clarification": "   "})
    _patch_transport(monkeypatch, _FakeTransport(replies=[reply]))
    result = resolve_desire_interpretation("хочу многого", _llm_on())
    assert result == {"catalog_key": None, "clarification": CLARIFICATION_UNSUPPORTED}


def test_garbage_and_exception_and_non_dict_fall_back(monkeypatch):
    text = "хочу найти друзей"
    deterministic = interpret_desire(text)
    for reply in ("not json at all", json.dumps([1, 2]), "null"):
        transport = _patch_transport(
            monkeypatch, _FakeTransport(replies=[reply]))
        result = resolve_desire_interpretation(text, _llm_on())
        assert result == deterministic, reply
        assert "interpreted_by" not in result
    transport = _patch_transport(
        monkeypatch, _FakeTransport(error=RuntimeError("ollama down")))
    assert resolve_desire_interpretation(text, _llm_on()) == deterministic
    assert transport.purposes == ["desire"]


def test_title_spoof_from_reply_is_ignored(monkeypatch):
    """title_ru comes from CATALOG even when the reply spoofs it."""
    reply = json.dumps({
        "catalog_key": "relationship", "title_ru": "Мэр",
        "confidence_note": "", "clarification": "",
    })
    _patch_transport(monkeypatch, _FakeTransport(replies=[reply]))
    result = resolve_desire_interpretation("хочу любовь", _llm_on())
    assert result["catalog_key"] == "relationship"
    assert result["title_ru"] == "Отношения"


def test_confidence_note_sanitized(monkeypatch):
    reply = json.dumps({
        "catalog_key": "business", "confidence_note": "  шум \n" * 100,
    })
    _patch_transport(monkeypatch, _FakeTransport(replies=[reply]))
    result = resolve_desire_interpretation("хочу своё дело", _llm_on())
    note = result["confidence_note"]
    assert len(note) == 200  # hard-truncated
    assert note == "шум " * 50  # stripped + whitespace collapsed
    assert "\n" not in note and "  " not in note


# ---------- 2. Endpoints through the same resolver ----------

def test_preview_endpoint_uses_llm_and_creates_no_row(monkeypatch, world_llm):
    _settings, _app, client, factory, cid, _engine = world_llm
    reply = json.dumps({
        "catalog_key": "relationship",
        "confidence_note": "мечтает о близости",
    })
    transport = _patch_transport(monkeypatch, _FakeTransport(replies=[reply]))
    r = client.post(f"/characters/{cid}/desire/interpret",
                    json={"text": "мечтаю о душевной близости"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["catalog_key"] == "relationship"
    interp = body["interpretation"]
    assert interp["interpreted_by"] == "llm"
    assert interp["title_ru"] == "Отношения"
    assert transport.purposes == ["desire"]
    # read-only: the LLM preview must not create a row
    with factory() as s:
        assert s.query(CharacterDesire).filter_by(character_id=cid).count() == 0
    assert client.get(f"/characters/{cid}/desire").json()["desire"] is None


def test_create_endpoint_llm_row_and_replace(monkeypatch, world_llm):
    _settings, _app, client, factory, cid, _engine = world_llm
    transport = _patch_transport(monkeypatch, _FakeTransport(replies=[
        json.dumps({"catalog_key": "relationship", "confidence_note": "о близости"}),
        json.dumps({"catalog_key": "business", "confidence_note": "о деле"}),
    ]))
    r1 = client.post(f"/characters/{cid}/desire", json={"text": "хочу любовь"})
    assert r1.status_code == 201, r1.text
    first = r1.json()["desire"]
    assert first["status"] == "active"
    r2 = client.post(f"/characters/{cid}/desire", json={"text": "хочу своё дело"})
    assert r2.status_code == 201, r2.text
    second = r2.json()["desire"]
    assert transport.purposes == ["desire", "desire"]
    # one-active-desire replace flow intact (m157 scenario)
    with factory() as s:
        rows = (s.query(CharacterDesire).filter_by(character_id=cid)
                .order_by(CharacterDesire.id).all())
        assert len(rows) == 2
        actives = [x for x in rows if x.status == "active"]
        replaced = [x for x in rows if x.status == "replaced"]
        assert len(actives) == 1 and actives[0].id == second["id"]
        assert len(replaced) == 1 and replaced[0].id == first["id"]
        assert replaced[0].replaced_by == actives[0].id
        # the stored interpretation JSON carries the honest LLM marker
        interp = actives[0].interpretation
        if isinstance(interp, str):
            interp = json.loads(interp)
        assert interp["interpreted_by"] == "llm"
        assert interp["catalog_key"] == "business"
    assert client.get(f"/characters/{cid}/desire").json()["desire"]["id"] == second["id"]


def test_create_endpoint_llm_down_falls_back(monkeypatch, world_llm):
    """Prod norm: llm.enabled=true with an UNAVAILABLE service — the create
    endpoint must still work through the deterministic catalog."""
    _patch_transport(monkeypatch, _FakeTransport(error=RuntimeError("ollama down")))
    _settings, _app, client, _factory, cid, _engine = world_llm
    r = client.post(f"/characters/{cid}/desire", json={"text": "хочу найти друзей"})
    assert r.status_code == 201, r.text
    interp = r.json()["interpretation"]
    assert interp["catalog_key"] == "relationship"
    assert "interpreted_by" not in interp  # honest: catalog answered, not the LLM
    assert interp["matched_keywords"]  # deterministic shape (keyword hits)


def test_disabled_endpoint_parity(world):
    """llm disabled (test default config): POST desire still works — the
    resolver fallback through the real endpoint, deterministic shape."""
    _settings, _app, client, _factory, cid, _engine = world
    r = client.post(f"/characters/{cid}/desire", json={"text": "хочу найти друзей"})
    assert r.status_code == 201, r.text
    interp = r.json()["interpretation"]
    assert interp["catalog_key"] == "relationship"
    assert "interpreted_by" not in interp
    p = client.post(f"/characters/{cid}/desire/interpret",
                    json={"text": "хочу найти друзей"})
    assert p.status_code == 200, p.text
    assert p.json()["catalog_key"] == "relationship"
    assert "interpreted_by" not in p.json()["interpretation"]


def test_real_stub_transport_end_to_end(world_llm):
    """Review: П2 locked end-to-end for desire. llm.enabled=true with the
    REAL unpatched StubTransport (config transport: stub) — no
    build_transport monkeypatch anywhere. The LLM seam runs, the pure
    catalog decides: recognized text → 201 with the classifier's catalog_key
    honestly marked interpreted_by "llm"; unrecognized text → 200 +
    clarification, NO row."""
    _settings, _app, client, factory, cid, _engine = world_llm
    text = "хочу найти друзей"
    r = client.post(f"/characters/{cid}/desire", json={"text": text})
    assert r.status_code == 201, r.text
    interp = r.json()["interpretation"]
    assert interp["catalog_key"] == interpret_desire(text)["catalog_key"]
    assert interp["catalog_key"] == "relationship"
    assert interp["interpreted_by"] == "llm"
    # unrecognized text: the stub answers catalog_key null → surfaced
    # clarification, 200, no row (the resolver does not re-classify nulls).
    r2 = client.post(f"/characters/{cid}/desire", json={"text": "съездить на Марс"})
    assert r2.status_code == 200, r2.text
    body2 = r2.json()
    assert body2["catalog_key"] is None
    assert body2["clarification"]
    with factory() as s:
        rows = s.query(CharacterDesire).filter_by(character_id=cid).all()
        assert len(rows) == 1 and rows[0].status == "active"
        assert rows[0].catalog_key == "relationship"
