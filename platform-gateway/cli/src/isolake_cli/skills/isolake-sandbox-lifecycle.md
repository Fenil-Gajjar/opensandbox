---
name: sandbox-lifecycle
description: Use Isolake CLI lifecycle commands to create, inspect, verify, renew, pause, resume, expose, and terminate sandboxes. Trigger when users want help provisioning a sandbox, choosing create flags, checking runtime state, retrieving endpoints, or safely cleaning up sandboxes.
---

# Isolake Sandbox Lifecycle

Use Isolake lifecycle commands directly instead of giving generic container advice. Prefer a verified lifecycle flow over isolated commands.

## When To Use

- the user wants to create a sandbox for a task or workflow
- the user needs to inspect sandbox state or health
- the user wants to expose a service port through an Isolake endpoint
- the user wants to renew, pause, resume, or terminate a sandbox
- the user is unsure which create flags or file-based inputs to use

## Configuration Resolution

Before giving lifecycle commands, resolve the active Isolake connection configuration:

```bash
isolake config show -o json
```

Check the resolved values for `domain`, `api_key`, `protocol`.

If `domain` is missing, stop and set it before lifecycle commands:

```bash
isolake config set connection.domain <host:port> -o json
isolake config show -o json
```

## Golden Path

```bash
isolake config show -o json
isolake sandbox create --image python:3.12 --timeout 30m -o json
isolake sandbox get <sandbox-id> -o json
isolake sandbox health <sandbox-id> -o json
```

If the sandbox serves traffic on a known port:

```bash
isolake sandbox endpoint <sandbox-id> --port 8080 -o json
```

## Create Options

```bash
isolake sandbox create --image python:3.12 -o json
isolake sandbox create --catalog chrome-browser -o json
isolake sandbox create --image node:20 --timeout 30m -o json
isolake sandbox create --image python:3.12 --timeout none -o json
isolake sandbox create --image python:3.12 --entrypoint python --entrypoint -m --entrypoint http.server -o json
isolake sandbox create --image python:3.12 --network-policy-file network-policy.json -o json
isolake sandbox create --image python:3.12 --volumes-file volumes.json -o json
```

- `--image`: required unless using `--catalog` or `defaults.image`
- `--catalog`: prebuilt sandbox flavor from catalog
- `--timeout`: recommended for temporary workloads; `none` for manual cleanup
- `--entrypoint`: repeat per argv item
- `--skip-health-check`: only when explicitly needed

## JSON Shapes

Example `network-policy.json`:

```json
{
  "defaultAction": "deny",
  "egress": [
    {"action": "allow", "target": "pypi.org"},
    {"action": "allow", "target": "files.pythonhosted.org"}
  ]
}
```

Example `volumes-pvc.json`:

```json
[
  {
    "name": "shared-models",
    "pvc": {"claimName": "shared-models-pvc"},
    "mountPath": "/workspace/models",
    "readOnly": true
  }
]
```

## Verification

```bash
isolake sandbox get <sandbox-id> -o json
isolake sandbox health <sandbox-id> -o json
isolake sandbox metrics <sandbox-id> -o json
isolake sandbox endpoint <sandbox-id> --port 8080 -o json
```

## Lifecycle Actions

```bash
isolake sandbox list -o json
isolake sandbox list --state running --state paused -o json
isolake sandbox renew <sandbox-id> --timeout 30m -o json
isolake sandbox pause <sandbox-id> -o json
isolake sandbox resume <sandbox-id> -o json
isolake sandbox kill <sandbox-id> -o json
```

## Minimal Closed Loops

Create and verify:

```bash
isolake sandbox create --image python:3.12 --timeout 30m -o json
isolake sandbox get <sandbox-id> -o json
isolake sandbox health <sandbox-id> -o json
```

Create from catalog:

```bash
isolake catalog list -o json
isolake sandbox create --catalog chrome-browser -o json
isolake sandbox endpoint <sandbox-id> --port 6080 -o json
```

Pause and resume:

```bash
isolake sandbox pause <sandbox-id> -o json
isolake sandbox get <sandbox-id> -o json
isolake sandbox resume <sandbox-id> -o json
isolake sandbox health <sandbox-id> -o json
```

## Response Pattern

1. exact command to run
2. what state change or verification result to expect
3. the next lifecycle command if the workflow continues

## Best Practices

- resolve config before assuming which server the command will hit
- prefer `health` over assuming readiness from `create` output alone
- prefer `endpoint` over guessing host/port mappings
- prefer `--catalog` for prebuilt environments
- switch to `sandbox-troubleshooting` if creation fails instead of adding more flags
