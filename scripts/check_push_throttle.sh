#!/usr/bin/env bash
# PreToolUse hook: enforce a minimum interval between consecutive
# ``git push`` invocations on the same branch.
#
# Why this exists:
#   Each push triggers full CI + reviewer (CodeRabbit) re-runs that
#   cost real money / quota. Pushing twice in a row inside one
#   review round (e.g. a rebase-only push followed minutes later by
#   the actual fix-bundle push) doubles that cost for zero benefit
#   over a single batched push. The model has repeatedly violated
#   the "one push per round" rule from
#   ``feedback_push_and_review_discipline.md`` despite the memory
#   being present, so this script is the hard backstop.
#
# Behaviour:
#   - Non-push commands: exit 0 (allow).
#   - First push to a branch in this repo: exit 0 (allow), record
#     timestamp + branch in ``.claude/state/last-push.json``.
#   - Push within < THROTTLE_MIN minutes of the last push to the
#     SAME branch: emit blocking JSON with a clear override
#     instruction, exit 2.
#   - Override: a one-shot flag file
#     ``.claude/state/allow-double-push.flag`` must exist AND its
#     first non-empty line must equal the current branch name
#     EXACTLY. The flag is CONSUMED (deleted) on successful use, so
#     each override authorises exactly one push. The model cannot
#     create the flag itself via Write (the path is under
#     ``.claude/state/`` which is gitignored runtime state, not a
#     source-tree edit) -- the user must create the flag in their
#     own shell. Recommended:
#       printf '%s\n' "$(git branch --show-current)" \
#           >.claude/state/allow-double-push.flag && git push <args>
#   - Threshold tuneable via ``SYNTHORG_PUSH_THROTTLE_MIN`` (env
#     or repo-level default below). Default: 5 minutes.

set -euo pipefail

# Validate ``SYNTHORG_PUSH_THROTTLE_MIN`` before arithmetic: a
# non-integer value (e.g. user types ``5min`` by mistake) would
# crash the script under ``set -e`` and unexpectedly DENY the push,
# which is the opposite of the safe default (allow on tooling
# failure). Coerce to the project default of 5 if invalid.
THROTTLE_MIN_RAW="${SYNTHORG_PUSH_THROTTLE_MIN:-5}"
if [[ "${THROTTLE_MIN_RAW}" =~ ^[0-9]+$ ]] && [[ "${THROTTLE_MIN_RAW}" -gt 0 ]]; then
    THROTTLE_MIN="${THROTTLE_MIN_RAW}"
else
    THROTTLE_MIN=5
fi
REPO_ROOT_DIR="${REPO_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null || echo .)}"
STATE_DIR="${REPO_ROOT_DIR}/.claude/state"
STATE_FILE="${STATE_DIR}/last-push.json"
OVERRIDE_FLAG="${STATE_DIR}/allow-double-push.flag"

# Read tool_input.command from JSON stdin (Claude Code / OpenCode
# both pass the tool payload as JSON on stdin for PreToolUse hooks).
# If stdin is not JSON (e.g. someone runs the script standalone),
# treat the input as a no-op and exit 0.
COMMAND=""
if [[ ! -t 0 ]]; then
    COMMAND=$(jq -r '.tool_input.command // ""' 2>/dev/null || echo "")
fi

# Only act on git push commands. Match anywhere in compound commands
# (``a && git push && b``) but require a real word boundary so we do
# not match ``git push-tag-helper`` or comments.
if [[ -z "$COMMAND" ]] || ! printf '%s\n' "$COMMAND" | grep -qE '\bgit[[:space:]]+push\b'; then
    exit 0
fi

BRANCH=$(git branch --show-current 2>/dev/null || echo "unknown")
NOW=$(date -u +%s)

# Only throttle when an OPEN PR exists for the current branch. The
# rationale for the rule is "every push burns CI + reviewer
# rate-limit budget"; outside a PR there is no reviewer to burn and
# no CI gate (most workflows are ``pull_request``-triggered), so a
# throttle would just slow down ordinary feature work for no
# benefit. We check the PR ``state`` field explicitly (not just
# ``gh``'s exit status, which could change semantics across
# versions): a CLOSED or MERGED PR for the branch is not a review
# round either. Anything other than literal ``OPEN`` falls
# through. If ``gh`` is unavailable or unauthenticated we fail
# OPEN (allow the push) -- the script's job is to prevent
# reviewer-rate-limit burn, not to gate developer pushes when the
# tooling is broken.
if ! command -v gh >/dev/null 2>&1; then
    exit 0
fi
PR_STATE="$(gh pr view --json state --jq '.state' 2>/dev/null || echo "")"
if [[ "${PR_STATE}" != "OPEN" ]]; then
    # No OPEN PR for ``${BRANCH}``: not in a review round.
    exit 0
fi

# Failing to create the runtime state dir is a tooling problem, not
# a "user pushed too soon" problem. Fail OPEN -- a broken filesystem
# state must never block a push (CodeRabbit, 2026-04-26).
if ! mkdir -p "${STATE_DIR}" 2>/dev/null; then
    exit 0
fi

# Check the override flag. The flag must exist AND its first
# non-empty line must match the current branch EXACTLY. This
# prevents a stale flag from a previous branch silently authorising
# pushes elsewhere, and prevents an accidental ``touch`` from
# bypassing the gate.
OVERRIDE=0
if [[ -f "${OVERRIDE_FLAG}" ]]; then
    FLAG_BRANCH=$(awk 'NF { print; exit }' "${OVERRIDE_FLAG}" 2>/dev/null || echo "")
    if [[ -n "${FLAG_BRANCH}" && "${FLAG_BRANCH}" == "${BRANCH}" ]]; then
        OVERRIDE=1
    fi
fi

# Read previous push record. Tolerate a missing or malformed file.
LAST_TS=0
LAST_BRANCH=""
if [[ -f "${STATE_FILE}" ]]; then
    LAST_TS=$(jq -r '.timestamp // 0' "${STATE_FILE}" 2>/dev/null || echo 0)
    LAST_BRANCH=$(jq -r '.branch // ""' "${STATE_FILE}" 2>/dev/null || echo "")
fi

# A syntactically valid but corrupt state file (e.g. someone wrote
# ``"timestamp":"oops"``) would yield a non-numeric LAST_TS and
# crash the next arithmetic expansion under ``set -e``, which
# would unexpectedly DENY the push. Coerce to 0 if non-numeric so
# the script fails OPEN (the script's job is to throttle, not to
# gate on its own state-file health).
if ! [[ "${LAST_TS}" =~ ^[0-9]+$ ]]; then
    LAST_TS=0
fi

# Guard against a future-dated LAST_TS (clock rollback or
# corrupted state). Without this, ``DELTA = NOW - LAST_TS`` is
# negative and the ``DELTA < THROTTLE_SEC`` test below succeeds
# trivially, blocking the push instead of failing OPEN
# (CodeRabbit, 2026-04-26). Reset to 0 so the gate behaves as if
# this were a first push.
if [[ "${LAST_TS}" -gt "${NOW}" ]]; then
    LAST_TS=0
fi

DELTA=$(( NOW - LAST_TS ))
THROTTLE_SEC=$(( THROTTLE_MIN * 60 ))

if [[ "${OVERRIDE}" -eq 0 && "${LAST_BRANCH}" == "${BRANCH}" && "${LAST_TS}" -gt 0 && "${DELTA}" -lt "${THROTTLE_SEC}" ]]; then
    REMAINING=$(( THROTTLE_SEC - DELTA ))
    REMAINING_MIN=$(( (REMAINING + 59) / 60 ))
    LAST_HUMAN=$(date -u -d "@${LAST_TS}" +"%H:%M:%SZ" 2>/dev/null || date -ur "${LAST_TS}" +"%H:%M:%SZ" 2>/dev/null || echo "${LAST_TS}")
    REASON="Push to '${BRANCH}' blocked: previous push was at ${LAST_HUMAN} (${DELTA}s ago). Minimum interval is ${THROTTLE_MIN} minutes; wait ~${REMAINING_MIN} more min and batch any pending fixes into ONE push. To override, the user (not the model) must run, in their own shell, the documented two-step command from .claude/hookify.block-double-push.md (write the current branch name into .claude/state/allow-double-push.flag, then re-run git push). The flag is consumed on use; each override authorises exactly one push. Each push triggers full CI + CodeRabbit re-runs; doubling that costs real money."
    # Build the JSON via ``jq -n --arg`` so REASON contents (e.g. an
    # exotic branch name with quotes / backticks / newlines) cannot
    # break the output structure and cause the hook contract to
    # parse-fail. Falling back to a hand-written here-doc would
    # require a bespoke escape pass and was the prior bug.
    jq -n --arg reason "${REASON}" '{
      hookSpecificOutput: {
        hookEventName: "PreToolUse",
        permissionDecision: "deny",
        permissionDecisionReason: $reason
      }
    }'
    exit 2
fi

# Successful authorisation: consume the override flag (one-shot).
if [[ "${OVERRIDE}" -eq 1 ]]; then
    rm -f "${OVERRIDE_FLAG}"
fi

# Record this push (allowed or override). We write atomically via a
# temp file in the same dir to survive a crash mid-write. Every step
# fails OPEN: the script's job is throttling, not gating on its own
# state-file health. JSON is generated via ``jq -n --arg`` so a
# branch name containing ``"`` cannot produce malformed JSON
# (CodeRabbit, 2026-04-26).
TMP_FILE=$(mktemp "${STATE_DIR}/last-push.json.XXXXXX" 2>/dev/null || echo "")
if [[ -z "${TMP_FILE}" ]]; then
    exit 0
fi
if ! jq -n \
    --argjson timestamp "${NOW}" \
    --arg branch "${BRANCH}" \
    --argjson override "${OVERRIDE}" \
    '{timestamp: $timestamp, branch: $branch, override: $override}' \
    >"${TMP_FILE}" 2>/dev/null; then
    rm -f "${TMP_FILE}"
    exit 0
fi
if ! mv -f "${TMP_FILE}" "${STATE_FILE}" 2>/dev/null; then
    rm -f "${TMP_FILE}"
    exit 0
fi

exit 0
