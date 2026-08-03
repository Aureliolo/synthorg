#!/usr/bin/env bash
# PostToolUse hook: re-warm the caches a dependency sync invalidated.
#
# Why this exists:
#   A ``uv sync`` rewrites the interpreter's site-packages, which silently
#   invalidates two separate caches. Neither says so; both are discovered
#   only once something slow is already underway.
#
#   1. The dmypy daemon stays alive but its graph no longer matches, so the
#      next check pays a full cold rebuild -- measured at 124s against 1.4s
#      warm. When that next check is the pre-push hook, a third of the 300s
#      push budget is gone before a single gate has run, and the push reads
#      as mysteriously slow rather than as the dependency change it is.
#   2. typeguard's instrumented bytecode is cached under a tag carrying its
#      own version, so a typeguard bump invalidates every cached file at
#      once. Re-instrumenting costs ~17s per process, and a pytest-xdist run
#      pays it in all 8 workers simultaneously.
#
#   The two differ in one way that matters here: the mypy re-warm restores a
#   warm state that already existed and costs ~2.5GB resident, so it refuses
#   unless a daemon is already up. The typeguard warm only writes .pyc files,
#   costs no resident memory, and is what every later test run reads, so it
#   runs unconditionally.
#
# Why PostToolUse on Bash rather than SessionStart:
#   Warming is not free: the main daemon holds ~2.5GB resident (the separate
#   ``scripts/`` daemon, which ``--rewarm`` never touches, costs roughly half
#   that again). That is why the worktree helper deliberately refuses to warm
#   at creation and leaves it to the one worktree actually being pushed from.
#   A SessionStart warm would fire in every worktree a session opens and blow
#   past the memory a machine running several of them has spare. The sync is
#   the event that causes the staleness, so it is the event worth reacting to,
#   and ``--rewarm`` additionally refuses unless a daemon is ALREADY resident:
#   it restores a warm state that existed, never creates a new one.
#
# Why detached:
#   The rebuild takes minutes. ``run_affected_mypy.py --warm`` documents that
#   the caller detaches rather than the script backgrounding itself, so a
#   failure still surfaces somewhere. Two mechanisms carry that here: output
#   goes to ``synthorg-hooks/mypy-rewarm-last.log``, and a failed rebuild
#   drops a ``mypy-rewarm-FAILED`` marker that the next ordinary type check
#   reports, so a failure is not merely logged where nobody looks.
#
# Harness divergence worth knowing: under Claude Code the payload carries
# ``tool_response``, so a FAILED sync correctly skips the re-warm. The
# OpenCode plugin's ``runHookScript`` sends only ``tool_input``, so there the
# success check sees no signal and the re-warm runs either way. That is the
# harmless direction (one wasted background rebuild, and only when a daemon is
# already resident), which is why it is tolerated rather than worked around.
#
# Always exits 0. PostToolUse cannot block a tool that already ran, and a
# housekeeping hook must never be the reason an agent's turn fails. But it is
# never SILENT about its own failures: every path that gives up before the log
# file can exist says so on stderr, which the harness does capture. A hook
# that quietly stops working would reintroduce exactly the slow-push mystery
# it exists to remove.

set -euo pipefail

if [[ -t 0 ]]; then
    exit 0
fi

# Guarded in the ``if`` condition so ``set -e`` does not abort while the read
# status stays observable: an I/O error on stdin is a different thing from the
# harness sending nothing, and collapsing the two would hide a real failure.
if ! PAYLOAD=$(cat); then
    printf 'rewarm_caches_after_sync: could not read the hook payload from stdin; no re-warm attempted.\n' >&2
    exit 0
fi
if [[ -z "${PAYLOAD}" ]]; then
    exit 0
fi

# jq is the only hard dependency, and a missing or broken one must not look
# like the ordinary "this command was not a sync" no-op. Left folded together,
# a jq that stopped working would disable this hook permanently and invisibly,
# and every later push would eat the cold rebuild with nothing to explain why.
if ! command -v jq >/dev/null 2>&1; then
    printf 'rewarm_caches_after_sync: jq not found on PATH; cannot inspect the hook payload, so the caches will not be re-warmed after dependency syncs.\n' >&2
    exit 0
fi
if ! COMMAND=$(printf '%s' "${PAYLOAD}" | jq -r '.tool_input.command // ""'); then
    printf 'rewarm_caches_after_sync: jq failed to parse the hook payload; no re-warm attempted.\n' >&2
    exit 0
fi
if [[ -z "${COMMAND}" ]]; then
    exit 0
fi

# ``uv sync`` and ``uv add`` / ``uv remove`` all rewrite the environment the
# daemon's graph was built against. ``uv run`` does not, and is by far the most
# common uv invocation, so it must not match: re-warming on every
# ``uv run pytest`` would rebuild the graph constantly for no reason.
SYNC_REGEX='(^|[[:space:]]|[|&;(])uv[[:space:]]+(sync|add|remove)([[:space:]]|$|[|&;)])'
if ! printf '%s\n' "${COMMAND}" | grep -qE "${SYNC_REGEX}"; then
    exit 0
fi

# A sync that failed left the environment substantially as it was, so the
# graph is still usable and there is nothing worth rebuilding. (A sync that
# died partway can leave partially-applied state; the guard below is a
# best-effort skip, not a guarantee, which is acceptable because the cost of
# guessing wrong is one wasted background rebuild.) An unparseable payload
# falls through to attempting the re-warm, which is the cheaper mistake.
INTERRUPTED=$(printf '%s' "${PAYLOAD}" \
    | jq -r '.tool_response.interrupted // false' 2>/dev/null || echo "false")
IS_ERROR=$(printf '%s' "${PAYLOAD}" \
    | jq -r '.tool_response.isError // .tool_response.is_error // empty' \
    2>/dev/null || echo "")
EXIT_CODE=$(printf '%s' "${PAYLOAD}" \
    | jq -r '.tool_response.exit_code // .tool_response.exitCode // empty' \
    2>/dev/null || echo "")
if [[ "${INTERRUPTED}" == "true" || "${IS_ERROR}" == "true" ]]; then
    exit 0
fi
if [[ -n "${EXIT_CODE}" && "${EXIT_CODE}" != "0" ]]; then
    exit 0
fi

# Derived unconditionally, never from an inherited ``REPO_ROOT``. The sibling
# record_push_throttle.sh honours that override, but it only ever uses the
# result as a prefix for a state file; here it would select the path of the
# script that gets EXECUTED, so a stale value from another worktree would run
# a different checkout's code.
if ! REPO_ROOT_DIR=$(git rev-parse --show-toplevel 2>/dev/null); then
    printf 'rewarm_caches_after_sync: not inside a git work tree; cannot locate the worktree to re-warm.\n' >&2
    exit 0
fi

if ! LOG_DIR=$(git rev-parse --git-path synthorg-hooks 2>/dev/null); then
    printf 'rewarm_caches_after_sync: could not resolve the git dir for the hook log; no re-warm attempted.\n' >&2
    exit 0
fi
# ``--git-path`` answers relative to the caller's cwd, which the harness owns
# and this script never changes. Anchoring to the worktree root is what keeps
# the log beside the marker that run_affected_mypy.py::_rewarm_marker resolves
# against its own repo root, so the stale-failure report names a log that
# actually exists. Git Bash answers with a drive-letter absolute path rather
# than a leading slash, so both spellings count as already-anchored.
if [[ "${LOG_DIR}" != /* && "${LOG_DIR}" != ?:[/\\]* ]]; then
    LOG_DIR="${REPO_ROOT_DIR}/${LOG_DIR}"
fi
if ! mkdir -p "${LOG_DIR}" 2>/dev/null; then
    printf 'rewarm_caches_after_sync: could not create %s; no re-warm attempted.\n' "${LOG_DIR}" >&2
    exit 0
fi
LOG="${LOG_DIR}/mypy-rewarm-last.log"
LOCK="${LOG_DIR}/mypy-rewarm.pid"

# One re-warm at a time per worktree. Two syncs in quick succession would
# otherwise detach two multi-minute rebuilds that queue against the same
# single-threaded daemon and interleave their output into the same truncated
# log, which defeats the log exactly when something unusual is happening.
if [[ -f "${LOCK}" ]]; then
    RUNNING_PID=$(cat "${LOCK}" 2>/dev/null || echo "")
    if [[ -n "${RUNNING_PID}" ]] && kill -0 "${RUNNING_PID}" 2>/dev/null; then
        exit 0
    fi
fi

# ``--project`` rather than a cd: the daemon is per-worktree, and inheriting
# whatever directory the hook process happens to sit in could re-warm a
# sibling checkout's daemon instead of this one.
#
# setsid where available so the rebuild outlives the hook process; nohup is
# the portable fallback (Git Bash on Windows has no setsid).
#
# typeguard first: it is the shorter of the two and its result is what the
# very next test run reads, whereas the mypy graph is only needed at push.
#
# ``--mark-failures`` because this is the detached path: nothing reads the
# exit code, so without a marker a repeatedly-failing warm is invisible and
# every test process silently keeps paying full instrumentation. The dmypy
# half already leaves ``mypy-rewarm-FAILED``; this is its counterpart.
#
# The program text is fixed and the repository path arrives as an argument:
# interpolating it would put a path into shell source, where a single quote
# in a directory name closes the quoting and the detached shell dies before
# either warm runs.
REWARM_CMD='uv run --project "$1" python "$1/scripts/warm_typeguard_cache.py" --quiet --mark-failures; uv run --project "$1" python "$1/scripts/run_affected_mypy.py" --rewarm'
if command -v setsid >/dev/null 2>&1; then
    setsid bash -c "${REWARM_CMD}" rewarm-caches "${REPO_ROOT_DIR}" >"${LOG}" 2>&1 &
else
    nohup bash -c "${REWARM_CMD}" rewarm-caches "${REPO_ROOT_DIR}" >"${LOG}" 2>&1 &
fi
REWARM_PID=$!
printf '%s\n' "${REWARM_PID}" >"${LOCK}"
disown 2>/dev/null || true

exit 0
