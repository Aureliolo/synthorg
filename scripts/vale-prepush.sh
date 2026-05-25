#!/usr/bin/env bash
# Vale pre-push wrapper.
#
# Vale's Google style package lives under .vale/styles/Google/ which is
# gitignored (it is a 52 KB upstream package downloaded by `vale sync`,
# not source we want to vendor). Each git worktree therefore starts with
# an empty styles dir and would normally need a manual
# `bash scripts/install_cli_tools.sh vale` before vale can run.
#
# This wrapper makes that lazy: it checks for a sentinel file in the
# styles dir and only runs `vale sync` when missing. After the first
# push in a worktree the check is a single stat() call; subsequent
# pushes pay no extra cost.
#
# The vale BINARY itself is still installed once per machine via
# scripts/install_cli_tools.sh (it has to land on PATH before this
# wrapper can run); if missing, this script prints a clear pointer
# rather than the opaque shell "command not found".

set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
cd "${repo_root}"

if ! command -v vale >/dev/null 2>&1; then
  echo "error: vale binary not found on PATH" >&2
  echo "       run 'bash scripts/install_cli_tools.sh vale' once on this machine" >&2
  exit 1
fi

# Acronyms.yml is shipped by the Google style package; using a real file
# (rather than `ls -A`) is faster and avoids the empty-directory edge case.
if [ ! -s .vale/styles/Google/Acronyms.yml ]; then
  echo "vale: Google style package missing for this worktree, running 'vale sync'..."
  vale --config .vale.ini sync
fi

exec vale --config .vale.ini "$@"
