"""
HTTP reverse proxy — forwards requests to the upstream OpenSandbox server.

Key behaviour:
  • Swaps the user's API key for the master key before forwarding.
  • Streams request and response bodies (no buffering large payloads).
  • Strips hop-by-hop headers per RFC 2616 §13.5.1.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse, Response

from gateway.config import settings
from gateway.auth import API_KEY_HEADER

logger = logging.getLogger(__name__)

# Shared async HTTP client — created in main.py lifespan, used everywhere.
_http_client: Optional[httpx.AsyncClient] = None

# RFC 2616 Section 13.5.1
HOP_BY_HOP = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade",
})


def set_http_client(client: httpx.AsyncClient) -> None:
    """Set the shared HTTP client (called during app startup)."""
    global _http_client
    _http_client = client


def get_http_client() -> httpx.AsyncClient:
    if _http_client is None:
        raise RuntimeError("HTTP client not initialized.")
    return _http_client


def _upstream_url(path: str) -> str:
    """Build full upstream URL from path."""
    return f"{settings.upstream_url.rstrip('/')}{path}"


def _proxy_headers(request: Request) -> Dict[str, str]:
    """
    Build headers to forward upstream.

    • Drops hop-by-hop headers.
    • Drops the user's API key (we inject the master key instead).
    • Drops Host (httpx sets it from the target URL).
    • Injects X-Forwarded-* headers.
    """
    out: Dict[str, str] = {}
    for key, value in request.headers.items():
        k_lower = key.lower()
        if k_lower in HOP_BY_HOP:
            continue
        if k_lower == "host":
            continue
        if k_lower == API_KEY_HEADER.lower():
            continue  # drop user key — master key injected below
        if k_lower == "content-length":
            continue  # let httpx recalculate from actual body
        out[key] = value

    # Inject master key
    if settings.master_api_key:
        out[API_KEY_HEADER] = settings.master_api_key

    # Standard reverse proxy headers
    if request.client:
        out.setdefault("X-Forwarded-For", request.client.host)
    out.setdefault("X-Forwarded-Proto", request.url.scheme)
    host = request.headers.get("host")
    if host:
        out.setdefault("X-Forwarded-Host", host)

    return out


def _strip_response_headers(resp: httpx.Response) -> Dict[str, str]:
    """Remove hop-by-hop headers from the upstream response."""
    excluded = set(HOP_BY_HOP)
    conn = resp.headers.get("connection")
    if conn:
        excluded.update(h.strip().lower() for h in conn.split(",") if h.strip())
    return {
        k: v for k, v in resp.headers.items()
        if k.lower() not in excluded
    }


async def forward_to_upstream(
    method: str,
    path: str,
    *,
    body: Optional[bytes] = None,
    query_string: str = "",
    headers: Optional[Dict[str, str]] = None,
) -> httpx.Response:
    """
    Low-level forward — used by both route handlers and internal calls
    (e.g. billing auto-stop).

    Returns the raw httpx.Response (NOT streamed).
    """
    client = get_http_client()
    url = _upstream_url(path)

    hdrs = dict(headers or {})
    if settings.master_api_key and API_KEY_HEADER not in hdrs:
        hdrs[API_KEY_HEADER] = settings.master_api_key

    resp = await client.request(
        method=method,
        url=url,
        content=body,
        params=query_string if query_string else None,
        headers=hdrs,
    )
    return resp


async def proxy_request(
    request: Request,
    path: str,
    *,
    body_override: Optional[bytes] = None,
    extra_query: Optional[str] = None,
) -> StreamingResponse | Response:
    """
    Full streaming reverse proxy for a FastAPI Request object.

    Streams both request body (POST/PUT/PATCH) and response body.
    Preserves status codes, content types, and non-hop-by-hop headers.
    """
    client = get_http_client()
    url = _upstream_url(path)
    hdrs = _proxy_headers(request)
    qs = request.url.query or ""
    if extra_query:
        qs = f"{qs}&{extra_query}" if qs else extra_query

    has_body = request.method in ("POST", "PUT", "PATCH", "DELETE")

    try:
        if body_override is not None:
            content = body_override
        elif has_body:
            content = await request.body()
        else:
            content = None

        req = client.build_request(
            method=request.method,
            url=url,
            params=qs if qs else None,
            headers=hdrs,
            content=content,
        )
        resp = await client.send(req, stream=True)

        async def stream_body():
            try:
                async for chunk in resp.aiter_bytes():
                    yield chunk
            finally:
                await resp.aclose()

        return StreamingResponse(
            content=stream_body(),
            status_code=resp.status_code,
            headers=_strip_response_headers(resp),
        )

    except httpx.ConnectError as e:
        logger.error("Upstream connection failed: %s", e)
        return JSONResponse(
            status_code=502,
            content={
                "code": "UPSTREAM_UNREACHABLE",
                "message": f"Cannot connect to sandbox server: {e}",
            },
        )
    except Exception as e:
        logger.exception("Proxy error: %s", e)
        return JSONResponse(
            status_code=500,
            content={
                "code": "PROXY_ERROR",
                "message": f"Internal proxy error: {e}",
            },
        )
