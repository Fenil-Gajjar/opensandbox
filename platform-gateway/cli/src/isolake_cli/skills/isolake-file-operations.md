---
name: file-operations
description: Use Isolake file commands to read, write, upload, download, search, replace, move, delete, and inspect files or directories inside a sandbox. Trigger when users want exact sandbox file manipulation commands instead of generic shell guidance.
---

# Isolake File Operations

Manipulate sandbox files with `isolake file` commands. Choose the operation mode first, then use the matching verification step.

## When To Use

- the user wants to read or write a file inside a sandbox
- the user needs to upload a local file into a sandbox or download a sandbox file back to the host
- the user wants to search, replace, move, chmod, or inspect paths
- the user needs directory creation or cleanup

## Operation Modes

- sandbox-only: `cat`, `write`, `replace`, `mv`, `mkdir`, `rm`, `rmdir`, `info`, `chmod`
- host-to-sandbox: `upload`
- sandbox-to-host: `download`
- discovery: `search`, then `info`

## Golden Paths

Write and verify:

```bash
isolake file write <sandbox-id> /workspace/app.txt -c "hello" -o json
isolake file cat <sandbox-id> /workspace/app.txt -o raw
```

Upload from host:

```bash
isolake file upload <sandbox-id> ./local.txt /workspace/local.txt -o json
isolake file cat <sandbox-id> /workspace/local.txt -o raw
```

Search before editing:

```bash
isolake file search <sandbox-id> /workspace --pattern "*.py" -o json
isolake file info <sandbox-id> /workspace/main.py -o json
```

## Command Map

```bash
isolake file cat <sandbox-id> /path -o raw
isolake file write <sandbox-id> /path -c "content" -o json
isolake file upload <sandbox-id> ./local /remote -o json
isolake file download <sandbox-id> /remote ./local -o json
isolake file replace <sandbox-id> /path --old old --new new -o json
isolake file mv <sandbox-id> /old /new -o json
isolake file mkdir <sandbox-id> /path -o json
isolake file mkdir <sandbox-id> /a /b --mode 755 -o json
isolake file rm <sandbox-id> /path -o json
isolake file rmdir <sandbox-id> /path -o json
isolake file search <sandbox-id> /workspace --pattern "*.py" -o json
isolake file info <sandbox-id> /path -o json
isolake file chmod <sandbox-id> /path --mode 755 -o json
isolake file chmod <sandbox-id> /path --mode 644 --owner root --group root -o json
```

## Destructive Operations

Verify before deleting:

```bash
isolake file info <sandbox-id> /workspace/tmp.txt -o json
isolake file rm <sandbox-id> /workspace/tmp.txt -o json
```

## Minimal Closed Loops

Write and verify:

```bash
isolake file write <sandbox-id> /workspace/app.txt -c "hello" -o json
isolake file cat <sandbox-id> /workspace/app.txt -o raw
```

Upload and verify:

```bash
isolake file upload <sandbox-id> ./local.txt /workspace/local.txt -o json
isolake file cat <sandbox-id> /workspace/local.txt -o raw
```

Replace and verify:

```bash
isolake file replace <sandbox-id> /workspace/app.txt --old hello --new world -o json
isolake file cat <sandbox-id> /workspace/app.txt -o raw
```

Permissions:

```bash
isolake file chmod <sandbox-id> /workspace/script.sh --mode 755 -o json
isolake file info <sandbox-id> /workspace/script.sh -o json
```

Move and verify:

```bash
isolake file mv <sandbox-id> /workspace/app.txt /workspace/archive/app.txt -o json
isolake file info <sandbox-id> /workspace/archive/app.txt -o json
```

Delete and verify:

```bash
isolake file info <sandbox-id> /workspace/tmp.txt -o json
isolake file rm <sandbox-id> /workspace/tmp.txt -o json
```

## Best Practices

- prefer `search` before modification when the path is uncertain
- prefer `info` before destructive actions
- prefer `replace` over full rewrites for small text changes
- prefer `upload`/`download` only for host boundary crossings
- verify after `mv`, `chmod`, `rm`, `rmdir`
