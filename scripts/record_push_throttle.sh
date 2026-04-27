#!/usr/bin/env bash
# PostToolUse hook: record a successful ``git push`` for the
# throttle window owned by ``scripts/check_push_throttle.sh``.
#
# Why split into pre/post:
#   The original throttle implementation recorded the timestamp in
#   the PreToolUse hook, before the bash command actually ran. A
#   sibling PreToolUse hook (mypy, eslint, ruff format, ...) that
#   rejected the push still reached this script first, so the
#   timestamp ticked even though no push had hit the remote and no
#   CI / reviewer cycle had been triggered. The next legitimate
#   push attempt was then throttled for "doubling reviewer cost"
#   that did not in fact happen.
#
#   Splitting the record half into PostToolUse closes that loop:
#     * If a sibling PreToolUse hook denied the bash, the tool
#       never executes and PostToolUse never fires -- the
#       timestamp is never written.
#     * If the bash ran but ``git push`` exited non-zero (remote
#       rejected non-fast-forward, network error, ...) -- no
#       webhook fired, no CI started, no reviewer was paged -- we
#       skip the record so the dev can retry without waiting.
#     * Only a real ``git push`` that exited 0 ticks the
#       timestamp, which is what the throttle is meant to protect
#       against.
#
# Behaviour:
#   - Non-push commands: exit 0.
#   - ``git push`` with non-zero exit code or interrupted: exit 0
#     (do not record).
#   - ``git push`` with exit code 0: write the timestamp + branch
#     to ``.claude/state/last-push.json`` atomically via mktemp +
#     mv. Every failure mode along the way exits 0 -- the script's
#     job is throttling, not gating on its own state-file health.
#   - PostToolUse cannot block the tool that already ran. We
#     emit a plain ``exit 0`` regardless; the JSON envelope is
#     unused for this hook.

set -euo pipefail

REPO_ROOT_DIR="${REPO_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null || echo .)}"
STATE_DIR="${REPO_ROOT_DIR}/.claude/state"
STATE_FILE="${STATE_DIR}/last-push.json"

# Read the PostToolUse JSON envelope from stdin. Falls back to a
# no-op if stdin is empty or non-JSON (e.g. someone runs the
# script standalone for inspection).
if [[ -t 0 ]]; then
    exit 0
fi

PAYLOAD=$(cat 2>/dev/null || echo "")
if [[ -z "${PAYLOAD}" ]]; then
    exit 0
fi

COMMAND=$(printf '%s' "${PAYLOAD}" | jq -r '.tool_input.command // ""' 2>/dev/null || echo "")

# Match ``git push`` with the same regex as the PreToolUse partner
# so the two hooks agree on which commands count.
PUSH_REGEX='(^|[[:space:]]|[|&;(])git[[:space:]]+push([[:space:]]|$|[|&;)])'
if [[ -z "${COMMAND}" ]] || ! printf '%s\n' "${COMMAND}" | grep -qE "${PUSH_REGEX}"; then
    exit 0
fi

# Determine whether the push actually succeeded. Different harness
# versions surface the outcome under slightly different field
# names; check the common shapes:
#   * ``tool_response.exit_code`` / ``.exitCode`` -- numeric
#   * ``tool_response.isError`` / ``.is_error`` -- boolean
#   * ``tool_response.success`` -- boolean
#   * ``tool_response.interrupted`` -- boolean (always means fail)
# Treat the push as failed if ANY of those signal failure. If we
# cannot parse any of them, fall through to the conservative
# default of NOT recording -- this is symmetric with the script's
# fail-OPEN philosophy and prevents a parser regression from
# silently re-introducing the "ticked on rejection" bug.
EXIT_CODE=$(printf '%s' "${PAYLOAD}" \
    | jq -r '.tool_response.exit_code // .tool_response.exitCode // empty' \
    2>/dev/null || echo "")
IS_ERROR=$(printf '%s' "${PAYLOAD}" \
    | jq -r '.tool_response.isError // .tool_response.is_error // empty' \
    2>/dev/null || echo "")
SUCCESS_FIELD=$(printf '%s' "${PAYLOAD}" \
    | jq -r '.tool_response.success // empty' \
    2>/dev/null || echo "")
INTERRUPTED=$(printf '%s' "${PAYLOAD}" \
    | jq -r '.tool_response.interrupted // false' \
    2>/dev/null || echo "false")

if [[ "${INTERRUPTED}" == "true" ]]; then
    exit 0
fi
if [[ "${IS_ERROR}" == "true" ]]; then
    exit 0
fi
if [[ -n "${SUCCESS_FIELD}" && "${SUCCESS_FIELD}" != "true" ]]; then
    exit 0
fi
if [[ -n "${EXIT_CODE}" ]] && [[ "${EXIT_CODE}" != "0" ]]; then
    exit 0
fi
# At this point: no failure signal observed. We treat the push as
# successful only if AT LEAST ONE positive signal is present (a
# numeric ``exit_code == 0``, or ``isError`` *explicitly* false
# plus a ``stdout`` field, or an explicit ``success == true``).
# Without that, exit 0 without recording -- the cost of skipping
# the record is one un-throttled push; the cost of recording on a
# malformed payload is the very bug we are trying to fix.
#
# IS_ERROR is parsed with ``// empty`` so an absent / parse-failed
# field shows up as the empty string and CANNOT slip into the
# ``isError == false`` positive-signal branch. Earlier the default
# was ``false``, which let a malformed payload that happened to
# carry a ``stdout`` key trigger a record write.
HAS_POSITIVE_SIGNAL=0
if [[ "${EXIT_CODE}" == "0" ]]; then
    HAS_POSITIVE_SIGNAL=1
fi
if [[ "${SUCCESS_FIELD}" == "true" ]]; then
    HAS_POSITIVE_SIGNAL=1
fi
HAS_STDOUT=$(printf '%s' "${PAYLOAD}" \
    | jq -r '.tool_response | has("stdout")' \
    2>/dev/null || echo "false")
if [[ "${IS_ERROR}" == "false" && "${HAS_STDOUT}" == "true" ]]; then
    HAS_POSITIVE_SIGNAL=1
fi
if [[ "${HAS_POSITIVE_SIGNAL}" -eq 0 ]]; then
    exit 0
fi

BRANCH=$(git branch --show-current 2>/dev/null || echo "unknown")
NOW=$(date -u +%s)

# PR-only throttle semantics: only record the timestamp during an
# active review round (i.e. an OPEN PR exists for this branch).
# The PreToolUse partner short-circuits when no OPEN PR exists, so
# recording outside a PR would just leave a stale timestamp that
# would (incorrectly) throttle the *first* push after the PR opens.
# Match the partner's PR detection so the two halves agree on which
# pushes count. ``gh`` missing or unauthenticated falls through to
# "no record" -- the script's job is throttling, not gating on its
# own tooling health.
if ! command -v gh >/dev/null 2>&1; then
    exit 0
fi
PR_STATE="$(gh pr view --json state --jq '.state' 2>/dev/null || echo "")"
if [[ "${PR_STATE}" != "OPEN" ]]; then
    exit 0
fi

if ! mkdir -p "${STATE_DIR}" 2>/dev/null; then
    exit 0
fi

TMP_FILE=$(mktemp "${STATE_DIR}/last-push.json.XXXXXX" 2>/dev/null || echo "")
if [[ -z "${TMP_FILE}" ]]; then
    exit 0
fi
if ! jq -n \
    --argjson timestamp "${NOW}" \
    --arg branch "${BRANCH}" \
    '{timestamp: $timestamp, branch: $branch}' \
    >"${TMP_FILE}" 2>/dev/null; then
    rm -f "${TMP_FILE}" 2>/dev/null || true
    exit 0
fi
if ! mv -f "${TMP_FILE}" "${STATE_FILE}" 2>/dev/null; then
    rm -f "${TMP_FILE}" 2>/dev/null || true
    exit 0
fi

exit 0
