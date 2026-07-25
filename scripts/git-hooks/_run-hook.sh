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

# Fail with a named diagnosis instead of git's opaque "this operation must
# be run in a work tree", which is what `rev-parse --show-toplevel` below
# emits once core.bare is set and which reads like the whole toolchain is
# broken. core.bare is a SHARED setting living in the common .git/config, so
# a single stray `git init --bare` that ran with GIT_DIR inherited from a
# hook environment flips it for the main repo and EVERY worktree at once.
# Nothing in this repo does that (both `--bare` call sites scrub GIT_*), so
# the cause is external and the useful response is to name it on sight.
# ``--local`` so a stray GLOBAL core.bare (someone's ~/.gitconfig) cannot block
# an ordinary checkout: the corruption we detect is written into the repo's own
# shared config by an inherited-GIT_DIR ``git init --bare``, which is exactly the
# local scope. ``--bool`` canonicalises git's truthy spellings (true/yes/on/1)
# so a non-``true`` literal cannot slip a bare repo past the guard.
if [ "$(git config --local --bool --get core.bare 2>/dev/null || true)" = "true" ]; then
  {
    echo "core.bare is true, so this repository has no work tree and every"
    echo "git command here will fail. That is a corrupted setting, not a"
    echo "normal state for a checkout with files in it."
    echo
    echo "Fix:  git config core.bare false"
    echo
    echo "Cause: some process ran 'git init --bare' with GIT_DIR pointing at"
    echo "this repo (a pre-push hook exports GIT_DIR, so a tool spawned from"
    echo "one inherits it). The setting is shared, so all worktrees broke"
    echo "together and all recover together."
  } >&2
  exit 1
fi

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

# A push is held to a five-minute budget. Without a recorded duration a
# gate-scope regression is only ever felt, never seen, so every run is
# timed and a run over budget says so loudly enough to be acted on.
# Overridable so a test can force the over-budget branch without waiting
# out the real ceiling.
: "${BUDGET_SECONDS:=300}"
started_at=$SECONDS

set +e
uv run --frozen --project "$ROOT" python -m pre_commit hook-impl \
  --config=.pre-commit-config.yaml --hook-type="$hook_type" \
  --hook-dir "$ROOT/scripts/git-hooks" -- "$@" 2>&1 | tee "$LOG"
status=${PIPESTATUS[0]}

# Still inside the fail-soft region: `set -e` + `pipefail` are what make a
# `tee` failure (a full disk, an unwritable git dir) abort the script, and
# aborting HERE would skip the status check below -- losing the failure
# banner, the FAILED marker the re-push guard depends on, and the real
# exit code. Diagnostics must never outrank the result they describe.
elapsed=$((SECONDS - started_at))
printf '\ngit %s hook: %dm%02ds total\n' \
  "$hook_type" "$((elapsed / 60))" "$((elapsed % 60))" | tee -a "$LOG"
if [ "$elapsed" -gt "$BUDGET_SECONDS" ]; then
  {
    echo "=================================================================="
    echo "OVER BUDGET: the ${hook_type} hook took ${elapsed}s, past the"
    echo "${BUDGET_SECONDS}s ceiling. That is a gate-scope defect, not a"
    echo "cost of doing business. Read ${LOG} to see which hook dominated;"
    echo "the usual cause is a change that widened an affected-scope"
    echo "selector into a whole-tree run."
    echo "=================================================================="
  } | tee -a "$LOG" >&2
fi

if [ "$status" -ne 0 ]; then
  {
    echo
    echo "=================================================================="
    echo "git ${hook_type} hook FAILED (exit ${status})."
    echo "Full untruncated output: ${LOG}"
    echo "Previous run's log preserved at: ${PREV}"
    echo "--- failing tail (last 60 lines; read the full log above if needed) ---"
    tail -n 60 "$LOG" || true
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
# `errexit` stays off from the hook invocation onwards: everything after it
# reports the result rather than producing it, so a failure writing a log
# line must never decide the process's exit code.
set -e

exit "$status"
