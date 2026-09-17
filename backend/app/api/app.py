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
        if not any(a.name == body.action_type for a in ACTION_REGISTRY):
            raise HTTPException(status_code=422, detail=f"unknown action_type {body.action_type}")

        now_ts = _world_now(session)
        ok, reason, needs_move_id, vparams = validate(
            session, character.world_id, character, body.action_type, now_ts, state["settings"]
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

    # Expose dependency accessors for sub-routers added in later chunks
    app.state.settings = settings
    app.state.session_factory = session_factory
    app.state.current_user = current_user
    app.state.db = db
    return app
