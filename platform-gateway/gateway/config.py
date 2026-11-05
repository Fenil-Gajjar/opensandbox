"""
Gateway configuration loaded from environment variables.

Every setting has a sensible default for local development.
In production, values come from K8s ConfigMap / Secrets.
"""

from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """All gateway configuration — sourced from env vars prefixed GATEWAY_."""

    # ── Upstream OpenSandbox server ──────────────────────────────────────
    upstream_url: str = Field(
        default="http://localhost:8080",
        description="Base URL of the OpenSandbox server (no trailing slash, no /v1).",
    )
    master_api_key: str = Field(
        default="",
        description=(
            "The single API key configured on the OpenSandbox server. "
            "The gateway swaps every user key for this master key before forwarding."
        ),
    )

    # ── Database ────────────────────────────────────────────────────────
    database_url: str = Field(
        default="postgresql://gateway:gateway@localhost:5432/gateway",
        description="asyncpg-compatible Postgres DSN.",
    )
    db_pool_min: int = Field(default=5, description="Minimum DB connections.")
    db_pool_max: int = Field(default=20, description="Maximum DB connections.")

    # ── Server ──────────────────────────────────────────────────────────
    host: str = Field(default="0.0.0.0", description="Bind address.")
    port: int = Field(default=9090, description="Bind port.")
    log_level: str = Field(default="info", description="Logging level.")

    # ── Billing ─────────────────────────────────────────────────────────
    free_credits_cents: int = Field(
        default=5000,
        description="Credits granted on signup, in cents ($50.00 = 5000).",
    )
    rate_vcpu_per_second: float = Field(
        default=0.00001400,
        description="USD per vCPU-second.",
    )
    rate_memory_per_second: float = Field(
        default=0.00000450,
        description="USD per GiB-second of RAM.",
    )
    rate_storage_per_second: float = Field(
        default=0.00000003,
        description="USD per GiB-second of disk.",
    )
    grace_period_seconds: int = Field(
        default=900,
        description="Seconds to wait after balance hits $0 before killing sandboxes.",
    )
    metering_interval_seconds: int = Field(
        default=30,
        description="How often the metering background job runs.",
    )

    # ── Resource hard caps ──────────────────────────────────────────────
    # Tuned for D32s_v5 nodes (31 usable vCPU, 123 GiB).
    # 8 vCPU max → fits 3+ sandboxes per node (good density).
    # 16 GiB RAM max → proportional to CPU (2 GiB per vCPU).
    # 50 GiB ephemeral storage max → node root disk is typically 128 GiB.
    max_cpu_per_sandbox: int = Field(default=8, description="Max vCPU per sandbox.")
    max_memory_gib_per_sandbox: int = Field(default=16, description="Max GiB RAM per sandbox.")
    max_storage_gib_per_sandbox: int = Field(default=50, description="Max GiB ephemeral storage per sandbox.")
    max_timeout_seconds: int = Field(default=604800, description="Max sandbox timeout (7 days).")
    max_concurrent_sandboxes: int = Field(default=50, description="Max concurrent sandboxes per user.")
    rate_limit_creates_per_minute: int = Field(default=10, description="Max sandbox creates per minute per user.")

    model_config = {"env_prefix": "GATEWAY_"}


# Module-level singleton — import from here everywhere.
settings = Settings()
