#!/usr/bin/env bash
# PreToolUse hook (Edit|Write): block client-side state persistence in the web
# dashboard BEFORE the write lands.
#
# The pre-commit gate scripts/check_no_client_state_persistence.py catches
# localStorage / sessionStorage / indexedDB access and zustand `persist`
# middleware after the fact; this hook fires on the Edit/Write tool call itself
# so the violation never reaches disk. The dashboard is a pure API consumer:
# the backend is the single source of truth (web/CLAUDE.md).
#
# Scope: web/src/**/*.ts(x), excluding test/story files and the small allowlist
# of non-domain transient transport/UX (auth + CSRF shim, build-version check,
# the test storage shim) and per-device ephemeral state (canvas viewport,
# in-progress form drafts). Keep this allowlist in lockstep with the ALLOWLIST
# in scripts/check_no_client_state_persistence.py.
#
# Exit behaviour:
#   - Clean (or out of scope): exit 0 (allow)
#   - Client storage detected: print JSON deny envelope, exit 2

set -euo pipefail

INPUT=$(cat || true)
if [[ -z "$INPUT" ]]; then
    exit 0
fi

FILE_PATH=$(jq -r '.tool_input.file_path // ""' <<<"$INPUT" 2>/dev/null || true)
if [[ -z "$FILE_PATH" ]]; then
    exit 0
fi

NORM=${FILE_PATH//\\//}

# Only web dashboard TypeScript sources are in scope.
case "$NORM" in
    */web/src/*.ts|*/web/src/*.tsx|web/src/*.ts|web/src/*.tsx) ;;
    *) exit 0 ;;
esac

# Test, story, and test-infra files are exempt.
case "$NORM" in
    *__tests__*|*.test.*|*.stories.*|*test-setup*|*test-infra*) exit 0 ;;
esac

# Allowlist: non-domain transient transport/UX + per-device ephemeral state.
# (`*` matches `/` in a case pattern, so `*web/src/...` covers both the
# repo-relative and absolute forms of the path.)
case "$NORM" in
    *web/src/utils/app-version.ts) exit 0 ;;
    *web/src/api/client.ts) exit 0 ;;
    *web/src/storage-shim.ts) exit 0 ;;
    *web/src/hooks/use-unsaved-changes-guard.ts) exit 0 ;;
    *web/src/pages/WorkflowEditorPage.tsx) exit 0 ;;
    *web/src/pages/org/useOrgChartController.ts) exit 0 ;;
esac

CONTENT=$(jq -r '.tool_input.content // .tool_input.new_string // ""' <<<"$INPUT" 2>/dev/null || true)
if [[ -z "$CONTENT" ]]; then
    exit 0
fi

# Member- or bracket-access storage usage, or zustand persist middleware import.
# This is a fast edit-time first line of defence; the pre-push gate
# (scripts/check_no_client_state_persistence.py) is the authoritative check --
# it strips comments and matches on word boundaries, so it also catches a bare
# ``const x = localStorage`` alias that this raw-content grep deliberately does
# not (matching a bare token here would false-positive on prose mentions).
if grep -qE '(localStorage|sessionStorage)(\.|\[)|\bindexedDB\b' <<<"$CONTENT" \
    || grep -qE "persist.*zustand/middleware|zustand/middleware.*persist" <<<"$CONTENT"; then
    REASON="Client-side state persistence is forbidden in the web dashboard (it is a pure API consumer; the backend is the single source of truth -- see web/CLAUDE.md). Move this state to a backend settings namespace and hydrate it via the REST API on mount. For genuinely per-device transient UX (canvas viewport, in-progress drafts), add the file to the allowlist in scripts/check_no_client_state_persistence.py AND this hook with a documented reason."
    jq -nc \
        --arg reason "$REASON" \
        '{hookSpecificOutput: {hookEventName: "PreToolUse", permissionDecision: "deny", permissionDecisionReason: $reason}}'
    exit 2
fi

exit 0
