"""#60 — LLM interpreter for the Desire layer (catalog stays the fallback).

Layered ON TOP of the pure keyword classifier in ``app.desire.desire``:

- ``llm_interpret_desire(text, settings)`` — the LLM seam, mirroring
  ``app.api.dialogue.llm_reply``: returns ``None`` whenever the LLM path is
  unavailable or unusable (llm disabled, transport build failure, transport
  exception, non-JSON reply, ``catalog_key`` outside the CATALOG whitelist)
  — the caller then falls back to the deterministic catalog. An LLM-answered
  «unsupported» (``catalog_key`` null) is a USABLE answer, not a failure: it
  surfaces the LLM clarification (sanitized; the catalog's generic
  CLARIFICATION_UNSUPPORTED when empty) and is NOT re-classified here.
- ``resolve_desire_interpretation(text, settings)`` — the single entry the
  endpoints call: LLM-first, deterministic fallback on ANY LLM failure.

Honesty boundaries (spec specs/004-llm R13):

- The whitelist is enforced in CODE: ``catalog_key`` must be a CATALOG key;
  ``title_ru`` always comes from CATALOG, never from the reply — the model
  cannot rename or invent domains.
- The reply can only fill three fields (``catalog_key``,
  ``confidence_note``, ``clarification``); notes are sanitized
  (strip + collapse whitespace, bounded length).
- The LLM path never mutates anything: interpretation is data; the planner
  is not invoked from here.
- The deterministic dict shape is UNCHANGED and carries NO ``interpreted_by``
  field (absence = catalog); the LLM path marks ``"interpreted_by": "llm"``.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from app.desire.desire import CATALOG, CLARIFICATION_UNSUPPORTED, interpret_desire

MAX_NOTE_LEN = 200
MAX_CLARIFICATION_LEN = 300


def _sanitize(value: Any, limit: int) -> str:
    """Strip + collapse whitespace + hard-truncate (LLM text is untrusted)."""
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit]


def _desire_prompt(text: str) -> str:
    """Compact RU instruction: 4 domains (title + 3 example keywords each)."""
    choices = "|".join(CATALOG)
    domains = "; ".join(
        f"{key} — {entry['title_ru']} "
        f"(примеры: {', '.join(entry['keywords'][:3])})"
        for key, entry in CATALOG.items()
    )
    return (
        "Классифицируй желание жителя острова по каталогу ровно из 4 тем.\n"
        f"Темы: {domains}.\n"
        "Придумывать новые темы запрещено.\n"
        "Ответь СТРОГО одним JSON-объектом и ничего кроме него:\n"
        f'{{"catalog_key": "{choices}|null", '
        '"confidence_note": "краткое пояснение выбора", '
        '"clarification": "уточняющий вопрос, если catalog_key null, '
        'иначе пустая строка"}\n'
        f"desire_text: {' '.join((text or '').split())}"
    )


def llm_interpret_desire(text: str, settings) -> Optional[Dict[str, Any]]:
    """LLM-first interpretation. ``None`` ⇒ caller must use the catalog.

    Never raises: every failure mode (disabled, transport build/complete
    exception, non-JSON, non-dict, non-string/unhashable catalog_key —
    the whitelist check is type-guarded) degrades to ``None`` —
    prod runs llm.enabled=true with an often-unavailable service, so the
    fallback must be bulletproof.
    """
    if not settings.llm.enabled:
        return None

    from app.ai.transports import build_transport  # lazy, mirrors dialogue.py

    try:
        transport = build_transport(settings.llm)
    except Exception:
        return None
    try:
        raw = transport.complete(_desire_prompt(text), "desire")
    except Exception:
        return None
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = json.loads(raw.strip())
    except ValueError:
        return None
    if not isinstance(parsed, dict):
        return None

    key = parsed.get("catalog_key")
    if key is None:
        # LLM-answered «unsupported» is a usable answer: surface its
        # clarification; do NOT re-classify deterministically here.
        clarification = _sanitize(parsed.get("clarification"), MAX_CLARIFICATION_LEN)
        return {
            "catalog_key": None,
            "clarification": clarification or CLARIFICATION_UNSUPPORTED,
        }
    if not isinstance(key, str) or key not in CATALOG:
        return None  # invented/renamed/unhashable domain → unusable, fallback
    return {
        "catalog_key": key,
        "title_ru": CATALOG[key]["title_ru"],  # from CODE, never from the reply
        "confidence_note": _sanitize(parsed.get("confidence_note"), MAX_NOTE_LEN),
        "matched_keywords": [],
        "interpreted_by": "llm",
    }


def resolve_desire_interpretation(text: str, settings) -> Dict[str, Any]:
    """Single resolver for both desire endpoints: LLM-first, catalog fallback.

    The deterministic dict shape is UNCHANGED and carries NO ``interpreted_by``
    field (absence = catalog; keeps m157 pins and pre-#60 rows consistent);
    the LLM path honestly marks ``"interpreted_by": "llm"``.
    """
    llm_result = llm_interpret_desire(text, settings)
    if llm_result is not None:
        return llm_result
    return interpret_desire(text)
