"""M4 AI Worker (SPEC §52): drain -> prompt -> transport -> validate -> result.

Fixed daily order (spec R4): interactive/critical drain -> memory capture +
consolidation -> background drain. All failures stay inside the worker;
the world keeps its deterministic path (constitution П2).
"""
import json
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.ai import gateway as ai_gateway
from app.ai.prompts import build_decision_prompt, build_dialogue_prompt
from app.ai.schemas import validate_response
from app.ai.transports import LlmTransport, build_transport
from app.db.models import AiRequest, Character, CharacterTask, DialogueTurn
from app.events.events import EventType, log_event


def _counts_today(session: Session, world_id: str, day_start: int) -> Dict:
    rows = (
        session.query(AiRequest)
        .filter(
            AiRequest.world_id == world_id,
            AiRequest.processed_at >= day_start,
            AiRequest.processed_at < day_start + 1440,
            AiRequest.status.in_(["done"]),
        )
        .all()
    )
    per_task: Dict[str, int] = {}
    for r in rows:
        per_task[r.task] = per_task.get(r.task, 0) + 1
    return {"total": sum(per_task.values()), "per_task": per_task}


def _process_request(session: Session, world_id: str, settings, req: AiRequest,
                     transport: LlmTransport, day_start: int,
                     counts: Dict) -> None:
    budgets = settings.llm.budgets
    if counts["total"] >= budgets.max_per_day:
        req.status, req.error = "skipped", "budget: max_per_day"
        req.processed_at = day_start
        return
    if counts["per_task"].get(req.task, 0) >= budgets.per_task.get(
            req.task, 0):
        req.status, req.error = "skipped", f"budget: {req.task}"
        req.processed_at = day_start
        return

    raw, data, error = None, None, ""
    for _ in range(settings.llm.retry + 1):
        prompt_bundle = _build_prompt(session, world_id, settings, req)
        prompt = prompt_bundle["prompt"]
        schema_hint = _SCHEMA_HINTS[req.task]
        try:
            raw = transport.complete(prompt, schema_hint)
        except Exception as exc:  # transport failure -> retry -> failed
            error = f"transport error: {exc}"
            continue
        ok, data, error = validate_response(
            req.task, raw, prompt_bundle, settings.llm.min_confidence)
        if ok:
            break

    if data is None:
        req.status = "failed"
        req.error = error or "unknown failure"
        req.processed_at = day_start
        return

    req.status = "done"
    req.result = data
    req.processed_at = day_start
    counts["total"] += 1
    counts["per_task"][req.task] = counts["per_task"].get(req.task, 0) + 1
    session.flush()

    if req.task == "decide":
        _apply_decision(session, world_id, req)


_SCHEMA_HINTS = {
    "decide": "decision",
    "classify": "classify",
    "dialogue": "dialogue",
    "summarize": "summarize",
}


def _build_prompt(session: Session, world_id: str, settings, req: AiRequest
                  ) -> Dict:
    if req.task == "decide":
        bundle = build_decision_prompt(
            session, world_id, req.character_id, settings,
            req.context.get("event_type", "UNKNOWN"), now=req.game_timestamp)
        req.context = {**req.context,
                       "available_actions": bundle["available_actions"],
                       "nearby": bundle["nearby"]}
        return bundle
    if req.task == "dialogue":
        return {"prompt": build_dialogue_prompt(
            session, world_id, req.character_id,
            req.context.get("messages", []))}
    if req.task == "classify":
        return {"prompt": f"current_event: "
                f"{req.context.get('event_type', 'UNKNOWN')}\n"
                "Classify importance 0-100. Reply strict JSON: "
                '{"importance": int}'}
    if req.task == "summarize":
        from app.ai.prompts import build_summarize_prompt
        return {"prompt": build_summarize_prompt(
            req.context.get("summaries", []))}
    return {"prompt": json.dumps(req.context)}


def _apply_decision(session: Session, world_id: str, req: AiRequest) -> None:
    """Spec R7: a valid decision is recorded as an event and executed via
    the existing M2 path (SOCIALIZE task, source='ai') — constitution П1:
    the LLM proposes intent, existing mechanics mutate the world."""
    data = req.result or {}
    decision = data.get("decision")
    target = data.get("target_character_id")

    applied = decision == "socialize_with" and target
    if applied:
        me = session.query(Character).filter_by(
            world_id=world_id, id=req.character_id).first()
        other = session.query(Character).filter_by(
            world_id=world_id, id=target).first()
        applied = (
            me is not None and me.alive
            and other is not None and other.alive
            and me.location_id == other.location_id
        )
    if applied:
        ts = req.game_timestamp
        session.add(CharacterTask(
            character_id=req.character_id,
            priority=1,
            task_type="SOCIALIZE",
            target_id=target,
            status="STARTED",
            source="ai",
            parameters=json.dumps({
                "target_id": target,
                "affection_at_start": 0.0,
                "request_id": req.id,
            }),
            created_at=ts,
            started_at=ts,
            ends_at=ts + 30,
        ))
        session.flush()

    log_event(
        session, world_id, req.processed_at or req.game_timestamp,
        EventType.AI_DECISION,
        actor_id=req.character_id, target_id=target,
        payload={
            "request_id": req.id,
            "decision": decision,
            "target_character_id": target,
            "confidence": data.get("confidence"),
            "reason": data.get("reason"),
            "applied": bool(applied),
        },
    )


def _drain(session: Session, world_id: str, settings, transport: LlmTransport,
           priorities: List[str], day_start: int, counts: Dict) -> None:
    rows = (
        session.query(AiRequest)
        .filter(
            AiRequest.world_id == world_id,
            AiRequest.status == "pending",
            AiRequest.priority.in_(priorities),
        )
        .order_by(AiRequest.id)
        .all()
    )
    for req in rows:
        _process_request(session, world_id, settings, req, transport,
                         day_start, counts)


def run_ai_phase(session: Session, world_id: str, settings,
                 game_timestamp: int, transport: Optional[LlmTransport] = None
                 ) -> None:
    """Daily AI phase (spec R4). Gate R1 is the first line: llm-off does
    nothing at all (byte-identity, AE5'')."""
    if not settings.llm.enabled:
        return
    if transport is None:
        transport = build_transport(settings.llm)

    day_start = (game_timestamp // 1440) * 1440
    counts = _counts_today(session, world_id, day_start)

    # 1. critical + interactive
    _drain(session, world_id, settings, transport,
           ["critical", "interactive"], day_start, counts)

    # 2. memory capture + consolidation (previous day's window)
    from app.ai.memory import capture_memories, consolidate_memories
    capture_memories(session, world_id, settings, game_timestamp)
    consolidate_memories(session, world_id, settings, game_timestamp,
                         transport)

    # 3. background
    _drain(session, world_id, settings, transport, ["background"],
           day_start, counts)


def process_dialogue(session: Session, world_id: str, settings,
                     character_id: str, messages: List[Dict],
                     session_id: str, game_timestamp: int,
                     transport: Optional[LlmTransport] = None
                     ) -> Optional[str]:
    """Synchronous dialogue (spec R9): enqueue + immediate processing.
    Journal-only: dialogue never mutates the world."""
    if not settings.llm.enabled:
        return None
    req_id = ai_gateway.enqueue_dialogue(
        session, world_id, settings, character_id, messages, game_timestamp,
        session_id)
    for msg in messages:
        session.add(DialogueTurn(
            world_id=world_id, session_id=session_id,
            character_id=character_id, role=msg.get("role", "user"),
            content=msg.get("content", ""), game_timestamp=game_timestamp,
            request_id=req_id))
    if req_id is None:
        return None
    session.flush()
    if transport is None:
        transport = build_transport(settings.llm)

    req = session.query(AiRequest).filter_by(id=req_id).first()
    day_start = (game_timestamp // 1440) * 1440
    _process_request(session, world_id, settings, req, transport,
                     day_start, _counts_today(session, world_id, day_start))
    session.flush()

    if req.status == "done" and req.result:
        reply = req.result.get("reply", "")
        session.add(DialogueTurn(
            world_id=world_id, session_id=session_id,
            character_id=character_id, role="assistant", content=reply,
            game_timestamp=game_timestamp, request_id=req_id))
        session.flush()
        return reply
    return None
