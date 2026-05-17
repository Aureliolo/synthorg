#!/usr/bin/env bash
# PreToolUse(Bash) hook: block any `git ...` or `uv ...` command
# whose output is piped into `head` or `tail`.
#
# Why this exists:
#   Piping `git push` / `git commit` through `head`/`tail` truncates
#   the pre-commit / pre-push hook output -- the exact ruff / mypy /
#   pytest / gate failure that rejected the push scrolls off, so the
#   model cannot see WHY it failed and "re-runs to capture the tail".
#   Each rerun re-executes the full, expensive affected-test + mypy
#   + gate suite for zero new information.
#
#   The same masking applies to `uv run pytest|mypy|ruff ... |
#   head/tail`: a truncated test run hides the failing assertion /
#   the mypy error / the ruff diagnostic, so the failure looks like
#   "no logs" and the run is repeated. The whole point of running
#   the command un-piped is that the FIRST run's failure is fully
#   visible -- there is never a reason to rerun with a different
#   `-n`. If the output is long, read it fully from the un-piped
#   run; do not paginate it away.
#
# Modes: JSON stdin -> inspect command; no stdin -> pass.
set -euo pipefail

RAW="$(cat)"
# No stdin (pre-commit, not a tool call): not applicable, pass.
if [[ -z "${RAW//[[:space:]]/}" ]]; then
    exit 0
fi
# stdin present: it MUST parse. A malformed envelope is an unknown
# state, not "no opinion" -- fail closed so a corrupted/truncated
# payload cannot silently bypass the gate.
if ! COMMAND=$(printf '%s' "$RAW" | jq -r '.tool_input.command // empty' 2>/dev/null); then
    echo "BLOCKED: malformed PreToolUse JSON envelope; gate fails closed." >&2
    exit 2
fi

if [[ -z "$COMMAND" ]]; then
    exit 0
fi

# Collapse newlines so a multi-line command is matched as one
# pipeline string (the pipe + pager can straddle a line break).
FLAT=$(printf '%s' "$COMMAND" | tr '\n\r' '  ')

# Split FLAT into statement segments on `;`, `&&`, `||` (each becomes
# its own line), then require a `git`/`uv` token AND a pipe into
# `head`/`tail` WITHIN THE SAME segment. Two independent global greps
# would false-positive on `git status; ls | tail` (git in one
# statement, pager in an unrelated one). A single `&` is deliberately
# NOT a split point: `git push 2>&1 | tail` must stay one segment and
# still be denied, so the between-token gap is `.*` (newlines are the
# only segment boundary after the split, and grep is line-oriented).
# Nested pipes (`uv run pytest | grep x | tail`) stay within one
# segment and still match.
SEGMENTS=$(printf '%s' "$FLAT" | sed -E 's/&&|\|\||;/\n/g')
CMD_PAGER_REGEX='(^|[[:space:]]|[|&(])(git|uv)[[:space:]].*\|[[:space:]]*(head|tail)([[:space:]]|$)'

if printf '%s\n' "$SEGMENTS" | grep -qE "$CMD_PAGER_REGEX"; then
    cat <<'ENDJSON'
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "BLOCKED: piping `git`/`uv` through `head`/`tail` truncates the output -- the pre-commit/pre-push hook failure, or the pytest/mypy/ruff diagnostic, scrolls off and the run looks like it produced 'no logs'. Run the command BARE (no pipe). The first run's full output IS the signal -- do NOT rerun with a different -n; re-running re-executes the entire expensive suite for zero new information. If the output is long, read it fully from the un-piped run."
  }
}
ENDJSON
    exit 2
fi
