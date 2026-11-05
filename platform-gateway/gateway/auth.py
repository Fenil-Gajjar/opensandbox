"""
Per-user API key authentication middleware.

Flow:
  1. Extract OPEN-SANDBOX-API-KEY header
  2. SHA-256 hash it
  3. Look up hash in api_keys table → get user_id
  4. Attach user_id to request.state for downstream handlers
  5. Exempt paths: /health, /api/signup, /docs, /openapi.json
"""

from __future__ import annotations

import hashlib
import logging
from typing import Optional

from fastapi import Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from gateway.db import get_pool

logger = logging.getLogger(__name__)

# Same header name the OpenSandbox server uses.
API_KEY_HEADER = "OPEN-SANDBOX-API-KEY"

# Paths that do NOT require authentication.
EXEMPT_PREFIXES = (
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/api/signup",
    "/api/catalog",
)


def hash_api_key(raw_key: str) -> str:
    """Deterministic SHA-256 hex digest used for DB storage / lookup."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


class GatewayAuthMiddleware(BaseHTTPMiddleware):
    """
    Validates per-user API keys against the gateway's Postgres database.

    On success, ``request.state.user_id`` is set for downstream handlers.
    The raw key is kept in ``request.state.raw_api_key`` so the proxy layer
    can swap it with the master key before forwarding.
    """

    async def dispatch(self, request: Request, call_next):
        # ── Skip exempt paths ───────────────────────────────────────────
        path = request.url.path
        if any(path.startswith(p) for p in EXEMPT_PREFIXES):
            return await call_next(request)

        # ── Extract key ─────────────────────────────────────────────────
        raw_key: Optional[str] = request.headers.get(API_KEY_HEADER)
        if not raw_key:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={
                    "code": "MISSING_API_KEY",
                    "message": (
                        "Authentication credentials are missing. "
                        f"Provide your API key via the {API_KEY_HEADER} header."
                    ),
                },
            )

        # ── Lookup ──────────────────────────────────────────────────────
        key_hash = hash_api_key(raw_key)
        pool = get_pool()
        row = await pool.fetchrow(
            """
            SELECT ak.user_id
            FROM api_keys ak
            WHERE ak.key_hash = $1
              AND ak.revoked_at IS NULL
            """,
            key_hash,
        )

        if row is None:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={
                    "code": "INVALID_API_KEY",
                    "message": "Authentication credentials are invalid. Check your API key and try again.",
                },
            )

        # ── Attach identity to request ──────────────────────────────────
        request.state.user_id = row["user_id"]
        request.state.raw_api_key = raw_key

        response = await call_next(request)
        return response
