#!/usr/bin/env bash
# PostToolUse hook: re-warm this worktree's mypy daemon after a dependency
# sync invalidated its resident build graph.
#
# Why this exists:
#   A ``uv sync`` rewrites the interpreter's site-packages. The dmypy daemon
#   stays alive but its graph no longer matches, so the next check silently
#   pays a full cold rebuild -- measured at 124s against 1.4s warm. When that
#   next check is the pre-push hook, a third of the 300s push budget is gone
#   before a single gate has run, and the push reads as mysteriously slow
#   rather than as the dependency change it actually is.
#
# Why PostToolUse on Bash rather than SessionStart:
#   Warming is not free: each daemon holds ~2.5GB resident, which is why the
#   worktree helper deliberately refuses to warm at creation and leaves it to
#   the one worktree actually being pushed from. A SessionStart warm would
#   fire in every worktree a session ever opens and blow past the memory a
#   machine running several of them has spare. The sync is the event that
#   causes the staleness, so it is the event worth reacting to, and
#   ``--rewarm`` additionally refuses unless a daemon is ALREADY resident --
#   it restores a warm state that existed, never creates a new one.
#
# Why detached:
#   The rebuild takes minutes. ``run_affected_mypy.py --warm`` documents that
#   the caller detaches rather than the script backgrounding itself, so a
#   failure still surfaces somewhere: output goes to the same
#   ``synthorg-hooks/`` log directory the git hooks tee into, and can be read
#   after the fact rather than being discarded.
#
# Always exits 0. PostToolUse cannot block a tool that already ran, and a
# housekeeping hook must never be the reason an agent's turn fails.

set -euo pipefail

if [[ -t 0 ]]; then
    exit 0
fi

PAYLOAD=$(cat 2>/dev/null || echo "")
if [[ -z "${PAYLOAD}" ]]; then
    exit 0
fi

COMMAND=$(printf '%s' "${PAYLOAD}" | jq -r '.tool_input.command // ""' 2>/dev/null || echo "")
if [[ -z "${COMMAND}" ]]; then
    exit 0
fi

# ``uv sync`` and ``uv add`` / ``uv remove`` / ``uv lock --upgrade`` all
# rewrite the environment the daemon's graph was built against. ``uv run``
# does not, and is by far the most common uv invocation, so it must not
# match: re-warming on every ``uv run pytest`` would rebuild the graph
# constantly for no reason.
SYNC_REGEX='(^|[[:space:]]|[|&;(])uv[[:space:]]+(sync|add|remove)([[:space:]]|$|[|&;)])'
if ! printf '%s\n' "${COMMAND}" | grep -qE "${SYNC_REGEX}"; then
    exit 0
fi

# A sync that failed left the old environment in place, so the graph is
# still valid and there is nothing to re-warm. Mirrors the failure-signal
# parsing in ``record_push_throttle.sh``; an unparseable payload falls
# through to doing nothing, which costs one slow push at worst.
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

REPO_ROOT_DIR="${REPO_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null || echo "")}"
if [[ -z "${REPO_ROOT_DIR}" ]]; then
    exit 0
fi

LOG_DIR="$(git rev-parse --git-path synthorg-hooks 2>/dev/null || echo "")"
if [[ -z "${LOG_DIR}" ]] || ! mkdir -p "${LOG_DIR}" 2>/dev/null; then
    exit 0
fi
LOG="${LOG_DIR}/mypy-rewarm-last.log"

# ``--project`` rather than a cd: the daemon is per-worktree, and inheriting
# whatever directory the hook process happens to sit in could re-warm a
# sibling checkout's daemon instead of this one.
#
# setsid where available so the rebuild outlives the hook process; nohup is
# the portable fallback (Git Bash on Windows has no setsid).
if command -v setsid >/dev/null 2>&1; then
    setsid uv run --project "${REPO_ROOT_DIR}" \
        python "${REPO_ROOT_DIR}/scripts/run_affected_mypy.py" --rewarm \
        >"${LOG}" 2>&1 &
else
    nohup uv run --project "${REPO_ROOT_DIR}" \
        python "${REPO_ROOT_DIR}/scripts/run_affected_mypy.py" --rewarm \
        >"${LOG}" 2>&1 &
fi
disown 2>/dev/null || true

exit 0
