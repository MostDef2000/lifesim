"""M5 (SPEC §104/R9, §79): WebSocket event stream.

/ws — HMAC-auth via the httpOnly session cookie on the upgrade request
(#124: no credential in the URL, so the token never reaches access logs);
server sends {type:'hello', cursor}, then polls world_events (id > cursor)
and batches them as {type:'events', events:[...]}.
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
    from fastapi import WebSocket, WebSocketDisconnect

    from app.api.auth import verify_token

    @app.websocket("/ws")
    async def ws_endpoint(
        websocket: WebSocket,
    ):
        # #124: origin gate — browsers attach Origin on cross-site WS
        # upgrades; absent Origin (TestClient, non-browser clients) is
        # allowed, a present one must match the Host header.
        origin = ""
        host_hdr = ""
        for hname, hvalue in websocket.scope["headers"]:
            if hname == b"origin":
                origin = hvalue.decode("latin-1", errors="replace")
            elif hname == b"host":
                host_hdr = hvalue.decode("latin-1", errors="replace")
        if origin:
            from urllib.parse import urlsplit
            origin_host = urlsplit(origin).hostname
            host_name = urlsplit(f"//{host_hdr}").hostname if host_hdr else None
            # hostname is already lowercased by urlsplit on both sides;
            # IPv6-literal Hosts and "null" origins resolve correctly
            # (null/no-host -> None -> reject, fail closed).
            if origin_host is None or origin_host != host_name:
                await websocket.close(code=4401)
                return

        from app.db.models import User

        # #124: auth via the httpOnly session cookie carried by the upgrade
        # request (same-origin client) — no credential in the URL, so the
        # token never reaches access logs. Supersedes /ws?token= (issue #124).
        session_cookie = _session_cookie(websocket, settings)
        payload = verify_token(session_cookie, _ws_secret(settings)) if session_cookie else None
        if payload is not None:
            try:
                with session_factory() as session:
                    user = session.get(User, int(payload["sub"]))
            except Exception:
                user = None  # fail closed on DB errors
            if user is None or user.disabled:
                payload = None
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


def _session_cookie(websocket, settings) -> str:
    """#124: extract settings.api.cookie_name from raw ASGI cookie headers.

    Fail-closed: ambiguous duplicates or unparseable header -> empty string.
    """
    from http.cookies import SimpleCookie

    name = settings.api.cookie_name
    candidates = []
    for hname, hvalue in websocket.scope["headers"]:
        if hname != b"cookie":
            continue
        try:
            jar = SimpleCookie()
            jar.load(hvalue.decode("latin-1"))
        except Exception:  # unparseable cookie header -> fail closed
            return ""
        morsel = jar.get(name)
        if morsel is not None and morsel.value:
            candidates.append(morsel.value)
    if len(candidates) != 1:
        return ""  # absent or ambiguous -> fail closed
    return candidates[0]


def _ws_secret(settings) -> str:
    from app.api.app import get_secret

    return get_secret(settings)
