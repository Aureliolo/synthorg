#!/usr/bin/env bash
# PreToolUse hook: block the model from creating or modifying any
# user-only override flag under ``.claude/state/``.
#
# Why this exists:
#   Several PreToolUse gates accept an override only when the user
#   has manually written a flag file with the current branch name
#   as its first line. The override is meant to be deliberate and
#   out-of-band -- a command the user types in their own terminal,
#   NOT something the model can create on its own. Without this
#   guard the model could call ``Write({ file_path: "...flag",
#   content: "feat/branch" })`` to manufacture an override and
#   bypass the gate, defeating the whole point.
#
# Flags covered:
#   - ``.claude/state/allow-double-push.flag`` -- one-shot push
#     throttle override (see ``scripts/check_push_throttle.sh``).
#   - ``.claude/state/allow-failing-ci-push.flag`` -- one-shot
#     CI-failure push override (see
#     ``scripts/check_ci_before_push.sh``).
#
# Coverage:
#   - Write tool: ``tool_input.file_path`` ends with a flag name.
#   - Edit tool: same path check (Edit only modifies existing files,
#     so it cannot create the flag, but we block it anyway in case a
#     future Edit variant supports create-on-missing).
#   - Bash tool: ``tool_input.command`` references any flag path
#     (touch / cp / mv / ln / mkfifo / chmod / any redirect target /
#     printf / etc.). The ``check_bash_no_write.sh`` hook blocks
#     redirect-based writes generally, but ``touch
#     .claude/state/<flag>`` is not a redirect, so we need an
#     explicit path-substring check here.
#
# Allow-list:
#   - The owning gate scripts read + delete their own flag at
#     runtime. They are invoked as PreToolUse hooks by the runtime,
#     not via Bash/Write/Edit, so they never go through this gate.
#   - The user creating a flag in their own shell (no Claude tools
#     involved) bypasses every PreToolUse hook by definition; that
#     is the intended override path.

set -euo pipefail

FLAG_NAMES=(
    "allow-double-push.flag"
    "allow-failing-ci-push.flag"
)
BASENAME_ALT=$(printf '%s|' "${FLAG_NAMES[@]}")
BASENAME_ALT="${BASENAME_ALT%|}"
PATH_SUFFIX_REGEX="(^|[/\\\\])\\.claude[/\\\\]state[/\\\\](${BASENAME_ALT//./\\.})\$"
COMMAND_REGEX="(${BASENAME_ALT//./\\.})"

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
        if [[ -n "$FILE_PATH" ]] && printf '%s\n' "$FILE_PATH" | grep -qE "${PATH_SUFFIX_REGEX}"; then
            deny "Creating or editing a user-only override flag under .claude/state/ is not permitted from the model. Each gate's override flag (allow-double-push.flag, allow-failing-ci-push.flag) must be created by the user in their own shell. See the owning gate's docstring for the documented override command."
        fi
        ;;
    Bash)
        COMMAND=$(printf '%s' "$PAYLOAD" | jq -r '.tool_input.command // ""' 2>/dev/null || echo "")
        # Strip quoted strings so a flag basename mentioned inside
        # a commit message body or here-doc does not trip the gate.
        # Real creation commands (``touch .claude/state/<flag>``,
        # ``cp ... <flag>``) reference the path UNQUOTED at the
        # verb position, so this strip preserves the protection
        # while killing the false positives. Collapse newlines to
        # spaces FIRST -- sed regex is line-oriented and
        # multi-line quoted commit-message bodies otherwise leak
        # the flag basename through the strip.
        COMMAND_STRIPPED=$(printf '%s' "$COMMAND" \
            | tr '\n\r' '  ' \
            | sed -E "s/'[^']*'//g; s/\"[^\"]*\"//g")
        if [[ -n "$COMMAND_STRIPPED" ]] && printf '%s\n' "$COMMAND_STRIPPED" | grep -qE "${COMMAND_REGEX}"; then
            deny "Bash commands that reference any user-only override flag under .claude/state/ are not permitted from the model. Each gate's override flag (allow-double-push.flag, allow-failing-ci-push.flag) must be created by the user in their own shell."
        fi
        ;;
    *)
        # Other tools cannot reach the flag path.
        :
        ;;
esac

exit 0
