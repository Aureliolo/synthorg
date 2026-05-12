#!/usr/bin/env bash
# PreToolUse hook: block Edit/Write on already-applied revision files.
# Revisions are immutable once committed; if you need different behaviour
# author a new revision instead of editing an existing one.
#
# Exit behavior:
#   - Non-migration files: exit 0 (allow)
#   - Migration files: print JSON with reason, exit 2

set -euo pipefail

if ! FILE_PATH=$(jq -r '.tool_input.file_path // ""' 2>/dev/null); then
    exit 0
fi
if [[ -z "$FILE_PATH" ]]; then
    exit 0
fi

REVISIONS_DIRS=(
    "src/synthorg/persistence/sqlite/revisions"
    "src/synthorg/persistence/postgres/revisions"
)

for REVISIONS_DIR in "${REVISIONS_DIRS[@]}"; do
    if [[ "$FILE_PATH" == */"$REVISIONS_DIR/"*.sql || "$FILE_PATH" == "$REVISIONS_DIR/"*.sql ]]; then
        REASON="Do not manually edit revision files. Edit schema.sql and author a new revision file (or regenerate the seed via the squash workflow if no users depend on the prior chain)."
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
done

exit 0
