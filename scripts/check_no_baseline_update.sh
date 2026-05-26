#!/usr/bin/env bash
# PreToolUse hook: block gate-baseline regeneration commands.
#
# `python scripts/check_<gate>.py --update-baseline` (and the older `--update`
# flag on a few gates) silently rewrites a baseline file to absorb new
# violations. This is the exact "grow gate-suppression debt" path we want
# to stop happening autonomously.
#
# Regeneration is rare and requires explicit user approval. Once the user
# has approved, prefix the command with the documented `ALLOW_BASELINE_GROWTH=1`
# signal -- the same per-invocation token the commit-time growth guard honours
# -- and this hook allows it through. Without that token the command is denied
# so a baseline can never grow autonomously.
#
# Exit behaviour:
#   - Non-update-baseline commands: exit 0 (allow)
#   - --update-baseline / --update carrying ALLOW_BASELINE_GROWTH=1: exit 0 (allow)
#   - --update-baseline / --update without the token: print JSON, exit 2

set -euo pipefail

COMMAND=$(jq -r '.tool_input.command // ""' 2>/dev/null || true)
if [[ -z "$COMMAND" ]]; then
    exit 0
fi

# Approved-bypass: an explicit ALLOW_BASELINE_GROWTH=1 (or =true) prefix is the
# user's per-invocation approval signal, mirroring `ALLOW_BASELINE_GROWTH=1 git
# commit`. The token must anchor to the start of the command (after optional
# leading whitespace) so a token buried mid-command -- e.g. an echo earlier in
# a `&&` chain -- cannot smuggle a bypass past the guard.
if [[ "$COMMAND" =~ ^[[:space:]]*ALLOW_BASELINE_GROWTH=(1|true)([[:space:]]|$) ]]; then
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
    jq -nc \
        --arg reason "$REASON" \
        '{hookSpecificOutput: {hookEventName: "PreToolUse", permissionDecision: "deny", permissionDecisionReason: $reason}}'
    exit 2
fi

exit 0
