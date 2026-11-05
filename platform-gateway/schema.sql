-- Platform Gateway — Initial Database Schema
-- Creates all tables for multi-tenant isolation, billing, and API key management.


-- Users
CREATE TABLE IF NOT EXISTS users (
    id          TEXT PRIMARY KEY,
    email       TEXT UNIQUE NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- API Keys (SHA-256 hashed)
CREATE TABLE IF NOT EXISTS api_keys (
    key_hash    TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id),
    label       TEXT NOT NULL DEFAULT 'default',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    revoked_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_api_keys_user ON api_keys(user_id);

-- Wallets (balance in cents to avoid float drift)
CREATE TABLE IF NOT EXISTS wallets (
    user_id         TEXT PRIMARY KEY REFERENCES users(id),
    balance_cents   BIGINT NOT NULL DEFAULT 0,
    auto_topup      BOOLEAN NOT NULL DEFAULT FALSE,
    topup_threshold BIGINT NOT NULL DEFAULT 500,
    topup_amount    BIGINT NOT NULL DEFAULT 5000,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Sandbox ownership (maps sandbox_id → user_id)
CREATE TABLE IF NOT EXISTS sandbox_ownership (
    sandbox_id  TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id),
    cpu_vcpu    REAL NOT NULL DEFAULT 1.0,
    memory_gib  REAL NOT NULL DEFAULT 1.0,
    state       TEXT NOT NULL DEFAULT 'active',  -- active, paused, deleted
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_sandbox_ownership_user ON sandbox_ownership(user_id, state);

-- Snapshot ownership (maps snapshot_id → sandbox_id for ownership checks)
CREATE TABLE IF NOT EXISTS snapshot_ownership (
    snapshot_id TEXT PRIMARY KEY,
    sandbox_id  TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Usage records (per-sandbox billing records)
CREATE TABLE IF NOT EXISTS usage_records (
    id          BIGSERIAL PRIMARY KEY,
    sandbox_id  TEXT NOT NULL,
    user_id     TEXT NOT NULL REFERENCES users(id),
    cpu_vcpu    REAL NOT NULL DEFAULT 1.0,
    memory_gib  REAL NOT NULL DEFAULT 1.0,
    storage_gib REAL NOT NULL DEFAULT 0.0,
    started_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at    TIMESTAMPTZ,
    cost_cents  BIGINT NOT NULL DEFAULT 0,
    state       TEXT NOT NULL DEFAULT 'running'  -- running, billed, paused_storage
);
CREATE INDEX IF NOT EXISTS idx_usage_records_user ON usage_records(user_id) WHERE state = 'running';
CREATE INDEX IF NOT EXISTS idx_usage_records_sandbox ON usage_records(sandbox_id);
-- Partial unique index: only one 'running' record per sandbox at a time
CREATE UNIQUE INDEX IF NOT EXISTS idx_usage_records_running
    ON usage_records(sandbox_id, state) WHERE state = 'running';

-- Transactions (audit log of all wallet changes)
CREATE TABLE IF NOT EXISTS transactions (
    id              BIGSERIAL PRIMARY KEY,
    user_id         TEXT NOT NULL REFERENCES users(id),
    type            TEXT NOT NULL,  -- credit, debit, topup, refund
    amount_cents    BIGINT NOT NULL,
    balance_after   BIGINT NOT NULL,
    description     TEXT,
    sandbox_id      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_transactions_user ON transactions(user_id, created_at DESC);

-- Balance alerts (tracks grace period for $0 balance users)
CREATE TABLE IF NOT EXISTS balance_alerts (
    user_id         TEXT PRIMARY KEY REFERENCES users(id),
    grace_started_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
