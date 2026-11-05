"""
Admin routes — platform operator endpoints.

These are protected by a separate admin auth mechanism (for now, a simple
admin API key check). Not exposed to regular users.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from gateway.db import get_pool

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["Admin"])

# Simple admin auth — check for X-ADMIN-KEY header
ADMIN_KEY_HEADER = "X-ADMIN-KEY"


def _check_admin(request: Request) -> Optional[JSONResponse]:
    """Verify admin access. Returns error response or None."""
    # In production, use a proper admin auth mechanism.
    # For now, check a simple header.
    admin_key = request.headers.get(ADMIN_KEY_HEADER)
    if not admin_key:
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={"code": "ADMIN_REQUIRED", "message": "Admin authentication required."},
        )
    # TODO: Validate admin_key against configured admin keys
    return None


class RefundRequest(BaseModel):
    user_id: str = Field(..., description="User to refund.")
    amount_cents: int = Field(..., ge=1, description="Amount in cents.")
    reason: str = Field(default="Admin refund", description="Reason for refund.")


@router.get("/users")
async def list_users(request: Request):
    """List all platform users with wallet balances."""
    err = _check_admin(request)
    if err:
        return err

    pool = get_pool()
    rows = await pool.fetch(
        """
        SELECT u.id, u.email, u.created_at,
               COALESCE(w.balance_cents, 0) AS balance_cents,
               (SELECT COUNT(*) FROM sandbox_ownership so WHERE so.user_id = u.id AND so.state = 'active') AS active_sandboxes
        FROM users u
        LEFT JOIN wallets w ON w.user_id = u.id
        ORDER BY u.created_at DESC
        LIMIT 100
        """
    )
    return {
        "users": [
            {
                "id": r["id"],
                "email": r["email"],
                "created_at": r["created_at"].isoformat(),
                "balance": f"${r['balance_cents'] / 100:.2f}",
                "active_sandboxes": r["active_sandboxes"],
            }
            for r in rows
        ]
    }


@router.post("/refund")
async def issue_refund(request: Request, req: RefundRequest):
    """Issue a refund to a user's wallet."""
    err = _check_admin(request)
    if err:
        return err

    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Verify user exists
            exists = await conn.fetchval("SELECT 1 FROM users WHERE id = $1", req.user_id)
            if not exists:
                return JSONResponse(status_code=404, content={"code": "NOT_FOUND", "message": "User not found."})

            await conn.execute(
                "UPDATE wallets SET balance_cents = balance_cents + $1, updated_at = NOW() WHERE user_id = $2",
                req.amount_cents,
                req.user_id,
            )
            balance_after = await conn.fetchval(
                "SELECT balance_cents FROM wallets WHERE user_id = $1", req.user_id
            )
            await conn.execute(
                """
                INSERT INTO transactions (user_id, type, amount_cents, balance_after, description, created_at)
                VALUES ($1, 'refund', $2, $3, $4, NOW())
                """,
                req.user_id,
                req.amount_cents,
                balance_after,
                f"Admin refund: {req.reason}",
            )
            # Clear grace alert if balance is now positive
            if balance_after > 0:
                await conn.execute("DELETE FROM balance_alerts WHERE user_id = $1", req.user_id)

    logger.info("Refund issued: user=%s amount=$%.2f reason=%s", req.user_id, req.amount_cents / 100, req.reason)
    return {"balance_cents": balance_after, "balance": f"${balance_after / 100:.2f}"}


@router.get("/stats")
async def platform_stats(request: Request):
    """Platform-wide statistics."""
    err = _check_admin(request)
    if err:
        return err

    pool = get_pool()
    total_users = await pool.fetchval("SELECT COUNT(*) FROM users")
    total_active_sandboxes = await pool.fetchval(
        "SELECT COUNT(*) FROM sandbox_ownership WHERE state = 'active'"
    )
    total_revenue_cents = await pool.fetchval(
        "SELECT COALESCE(SUM(ABS(amount_cents)), 0) FROM transactions WHERE type = 'debit'"
    )
    total_balance_cents = await pool.fetchval(
        "SELECT COALESCE(SUM(balance_cents), 0) FROM wallets"
    )

    return {
        "total_users": total_users,
        "active_sandboxes": total_active_sandboxes,
        "total_revenue": f"${total_revenue_cents / 100:.2f}",
        "total_outstanding_balance": f"${total_balance_cents / 100:.2f}",
    }
