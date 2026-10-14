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
  #189: usable LLM results are cached in-process (TTL, capped, keyed by the
  sha256 of the normalized text) so preview → create never pays for a
  second LLM round-trip on the same text; the disabled path bypasses the
  cache entirely.

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

import copy
import hashlib
import json
import time
from typing import Any, Dict, Optional

from app.desire.desire import CATALOG, CLARIFICATION_UNSUPPORTED, interpret_desire

MAX_NOTE_LEN = 200
MAX_CLARIFICATION_LEN = 300

# #189 — in-process interpretation cache (UX optimization, NOT a correctness
# layer): with a live-but-slow LLM (prod: home-PC ollama over a tunnel) the
# preview and create endpoints resolve the SAME text twice; the cache makes
# the second resolve free. Per-process and best-effort: a restart clears it.
# Concurrent requests may race benignly: all removals use dict.pop(key, None)
# (never bare ``del``), so a thread sweeping an entry another thread already
# removed cannot raise KeyError — worst case is one redundant LLM call; no
# locks, a plain module-level dict is enough (CPython dict ops on existing
# keys are atomic under the GIL).
DESIRE_CACHE_TTL_SEC = 600  # 10 minutes
DESIRE_CACHE_MAX = 128  # max entries; oldest evicted when over cap
# key: sha256 hex of the normalized text → value: (time.monotonic(), result)
_desire_cache: Dict[str, tuple[float, Dict[str, Any]]] = {}


def _normalize_desire_text(text: str) -> str:
    """Case/whitespace-insensitive normalization for the cache key.

    Same normalization the catalog classifier uses (strip, lowercase,
    collapse runs of whitespace) — «ХОЧУ  найти друзей» and
    «хочу найти ДРУЗЕЙ» are one and the same desire.
    """
    return " ".join((text or "").strip().lower().split())


def _desire_cache_key(text: str) -> str:
    """sha256 hex of the normalized text (bounded, uniform keys).

    The key deliberately ignores settings: one settings object per process,
    and tests clear the cache per test.
    """
    return hashlib.sha256(_normalize_desire_text(text).encode("utf-8")).hexdigest()


def clear_desire_interpretation_cache() -> None:
    """Drop every cached interpretation (tests and future admin use)."""
    _desire_cache.clear()


def _evict_desire_cache(now: float) -> None:
    """Enforce DESIRE_CACHE_MAX: drop expired entries first, then oldest."""
    if len(_desire_cache) <= DESIRE_CACHE_MAX:
        return
    expired = [key for key, (ts, _result) in _desire_cache.items()
               if now - ts >= DESIRE_CACHE_TTL_SEC]
    for key in expired:
        _desire_cache.pop(key, None)  # pop: benign if raced away
    while len(_desire_cache) > DESIRE_CACHE_MAX:
        _desire_cache.pop(
            min(_desire_cache, key=lambda k: _desire_cache[k][0]), None)


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

    #189 — in-process TTL cache (preview → create must not call the LLM
    twice on the same text):

    - Per-process, best-effort ONLY — a restart clears it; it is a UX
      optimization, never a correctness layer.
    - Keyed by the sha256 of the normalized text (case/whitespace-
      insensitive, the same normalization the catalog uses).
    - Only USABLE LLM results are cached (the dict returned by
      ``llm_interpret_desire`` — including the «unsupported»
      catalog_key-None-with-clarification shape). A failure (``None``) is
      NEVER cached — a later retry may succeed.
    - The ``interpreted_by`` marker of the FIRST successful interpretation
      is preserved verbatim: the cache never re-marks or re-classifies.
    - Deterministic results are never cached — absence of ``interpreted_by``
      semantics is unchanged.
    - llm.enabled=false bypasses the cache entirely (not read, not written):
      llm-off behavior is byte-identical to pre-#189 (m157 pins intact).
    - Results are deep-copied on both store and hit — callers cannot
      mutate the cache entry through any nesting level (e.g. the
      ``matched_keywords`` list).
    - TTL math uses ``time.monotonic()`` (monkeypatchable in tests).
    """
    if not settings.llm.enabled:
        # Byte-identical to pre-#189: no cache read, no cache write.
        return interpret_desire(text)

    key = _desire_cache_key(text)
    now = time.monotonic()
    hit = _desire_cache.get(key)
    if hit is not None:
        ts, cached = hit
        if now - ts < DESIRE_CACHE_TTL_SEC:
            return copy.deepcopy(cached)  # protect the cache entry
        _desire_cache.pop(key, None)  # lazy expiry; pop: benign if raced

    llm_result = llm_interpret_desire(text, settings)
    if llm_result is None:
        # Failure is NEVER cached — a later retry may succeed.
        return interpret_desire(text)
    stored_at = time.monotonic()
    # Defensive copy on store too: the first caller keeps the exact dict
    # from the seam, yet mutating it (at any nesting level) can never
    # poison the cache entry.
    _desire_cache[key] = (stored_at, copy.deepcopy(llm_result))
    _evict_desire_cache(stored_at)
    return llm_result
