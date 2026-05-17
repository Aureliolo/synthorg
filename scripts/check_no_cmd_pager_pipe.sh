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

# Match a `git` or `uv` invocation at a command-token boundary,
# followed (anywhere later in the same flattened command) by a pipe
# into a `head`/`tail` token. Nested pipes
# (`uv run pytest | grep x | tail`) must still match, so the pattern
# is: a git/uv token ... a `|` ... `head`/`tail` as the next command
# token. `2>&1 | tail` is covered because the `|` and pager survive
# the redirect.
CMD_REGEX='(^|[[:space:]]|[|&;(])(git|uv)[[:space:]]'
PAGER_REGEX='\|[[:space:]]*(head|tail)([[:space:]]|$)'

if printf '%s\n' "$FLAT" | grep -qE "$CMD_REGEX" \
    && printf '%s\n' "$FLAT" | grep -qE "$PAGER_REGEX"; then
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
