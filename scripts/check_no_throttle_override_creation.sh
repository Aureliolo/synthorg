#!/usr/bin/env bash
# PreToolUse hook: block the model from creating or modifying the
# push-throttle override flag at ``.claude/state/allow-double-push.flag``.
#
# Why this exists:
#   ``check_push_throttle.sh`` (the 5-minute push-throttle gate)
#   accepts an override only when the user has manually written
#   the flag file with the current branch name as its first line.
#   The override is meant to be deliberate and out-of-band -- a
#   command the user types in their own terminal, NOT something
#   the model can create on its own. Without this guard the model
#   could call ``Write({ file_path: "...allow-double-push.flag",
#   content: "feat/branch" })`` to manufacture an override and
#   bypass the throttle, defeating the whole point of the gate.
#
# Coverage:
#   - Write tool: ``tool_input.file_path`` ends with the flag name.
#   - Edit tool: same path check (Edit only modifies existing files,
#     so it cannot create the flag, but we block it anyway in case a
#     future Edit variant supports create-on-missing).
#   - Bash tool: ``tool_input.command`` references the flag path
#     (touch / cp / mv / ln / mkfifo / chmod / any redirect target /
#     printf / etc.). The ``check_bash_no_write.sh`` hook blocks
#     redirect-based writes generally, but ``touch
#     .claude/state/allow-double-push.flag`` is not a redirect, so
#     we need an explicit path-substring check here.
#
# Allow-list:
#   - ``check_push_throttle.sh`` itself reads + deletes the flag at
#     runtime. It is invoked as a PreToolUse hook by the runtime,
#     not via Bash/Write/Edit, so it never goes through this gate.
#   - The user creating the flag in their own shell (no Claude
#     tools involved) bypasses every PreToolUse hook by definition;
#     that is the intended override path.

set -euo pipefail

FLAG_REL=".claude/state/allow-double-push.flag"

deny() {
    local reason="$1"
    cat <<ENDJSON
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "$reason"
  }
}
ENDJSON
    exit 2
}

# Pull the full tool_input payload once. We tolerate non-JSON stdin
# (script invoked standalone for debugging) by exiting 0.
PAYLOAD=""
if [[ ! -t 0 ]]; then
    PAYLOAD=$(cat || echo "")
fi
if [[ -z "$PAYLOAD" ]]; then
    exit 0
fi

TOOL=$(printf '%s' "$PAYLOAD" | jq -r '.tool_name // ""' 2>/dev/null || echo "")

case "$TOOL" in
    Write|Edit|NotebookEdit)
        FILE_PATH=$(printf '%s' "$PAYLOAD" | jq -r '.tool_input.file_path // ""' 2>/dev/null || echo "")
        if [[ -n "$FILE_PATH" ]] && printf '%s\n' "$FILE_PATH" | grep -qE "(^|[/\\\\])${FLAG_REL//./\\.}\$"; then
            deny "Creating or editing the push-throttle override flag (${FLAG_REL}) is not permitted from the model. The override must be created by the user in their own shell. See scripts/check_push_throttle.sh for the documented override command."
        fi
        ;;
    Bash)
        COMMAND=$(printf '%s' "$PAYLOAD" | jq -r '.tool_input.command // ""' 2>/dev/null || echo "")
        if [[ -n "$COMMAND" ]] && printf '%s\n' "$COMMAND" | grep -qE 'allow-double-push\.flag'; then
            deny "Bash commands that reference the push-throttle override flag (${FLAG_REL}) are not permitted from the model. The override must be created by the user in their own shell. See scripts/check_push_throttle.sh for the documented override command."
        fi
        ;;
    *)
        # Other tools cannot reach the flag path.
        :
        ;;
esac

exit 0
