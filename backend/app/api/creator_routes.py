"""020 wave 4 (issue #316): POST /characters/draft — AI character drafting.

Optional editable DRAFT proposals for the character creator (constitution
П2 amendment 2026-11-11): ONE LLM call per operation via the EXISTING
configured transport, strict server-side validation of every value, and a
STRICT no-write invariant — no Character/inventory/event/asset rows, no
AI-request persistence (the queued gateway is deliberately NOT used: it
writes AiRequest rows). The manual creation path stays untouched.

Operations:
- {"operation": "generate", "idea": str(1..500), "revision": int}
  -> {"draft": <validated 19-key draft>, "revision": <body.revision>}
- {"operation": "variant", "field": <draft key>, "draft": <full current
  draft>, "revision": int}
  -> {"field": ..., "value": <validated replacement>, "revision": ...}

Status contract: 401 unauth, 422 bad request shape, 429 rate/busy,
502 invalid model output (unknown keys / wrong types / out-of-range /
unparseable JSON / transport failure), 503 LLM disabled
(settings.llm.enabled=false), 504 LLM timeout. Raw upstream exceptions
never leave the server.

Limits (per-PROCESS, not fleet-wide — documented; in-process state resets
on restart, mirroring the admin rate-limit middleware and the preview
limiter):
- per-user 5 attempts/minute sliding window; FAILED attempts consume
  quota (the append happens at admission, before the LLM call); a 429
  consumes nothing;
- max ONE active request per user AND one per process (in-process flags
  under a lock, released in ``finally`` on EVERY exit path).

NOTE: no `from __future__ import annotations` — it breaks FastAPI
resolution of the locally-defined Pydantic models (same trap as
app.py/ws.py in M5, visual_routes.py).
"""
import collections
import threading
import time

from sqlalchemy.orm import sessionmaker

from app.config.config import Settings


def register_creator_routes(app, settings: Settings, session_factory: sessionmaker) -> None:
    from fastapi import Depends, HTTPException
    from pydantic import BaseModel

    from app.ai.transports import build_transport
    from app.characters.creator import (
        DRAFT_FIELDS,
        IDEA_MAX,
        MAX_RESPONSE_CHARS,
        build_generate_prompt,
        build_variant_prompt,
        generate_schema_hint,
        parse_llm_json,
        validate_draft,
        validate_field_value,
        variant_schema_hint,
    )

    current_user = app.state.current_user

    # ---------- limits (in-process, per-process discipline) ----------
    # Same dependency-free pattern as the preview limiter
    # (visual_routes): sliding 60s window per user in a deque of
    # monotonic timestamps, pruned on access — bounded state (one deque
    # entry per attempt within the window). Per-process, NOT fleet-wide.
    _RATE_LIMIT = 5
    _RATE_WINDOW_SEC = 60.0
    _hits: dict[int, collections.deque] = {}
    _hits_guard = threading.Lock()

    def _rate_limited(user_id: int) -> bool:
        """True when this user already used their 5-attempts quota in the
        current window (the consuming append happens only when allowed —
        a 429 never consumes, mirroring the m216 preview-limiter pin)."""
        now = time.monotonic()
        with _hits_guard:
            bucket = _hits.setdefault(user_id, collections.deque())
            while bucket and now - bucket[0] > _RATE_WINDOW_SEC:
                bucket.popleft()
            if len(bucket) >= _RATE_LIMIT:
                return True
            bucket.append(now)
            return False

    # Single-flight: ONE active draft request per user AND one per
    # process. Both flags live under one guard lock; the release happens
    # in a ``finally`` that covers every exit path after acquisition
    # (validation failures, transport failures, timeouts, success).
    _busy_guard = threading.Lock()
    _process_busy = False
    _user_busy: set[int] = set()

    def _llm_transport():
        # Overridable seam for tests (app.state monkeypatch), mirroring
        # the visual routes' flux_transport_factory seam.
        if hasattr(app.state, "llm_transport_factory"):
            return app.state.llm_transport_factory()
        return build_transport(settings.llm)

    class DraftIn(BaseModel):
        # One body model for both operations (discriminated by
        # ``operation``); irrelevant fields stay None per operation.
        operation: str
        idea: str | None = None
        revision: int = 0
        field: str | None = None
        draft: dict | None = None

    @app.post("/characters/draft")
    def create_draft(body: DraftIn, user=Depends(current_user)):
        """AI character draft (generate | variant). PURE: no DB writes, no
        retries, no repair loops; one transport call per request."""
        import urllib.error

        # 503 gate first — consumes nothing (m216 gate-before-limiter
        # discipline).
        if not settings.llm.enabled:
            raise HTTPException(status_code=503, detail="LLM disabled")
        # Rate limit: admission consumes; failures downstream therefore
        # consume quota too (design: failed attempts consume quota).
        if _rate_limited(user.id):
            raise HTTPException(
                status_code=429, detail="draft rate limit exceeded")
        # ---- request shape (422) — before any LLM work ----
        operation = body.operation
        if operation == "generate":
            idea = (body.idea or "").strip()
            if not idea or len(idea) > IDEA_MAX:
                raise HTTPException(
                    status_code=422, detail=f"idea must be 1..{IDEA_MAX} chars")
            prompt = build_generate_prompt(idea)
            schema_hint = generate_schema_hint()
        elif operation == "variant":
            if body.field not in DRAFT_FIELDS:
                raise HTTPException(
                    status_code=422,
                    detail=f"field must be one of: {', '.join(DRAFT_FIELDS)}")
            try:
                draft = validate_draft(body.draft)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc))
            prompt = build_variant_prompt(body.field, draft)
            schema_hint = variant_schema_hint()
        else:
            raise HTTPException(
                status_code=422, detail="operation must be generate|variant")

        # ---------- single-flight (per user + per process) ----------
        # _process_busy is REBOUND here, and it lives in THIS enclosing
        # function's scope (register_creator_routes) — `nonlocal`, not
        # `global` (a `global` declaration would point at module level and
        # raise NameError on every request).
        nonlocal _process_busy
        with _busy_guard:
            if _process_busy or user.id in _user_busy:
                raise HTTPException(
                    status_code=429, detail="draft already in progress")
            _process_busy = True
            _user_busy.add(user.id)
        try:
            transport = _llm_transport()
            try:
                raw = transport.complete(
                    prompt, schema_hint, max_chars=MAX_RESPONSE_CHARS)
            except HTTPException:
                raise
            except TimeoutError:
                # socket.timeout is an alias of TimeoutError (py3.10+):
                # both map to 504, upstream detail never leaks.
                raise HTTPException(
                    status_code=504, detail="LLM request timed out") from None
            except urllib.error.URLError as exc:
                if isinstance(getattr(exc, "reason", None), TimeoutError):
                    raise HTTPException(
                        status_code=504,
                        detail="LLM request timed out") from None
                raise HTTPException(
                    status_code=502, detail="LLM transport failed") from None
            except ValueError:
                # transport-level ValueError (empty content / response
                # bound exceeded) — generic detail, no upstream leak.
                raise HTTPException(
                    status_code=502, detail="LLM transport failed") from None
            except Exception:
                # Boundary: any other upstream failure is a generic 502
                # (never a raw exception leak).
                raise HTTPException(
                    status_code=502, detail="LLM transport failed") from None
            try:
                if operation == "generate":
                    draft = validate_draft(parse_llm_json(raw))
                    return {"draft": draft, "revision": body.revision}
                value = validate_field_value(
                    body.field, parse_llm_json(raw).get("value"))
                return {
                    "field": body.field, "value": value,
                    "revision": body.revision,
                }
            except ValueError as exc:
                raise HTTPException(
                    status_code=502,
                    detail=f"invalid model output: {exc}") from None
        finally:
            # EVERY exit path releases both flags (HTTPExceptions included).
            with _busy_guard:
                _process_busy = False
                _user_busy.discard(user.id)
