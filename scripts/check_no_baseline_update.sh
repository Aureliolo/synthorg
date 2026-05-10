#!/usr/bin/env bash
# PreToolUse hook: block gate-baseline regeneration commands.
#
# `python scripts/check_<gate>.py --update-baseline` (and the older `--update`
# flag on a few gates) silently rewrites a baseline file to absorb new
# violations. This is the exact "grow gate-suppression debt" path we want
# to stop happening autonomously.
#
# Regeneration is rare and requires explicit user approval. If the user
# decides a regeneration is warranted, they can run the command themselves
# in a `! `-prefixed bash invocation, or temporarily disable this hook.
#
# Exit behavior:
#   - Non-update-baseline commands: exit 0 (allow)
#   - --update-baseline / --update on a check_*.py: print JSON, exit 2

set -euo pipefail

if ! COMMAND=$(jq -r '.tool_input.command // ""' 2>/dev/null); then
    exit 0
fi
if [[ -z "$COMMAND" ]]; then
    exit 0
fi

# Match patterns:
#   scripts/check_<anything>.py --update-baseline
#   scripts/check_<anything>.py --update
#   python ... check_<anything>.py ... --update-baseline / --update
#   uv run python ... check_<anything>.py ... --update-baseline / --update
#   python scripts/check_<anything>.py --refresh-baseline
if [[ "$COMMAND" =~ scripts/check_[a-z_]+\.py.*(--update-baseline|--refresh-baseline) ]] || \
   [[ "$COMMAND" =~ scripts/check_[a-z_]+\.py.*--update($|[[:space:]]) ]]; then
    REASON="Gate baseline regeneration is not autonomously allowed. Adding a new exception requires explicit user approval. Fix the source instead, or ask the user before running --update-baseline / --update / --refresh-baseline."
    cat <<ENDJSON
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "$REASON"
  }
}
ENDJSON
    exit 2
fi

exit 0
