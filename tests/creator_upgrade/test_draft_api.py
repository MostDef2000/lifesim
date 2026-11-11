"""020 wave 4 (issue #316) — POST /characters/draft API contract.

Contract of record: specs/020-character-creator/spec.md (wave-4 section) +
tasks.md traceability. The endpoint is PURE: ONE LLM call per operation via
the app.state.llm_transport_factory seam (mirror of flux_transport_factory),
strict server-side validation of every model value, ZERO DB writes.

Covers:
- generate 200: response key set EXACTLY {"draft", "revision"}; draft keys
  exactly DRAFT_FIELDS in canonical order; ONE transport call carrying the
  idea, the generate schema hint and max_chars=MAX_RESPONSE_CHARS.
- variant 200: response key set EXACTLY {"field", "value", "revision"};
  single-field replacement; unknown field → 422 before any LLM call;
  a client-sent draft failing validate_draft → 422 (request shape, not 502).
- 502 matrix: unparseable JSON / unknown draft key / missing key /
  out-of-range age / AI-proposed nude outfit / bad sex / transport failure —
  every invalid model output maps to 502; upstream exception detail never
  leaks. Transport timeout → 504.
- 503 LLM disabled: gate fires FIRST, consumes nothing (full quota after
  re-enable — the m216 gate discipline).
- rate limit: 429 after 5 attempts; FAILED attempts consume quota (502s
  and 422s); a 429 consumes nothing (strong oracle: reject mid-window, then
  FIVE consecutive successes after expiry — a consuming bug 429s the 5th).
- single-flight: a second request while one is in flight → 429 (same user
  AND a different user — one active per process); the lock releases on
  EVERY exit path (after a transport exception the next request succeeds).
- 401 unauthenticated; idea shape (empty / >500) → 422; a 200 generate
  writes no rows anywhere (Character/CharacterAppearance/VisualAsset/
  WorldEvent/AiRequest counts unchanged).
"""
import json
import threading
import time as _real_time

import pytest
from fastapi.testclient import TestClient
from tests.helpers import auth_client, make_user

from app.api import creator_routes
from app.characters.creator import (
    DRAFT_FIELDS,
    generate_schema_hint,
    variant_schema_hint,
)

# The 19-key valid draft (3 identity + 12 anatomy + supplement/biography/
# outfit_mode/custom_clothing) — every value inside the documented bounds.
VALID_DRAFT = {
    "name": "Анна Смирнова",
    "sex": "F",
    "age": 28,
    "face": "soft oval face",
    "eyes": "green eyes",
    "nose": "small nose",
    "lips": "full lips",
    "hair": "long braid",
    "hair_color": "chestnut",
    "skin": "fair skin",
    "height": "170 cm",
    "weight": "60 kg",
    "build": "slim build",
    "chest": "modest chest",
    "features": "freckles",
    "supplement": "a silver pendant",
    "biography": "Родилась в маленьком городке, любит книги.",
    "outfit_mode": "casual",
    "custom_clothing": "a denim jacket",
}


class MockLlm:
    """Records complete() calls; returns the configured raw string or
    raises the configured exception (the route must map, never leak)."""

    def __init__(self, raw=None, exc=None):
        self.raw = raw
        self.exc = exc
        self.calls = []

    def complete(self, prompt, schema_hint, max_chars=None):
        self.calls.append({
            "prompt": prompt, "schema_hint": schema_hint,
            "max_chars": max_chars,
        })
        if self.exc is not None:
            raise self.exc
        return self.raw


def _generate_body(**over):
    return {"operation": "generate", "idea": "рыжая кошка-программист",
            **over}


class TestGenerateContract:
    def test_200_exact_schema_and_one_call(self, authed):
        settings, app, factory, client = authed("wcrgen")
        mock = MockLlm(json.dumps(VALID_DRAFT))
        app.state.llm_transport_factory = lambda: mock
        r = client.post("/characters/draft",
                        json=_generate_body(revision=3))
        assert r.status_code == 200, r.text
        data = r.json()
        assert set(data.keys()) == {"draft", "revision"}
        assert data["revision"] == 3
        # canonical key order, exactly the 19 draft keys
        assert list(data["draft"].keys()) == list(DRAFT_FIELDS)
        assert data["draft"]["name"] == "Анна Смирнова"
        assert data["draft"]["age"] == 28
        assert data["draft"]["outfit_mode"] == "casual"
        # ONE LLM call, carrying the idea + schema hint + read bound
        assert len(mock.calls) == 1
        assert "рыжая кошка-программист" in mock.calls[0]["prompt"]
        assert mock.calls[0]["schema_hint"] == generate_schema_hint()
        from app.characters.creator import MAX_RESPONSE_CHARS
        assert mock.calls[0]["max_chars"] == MAX_RESPONSE_CHARS

    def test_variant_200_single_field(self, authed):
        settings, app, factory, client = authed("wcrvar")
        mock = MockLlm(json.dumps({"value": "ash blonde"}))
        app.state.llm_transport_factory = lambda: mock
        r = client.post("/characters/draft", json={
            "operation": "variant", "field": "hair_color",
            "draft": VALID_DRAFT, "revision": 7})
        assert r.status_code == 200, r.text
        data = r.json()
        assert set(data.keys()) == {"field", "value", "revision"}
        assert data["field"] == "hair_color"
        assert data["value"] == "ash blonde"
        assert data["revision"] == 7
        assert len(mock.calls) == 1
        assert mock.calls[0]["schema_hint"] == variant_schema_hint()
        # the current draft rides the variant prompt
        assert "chestnut" in mock.calls[0]["prompt"]
        assert '"hair_color"' in mock.calls[0]["prompt"]

    def test_variant_unknown_field_422_no_llm_call(self, authed):
        settings, app, factory, client = authed("wcrvarfield")
        mock = MockLlm(json.dumps({"value": "x"}))
        app.state.llm_transport_factory = lambda: mock
        r = client.post("/characters/draft", json={
            "operation": "variant", "field": "nope",
            "draft": VALID_DRAFT})
        assert r.status_code == 422, r.text
        assert mock.calls == []

    def test_variant_client_draft_invalid_422_not_502(self, authed):
        # a BROKEN CLIENT draft is a request-shape error (422), distinct
        # from a broken MODEL output (502).
        settings, app, factory, client = authed("wcrvardraft")
        mock = MockLlm(json.dumps({"value": "x"}))
        app.state.llm_transport_factory = lambda: mock
        broken = {k: v for k, v in VALID_DRAFT.items() if k != "age"}
        r = client.post("/characters/draft", json={
            "operation": "variant", "field": "hair_color", "draft": broken})
        assert r.status_code == 422, r.text
        assert mock.calls == []

    def test_unknown_operation_422(self, authed):
        settings, app, factory, client = authed("wcrop")
        r = client.post("/characters/draft", json={"operation": "rewrite"})
        assert r.status_code == 422, r.text


class TestInvalidModelOutput502:
    @pytest.mark.parametrize("raw,reason", [
        ("not json at all", "unparseable JSON"),
        (json.dumps({**VALID_DRAFT, "extra_key": "x"}), "unknown key"),
        (json.dumps({k: v for k, v in VALID_DRAFT.items() if k != "age"}),
         "missing key"),
        (json.dumps({**VALID_DRAFT, "age": 10}), "out-of-range age"),
        (json.dumps({**VALID_DRAFT, "age": "28"}), "wrong type"),
        (json.dumps({**VALID_DRAFT, "outfit_mode": "nude"}), "AI nude"),
        (json.dumps({**VALID_DRAFT, "sex": "X"}), "bad sex"),
        ("[1, 2, 3]", "non-object JSON"),
    ])
    def test_invalid_model_output_502(self, authed, raw, reason):
        settings, app, factory, client = authed("wcr502")
        app.state.llm_transport_factory = lambda: MockLlm(raw)
        r = client.post("/characters/draft", json=_generate_body())
        assert r.status_code == 502, (reason, r.text)
        assert r.json()["detail"].startswith("invalid model output"), reason

    def test_transport_exception_502_generic_no_leak(self, authed):
        settings, app, factory, client = authed("wcrboom")
        app.state.llm_transport_factory = lambda: MockLlm(
            exc=RuntimeError("secret internal stack"))
        r = client.post("/characters/draft", json=_generate_body())
        assert r.status_code == 502, r.text
        assert r.json()["detail"] == "LLM transport failed"
        assert "secret" not in r.text

    def test_transport_timeout_504(self, authed):
        settings, app, factory, client = authed("wcrtimeout")
        app.state.llm_transport_factory = lambda: MockLlm(exc=TimeoutError())
        r = client.post("/characters/draft", json=_generate_body())
        assert r.status_code == 504, r.text
        assert r.json()["detail"] == "LLM request timed out"


class TestGatesAndLimits:
    def test_llm_disabled_503_first_consumes_nothing(self, authed):
        settings, app, factory, client = authed("wcrgate", llm_enabled=False)
        calls = []
        app.state.llm_transport_factory = (
            lambda: calls.append(1) or MockLlm(json.dumps(VALID_DRAFT)))
        for _ in range(7):  # > rpm: proves the limiter never ran
            r = client.post("/characters/draft", json=_generate_body())
            assert r.status_code == 503, r.text
        assert calls == []
        # flip the SAME llm config object the gate closure reads; the full
        # default quota (5) must still be available — a 503 that consumed
        # would 429 the 5th request below.
        settings.llm.enabled = True
        for i in range(5):
            r = client.post("/characters/draft", json=_generate_body())
            assert r.status_code == 200, (i, r.text)
        assert client.post(
            "/characters/draft", json=_generate_body()).status_code == 429

    def test_unauthenticated_401(self, make_env):
        settings, app, factory = make_env()
        anon = TestClient(app)
        r = anon.post("/characters/draft", json=_generate_body())
        assert r.status_code == 401

    @pytest.mark.parametrize("idea", ["", "   ", "x" * 501])
    def test_idea_shape_422_no_llm_call(self, authed, idea):
        settings, app, factory, client = authed("wcridea")
        mock = MockLlm(json.dumps(VALID_DRAFT))
        app.state.llm_transport_factory = lambda: mock
        r = client.post("/characters/draft",
                        json={"operation": "generate", "idea": idea})
        assert r.status_code == 422, r.text
        assert mock.calls == []


class TestRateLimit:
    def test_429_after_5_failures_consume_and_429_does_not(
            self, authed, monkeypatch):
        settings, app, factory, client = authed("wcrrate")
        mock = MockLlm(exc=ValueError("boom"))  # failures → 502, consume
        app.state.llm_transport_factory = lambda: mock

        class _FakeClock:
            def __init__(self, start=1000.0):
                self.now = float(start)

            def monotonic(self):
                return self.now

            def __getattr__(self, name):
                return getattr(_real_time, name)

        clock = _FakeClock(1000.0)
        monkeypatch.setattr(creator_routes, "time", clock)
        # 5 FAILED attempts (502) consume the whole window
        for _ in range(5):
            assert client.post(
                "/characters/draft", json=_generate_body()).status_code == 502
        clock.now = 1030.0
        assert client.post(
            "/characters/draft", json=_generate_body()).status_code == 429
        # strong non-consumption oracle (m216 pattern): after the window
        # expires, FIVE consecutive successes must pass — a 429 that
        # consumed would 429 the 5th.
        mock.exc = None
        mock.raw = json.dumps(VALID_DRAFT)
        clock.now = 1061.0
        for i in range(5):
            r = client.post("/characters/draft", json=_generate_body())
            assert r.status_code == 200, (i, r.text)
        assert client.post(
            "/characters/draft", json=_generate_body()).status_code == 429

    def test_422s_consume_quota(self, authed):
        settings, app, factory, client = authed("wcr422")
        app.state.llm_transport_factory = lambda: MockLlm(
            json.dumps(VALID_DRAFT))
        for _ in range(5):
            r = client.post("/characters/draft",
                            json={"operation": "generate", "idea": ""})
            assert r.status_code == 422, r.text
        # the 422s consumed admission quota: a VALID request now 429s
        r = client.post("/characters/draft", json=_generate_body())
        assert r.status_code == 429, r.text


class TestSingleFlight:
    def test_second_concurrent_request_429_same_and_other_user(
            self, make_env):
        settings, app, factory = make_env()
        with factory() as session:
            user = make_user(session, "wcrbusy", "wcrbusy@x.com")
        client = auth_client(app, settings, user)
        release = threading.Event()
        started = threading.Event()

        class BlockingLlm:
            def complete(self, prompt, schema_hint, max_chars=None):
                started.set()
                release.wait(timeout=5)
                return json.dumps(VALID_DRAFT)

        app.state.llm_transport_factory = lambda: BlockingLlm()
        result = {}

        def first():
            result["r"] = client.post(
                "/characters/draft", json=_generate_body())

        th = threading.Thread(target=first)
        th.start()
        try:
            assert started.wait(timeout=5), "first request never reached LLM"
            # same user, fresh client (same app/closure state) → busy 429
            client2 = auth_client(app, settings, user)
            r2 = client2.post("/characters/draft", json=_generate_body())
            assert r2.status_code == 429, r2.text
            assert "in progress" in r2.json()["detail"]
            # a DIFFERENT user is blocked too — one active per PROCESS
            with factory() as session:
                user2 = make_user(session, "wcrbusy2", "wcrbusy2@x.com")
            client3 = auth_client(app, settings, user2)
            r3 = client3.post("/characters/draft", json=_generate_body())
            assert r3.status_code == 429, r3.text
        finally:
            release.set()
            th.join(timeout=5)
        assert result["r"].status_code == 200, result["r"].text

    def test_lock_released_after_transport_exception(self, authed):
        settings, app, factory, client = authed("wcrlock")
        app.state.llm_transport_factory = lambda: MockLlm(
            exc=RuntimeError("x"))
        assert client.post(
            "/characters/draft", json=_generate_body()).status_code == 502
        # the NEXT request must succeed → the finally-release ran on the
        # exception path (a stuck busy flag would 429 here)
        app.state.llm_transport_factory = lambda: MockLlm(
            json.dumps(VALID_DRAFT))
        r = client.post("/characters/draft", json=_generate_body())
        assert r.status_code == 200, r.text

    def test_lock_released_after_invalid_model_output(self, authed):
        settings, app, factory, client = authed("wcrlock2")
        app.state.llm_transport_factory = lambda: MockLlm("not json")
        assert client.post(
            "/characters/draft", json=_generate_body()).status_code == 502
        app.state.llm_transport_factory = lambda: MockLlm(
            json.dumps(VALID_DRAFT))
        r = client.post("/characters/draft", json=_generate_body())
        assert r.status_code == 200, r.text


class TestNoDbWrites:
    def test_generate_and_variant_write_no_rows(self, authed):
        from app.db.models import (
            AiRequest,
            Character,
            CharacterAppearance,
            VisualAsset,
            WorldEvent,
        )

        settings, app, factory, client = authed("wcrnowrite")
        app.state.llm_transport_factory = lambda: MockLlm(
            json.dumps(VALID_DRAFT))

        def counts():
            with factory() as session:
                return {
                    "characters": session.query(Character).count(),
                    "appearances": session.query(CharacterAppearance).count(),
                    "assets": session.query(VisualAsset).count(),
                    "events": session.query(WorldEvent).count(),
                    "ai_requests": session.query(AiRequest).count(),
                }

        before = counts()
        assert client.post(
            "/characters/draft", json=_generate_body()).status_code == 200
        # the variant path expects {"value": ...} from the model
        app.state.llm_transport_factory = lambda: MockLlm(
            json.dumps({"value": "ash blonde"}))
        assert client.post("/characters/draft", json={
            "operation": "variant", "field": "hair_color",
            "draft": VALID_DRAFT}).status_code == 200
        assert counts() == before
