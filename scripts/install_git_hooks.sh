#!/usr/bin/env bash
# One-time, per-clone git-hook wiring. Idempotent: safe to re-run.
#
# Points the shared core.hooksPath at the RELATIVE in-tree directory
# scripts/git-hooks. Git resolves a relative core.hooksPath against each
# worktree's own working tree, so a single shared-config value makes
# every worktree (existing and future) run its OWN committed wrappers
# against its OWN venv, with no per-worktree step.
#
# NOT `pre-commit install` -- that regenerates venv-baked wrappers in
# .git/hooks, the exact failure mode this replaces.
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
HOOKS_REL="scripts/git-hooks"
HOOKS_DIR="$ROOT/$HOOKS_REL"
REQUIRED=(_run-hook.sh pre-commit pre-push commit-msg)

for name in "${REQUIRED[@]}"; do
  if [ ! -f "$HOOKS_DIR/$name" ]; then
    echo "ERROR: $HOOKS_REL/$name is missing -- run from a clean checkout." >&2
    exit 1
  fi
done

# POSIX platforms need the exec bit to run the hooks. Idempotent.
chmod +x "$HOOKS_DIR"/_run-hook.sh "$HOOKS_DIR"/pre-commit \
  "$HOOKS_DIR"/pre-push "$HOOKS_DIR"/commit-msg

git config core.hooksPath "$HOOKS_REL"

echo "core.hooksPath -> $HOOKS_REL (relative; resolved per worktree)"
