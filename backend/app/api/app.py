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



def _schema_version(session):
    """Current schema version from SchemaMeta (010: dynamic, was hardcoded)."""
    from app.db.models import SchemaMeta

    row = session.query(SchemaMeta).filter_by(key="version").first()
    return row.value if row is not None else "unknown"

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

    # M8 (§27): per-IP sliding-window rate limits (in-memory, per-process)
    if settings.admin.rate_limit_enabled:
        import collections

        # separate windows per (ip, scope): auth endpoints are stricter
        _hits: dict[tuple[str, str], collections.deque] = {}

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
            # Strict bucket only for credential endpoints (brute-force
            # surface). /auth/me & /auth/logout share the global bucket:
            # a legit re-login after a secret reset must not lock the
            # user out (phase-5 incident, lf.mostdef.ru).
            path = request.url.path
            strict_auth = path in ("/auth/login", "/auth/register")
            scope = "auth" if strict_auth else "global"
            limit = settings.admin.auth_rpm if strict_auth else settings.admin.global_rpm
            bucket = _hits.setdefault((ip, scope), collections.deque())
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

    app.state.ws_connections = 0
    import time as _time

    app.state.started_at = _time.time()

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
        looks: str | None = None  # 014 (§27): appearance, ≤500 chars
        biography: str | None = None  # 014 (§27): backstory, ≤2000 chars

    class MeOut(BaseModel):
        id: int
        username: str
        email: str
        role: str
        ws_token: str  # M9 (§79): browser cannot read the httpOnly cookie

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
        from app.api.auth import sign_token

        token = sign_token(
            user.id, user.role, get_secret(state["settings"]),
            state["settings"].api.session_ttl_min,
        )
        return MeOut(
            id=user.id, username=user.username, email=user.email,
            role=user.role, ws_token=token,
        )

    # ---------- Characters (R3) ----------

    @app.post("/characters", status_code=201)
    def create_character(
        body: CharacterIn, user: User = Depends(current_user), session: Session = Depends(db)
    ):
        from app.characters.player import create_player_character

        looks = (body.looks or "").strip()
        biography = (body.biography or "").strip()
        if len(looks) > 500 or len(biography) > 2000:
            raise HTTPException(
                status_code=422, detail="looks ≤500, biography ≤2000")
        settings_ = state["settings"]
        world_id = settings_.world.world_id
        try:
            character = create_player_character(
                session, settings_, world_id, user,
                name=body.name.strip(), sex=body.sex, age=body.age,
                game_timestamp=0,
                looks=looks or None, biography=biography or None,
            )
        except ValueError as exc:
            detail = str(exc)
            status = 409 if ("taken" in detail or "already owns" in detail) else 422
            raise HTTPException(status_code=status, detail=detail)
        session.commit()
        return {"id": character.id, "name": f"{character.first_name} {character.last_name}".strip(),
                "control_mode": character.control_mode, "location_id": character.location_id,
                "looks": character.looks, "biography": character.biography}

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

    @app.get("/characters/{cid}/tasks")
    def get_character_tasks(
        cid: str, user: User = Depends(current_user), session: Session = Depends(db)
    ):
        from app.db.models import CharacterTask

        _owned_character(session, user, cid)

        planned = (
            session.query(CharacterTask)
            .filter(CharacterTask.character_id == cid, CharacterTask.status == "planned")
            .order_by(CharacterTask.created_at.asc())
            .all()
        )
        active = (
            session.query(CharacterTask)
            .filter(
                CharacterTask.character_id == cid,
                CharacterTask.status.in_(["active", "queued", "STARTED"]),
            )
            .order_by(CharacterTask.created_at.asc())
            .all()
        )
        terminal = (
            session.query(CharacterTask)
            .filter(CharacterTask.character_id == cid, CharacterTask.completed_at.isnot(None))
            .order_by(CharacterTask.completed_at.desc())
            .limit(10)
            .all()
        )

        def task_to_dict(t):
            return {
                "id": t.id, "task_type": t.task_type, "status": t.status,
                "target_id": t.target_id, "source": t.source,
                "created_at": t.created_at, "started_at": t.started_at,
                "ends_at": t.ends_at, "completed_at": t.completed_at,
            }

        return {
            "planned": [task_to_dict(t) for t in planned],
            "active": [task_to_dict(t) for t in active],
            "recent_terminal": [task_to_dict(t) for t in terminal],
        }

    @app.get("/characters/{cid}/relationships")
    def get_character_relationships(
        cid: str, user: User = Depends(current_user), session: Session = Depends(db)
    ):
        from sqlalchemy import or_

        from app.db.models import Character, Relationship

        character = _owned_character(session, user, cid)
        rels = (
            session.query(Relationship)
            .filter(Relationship.world_id == character.world_id,
                    or_(Relationship.character_a == cid, Relationship.character_b == cid))
            .order_by(Relationship.updated_at.desc())
            .all()
        )

        out = []
        for r in rels:
            other_id = r.character_b if r.character_a == cid else r.character_a
            other = session.get(Character, other_id)
            other_name = (
                f"{other.first_name} {other.last_name}".strip()
                if other else other_id
            )
            out.append({
                "other_id": other_id,
                "other_name": other_name,
                "trust": r.trust, "affection": r.affection, "familiarity": r.familiarity,
                "romantic_interest": r.romantic_interest, "updated_at": r.updated_at,
            })
        return out


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

    # ---------- Health (deploy runbook §6) ----------

    @app.get("/health")
    def health():
        """Liveness for systemd/Caddy/uptime checks. No auth, no DB."""
        return {"status": "ok"}

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



    @app.get("/weather")
    def get_weather(session: Session = Depends(db), user: User = Depends(current_user)):
        """010 (§71, R4): current-day weather for the caller's world."""
        from app.db.models import WorldClock
        from app.simulation.weather import (
            describe_weather,
            get_or_create_weather,
        )

        world_id = state["settings"].world.world_id
        clock = session.query(WorldClock).filter_by(world_id=world_id).first()
        current_day = clock.game_timestamp // 1440 if clock is not None else 0
        row = get_or_create_weather(session, world_id, current_day, state["settings"])
        session.commit()
        if row is None:
            return {"enabled": False}
        return {
            "enabled": True,
            "day": row.day,
            "temperature": row.temperature,
            "wind": row.wind,
            "precipitation": row.precipitation,
            "cloudiness": row.cloudiness,
            "visibility": row.visibility,
            "source": row.source,
            "real_date": row.real_date,
            "description": describe_weather(row),
        }

    @app.get("/weather/history")
    def get_weather_history(
        days: int = 7, session: Session = Depends(db), user: User = Depends(current_user)
    ):
        """010 (R4): last N days (<=30), newest first."""
        from app.db.models import WeatherState

        world_id = state["settings"].world.world_id
        days = max(1, min(days, 30))
        rows = (
            session.query(WeatherState)
            .filter_by(world_id=world_id)
            .order_by(WeatherState.day.desc())
            .limit(days)
            .all()
        )
        return [
            {
                "day": r.day, "temperature": r.temperature, "wind": r.wind,
                "precipitation": r.precipitation, "cloudiness": r.cloudiness,
                "visibility": r.visibility, "source": r.source,
                "real_date": r.real_date,
            }
            for r in rows
        ]

    @app.get("/locations")
    def list_locations(user: User = Depends(current_user), session: Session = Depends(db)):
        """M9 (§78): map — list of world locations for the client."""
        from app.db.models import Location

        locs = (
            session.query(Location)
            .filter_by(world_id=state["settings"].world.world_id)
            .order_by(Location.id)
            .all()
        )
        return [
            {"id": loc.id, "type": loc.type, "name": loc.name}
            for loc in locs
        ]

    @app.get("/characters/by-user/{user_id}")
    def characters_by_user(user_id: int, session: Session = Depends(db)):
        """M9: characters of the authenticated user (client bootstrap)."""
        from app.db.models import Character

        rows = (
            session.query(Character)
            .filter_by(
                world_id=state["settings"].world.world_id, user_id=user_id
            )
            .order_by(Character.id)
            .all()
        )
        return [
            {"id": c.id, "name": f"{c.first_name} {c.last_name}",
             "alive": bool(c.alive), "control_mode": c.control_mode,
             "looks": c.looks, "biography": c.biography}
            for c in rows
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
        from app.db.models import Account, CharacterJob, CharacterNeeds, Job

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
            acc = (
                session.query(Account)
                .filter_by(owner_type="character", owner_id=cid)
                .first()
            )
            out["balance"] = acc.balance if acc else 0
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
        import json as _json

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

        out = []
        for o in items:
            meta = {}
            try:
                meta = _json.loads(o.object_metadata)
                if not isinstance(meta, dict):
                    meta = {}
            except (ValueError, TypeError):
                meta = {}

            out.append({
                "id": o.id, "object_type": o.object_type,
                "quantity": o.quantity, "location_id": o.location_id,
                "worn": meta.get("worn") is True,
                "slot": meta.get("slot") if isinstance(meta.get("slot"), str) else None,
            })
        return out


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

    # ---------- Admin (M8, §82-86) ----------

    def require_role(minimum: str):
        allowed = {
            "moderator": {"moderator", "admin", "developer"},
            "admin": {"admin", "developer"},
        }[minimum]

        def guard(user: User = Depends(current_user)) -> User:
            if user.role not in allowed:
                raise HTTPException(status_code=403, detail="insufficient role")
            return user

        return guard

    def audit(
        session: Session, admin_user: User, action: str,
        target_type: str, target_id, payload=None,
    ):
        from datetime import datetime as _dt

        from app.db.models import AdminAuditLog

        entry = AdminAuditLog(
            world_id=state["settings"].world.world_id,
            admin_user_id=admin_user.id,
            action=action,
            target_type=target_type,
            target_id=str(target_id) if target_id is not None else None,
            payload=payload or {},
            wall_created_at=_dt.utcnow().isoformat(),
        )
        session.add(entry)
        session.flush()

    @app.get("/admin/overview")
    def admin_overview(
        user: User = Depends(require_role("moderator")),
        session: Session = Depends(db),
    ):
        import time as _time

        from app.db.models import (
            Character,
            CharacterTask,
            WorldClock,
            WorldEvent,
        )

        world_id = state["settings"].world.world_id
        started = getattr(app.state, "started_at", None)
        users_count = session.query(User).count()
        players = session.query(Character).filter(Character.user_id.isnot(None)).count()
        npcs = session.query(Character).filter(Character.user_id.is_(None)).count()
        active_tasks = (
            session.query(CharacterTask)
            .filter(CharacterTask.status.in_(["planned", "active"]))
            .count()
        )
        hour_ago = max(0, _world_now(session) - 60)
        events_last_hour = (
            session.query(WorldEvent)
            .filter(
                WorldEvent.world_id == world_id,
                WorldEvent.id > 0,
                WorldEvent.game_timestamp >= hour_ago,
            )
            .count()
        )
        clock = session.get(WorldClock, world_id)
        return {
            "users": users_count,
            "players": players,
            "npcs": npcs,
            "active_tasks": active_tasks,
            "events_last_hour": events_last_hour,
            "world_clock": {
                "game_timestamp": clock.game_timestamp if clock else 0,
                "day": (clock.game_timestamp // 1440) if clock else 0,
                "is_paused": bool(clock.is_paused) if clock else False,
                "time_scale": clock.time_scale if clock else 1.0,
            },
            "schema_version": _schema_version(session),
            "uptime_sec": int(_time.time() - started) if started else 0,
            "ws_connections": getattr(app.state, "ws_connections", 0),
        }

    class TimescaleIn(BaseModel):
        time_scale: float

    class TeleportIn(BaseModel):
        location_id: int

    @app.post("/admin/world/pause")
    def admin_pause(
        user: User = Depends(require_role("admin")), session: Session = Depends(db)
    ):
        from app.db.models import WorldClock

        clock = session.get(WorldClock, state["settings"].world.world_id)
        if clock is None:
            raise HTTPException(status_code=404, detail="world clock not found")
        previous = bool(clock.is_paused)
        clock.is_paused = True
        audit(session, user, "pause_world", "world",
              state["settings"].world.world_id, {"previous": previous})
        session.commit()
        return {"is_paused": True}

    @app.post("/admin/world/resume")
    def admin_resume(
        user: User = Depends(require_role("admin")), session: Session = Depends(db)
    ):
        from app.db.models import WorldClock

        clock = session.get(WorldClock, state["settings"].world.world_id)
        if clock is None:
            raise HTTPException(status_code=404, detail="world clock not found")
        clock.is_paused = False
        audit(session, user, "resume_world", "world",
              state["settings"].world.world_id, {})
        session.commit()
        return {"is_paused": False}

    @app.post("/admin/world/timescale")
    def admin_timescale(
        body: TimescaleIn,
        user: User = Depends(require_role("admin")),
        session: Session = Depends(db),
    ):
        from app.db.models import WorldClock

        if body.time_scale <= 0:
            raise HTTPException(status_code=422, detail="time_scale must be > 0")
        clock = session.get(WorldClock, state["settings"].world.world_id)
        if clock is None:
            raise HTTPException(status_code=404, detail="world clock not found")
        previous = clock.time_scale
        clock.time_scale = body.time_scale
        audit(session, user, "set_timescale", "world",
              state["settings"].world.world_id,
              {"previous": previous, "new": body.time_scale})
        session.commit()
        return {"time_scale": body.time_scale}

    @app.post("/admin/characters/{cid}/teleport")
    def admin_teleport(
        cid: str,
        body: TeleportIn,
        user: User = Depends(require_role("admin")),
        session: Session = Depends(db),
    ):
        from app.db.models import Location

        character = (
            session.query(Character)
            .filter_by(world_id=state["settings"].world.world_id, id=cid)
            .first()
        )
        if character is None:
            raise HTTPException(status_code=404, detail="character not found")
        if not character.alive:
            raise HTTPException(status_code=422, detail="character is not alive")
        location = session.get(Location, body.location_id)
        if location is None or location.world_id != state["settings"].world.world_id:
            raise HTTPException(status_code=422, detail="unknown location")
        previous = character.location_id
        character.location_id = body.location_id
        audit(session, user, "teleport_character", "character", cid,
              {"previous": previous, "new": body.location_id})
        session.commit()
        return {"character_id": cid, "location_id": body.location_id}

    @app.post("/admin/tasks/{task_id}/cancel")
    def admin_cancel_task(
        task_id: str,
        user: User = Depends(require_role("admin")),
        session: Session = Depends(db),
    ):
        from app.db.models import CharacterTask

        task = session.get(CharacterTask, int(task_id))
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        if task.status in ("completed", "failed", "cancelled"):
            raise HTTPException(status_code=409, detail="task already terminal")
        now_ts = _world_now(session)
        task.status = "cancelled"
        if task.ends_at is None or task.ends_at > now_ts:
            task.ends_at = now_ts  # stop-the-clock semantics (§61)
        audit(session, user, "cancel_task", "task", task_id,
              {"character_id": task.character_id, "previous_status": task.status})
        session.commit()
        return {"task_id": task.id, "status": task.status}

    @app.post("/admin/users/{user_id}/disable")
    def admin_disable_user(
        user_id: int,
        user: User = Depends(require_role("admin")),
        session: Session = Depends(db),
    ):
        target = session.get(User, user_id)
        if target is None:
            raise HTTPException(status_code=404, detail="user not found")
        if target.id == user.id:
            raise HTTPException(status_code=422, detail="cannot disable yourself")
        target.disabled = True
        # Kill in-flight work of the banned account (§84: disable account)
        from app.db.models import Character, CharacterTask

        player_ids = [
            c.id for c in session.query(Character).filter_by(user_id=target.id).all()
        ]
        if player_ids:
            session.query(CharacterTask).filter(
                CharacterTask.character_id.in_(player_ids),
                CharacterTask.status.in_(["planned", "active"]),
            ).update({"status": "cancelled"}, synchronize_session=False)
        audit(session, user, "disable_account", "user", target.id,
              {"username": target.username, "cancelled_tasks": len(player_ids)})
        session.commit()
        return {"user_id": target.id, "disabled": True}

    @app.post("/admin/users/{user_id}/enable")
    def admin_enable_user(
        user_id: int,
        user: User = Depends(require_role("admin")),
        session: Session = Depends(db),
    ):
        target = session.get(User, user_id)
        if target is None:
            raise HTTPException(status_code=404, detail="user not found")
        target.disabled = False
        audit(session, user, "enable_account", "user", target.id, {})
        session.commit()
        return {"user_id": target.id, "disabled": False}

    @app.get("/admin/audit")
    def admin_audit(
        limit: int = 50,
        user: User = Depends(require_role("moderator")),
        session: Session = Depends(db),
    ):
        from app.db.models import AdminAuditLog

        limit = max(1, min(limit, 500))
        rows = (
            session.query(AdminAuditLog)
            .order_by(AdminAuditLog.id.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": r.id, "admin_user_id": r.admin_user_id, "action": r.action,
                "target_type": r.target_type, "target_id": r.target_id,
                "payload": r.payload, "wall_created_at": r.wall_created_at,
            }
            for r in rows
        ]

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

    # M9 (§77): static web client — served last so API routes win
    from fastapi.responses import FileResponse
    @app.post("/admin/fire/ignite")
    def admin_fire_ignite(
        payload: dict = None, session: Session = Depends(db),
        user: User = Depends(require_role("moderator")),
    ):
        """011 (§72, R6): manual ignition (admin/moderator), audited §86."""
        from app.db.models import WorldClock, WorldObject
        from app.simulation.fire import ignite_object

        world_id = state["settings"].world.world_id
        object_id = (payload or {}).get("object_id")
        if object_id is None:
            raise HTTPException(status_code=422, detail="object_id required")
        obj = session.query(WorldObject).filter_by(
            id=object_id, world_id=world_id).first()
        if obj is None:
            raise HTTPException(status_code=404, detail="object not found")
        clock = session.get(WorldClock, world_id)
        day = clock.game_timestamp // 1440 if clock is not None else 0
        ignite_object(session, world_id, obj, day, state["settings"])
        audit(session, user, "fire_ignite", "world_object", obj.id,
              {"day": day})
        session.commit()
        return {"ok": True, "object_id": obj.id, "day": day}

    @app.get("/fire/active")
    def fire_active(session: Session = Depends(db), user: User = Depends(current_user)):
        """011 (§72, R7): currently burning objects."""
        from app.db.models import WorldClock, WorldEvent, WorldObject

        world_id = state["settings"].world.world_id
        clock = session.query(WorldClock).filter_by(world_id=world_id).first()
        day = clock.game_timestamp // 1440 if clock is not None else 0
        burning = (
            session.query(WorldObject)
            .filter_by(world_id=world_id, burn_state="burning")
            .all()
        )
        out = []
        for obj in burning:
            days = None
            import json as _json

            evs = (
                session.query(WorldEvent)
                .filter_by(world_id=world_id, event_type="OBJECT_BURNING")
                .all()
            )
            for e in evs:
                p = _json.loads(e.payload) if e.payload else {}
                if p.get("object_id") == obj.id:
                    days = day - p.get("day", day)
                    break
            out.append({
                "object_id": obj.id, "location_id": obj.location_id,
                "type": obj.object_type, "days_burning": days,
            })
        return out

    @app.post("/market/offers")
    def market_create_offer(
        payload: dict = None, session: Session = Depends(db),
        user: User = Depends(current_user),
    ):
        """012 (§74, R6): list an owned item for sale."""
        from app.market.marketplace import MarketError, list_object

        data = payload or {}
        char = session.query(Character).filter_by(
            user_id=user.id,
            world_id=state["settings"].world.world_id,
        ).first()
        if char is None:
            raise HTTPException(status_code=409, detail="no character")
        try:
            offer = list_object(
                session, state["settings"].world.world_id,
                _world_now(session), char,
                int(data.get("object_id", 0)), int(data.get("price", 0)),
            )
        except MarketError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.code)
        session.commit()
        return {"offer_id": offer.id, "status": offer.status}

    @app.get("/market/offers")
    def market_list_offers(
        status: str = "active", session: Session = Depends(db),
        user: User = Depends(current_user),
    ):
        """012 (R6): browse offers."""
        from app.db.models import MarketOffer

        if status not in ("active", "sold", "cancelled"):
            raise HTTPException(status_code=422, detail="bad status")
        rows = (
            session.query(MarketOffer)
            .filter_by(world_id=state["settings"].world.world_id,
                       status=status)
            .order_by(MarketOffer.id.desc())
            .limit(50)
            .all()
        )
        return [
            {"id": o.id, "object_id": o.object_id, "price": o.price,
             "seller": o.seller_character_id, "status": o.status}
            for o in rows
        ]

    @app.post("/market/offers/{offer_id}/buy")
    def market_buy(
        offer_id: int, session: Session = Depends(db),
        user: User = Depends(current_user),
    ):
        """012 (§74, R2-R3): buy — ledger + ownership, atomic."""
        from app.market.marketplace import MarketError, buy_offer

        char = session.query(Character).filter_by(
            user_id=user.id,
            world_id=state["settings"].world.world_id,
        ).first()
        if char is None:
            raise HTTPException(status_code=409, detail="no character")
        try:
            offer = buy_offer(
                session, state["settings"].world.world_id,
                _world_now(session), char, offer_id,
            )
        except MarketError as exc:
            session.rollback()
            raise HTTPException(status_code=exc.status, detail=exc.code)
        session.commit()
        return {"offer_id": offer.id, "status": offer.status}

    @app.post("/market/offers/{offer_id}/cancel")
    def market_cancel(
        offer_id: int, session: Session = Depends(db),
        user: User = Depends(current_user),
    ):
        """012 (AE3): cancel — seller only."""
        from app.market.marketplace import MarketError, cancel_offer

        char = session.query(Character).filter_by(
            user_id=user.id,
            world_id=state["settings"].world.world_id,
        ).first()
        if char is None:
            raise HTTPException(status_code=409, detail="no character")
        try:
            cancel_offer(
                session, state["settings"].world.world_id,
                _world_now(session), char, offer_id,
            )
        except MarketError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.code)
        session.commit()
        return {"ok": True}

    @app.post("/build")
    def build_construct(
        payload: dict = None, session: Session = Depends(db),
        user: User = Depends(current_user),
    ):
        """012 (§75, R4): enqueue a CONSTRUCT task for the character."""
        from app.db.models import CharacterTask

        data = payload or {}
        char = session.query(Character).filter_by(
            user_id=user.id,
            world_id=state["settings"].world.world_id,
        ).first()
        if char is None:
            raise HTTPException(status_code=409, detail="no character")
        object_type = data.get("object_type", "")
        if object_type not in state["settings"].construction.costs:
            raise HTTPException(status_code=422, detail="unknown blueprint")
        costs = state["settings"].construction.costs[object_type]
        days = int(costs.get("days", 2))
        now = _world_now(session)
        import json as _json

        task = CharacterTask(
            character_id=char.id, priority=5, task_type="CONSTRUCT",
            target_id=None, status="planned", source="player",
            parameters=_json.dumps({
                "object_type": object_type,
                "required_items": costs.get("required_items", {}),
            }),
            created_at=now, started_at=None,
            ends_at=now + days * 1440,
        )
        session.add(task)
        session.commit()
        return {"task_id": task.id, "object_type": object_type,
                "ends_at": task.ends_at}

    @app.post("/interactions/romantic")
    def romantic_request_route(
        payload: dict = None, session: Session = Depends(db),
        user: User = Depends(current_user),
    ):
        """018 (§31): explicit romance request; deterministic NPC decision."""
        from app.social.consent import ConsentError, request_romantic

        data = payload or {}
        char = session.query(Character).filter_by(
            user_id=user.id,
            world_id=state["settings"].world.world_id,
        ).first()
        if char is None:
            raise HTTPException(status_code=409, detail="no character")
        npc = session.query(Character).filter_by(
            id=str(data.get("npc_id", "")),
            world_id=state["settings"].world.world_id,
        ).first()
        if npc is None:
            raise HTTPException(status_code=404, detail="npc_not_found")
        try:
            permission = request_romantic(
                session, state["settings"].world.world_id,
                _world_now(session), char, npc, state["settings"],
            )
        except ConsentError as exc:
            session.rollback()
            raise HTTPException(status_code=exc.status, detail=exc.code)
        session.commit()
        return {"npc_id": npc.id, "permission": permission}

    @app.get("/interactions/permissions")
    def permissions_route(
        session: Session = Depends(db),
        user: User = Depends(current_user),
    ):
        """018 (R3): consent rows where the player is actor or target."""
        from app.social.consent import list_permissions

        char = session.query(Character).filter_by(
            user_id=user.id,
            world_id=state["settings"].world.world_id,
        ).first()
        if char is None:
            raise HTTPException(status_code=409, detail="no character")
        rows = list_permissions(
            session, state["settings"].world.world_id, char.id)
        return {"permissions": rows}

    @app.post("/wear")
    def wear_route(
        payload: dict = None, session: Session = Depends(db),
        user: User = Depends(current_user),
    ):
        """018 (§28): toggle worn on own wearable item."""
        from app.social.clothing import WearError, toggle_wear

        data = payload or {}
        char = session.query(Character).filter_by(
            user_id=user.id,
            world_id=state["settings"].world.world_id,
        ).first()
        if char is None:
            raise HTTPException(status_code=409, detail="no character")
        try:
            object_id = int(data.get("object_id", 0))
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="bad_object_id")
        try:
            result = toggle_wear(
                session, state["settings"].world.world_id, char.id,
                object_id,
            )
        except WearError as exc:
            session.rollback()
            raise HTTPException(status_code=exc.status, detail=exc.code)
        session.commit()
        return result

    @app.post("/messages")
    def send_message_route(
        payload: dict = None, session: Session = Depends(db),
        user: User = Depends(current_user),
    ):
        """013 (R5): letter to a contact; NPC auto-replies deterministically."""
        from app.social.messages import MessageError, send_message

        data = payload or {}
        char = session.query(Character).filter_by(
            user_id=user.id,
            world_id=state["settings"].world.world_id,
        ).first()
        if char is None:
            raise HTTPException(status_code=409, detail="no character")
        try:
            msg = send_message(
                session, state["settings"].world.world_id,
                _world_now(session), char,
                str(data.get("to_character_id", "")),
                str(data.get("body", "")),
            )
        except MessageError as exc:
            session.rollback()
            raise HTTPException(status_code=exc.status, detail=exc.code)
        session.commit()
        return {"message_id": msg.id, "status": "sent"}

    @app.get("/messages")
    def inbox_route(
        mark_read: bool = False, session: Session = Depends(db),
        user: User = Depends(current_user),
    ):
        """013 (R5): incoming letters."""
        from app.social.messages import inbox as _inbox
        from app.social.messages import mark_read as _mr

        char = session.query(Character).filter_by(
            user_id=user.id,
            world_id=state["settings"].world.world_id,
        ).first()
        if char is None:
            raise HTTPException(status_code=409, detail="no character")
        rows = _inbox(session, state["settings"].world.world_id, char)
        read_count = 0
        if mark_read:
            read_count = _mr(session, state["settings"].world.world_id, char)
            session.commit()
        return {"unread_marked": read_count, "messages": [
            {"id": m.id, "from": m.from_character_id, "body": m.body,
             "created_at": m.created_at, "read_at": m.read_at}
            for m in rows
        ]}

    @app.get("/messages/sent")
    def sent_route(
        session: Session = Depends(db), user: User = Depends(current_user),
    ):
        """013 (R5): outgoing letters."""
        from app.social.messages import sent as _sent

        char = session.query(Character).filter_by(
            user_id=user.id,
            world_id=state["settings"].world.world_id,
        ).first()
        if char is None:
            raise HTTPException(status_code=409, detail="no character")
        rows = _sent(session, state["settings"].world.world_id, char)
        return [
            {"id": m.id, "to": m.to_character_id, "body": m.body,
             "created_at": m.created_at}
            for m in rows
        ]

    from fastapi.staticfiles import StaticFiles

    web_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web")
    if os.path.isdir(web_dir):
        app.mount(
            "/static", StaticFiles(directory=web_dir), name="static"
        )

        @app.get("/", include_in_schema=False)
        def web_index():
            return FileResponse(os.path.join(web_dir, "index.html"))

    return app
