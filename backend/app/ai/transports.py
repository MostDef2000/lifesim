"""M4 AI transports (SPEC §52): LlmTransport protocol, deterministic stub,
and an Ollama chat-endpoint transport. No new dependencies.
"""
import json
import urllib.request
from typing import List, Optional, Protocol

from sqlalchemy.orm import Session

from app.db.models import Account, Character, OrganizationMember


class LlmTransport(Protocol):
    """A transport turns a prompt + schema hint into raw response text."""

    def complete(self, prompt: str, schema_hint: str) -> str:  # pragma: no cover
        ...


class StubTransport:
    """Deterministic transport: no network, no clock, no RNG.

    Answers derive only from the prompt content, so llm-on(stub) runs are
    byte-reproducible (spec AE4'').
    """

    def complete(self, prompt: str, schema_hint: str) -> str:
        if schema_hint == "decision":
            return self._decision(prompt)
        if schema_hint == "classify":
            return self._classify(prompt)
        if schema_hint == "dialogue":
            return self._dialogue(prompt)
        if schema_hint == "summarize":
            return self._summarize(prompt)
        raise ValueError(f"unknown schema_hint: {schema_hint}")

    def _decision(self, prompt: str) -> str:
        # The context lists available actions; the stub picks the first one
        # and targets the first nearby character (socialize_with needs one).
        actions = self._json_list_after(prompt, "available_actions:")
        decision = actions[0] if actions else "work_overtime"
        nearby = self._json_list_after(prompt, "nearby_characters:")
        return json.dumps({
            "decision": decision,
            "target_character_id": nearby[0] if nearby else None,
            "confidence": 0.5,
            "reason": "stub: first available action",
        })

    def _classify(self, prompt: str) -> str:
        # Deterministic importance from the event type line, if present.
        importance = 10
        for line in prompt.splitlines():
            if line.startswith("current_event:"):
                event_type = line.split(":", 1)[1].strip()
                base = {
                    "CONFLICT": 70, "RECONCILIATION": 60, "ELECTION": 50,
                    "LAW_VIOLATION": 40, "ORG_FEAST": 30,
                }
                importance = base.get(event_type, 10)
                break
        return json.dumps({"importance": importance})

    def _dialogue(self, prompt: str) -> str:
        name = "NPC"
        for line in prompt.splitlines():
            if line.startswith("identity:"):
                name = line.split(":", 1)[1].strip() or "NPC"
                break
        return json.dumps({
            "reply": f"[stub] {name} nods and listens.",
            "intent": None,
            "confidence": 0.9,
        })

    def _summarize(self, prompt: str) -> str:
        lines = [ln for ln in prompt.splitlines() if ln.startswith("- ")]
        kinds: List[str] = []
        for ln in lines:
            kind = ln[2:].split(" ", 1)[0]
            if kind and kind not in kinds:
                kinds.append(kind)
        summary = "consolidated: " + ", ".join(kinds) if kinds else \
            "consolidated: misc"
        return json.dumps({"summary": summary})

    @staticmethod
    def _json_list_after(prompt: str, marker: str) -> List[str]:
        try:
            idx = prompt.index(marker)
        except ValueError:
            return []
        tail = prompt[idx + len(marker):].strip().splitlines()
        if not tail:
            return []
        try:
            data = json.loads(tail[0].strip())
        except (json.JSONDecodeError, IndexError):
            return []
        return data if isinstance(data, list) else []


class OllamaTransport:
    """OpenAI-compatible chat endpoint (Ollama /api/chat, format='json')."""

    def __init__(self, base_url: str, model: str, timeout_sec: int = 30):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_sec = timeout_sec

    def complete(self, prompt: str, schema_hint: str) -> str:
        payload = json.dumps({
            "model": self.model,
            "messages": [
                {"role": "system",
                 "content": f"Reply with strict JSON matching: {schema_hint}"},
                {"role": "user", "content": prompt},
            ],
            "format": "json",
            "stream": False,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/api/chat", data=payload,
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        content = body.get("message", {}).get("content")
        if not isinstance(content, str) or not content:
            raise ValueError("ollama: empty response content")
        return content


def build_transport(llm_config) -> LlmTransport:
    """Accepts the LlmConfig sub-object (settings.llm)."""
    if llm_config.transport == "ollama":
        return OllamaTransport(
            llm_config.base_url, llm_config.model_tier2,
            llm_config.timeout_sec)
    return StubTransport()


# --- prompt context helpers (SPEC §54) -----------------------------------

def character_identity(session: Session, character_id: str) -> str:
    c = session.query(Character).filter_by(id=character_id).first()
    if c is None:
        return character_id
    return c.name if getattr(c, "name", None) else character_id


def org_balance(session: Session, world_id: str, organization_id: int) -> int:
    acc = session.query(Account).filter_by(
        world_id=world_id, owner_type="organization",
        owner_id=str(organization_id)).first()
    if acc is None:
        return 0
    return int(acc.balance)


def org_member_ids(session: Session, world_id: str,
                   organization_id: int) -> List[str]:
    return [
        m.character_id
        for m in session.query(OrganizationMember)
        .filter_by(organization_id=organization_id).all()
    ]


def alive_character_ids(session: Session, world_id: str) -> List[str]:
    return [
        c.id
        for c in session.query(Character).filter_by(world_id=world_id).all()
        if getattr(c, "alive", False)
    ]


def find_character(session: Session, world_id: str,
                   character_id: Optional[str]):
    if not character_id:
        return None
    return session.query(Character).filter_by(
        world_id=world_id, id=character_id).first()
