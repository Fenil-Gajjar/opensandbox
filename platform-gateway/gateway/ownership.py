"""
Tenant isolation — ownership injection and verification.

CREATE:  Injects metadata.tenant_id = user_id into the request body.
LIST:    Injects metadata query filter tenant_id=user_id.
GET/DELETE/PATCH/PAUSE/RESUME/RENEW/ENDPOINTS/PROXY:
         Verifies sandbox_id belongs to user_id via sandbox_ownership table.
SNAPSHOTS:
         Verifies the parent sandbox belongs to the user.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import status
from fastapi.responses import JSONResponse

from gateway.db import get_pool

logger = logging.getLogger(__name__)

TENANT_KEY = "tenant_id"


async def record_ownership(
    sandbox_id: str,
    user_id: str,
    cpu_vcpu: float,
    memory_gib: float,
) -> None:
    """Insert a row into sandbox_ownership after successful creation."""
    pool = get_pool()
    await pool.execute(
        """
        INSERT INTO sandbox_ownership (sandbox_id, user_id, cpu_vcpu, memory_gib, state, created_at)
        VALUES ($1, $2, $3, $4, 'active', NOW())
        ON CONFLICT (sandbox_id) DO NOTHING
        """,
        sandbox_id,
        user_id,
        cpu_vcpu,
        memory_gib,
    )
    logger.info("Recorded ownership: sandbox=%s user=%s", sandbox_id, user_id)


async def verify_ownership(sandbox_id: str, user_id: str) -> Optional[JSONResponse]:
    """
    Check that *sandbox_id* belongs to *user_id*.

    Returns None on success (ownership confirmed).
    Returns a 403 JSONResponse if the sandbox doesn't belong to this user,
    or a 404 if the sandbox is unknown to the gateway entirely.
    """
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT user_id FROM sandbox_ownership WHERE sandbox_id = $1",
        sandbox_id,
    )
    if row is None:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={
                "code": "NOT_FOUND",
                "message": f"Sandbox '{sandbox_id}' does not exist.",
            },
        )
    if row["user_id"] != user_id:
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={
                "code": "FORBIDDEN",
                "message": "You do not have access to this sandbox.",
            },
        )
    return None  # ownership confirmed


async def verify_snapshot_ownership(snapshot_id: str, user_id: str) -> Optional[JSONResponse]:
    """
    Verify user owns the sandbox that produced this snapshot.

    We need to call the upstream to GET the snapshot first to find the sandboxId,
    then check ownership of that sandbox. For efficiency, we maintain a
    snapshot_ownership cache. If not cached, we return None and let the proxy
    handle it — the upstream will 404 if it doesn't exist.
    """
    pool = get_pool()
    row = await pool.fetchrow(
        """
        SELECT so.user_id
        FROM snapshot_ownership snap
        JOIN sandbox_ownership so ON so.sandbox_id = snap.sandbox_id
        WHERE snap.snapshot_id = $1
        """,
        snapshot_id,
    )
    if row is None:
        # Not in our cache — could be a snapshot we haven't seen yet.
        # Let upstream handle 404. We can't block it here without knowing the sandbox.
        return None
    if row["user_id"] != user_id:
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={
                "code": "FORBIDDEN",
                "message": "You do not have access to this snapshot.",
            },
        )
    return None


async def record_snapshot_ownership(snapshot_id: str, sandbox_id: str) -> None:
    """Cache snapshot→sandbox mapping for ownership checks."""
    pool = get_pool()
    await pool.execute(
        """
        INSERT INTO snapshot_ownership (snapshot_id, sandbox_id, created_at)
        VALUES ($1, $2, NOW())
        ON CONFLICT (snapshot_id) DO NOTHING
        """,
        snapshot_id,
        sandbox_id,
    )


async def mark_sandbox_deleted(sandbox_id: str) -> None:
    """Mark sandbox as deleted in ownership table."""
    pool = get_pool()
    await pool.execute(
        "UPDATE sandbox_ownership SET state = 'deleted' WHERE sandbox_id = $1",
        sandbox_id,
    )


async def mark_sandbox_paused(sandbox_id: str) -> None:
    """Mark sandbox as paused in ownership table."""
    pool = get_pool()
    await pool.execute(
        "UPDATE sandbox_ownership SET state = 'paused' WHERE sandbox_id = $1",
        sandbox_id,
    )


async def mark_sandbox_running(sandbox_id: str) -> None:
    """Mark sandbox as running in ownership table (after resume)."""
    pool = get_pool()
    await pool.execute(
        "UPDATE sandbox_ownership SET state = 'active' WHERE sandbox_id = $1",
        sandbox_id,
    )


async def get_active_sandbox_count(user_id: str) -> int:
    """Count active (non-deleted) sandboxes for a user."""
    pool = get_pool()
    count = await pool.fetchval(
        "SELECT COUNT(*) FROM sandbox_ownership WHERE user_id = $1 AND state = 'active'",
        user_id,
    )
    return count or 0
