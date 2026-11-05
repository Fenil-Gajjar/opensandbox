# Platform Gateway

Multi-tenant API gateway for OpenSandbox. Sits between users and the OpenSandbox server, providing per-user authentication, tenant isolation, billing, and quota enforcement.

## Architecture

```
Users (SDK/CLI/Agent)
        │
        │  OPEN-SANDBOX-API-KEY: osb_user_xxx
        ▼
┌─────────────────────┐
│   Platform Gateway   │ ← FastAPI (port 9090)
│   ┌───────────────┐ │
│   │ Auth          │ │  hash(key) → lookup user_id
│   │ Ownership     │ │  sandbox_id belongs to user?
│   │ Limits        │ │  16 vCPU, 32Gi max
│   │ Billing       │ │  balance check, metering
│   │ Proxy         │ │  swap key → master key
│   └───────────────┘ │
└─────────┬───────────┘
          │  OPEN-SANDBOX-API-KEY: <master_key>
          ▼
┌─────────────────────┐
│  OpenSandbox Server  │ ← Unmodified (port 8080)
│  (Kubernetes + Kata) │
└─────────────────────┘
```

## Quick Start (Local Development)

```bash
# 1. Start Postgres + Gateway
docker compose up -d

# 2. Create a user
curl -X POST http://localhost:9090/api/signup \
  -H "Content-Type: application/json" \
  -d '{"email": "user@example.com"}'

# Response: {"user_id": "user_xxx", "api_key": "osb_xxx", "balance": "$50.00"}

# 3. Create a sandbox (using the returned API key)
curl -X POST http://localhost:9090/v1/sandboxes \
  -H "OPEN-SANDBOX-API-KEY: osb_xxx" \
  -H "Content-Type: application/json" \
  -d '{
    "image": {"uri": "python:3.12"},
    "entrypoint": ["tail", "-f", "/dev/null"],
    "resourceLimits": {"cpu": "2", "memory": "4Gi"},
    "timeout": 3600
  }'

# 4. List YOUR sandboxes (tenant-filtered automatically)
curl http://localhost:9090/v1/sandboxes \
  -H "OPEN-SANDBOX-API-KEY: osb_xxx"

# 5. Check wallet
curl http://localhost:9090/api/wallet \
  -H "OPEN-SANDBOX-API-KEY: osb_xxx"
```

## Configuration

All settings via environment variables (prefix `GATEWAY_`):

| Variable | Default | Description |
|----------|---------|-------------|
| `GATEWAY_UPSTREAM_URL` | `http://localhost:8080` | OpenSandbox server URL |
| `GATEWAY_MASTER_API_KEY` | `` | OpenSandbox server's API key |
| `GATEWAY_DATABASE_URL` | `postgresql://gateway:gateway@localhost:5432/gateway` | Postgres DSN |
| `GATEWAY_PORT` | `9090` | Gateway listen port |
| `GATEWAY_FREE_CREDITS` | `5000` | Signup credits in cents ($50) |
| `GATEWAY_MAX_CPU_PER_SANDBOX` | `16` | Max vCPU per sandbox |
| `GATEWAY_MAX_MEMORY_GIB_PER_SANDBOX` | `32` | Max GiB RAM per sandbox |
| `GATEWAY_MAX_CONCURRENT_SANDBOXES` | `50` | Max concurrent sandboxes per user |
| `GATEWAY_GRACE_PERIOD_SECONDS` | `900` | Grace period before killing sandboxes at $0 |
| `GATEWAY_METERING_INTERVAL_SECONDS` | `30` | How often the metering job runs |

## API Routes

### User-facing (proxied to OpenSandbox with tenant isolation):

| Method | Path | What it does |
|--------|------|-------------|
| POST | `/v1/sandboxes` | Create sandbox (+ limits + billing + tenant_id injection) |
| GET | `/v1/sandboxes` | List sandboxes (auto-filtered to user's own) |
| GET | `/v1/sandboxes/{id}` | Get sandbox (ownership verified) |
| PATCH | `/v1/sandboxes/{id}/metadata` | Patch metadata (tenant_id protected) |
| DELETE | `/v1/sandboxes/{id}` | Delete sandbox (+ finalize billing) |
| POST | `/v1/sandboxes/{id}/pause` | Pause (+ stop billing) |
| POST | `/v1/sandboxes/{id}/resume` | Resume (+ balance check + restart billing) |
| POST | `/v1/sandboxes/{id}/renew-expiration` | Renew expiration |
| GET | `/v1/sandboxes/{id}/endpoints/{port}` | Get endpoint URL |
| * | `/v1/sandboxes/{id}/proxy/{port}/**` | HTTP proxy to sandbox |
| WS | `/v1/sandboxes/{id}/proxy/{port}/**` | WebSocket proxy to sandbox |
| POST | `/v1/sandboxes/{id}/snapshots` | Create snapshot |
| GET | `/v1/snapshots` | List snapshots |
| GET | `/v1/snapshots/{id}` | Get snapshot |
| DELETE | `/v1/snapshots/{id}` | Delete snapshot |

### Platform-specific (handled by gateway):

| Method | Path | What it does |
|--------|------|-------------|
| POST | `/api/signup` | Create user + wallet + API key |
| GET | `/api/wallet` | Balance + burn rate |
| POST | `/api/wallet/topup` | Add funds |
| GET | `/api/usage` | Monthly usage breakdown |
| GET | `/api/keys` | List API keys |
| POST | `/api/keys` | Generate new key |
| DELETE | `/api/keys/{prefix}` | Revoke key |

### Admin:

| Method | Path | What it does |
|--------|------|-------------|
| GET | `/admin/users` | List all users |
| POST | `/admin/refund` | Issue refund |
| GET | `/admin/stats` | Platform stats |

## Database Schema

8 tables: `users`, `api_keys`, `wallets`, `sandbox_ownership`, `snapshot_ownership`, `usage_records`, `transactions`, `balance_alerts`

Schema auto-applied on first `docker compose up` via `schema.sql`.

## Billing

- **Per-second metering** at Daytona-equivalent rates
- **Background job** every 30s calculates running costs
- **Grace period** (15 min) before killing sandboxes at $0 balance
- **Atomic balance checks** via `SELECT FOR UPDATE` (prevents race conditions)
- All money stored as **integer cents** (no floating-point drift)

## Production Deployment

See the Helm chart in `deploy/` for Kubernetes deployment alongside the OpenSandbox server.
