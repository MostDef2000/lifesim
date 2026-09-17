"""M5 (SPEC §104/R6): player goal intent parsing.

Tier-0 deterministic parser (CI-safe, П2). When llm.enabled, the same text
goes through the M4 transport with a 'goal' schema hint — same output shape.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.db.models import Character, Location

TRAVEL_WORDS = re.compile(r"\b(иди|пойди|съезди|езд|отправься|сходи|go to|travel)\b", re.I)
SOCIAL_WORDS = re.compile(r"\b(поговори|познакомься|обсуди|свидание|talk|meet|chat)\b", re.I)
ACQUIRE_WORDS = re.compile(r"\b(купи|добудь|приобрети|закупи|buy|get|acquire)\b", re.I)


def parse_intent(text: str) -> Dict[str, Any]:
    """Deterministic Tier-0 parse: returns {goal_type, params} or {goal_type: None}."""
    normalized = (text or "").strip().lower()
    if not normalized:
        return {"goal_type": None, "params": {}}

    # acquire_items: «купи инструменты» / «купи еду в городе»
    if ACQUIRE_WORDS.search(normalized):
        item_key = _extract_item_key(normalized)
        if item_key:
            return {"goal_type": "acquire_items", "params": {"item_key": item_key}}
        return {"goal_type": None, "params": {}}

    if SOCIAL_WORDS.search(normalized):
        return {"goal_type": "socialize_with", "params": {}}  # target resolved later

    if TRAVEL_WORDS.search(normalized):
        return {"goal_type": "travel_to", "params": {}}  # destination resolved later

    return {"goal_type": None, "params": {}}


_ITEM_SYNONYMS = {
    "инструменты": "tool", "инструмент": "tool", "tools": "tool",
    "еду": "food", "еда": "food", "провизию": "food", "food": "food",
    "воду": "water", "water": "water", "лекарства": "medicine", "medicine": "medicine",
    "одежду": "clothes", "clothes": "clothes", "книгу": "book", "книги": "book", "book": "book",
}


def _extract_item_key(normalized: str) -> Optional[str]:
    for word, key in _ITEM_SYNONYMS.items():
        if word in normalized:
            return key
    m = re.search(r"(?:купи|buy|get|acquire)\s+([a-zа-яё]+)", normalized)
    if m:
        return m.group(1)
    return None


def resolve_goal_params(
    session: Session,
    world_id: str,
    goal_type: str,
    params: Dict[str, Any],
    text: str,
) -> Dict[str, Any]:
    """
    Ground parsed intent against world facts (R6 validation stage 1).
    Raises ValueError with a user-facing reason when grounding fails.
    """
    normalized = (text or "").strip().lower()

    if goal_type == "travel_to":
        locs = session.query(Location).filter_by(world_id=world_id).all()
        target = None
        for loc in locs:
            if loc.name and loc.name.lower() in normalized:
                target = loc
                break
        if target is None:
            # fallback: known type words
            for t in ("shop", "workplace", "well", "kitchen"):
                if t in normalized:
                    target = (
                        session.query(Location)
                        .filter_by(world_id=world_id, type=t)
                        .order_by(Location.id)
                        .first()
                    )
                    break
        if target is None:
            raise ValueError("destination not recognized in world")
        return {"destination_location_id": target.id, "destination_name": target.name}

    if goal_type == "socialize_with":
        # find a name mention among alive characters
        candidates = session.query(Character).filter(
            Character.world_id == world_id, Character.alive == True  # noqa: E712
        ).all()
        for c in candidates_sorted(candidates):
            full = f"{c.first_name} {c.last_name}".strip().lower()
            if full and full in normalized:
                return {"target_character_id": c.id}
            if c.first_name and c.first_name.lower() in normalized:
                return {"target_character_id": c.id}
        # no explicit target: pick best co-located later (conversion stage);
        # MVP: leave empty → conversion fails gracefully
        return {}

    if goal_type == "acquire_items":
        return params  # item_key already extracted

    raise ValueError("unsupported goal_type")


def candidates_sorted(chars):
    return sorted(chars, key=lambda c: len(f"{c.first_name} {c.last_name}".strip()), reverse=True)
