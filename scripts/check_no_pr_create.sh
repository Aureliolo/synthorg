#!/usr/bin/env bash
# PreToolUse(Bash) hook: block direct `gh pr create`.
#
# PR creation must go through /pre-pr-review (automated checks + review
# agents + fixes before the PR exists). Replaces the inert
# .claude/hookify.block-pr-create.md rule with a guaranteed-firing gate.
#
# Modes: JSON stdin (Claude Code / OpenCode) -> inspect command; no
# stdin (pre-commit) -> not applicable, pass.
set -euo pipefail

if ! COMMAND=$(jq -r '.tool_input.command // empty' 2>/dev/null); then
    exit 0
fi

if [[ -z "$COMMAND" ]]; then
    exit 0
fi

if echo "$COMMAND" | grep -qE '\bgh[[:space:]]+pr[[:space:]]+create\b'; then
    cat <<'ENDJSON'
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "BLOCKED: do not run `gh pr create` directly. Use /pre-pr-review -- it runs automated checks + review agents + fixes, then creates the PR."
  }
}
ENDJSON
    exit 2
fi
