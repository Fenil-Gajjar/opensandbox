"""
Billing engine — wallet checks, usage metering, balance enforcement.

All monetary values are stored as INTEGER CENTS to avoid floating-point drift.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import status
from fastapi.responses import JSONResponse

from gateway.config import settings
from gateway.db import get_pool

logger = logging.getLogger(__name__)


# ─── Wallet checks ──────────────────────────────────────────────────────────

async def check_balance_for_creation(
    user_id: str,
    cpu_vcpu: float,
    memory_gib: float,
) -> Optional[JSONResponse]:
    """
    Ensure user has at least 1 hour of the requested resource cost in their wallet.

    Uses SELECT FOR UPDATE to prevent race conditions when multiple agents
    create sandboxes simultaneously.

    Returns None if OK, or a 403 JSONResponse.
    """
    pool = get_pool()

    # Calculate minimum required balance = 1 hour of this sandbox
    hourly_cost = cpu_vcpu * settings.rate_vcpu_per_second * 3600 + \
                  memory_gib * settings.rate_memory_per_second * 3600
    min_cents = max(int(hourly_cost * 100), 100)  # At least $1.00

    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT balance_cents FROM wallets WHERE user_id = $1 FOR UPDATE",
                user_id,
            )
            if row is None:
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={
                        "code": "NO_WALLET",
                        "message": "No wallet found. Contact support.",
                    },
                )

            if row["balance_cents"] < min_cents:
                needed = min_cents / 100
                current = row["balance_cents"] / 100
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={
                        "code": "INSUFFICIENT_BALANCE",
                        "message": (
                            f"Insufficient balance. Need at least ${needed:.2f} "
                            f"(1 hour of {cpu_vcpu} vCPU + {memory_gib:.1f} GiB). "
                            f"Current balance: ${current:.2f}. Add funds to continue."
                        ),
                    },
                )

    return None  # balance OK


# ─── Usage record lifecycle ────────────────────────────────────────────────

async def start_usage_record(
    sandbox_id: str,
    user_id: str,
    cpu_vcpu: float,
    memory_gib: float,
) -> None:
    """Begin billing for a running sandbox."""
    pool = get_pool()
    await pool.execute(
        """
        INSERT INTO usage_records
            (sandbox_id, user_id, cpu_vcpu, memory_gib, storage_gib, started_at, state)
        VALUES ($1, $2, $3, $4, 0, NOW(), 'running')
        ON CONFLICT DO NOTHING
        """,
        sandbox_id,
        user_id,
        cpu_vcpu,
        memory_gib,
    )
    logger.info("Billing started: sandbox=%s user=%s cpu=%.1f mem=%.1fGi", sandbox_id, user_id, cpu_vcpu, memory_gib)


async def finalize_usage_record(sandbox_id: str) -> None:
    """
    Stop billing for a sandbox (deleted or stopped due to balance).

    Calculates final cost, deducts from wallet, logs transaction.
    """
    pool = get_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT id, user_id, cpu_vcpu, memory_gib, storage_gib, started_at
                FROM usage_records
                WHERE sandbox_id = $1 AND state = 'running'
                FOR UPDATE
                """,
                sandbox_id,
            )
            if row is None:
                return  # no active billing record

            now = datetime.now(timezone.utc)
            started = row["started_at"]
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            elapsed = (now - started).total_seconds()

            cost_usd = (
                row["cpu_vcpu"] * settings.rate_vcpu_per_second * elapsed
                + row["memory_gib"] * settings.rate_memory_per_second * elapsed
                + row["storage_gib"] * settings.rate_storage_per_second * elapsed
            )
            cost_cents = int(cost_usd * 100)

            # Finalize the usage record
            await conn.execute(
                """
                UPDATE usage_records
                SET state = 'billed', ended_at = NOW(), cost_cents = $1
                WHERE id = $2
                """,
                cost_cents,
                row["id"],
            )

            # Deduct from wallet
            await conn.execute(
                "UPDATE wallets SET balance_cents = balance_cents - $1, updated_at = NOW() WHERE user_id = $2",
                cost_cents,
                row["user_id"],
            )

            # Record transaction
            balance_after = await conn.fetchval(
                "SELECT balance_cents FROM wallets WHERE user_id = $1",
                row["user_id"],
            )
            await conn.execute(
                """
                INSERT INTO transactions
                    (user_id, type, amount_cents, balance_after, description, sandbox_id, created_at)
                VALUES ($1, 'debit', $2, $3, $4, $5, NOW())
                """,
                row["user_id"],
                -cost_cents,
                balance_after,
                f"Sandbox {sandbox_id}: {elapsed:.0f}s ({row['cpu_vcpu']} vCPU, {row['memory_gib']:.1f}Gi)",
                sandbox_id,
            )

            logger.info(
                "Billing finalized: sandbox=%s cost=$%.4f elapsed=%.0fs",
                sandbox_id, cost_usd, elapsed,
            )


async def pause_billing(sandbox_id: str) -> None:
    """Finalize compute billing when a sandbox is paused."""
    await finalize_usage_record(sandbox_id)
    logger.info("Billing paused: sandbox=%s", sandbox_id)


async def resume_billing(sandbox_id: str, user_id: str, cpu_vcpu: float, memory_gib: float) -> None:
    """Restart billing when a sandbox resumes."""
    await start_usage_record(sandbox_id, user_id, cpu_vcpu, memory_gib)
    logger.info("Billing resumed: sandbox=%s", sandbox_id)


# ─── Background metering job ───────────────────────────────────────────────

async def _meter_once() -> None:
    """Single metering pass — update running costs and check balances."""
    pool = get_pool()

    # Get all running usage records with wallet balance
    rows = await pool.fetch(
        """
        SELECT ur.id, ur.sandbox_id, ur.user_id, ur.cpu_vcpu, ur.memory_gib,
               ur.storage_gib, ur.started_at, w.balance_cents
        FROM usage_records ur
        JOIN wallets w ON w.user_id = ur.user_id
        WHERE ur.state = 'running'
        """
    )

    now = datetime.now(timezone.utc)
    users_at_zero: set[str] = set()

    for row in rows:
        started = row["started_at"]
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        elapsed = (now - started).total_seconds()

        cost_usd = (
            row["cpu_vcpu"] * settings.rate_vcpu_per_second * elapsed
            + row["memory_gib"] * settings.rate_memory_per_second * elapsed
            + row["storage_gib"] * settings.rate_storage_per_second * elapsed
        )
        cost_cents = int(cost_usd * 100)

        # Update running cost for display purposes
        await pool.execute(
            "UPDATE usage_records SET cost_cents = $1 WHERE id = $2",
            cost_cents,
            row["id"],
        )

        # Check if user is at zero
        if row["balance_cents"] - cost_cents <= 0:
            users_at_zero.add(row["user_id"])

    # Handle users at zero balance
    for user_id in users_at_zero:
        await _handle_insufficient_balance(user_id)


async def _handle_insufficient_balance(user_id: str) -> None:
    """Handle a user whose balance has reached $0."""
    pool = get_pool()

    # Check if grace period already started
    grace = await pool.fetchval(
        "SELECT grace_started_at FROM balance_alerts WHERE user_id = $1",
        user_id,
    )

    now = datetime.now(timezone.utc)

    if grace is None:
        # Start grace period
        await pool.execute(
            """
            INSERT INTO balance_alerts (user_id, grace_started_at)
            VALUES ($1, NOW())
            ON CONFLICT (user_id) DO NOTHING
            """,
            user_id,
        )
        logger.warning("Balance alert: user=%s — grace period started (%ds)", user_id, settings.grace_period_seconds)
        return

    # Check if grace period has expired
    if grace.tzinfo is None:
        grace = grace.replace(tzinfo=timezone.utc)
    elapsed = (now - grace).total_seconds()

    if elapsed < settings.grace_period_seconds:
        return  # still in grace period

    # Grace period expired — stop all sandboxes for this user
    logger.warning("Grace period expired for user=%s — stopping all sandboxes", user_id)

    sandboxes = await pool.fetch(
        "SELECT sandbox_id FROM sandbox_ownership WHERE user_id = $1 AND state = 'active'",
        user_id,
    )

    # Import here to avoid circular dependency
    from gateway.proxy_http import forward_to_upstream

    for sbx in sandboxes:
        sid = sbx["sandbox_id"]
        try:
            await forward_to_upstream("DELETE", f"/v1/sandboxes/{sid}", body=None, query_string="")
            await finalize_usage_record(sid)
            await pool.execute(
                "UPDATE sandbox_ownership SET state = 'deleted' WHERE sandbox_id = $1",
                sid,
            )
            logger.info("Auto-stopped sandbox=%s for user=%s (balance exhausted)", sid, user_id)
        except Exception:
            logger.exception("Failed to auto-stop sandbox=%s", sid)

    # Clear the grace alert
    await pool.execute("DELETE FROM balance_alerts WHERE user_id = $1", user_id)


async def _clear_grace_if_balance_ok(user_id: str) -> None:
    """Remove grace alert if user has topped up."""
    pool = get_pool()
    balance = await pool.fetchval(
        "SELECT balance_cents FROM wallets WHERE user_id = $1",
        user_id,
    )
    if balance is not None and balance > 0:
        await pool.execute("DELETE FROM balance_alerts WHERE user_id = $1", user_id)


async def run_metering_loop() -> None:
    """Background task — runs metering every N seconds until cancelled."""
    logger.info("Metering loop started (interval=%ds)", settings.metering_interval_seconds)
    while True:
        try:
            await _meter_once()
        except asyncio.CancelledError:
            logger.info("Metering loop cancelled.")
            break
        except Exception:
            logger.exception("Metering loop error (will retry)")
        await asyncio.sleep(settings.metering_interval_seconds)
