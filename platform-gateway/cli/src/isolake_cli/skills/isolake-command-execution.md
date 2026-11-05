---
name: command-execution
description: Use Isolake command execution commands to run foreground or background processes, inspect tracked execution status and logs, interrupt work, and manage persistent shell sessions inside a sandbox. Trigger when users want to execute commands in a sandbox and need exact Isolake CLI flows.
---

# Isolake Command Execution

Run commands with `isolake command`. Use foreground streaming by default, and add `--background` when you need tracked execution.

## When To Use

- the user wants to run a one-off command in a sandbox
- the user needs a tracked background command with later status/log inspection
- the user wants to stop a running command
- the user wants a persistent shell session that keeps working directory or environment state across runs

## Separator Rule

Use `--` before the sandbox command payload when the payload contains flags:

```bash
isolake command run <sandbox-id> -o raw -- sh -lc 'echo ready'
isolake command run <sandbox-id> -o raw -- python -m http.server
```

## Execution Modes

- `isolake command run` without `--background`: foreground one-shot, streams to terminal
- `isolake command run --background`: tracked, returns execution ID for later inspection
- `isolake command session ...`: persistent shell state across commands

## Golden Paths

Foreground one-shot:

```bash
isolake command run <sandbox-id> -o raw -- python -c "print(1 + 1)"
```

Tracked background:

```bash
isolake command run <sandbox-id> --background -o json -- sh -c "sleep 10; echo done"
isolake command status <sandbox-id> <execution-id> -o json
isolake command logs <sandbox-id> <execution-id> -o json
```

Persistent session:

```bash
isolake command session create <sandbox-id> --workdir /workspace -o json
isolake command session run <sandbox-id> <session-id> -o raw -- pwd
isolake command session run <sandbox-id> <session-id> -o raw -- export FOO=bar
isolake command session run <sandbox-id> <session-id> -o raw -- sh -c 'echo $FOO'
isolake command session delete <sandbox-id> <session-id> -o json
```

## Foreground Commands

```bash
isolake command run <sandbox-id> -o raw -- <command>
isolake command run <sandbox-id> --workdir /workspace -o raw -- <command>
isolake command run <sandbox-id> --timeout 30s -o raw -- <command>
```

## Background Commands

```bash
isolake command run <sandbox-id> --background -o json -- <command>
isolake command status <sandbox-id> <execution-id> -o json
isolake command logs <sandbox-id> <execution-id> -o json
isolake command logs <sandbox-id> <execution-id> --cursor 0 -o json
```

## Persistent Sessions

```bash
isolake command session create <sandbox-id> --workdir /workspace -o json
isolake command session run <sandbox-id> <session-id> -o raw -- pwd
isolake command session run <sandbox-id> <session-id> -o raw -- export FOO=bar
isolake command session run <sandbox-id> <session-id> -o raw -- sh -c 'echo $FOO'
isolake command session run <sandbox-id> <session-id> --workdir /var -o raw -- pwd
isolake command session delete <sandbox-id> <session-id> -o json
```

Rules:
- `session create --workdir` sets the initial working directory
- `session run --workdir` overrides for that single run only
- exported variables persist across `session run` calls
- delete the session when done

## Interrupting Work

```bash
isolake command interrupt <sandbox-id> <execution-id> -o json
```

## Minimal Closed Loops

Foreground with timeout:

```bash
isolake command run <sandbox-id> --timeout 30s -o raw -- python -c "print(1 + 1)"
```

Tracked background:

```bash
isolake command run <sandbox-id> --background -o json -- sh -c "sleep 10; echo done"
isolake command status <sandbox-id> <execution-id> -o json
isolake command logs <sandbox-id> <execution-id> -o json
```

Background with interrupt:

```bash
isolake command run <sandbox-id> --background -o json -- sh -c "sleep 300"
isolake command interrupt <sandbox-id> <execution-id> -o json
isolake command status <sandbox-id> <execution-id> -o json
```

Session with shared state:

```bash
isolake command session create <sandbox-id> --workdir /workspace -o json
isolake command session run <sandbox-id> <session-id> -o raw -- export FOO=bar
isolake command session run <sandbox-id> <session-id> -o raw -- sh -c 'echo $FOO'
isolake command session delete <sandbox-id> <session-id> -o json
```

## Best Practices

- use `-o raw` for quick foreground commands
- use `--background` when the user will need execution tracking
- use sessions only when state persistence is actually needed
- use `--timeout` when the command should not run indefinitely
- prefer `status` before guessing whether a background command is still running
