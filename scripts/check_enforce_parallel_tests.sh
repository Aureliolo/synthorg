#!/usr/bin/env bash
# PreToolUse(Bash) hook: keep the unit suite running under xdist.
#
# pyproject ``addopts`` already applies ``-n=8 --dist=loadfile``, so a
# plain ``pytest tests/ -m unit`` IS parallel and must NOT be blocked
# (the inert .claude/hookify.enforce-parallel-tests.md rule's literal
# "must contain -n 8" would have blocked the documented command -- a
# bug; this gate enforces the actual intent instead).
#
# Blocks only an EXPLICIT xdist-disable (`-n 0` / `-n0` /
# `--numprocesses 0` / `-p no:xdist` / `--dist no`) because that
# silently drops the loadfile isolation the 3.14+Windows event-loop
# teardown race depends on. Benchmark / CodSpeed runs legitimately use
# ``-n0`` (single-process timing) and are exempt.
#
# Modes: JSON stdin -> inspect command; no stdin -> pass.
set -euo pipefail

if ! COMMAND=$(jq -r '.tool_input.command // empty' 2>/dev/null); then
    exit 0
fi

if [[ -z "$COMMAND" ]]; then
    exit 0
fi

# Not a pytest invocation -- no opinion.
if ! echo "$COMMAND" | grep -qE '(^|[[:space:]])(pytest|python[[:space:]]+-m[[:space:]]+pytest)\b'; then
    exit 0
fi

# Benchmark / CodSpeed runs are single-process by design.
if echo "$COMMAND" | grep -qE '(--codspeed|tests/benchmarks)'; then
    exit 0
fi

if echo "$COMMAND" | grep -qE '(^|[[:space:]])(-n[[:space:]]*0|--numprocesses[[:space:]]+0|-p[[:space:]]+no:xdist|--dist[[:space:]]+no)(\b|$)'; then
    cat <<'ENDJSON'
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "BLOCKED: this pytest run explicitly disables xdist. The unit suite must run under `-n` (pyproject addopts applies -n=8 --dist=loadfile; loadfile isolation guards the 3.14+Windows event-loop teardown race). Drop the -n0/--dist no override. Benchmarks (--codspeed / tests/benchmarks) are exempt."
  }
}
ENDJSON
    exit 2
fi
