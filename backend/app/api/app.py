"""M5 (SPEC §80-81/104): FastAPI application factory.

The API layer is additive: headless simulation never imports this module
(lazy imports inside routes). П2: with api.enabled=false nothing here runs.
"""

import asyncio
import contextlib
import logging
import os
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

_DEV_SECRET_FALLBACK = "dev-insecure-secret-change-me"


def get_secret(settings) -> str:
    """HMAC secret from env; dev fallback only when api.require_secret=false.

    #112 (§security): lookup uses ONLY the configured api.secret_env name —
    the old secondary `or os.getenv("VL1_SECRET")` let the canonical var
    silently override a custom secret_env and masked a missing secret. When
    api.require_secret is true and the env var is unset, raise instead of
    failing open to the dev literal.
    """

    env_name = getattr(getattr(settings, "api", None), "secret_env", "VL1_SECRET")
    secret = os.getenv(env_name)
    if secret:
        return secret
    if getattr(getattr(settings, "api", None), "require_secret", False):
        raise RuntimeError(
            f"api.secret_env '{env_name}' not set; refusing to sign sessions "
            "with a dev secret (§security)"
        )
    return _DEV_SECRET_FALLBACK  # dev fallback (R2/NFR-security; dev/test only)



def _schema_version(session):
    """Current schema version from SchemaMeta (010: dynamic, was hardcoded)."""
    from app.db.models import SchemaMeta

    row = session.query(SchemaMeta).filter_by(key="version").first()
    return row.value if row is not None else "unknown"


def _masked_validation_handler(request, exc):
    """(#110) Mask sensitive request values echoed in 422 validation errors.

    FastAPI's default handler jsonifies exc.errors() including each error's
    `input` (the submitted value). Rebuild the error list keeping the
    type/loc/msg/url/ctx diagnostics, but drop `input` when any string loc
    component names a sensitive field (substring match, so compound names
    like password_confirm or api_token are masked too), or when the error
    type is json_invalid (its input echoes the raw body on some FastAPI
    versions). Non-sensitive errors keep everything.
    """
    from fastapi.encoders import jsonable_encoder
    from fastapi.responses import JSONResponse

    sanitized = []
    for err in exc.errors():
        loc_parts = [part for part in err.get("loc", ()) if isinstance(part, str)]
        sensitive = any(
            "password" in part or "token" in part or "secret" in part
            for part in loc_parts
        ) or err.get("type") == "json_invalid"
        if sensitive:
            err = {key: value for key, value in err.items() if key != "input"}
        sanitized.append(jsonable_encoder(err))
    return JSONResponse(status_code=422, content={"detail": sanitized})

def _setup_app_logging():
    """#155: route application logs to stderr so uvicorn entrypoints work.

    `uvicorn app.run:app` and `vl1 serve` configure no application logging:
    the root logger ran on lastResort (WARNING+), so INFO lines from
    vl1.ticker ("live tick driver started", lock-contention warning) never
    reached stdout/stderr and therefore journalctl. The `vl1` logger (covers
    vl1.ticker and future vl1.*) gets a stderr StreamHandler (12-factor) with
    propagate=False — deterministic routing, root/lastResort never
    duplicates. uvicorn.* loggers are NOT touched: uvicorn configures its
    own access logs with propagate=False, and inflating the journal is an
    explicit non-goal of #155. Idempotent across repeated create_app()
    calls (tests build many apps): the handler carries a private _vl1
    marker and is only added once.
    """
    logger = logging.getLogger("vl1")
    if not any(getattr(h, "_vl1", False) for h in logger.handlers):
        handler = logging.StreamHandler()  # stderr (12-factor)
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
        handler._vl1 = True  # private idempotency marker
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


def create_app(settings, session_factory: sessionmaker):
    """Build the FastAPI app bound to a sessionmaker and the given Settings."""
    # #155: both uvicorn entrypoints build the app through this factory
    # (`vl1 serve` via cli.py, `uvicorn app.run:app` via run._build), so the
    # logging setup lives here — one seam covers both entrypoints.
    _setup_app_logging()
    from fastapi import Depends, FastAPI, HTTPException, Request, Response
    from fastapi.exceptions import RequestValidationError
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

    # #113: Swagger/redoc/openapi.json only when api.docs=true (fail-closed;
    # default.yaml opts in for dev, production.yaml pins docs: false, and the
    # deploy/Caddyfile edge block 404s the paths anyway — defense in depth).
    docs = settings.api.docs

    # #151 [alpha-v1][engine]: live tick driver. Started in the lifespan so
    # every ASGI runtime gets it (`vl1 serve` and `uvicorn app.run:app`
    # alike). TestClient used WITHOUT a context manager never triggers the
    # lifespan, so existing suites keep the frozen-clock behavior; m152 uses
    # the context manager to exercise the driver. ONE writer per world: the
    # live driver replaces an external cron-driven `vl1 simulate`.
    @contextlib.asynccontextmanager
    async def _lifespan(_app):
        # B2-review F3: alpha schema self-heal — a pre-existing dev DB created
        # before new alpha tables (e.g. character_desires) must not 500 on the
        # desire endpoints until the next bootstrap. create_all is idempotent
        # and additive-only (no destructive migration by design).
        from app.db.models import Base
        Base.metadata.create_all(bind=session_factory.kw["bind"])
        tick_task = None
        if settings.world.live_tick_enabled:
            from app.api.ticker import start_tick_driver

            # Review F1 (#151): start_tick_driver returns None when another
            # process holds the single-writer tick lock — this process then
            # simply doesn't tick (multi-worker/multi-server safe).
            tick_task = start_tick_driver(settings, session_factory)
        try:
            yield
        finally:
            if tick_task is not None:
                tick_task.cancel()
                # Graceful cancel: wait for the loop to unwind; a driver
                # that already died with another error must not fail
                # shutdown.
                await asyncio.gather(tick_task, return_exceptions=True)

    app = FastAPI(
        title="VL1 LifeSim API",
        version="0.1.0",
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
        lifespan=_lifespan,
    )
    app.add_exception_handler(RequestValidationError, _masked_validation_handler)
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

    class DeleteAccountIn(BaseModel):
        # #138: self-service deletion confirm — must equal the caller's username.
        confirm: str

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
        """Clear the session cookie (#138).

        Sessions are stateless HMAC tokens (app.api.auth sign_token/
        verify_token): there is no session table and therefore no server-side
        state to revoke. Logout is exactly this cookie clear. An outstanding
        token remains valid until its TTL expires, but it only authenticates
        a live user — a deleted or disabled account is rejected by
        current_user regardless of the token.
        """
        response.delete_cookie(settings.api.cookie_name)
        return {"ok": True}

    @app.get("/auth/me", response_model=MeOut)
    def me(user: User = Depends(current_user)):
        # #124: no session-equivalent JWT is minted for the client anymore —
        # WS auth rides the httpOnly cookie on the upgrade request.
        return MeOut(
            id=user.id, username=user.username, email=user.email,
            role=user.role,
        )

    def _delete_user_cascade(session: Session, user: User) -> list[str]:
        """Explicit cascade delete of the user and every FK-bound row (#138).

        FK-order rationale: create_engine_factory sets PRAGMA
        foreign_keys=ON on every connection, so SQLite enforces each FK
        immediately — there is no deferred/ON DELETE CASCADE behavior, and a
        single unhandled child row turns the delete into a 500. Deletes are
        therefore ordered children-first along the real FK graph:

        - Nullable owner links are NULLED, not deleted: world_objects.owner
          and organizations.leader survive the player (objects/orgs outlive
          them like abandoned property). MarketOffer.buyer is likewise
          nulled; offers where the deleted player was the SELLER are removed
          (NOT NULL FK).
        - The financial ledger (accounts.owner_id) and world_events.actor_id
          are plain strings, NOT FKs — those rows are preserved deliberately
          (audit/history outlives the account).
        - AdminAuditLog rows authored by the deleted user are DELETED
          (admin_user_id is a NOT NULL FK to users); moderation rows merely
          targeting them remain.
        - Interleaved chains order two more steps: character_goals reference
          character_tasks (goals before tasks), dialogue_messages reference
          dialogue_sessions (messages before sessions), and dialogue_turns
          reference ai_requests (turns before requests).

        Returns the storage_path values of the VisualAsset rows orphaned by
        the delete, so the caller can unlink the files only AFTER a
        successful commit (a file surviving a rolled-back delete is better
        than a file deleted before the DB settled).
        """
        from sqlalchemy import or_

        from app.db.models import (
            AdminAuditLog,
            AiRequest,
            CharacterGoal,
            CharacterHealth,
            CharacterJob,
            CharacterNeeds,
            CharacterProfile,
            CharacterTask,
            CharacterTrait,
            Crime,
            DialogueMessage,
            DialogueSession,
            DialogueTurn,
            ExternalContact,
            InteractionPermission,
            MarketOffer,
            Memory,
            Message,
            Organization,
            OrganizationMember,
            OrgLaw,
            OrgLawViolation,
            Relationship,
            RelationshipEvent,
            VisualAsset,
            WorldObject,
        )

        cids = [c.id for c in session.query(Character).filter_by(user_id=user.id).all()]
        orphaned_assets: list[str] = []

        # 0) user-scoped NOT NULL FK children — must run even when the user
        # owns no characters (an admin/moderator who audited actions has
        # admin_audit_log rows; review ses_ee064 Finding 1).
        session.query(AdminAuditLog).filter(
            AdminAuditLog.admin_user_id == user.id
        ).delete(synchronize_session=False)
        # dialogue_sessions.user_id is a NOT NULL FK to users — scope sessions
        # to the user (plus their characters) unconditionally, not just when
        # cids is non-empty.
        dlg_ids = [
            row.id
            for row in session.query(DialogueSession.id)
            .filter(or_(
                DialogueSession.user_id == user.id,
                DialogueSession.character_id.in_(cids),
                DialogueSession.npc_id.in_(cids),
            ))
            .all()
        ]
        if dlg_ids:
            session.query(DialogueMessage).filter(
                DialogueMessage.session_id.in_(dlg_ids)
            ).delete(synchronize_session=False)
            session.query(DialogueSession).filter(
                DialogueSession.id.in_(dlg_ids)
            ).delete(synchronize_session=False)

        if cids:
            # 1) nullable owner links: null, don't delete (abandoned property)
            session.query(WorldObject).filter(
                WorldObject.owner_character_id.in_(cids)
            ).update({"owner_character_id": None}, synchronize_session=False)
            session.query(Organization).filter(
                Organization.leader_character_id.in_(cids)
            ).update({"leader_character_id": None}, synchronize_session=False)
            session.query(MarketOffer).filter(
                MarketOffer.buyer_character_id.in_(cids)
            ).update({"buyer_character_id": None}, synchronize_session=False)

            # 2) NOT NULL FK children, dependency-ordered
            session.query(CharacterGoal).filter(
                CharacterGoal.character_id.in_(cids)  # FK -> character_tasks
            ).delete(synchronize_session=False)
            session.query(CharacterTask).filter(
                CharacterTask.character_id.in_(cids)
            ).delete(synchronize_session=False)
            session.query(DialogueTurn).filter(
                DialogueTurn.character_id.in_(cids)  # FK -> ai_requests
            ).delete(synchronize_session=False)
            session.query(AiRequest).filter(
                AiRequest.character_id.in_(cids)
            ).delete(synchronize_session=False)
            session.query(Relationship).filter(or_(
                Relationship.character_a.in_(cids),
                Relationship.character_b.in_(cids),
            )).delete(synchronize_session=False)
            session.query(RelationshipEvent).filter(or_(
                RelationshipEvent.character_a.in_(cids),
                RelationshipEvent.character_b.in_(cids),
            )).delete(synchronize_session=False)
            session.query(OrgLaw).filter(
                OrgLaw.enacted_by_character_id.in_(cids)
            ).delete(synchronize_session=False)
            session.query(OrgLawViolation).filter(
                OrgLawViolation.character_id.in_(cids)
            ).delete(synchronize_session=False)
            session.query(Memory).filter(
                Memory.character_id.in_(cids)
            ).delete(synchronize_session=False)
            session.query(OrganizationMember).filter(
                OrganizationMember.character_id.in_(cids)
            ).delete(synchronize_session=False)
            session.query(CharacterJob).filter(
                CharacterJob.character_id.in_(cids)
            ).delete(synchronize_session=False)
            session.query(ExternalContact).filter(
                ExternalContact.character_id.in_(cids)
            ).delete(synchronize_session=False)
            session.query(Crime).filter(
                Crime.actor_character_id.in_(cids)
            ).delete(synchronize_session=False)
            session.query(Message).filter(or_(
                Message.from_character_id.in_(cids),
                Message.to_character_id.in_(cids),
            )).delete(synchronize_session=False)
            session.query(InteractionPermission).filter(or_(
                InteractionPermission.actor_character_id.in_(cids),
                InteractionPermission.target_character_id.in_(cids),
            )).delete(synchronize_session=False)
            session.query(MarketOffer).filter(
                MarketOffer.seller_character_id.in_(cids)
            ).delete(synchronize_session=False)

            # 3) visual assets: collect orphaned file paths BEFORE the delete
            orphaned_assets = [
                row.storage_path
                for row in session.query(VisualAsset)
                .filter(VisualAsset.character_id.in_(cids))
                .all()
            ]
            session.query(VisualAsset).filter(
                VisualAsset.character_id.in_(cids)
            ).delete(synchronize_session=False)

            # 4) 1:1 / attribute rows of the owned characters
            session.query(CharacterProfile).filter(
                CharacterProfile.character_id.in_(cids)
            ).delete(synchronize_session=False)
            session.query(CharacterNeeds).filter(
                CharacterNeeds.character_id.in_(cids)
            ).delete(synchronize_session=False)
            session.query(CharacterHealth).filter(
                CharacterHealth.character_id.in_(cids)
            ).delete(synchronize_session=False)
            session.query(CharacterTrait).filter(
                CharacterTrait.character_id.in_(cids)
            ).delete(synchronize_session=False)

            # 5) the characters themselves (audit rows for this user were
            # already removed unconditionally in step 0)
            session.query(Character).filter(
                Character.id.in_(cids)
            ).delete(synchronize_session=False)

        session.delete(user)
        return orphaned_assets

    @app.post("/auth/account/delete")
    def delete_account(
        body: DeleteAccountIn,
        response: Response,
        user: User = Depends(current_user),
        session: Session = Depends(db),
    ):
        """Self-service account deletion (#138): confirm == username."""
        if body.confirm != user.username:
            raise HTTPException(status_code=409, detail="confirm does not match username")
        orphaned_assets = _delete_user_cascade(session, user)
        session.commit()
        # Files go only after the DB settled: unlink best-effort, traversal
        # safety delegated to AssetStore._resolve (same guard as read()).
        from app.visual.store import AssetStore

        store = AssetStore(settings.visual)
        for relative in orphaned_assets:
            try:
                store.remove(relative)
            except (ValueError, OSError):
                pass  # best-effort; the row is gone either way
        response.delete_cookie(settings.api.cookie_name)
        return {"ok": True}

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
            .limit(20)  # #92: journal/history bounded to the last N=20
            .all()
        )

        def task_to_dict(t):
            import json as _json
            try:
                params = _json.loads(t.parameters) if t.parameters else {}
            except (ValueError, TypeError):
                params = {}
            return {
                "id": t.id, "task_type": t.task_type, "status": t.status,
                "target_id": t.target_id, "source": t.source,
                "created_at": t.created_at, "started_at": t.started_at,
                "ends_at": t.ends_at, "completed_at": t.completed_at,
                "parameters": params,
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

    @app.get("/characters/{cid}/roles")
    def get_character_roles(
        cid: str, user: User = Depends(current_user), session: Session = Depends(db)
    ):
        # #89 (A7): public roles read model — derive-on-read from
        # authoritative rows; any authenticated user may read (public facts),
        # unlike write paths which keep _owned_character.
        from app.social.roles import derive_roles

        character = (
            session.query(Character)
            .filter_by(world_id=state["settings"].world.world_id, id=cid)
            .first()
        )
        if character is None:
            raise HTTPException(status_code=404, detail="character not found")
        return {
            "character_id": cid,
            "roles": derive_roles(session, character.world_id, cid),
        }

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

    def _validated_enqueue(
        session: Session, character: Character, action_type: str,
        request_params: dict, now_ts: int,
    ):
        """Authoritative player-action path shared by POST /actions and
        POST /scene/act (#87 A5): the SAME validate() → enqueue_task()
        sequence, so the two endpoints cannot diverge. validate() is
        read-only (world queries + find_path); enqueue_task() is the only
        write here. Returns (task, reject_reason): reject_reason set →
        validate() refused (no mutation happened); task None without a
        reason → the character already has an active task. Caller commits."""
        from app.actions.lifecycle import enqueue_task
        from app.actions.validators import validate

        params = request_params or {}
        ok, reason, _needs_move, vparams = validate(
            session, character.world_id, character, action_type, now_ts,
            state["settings"], params=params,
        )
        if not ok:
            return None, reason
        task = enqueue_task(
            session, character.world_id, character, action_type,
            "player", {**vparams, **params}, now_ts, state["settings"],
        )
        return task, None

    def _actor_events_since(
        session: Session, world_id: str, actor_id: str, after_id: int, limit: int = 10
    ):
        """#87 (A5): WorldEvents logged during THIS request (actor-scoped,
        strictly after the pre-call watermark), so the client can show an
        event line before the scene refresh. enqueue_task() itself logs
        nothing — completion events land later via the tick — so this is
        usually []; the field exists so the UI never guesses."""
        import json as _json

        from app.db.models import WorldEvent

        rows = (
            session.query(WorldEvent)
            .filter(
                WorldEvent.world_id == world_id,
                WorldEvent.actor_id == actor_id,
                WorldEvent.id > after_id,
            )
            .order_by(WorldEvent.id)
            .limit(limit)
            .all()
        )
        return [
            {
                "id": e.id, "event_type": e.event_type, "actor_id": e.actor_id,
                "location_id": e.location_id, "day": e.game_timestamp // 1440,
                "game_timestamp": e.game_timestamp,
                "payload": _json.loads(e.payload) if e.payload else {},
            }
            for e in rows
        ]

    @app.post("/actions", status_code=201)
    def post_action(
        body: ActionIn, user: User = Depends(current_user), session: Session = Depends(db)
    ):
        from app.actions.registry import ACTION_REGISTRY

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
        task, reject_reason = _validated_enqueue(
            session, character, body.action_type, body.params, now_ts)
        if reject_reason is not None:
            raise HTTPException(
                status_code=422, detail=f"action not possible: {reject_reason}")
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

    # ---------- #82 (A3): free-text destination → authoritative MOVE ----------

    class TravelPlanIn(BaseModel):
        text: str

    @app.post("/travel/plan", status_code=201)
    def travel_plan(
        body: TravelPlanIn,
        user: User = Depends(current_user),
        session: Session = Depends(db),
    ):
        """Ground free text against canonical locations and create the SAME
        authoritative MOVE as a direct map click (reuse of the fixed
        validate() MOVE path — the two endpoints cannot diverge).

        Deterministic only (LLM disabled in this slice). 422 payloads:
        {"detail": "unknown destination"} | {"detail": "impossible route"} |
        {"detail": "ambiguous destination", "options": [names]} — no state
        mutation on any 422.
        """
        from fastapi.responses import JSONResponse

        from app.actions.lifecycle import enqueue_task
        from app.actions.validators import validate
        from app.api.intent import ground_destination

        text = (body.text or "").strip()
        if not text or len(text) > 2000:
            return JSONResponse(
                status_code=422, content={"detail": "unknown destination"})
        character = _owned_character_by_user(session, user)
        if character.control_mode == "AUTONOMOUS":
            raise HTTPException(
                status_code=409,
                detail="character is AUTONOMOUS; switch to GUIDED or DIRECT first (§61)",
            )
        resolved = ground_destination(
            session, character.world_id, state["settings"], text)
        if isinstance(resolved, dict):
            # {"detail": ...} | {"detail": ..., "options": [...]} — no mutation.
            return JSONResponse(status_code=422, content=resolved)

        now_ts = _world_now(session)
        ok, reason, _needs_move, vparams = validate(
            session, character.world_id, character, "MOVE", now_ts,
            state["settings"], params={"location_id": resolved.id},
        )
        if not ok:
            return JSONResponse(status_code=422, content={"detail": reason})

        task = enqueue_task(
            session, character.world_id, character, "MOVE", "player",
            {**vparams}, now_ts, state["settings"],
        )
        if task is None:
            raise HTTPException(
                status_code=409, detail="character already has an active task")
        session.commit()
        return {
            "location_id": resolved.id, "name": resolved.name,
            "travel_minutes": vparams.get("total_minutes"),
            "task_id": task.id, "task_type": task.task_type,
            "status": task.status,
        }

    # ---------- #87 (A5): free-text scene action ----------

    class SceneActIn(BaseModel):
        text: str

    # scene intent → registry action_type (IDLE is NOT here — see below).
    _SCENE_TASK_TYPES = {
        "move": "MOVE", "socialize": "SOCIALIZE", "sleep": "SLEEP",
        "eat": "EAT", "drink": "DRINK", "buy": "BUY_ITEM",
    }

    def _scene_act_wear(
        session: Session, wid: str, character: Character, kind: str,
        params: dict, watermark: int,
    ):
        """A5 wear/take-off: resolve the OWN wearable (the only writer is
        toggle_wear — the SAME path POST /wear uses) and toggle it. No task
        is created; the worn-flag flip is the whole mutation. The intent
        maps to the expected outcome: «надеть» picks an object that is not
        worn yet, «снять» a worn one (toggle_wear itself stays a toggle)."""
        import json as _json

        from fastapi.responses import JSONResponse

        from app.db.models import WorldObject
        from app.social.clothing import WearError, toggle_wear

        interpretation = {"intent": kind, "params": params}
        wtype = params.get("wearable")
        if not wtype:
            return JSONResponse(status_code=200, content={
                "ok": False, "reason": "not_possible",
                "detail": "Вещь не распознана (каталог: jacket/куртка, "
                          "boots/сапоги, hat/шляпа).",
                "interpretation": interpretation, "events": [],
            })
        own = (
            session.query(WorldObject)
            .filter(
                WorldObject.world_id == wid,
                WorldObject.owner_character_id == character.id,
                WorldObject.object_type == wtype,
            )
            .order_by(WorldObject.id)
            .all()
        )
        want_worn = kind == "wear"
        pick = None
        for obj in own:
            try:
                meta = _json.loads(obj.object_metadata or "{}")
            except (ValueError, TypeError):
                meta = {}
            if isinstance(meta, dict) and bool(meta.get("worn")) != want_worn:
                pick = obj
                break
        if pick is None:
            if own:
                detail = "Уже надето." if want_worn else "Вещь не надета."
            else:
                detail = f"Нет такой вещи: {wtype}."
            return JSONResponse(status_code=200, content={
                "ok": False, "reason": "not_possible", "detail": detail,
                "interpretation": interpretation, "events": [],
            })
        try:
            result = toggle_wear(session, wid, character.id, pick.id)
        except WearError as exc:
            session.rollback()
            return JSONResponse(status_code=200, content={
                "ok": False, "reason": "not_possible", "detail": exc.code,
                "interpretation": interpretation, "events": [],
            })
        session.commit()
        return JSONResponse(status_code=201, content={
            "ok": True, "action": kind, "object_id": pick.id,
            "worn": result["worn"], "slot": result["slot"],
            "interpretation": interpretation,
            "events": _actor_events_since(session, wid, character.id, watermark),
        })

    @app.post("/scene/act")
    def scene_act(
        body: SceneActIn, user: User = Depends(current_user), session: Session = Depends(db)
    ):
        """#87 (A5): free-text scene action → interpret → validate → commit.

        Deterministic only: llm.enabled=false in dev and there is NO LLM call
        on any path of this endpoint — the interpreter
        (app.api.scene_act.interpret_scene_action) is a PURE text→intent
        function without a Session parameter, so neither it nor any model
        can write the DB/world directly. Every mutation goes through the
        existing authoritative paths only: validate()+enqueue_task() (the
        POST /actions path, shared via _validated_enqueue) and toggle_wear()
        (the POST /wear path). Unsupported/ambiguous/validator-rejected
        intents return 200 {"ok": false, "reason", "detail"} with NO
        mutation (validate() is read-only).

        IDLE: validate() has no IDLE branch ("Unknown action type"); the
        engine itself enqueues IDLE directly (lifecycle.py path-not-found
        fallback), so this endpoint follows that existing precedent with
        source="player" instead of a doomed validate() call.

        Status codes: 201 task/wear committed; 200 look (pure read) or
        ok:false; 409 AUTONOMOUS (same as POST /actions); 401 unauthenticated.
        """
        from fastapi.responses import JSONResponse
        from sqlalchemy import func

        from app.actions.lifecycle import enqueue_task
        from app.api.intent import ground_destination
        from app.api.scene_act import (
            UNSUPPORTED_DETAIL,
            ground_social_target,
            interpret_scene_action,
        )
        from app.db.models import WorldEvent
        from app.visual.descriptor import build_scene_descriptor

        text = (body.text or "").strip()
        intent = interpret_scene_action(text)
        if intent is None:
            # Interpreter could not classify: explanation + catalog, no
            # mutation (the character is not even resolved yet).
            return JSONResponse(status_code=200, content={
                "ok": False, "reason": "unsupported_action",
                "detail": UNSUPPORTED_DETAIL, "events": [],
            })

        character = _owned_character_by_user(session, user)
        if character.control_mode == "AUTONOMOUS":
            raise HTTPException(
                status_code=409,
                detail="character is AUTONOMOUS; switch to GUIDED or DIRECT first (§61)",
            )

        wid = character.world_id
        settings_ = state["settings"]
        now_ts = _world_now(session)
        # Pre-call event watermark: everything the response reports must be
        # a WorldEvent committed by THIS request (never fabricated).
        watermark_row = session.query(func.max(WorldEvent.id)).first()
        watermark = watermark_row[0] if watermark_row and watermark_row[0] else 0

        kind = intent["intent"]
        params = intent["params"]
        interpretation = {"intent": kind, "params": {}}

        if kind == "look":
            # Pure read of committed state (§67 descriptor) — no task, no
            # mutation of any kind (no commit, no event, no task row).
            descriptor = build_scene_descriptor(
                session, wid, character.location_id,
                player_character_id=character.id,
            )
            return {
                "ok": True, "action": "look", "result": "Вы осматриваетесь.",
                "scene": descriptor, "interpretation": interpretation,
                "events": [],
            }

        if kind in ("wear", "take_off"):
            return _scene_act_wear(session, wid, character, kind, params, watermark)

        request_params: dict = {}
        if kind == "move":
            resolved = ground_destination(
                session, wid, settings_, params.get("destination_text", ""))
            if isinstance(resolved, dict):
                if resolved.get("options"):
                    return JSONResponse(status_code=200, content={
                        "ok": False, "reason": "ambiguous",
                        "detail": "Уточните пункт назначения: "
                                  + ", ".join(resolved["options"]),
                        "options": resolved["options"], "events": [],
                    })
                return JSONResponse(status_code=200, content={
                    "ok": False, "reason": "not_possible",
                    "detail": resolved.get("detail", "unknown destination"),
                    "events": [],
                })
            request_params = {"location_id": resolved.id}
            interpretation["params"] = {
                "destination_id": resolved.id, "destination_name": resolved.name}

        elif kind == "socialize":
            # Resolve the mentioned target among the characters PRESENT at
            # this scene for the interpretation + ambiguity UX; the SOCIALIZE
            # validator stays authoritative for the actual target (it ignores
            # request params on purpose), so request_params stays {} and the
            # task carries the validator's own choice.
            present = (
                session.query(Character)
                .filter(
                    Character.world_id == wid,
                    Character.alive,
                    Character.id != character.id,
                    Character.location_id == character.location_id,
                )
                .order_by(Character.id)
                .all()
            )
            candidates = [
                (c.id, f"{c.first_name} {c.last_name}".strip()) for c in present
            ]
            status_t, _tid, name_t = ground_social_target(
                candidates, params.get("target_text", ""))
            options_t = list(name_t) if isinstance(name_t, list) else []
            if status_t == "ambiguous":
                return JSONResponse(status_code=200, content={
                    "ok": False, "reason": "ambiguous",
                    "detail": "Уточните, с кем говорить: " + ", ".join(options_t),
                    "options": options_t, "events": [],
                })
            if status_t == "none":
                detail = (
                    "Рядом нет такого персонажа. Кто здесь: " + ", ".join(options_t)
                    if options_t else "Рядом никого — некому говорить."
                )
                return JSONResponse(status_code=200, content={
                    "ok": False, "reason": "ambiguous", "detail": detail,
                    "options": options_t, "events": [],
                })
            interpretation["params"] = {"target_id": _tid, "target_name": name_t}

        elif kind == "buy":
            if not params.get("item"):
                return JSONResponse(status_code=200, content={
                    "ok": False, "reason": "not_possible",
                    "detail": "Предмет не распознан (каталог: еда, вода, "
                              "инструменты, лекарства, одежда, книга).",
                    "interpretation": interpretation, "events": [],
                })
            # The BUY_ITEM validator derives the stocked object itself; the
            # parsed key only documents the interpretation.
            interpretation["params"] = {"item": params["item"]}

        if kind == "idle":
            # validate() has no IDLE branch — see the docstring; direct
            # enqueue follows the engine's own IDLE fallback precedent.
            task = enqueue_task(
                session, wid, character, "IDLE", "player", {}, now_ts, settings_)
            reject_reason = None
        else:
            task, reject_reason = _validated_enqueue(
                session, character, _SCENE_TASK_TYPES[kind], request_params, now_ts)

        if reject_reason is not None:
            # validate() refused — read-only, so nothing was mutated.
            return JSONResponse(status_code=200, content={
                "ok": False, "reason": "not_possible", "detail": reject_reason,
                "interpretation": interpretation, "events": [],
            })
        if task is None:
            raise HTTPException(
                status_code=409, detail="character already has an active task")
        session.commit()
        return JSONResponse(status_code=201, content={
            "ok": True, "task_id": task.id, "task_type": task.task_type,
            "status": task.status, "interpretation": interpretation,
            "events": _actor_events_since(session, wid, character.id, watermark),
        })

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

    # ---------- Desire (#90 [A8]) — long-term layer, SEPARATE from goals ----

    class DesireIn(BaseModel):
        text: str

    def _desire_to_dict(d) -> dict:
        import json as _json

        try:
            interp = (_json.loads(d.interpretation)
                      if isinstance(d.interpretation, str) else d.interpretation)
        except (ValueError, TypeError):
            interp = {}
        return {
            "id": d.id, "source_text": d.source_text,
            "catalog_key": d.catalog_key, "status": d.status,
            "interpretation": interp or {},
            "created_at": d.created_at, "updated_at": d.updated_at,
            "replaced_by": d.replaced_by,
        }

    def _active_desire(session: Session, world_id: str, cid: str):
        from app.db.models import CharacterDesire

        return (
            session.query(CharacterDesire)
            .filter_by(world_id=world_id, character_id=cid, status="active")
            .order_by(CharacterDesire.id.desc())
            .first()
        )

    @app.post("/characters/{cid}/desire", status_code=201)
    def post_desire(
        cid: str, body: DesireIn,
        user: User = Depends(current_user), session: Session = Depends(db)
    ):
        """Create/replace the ONE active desire. Unsupported text → HTTP 200
        with an explicit clarification and NO row (scene_act ok:false
        pattern #87: the reason is data, not an error)."""
        from app.db.models import CharacterDesire
        from app.desire.desire import interpret_desire

        character = _owned_character(session, user, cid)
        text = (body.text or "").strip()
        if len(text) > 500:
            raise HTTPException(status_code=422, detail="text must be 1..500 chars")

        interpretation = interpret_desire(text)
        if interpretation.get("catalog_key") is None:
            return JSONResponse(status_code=200, content={
                "desire": None, "catalog_key": None,
                "clarification": interpretation.get("clarification", ""),
            })

        now_ts = _world_now(session)
        current = _active_desire(session, character.world_id, cid)
        row = CharacterDesire(
            world_id=character.world_id, character_id=cid,
            source_text=text, catalog_key=interpretation["catalog_key"],
            status="active", interpretation=interpretation,
            created_at=now_ts, updated_at=now_ts,
        )
        session.add(row)
        session.flush()
        if current is not None:
            current.status = "replaced"
            current.replaced_by = row.id
            current.updated_at = now_ts
        session.commit()
        return {"desire": _desire_to_dict(row), "interpretation": interpretation}

    @app.get("/characters/{cid}/desire")
    def get_desire(
        cid: str, user: User = Depends(current_user), session: Session = Depends(db)
    ):
        """Active desire or the explicit empty state {desire: null}."""
        character = _owned_character(session, user, cid)
        row = _active_desire(session, character.world_id, cid)
        return {"desire": _desire_to_dict(row) if row is not None else None}

    @app.delete("/characters/{cid}/desire")
    def delete_desire(
        cid: str, user: User = Depends(current_user), session: Session = Depends(db)
    ):
        """Abandon the active desire (kept as a terminal row; 404 if none)."""
        character = _owned_character(session, user, cid)
        row = _active_desire(session, character.world_id, cid)
        if row is None:
            raise HTTPException(status_code=404, detail="no active desire")
        row.status = "abandoned"
        row.updated_at = _world_now(session)
        session.commit()
        return {"desire": _desire_to_dict(row)}

    @app.get("/characters/{cid}/desire/opportunities")
    def get_desire_opportunities(
        cid: str, user: User = Depends(current_user), session: Session = Depends(db)
    ):
        """Bounded, read-only opportunities from the desire planner."""
        from app.desire.desire import build_opportunities

        character = _owned_character(session, user, cid)
        row = _active_desire(session, character.world_id, cid)
        opportunities = (
            build_opportunities(session, character.world_id, cid, row)
            if row is not None else []
        )
        return {
            "desire": _desire_to_dict(row) if row is not None else None,
            "opportunities": opportunities,
        }

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
            "time_scale": clock.time_scale if clock else 1.0,
            "is_paused": bool(clock.is_paused) if clock else False,
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
        from app.db.models import Character, Location

        locs = (
            session.query(Location)
            .filter_by(world_id=state["settings"].world.world_id)
            .order_by(Location.id)
            .all()
        )

        # A2: expose the minimum authoritative identity needed for map markers.
        # Coordinates remain location truth: occupants do not receive synthetic x/y.
        active_chars = (
            session.query(Character)
            .filter_by(world_id=state["settings"].world.world_id, alive=True)
            .order_by(Character.id)
            .all()
        )
        occupants_by_location: dict[int, list[dict[str, Any]]] = {}
        for character in active_chars:
            occupants_by_location.setdefault(character.location_id, []).append({
                "id": character.id,
                "name": f"{character.first_name} {character.last_name}".strip(),
                "kind": "player" if character.user_id is not None else "npc",
            })

        return [
            {
                "id": loc.id, "type": loc.type, "name": loc.name,
                "x": loc.x, "y": loc.y, "parent_id": loc.parent_id,
                "occupants_count": len(occupants_by_location.get(loc.id, [])),
                "occupants": occupants_by_location.get(loc.id, []),
            }
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
                "condition": o.condition,
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
            source = "fallback"
            player_name = user_char.first_name
            reply = fallback_reply(context, player_name)
        else:
            source = "llm"
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
            # #88 (A6): which path produced the reply — UI surfaces
            # «LLM недоступен, отвечает fallback» from this flag.
            "source": source,
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
