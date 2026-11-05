"""
Platform Gateway — FastAPI application.

This is the entry point. It:
  1. Mounts auth middleware
  2. Registers all OpenSandbox proxy routes with tenant isolation
  3. Registers platform-specific routes (signup, wallet, etc.)
  4. Starts the billing metering background task
  5. Manages httpx client and DB pool lifecycle
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Optional

import httpx
from fastapi import FastAPI, Request, WebSocket, status
from fastapi.responses import JSONResponse, Response

from gateway.config import settings
from gateway.db import init_pool, close_pool, get_pool
from gateway.auth import GatewayAuthMiddleware
from gateway.ownership import (
    TENANT_KEY,
    record_ownership,
    verify_ownership,
    mark_sandbox_deleted,
    mark_sandbox_paused,
    mark_sandbox_running,
    record_snapshot_ownership,
    verify_snapshot_ownership,
)
from gateway.limits import (
    validate_resource_limits,
    check_concurrency,
    extract_resources,
)
from gateway.billing import (
    check_balance_for_creation,
    start_usage_record,
    finalize_usage_record,
    pause_billing,
    resume_billing,
    run_metering_loop,
)
from gateway.proxy_http import proxy_request, forward_to_upstream, set_http_client
from gateway.proxy_ws import proxy_websocket
from gateway.routes.platform import router as platform_router
from gateway.routes.admin import router as admin_router
from gateway.catalog import load_catalog, list_catalog_summary, get_catalog_entry, resolve_catalog_body

logger = logging.getLogger(__name__)

# ── Metering task handle ────────────────────────────────────────────────────
_metering_task: Optional[asyncio.Task] = None


# ── App lifespan ────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage startup/shutdown of DB pool, HTTP client, and metering task."""
    global _metering_task

    # Startup
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
    )
    logger.info("Starting Platform Gateway v0.1.0")
    logger.info("Upstream: %s", settings.upstream_url)

    # DB pool
    await init_pool()

    # HTTP client for proxying
    client = httpx.AsyncClient(
        timeout=httpx.Timeout(300, connect=10),
        limits=httpx.Limits(max_connections=200, max_keepalive_connections=50),
        follow_redirects=False,
    )
    set_http_client(client)

    # Load catalog
    load_catalog()

    # Metering background task
    _metering_task = asyncio.create_task(run_metering_loop())

    logger.info("Gateway ready on %s:%d", settings.host, settings.port)

    yield  # ── App is running ──

    # Shutdown
    logger.info("Shutting down gateway...")
    if _metering_task:
        _metering_task.cancel()
        try:
            await _metering_task
        except asyncio.CancelledError:
            pass
    await client.aclose()
    await close_pool()
    logger.info("Gateway stopped.")


# ── FastAPI app ─────────────────────────────────────────────────────────────

app = FastAPI(
    title="OpenSandbox Platform Gateway",
    version="0.1.0",
    description="Multi-tenant API gateway for OpenSandbox",
    lifespan=lifespan,
)

# Auth middleware
app.add_middleware(GatewayAuthMiddleware)

# Platform routes (signup, wallet, usage, keys)
app.include_router(platform_router)

# Admin routes (users, refund, stats)
app.include_router(admin_router)


# ── Health check ────────────────────────────────────────────────────────────

@app.get("/health", tags=["Health"])
async def health():
    return {"status": "ok", "service": "platform-gateway"}


# ── Catalog ────────────────────────────────────────────────────────────────

@app.get("/api/catalog", tags=["Catalog"])
async def api_list_catalog(category: str | None = None, tag: str | None = None):
    """List prebuilt sandbox flavors. Optionally filter by category or tag."""
    return list_catalog_summary(category=category, tag=tag)


@app.get("/api/catalog/{slug}", tags=["Catalog"])
async def api_get_catalog(slug: str):
    """Get full details of a prebuilt sandbox flavor."""
    entry = get_catalog_entry(slug)
    if entry is None:
        return JSONResponse(
            status_code=404,
            content={"code": "CATALOG_NOT_FOUND", "message": f"No catalog entry with slug '{slug}'."},
        )
    return entry


# ============================================================================
# OpenSandbox Proxy Routes — with tenant isolation, billing, ownership
# ============================================================================

# ── CREATE SANDBOX ──────────────────────────────────────────────────────────

@app.post("/v1/sandboxes", tags=["Sandboxes"])
async def create_sandbox(request: Request):
    """
    Create a sandbox — the most complex route.

    Pipeline:
      1. Auth (middleware)
      2. Parse body
      3. Validate resource limits (16 vCPU, 32Gi caps)
      4. Check concurrency (max 50)
      5. Check wallet balance (min 1 hour cost)
      6. Inject metadata.tenant_id
      7. Forward to upstream
      8. Record ownership + start billing
    """
    user_id = request.state.user_id
    body_bytes = await request.body()

    try:
        body = json.loads(body_bytes)
    except (json.JSONDecodeError, ValueError):
        return JSONResponse(
            status_code=400,
            content={"code": "INVALID_JSON", "message": "Request body is not valid JSON."},
        )

    # 2b. Resolve catalog if specified
    catalog_slug = body.pop("catalog", None)
    if catalog_slug:
        resolved, err_msg = resolve_catalog_body(catalog_slug, body)
        if resolved is None:
            return JSONResponse(
                status_code=400,
                content={"code": "INVALID_CATALOG", "message": err_msg},
            )
        body = resolved

    # 3. Resource limits
    err = validate_resource_limits(body)
    if err:
        return err

    # 4. Concurrency
    err = await check_concurrency(user_id)
    if err:
        return err

    # 5. Balance check
    cpu, mem = extract_resources(body)
    err = await check_balance_for_creation(user_id, cpu, mem)
    if err:
        return err

    # 6. Inject tenant_id into metadata
    if "metadata" not in body or body["metadata"] is None:
        body["metadata"] = {}
    body["metadata"][TENANT_KEY] = user_id

    # 7. Forward with modified body
    modified_body = json.dumps(body).encode("utf-8")
    response = await proxy_request(request, "/v1/sandboxes", body_override=modified_body)

    # 8. Record ownership + start billing if creation accepted
    if hasattr(response, "status_code") and response.status_code in (200, 201, 202):
        # We need to read the response body to get the sandbox ID.
        # Since proxy_request returns a StreamingResponse, we need to
        # consume it, extract the sandbox ID, then return a normal response.
        resp_body = b""
        async for chunk in response.body_iterator:
            resp_body += chunk

        try:
            resp_data = json.loads(resp_body)
            sandbox_id = resp_data.get("id", "")
            if sandbox_id:
                await record_ownership(sandbox_id, user_id, cpu, mem)

                # Start billing if state is Running, otherwise schedule check
                resp_state = resp_data.get("status", {}).get("state", "")
                if resp_state in ("Running", "Pending", ""):
                    await start_usage_record(sandbox_id, user_id, cpu, mem)
        except (json.JSONDecodeError, KeyError):
            logger.warning("Could not parse create response for ownership recording")

        return Response(
            content=resp_body,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type="application/json",
        )

    return response


# ── LIST SANDBOXES ──────────────────────────────────────────────────────────

@app.get("/v1/sandboxes", tags=["Sandboxes"])
async def list_sandboxes(request: Request):
    """
    List sandboxes — OPTIMIZED: serves from gateway ownership table.

    Why not just proxy with a metadata filter?
    Because OpenSandbox's list_sandboxes fetches ALL pods from K8s API
    (list_helpers.py line 58-61), then filters in Python. At 50K sandboxes,
    this takes 10-30 seconds. Instead we:
      1. Query our ownership table (indexed, ~0.1ms)
      2. Batch-fetch each sandbox via individual GET (K8s name lookup, ~10ms each)
      3. Parallelize with asyncio.gather → ~50ms total for 5 sandboxes

    This is O(M) where M = THIS USER's sandboxes, not O(N) total.
    """
    import asyncio

    user_id = request.state.user_id
    pool = get_pool()

    # Parse query params for state filter and pagination
    state_filter = request.query_params.get("state")
    page = int(request.query_params.get("page", "1"))
    page_size = min(int(request.query_params.get("pageSize", "20")), 200)

    # 1. Get this user's sandbox IDs from our ownership table — O(1) indexed
    if state_filter:
        # Map lifecycle state → ownership state
        ownership_states = []
        requested_states = [s.strip() for s in state_filter.split(",")]
        for s in requested_states:
            if s in ("Running", "Pending"):
                ownership_states.append("active")
            elif s in ("Paused", "Pausing", "Resuming"):
                ownership_states.append("paused")
        if not ownership_states:
            ownership_states = ["active", "paused"]

        rows = await pool.fetch(
            "SELECT sandbox_id FROM sandbox_ownership WHERE user_id = $1 AND state = ANY($2) ORDER BY created_at DESC",
            user_id,
            ownership_states,
        )
    else:
        rows = await pool.fetch(
            "SELECT sandbox_id FROM sandbox_ownership WHERE user_id = $1 AND state IN ('active', 'paused') ORDER BY created_at DESC",
            user_id,
        )

    total_items = len(rows)
    total_pages = max(1, (total_items + page_size - 1) // page_size)
    start = (page - 1) * page_size
    page_ids = [r["sandbox_id"] for r in rows[start : start + page_size]]

    # 2. Batch-fetch sandbox details in parallel — each is a direct K8s name lookup (~10ms)
    async def _fetch_one(sid: str):
        try:
            resp = await forward_to_upstream("GET", f"/v1/sandboxes/{sid}")
            if resp.status_code == 200:
                return json.loads(resp.content)
        except Exception:
            pass
        return None

    results = await asyncio.gather(*[_fetch_one(sid) for sid in page_ids])
    items = [r for r in results if r is not None]

    return {
        "items": items,
        "pagination": {
            "page": page,
            "pageSize": page_size,
            "totalItems": total_items,
            "totalPages": total_pages,
            "hasNextPage": page < total_pages,
        },
    }



# ── GET SANDBOX ─────────────────────────────────────────────────────────────

@app.get("/v1/sandboxes/{sandbox_id}", tags=["Sandboxes"])
async def get_sandbox(request: Request, sandbox_id: str):
    """Get sandbox — ownership check then forward."""
    err = await verify_ownership(sandbox_id, request.state.user_id)
    if err:
        return err
    return await proxy_request(request, f"/v1/sandboxes/{sandbox_id}")


# ── PATCH METADATA ──────────────────────────────────────────────────────────

@app.patch("/v1/sandboxes/{sandbox_id}/metadata", tags=["Sandboxes"])
async def patch_sandbox_metadata(request: Request, sandbox_id: str):
    """
    Patch metadata — ownership check + protect tenant_id from deletion.

    If user tries to set tenant_id to null (delete it) or change it,
    we reject the request.
    """
    err = await verify_ownership(sandbox_id, request.state.user_id)
    if err:
        return err

    body_bytes = await request.body()
    try:
        body = json.loads(body_bytes)
    except (json.JSONDecodeError, ValueError):
        return JSONResponse(status_code=400, content={"code": "INVALID_JSON", "message": "Invalid JSON."})

    # Protect tenant_id
    if isinstance(body, dict) and TENANT_KEY in body:
        if body[TENANT_KEY] is None or body[TENANT_KEY] != request.state.user_id:
            return JSONResponse(
                status_code=403,
                content={"code": "FORBIDDEN", "message": "Cannot modify tenant_id metadata."},
            )

    return await proxy_request(request, f"/v1/sandboxes/{sandbox_id}/metadata")


# ── DELETE SANDBOX ──────────────────────────────────────────────────────────

@app.delete("/v1/sandboxes/{sandbox_id}", tags=["Sandboxes"])
async def delete_sandbox(request: Request, sandbox_id: str):
    """Delete — ownership check, forward, finalize billing, update ownership."""
    err = await verify_ownership(sandbox_id, request.state.user_id)
    if err:
        return err

    response = await proxy_request(request, f"/v1/sandboxes/{sandbox_id}")

    if hasattr(response, "status_code") and response.status_code == 204:
        await finalize_usage_record(sandbox_id)
        await mark_sandbox_deleted(sandbox_id)

    return response


# ── PAUSE SANDBOX ───────────────────────────────────────────────────────────

@app.post("/v1/sandboxes/{sandbox_id}/pause", tags=["Sandboxes"])
async def pause_sandbox(request: Request, sandbox_id: str):
    """Pause — ownership check, forward, stop compute billing."""
    err = await verify_ownership(sandbox_id, request.state.user_id)
    if err:
        return err

    response = await proxy_request(request, f"/v1/sandboxes/{sandbox_id}/pause")

    if hasattr(response, "status_code") and response.status_code == 202:
        await pause_billing(sandbox_id)
        await mark_sandbox_paused(sandbox_id)

    return response


# ── RESUME SANDBOX ──────────────────────────────────────────────────────────

@app.post("/v1/sandboxes/{sandbox_id}/resume", tags=["Sandboxes"])
async def resume_sandbox(request: Request, sandbox_id: str):
    """Resume — ownership check, balance check, forward, restart billing."""
    user_id = request.state.user_id
    err = await verify_ownership(sandbox_id, user_id)
    if err:
        return err

    # Get the sandbox's resource specs from ownership table
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT cpu_vcpu, memory_gib FROM sandbox_ownership WHERE sandbox_id = $1",
        sandbox_id,
    )
    cpu = float(row["cpu_vcpu"]) if row else 1.0
    mem = float(row["memory_gib"]) if row else 1.0

    # Balance check before resuming
    err = await check_balance_for_creation(user_id, cpu, mem)
    if err:
        return err

    response = await proxy_request(request, f"/v1/sandboxes/{sandbox_id}/resume")

    if hasattr(response, "status_code") and response.status_code == 202:
        await resume_billing(sandbox_id, user_id, cpu, mem)
        await mark_sandbox_running(sandbox_id)

    return response


# ── RENEW EXPIRATION ────────────────────────────────────────────────────────

@app.post("/v1/sandboxes/{sandbox_id}/renew-expiration", tags=["Sandboxes"])
async def renew_sandbox_expiration(request: Request, sandbox_id: str):
    """Renew expiration — ownership check then forward."""
    err = await verify_ownership(sandbox_id, request.state.user_id)
    if err:
        return err
    return await proxy_request(request, f"/v1/sandboxes/{sandbox_id}/renew-expiration")


# ── ENDPOINTS ───────────────────────────────────────────────────────────────

# System ports that MUST route through the gateway for auth + ownership.
_FORCE_PROXY_PORTS = {44772, 18080}   # execd, egress

@app.get("/v1/sandboxes/{sandbox_id}/endpoints/{port}", tags=["Sandboxes"])
async def get_sandbox_endpoint(request: Request, sandbox_id: str, port: int):
    """Get endpoint — ownership check then forward.

    For system ports (execd=44772, egress=18080), the gateway always forces
    ``use_server_proxy=true`` so that SDK command / file / health traffic is
    routed back through the authenticated gateway regardless of what the
    client requested.  User-facing ports (e.g. 8080 for VS Code) are left
    unchanged so the server can return a browser-friendly wildcard URL.

    When force-proxying, the user's API key is injected into the endpoint
    response ``headers`` so that SDK adapters (health, commands, files)
    automatically authenticate subsequent proxy requests through the gateway.
    The upstream OpenSandbox execd doesn't require auth (direct pod access),
    but our gateway does.
    """
    err = await verify_ownership(sandbox_id, request.state.user_id)
    if err:
        return err

    upstream = f"/v1/sandboxes/{sandbox_id}/endpoints/{port}"
    force_proxy = port in _FORCE_PROXY_PORTS

    if force_proxy:
        # System ports: force use_server_proxy=true via extra_query so the
        # server returns a proxy endpoint.  We'll rewrite the response below
        # to point at the gateway's public URL instead of internal K8s DNS.
        response = await proxy_request(
            request, upstream, extra_query="use_server_proxy=true"
        )
    else:
        # User-facing ports (e.g. 8080 for VS Code): strip use_server_proxy
        # from the forwarded query so the server returns browser-friendly
        # wildcard URLs (e.g. abc123-8080.sandbox.hyperlake.cloud).
        # The SDK/CLI may set use_server_proxy globally, but only system
        # ports should actually route through the gateway proxy.
        from urllib.parse import urlencode, parse_qs

        orig_qs = request.url.query or ""
        if "use_server_proxy" in orig_qs:
            params = parse_qs(orig_qs, keep_blank_values=True)
            params.pop("use_server_proxy", None)
            cleaned_qs = urlencode(params, doseq=True)
            # Build a new Request with the cleaned query string so
            # proxy_request doesn't forward use_server_proxy upstream.
            scope = dict(request.scope)
            scope["query_string"] = cleaned_qs.encode()
            request = Request(scope, request.receive)

        response = await proxy_request(request, upstream)
        return response

    # ── System-port response rewriting ──────────────────────────────────
    # Rewrite the endpoint URL to the gateway's public domain and inject
    # the caller's API key so SDK adapters auto-authenticate.
    try:
        resp_body = b""
        if hasattr(response, "body_iterator"):
            async for chunk in response.body_iterator:
                resp_body += chunk if isinstance(chunk, bytes) else chunk.encode()
        elif hasattr(response, "body"):
            resp_body = response.body

        data = json.loads(resp_body)
        if isinstance(data, dict):
            # Rewrite endpoint to point to the gateway's public URL
            host = request.headers.get("host") or request.url.netloc
            data["endpoint"] = f"{host}/v1/sandboxes/{sandbox_id}/proxy/{port}"

            raw_key = getattr(request.state, "raw_api_key", None)
            if raw_key:
                hdrs = data.get("headers") or {}
                hdrs["OPEN-SANDBOX-API-KEY"] = raw_key
                data["headers"] = hdrs

            new_body = json.dumps(data).encode()

            # Preserve original headers but update Content-Length
            new_headers = dict(response.headers)
            new_headers["content-length"] = str(len(new_body))

            return Response(
                content=new_body,
                status_code=response.status_code,
                headers=new_headers,
                media_type="application/json",
            )
    except (json.JSONDecodeError, AttributeError):
        pass  # Return original response on any parse failure

    return response



# ── SNAPSHOTS ───────────────────────────────────────────────────────────────

@app.post("/v1/sandboxes/{sandbox_id}/snapshots", tags=["Snapshots"])
async def create_snapshot(request: Request, sandbox_id: str):
    """Create snapshot — ownership of parent sandbox required."""
    err = await verify_ownership(sandbox_id, request.state.user_id)
    if err:
        return err

    response = await proxy_request(request, f"/v1/sandboxes/{sandbox_id}/snapshots")

    # Record snapshot ownership
    if hasattr(response, "status_code") and response.status_code in (200, 201, 202):
        resp_body = b""
        async for chunk in response.body_iterator:
            resp_body += chunk
        try:
            resp_data = json.loads(resp_body)
            snap_id = resp_data.get("id", "")
            if snap_id:
                await record_snapshot_ownership(snap_id, sandbox_id)
        except (json.JSONDecodeError, KeyError):
            pass
        return Response(
            content=resp_body,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type="application/json",
        )
    return response


@app.get("/v1/snapshots", tags=["Snapshots"])
async def list_snapshots(request: Request):
    """
    List snapshots — tenant-isolated.

    Fetches this user's sandbox IDs from ownership table, then filters
    the upstream snapshot list to only include snapshots from those sandboxes.
    """
    import asyncio

    user_id = request.state.user_id
    pool = get_pool()

    # Get all sandbox IDs this user owns (including deleted — snapshots survive sandbox deletion)
    rows = await pool.fetch(
        "SELECT sandbox_id FROM sandbox_ownership WHERE user_id = $1",
        user_id,
    )
    owned_sandbox_ids = {r["sandbox_id"] for r in rows}

    if not owned_sandbox_ids:
        return {"items": [], "pagination": {"page": 1, "pageSize": 20, "totalItems": 0, "totalPages": 0, "hasNextPage": False}}

    # Forward to upstream and filter results
    response = await proxy_request(request, "/v1/snapshots")

    # Filter response to only include this user's snapshots
    if hasattr(response, "body_iterator"):
        resp_body = b""
        async for chunk in response.body_iterator:
            resp_body += chunk
        try:
            data = json.loads(resp_body)
            if "items" in data:
                data["items"] = [
                    s for s in data["items"]
                    if s.get("sandboxId", "") in owned_sandbox_ids
                ]
                data["pagination"]["totalItems"] = len(data["items"])
            return JSONResponse(content=data, status_code=response.status_code)
        except (json.JSONDecodeError, KeyError):
            return Response(content=resp_body, status_code=response.status_code, media_type="application/json")

    return response


@app.get("/v1/snapshots/{snapshot_id}", tags=["Snapshots"])
async def get_snapshot(request: Request, snapshot_id: str):
    """Get snapshot — verify snapshot ownership then forward."""
    err = await verify_snapshot_ownership(snapshot_id, request.state.user_id)
    if err:
        return err
    return await proxy_request(request, f"/v1/snapshots/{snapshot_id}")


@app.delete("/v1/snapshots/{snapshot_id}", tags=["Snapshots"])
async def delete_snapshot(request: Request, snapshot_id: str):
    """Delete snapshot — verify snapshot ownership then forward."""
    err = await verify_snapshot_ownership(snapshot_id, request.state.user_id)
    if err:
        return err
    return await proxy_request(request, f"/v1/snapshots/{snapshot_id}")


# ── DIAGNOSTICS ─────────────────────────────────────────────────────────────

@app.get("/v1/sandboxes/{sandbox_id}/diagnostics/logs", tags=["Diagnostics"])
async def get_sandbox_logs(request: Request, sandbox_id: str):
    """Get sandbox container logs — ownership check then forward."""
    err = await verify_ownership(sandbox_id, request.state.user_id)
    if err:
        return err
    return await proxy_request(request, f"/v1/sandboxes/{sandbox_id}/diagnostics/logs")


@app.get("/v1/sandboxes/{sandbox_id}/diagnostics/inspect", tags=["Diagnostics"])
async def get_sandbox_inspect(request: Request, sandbox_id: str):
    """Get sandbox inspection info — ownership check then forward."""
    err = await verify_ownership(sandbox_id, request.state.user_id)
    if err:
        return err
    return await proxy_request(request, f"/v1/sandboxes/{sandbox_id}/diagnostics/inspect")


@app.get("/v1/sandboxes/{sandbox_id}/diagnostics/events", tags=["Diagnostics"])
async def get_sandbox_events(request: Request, sandbox_id: str):
    """Get sandbox events — ownership check then forward."""
    err = await verify_ownership(sandbox_id, request.state.user_id)
    if err:
        return err
    return await proxy_request(request, f"/v1/sandboxes/{sandbox_id}/diagnostics/events")


@app.get("/v1/sandboxes/{sandbox_id}/diagnostics/summary", tags=["Diagnostics"])
async def get_sandbox_diagnostics_summary(request: Request, sandbox_id: str):
    """One-shot diagnostics summary — ownership check then forward."""
    err = await verify_ownership(sandbox_id, request.state.user_id)
    if err:
        return err
    return await proxy_request(request, f"/v1/sandboxes/{sandbox_id}/diagnostics/summary")


# ── HTTP PROXY (to sandbox services) ───────────────────────────────────────

@app.api_route(
    "/v1/sandboxes/{sandbox_id}/proxy/{port}/{full_path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    tags=["Proxy"],
)
async def proxy_sandbox_http(request: Request, sandbox_id: str, port: int, full_path: str = ""):
    """HTTP proxy to sandbox service — ownership check then stream."""
    err = await verify_ownership(sandbox_id, request.state.user_id)
    if err:
        return err
    path = f"/v1/sandboxes/{sandbox_id}/proxy/{port}"
    if full_path:
        path = f"{path}/{full_path}"
    return await proxy_request(request, path)


@app.api_route(
    "/v1/sandboxes/{sandbox_id}/proxy/{port}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    tags=["Proxy"],
    include_in_schema=False,
)
async def proxy_sandbox_http_root(request: Request, sandbox_id: str, port: int):
    """HTTP proxy to sandbox service root — ownership check then stream."""
    err = await verify_ownership(sandbox_id, request.state.user_id)
    if err:
        return err
    return await proxy_request(request, f"/v1/sandboxes/{sandbox_id}/proxy/{port}")


# ── WebSocket PROXY (to sandbox services) ──────────────────────────────────

async def _ws_authenticate(ws: WebSocket) -> str | None:
    """
    Authenticate a WebSocket connection via header or query param.

    Returns user_id on success, or None (after closing the WS) on failure.
    """
    raw_key = ws.headers.get("OPEN-SANDBOX-API-KEY") or ws.query_params.get("api_key")
    if not raw_key:
        await ws.close(code=status.WS_1008_POLICY_VIOLATION, reason="Missing API key")
        return None

    from gateway.auth import hash_api_key
    pool = get_pool()
    key_hash = hash_api_key(raw_key)
    row = await pool.fetchrow(
        "SELECT user_id FROM api_keys WHERE key_hash = $1 AND revoked_at IS NULL",
        key_hash,
    )
    if not row:
        await ws.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid API key")
        return None

    return row["user_id"]


@app.websocket("/v1/sandboxes/{sandbox_id}/proxy/{port}/{full_path:path}")
async def proxy_sandbox_ws(ws: WebSocket, sandbox_id: str, port: int, full_path: str = ""):
    """WebSocket proxy to sandbox — ownership check then relay."""
    user_id = await _ws_authenticate(ws)
    if user_id is None:
        return

    err = await verify_ownership(sandbox_id, user_id)
    if err:
        await ws.close(code=status.WS_1008_POLICY_VIOLATION, reason="Access denied")
        return

    path = f"/v1/sandboxes/{sandbox_id}/proxy/{port}"
    if full_path:
        path = f"{path}/{full_path}"
    await proxy_websocket(ws, path)


@app.websocket("/v1/sandboxes/{sandbox_id}/proxy/{port}")
async def proxy_sandbox_ws_root(ws: WebSocket, sandbox_id: str, port: int):
    """WebSocket proxy root — same as above without path."""
    user_id = await _ws_authenticate(ws)
    if user_id is None:
        return

    err = await verify_ownership(sandbox_id, user_id)
    if err:
        await ws.close(code=status.WS_1008_POLICY_VIOLATION, reason="Access denied")
        return

    await proxy_websocket(ws, f"/v1/sandboxes/{sandbox_id}/proxy/{port}")



# ── POOL routes are admin-only — BLOCKED for regular users ──────────────────

@app.api_route("/v1/pools{path:path}", methods=["GET", "POST", "PUT", "DELETE"], tags=["Admin"])
async def pools_blocked(request: Request, path: str = ""):
    """Pool management is admin-only. Not exposed through the platform gateway."""
    return JSONResponse(
        status_code=status.HTTP_403_FORBIDDEN,
        content={
            "code": "ADMIN_ONLY",
            "message": "Pool management is restricted to platform administrators.",
        },
    )


# ── Devops/diagnostics routes — admin-only ──────────────────────────────────

@app.api_route("/v1/devops{path:path}", methods=["GET", "POST"], tags=["Admin"])
async def devops_blocked(request: Request, path: str = ""):
    """Diagnostics endpoints are admin-only."""
    return JSONResponse(
        status_code=status.HTTP_403_FORBIDDEN,
        content={
            "code": "ADMIN_ONLY",
            "message": "Diagnostics endpoints are restricted to platform administrators.",
        },
    )
