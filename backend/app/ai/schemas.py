"""M4 AI output validation (SPEC §53, §117 'AI JSON validation').

Every transport response is parsed and schema-checked before it may touch
the world. Validation failure -> retry -> failed request; the simulation
continues deterministically.
"""
import json
from typing import Any, Dict, Optional, Tuple

from app.db.models import Character


def validate_response(
    task: str,
    raw: str,
    context: Dict[str, Any],
    min_confidence: float,
) -> Tuple[bool, Optional[Dict], str]:
    """Returns (ok, data, error). ok=False means invalid output."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return False, None, "invalid JSON"

    if not isinstance(data, dict):
        return False, None, "response is not a JSON object"

    if task == "decide":
        return _validate_decision(data, context, min_confidence)
    if task == "classify":
        return _validate_classify(data)
    if task == "dialogue":
        return _validate_dialogue(data)
    if task == "summarize":
        summary = data.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            return False, None, "summary must be a non-empty string"
        return True, {"summary": summary.strip()}, ""
    return False, None, f"unknown task: {task}"


def _confidence_ok(data: Dict, min_confidence: float) -> Tuple[bool, str]:
    conf = data.get("confidence")
    if not isinstance(conf, (int, float)) or isinstance(conf, bool):
        return False, "confidence must be a number"
    if not 0.0 <= float(conf) <= 1.0:
        return False, "confidence out of range"
    if float(conf) < min_confidence:
        return False, "confidence below threshold"
    return True, ""


def _validate_decision(data: Dict, context: Dict, min_confidence: float
                       ) -> Tuple[bool, Optional[Dict], str]:
    decision = data.get("decision")
    available = context.get("available_actions", [])
    if decision not in available:
        return False, None, f"decision '{decision}' not in available actions"

    ok, err = _confidence_ok(data, min_confidence)
    if not ok:
        return False, None, err

    target = data.get("target_character_id")
    nearby = context.get("nearby", [])
    if target is not None and target not in nearby:
        return False, None, "target_character_id is not a nearby character"

    reason = data.get("reason")
    if reason is not None and not isinstance(reason, str):
        return False, None, "reason must be a string"

    return True, {
        "decision": decision,
        "target_character_id": target,
        "confidence": float(data["confidence"]),
        "reason": reason,
    }, ""


def _validate_classify(data: Dict) -> Tuple[bool, Optional[Dict], str]:
    importance = data.get("importance")
    if not isinstance(importance, int) or isinstance(importance, bool):
        return False, None, "importance must be an integer"
    if not 0 <= importance <= 100:
        return False, None, "importance out of range"
    return True, {"importance": importance}, ""


def _validate_dialogue(data: Dict) -> Tuple[bool, Optional[Dict], str]:
    reply = data.get("reply")
    if not isinstance(reply, str) or not reply.strip():
        return False, None, "reply must be a non-empty string"
    if len(reply) > 2000:
        return False, None, "reply exceeds 2000 characters"
    intent = data.get("intent")
    if intent is not None and not isinstance(intent, str):
        return False, None, "intent must be a string or null"
    # Dialogue does not gate on min_confidence: conversation is journal-only.
    return True, {
        "reply": reply.strip(),
        "intent": intent,
        "confidence": data.get("confidence"),
    }, ""


def target_is_valid_character(session, world_id: str,
                              character_id: Optional[str]) -> bool:
    if not character_id:
        return False
    row = session.query(Character).filter_by(
        world_id=world_id, id=character_id).first()
    return row is not None and row.alive
