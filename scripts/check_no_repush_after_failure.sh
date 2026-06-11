#!/usr/bin/env bash
# PreToolUse hook: refuse a reflexive ``git push`` immediately after the
# previous pre-push hook FAILED, until its log has been read and the
# failure marker explicitly cleared.
#
# Why this exists:
#   A failed ``git push`` (a red pre-push gate) must be DIAGNOSED, not
#   retried. Re-pushing without reading the failure:
#     1. overwrites the failing log in place (``-last.log`` has no
#        rotation), destroying the only diagnostic of what went wrong; and
#     2. re-runs an expensive multi-minute gate suite for zero new
#        information, and usually fails again on the same cause.
#   The model has done exactly this -- re-pushed a load-induced wall-clock
#   timeout as "flaky" and destroyed the log. This gate makes that
#   impossible: after a pre-push failure, the next push is BLOCKED until
#   the operator has read the preserved log and consciously cleared the
#   marker.
#
# How the marker is set:
#   ``scripts/git-hooks/_run-hook.sh`` writes
#   ``<git-dir>/synthorg-hooks/pre-push-FAILED`` on a failed pre-push run
#   and removes it on a clean run. The failing log is at
#   ``pre-push-last.log`` and the prior run is preserved at
#   ``pre-push-prev.log`` (both written by the same runner).
#
# Behaviour:
#   - Non-push commands: exit 0 (allow).
#   - No marker present: exit 0 (allow) -- the last pre-push was clean
#     (or there hasn't been one).
#   - Marker present: deny. The operator must read the log, fix the root
#     cause, then ``rm`` the marker to authorise the push. Clearing the
#     marker is the deliberate acknowledgement the gate requires; a clean
#     pre-push run also clears it automatically.

set -euo pipefail

# Read PreToolUse JSON envelope. No-op when run interactively.
if [[ -t 0 ]]; then
    exit 0
fi

PAYLOAD=$(cat 2>/dev/null || echo "")
if [[ -z "${PAYLOAD}" ]]; then
    exit 0
fi

# Fail-open if the payload is not parseable JSON, but SURFACE it: this is a
# developer-experience guard, not a security boundary, so a broken envelope
# must not block the user -- yet a silent pass hides a real parse problem
# (unlike check_bash_no_write.sh, which fails closed). Distinguish a jq parse
# failure (warn, then allow) from a successfully-parsed non-command tool call
# (allow silently).
if ! COMMAND=$(printf '%s' "${PAYLOAD}" | jq -r '.tool_input.command // ""' 2>/dev/null); then
    echo "check_no_repush_after_failure: could not parse hook payload as JSON; failing open" >&2
    exit 0
fi
if [[ -z "${COMMAND}" ]]; then
    exit 0
fi

# Robust ``git push`` detection (mirrors check_ci_before_push.sh): the
# boundary class includes shell-quote characters so nested-shell wrapper
# forms (``bash -lc 'git push'``) are caught, and ``-m`` / ``--message``
# argument values are stripped first so a commit-message body mentioning
# "git push" does not trip the regex.
PUSH_REGEX=$'(^|[[:space:]|&;(\'"])git[[:space:]]+push([[:space:]]|$|[|&;)\'"])'
COMMAND_FOR_PUSH_CHECK=$(printf '%s' "${COMMAND}" \
    | tr '\n\r' '  ' \
    | sed -E "
        s/-m[[:space:]]+'[^']*'/-m _MSG_/g
        s/-m[[:space:]]+\"[^\"]*\"/-m _MSG_/g
        s/--message=[[:space:]]*'[^']*'/--message=_MSG_/g
        s/--message=[[:space:]]*\"[^\"]*\"/--message=_MSG_/g
        s/--message[[:space:]]+'[^']*'/--message _MSG_/g
        s/--message[[:space:]]+\"[^\"]*\"/--message _MSG_/g
    ")
if ! printf '%s\n' "${COMMAND_FOR_PUSH_CHECK}" | grep -qE "${PUSH_REGEX}"; then
    exit 0
fi

LOG_DIR=$(git rev-parse --git-path synthorg-hooks 2>/dev/null || echo "")
if [[ -z "${LOG_DIR}" ]]; then
    exit 0
fi
MARKER="${LOG_DIR}/pre-push-FAILED"
if [[ ! -f "${MARKER}" ]]; then
    exit 0
fi

LOG="${LOG_DIR}/pre-push-last.log"
PREV="${LOG_DIR}/pre-push-prev.log"
REASON="git push BLOCKED: the previous pre-push gate FAILED and has not been diagnosed. Re-pushing without reading the log destroys the failing run's output (no log rotation) and re-runs the multi-minute gate suite for nothing -- it almost always fails again on the same cause. Read the full log FIRST: ${LOG} (the run before it is preserved at ${PREV}). Find the FIRST failing gate, root-cause it (a timeout/slow test is a real regression, not 'flaky'; verify in isolation is necessary but NOT sufficient), and FIX it. Only then clear the marker to authorise the push: rm '${MARKER}'. A clean pre-push run also clears it automatically. Do NOT reflexively re-push."
jq -n --arg reason "${REASON}" '{
  hookSpecificOutput: {
    hookEventName: "PreToolUse",
    permissionDecision: "deny",
    permissionDecisionReason: $reason
  }
}'
exit 2
