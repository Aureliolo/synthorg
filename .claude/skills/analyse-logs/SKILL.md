---
description: "Analyse application logs from Docker containers: extract, parse, summarize, and detect logging pipeline discrepancies"
argument-hint: "[errors|warnings|all|discrepancy|summary] [--since 1h|30m|2h] [--agent <name>] [--correlation <id>]"
allowed-tools:
  - Bash
  - Read
  - Write
  - Grep
  - Glob
  - Agent
  - AskUserQuestion
---

# Log Analyser

Extract, parse, and analyse SynthOrg application logs from Docker containers. Detects discrepancies between Docker stdout and file-based log sinks.

**Arguments:** "$ARGUMENTS"

---

## Architecture Context

The SynthOrg backend has an 11-sink structlog pipeline:

| Sink | File | Level | Filter | Format |
|------|------|-------|--------|--------|
| Console | (stderr/Docker logs) | INFO | none | colored text |
| Main | `synthorg.log` | INFO | none (catch-all) | JSON |
| Audit | `audit.log` | INFO | `synthorg.security.*`, `synthorg.hr.*`, `synthorg.observability.*` | JSON |
| Errors | `errors.log` | ERROR | none | JSON |
| Agent | `agent_activity.log` | DEBUG | `synthorg.engine.*`, `synthorg.core.*`, `synthorg.communication.*`, `synthorg.tools.*`, `synthorg.memory.*` | JSON |
| Cost | `cost_usage.log` | INFO | `synthorg.budget.*`, `synthorg.providers.*` | JSON |
| Debug | `debug.log` | DEBUG | none (catch-all) | JSON |
| Access | `access.log` | INFO | `synthorg.api.*` | JSON |
| Persistence | `persistence.log` | INFO | `synthorg.persistence.*` | JSON |
| Configuration | `configuration.log` | INFO | `synthorg.settings.*`, `synthorg.config.*` | JSON |
| Backup | `backup.log` | INFO | `synthorg.backup.*` | JSON |

**Key invariant**: Every message that appears in Docker logs (console sink, INFO+) MUST also appear in `synthorg.log` (catch-all, INFO+) and `debug.log` (catch-all, DEBUG+). A message in Docker logs but missing from file sinks is a **routing bug** in the observability pipeline.

The reverse is fine: file sinks contain DEBUG-level messages and routed messages that the console sink filters out.

Log files live at `/data/logs/` inside the backend container, on the `synthorg-data` Docker volume.

---

## Step 1: Extract Logs

### File-based logs (always do this first)

Copy the log files out of the container volume via `docker cp`. The container just needs to exist (even stopped):

```bash
mkdir -p logs && docker cp synthorg-backend-1:/data/logs/. logs/
ls -lh logs/
```

### Docker logs (only needed for discrepancy check)

Only fetch Docker logs if running discrepancy mode or default (summary + discrepancy). Requires the container to be running:

```bash
docker logs synthorg-backend-1 --tail 1000 > logs/docker-stdout.txt 2>&1
```

Do **not** pass `--timestamps`; Docker's RFC 3339 prefix would prepend a second timestamp before the app's own timestamp, breaking the parsing regex in Step 3.

If the container is stopped, skip the discrepancy check and report: "Container is stopped: file log analysis only (no discrepancy check)."

---

## Step 2: Parse and Filter

All file-based logs are newline-delimited JSON. Each line has at minimum:
- `timestamp` (ISO 8601 UTC)
- `level` (debug/info/warning/error/critical)
- `event` (the log event name; typically a constant from `synthorg.observability.events`)
- `logger` (the Python module path, e.g. `synthorg.engine.loops.react`)

Optional fields: `request_id`, `task_id`, `agent_id`, plus arbitrary structured kwargs.

### Filtering by arguments

- `errors`: only show ERROR and CRITICAL from `errors.log`
- `warnings`: show WARNING+ from `synthorg.log`
- `all`: full dump from `debug.log`
- `summary`: aggregate counts by level, logger, and event across all sinks
- `discrepancy`: run the discrepancy check (Step 4)
- `--since <duration>`: filter by timestamp (parse ISO timestamps, compare)
- `--agent <name>`: filter by `agent_id` field
- `--correlation <id>`: filter by `request_id` or `task_id`

If no argument is provided, default to `summary` + `discrepancy`.

### Parsing approach

For large log files, do NOT read the entire file into the conversation. Instead:

1. Use `python -c` or a small script to parse JSON lines and produce aggregates
2. For error analysis, extract only the relevant lines
3. Present structured summaries, not raw dumps

Example summary script pattern (minimal; does not implement `--since`, `--agent`, or `--correlation` filtering; extend with `argparse` as needed):

```bash
python -c "
import json, sys, collections
levels = collections.Counter()
events = collections.Counter()
loggers = collections.Counter()
for line in open('logs/synthorg.log'):
    line = line.strip()
    if not line:
        continue
    try:
        rec = json.loads(line)
        levels[rec.get('level', '?')] += 1
        events[rec.get('event', '?')] += 1
        loggers[rec.get('logger', '?')] += 1
    except json.JSONDecodeError:
        levels['PARSE_ERROR'] += 1
print('=== Levels ===')
for k, v in levels.most_common(): print(f'  {k}: {v}')
print('=== Top Events ===')
for k, v in events.most_common(20): print(f'  {k}: {v}')
print('=== Top Loggers ===')
for k, v in loggers.most_common(20): print(f'  {k}: {v}')
"
```

---

## Step 3: Discrepancy Detection

This is the critical check. Every INFO+ message in Docker logs must also exist in the file-based logs.

### Method

1. **Parse Docker logs** into structured records. The console sink outputs colored text, not JSON. Each line (captured without `--timestamps`) typically looks like:

   ```text
   2026-03-20T10:15:30.123456Z [info     ] event_name                     [synthorg.module] key=value key2=value2
   ```

   Extract: timestamp, level, event, logger name. If `--timestamps` was accidentally used, strip the Docker-added RFC 3339 prefix (everything up to and including the first space before the app timestamp) before parsing.

2. **Parse `synthorg.log`** (the INFO+ catch-all) into a set of `(event, logger, approximate_timestamp)` tuples.

3. **For each Docker log entry**, check if a matching record exists in `synthorg.log` within a small timestamp window (1 second). Match on `event` + `logger`.

4. **Report discrepancies**: Docker log entries with no matching file log entry. These indicate:
   - A logger that somehow bypasses the file handler chain
   - A foreign library logging directly to stderr without going through structlog
   - A `print()` statement in application code (forbidden by convention)
   - Handler initialization failure that was silently swallowed

### Discrepancy analysis script pattern

```bash
python -c "
import json, sys, re
from datetime import datetime, timedelta

# Parse Docker logs (console format)
docker_entries = []
with open('logs/docker-stdout.txt') as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        # Try to extract timestamp, level, event, logger from console format
        # Pattern: TIMESTAMP [level] event [logger] kwargs
        m = re.match(r'(\d{4}-\d{2}-\d{2}T[\d:.]+Z?)\s+\[(\w+)\s*\]\s+(\S+)\s+\[([^\]]+)\]', line)
        if m:
            docker_entries.append({
                'ts': m.group(1), 'level': m.group(2),
                'event': m.group(3), 'logger': m.group(4),
                'raw': line
            })
        else:
            docker_entries.append({'ts': '', 'level': '?', 'event': '?', 'logger': '?', 'raw': line})

# Parse synthorg.log (JSON catch-all) with timestamps
file_events = {}  # (event, logger) -> [timestamps]
with open('logs/synthorg.log') as f:
    for line in f:
        try:
            rec = json.loads(line.strip())
            key = (rec.get('event', ''), rec.get('logger', ''))
            ts_str = rec.get('timestamp', '')
            try:
                ts = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
            except (ValueError, AttributeError):
                ts = None
            file_events.setdefault(key, []).append(ts)
        except (json.JSONDecodeError, ValueError):
            pass

# Find discrepancies (within 1-second timestamp window)
missing = []
for entry in docker_entries:
    key = (entry['event'], entry['logger'])
    if entry['event'] == '?':
        continue
    if key not in file_events:
        missing.append(entry)
        continue
    # Check timestamp proximity if both sides have timestamps
    if entry['ts']:
        try:
            d_ts = datetime.fromisoformat(entry['ts'].replace('Z', '+00:00'))
            if not any(f_ts and abs((d_ts - f_ts).total_seconds()) <= 1.0
                       for f_ts in file_events[key]):
                missing.append(entry)
        except (ValueError, AttributeError):
            pass  # fall back to key-only match (already matched above)

if missing:
    print(f'DISCREPANCY: {len(missing)} Docker log entries not found in file sinks:')
    for m in missing[:20]:
        print(f'  [{m[\"level\"]}] {m[\"event\"]} [{m[\"logger\"]}]')
    if len(missing) > 20:
        print(f'  ... and {len(missing) - 20} more')
else:
    print('No discrepancies: all Docker log entries found in file sinks.')
"
```

### Interpreting discrepancies

Report each discrepancy with:
- The event name and logger
- Whether it looks like a foreign library log (logger doesn't start with `synthorg.`)
- Whether it looks like a raw print() (no structured format at all)
- Suggested fix (add logger routing, wrap foreign library, remove print statement)

**Severity**:
- `synthorg.*` logger missing from files: **BUG** in sink routing (must fix)
- Foreign library (uvicorn, litellm, etc.) only in Docker: **WARNING** (should be captured but may be acceptable)
- Raw unstructured text (no logger pattern): **BUG** (likely a `print()` statement or uncaught exception going to stderr)

---

## Step 4: Present Results

Format the output based on the requested mode:

### Summary mode (default)

```text
## Log Summary (last 1h)

### Volume
| Sink | Lines | Size |
|------|-------|------|
| synthorg.log | 1,234 | 2.1 MB |
| debug.log | 5,678 | 8.3 MB |
| errors.log | 12 | 45 KB |
| ... | ... | ... |

### By Level
| Level | Count |
|-------|-------|
| DEBUG | 4,444 |
| INFO | 1,200 |
| WARNING | 22 |
| ERROR | 12 |

### Top Events (by frequency)
1. `TOOL_INVOKE_START` (synthorg.tools): 340
2. `API_REQUEST_STARTED` (synthorg.api): 280
...

### Errors (last 1h)
- [10:15:30] PROVIDER_CONNECTION_ERROR (synthorg.providers.litellm): connection refused to example-provider
- [10:22:45] TASK_EXECUTION_FAILED (synthorg.engine.loops.react): agent_id=ceo, task_id=abc123
...
```

### Discrepancy mode

```text
## Discrepancy Report

Status: X entries in Docker logs missing from file sinks

### Bugs (synthorg.* logger, must fix)
- `SOME_EVENT` from `synthorg.some.module`: not routed to any file sink
  Fix: check _SINK_ROUTING in sinks.py, verify handler attachment

### Warnings (foreign library)
- uvicorn access log entries: going to stderr only
  Fix: configure uvicorn to use structlog wrapper

### Raw output (no logger)
- 3 lines of unstructured text: likely print() statements
  Fix: grep codebase for print() in application code
```

---

## Step 5: Cleanup

Remove the temporary files:

```bash
rm -rf logs/
```

---

## Rules

- **Never dump raw log files** into the conversation; always parse and summarize
- **Large files**: use `wc -l` and `du -h` first to gauge size before deciding how to process
- **Timestamps**: all file logs are ISO 8601 UTC. Docker log timestamps may include timezone; normalize before comparing
- **Rotation**: log files may have rotated copies (`.1`, `.2`, etc.); include them in analysis if `--since` spans beyond current file
- **Sensitive data**: the logs have already been through `sanitize_sensitive_fields` (passwords, tokens, API keys are `**REDACTED**`), but still avoid dumping large volumes of log data into the conversation unnecessarily
- **Container access**: use `docker ps -a --filter "name=synthorg"` for discovery and `docker cp`/`docker exec`/`docker logs` with the container name (`synthorg-backend-1`) directly. Use `synthorg logs` CLI when available. Do NOT use `docker compose -f docker/compose.yml`; the running compose file is in the CLI data directory (e.g. `~/.synthorg/` on Linux/macOS, `%LOCALAPPDATA%\synthorg\` on Windows), not the repo's `docker/` directory. Volumes and container metadata live in the Docker VM, so accessing repo files directly will target the wrong or non-existent containers.
- **Distroless container**: the backend uses a Chainguard distroless base image; there is no `/bin/sh` or `/bin/bash`. Commands like `docker exec synthorg-backend-1 sh -c '...'` will fail. Use `docker cp` to extract files (primary method) and `docker exec synthorg-backend-1 /app/.venv/bin/python -c '...'` for any in-container Python execution.
- **If containers are down**: report clearly and stop. Don't try to access volumes directly (they may be on a Docker VM).
