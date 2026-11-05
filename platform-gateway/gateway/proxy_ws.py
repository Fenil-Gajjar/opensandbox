"""
WebSocket reverse proxy — relays WS connections to sandbox services.

Mirrors the bidirectional relay pattern from OpenSandbox's proxy.py:
  client ←→ gateway ←→ upstream sandbox (via OpenSandbox server proxy or direct execd)

Used for VNC, VSCode, Chrome browser, and any WebSocket-based sandbox access.
"""

from __future__ import annotations

import logging

import anyio
import websockets
from fastapi import WebSocket, status
from starlette.websockets import WebSocketDisconnect
from websockets.asyncio.client import ClientConnection
from websockets.typing import Origin

from gateway.config import settings
from gateway.auth import API_KEY_HEADER

logger = logging.getLogger(__name__)

# Headers NOT forwarded on the WebSocket handshake (managed by the library).
WS_HANDSHAKE_HEADERS = frozenset({
    "upgrade", "connection", "host",
    "sec-websocket-extensions", "sec-websocket-key",
    "sec-websocket-protocol", "sec-websocket-version",
})

SENSITIVE_HEADERS = frozenset({
    "authorization", "cookie",
    API_KEY_HEADER.lower(),
})


def _build_ws_url(path: str, query: str) -> str:
    """Convert upstream HTTP URL to ws:// and append path + query."""
    base = settings.upstream_url.rstrip("/")
    base = base.replace("https://", "wss://").replace("http://", "ws://")
    url = f"{base}{path}"
    if query:
        url = f"{url}?{query}"
    return url


def _filter_ws_headers(raw: dict[str, str]) -> dict[str, str]:
    """Keep app-level headers, drop handshake + sensitive ones."""
    out: dict[str, str] = {}
    for k, v in raw.items():
        if k.lower() in WS_HANDSHAKE_HEADERS or k.lower() in SENSITIVE_HEADERS:
            continue
        out[k] = v
    # Inject master key
    if settings.master_api_key:
        out[API_KEY_HEADER] = settings.master_api_key
    return out


async def _relay_client_to_backend(
    ws: WebSocket,
    backend: ClientConnection,
    cancel_scope: anyio.CancelScope,
) -> None:
    """Forward messages from the client WebSocket to the backend."""
    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.receive":
                if msg.get("text") is not None:
                    await backend.send(msg["text"])
                elif msg.get("bytes") is not None:
                    await backend.send(msg["bytes"])
            elif msg["type"] == "websocket.disconnect":
                code = msg.get("code", status.WS_1000_NORMAL_CLOSURE)
                await backend.close(code=code, reason=msg.get("reason", ""))
                return
    except WebSocketDisconnect as e:
        await backend.close(code=e.code, reason=getattr(e, "reason", "") or "")
    finally:
        cancel_scope.cancel()


async def _relay_backend_to_client(
    ws: WebSocket,
    backend: ClientConnection,
    cancel_scope: anyio.CancelScope,
) -> None:
    """Forward messages from the backend to the client WebSocket."""
    try:
        while True:
            payload = await backend.recv()
            if isinstance(payload, bytes):
                await ws.send_bytes(payload)
            else:
                await ws.send_text(payload)
    except websockets.ConnectionClosed as e:
        try:
            await ws.close(
                code=e.code or status.WS_1000_NORMAL_CLOSURE,
                reason=e.reason or "",
            )
        except RuntimeError:
            pass
    finally:
        cancel_scope.cancel()


async def proxy_websocket(ws: WebSocket, path: str) -> None:
    """
    Full WebSocket reverse proxy.

    Accepts the client handshake, connects to upstream, then relays
    messages bidirectionally until either side closes.
    """
    query = ws.url.query or ""
    target_url = _build_ws_url(path, query)
    headers = _filter_ws_headers(dict(ws.headers))
    subprotocols = list(ws.scope.get("subprotocols", []))
    raw_origin = ws.headers.get("origin")
    origin = Origin(raw_origin) if raw_origin else None

    try:
        async with websockets.connect(
            target_url,
            additional_headers=headers or None,
            subprotocols=subprotocols or None,
            origin=origin,
            max_size=None,  # don't cap payload size
        ) as backend:
            await ws.accept(subprotocol=backend.subprotocol)
            async with anyio.create_task_group() as tg:
                tg.start_soon(_relay_client_to_backend, ws, backend, tg.cancel_scope)
                tg.start_soon(_relay_backend_to_client, ws, backend, tg.cancel_scope)

    except websockets.InvalidStatus as e:
        logger.warning("Backend WS handshake failed for %s: %s", path, e)
        await _fail_ws(ws, status.WS_1008_POLICY_VIOLATION)
    except OSError as e:
        logger.warning("WS connect error for %s: %s", path, e)
        await _fail_ws(ws, status.WS_1011_INTERNAL_ERROR)
    except Exception:
        logger.exception("Unexpected WS proxy failure for %s", path)
        await _fail_ws(ws, status.WS_1011_INTERNAL_ERROR)


async def _fail_ws(ws: WebSocket, code: int, reason: str = "") -> None:
    """Accept then immediately close the WebSocket with an error code."""
    try:
        await ws.accept()
    except RuntimeError:
        pass
    try:
        await ws.close(code=code, reason=reason[:123])
    except RuntimeError:
        pass
