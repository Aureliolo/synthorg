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
# `--sync-only` performs the binary, version and package checks and then
# stops, for a caller that needs the style package materialised but does
# not lint Markdown itself. `check_vale_ledger_complete.py` is that
# caller: it enumerates the package, and it runs on changes to the vale
# config, which are exactly the pushes this wrapper's own Markdown file
# filter does not match.
#
# The vale BINARY itself is still installed once per machine via
# scripts/install_cli_tools.sh (it has to land on PATH before this
# wrapper can run); if missing, this script prints a clear pointer
# rather than the opaque shell "command not found".
#
# The binary's VERSION is checked too, not just its presence. Vale
# decides how a rule is scoped, so two versions disagree about what the
# same style package flags: on one measured corpus 3.17.0 reported 137
# findings that 3.14.2 did not. A developer who installed vale once and
# never re-ran the installer would push against a weaker gate than CI
# runs, which is the failure this whole gate exists to rule out.

set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
cd "${repo_root}"

if ! command -v vale >/dev/null 2>&1; then
  echo "error: vale binary not found on PATH" >&2
  echo "       run 'bash scripts/install_cli_tools.sh vale' once on this machine" >&2
  exit 1
fi

# Single source of truth: the pin in the installer, read rather than copied.
pinned_version="$(sed -n 's/^VALE_VERSION="\(.*\)"$/\1/p' scripts/install_cli_tools.sh)"
if [ -z "${pinned_version}" ]; then
  echo "error: could not read VALE_VERSION from scripts/install_cli_tools.sh" >&2
  exit 1
fi

installed_version="v$(vale --version | sed -n 's/^vale version \([0-9][0-9.]*\).*$/\1/p')"
if [ "${installed_version}" != "${pinned_version}" ]; then
  echo "error: vale on PATH is ${installed_version}, but this repository pins ${pinned_version}" >&2
  echo "       a different vale scopes rules differently, so a local pass would not mean CI passes" >&2
  echo "       run 'bash scripts/install_cli_tools.sh vale' to upgrade in place" >&2
  exit 1
fi

# Acronyms.yml is shipped by the Google style package; using a real file
# (rather than `ls -A`) is faster and avoids the empty-directory edge case.
if [ ! -s .vale/styles/Google/Acronyms.yml ]; then
  echo "vale: Google style package missing for this worktree, running 'vale sync'..."
  vale --config .vale.ini sync
fi

if [ "${1:-}" = "--sync-only" ]; then
  exit 0
fi

exec vale --config .vale.ini "$@"
