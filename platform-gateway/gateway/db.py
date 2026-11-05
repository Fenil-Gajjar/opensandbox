"""
Async Postgres connection pool (asyncpg).

Usage:
    from gateway.db import get_pool
    pool = get_pool()
    row = await pool.fetchrow("SELECT 1")
"""

from __future__ import annotations

import logging
from typing import Optional

import asyncpg

from gateway.config import settings

logger = logging.getLogger(__name__)

_pool: Optional[asyncpg.Pool] = None


async def init_pool() -> asyncpg.Pool:
    """Create the connection pool.  Called once during app startup."""
    global _pool
    if _pool is not None:
        return _pool

    logger.info(
        "Connecting to Postgres: %s (pool %d–%d)",
        settings.database_url.split("@")[-1],  # log host only, not credentials
        settings.db_pool_min,
        settings.db_pool_max,
    )
    _pool = await asyncpg.create_pool(
        dsn=settings.database_url,
        min_size=settings.db_pool_min,
        max_size=settings.db_pool_max,
    )
    logger.info("Postgres pool ready.")
    return _pool


def get_pool() -> asyncpg.Pool:
    """Return the live pool.  Raises if init_pool() hasn't been called."""
    if _pool is None:
        raise RuntimeError("Database pool not initialized — call init_pool() first.")
    return _pool


async def close_pool() -> None:
    """Drain and close the pool.  Called during app shutdown."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("Postgres pool closed.")
