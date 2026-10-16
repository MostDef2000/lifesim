"""M6 (SPEC §105/66-70): visual API routes.

Registered from create_app; every route is gated by settings.visual.enabled (503).
Visual layer is read-only over world state (П1-аналог): writes only visual_assets + files.
#191 exceptions to the gate on GET /visual/characters/{cid}/portrait (both because the
#191 character guard precedes the gate on the canonical-miss path):
- an NPC (user_id is None) with visual.enabled=false answers 404 (no transport call);
- with visual.enabled=false, unknown/dead/non-owned cids answer 404 too (the guard
  runs before the gate), while an alive player character (own or another player's)
  keeps the 503 gate — matching the m6/m158 pins.
NOTE: no `from __future__ import annotations` — it breaks FastAPI resolution of the
locally-defined Pydantic models (same trap as app.py/ws.py in M5).
"""
import hashlib
import logging
import threading
import time
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.config.config import Settings

logger = logging.getLogger("vl1.visual")


def _deterministic_seed(*parts: Any) -> int:
    digest = hashlib.sha256(":".join(str(p) for p in parts).encode()).digest()
    return int.from_bytes(digest[:8], "big") % (2**31)


# #191 — lazy canonical NPC portrait state (in-process, mirrors the #189 desire
# cache discipline):
# - per-(world_id, cid) locks: two parallel GETs of the same NPC portrait must
#   produce exactly ONE transport call (single-flight + double-check inside);
# - negative cache: after a FAILED lazy attempt, (world_id, cid) ->
#   time.monotonic() is remembered for NPC_PORTRAIT_NEGATIVE_TTL_SEC, so
#   repeat GETs return the same honest 404 without retrying the transport.
#   A SUCCESSFUL generation pops the entry. Every removal uses dict.pop(key,
#   None) (never bare ``del``) so a racing thread cannot raise KeyError;
#   the cache is capped and evicted expired-then-oldest like the desire cache.
# failure backoff window; 300 matches visual.timeout_sec in PRODUCTION
# config/production.yaml (default.yaml ships 30).
NPC_PORTRAIT_NEGATIVE_TTL_SEC = 300.0
NPC_PORTRAIT_NEGATIVE_MAX = 512
_npc_portrait_locks: dict[tuple[str, str], threading.Lock] = {}
_npc_portrait_locks_guard = threading.Lock()
_npc_portrait_negative: dict[tuple[str, str], float] = {}


def clear_npc_portrait_negative_cache() -> None:
    """Drop negative-cache entries and per-cid locks (tests and admin use)."""
    with _npc_portrait_locks_guard:
        _npc_portrait_negative.clear()
        _npc_portrait_locks.clear()


def _npc_portrait_lock(key: tuple[str, str]) -> threading.Lock:
    """Per-(world_id, cid) single-flight lock (created under the meta-lock)."""
    with _npc_portrait_locks_guard:
        lock = _npc_portrait_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _npc_portrait_locks[key] = lock
        return lock


def _evict_npc_portrait_negative(now: float) -> None:
    """Enforce NPC_PORTRAIT_NEGATIVE_MAX: drop expired entries first, then oldest."""
    if len(_npc_portrait_negative) <= NPC_PORTRAIT_NEGATIVE_MAX:
        return
    expired = [key for key, ts in _npc_portrait_negative.items()
               if now - ts >= NPC_PORTRAIT_NEGATIVE_TTL_SEC]
    for key in expired:
        _npc_portrait_negative.pop(key, None)  # pop: benign if raced away
    while len(_npc_portrait_negative) > NPC_PORTRAIT_NEGATIVE_MAX:
        _npc_portrait_negative.pop(
            min(_npc_portrait_negative, key=lambda k: _npc_portrait_negative[k]),
            None)


def register_visual_routes(app, settings: Settings, session_factory: sessionmaker) -> None:
    from fastapi import Depends, HTTPException, Response
    from fastapi.responses import JSONResponse
    from pydantic import BaseModel

    current_user = app.state.current_user
    db = app.state.db

    visual_settings = settings.visual
    from app.visual.store import AssetStore
    from app.visual.transports import build_transport

    store = AssetStore(visual_settings)

    def gate() -> None:
        if not visual_settings.enabled:
            raise HTTPException(status_code=503, detail="visual generation disabled")

    def transport_factory():
        # Overridable seam for tests (app.state monkeypatch) and future DI.
        if hasattr(app.state, "flux_transport_factory"):
            return app.state.flux_transport_factory()
        return build_transport(visual_settings)

    def _generate_asset(
        session: Session,
        *,
        asset_type: str,
        descriptor: dict,
        character_id: str | None,
        location_id: int | None,
        object_id: int | None,
        actor_id: str | None,
        is_canonical: bool = False,
    ) -> tuple[dict, bytes]:
        """§66 pipeline: descriptor -> prompt -> Flux -> image -> asset registration.

        #191: is_canonical=True (lazy NPC GET path) creates the row canonical
        in the SAME transaction/commit as the file — no window where a
        canonical row exists without the file. Default False keeps POST flows
        byte-identical (they promote via POST /assets/{id}/canonical). The
        parameter is NOT named ``canonical``: the scene-reference loop below
        already binds that local name to a VisualAsset row.
        """
        from app.db.models import VisualAsset, WorldClock
        from app.events.events import EventType, log_event

        world_id = settings.world.world_id
        clock = session.get(WorldClock, world_id)
        game_ts = int(clock.game_timestamp) if clock else 0

        # §70: canonical references of the scene's characters
        reference_ids: list[int] = []
        if asset_type == "scene":
            for char in descriptor.get("characters", []):
                canonical = (
                    session.query(VisualAsset)
                    .filter_by(
                        world_id=world_id, asset_type="portrait",
                        character_id=char["id"], canonical=True,
                    )
                    .first()
                )
                if canonical is not None:
                    reference_ids.append(canonical.id)
        descriptor = {**descriptor, "references": reference_ids}

        existing = (
            session.query(VisualAsset)
            .filter_by(world_id=world_id, asset_type=asset_type)
            .count()
        )
        seed = _deterministic_seed(
            world_id, character_id or location_id, asset_type, existing
        )

        from app.visual.descriptor import _AUTO_OUTFIT_POOL, build_prompt

        # W205d: seed depends on existing-asset count, so each regeneration
        # rotates the auto outfit pool; scene prompts ignore the key.
        if asset_type == "portrait":
            descriptor["outfit_auto_variant"] = _AUTO_OUTFIT_POOL[
                seed % len(_AUTO_OUTFIT_POOL)
            ]

        prompt = build_prompt(descriptor)

        try:
            bytes_out = transport_factory().generate(
                prompt, seed, visual_settings.image_size
            )
        except ValueError as exc:
            raise HTTPException(status_code=502, detail=str(exc))

        asset = VisualAsset(
            world_id=world_id,
            asset_type=asset_type,
            character_id=character_id,
            location_id=location_id,
            object_id=object_id,
            scene_descriptor=descriptor,
            prompt=prompt,
            storage_path="",  # placeholder until we have the id
            seed=seed,
            model=visual_settings.model,
            lora=visual_settings.lora or None,
            canonical=is_canonical,
            created_at=f"ts{game_ts}",
        )
        session.add(asset)
        session.flush()
        asset.storage_path = store.save(asset.id, bytes_out)
        log_event(
            session, world_id, game_ts,
            EventType.VISUAL_ASSET_CREATED,
            actor_id=actor_id,
            payload={
                "asset_id": asset.id, "asset_type": asset_type,
                "canonical": asset.canonical, "storage_path": asset.storage_path,
            },
        )
        session.commit()
        return _asset_payload(asset), bytes_out

    def _asset_payload(asset) -> dict:
        return {
            "id": asset.id,
            "asset_type": asset.asset_type,
            "character_id": asset.character_id,
            "location_id": asset.location_id,
            "canonical": asset.canonical,
            "prompt": asset.prompt,
            "seed": asset.seed,
            "model": asset.model,
            "lora": asset.lora,
            "storage_path": asset.storage_path,
            "scene_descriptor": asset.scene_descriptor,
        }

    def _owned_character_for_portrait(session: Session, request_user, cid: str):
        from app.db.models import Character

        character = (
            session.query(Character)
            .filter_by(world_id=settings.world.world_id, id=cid)
            .first()
        )
        if character is None:
            raise HTTPException(status_code=404, detail="character not found")
        if not character.alive:
            raise HTTPException(status_code=422, detail="character is not alive")
        if character.user_id != request_user.id:
            raise HTTPException(status_code=403, detail="not your character")
        return character

    class CanonicalIn(BaseModel):
        canonical: bool

    class SceneIn(BaseModel):
        location_id: int
        event_id: int | None = None

    @app.post("/visual/portraits/{cid}")
    def create_portrait(
        cid: str,
        request_user=Depends(current_user),
        session: Session = Depends(db),
    ):
        gate()
        from app.db.models import VisualAsset
        from app.visual.descriptor import build_portrait_descriptor

        # ownership + aliveness guard (404/422/403)
        _owned_character_for_portrait(session, request_user, cid)
        # §70 reuse: existing canonical portrait -> return it, no generation
        canonical = (
            session.query(VisualAsset)
            .filter_by(
                world_id=settings.world.world_id, asset_type="portrait",
                character_id=cid, canonical=True,
            )
            .first()
        )
        if canonical is not None:
            return JSONResponse(status_code=200, content={
                "asset": _asset_payload(canonical), "reused": True,
            })
        descriptor = build_portrait_descriptor(session, settings.world.world_id, cid)
        payload, _ = _generate_asset(
            session,
            asset_type="portrait",
            descriptor=descriptor,
            character_id=cid,
            location_id=None,
            object_id=None,
            actor_id=cid,
        )
        return JSONResponse(status_code=201, content={"asset": payload, "reused": False})

    @app.post("/visual/scenes")
    def create_scene(
        body: SceneIn,
        request_user=Depends(current_user),
        session: Session = Depends(db),
    ):
        gate()
        from app.db.models import Character
        from app.visual.descriptor import build_scene_descriptor

        # #86 (A4): the requesting player's character enriches the descriptor
        # (appearance/outfit + canonical reference). Read-only lookup; a user
        # without a character still gets a scene, just without a player part.
        player = (
            session.query(Character)
            .filter_by(world_id=settings.world.world_id, user_id=request_user.id)
            .order_by(Character.id)
            .first()
        )
        try:
            descriptor = build_scene_descriptor(
                session, settings.world.world_id, body.location_id, body.event_id,
                player_character_id=player.id if player else None,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        payload, _ = _generate_asset(
            session,
            asset_type="scene",
            descriptor=descriptor,
            character_id=None,
            location_id=body.location_id,
            object_id=None,
            actor_id=None,
        )
        return JSONResponse(status_code=201, content={"asset": payload, "reused": False})

    @app.post("/visual/assets/{asset_id}/canonical")
    def set_canonical(
        asset_id: int,
        body: CanonicalIn,
        request_user=Depends(current_user),
        session: Session = Depends(db),
    ):
        gate()
        from app.db.models import VisualAsset

        asset = session.get(VisualAsset, asset_id)
        if asset is None or asset.world_id != settings.world.world_id:
            raise HTTPException(status_code=404, detail="asset not found")
        asset.canonical = body.canonical
        session.commit()
        return _asset_payload(asset)

    @app.get("/visual/assets/{asset_id}")
    def get_asset(
        asset_id: int,
        request_user=Depends(current_user),
        session: Session = Depends(db),
    ):
        gate()
        from app.db.models import VisualAsset

        asset = session.get(VisualAsset, asset_id)
        if asset is None or asset.world_id != settings.world.world_id:
            raise HTTPException(status_code=404, detail="asset not found")
        return _asset_payload(asset)

    @app.get("/visual/assets/{asset_id}/file")
    def get_asset_file(
        asset_id: int,
        request_user=Depends(current_user),
        session: Session = Depends(db),
    ):
        gate()
        from app.db.models import VisualAsset

        asset = session.get(VisualAsset, asset_id)
        if asset is None or asset.world_id != settings.world.world_id:
            raise HTTPException(status_code=404, detail="asset not found")
        try:
            data = store.read(asset.storage_path)
        except (OSError, ValueError):
            raise HTTPException(status_code=404, detail="asset file missing")
        return Response(content=data, media_type="image/png")

    @app.get("/visual/characters/{cid}/portrait")
    def get_canonical_portrait(
        cid: str,
        request_user=Depends(current_user),
        session: Session = Depends(db),
    ):
        from app.db.models import Character, VisualAsset

        world_id = settings.world.world_id
        asset = (
            session.query(VisualAsset)
            .filter_by(
                world_id=world_id, asset_type="portrait",
                character_id=cid, canonical=True,
            )
            .first()
        )
        if asset is not None:
            gate()
            return _asset_payload(asset)

        # #191: canonical miss. Character guard first (pure reads, same 404
        # JSON as today — no existence leaking): unknown/dead → 404; a PLAYER
        # character (user_id set — own or another player's) keeps the
        # pure-read contract (own portraits flow through POST only; another
        # player's are never generated/served implicitly) and keeps the 503
        # gate when visual is disabled (m6/m158 pins).
        def _not_found() -> HTTPException:
            # Fresh instance per raise (a raised HTTPException carries a
            # traceback; never share one instance across requests).
            return HTTPException(
                status_code=404, detail="canonical portrait not found")

        character = (
            session.query(Character)
            .filter_by(world_id=world_id, id=cid)
            .first()
        )
        if character is None or not character.alive:
            raise _not_found()
        if character.user_id is not None:
            gate()
            raise _not_found()
        # NPC: visual disabled → honest 404, transport never built.
        if not visual_settings.enabled:
            raise _not_found()

        # Lazy canonical generation for an NPC (any auth user may trigger it
        # once per NPC). Failure honesty: any generation failure (transport
        # exception / non-2xx → 502, descriptor race, storage error) becomes
        # the SAME 404 as today — logged as a one-line warning for the
        # journal (a silent 404 is undiagnosable in prod) — and is remembered
        # in the negative cache so repeat GETs within the TTL do not retry
        # the transport.
        key = (world_id, cid)
        failed_at = _npc_portrait_negative.get(key)
        if failed_at is not None and (
            time.monotonic() - failed_at < NPC_PORTRAIT_NEGATIVE_TTL_SEC
        ):
            raise _not_found()
        _npc_portrait_negative.pop(key, None)  # expired entry out (pop: benign)

        with _npc_portrait_lock(key):
            # Single-flight double-check: a parallel GET may have committed
            # the canonical asset while this thread waited on the lock.
            asset = (
                session.query(VisualAsset)
                .filter_by(
                    world_id=world_id, asset_type="portrait",
                    character_id=cid, canonical=True,
                )
                .first()
            )
            if asset is not None:
                _npc_portrait_negative.pop(key, None)
                return _asset_payload(asset)
            # Re-check the negative cache: a parallel attempt may have failed
            # while this thread waited on the lock.
            failed_at = _npc_portrait_negative.get(key)
            if failed_at is not None and (
                time.monotonic() - failed_at < NPC_PORTRAIT_NEGATIVE_TTL_SEC
            ):
                raise _not_found()
            _npc_portrait_negative.pop(key, None)  # retrying: stale entry out
            try:
                from app.visual.descriptor import build_portrait_descriptor

                descriptor = build_portrait_descriptor(session, world_id, cid)
                payload, _ = _generate_asset(
                    session,
                    asset_type="portrait",
                    descriptor=descriptor,
                    character_id=cid,
                    location_id=None,
                    object_id=None,
                    actor_id=cid,
                    is_canonical=True,
                )
            except Exception as exc:
                # Same 404 as today; the failure is NOT retried on every
                # request (negative cache), and the state cannot be poisoned:
                # _generate_asset commits the canonical row + file in ONE
                # transaction, or writes nothing. One-line warning BEFORE the
                # 404: without it the failure is invisible in prod (uvicorn
                # runs --no-access-log and this handler answers everything).
                logger.warning(
                    "visual: lazy NPC portrait generation failed world=%s cid=%s: %r",
                    world_id, cid, exc)
                _npc_portrait_negative[key] = time.monotonic()
                _evict_npc_portrait_negative(time.monotonic())
                raise _not_found() from None
            _npc_portrait_negative.pop(key, None)  # success clears the entry
            return payload
