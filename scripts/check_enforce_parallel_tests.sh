#!/usr/bin/env bash
# PreToolUse(Bash) hook: the unit suite always runs under the pinned
# xdist default; explicit -n is a smell.
#
# pyproject ``addopts`` pins ``-n=8 --dist=loadfile``. The ONLY
# sanctioned form is therefore NO ``-n`` flag at all -- the default
# governs. Rules:
#
#   * No -n / --numprocesses / --dist / -p no:xdist  -> OK
#   * Any explicit non-zero -n / --numprocesses
#     (-n 2, -n 8, -n auto, ...)                      -> BLOCK
#     (never a reason to hand-pick a worker count; omit it)
#   * xdist-disable (-n 0 / -n0 / --numprocesses 0 /
#     --dist no / -p no:xdist)                        -> BLOCK,
#     UNLESS the command targets a single test (a ``::`` node id) --
#     single-process is valid only to read one test's full log.
#   * Benchmark / CodSpeed runs (--codspeed / tests/benchmarks)
#     are single-process by design                    -> OK
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

deny() {
    cat <<ENDJSON
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "$1"
  }
}
ENDJSON
    exit 2
}

# An explicit, NON-zero worker count is always wrong: pyproject
# addopts already applies -n=8 --dist=loadfile. Omit the flag.
if echo "$COMMAND" | grep -qE '(^|[[:space:]])(-n[[:space:]]*([1-9][0-9]*|auto|logical)|--numprocesses[[:space:]]+([1-9][0-9]*|auto|logical))(\b|$)'; then
    deny "BLOCKED: do not pass an explicit -n/--numprocesses. pyproject addopts already pins -n=8 --dist=loadfile; the only correct form is NO -n flag (the default governs). Remove it."
fi

# xdist-disable: only legitimate to read ONE test's full log, so
# require a node id. A bare directory/suite run with -n0 is blocked.
if echo "$COMMAND" | grep -qE '(^|[[:space:]])(-n[[:space:]]*0|--numprocesses[[:space:]]+0|--dist[[:space:]]+no|-p[[:space:]]+no:xdist)(\b|$)'; then
    if echo "$COMMAND" | grep -qE '::'; then
        exit 0
    fi
    deny "BLOCKED: single-process pytest (-n0 / --dist no / -p no:xdist) is allowed ONLY for a single test (a path::test_name node id) to read its full log. For any suite/directory run, omit -n entirely so the pinned -n=8 --dist=loadfile default applies."
fi

exit 0
