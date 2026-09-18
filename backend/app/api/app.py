"""M5 (SPEC §80-81/104): FastAPI application factory.

The API layer is additive: headless simulation never imports this module
(lazy imports inside routes). П2: with api.enabled=false nothing here runs.
"""

import os
from typing import Any

from sqlalchemy.orm import Session, sessionmaker


def get_secret(settings) -> str:
    """HMAC secret from env; dev fallback (R2/NFR-security)."""

    env_name = getattr(getattr(settings, "api", None), "secret_env", "VL1_SECRET")
    secret = os.getenv(env_name) or os.getenv("VL1_SECRET") or "dev-insecure-secret-change-me"
    return secret


def create_app(settings, session_factory: sessionmaker):
    """Build the FastAPI app bound to a sessionmaker and the given Settings."""
    from fastapi import Depends, FastAPI, HTTPException, Request, Response
    from fastapi.responses import JSONResponse
    from pydantic import BaseModel

    from app.api.auth import (
        hash_password,
        sign_token,
        validate_registration,
        verify_password,
        verify_token,
    )
    from app.db.models import Character, User

    app = FastAPI(title="VL1 LifeSim API", version="0.1.0")
    state: dict[str, Any] = {"settings": settings, "session_factory": session_factory}

    # M8 (§88): request-id + access log
    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        import time as _time
        import uuid as _uuid

        request_id = request.headers.get("x-request-id") or str(_uuid.uuid4())
        started = _time.perf_counter()
        response = await call_next(request)
        duration_ms = int((_time.perf_counter() - started) * 1000)
        response.headers["X-Request-ID"] = request_id
        print(
            f"request_id={request_id} method={request.method} "
            f"path={request.url.path} status={response.status_code} "
            f"duration_ms={duration_ms}",
            flush=True,
        )
        return response

    # M8 (§27): per-IP sliding-window rate limits (in-memory, per-process)
    if settings.admin.rate_limit_enabled:
        import collections

        _hits: dict[str, collections.deque] = {}

        def _client_ip(request: Request) -> str:
            forwarded = request.headers.get("x-forwarded-for")
            if forwarded:
                return forwarded.split(",")[0].strip()
            return request.client.host if request.client else "unknown"

        @app.middleware("http")
        async def rate_limit_middleware(request: Request, call_next):
            import time as _time

            ip = _client_ip(request)
            now = _time.time()
            window = settings.admin.rate_limit_window_sec
            is_auth = request.url.path.startswith("/auth/")
            limit = settings.admin.auth_rpm if is_auth else settings.admin.global_rpm
            bucket = _hits.setdefault(ip, collections.deque())
            while bucket and now - bucket[0] > window:
                bucket.popleft()
            if len(bucket) >= limit:
                from fastapi.responses import PlainTextResponse

                return PlainTextResponse(
                    "rate limit exceeded",
                    status_code=429,
                    headers={"Retry-After": str(window)},
                )
            bucket.append(now)
            return await call_next(request)

    app.state.ws_connections = 0

    def db() -> Session:
        s = state["session_factory"]()
        try:
            yield s
        finally:
            s.close()

    def current_user(request: Request, session: Session = Depends(db)) -> User:
        """Auth via httpOnly cookie or Authorization: Bearer (R2)."""
        token = request.cookies.get(settings.api.cookie_name)
        if not token:
            auth = request.headers.get("authorization", "")
            if auth.startswith("Bearer "):
                token = auth[7:]
        if not token:
            raise HTTPException(status_code=401, detail="not authenticated")
        payload = verify_token(token, get_secret(settings))
        if payload is None:
            raise HTTPException(status_code=401, detail="invalid or expired token")
        user = session.get(User, int(payload["sub"]))
        if user is None:
            raise HTTPException(status_code=401, detail="user not found")
        if user.disabled:  # M8 (§84): banned accounts lose API access
            raise HTTPException(status_code=403, detail="account disabled")
        return user

    # ---------- Schemas ----------

    def _world_now(session: Session) -> int:
        """Current game timestamp from the world clock (API-created rows)."""
        from app.db.models import WorldClock

        clock = (
            session.query(WorldClock)
            .filter_by(world_id=state["settings"].world.world_id)
            .first()
        )
        return int(clock.game_timestamp) if clock is not None else 0

    class RegisterIn(BaseModel):
        username: str
        email: str
        password: str
        age_confirmed: bool = False

    class LoginIn(BaseModel):
        username: str
        password: str

    class CharacterIn(BaseModel):
        name: str
        sex: str
        age: int = 18

    class MeOut(BaseModel):
        id: int
        username: str
        email: str
        role: str

    # ---------- Auth routes (§81) ----------

    def _set_cookie(response: Response, user: User) -> None:
        token = sign_token(
            user.id, user.role, get_secret(settings), settings.api.session_ttl_min
        )
        response.set_cookie(
            settings.api.cookie_name,
            token,
            httponly=True,
            samesite="lax",
            max_age=settings.api.session_ttl_min * 60,
        )

    @app.post("/auth/register", status_code=201)
    def register(body: RegisterIn, session: Session = Depends(db)):
        # M8 (§85): registration controls
        if not settings.admin.registration_enabled:
            raise HTTPException(status_code=403, detail="registration disabled")
        if settings.admin.max_players > 0:
            users_count = session.query(User).count()
            if users_count >= settings.admin.max_players:
                raise HTTPException(status_code=409, detail="max players reached")
        errors = validate_registration(body.username, body.email, body.password, body.age_confirmed)
        if errors:
            return JSONResponse(status_code=422, content={"detail": errors})
        dup = (
            session.query(User)
            .filter((User.username == body.username) | (User.email == body.email))
            .first()
        )
        if dup is not None:
            raise HTTPException(status_code=409, detail="username or email already registered")
        user = User(
            username=body.username,
            email=body.email,
            password_hash=hash_password(body.password),
            role="user",
            age_confirmed=True,
            created_at=0,
        )
        session.add(user)
        session.commit()
        return {"id": user.id, "username": user.username}

    @app.post("/auth/login")
    def login(body: LoginIn, response: Response, session: Session = Depends(db)):
        user = session.query(User).filter_by(username=body.username).first()
        if user is None or not verify_password(body.password, user.password_hash):
            raise HTTPException(status_code=401, detail="invalid credentials")
        if user.disabled:  # M8 (§84): account moderation
            raise HTTPException(status_code=403, detail="account disabled")
        _set_cookie(response, user)
        return {"id": user.id, "username": user.username}

    @app.post("/auth/logout")
    def logout(response: Response):
        response.delete_cookie(settings.api.cookie_name)
        return {"ok": True}

    @app.get("/auth/me", response_model=MeOut)
    def me(user: User = Depends(current_user)):
        return MeOut(id=user.id, username=user.username, email=user.email, role=user.role)

    # ---------- Characters (R3) ----------

    @app.post("/characters", status_code=201)
    def create_character(
        body: CharacterIn, user: User = Depends(current_user), session: Session = Depends(db)
    ):
        from app.characters.player import create_player_character

        settings_ = state["settings"]
        world_id = settings_.world.world_id
        try:
            character = create_player_character(
                session, settings_, world_id, user,
                name=body.name.strip(), sex=body.sex, age=body.age,
                game_timestamp=0,
            )
        except ValueError as exc:
            detail = str(exc)
            status = 409 if ("taken" in detail or "already owns" in detail) else 422
            raise HTTPException(status_code=status, detail=detail)
        session.commit()
        return {"id": character.id, "name": f"{character.first_name} {character.last_name}".strip(),
                "control_mode": character.control_mode, "location_id": character.location_id}

    # ---------- Control modes (R4, §60-61) ----------

    class ControlIn(BaseModel):
        mode: str

    def _owned_character(session: Session, user: User, cid: str):
        character = (
            session.query(Character)
            .filter_by(world_id=state["settings"].world.world_id, id=cid)
            .first()
        )
        if character is None:
            raise HTTPException(status_code=404, detail="character not found")
        if character.user_id != user.id:
            raise HTTPException(status_code=403, detail="not your character")
        return character

    @app.post("/characters/{cid}/control")
    def set_control(
        cid: str, body: ControlIn,
        user: User = Depends(current_user), session: Session = Depends(db)
    ):
        from app.db.models import CharacterTask
        from app.events.events import EventType, log_event

        if body.mode not in ("AUTONOMOUS", "GUIDED", "DIRECT"):
            raise HTTPException(status_code=422, detail="mode must be AUTONOMOUS|GUIDED|DIRECT")
        character = _owned_character(session, user, cid)
        if character.control_mode == body.mode:
            return {"id": cid, "control_mode": character.control_mode, "cancelled": 0}

        now_ts = _world_now(session)
        cancelled = 0
        if body.mode == "DIRECT":
            # §61: stop AI command — cancel active and planned non-player tasks
            tasks = (
                session.query(CharacterTask)
                .filter(
                    CharacterTask.character_id == cid,
                    CharacterTask.status.in_(("active", "planned")),
                    CharacterTask.source != "player",
                )
                .all()
            )
            for t in tasks:
                t.status = "cancelled"
                if t.ends_at is None or t.ends_at > now_ts:
                    t.ends_at = now_ts  # stop the clock: §61 "останавливается"
                cancelled += 1

        log_event(
            session, character.world_id, now_ts, EventType.CONTROL_CHANGED,
            actor_id=cid,
            payload={"user_id": user.id, "from": character.control_mode, "to": body.mode},
        )
        character.control_mode = body.mode
        character.updated_at = now_ts
        session.commit()
        return {"id": cid, "control_mode": body.mode, "cancelled": cancelled}

    # ---------- Direct actions (R5, §80) ----------

    class ActionIn(BaseModel):
        character_id: str
        action_type: str
        params: dict = {}

    @app.post("/actions", status_code=201)
    def post_action(
        body: ActionIn, user: User = Depends(current_user), session: Session = Depends(db)
    ):
        from app.actions.lifecycle import enqueue_task
        from app.actions.registry import ACTION_REGISTRY
        from app.actions.validators import validate

        character = _owned_character(session, user, body.character_id)
        if character.control_mode == "AUTONOMOUS":
            raise HTTPException(
                status_code=409,
                detail="character is AUTONOMOUS; switch to GUIDED or DIRECT first (§61)",
            )
        if not any(a.name == body.action_type for a in ACTION_REGISTRY) and \
                body.action_type != "TRAVEL_EXTERNAL":
            raise HTTPException(status_code=422, detail=f"unknown action_type {body.action_type}")

        now_ts = _world_now(session)
        ok, reason, needs_move_id, vparams = validate(
            session, character.world_id, character, body.action_type, now_ts,
            state["settings"], params=body.params or {},
        )
        if not ok:
            raise HTTPException(status_code=422, detail=f"action not possible: {reason}")

        task = enqueue_task(
            session, character.world_id, character, body.action_type,
            "player", {**vparams, **body.params}, now_ts, state["settings"],
        )
        if task is None:
            raise HTTPException(status_code=409, detail="character already has an active task")
        session.commit()
        return {"task_id": task.id, "task_type": task.task_type, "status": task.status}

    @app.get("/actions/{task_id}")
    def get_action(
        task_id: str, user: User = Depends(current_user), session: Session = Depends(db)
    ):
        from app.db.models import CharacterTask

        task = session.get(CharacterTask, int(task_id))
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        character = (
            session.query(Character).filter_by(id=task.character_id).first()
        )
        if character is None or character.user_id != user.id:
            raise HTTPException(status_code=403, detail="not your character")
        return {
            "task_id": task.id, "task_type": task.task_type, "status": task.status,
            "source": task.source, "started_at": task.started_at, "ends_at": task.ends_at,
        }

    # ---------- Goals (R6, §62) ----------

    class GoalIn(BaseModel):
        text: str

    @app.post("/characters/{cid}/goals", status_code=201)
    def post_goal(
        cid: str, body: GoalIn,
        user: User = Depends(current_user), session: Session = Depends(db)
    ):
        from app.api.intent import parse_intent, resolve_goal_params
        from app.db.models import CharacterGoal
        from app.events.events import EventType, log_event

        character = _owned_character(session, user, cid)
        text = (body.text or "").strip()
        if not text or len(text) > 2000:
            raise HTTPException(status_code=422, detail="text must be 1..2000 chars")

        parsed = parse_intent(text)
        if parsed["goal_type"] is None:
            raise HTTPException(
                status_code=422,
                detail="intent not recognized "
                       "(MVP catalog: travel_to, socialize_with, acquire_items)",
            )
        try:
            params = resolve_goal_params(
                session, character.world_id, parsed["goal_type"], parsed["params"], text
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

        now_ts = _world_now(session)
        day = now_ts // 1440
        goal = CharacterGoal(
            world_id=character.world_id,
            character_id=cid,
            goal_type=parsed["goal_type"],
            params=params,
            status="queued",
            deadline_day=day + 1,
            source_text=text,
            created_at=now_ts,
            updated_at=now_ts,
        )
        session.add(goal)
        session.flush()
        log_event(
            session, character.world_id, now_ts, EventType.GOAL_QUEUED,
            actor_id=cid,
            payload={"goal_id": goal.id, "goal_type": goal.goal_type, "params": params},
        )
        session.commit()
        return {
            "goal_id": goal.id, "goal_type": goal.goal_type,
            "params": params, "status": "queued",
        }

    @app.get("/characters/{cid}/goals")
    def get_goals(cid: str, user: User = Depends(current_user), session: Session = Depends(db)):
        from app.db.models import CharacterGoal

        character = _owned_character(session, user, cid)
        goals = (
            session.query(CharacterGoal)
            .filter_by(world_id=character.world_id, character_id=cid)
            .order_by(CharacterGoal.id)
            .all()
        )
        return [
            {
                "goal_id": g.id, "goal_type": g.goal_type, "params": g.params,
                "status": g.status, "deadline_day": g.deadline_day, "source_text": g.source_text,
            }
            for g in goals
        ]

    # ---------- World / read endpoints (R8, §80) ----------

    @app.get("/world")
    def get_world(session: Session = Depends(db)):
        from app.db.models import Character, World, WorldClock

        wid = state["settings"].world.world_id
        world = session.get(World, wid)
        if world is None:
            raise HTTPException(status_code=404, detail="world not found")
        clock = session.query(WorldClock).filter_by(world_id=wid).first()
        population = (
            session.query(Character)
            .filter_by(world_id=wid, alive=True)
            .count()
        )
        return {
            "id": wid, "seed": world.seed,
            "game_timestamp": clock.game_timestamp if clock else 0,
            "day": (clock.game_timestamp // 1440) if clock else 0,
            "population": population,
        }

    @app.get("/world/events")
    def get_world_events(
        since: int = 0, limit: int = 100, session: Session = Depends(db)
    ):
        from app.db.models import WorldEvent

        limit = max(1, min(limit, 500))
        events = (
            session.query(WorldEvent)
            .filter_by(world_id=state["settings"].world.world_id)
            .filter(WorldEvent.id > since)
            .order_by(WorldEvent.id)
            .limit(limit)
            .all()
        )
        import json as _json

        return [
            {
                "id": e.id, "event_type": e.event_type, "actor_id": e.actor_id,
                "location_id": e.location_id, "day": e.game_timestamp // 1440,
                "game_timestamp": e.game_timestamp,
                "payload": _json.loads(e.payload) if e.payload else {},
            }
            for e in events
        ]

    @app.get("/locations/{loc_id}")
    def get_location(loc_id: int, session: Session = Depends(db)):
        from app.db.models import Character, Location

        loc = session.get(Location, loc_id)
        if loc is None or loc.world_id != state["settings"].world.world_id:
            raise HTTPException(status_code=404, detail="location not found")
        here = (
            session.query(Character)
            .filter_by(world_id=loc.world_id, location_id=loc.id, alive=True)
            .all()
        )
        return {
            "id": loc.id, "type": loc.type, "name": loc.name,
            "characters_here": [c.id for c in here],
        }

    @app.get("/characters/{cid}")
    def get_character(cid: str, user: User = Depends(current_user), session: Session = Depends(db)):
        from app.db.models import CharacterJob, CharacterNeeds, Job

        character = (
            session.query(Character)
            .filter_by(world_id=state["settings"].world.world_id, id=cid)
            .first()
        )
        if character is None:
            raise HTTPException(status_code=404, detail="character not found")
        is_owner = character.user_id == user.id
        out = {
            "id": character.id,
            "name": f"{character.first_name} {character.last_name}".strip(),
            "sex": character.sex, "age": character.age,
            "alive": character.alive,
            "location_id": character.location_id,
            "control_mode": character.control_mode,
            "is_owner": is_owner,
        }
        if is_owner:
            needs = (
                session.query(CharacterNeeds).filter_by(character_id=cid).first()
            )
            if needs is not None:
                out["needs"] = {
                    "hunger": needs.hunger, "energy": needs.energy,
                    "thirst": needs.thirst, "social": needs.social,
                }
            job = (
                session.query(CharacterJob, Job)
                .join(Job, Job.id == CharacterJob.job_id)
                .filter(CharacterJob.character_id == cid)
                .first()
            )
            out["job"] = job[1].title if job else None
        return out

    @app.get("/characters/{cid}/inventory")
    def get_inventory(cid: str, user: User = Depends(current_user), session: Session = Depends(db)):
        from app.db.models import WorldObject

        character = (
            session.query(Character)
            .filter_by(world_id=state["settings"].world.world_id, id=cid)
            .first()
        )
        if character is None:
            raise HTTPException(status_code=404, detail="character not found")
        if character.user_id != user.id:
            raise HTTPException(status_code=403, detail="not your character")
        items = (
            session.query(WorldObject)
            .filter_by(owner_character_id=cid)
            .order_by(WorldObject.id)
            .all()
        )
        return [
            {
                "id": o.id, "object_type": o.object_type,
                "quantity": o.quantity, "location_id": o.location_id,
            }
            for o in items
        ]

    # ---------- Dialogue / chat (R7, §63-65) ----------

    class DialogueStartIn(BaseModel):
        npc_id: str

    class DialogueMessageIn(BaseModel):
        content: str

    def _dialogue_session_owned(session: Session, user: User, session_id: str):
        from app.db.models import DialogueSession

        row = (
            session.query(DialogueSession)
            .filter_by(id=session_id, user_id=user.id)
            .first()
        )
        if row is None:
            raise HTTPException(status_code=404, detail="dialogue session not found")
        return row

    @app.post("/dialogue/start", status_code=201)
    def dialogue_start(
        body: DialogueStartIn,
        user: User = Depends(current_user), session: Session = Depends(db),
    ):
        from app.db.models import Character as _C
        from app.db.models import DialogueSession

        character = _owned_character_by_user(session, user)
        npc = (
            session.query(_C)
            .filter_by(world_id=character.world_id, id=body.npc_id)
            .first()
        )
        if npc is None:
            raise HTTPException(status_code=404, detail="npc not found")
        if not npc.alive:
            raise HTTPException(status_code=422, detail="npc is not alive")
        if npc.user_id is not None:
            raise HTTPException(status_code=422, detail="cannot chat with a player character")

        sid = f"dlg_{user.id}_{npc.id}_{_world_now(session)}"
        row = DialogueSession(
            id=sid,
            world_id=character.world_id,
            user_id=user.id,
            character_id=character.id,
            npc_id=npc.id,
            location_id=character.location_id,
            started_at=_world_now(session),
            ended_at=None,
            context={},
        )
        session.add(row)
        session.commit()
        return {"session_id": sid, "npc_id": npc.id, "character_id": character.id}

    def _owned_character_by_user(session: Session, user: User) -> Character:
        character = (
            session.query(Character)
            .filter_by(
                world_id=state["settings"].world.world_id,
                user_id=user.id, alive=True,
            )
            .first()
        )
        if character is None:
            raise HTTPException(status_code=422, detail="you do not own a living character")
        return character

    @app.post("/dialogue/{session_id}/message")
    def dialogue_message(
        session_id: str, body: DialogueMessageIn,
        user: User = Depends(current_user), session: Session = Depends(db),
    ):
        from app.api.dialogue import (
            build_context,
            fallback_reply,
            llm_reply,
            safety_check,
            suggested_responses,
        )
        from app.db.models import Character as _C
        from app.db.models import DialogueMessage

        row = _dialogue_session_owned(session, user, session_id)
        content = body.content or ""
        error = safety_check(content)
        if error:
            raise HTTPException(status_code=422, detail=error)

        user_char = session.get(_C, row.character_id)
        npc = session.get(_C, row.npc_id)
        if npc is None or not npc.alive or user_char is None or not user_char.alive:
            raise HTTPException(status_code=422, detail="dialogue participants must be alive")

        now_ts = _world_now(session)
        session.add(DialogueMessage(
            session_id=row.id, sender="user", content=content,
            game_timestamp=now_ts,
        ))
        session.flush()

        context = build_context(session, row.world_id, user_char, npc)
        history = [
            {"sender": m.sender, "content": m.content}
            for m in session.query(DialogueMessage)
            .filter_by(session_id=row.id)
            .order_by(DialogueMessage.id)
            .all()
        ]
        reply = llm_reply(session, state["settings"], row.world_id, row, history, context, content)
        if reply is None:
            player_name = user_char.first_name
            reply = fallback_reply(context, player_name)
        suggestions = suggested_responses(context)

        session.add(DialogueMessage(
            session_id=row.id, sender="npc", content=reply,
            suggested_responses=suggestions, game_timestamp=now_ts,
        ))
        session.commit()
        return {
            "npc_reply": reply,
            "suggested_responses": suggestions,
            "session_id": row.id,
        }

    @app.get("/dialogue/{session_id}")
    def dialogue_get(
        session_id: str,
        user: User = Depends(current_user), session: Session = Depends(db),
    ):
        from app.db.models import DialogueMessage

        row = _dialogue_session_owned(session, user, session_id)
        messages = (
            session.query(DialogueMessage)
            .filter_by(session_id=row.id)
            .order_by(DialogueMessage.id)
            .all()
        )
        return {
            "session_id": row.id,
            "character_id": row.character_id, "npc_id": row.npc_id,
            "started_at": row.started_at, "ended_at": row.ended_at,
            "messages": [
                {
                    "sender": m.sender, "content": m.content,
                    "suggested_responses": m.suggested_responses,
                    "game_timestamp": m.game_timestamp,
                }
                for m in messages
            ],
        }

    # Expose dependency accessors for sub-routers added in later chunks
    app.state.settings = settings
    app.state.session_factory = session_factory
    app.state.current_user = current_user
    app.state.db = db

    # M5 (R9, §79): WebSocket event stream
    from app.api.ws import register_ws_route

    register_ws_route(app, settings, session_factory)

    # ---------- External Vladivostok (M7, §38-39/106) ----------

    @app.get("/external")
    def get_external(
        user: User = Depends(current_user), session: Session = Depends(db)
    ):
        from app.db.models import ExternalLocation, ExternalService

        locations = (
            session.query(ExternalLocation)
            .filter_by(world_id=state["settings"].world.world_id)
            .order_by(ExternalLocation.id)
            .all()
        )
        services = (
            session.query(ExternalService)
            .filter_by(world_id=state["settings"].world.world_id)
            .order_by(ExternalService.id)
            .all()
        )
        by_loc: dict = {}
        for svc in services:
            by_loc.setdefault(svc.external_location_id, []).append({
                "id": svc.id, "service_type": svc.service_type,
                "item_type": svc.item_type, "price": svc.price,
                "heal_amount": svc.heal_amount,
                "duration_minutes": svc.duration_minutes,
            })
        return [
            {
                "id": loc.id, "name": loc.name, "ext_type": loc.ext_type,
                "description": loc.description, "services": by_loc.get(loc.id, []),
            }
            for loc in locations
        ]

    @app.get("/characters/{cid}/contacts")
    def get_contacts(
        cid: str, user: User = Depends(current_user), session: Session = Depends(db)
    ):
        from app.db.models import ExternalContact

        character = _owned_character(session, user, cid)
        contacts = (
            session.query(ExternalContact)
            .filter_by(world_id=state["settings"].world.world_id, character_id=character.id)
            .order_by(ExternalContact.id)
            .all()
        )
        return [
            {
                "id": c.id, "contact_type": c.contact_type, "name": c.name,
                "external_location_id": c.external_location_id, "note": c.note,
            }
            for c in contacts
        ]

    # M6 (R1, §105): visual routes behind the visual.enabled gate
    from app.api.visual_routes import register_visual_routes

    register_visual_routes(app, settings, session_factory)
    return app
