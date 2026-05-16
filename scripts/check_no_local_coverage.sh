#!/usr/bin/env bash
# PreToolUse(Bash) hook: block local coverage runs.
#
# Coverage is a CI concern (the dedicated coverage job enforces the
# 80% floor). Locally it roughly doubles unit-suite wall-clock for no
# added signal during development. Blocks a pytest invocation that
# passes `--cov` or a bare `coverage run`. Replaces the inert
# .claude/hookify.no-local-coverage.md rule.
#
# Modes: JSON stdin -> inspect command; no stdin -> pass.
set -euo pipefail

RAW="$(cat)"
# No stdin (pre-commit, not a tool call): not applicable, pass.
if [[ -z "${RAW//[[:space:]]/}" ]]; then
    exit 0
fi
# stdin present: it MUST parse. A malformed envelope is an unknown
# state, not "no opinion" -- fail closed so a corrupted/truncated
# payload cannot silently bypass the gate.
if ! COMMAND=$(printf '%s' "$RAW" | jq -r '.tool_input.command // empty' 2>/dev/null); then
    echo "BLOCKED: malformed PreToolUse JSON envelope; gate fails closed." >&2
    exit 2
fi

if [[ -z "$COMMAND" ]]; then
    exit 0
fi

is_pytest=false
if echo "$COMMAND" | grep -qE '(^|[[:space:]])(pytest|python[[:space:]]+-m[[:space:]]+pytest)\b'; then
    is_pytest=true
fi

if { [[ "$is_pytest" == true ]] && echo "$COMMAND" | grep -qE '(^|[[:space:]])--cov(=|[[:space:]]|$)'; } \
    || echo "$COMMAND" | grep -qE '(^|[[:space:]])coverage[[:space:]]+run\b'; then
    cat <<'ENDJSON'
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "BLOCKED: do not run coverage locally. The dedicated CI coverage job enforces the 80% floor; locally it ~doubles unit-suite wall-clock for no added signal. Run the suite without --cov."
  }
}
ENDJSON
    exit 2
fi
