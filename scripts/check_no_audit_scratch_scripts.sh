#!/usr/bin/env bash
# PreToolUse hook (Edit|Write): block agent-leaked scratch scripts during a
# /codebase-audit run.
#
# Audit subagents have repeatedly leaked helper scripts (audit_*.py,
# find_*.py, verify_*.py, etc.) to the project root and scripts/ despite the
# agent-prompt rule against it. Those trigger Pyright diagnostic floods in the
# main thread and require user cleanup. The skill's own lessons recommend this
# hook.
#
# SCOPED, not blanket: this hook is INERT unless an audit run is active, which
# the skill signals by creating the marker file `_audit/.audit-run-active` in
# Phase 0 and removing it in Phase 7. So normal development is never affected.
# A staleness guard ignores a marker left behind by a crashed run.
#
# Blocks: *.py / *.sh written at the project root, or as a direct child of
# scripts/. Allows: anything under _audit/, and any path deeper than a direct
# scripts/ child.
#
# Exit behavior: allow -> exit 0; block -> emit deny JSON, exit 2.

set -euo pipefail

MARKER="_audit/.audit-run-active"

# Marker-first: if no audit run is active, this hook does nothing. Checked
# before any parsing so normal dev is never impacted by hook internals.
[[ -f "$MARKER" ]] || exit 0

# Staleness guard: a marker older than 12h is treated as a crashed-run
# leftover and ignored (and removed), so it cannot wedge future edits.
if find "$MARKER" -mmin +720 -print 2>/dev/null | grep -q .; then
    rm -f "$MARKER" 2>/dev/null || true
    exit 0
fi

# Fail OPEN on a parse error: this hook is narrow defense-in-depth (the Phase 7
# sweep is the backstop), so a jq hiccup must never block a legitimate write.
FILE=$(jq -r '.tool_input.file_path // ""' 2>/dev/null) || exit 0
[[ -z "$FILE" ]] && exit 0

norm() {
    local p="${1//\\//}"
    if [[ "$p" =~ ^([A-Za-z]):/(.*)$ ]]; then
        local drive
        drive=$(printf '%s' "${BASH_REMATCH[1]}" | tr '[:upper:]' '[:lower:]')
        p="/$drive/${BASH_REMATCH[2]}"
    fi
    # Strip any leading "./" (and repeated "././") so a relative path like
    # "./scratch.py" cannot slip past the anchored deny regexes below.
    while [[ "$p" == "./"* ]]; do p="${p#./}"; done
    printf '%s' "$p"
}

FILE=$(norm "$FILE")
ROOT=$(norm "$(pwd)")

# Reduce to a project-relative path when the file is inside the repo.
REL="$FILE"
case "$FILE" in
    "$ROOT"/*) REL="${FILE#"$ROOT"/}" ;;
esac

# _audit/ is the agents' only legitimate write target -- never block it.
case "$REL" in
    _audit/*) exit 0 ;;
esac

deny() {
    cat <<ENDJSON
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "$1"
  }
}
ENDJSON
    exit 2
}

REASON="Audit run active (_audit/.audit-run-active present): do NOT write helper scripts to the project root or scripts/. Audit agents may only write finding files under _audit/latest/findings/. Use Grep/Glob/Read inline instead. If this is genuine non-audit work, remove the stale marker first."

# Root-level *.py / *.sh (REL has no slash): the classic leak pattern.
if [[ "$REL" =~ ^[^/]+\.(py|sh)$ ]]; then
    deny "$REASON"
fi

# Direct child of scripts/ (scripts/<name>.py|sh, no deeper nesting).
if [[ "$REL" =~ ^scripts/[^/]+\.(py|sh)$ ]]; then
    deny "$REASON"
fi

exit 0
