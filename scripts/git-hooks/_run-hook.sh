#!/usr/bin/env bash
# Venv-agnostic git hook implementation (committed, version-controlled).
#
# WHY this exists instead of pre-commit's generated wrappers:
# every worktree shares one core.hooksPath, and pre-commit's generated
# wrapper bakes one worktree's absolute venv path into INSTALL_PYTHON.
# When that worktree's venv changes or the worktree is deleted, every
# other worktree's git commit/push routes through a dead interpreter and
# silently degrades to a different global env, so pushes fail with no
# diagnostic.
#
# This implementation resolves the venv at runtime instead of at install
# time. core.hooksPath is set to the RELATIVE path `scripts/git-hooks`,
# which git resolves against the working tree of whichever worktree is
# running the hook, so `git rev-parse --show-toplevel` is that worktree's
# own root and `uv run --project "$ROOT"` always selects that worktree's
# own interpreter. No per-worktree install step; nothing another worktree
# can poison; deleting a worktree cannot affect a sibling.
#
# UV_FROZEN is exported so neither this invocation nor any inner
# `uv run ...` hook entry pre-commit spawns can rewrite uv.lock (a stale
# lock, or a parallel worktree's `uv sync`, would otherwise dirty the
# tree and trip pre-commit's "files were modified by this hook").
#
# The three sibling files (pre-commit, pre-push, commit-msg) are thin
# dispatchers that exec this script with their hook type. The argv shape
# below mirrors pre-commit's own generated wrapper
# (`hook-impl --config=... --hook-type=... --hook-dir ... -- "$@"`), a
# stable public pre-commit CLI contract pinned by the wrapper test.
set -euo pipefail

if [ "$#" -lt 1 ]; then
  echo "git-hooks/_run-hook.sh: missing hook-type argument" >&2
  exit 1
fi

hook_type="$1"
shift

ROOT="$(git rev-parse --show-toplevel)"
export UV_FROZEN=1

# Durable full-output log. A bare `git commit`/`git push` routes the
# entire pre-commit/pre-push stream (the affected-pytest dot output
# alone runs tens of KB) through whatever invoked git; terminals and
# tool output caps truncate that, and the actual failure -- the pytest
# summary or mypy error, which lands at the very END -- scrolls off, so
# the run looks like it produced no diagnostic. Teeing every byte to a
# file in the git dir means the complete output is ALWAYS recoverable
# regardless of any caller's truncation, and on failure the short
# failing tail is re-emitted to stderr so even a clipped terminal shows
# the actionable signal. The log lives under the git dir (per-worktree
# via --git-path), never the working tree, so it cannot dirty
# pre-commit's "files were modified by this hook" check.
LOG_DIR="$(git rev-parse --git-path synthorg-hooks)"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/${hook_type}-last.log"
PREV="$LOG_DIR/${hook_type}-prev.log"
FAILED_MARKER="$LOG_DIR/${hook_type}-FAILED"

# Rotate the previous run's log to ``-prev.log`` BEFORE this run overwrites
# ``-last.log``. Without rotation a re-run (e.g. re-pushing after a failure)
# overwrites the failing log in place, destroying the only diagnostic of
# what went wrong -- so the prior run's full output is always recoverable
# for at least one more cycle.
if [ -f "$LOG" ]; then
  cp -f "$LOG" "$PREV"
fi

set +e
uv run --frozen --project "$ROOT" python -m pre_commit hook-impl \
  --config=.pre-commit-config.yaml --hook-type="$hook_type" \
  --hook-dir "$ROOT/scripts/git-hooks" -- "$@" 2>&1 | tee "$LOG"
status=${PIPESTATUS[0]}
set -e

if [ "$status" -ne 0 ]; then
  {
    echo
    echo "=================================================================="
    echo "git ${hook_type} hook FAILED (exit ${status})."
    echo "Full untruncated output: ${LOG}"
    echo "Previous run's log preserved at: ${PREV}"
    echo "--- failing tail (last 60 lines; read the full log above if needed) ---"
    tail -n 60 "$LOG"
    echo "=================================================================="
  } >&2
  # Drop a failure marker so the Claude PreToolUse guard
  # (check_no_repush_after_failure.sh) blocks a reflexive re-run until the
  # log has been read and the marker cleared. Best-effort; never fail the
  # hook on a marker-write error.
  {
    printf 'hook=%s\nstatus=%s\nlog=%s\nprev=%s\n' \
      "$hook_type" "$status" "$LOG" "$PREV"
  } > "$FAILED_MARKER" 2>/dev/null || true
else
  # Clean run: clear any stale failure marker so the guard stops blocking.
  rm -f "$FAILED_MARKER" 2>/dev/null || true
fi

exit "$status"
