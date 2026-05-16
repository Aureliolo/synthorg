#!/usr/bin/env bash
# PreToolUse(Bash) hook: block a command that STARTS with `cd `.
#
# A leading `cd <dir> && ...` poisons the Bash tool's cwd for every
# later call. The sanctioned escape for tools without a -C/--prefix
# flag is `bash -c "cd <dir> && <cmd>"` (a child shell), which does
# not start with `cd` and is therefore allowed. Replaces the inert
# .claude/hookify.no-cd-prefix.md rule.
#
# Modes: JSON stdin -> inspect command; no stdin -> pass.
set -euo pipefail

if ! COMMAND=$(jq -r '.tool_input.command // empty' 2>/dev/null); then
    exit 0
fi

if [[ -z "$COMMAND" ]]; then
    exit 0
fi

# Only a LEADING cd (optionally after whitespace) is blocked; an
# embedded `bash -c "cd ..."` or `git ... && cd` is not matched.
if echo "$COMMAND" | grep -qE '^[[:space:]]*cd[[:space:]]'; then
    cat <<'ENDJSON'
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "BLOCKED: command starts with `cd`, which poisons the Bash tool cwd for all later calls. The tool already runs in the project root -- run the command directly, use the tool's native -C/--prefix/--project flag, or `bash -c \"cd <dir> && <cmd>\"` for tools without one."
  }
}
ENDJSON
    exit 2
fi
