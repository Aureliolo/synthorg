#!/usr/bin/env bash
# PreToolUse hook: block Edit/Write on protected baseline files.
# Baseline files must only be updated with explicit user approval.
# Claude must never autonomously bump baselines to bypass regression guards
# or grow gate-suppression debt.
#
# Protected files:
#   - tests/baselines/*.json            (test timing baselines)
#   - scripts/*_baseline.txt            (gate suppression baselines)
#   - scripts/*_baseline.json           (gate suppression baselines)
#   - scripts/_*_baseline.py            (gate suppression baselines, py-format)
#
# Exit behavior:
#   - Non-baseline files: exit 0 (allow)
#   - Baseline files: print JSON with reason, exit 2

set -euo pipefail

if ! FILE_PATH=$(jq -r '.tool_input.file_path // ""' 2>/dev/null); then
    exit 0
fi
if [[ -z "$FILE_PATH" ]]; then
    exit 0
fi

REASON=""

case "$FILE_PATH" in
    */tests/baselines/*.json|tests/baselines/*.json)
        REASON="Test timing baselines require explicit user approval to modify. Do not bump baselines to bypass regression guards -- fix the source code or tests instead."
        ;;
    */scripts/*_baseline.txt|scripts/*_baseline.txt|*/scripts/*_baseline.json|scripts/*_baseline.json|*/scripts/_*_baseline.py|scripts/_*_baseline.py)
        REASON="Gate suppression baselines require explicit user approval to modify. Do not grow them to silence new violations -- fix the source instead. If a legitimate exception exists, ask the user before regenerating with the gate's --update-baseline flag."
        ;;
esac

if [[ -n "$REASON" ]]; then
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
