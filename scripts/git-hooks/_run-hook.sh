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
    echo "--- failing tail (last 60 lines; read the full log above if needed) ---"
    tail -n 60 "$LOG"
    echo "=================================================================="
  } >&2
fi

exit "$status"
