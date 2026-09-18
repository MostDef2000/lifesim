"""M5 (SPEC §104/R9, §79): WebSocket event stream.

/ws?token= — HMAC-auth; server sends {type:'hello', cursor}, then polls
world_events (id > cursor) and batches them as {type:'events', events:[...]}.
Read-only: no mutations over WS (MVP).
NOTE: no `from __future__ import annotations` — FastAPI must resolve the
WebSocket annotation at route-add time.
"""

import asyncio
import json
from typing import Any, Dict, List

from sqlalchemy.orm import Session


def _serialize_event(event) -> Dict[str, Any]:
    return {
        "id": event.id,
        "event_type": event.event_type,
        "actor_id": event.actor_id,
        "location_id": event.location_id,
        "game_timestamp": event.game_timestamp,
        "day": event.game_timestamp // 1440,
        "payload": json.loads(event.payload) if event.payload else {},
    }


def poll_events(
    session: Session, world_id: str, cursor: int, limit: int = 100
) -> List[Dict[str, Any]]:
    from app.db.models import WorldEvent

    events = (
        session.query(WorldEvent)
        .filter_by(world_id=world_id)
        .filter(WorldEvent.id > cursor)
        .order_by(WorldEvent.id)
        .limit(limit)
        .all()
    )
    return [_serialize_event(e) for e in events]


def register_ws_route(app, settings, session_factory) -> None:
    from fastapi import Query, WebSocket, WebSocketDisconnect

    from app.api.auth import verify_token

    @app.websocket("/ws")
    async def ws_endpoint(
        websocket: WebSocket,
        token: str = Query(""),
    ):
        payload = verify_token(token, _ws_secret(settings))
        if payload is None:
            await websocket.close(code=4401)
            return

        await websocket.accept()
        # M8 (§89): live connection counter for /admin/overview
        app.state.ws_connections = getattr(app.state, "ws_connections", 0) + 1
        cursor = 0
        await websocket.send_json({"type": "hello", "cursor": cursor})

        interval = settings.api.ws_interval_s
        try:
            while True:
                # non-blocking drain of client control messages (cursor updates)
                client_cursor = None
                try:
                    msg = await asyncio.wait_for(websocket.receive_json(), timeout=0.001)
                    if isinstance(msg, dict) and msg.get("type") == "cursor":
                        client_cursor = int(msg.get("id", 0))
                        await websocket.send_json({"type": "cursor", "id": client_cursor})
                except (asyncio.TimeoutError, WebSocketDisconnect):
                    pass
                except Exception:
                    pass

                with session_factory() as session:
                    events = poll_events(
                        session, settings.world.world_id, cursor
                    )
                if events:
                    cursor = events[-1]["id"]
                    await websocket.send_json({"type": "events", "events": events})

                if client_cursor is not None and client_cursor > cursor:
                    cursor = client_cursor

                await asyncio.sleep(interval)
        except WebSocketDisconnect:
            app.state.ws_connections = max(0, getattr(app.state, "ws_connections", 1) - 1)
            return
        except Exception:
            app.state.ws_connections = max(0, getattr(app.state, "ws_connections", 1) - 1)
            try:
                await websocket.close()
            except Exception:
                pass
            return


def _ws_secret(settings) -> str:
    from app.api.app import get_secret

    return get_secret(settings)
