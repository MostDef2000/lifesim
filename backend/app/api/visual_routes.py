"""M6 (SPEC §105/66-70): visual API routes.

Registered from create_app; every route is gated by settings.visual.enabled (503).
Visual layer is read-only over world state (П1-аналог): writes only visual_assets + files.
NOTE: no `from __future__ import annotations` — it breaks FastAPI resolution of the
locally-defined Pydantic models (same trap as app.py/ws.py in M5).
"""
import hashlib
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.config.config import Settings


def _deterministic_seed(*parts: Any) -> int:
    digest = hashlib.sha256(":".join(str(p) for p in parts).encode()).digest()
    return int.from_bytes(digest[:8], "big") % (2**31)


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
    ) -> tuple[dict, bytes]:
        """§66 pipeline: descriptor -> prompt -> Flux -> image -> asset registration."""
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

        from app.visual.descriptor import build_prompt

        prompt = build_prompt(descriptor)

        existing = (
            session.query(VisualAsset)
            .filter_by(world_id=world_id, asset_type=asset_type)
            .count()
        )
        seed = _deterministic_seed(
            world_id, character_id or location_id, asset_type, existing
        )

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
            canonical=False,
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
        from app.visual.descriptor import build_scene_descriptor

        try:
            descriptor = build_scene_descriptor(
                session, settings.world.world_id, body.location_id, body.event_id,
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
        gate()
        from app.db.models import VisualAsset

        asset = (
            session.query(VisualAsset)
            .filter_by(
                world_id=settings.world.world_id, asset_type="portrait",
                character_id=cid, canonical=True,
            )
            .first()
        )
        if asset is None:
            raise HTTPException(status_code=404, detail="canonical portrait not found")
        return _asset_payload(asset)
