"""
Platform-specific API routes — user signup, wallet, usage, API keys.

These endpoints are NOT proxied to OpenSandbox; they're handled entirely
by the gateway and its Postgres database.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from gateway.auth import hash_api_key
from gateway.config import settings
from gateway.db import get_pool

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["Platform"])


# ── Request / Response models ───────────────────────────────────────────────

class SignupRequest(BaseModel):
    email: str = Field(..., description="User email address.")


class SignupResponse(BaseModel):
    user_id: str
    api_key: str = Field(..., description="Your API key. Store it safely — it won't be shown again.")
    balance: str = Field(..., description="Starting balance.")


class TopupRequest(BaseModel):
    amount_cents: int = Field(..., ge=100, description="Amount to add in cents (min $1.00).")


class CreateKeyRequest(BaseModel):
    label: str = Field(default="default", description="Human-readable label for this key.")


# ── Signup ──────────────────────────────────────────────────────────────────

@router.post("/signup", status_code=status.HTTP_201_CREATED)
async def signup(req: SignupRequest) -> SignupResponse:
    """
    Create a new platform user with a wallet and first API key.

    Returns the API key exactly once — user must save it.
    """
    pool = get_pool()
    email = req.email.strip().lower()

    # Check if email already exists
    existing = await pool.fetchval("SELECT id FROM users WHERE email = $1", email)
    if existing:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"code": "EMAIL_EXISTS", "message": "This email is already registered."},
        )

    user_id = f"user_{secrets.token_hex(12)}"
    raw_key = f"osb_{secrets.token_urlsafe(32)}"
    key_hash = hash_api_key(raw_key)

    async with pool.acquire() as conn:
        async with conn.transaction():
            # Create user
            await conn.execute(
                "INSERT INTO users (id, email, created_at) VALUES ($1, $2, NOW())",
                user_id,
                email,
            )
            # Create wallet with free credits
            await conn.execute(
                "INSERT INTO wallets (user_id, balance_cents, updated_at) VALUES ($1, $2, NOW())",
                user_id,
                settings.free_credits_cents,
            )
            # Create first API key
            await conn.execute(
                "INSERT INTO api_keys (key_hash, user_id, label, created_at) VALUES ($1, $2, $3, NOW())",
                key_hash,
                user_id,
                "default",
            )
            # Record the free credit transaction
            await conn.execute(
                """
                INSERT INTO transactions (user_id, type, amount_cents, balance_after, description, created_at)
                VALUES ($1, 'credit', $2, $2, 'Signup bonus', NOW())
                """,
                user_id,
                settings.free_credits_cents,
            )

    logger.info("New user: id=%s email=%s credits=$%.2f", user_id, email, settings.free_credits_cents / 100)

    return SignupResponse(
        user_id=user_id,
        api_key=raw_key,
        balance=f"${settings.free_credits_cents / 100:.2f}",
    )


# ── Wallet ──────────────────────────────────────────────────────────────────

@router.get("/wallet")
async def get_wallet(request: Request):
    """Get current wallet balance and active sandbox burn rate."""
    user_id = request.state.user_id
    pool = get_pool()

    wallet = await pool.fetchrow("SELECT balance_cents, updated_at FROM wallets WHERE user_id = $1", user_id)
    if not wallet:
        return JSONResponse(status_code=404, content={"code": "NOT_FOUND", "message": "Wallet not found."})

    # Calculate current burn rate from running usage records
    running = await pool.fetch(
        "SELECT cpu_vcpu, memory_gib, storage_gib FROM usage_records WHERE user_id = $1 AND state = 'running'",
        user_id,
    )
    burn_per_hour = sum(
        r["cpu_vcpu"] * settings.rate_vcpu_per_second * 3600
        + r["memory_gib"] * settings.rate_memory_per_second * 3600
        + r["storage_gib"] * settings.rate_storage_per_second * 3600
        for r in running
    )

    balance = wallet["balance_cents"] / 100
    hours_remaining = balance / burn_per_hour if burn_per_hour > 0 else None

    return {
        "balance": f"${balance:.2f}",
        "balance_cents": wallet["balance_cents"],
        "burn_rate_per_hour": f"${burn_per_hour:.4f}",
        "active_sandboxes": len(running),
        "hours_remaining": round(hours_remaining, 1) if hours_remaining else None,
    }


@router.post("/wallet/topup")
async def topup_wallet(request: Request, req: TopupRequest):
    """Add funds to wallet (placeholder — integrate Stripe later)."""
    user_id = request.state.user_id
    pool = get_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "UPDATE wallets SET balance_cents = balance_cents + $1, updated_at = NOW() WHERE user_id = $2",
                req.amount_cents,
                user_id,
            )
            balance_after = await conn.fetchval(
                "SELECT balance_cents FROM wallets WHERE user_id = $1", user_id
            )
            await conn.execute(
                """
                INSERT INTO transactions (user_id, type, amount_cents, balance_after, description, created_at)
                VALUES ($1, 'topup', $2, $3, 'Manual top-up', NOW())
                """,
                user_id,
                req.amount_cents,
                balance_after,
            )
            # Clear any grace alert if balance is now positive
            if balance_after > 0:
                await conn.execute("DELETE FROM balance_alerts WHERE user_id = $1", user_id)

    return {"balance_cents": balance_after, "balance": f"${balance_after / 100:.2f}"}


# ── Usage ───────────────────────────────────────────────────────────────────

@router.get("/usage")
async def get_usage(request: Request):
    """Get current month's usage breakdown."""
    user_id = request.state.user_id
    pool = get_pool()

    # Current month totals
    rows = await pool.fetch(
        """
        SELECT sandbox_id, cpu_vcpu, memory_gib, started_at, ended_at, cost_cents, state
        FROM usage_records
        WHERE user_id = $1
          AND started_at >= date_trunc('month', NOW())
        ORDER BY started_at DESC
        """,
        user_id,
    )

    total_cents = sum(r["cost_cents"] for r in rows)
    items = [
        {
            "sandbox_id": r["sandbox_id"],
            "cpu_vcpu": float(r["cpu_vcpu"]),
            "memory_gib": float(r["memory_gib"]),
            "started_at": r["started_at"].isoformat() if r["started_at"] else None,
            "ended_at": r["ended_at"].isoformat() if r["ended_at"] else None,
            "cost": f"${r['cost_cents'] / 100:.4f}",
            "state": r["state"],
        }
        for r in rows
    ]

    return {
        "month": datetime.now(timezone.utc).strftime("%Y-%m"),
        "total_cost": f"${total_cents / 100:.2f}",
        "total_cost_cents": total_cents,
        "records": items,
    }


# ── API Keys ────────────────────────────────────────────────────────────────

@router.get("/keys")
async def list_keys(request: Request):
    """List all API keys for the authenticated user."""
    user_id = request.state.user_id
    pool = get_pool()
    rows = await pool.fetch(
        """
        SELECT key_hash, label, created_at, revoked_at
        FROM api_keys
        WHERE user_id = $1
        ORDER BY created_at DESC
        """,
        user_id,
    )
    return {
        "keys": [
            {
                "key_id": r["key_hash"][:12] + "...",  # partial hash as identifier
                "label": r["label"],
                "created_at": r["created_at"].isoformat(),
                "revoked": r["revoked_at"] is not None,
            }
            for r in rows
        ]
    }


@router.post("/keys", status_code=status.HTTP_201_CREATED)
async def create_key(request: Request, req: CreateKeyRequest):
    """Generate a new API key for the authenticated user."""
    user_id = request.state.user_id
    pool = get_pool()

    raw_key = f"osb_{secrets.token_urlsafe(32)}"
    key_hash = hash_api_key(raw_key)

    await pool.execute(
        "INSERT INTO api_keys (key_hash, user_id, label, created_at) VALUES ($1, $2, $3, NOW())",
        key_hash,
        user_id,
        req.label,
    )

    return {
        "api_key": raw_key,
        "label": req.label,
        "message": "Store this key safely — it won't be shown again.",
    }


@router.delete("/keys/{key_prefix}")
async def revoke_key(request: Request, key_prefix: str):
    """Revoke an API key by its prefix (first 12 chars of hash)."""
    user_id = request.state.user_id
    pool = get_pool()

    # Find the key by prefix
    row = await pool.fetchrow(
        """
        SELECT key_hash FROM api_keys
        WHERE user_id = $1 AND key_hash LIKE $2 AND revoked_at IS NULL
        """,
        user_id,
        f"{key_prefix}%",
    )
    if not row:
        return JSONResponse(status_code=404, content={"code": "NOT_FOUND", "message": "Key not found."})

    # Don't allow revoking the last active key
    active_count = await pool.fetchval(
        "SELECT COUNT(*) FROM api_keys WHERE user_id = $1 AND revoked_at IS NULL",
        user_id,
    )
    if active_count <= 1:
        return JSONResponse(
            status_code=400,
            content={"code": "LAST_KEY", "message": "Cannot revoke your last active API key."},
        )

    await pool.execute(
        "UPDATE api_keys SET revoked_at = NOW() WHERE key_hash = $1",
        row["key_hash"],
    )

    return {"message": "Key revoked.", "key_id": key_prefix + "..."}
