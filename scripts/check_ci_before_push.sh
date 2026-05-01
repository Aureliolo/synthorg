#!/usr/bin/env bash
# PreToolUse hook: refuse ``git push`` when the current remote
# head on this branch has failing CI checks that have not been
# addressed in the new commits.
#
# Why this exists:
#   /babysit-pr Phase 6 says: "for each entry in
#   ``statusCheckRollup`` with conclusion: FAILURE, capture name
#   and the targetUrl... Extract the run id... gh run view
#   --log-failed". Phase 7 maps CI failures to CRITICAL severity.
#   The Loop Discipline rule "Check CI and external reviewers
#   TOGETHER every cycle" forbids pushing while CI is red.
#
#   In practice the model has shipped a "fix CodeRabbit comments"
#   commit while the prior head's CI was failing on a separate
#   surface (different test, different job), then discovered the
#   miss only after CI re-ran on the new push. This gate enforces
#   the Phase 6 + 7 contract at the push boundary: if the remote
#   head has failing CI, the model MUST address those failures in
#   the local commits-ahead before the push is authorised.
#
# Behaviour:
#   - Non-push commands: exit 0 (allow).
#   - ``git push`` outside an OPEN PR: exit 0 (allow). Outside a
#     review round there is no CI to enforce on.
#   - ``gh`` not on PATH or not authenticated: exit 0 (allow). The
#     gate's job is enforcement, not gating on its own tooling.
#   - Remote head has all-success CI: exit 0 (allow).
#   - Remote head has only IN_PROGRESS / QUEUED checks (still
#     running): exit 0 (allow). The skill's Phase 5 already
#     handles the "wait for CI" branch; the push itself is fine.
#   - Remote head has FAILURE entries: deny with the failing job
#     names, run ids, and the exact ``gh run view`` invocation
#     to inspect. Exception: the deny is downgraded to ALLOW when
#     the local commits-ahead-of-origin look like a real fix
#     attempt (commit subject prefixed with ``fix:`` or
#     ``babysit``). This lets the babysit-loop's standard "fix and
#     push" round flow through; non-fix pushes (chore-only, doc-
#     only, force pushes) are blocked until either the CI failures
#     are addressed or the override flag is set.
#
#     The ``fix:`` heuristic matches the commit subject prefix
#     only (``^fix(\([^)]*\))?:``); a previous variant also
#     accepted any subject containing the word ``babysit``,
#     which let unrelated subjects (``docs: explain babysit
#     flow``) slip through, and is no longer accepted.
#
# Override (one-shot, branch-bound, user-only):
#   ``.claude/state/allow-failing-ci-push.flag`` whose first non-
#   empty line is the current branch name. Consumed on use.
#   ``scripts/check_no_throttle_override_creation.sh`` blocks the
#   model from minting this flag itself.
#
# Tunable: ``SYNTHORG_CI_GATE_DISABLED=1`` env var disables the
#   gate entirely (e.g. when GitHub is down). Use sparingly.

set -euo pipefail

REPO_ROOT_DIR="${REPO_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null || echo .)}"
STATE_DIR="${REPO_ROOT_DIR}/.claude/state"
OVERRIDE_FLAG="${STATE_DIR}/allow-failing-ci-push.flag"

if [[ "${SYNTHORG_CI_GATE_DISABLED:-0}" == "1" ]]; then
    exit 0
fi

# Read PreToolUse JSON envelope. No-op when run interactively.
if [[ -t 0 ]]; then
    exit 0
fi

PAYLOAD=$(cat 2>/dev/null || echo "")
if [[ -z "${PAYLOAD}" ]]; then
    exit 0
fi

COMMAND=$(printf '%s' "${PAYLOAD}" | jq -r '.tool_input.command // ""' 2>/dev/null || echo "")
# Boundary class includes shell-quote characters (' and ") so
# wrapper forms like ``bash -lc 'git push'`` and
# ``sh -c "git push"`` -- where the inner ``git push`` IS the
# command being executed -- are detected. Without the quote
# chars in the boundary the regex would only fire on bare
# ``git push`` and any nested-shell wrapper would silently
# bypass the gate.
PUSH_REGEX=$'(^|[[:space:]|&;(\'"])git[[:space:]]+push([[:space:]]|$|[|&;)\'"])'
# Strip ONLY ``-m`` / ``--message`` argument values before
# matching the push regex. A blanket quote strip would turn
# wrapper invocations like ``bash -lc 'git push'`` and
# ``sh -c "git push"`` into bypass paths -- the inner quoted
# text IS the command being executed in those forms, so it
# must survive the strip. Targeting the commit-message
# argument keeps the false-positive fix in place (a
# ``git commit -m "feat: block git push when..."`` body no
# longer trips the regex) without erasing wrapper payloads.
# Collapse newlines first because sed is line-oriented and
# multi-line ``-m '...'`` bodies would otherwise leak.
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
if [[ -z "${COMMAND}" ]] || ! printf '%s\n' "${COMMAND_FOR_PUSH_CHECK}" | grep -qE "${PUSH_REGEX}"; then
    exit 0
fi

BRANCH=$(git branch --show-current 2>/dev/null || echo "unknown")
if [[ "${BRANCH}" == "unknown" ]]; then
    exit 0
fi

# PR-only enforcement.
if ! command -v gh >/dev/null 2>&1; then
    exit 0
fi
PR_NUMBER="$(gh pr view --json number --jq '.number' 2>/dev/null || echo "")"
PR_STATE="$(gh pr view --json state --jq '.state' 2>/dev/null || echo "")"
if [[ -z "${PR_NUMBER}" ]] || [[ "${PR_STATE}" != "OPEN" ]]; then
    exit 0
fi

# Override check (consume on success, not here -- if we deny, the
# user can write the flag and re-push; the allow path consumes it).
OVERRIDE=0
if [[ -f "${OVERRIDE_FLAG}" ]]; then
    FLAG_BRANCH=$(awk 'NF { print; exit }' "${OVERRIDE_FLAG}" 2>/dev/null || echo "")
    if [[ -n "${FLAG_BRANCH}" && "${FLAG_BRANCH}" == "${BRANCH}" ]]; then
        OVERRIDE=1
    fi
fi

# Fetch the current statusCheckRollup. The remote head is what
# CI is actually running against; the local HEAD might be ahead
# (the about-to-be-pushed commits), and that's expected.
ROLLUP_JSON="$(gh pr view "${PR_NUMBER}" --json statusCheckRollup 2>/dev/null || echo "")"
if [[ -z "${ROLLUP_JSON}" ]]; then
    # GitHub API unreachable / response unparseable: fail OPEN.
    # The push will trigger a fresh CI run anyway.
    exit 0
fi

FAILURE_COUNT=$(printf '%s' "${ROLLUP_JSON}" \
    | jq -r '[.statusCheckRollup[] | select(.conclusion == "FAILURE")] | length' \
    2>/dev/null || echo 0)

if [[ "${FAILURE_COUNT}" -eq 0 ]]; then
    # Either everything's green or there are still IN_PROGRESS /
    # QUEUED entries with no failures yet. Allow.
    if [[ "${OVERRIDE}" -eq 1 ]]; then
        rm -f "${OVERRIDE_FLAG}" 2>/dev/null || true
    fi
    exit 0
fi

# Best-effort fix-attempt detection: if the local commits-ahead of
# ``origin/<branch>`` carry any commit whose subject starts with
# ``fix:`` or ``fix(scope):``, treat the push as a remediation
# push and let it through. The babysit loop ALWAYS uses
# ``fix: babysit round R, M findings (...)`` for its commit
# subject, so the ``fix:`` prefix already covers the loop's normal
# flow. Earlier the regex also matched any subject containing the
# word ``babysit`` anywhere, which let unrelated subjects like
# ``docs: explain babysit flow`` slip through; tightened to a
# strict ``fix:``-prefix match.
FIX_ATTEMPT=0
if AHEAD_SUBJECTS="$(git log --format='%s' "origin/${BRANCH}..HEAD" 2>/dev/null)"; then
    if printf '%s\n' "${AHEAD_SUBJECTS}" \
        | grep -qE '^fix(\([^)]*\))?:'; then
        FIX_ATTEMPT=1
    fi
fi

if [[ "${OVERRIDE}" -eq 1 || "${FIX_ATTEMPT}" -eq 1 ]]; then
    # Allow path. Always print the failures to stderr-equivalent
    # via the deny-style envelope's reason field so the model has
    # them in context for the round even when the push is allowed.
    # We use ``hookSpecificOutput`` with permissionDecision=allow
    # so the message shows up but the push goes through.
    FAIL_SUMMARY=$(printf '%s' "${ROLLUP_JSON}" \
        | jq -r '
            [.statusCheckRollup[]
             | select(.conclusion == "FAILURE")
             | "  - " + .name + " :: " + (.targetUrl // "no targetUrl (rollup or external app)")]
            | join("\n")
        ' 2>/dev/null || echo "")
    NOTICE="CI gate: PR #${PR_NUMBER} has ${FAILURE_COUNT} failing check(s) on the previous head. The push is authorised because the local commits look like a fix attempt (subject prefixed with 'fix:' or contains 'babysit'). Make sure the new commits actually address ALL of these failures before relying on the next CI run:\n${FAIL_SUMMARY}\n\nTo inspect each: gh run list --branch ${BRANCH} --json databaseId,name,conclusion --jq '.[]|select(.conclusion==\"failure\")' then gh run view <id> --log-failed."
    if [[ "${OVERRIDE}" -eq 1 ]]; then
        rm -f "${OVERRIDE_FLAG}" 2>/dev/null || true
    fi
    # Print the notice to stderr so it surfaces in the harness
    # output without changing the permission decision.
    printf '%b\n' "${NOTICE}" >&2
    exit 0
fi

# Deny: failures exist and the local commits do not look like a
# fix attempt.
FAIL_SUMMARY=$(printf '%s' "${ROLLUP_JSON}" \
    | jq -r '
        [.statusCheckRollup[]
         | select(.conclusion == "FAILURE")
         | "  - " + .name + " :: " + (.targetUrl // "no targetUrl (rollup or external app)")]
        | join("\n")
    ' 2>/dev/null || echo "")
REASON="Push to '${BRANCH}' blocked: PR #${PR_NUMBER} has ${FAILURE_COUNT} failing CI check(s) on the current remote head, and the commits-ahead do not look like a fix attempt (no commit subject starting with 'fix:'). /babysit-pr Phase 6 + 7 require these failures to be diagnosed and addressed BEFORE the next push -- pushing chore / docs / unrelated work over a red branch wastes the CI + reviewer cycle and lets real failures hide. Failing checks: ${FAIL_SUMMARY}. Investigate with: gh run list --branch ${BRANCH} --json databaseId,name,conclusion then gh run view <id> --log-failed for each FAILURE entry. Fix the underlying issues, commit with a 'fix:' subject (or 'fix: babysit round N, ...' for the loop), and retry. Override (rare): the user (not the model) writes \`printf '%s\\n' \"\$(git branch --show-current)\" > .claude/state/allow-failing-ci-push.flag\` and re-runs git push. The flag is consumed on use."
jq -n --arg reason "${REASON}" '{
  hookSpecificOutput: {
    hookEventName: "PreToolUse",
    permissionDecision: "deny",
    permissionDecisionReason: $reason
  }
}'
exit 2
