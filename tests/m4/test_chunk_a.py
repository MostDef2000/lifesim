"""M4 Chunk A: schema 0.4.0, LlmConfig, EventType 21, transports, CLI flag."""
import json
import urllib.request
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.ai.transports import OllamaTransport, StubTransport, build_transport
from app.config.config import LlmConfig, load_config
from app.db.models import AiRequest, DialogueTurn, Memory, bootstrap
from app.events.events import EventType


def test_schema_050_tables_created(default_settings):
    default_settings.llm = LlmConfig(enabled=True)
    engine = create_engine("sqlite:///:memory:")
    bootstrap(engine, default_settings, 42)
    Session = sessionmaker(bind=engine)
    session = Session()

    # All three M4 tables exist and accept rows.
    session.add(AiRequest(
        world_id=default_settings.world.world_id, character_id="npc_0001",
        task="decide", context={"k": "v"}, priority="interactive",
        created_at=0, game_timestamp=0))
    session.add(Memory(
        world_id=default_settings.world.world_id, character_id="npc_0001",
        event_id=1, memory_type="CONFLICT", importance=70,
        emotional_valence=-0.6, summary="s", created_at=0))
    session.add(DialogueTurn(
        world_id=default_settings.world.world_id, session_id="s1",
        character_id="npc_0001", role="user", content="hi",
        game_timestamp=0))
    session.commit()

    version = session.execute(
        __import__("sqlalchemy").text(
            "SELECT value FROM schema_meta WHERE key='version'")
    ).scalar()
    assert version == "0.5.0"
    session.close()


def test_event_type_closed_set_24():
    values = {e.value for e in EventType}
    assert len(values) == 24
    for added in ("AI_DECISION", "MEMORY_CREATED", "MEMORY_CONSOLIDATED"):
        assert added in values


def test_llm_config_defaults():
    cfg = LlmConfig()
    assert cfg.enabled is False
    assert cfg.transport == "stub"
    assert cfg.budgets.max_per_day == 40
    assert cfg.budgets.per_task["decide"] == 10
    assert cfg.memory.top_k == 5
    assert cfg.retry == 1


def test_config_without_llm_section_uses_defaults(tmp_path):
    base = load_config("config/default.yaml").model_dump()
    base.pop("llm", None)
    base["persistence"]["db_path"] = ":memory:"
    p = tmp_path / "no_llm.yaml"
    import yaml
    p.write_text(yaml.safe_dump(base))
    settings = load_config(str(p))
    assert settings.llm.enabled is False
    assert settings.llm.transport == "stub"


# --- StubTransport determinism -------------------------------------------

def test_stub_decision_picks_first_available_action():
    prompt = (
        "identity: Anna\n"
        "available_actions: [\"socialize_with\", \"visit\", \"work_overtime\"]\n"
    )
    out = StubTransport().complete(prompt, "decision")
    data = json.loads(out)
    assert data["decision"] == "socialize_with"
    assert data["confidence"] == 0.5
    # Deterministic: same prompt -> same answer.
    assert StubTransport().complete(prompt, "decision") == out


def test_stub_decision_without_actions_falls_back():
    out = StubTransport().complete("identity: Anna\n", "decision")
    assert json.loads(out)["decision"] == "work_overtime"


def test_stub_classify_maps_known_events():
    st = StubTransport()
    for event_type, expected in [("CONFLICT", 70), ("ELECTION", 50),
                                 ("ORG_FEAST", 30), ("UNKNOWN_EV", 10)]:
        out = st.complete(f"current_event: {event_type}\n", "classify")
        assert json.loads(out)["importance"] == expected


def test_stub_dialogue_and_summarize():
    st = StubTransport()
    reply = json.loads(st.complete("identity: Anna\n", "dialogue"))
    assert reply["reply"] == "[stub] Anna nods and listens."

    summ = json.loads(st.complete(
        "- CONFLICT at 100\n- CONFLICT at 200\n- ELECTION at 300\n",
        "summarize"))
    assert summ["summary"] == "consolidated: CONFLICT, ELECTION"


def test_build_transport_dispatch():
    settings = LlmConfig(transport="stub")
    assert isinstance(build_transport(settings), StubTransport)
    settings = LlmConfig(transport="ollama", base_url="http://x:1",
                         model_tier2="m", timeout_sec=5)
    t = build_transport(settings)
    assert isinstance(t, OllamaTransport)
    assert t.model == "m" and t.timeout_sec == 5


# --- OllamaTransport contract (network mocked, CI-safe) -------------------

def test_ollama_transport_contract():
    body = json.dumps(
        {"message": {"content": json.dumps({"reply": "ok"})}}).encode()

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return body

    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["payload"] = json.loads(req.data.decode())
        captured["timeout"] = timeout
        return FakeResp()

    with patch.object(urllib.request, "urlopen", side_effect=fake_urlopen):
        t = OllamaTransport("http://h:11434/", "qwen3:14b", timeout_sec=7)
        out = t.complete("hello", "dialogue")
    assert json.loads(out) == {"reply": "ok"}
    assert captured["url"] == "http://h:11434/api/chat"
    assert captured["payload"]["model"] == "qwen3:14b"
    assert captured["payload"]["format"] == "json"
    assert captured["payload"]["stream"] is False
    assert captured["timeout"] == 7


def test_ollama_transport_raises_on_empty_content():
    body = json.dumps({"message": {"content": ""}}).encode()

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return body

    with patch.object(urllib.request, "urlopen", return_value=FakeResp()):
        with pytest.raises(ValueError):
            OllamaTransport("http://h:11434", "m").complete("p", "dialogue")


# --- CLI flag -------------------------------------------------------------

def test_cli_has_llm_flag():

    from app.simulation.cli import main
    # --help exits cleanly and mentions --llm.
    with pytest.raises(SystemExit) as exc:
        main(["simulate", "--help"])
    assert exc.value.code == 0
