---
name: sandbox-troubleshooting
description: Use Isolake CLI state, health, and stable diagnostics logs/events to investigate failed, unhealthy, or unreachable sandboxes. Trigger when users report startup failures, crashes, OOM, image pull problems, pending sandboxes, network issues, or an unresponsive sandbox and want root cause plus next actions.
---

# Isolake Sandbox Troubleshooting

Investigate the reported sandbox before proposing a fix. Prefer evidence from Isolake state, health checks, and stable diagnostics streams over speculation.

## Inputs To Collect

- sandbox ID
- whether `isolake` CLI is available
- reported symptom: pending forever, crash, OOM, unreachable service, bad image, failed exec, etc.

## Triage Order

```bash
isolake sandbox get <sandbox-id> -o json
isolake sandbox health <sandbox-id> -o json
isolake diagnostics events <sandbox-id> --scope lifecycle -o raw
isolake diagnostics events <sandbox-id> --scope runtime -o raw
isolake diagnostics logs <sandbox-id> --scope container -o raw
```

Then drill down:

```bash
isolake diagnostics logs <sandbox-id> --scope lifecycle -o raw
isolake diagnostics events <sandbox-id> --scope all -o raw
isolake diagnostics logs <sandbox-id> --scope all -o raw
```

## Diagnostics Streams

- `diagnostics events --scope lifecycle`: sandbox actions (CREATE, RENEW, DELETE, PAUSE, RESUME)
- `diagnostics events --scope runtime`: scheduler/container events (Scheduled, Pulling, Pulled, Created, Started, ContainerDied)
- `diagnostics logs --scope lifecycle`: manager server logs
- `diagnostics logs --scope container`: sandbox stdout, application errors, startup hangs
- `--scope` is required; `-o raw` prints inline text
- `truncated: true` means incomplete evidence

## Symptom To Command Mapping

| Symptom | First command |
| --- | --- |
| pending forever | `isolake diagnostics events <sandbox-id> --scope runtime -o raw` |
| image pull failure | `isolake diagnostics events <sandbox-id> --scope runtime -o raw` |
| crash loop | `isolake diagnostics logs <sandbox-id> --scope container -o raw` |
| suspected OOM | `isolake diagnostics events <sandbox-id> --scope runtime -o raw` |
| endpoint unreachable | `isolake sandbox health <sandbox-id> -o json` then `isolake sandbox endpoint <sandbox-id> --port <port> -o json` |
| outbound network failure | `isolake sandbox health <sandbox-id> -o json` then switch to `network-egress` |

## Diagnosis Playbooks

### Image Pull Failure
- evidence: events show `ImagePullBackOff`, `ErrImagePull`
- cause: bad image reference or missing registry credentials
- fix: verify image URI, fix auth, recreate

### OOM Kill
- evidence: runtime events mention killed due to memory
- cause: memory limit too low
- fix: increase memory, rerun

### Crash Loop / Bad Entrypoint
- evidence: logs show startup exceptions, missing binaries
- cause: bad entrypoint or application crash
- fix: fix command/image, correct permissions, recreate

### Endpoint Unreachable
- evidence: sandbox Running but requests fail
- cause: wrong port, service not listening
- fix: verify port, inspect service logs

## Minimal Closed Loops

Full triage:

```bash
isolake sandbox get <sandbox-id> -o json
isolake sandbox health <sandbox-id> -o json
isolake diagnostics events <sandbox-id> --scope lifecycle -o raw
isolake diagnostics events <sandbox-id> --scope runtime -o raw
isolake diagnostics logs <sandbox-id> --scope container -o raw
```

Crash investigation:

```bash
isolake diagnostics logs <sandbox-id> --scope container -o raw
isolake diagnostics events <sandbox-id> --scope runtime -o raw
```

Endpoint troubleshooting:

```bash
isolake sandbox get <sandbox-id> -o json
isolake sandbox health <sandbox-id> -o json
isolake sandbox endpoint <sandbox-id> --port <port> -o json
isolake diagnostics logs <sandbox-id> --scope container -o raw
```

## Response Format

1. current state: what the sandbox is doing now
2. evidence: the command output that matters
3. root cause: most likely diagnosis
4. next actions: specific fixes or follow-up checks

## Operating Rules

- start with highest-signal commands: state, health, then diagnostics
- distinguish sandbox/runtime failures from workload/application failures
- do not paper over readiness problems with `--skip-health-check`
- end with likely root cause and 1-3 concrete remediation steps
