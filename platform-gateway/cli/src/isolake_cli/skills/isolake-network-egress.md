---
name: network-egress
description: Use Isolake egress commands to inspect and patch runtime outbound network policy for a sandbox. Trigger when users want to allow or deny domains, confirm current egress rules, or debug outbound network access problems caused by policy.
---

# Isolake Network Egress

Manage outbound network access with `isolake egress`. Always inspect current state, patch only what is needed, then verify both policy text and actual network behavior.

## When To Use

- the user wants to see current egress rules for a sandbox
- the user wants to allow or deny one or more outbound domains
- the user reports outbound access issues and runtime policy may be the cause

## Policy Model

- `defaultAction`: fallback when no rule matches (defaults to `deny`)
- `egress`: ordered list of allow/deny rules
- patching uses merge semantics, not full replacement
- targets are domain-based: `pypi.org`, `*.example.com`

## Inspect Current Policy

```bash
isolake egress get <sandbox-id> -o json
```

## Patch Runtime Rules

```bash
isolake egress patch <sandbox-id> --rule allow=pypi.org -o json
isolake egress patch <sandbox-id> --rule deny=internal.example.com -o json
isolake egress patch <sandbox-id> --rule allow=*.example.com -o json
isolake egress patch <sandbox-id> --rule allow=www.github.com --rule deny=pypi.org -o json
```

## Behavior Verification

Verify with actual commands, not just policy text:

```bash
isolake command run <sandbox-id> -o raw -- curl -I https://pypi.org
isolake command run <sandbox-id> -o raw -- curl -I https://www.github.com
```

## Golden Paths

Inspect and patch:

```bash
isolake egress get <sandbox-id> -o json
isolake egress patch <sandbox-id> --rule allow=pypi.org -o json
isolake egress get <sandbox-id> -o json
```

Inspect, patch, and verify behavior:

```bash
isolake egress get <sandbox-id> -o json
isolake egress patch <sandbox-id> --rule allow=www.github.com --rule deny=pypi.org -o json
isolake egress get <sandbox-id> -o json
isolake command run <sandbox-id> -o raw -- curl -I https://www.github.com
isolake command run <sandbox-id> -o raw -- curl -I https://pypi.org
```

## Minimal Closed Loops

Allow one domain:

```bash
isolake egress get <sandbox-id> -o json
isolake egress patch <sandbox-id> --rule allow=pypi.org -o json
isolake egress get <sandbox-id> -o json
isolake command run <sandbox-id> -o raw -- curl -I https://pypi.org
```

## Best Practices

- inspect before patching
- patch only the minimum required domains
- verify actual behavior, not just rule text
- prefer explicit domain targets over broad wildcards
- use create-time `--network-policy-file` for initial setup, runtime patching for later changes
