#!/usr/bin/env bash
# Retry transient GitHub API failures behind a `gh` invocation.
#
# Wraps an arbitrary `gh` command (e.g. `gh pr list --json ...`, `gh api ...`)
# with bounded exponential-with-cap retry. The GitHub Actions auth service
# occasionally returns `HTTP 401: Requires authentication` on the very first
# API call right after a job starts, and the next attempt with the same
# (still-valid, full-scope) token succeeds. 5xx, network resets, and EOF are
# the other transient classes. Definitive client errors (400/403/404/409/422)
# fail fast: retrying a missing branch, a revoked-scope token, or a
# ruleset-rejected write only wastes the retry budget and hides a real config
# bug. 401 is deliberately treated as transient (not definitive) -- see above.
#
# Posture mirrors `.github/workflows/cla.yml::gh_api_retry` and
# `.github/scripts/docker_push_with_retry.sh`, but with the shorter 4-attempt
# ladder (15 -> 30 -> 60s, ~1m45s worst case) suited to short-timeout release
# jobs whose work self-heals on the next trigger rather than the CLA gate's
# ~10-min budget.
#
# Usage:
#   gh_with_retry.sh "label for log" gh pr list --state all --head BRANCH --json number,state --jq '...'
#   gh_with_retry.sh "label for log" gh api repos/OWNER/REPO/...
#
# Behaviour / contract:
#   - The wrapped command's stdout is captured cleanly and re-emitted on
#     success (stderr is kept separate so it never contaminates a value the
#     caller assigns via command substitution).
#   - exit 0  : success; captured stdout printed.
#   - exit 75 : transient failure, retries exhausted. EX_TEMPFAIL-style signal
#               so callers whose work self-heals can skip this run rather than
#               page. Distinct from the wrapped command's own exit codes
#               (`gh` only ever exits 0-4), so callers can branch on it.
#   - exit <wrapped rc> (non-zero, non-75) : definitive / non-transient error;
#               fail loud.
set -euo pipefail

# EX_TEMPFAIL (sysexits.h): transient failure the caller may choose to defer.
EXIT_TRANSIENT_EXHAUSTED=75

LABEL="${1:?missing label}"
shift
if [ "$#" -eq 0 ]; then
  echo "::error::gh_with_retry.sh: no command supplied" >&2
  exit 2
fi

# 4 attempts, backoff 15s -> 30s -> 60s = ~1m45s of wait in the worst case
# before the final attempt. Callers must run under a job timeout comfortably
# larger than this so the exhaustion path (exit 75) is actually reachable
# rather than the job being killed mid-ladder. Overridable via env for
# callers on tighter timeouts (and for tests): GH_RETRY_ATTEMPTS /
# GH_RETRY_BACKOFF.
ATTEMPTS="${GH_RETRY_ATTEMPTS:-4}"
BACKOFF="${GH_RETRY_BACKOFF:-15}"

errfile="$(mktemp)"
trap 'rm -f "$errfile"' EXIT

for ((i = 1; i <= ATTEMPTS; i++)); do
  out=""
  rc=0
  # Capture stdout into `out`; route stderr to a file so the caller's
  # command-substituted value stays clean. `|| rc=$?` keeps `set -e` from
  # aborting and avoids the `if cmd; then` idiom that clears $? to 0 once
  # the if-statement completes.
  out="$("$@" 2>"$errfile")" || rc=$?
  if [ "$rc" -eq 0 ]; then
    if [ "$i" -gt 1 ]; then
      echo "::notice::${LABEL} succeeded on attempt ${i}" >&2
    fi
    printf '%s\n' "$out"
    exit 0
  fi

  # Definitive client error: fail fast, bubble the real exit code so the
  # caller fails loud (a 403 from token-scope narrowing or ruleset drift is a
  # real config bug, not a blip). 401 is intentionally excluded -- see header.
  if grep -qE 'HTTP 4(00|03|04|09|22)' "$errfile"; then
    cat "$errfile" >&2
    echo "::error::${LABEL}: definitive client error (no retry, exit ${rc})" >&2
    exit "$rc"
  fi

  if [ "$i" -eq "$ATTEMPTS" ]; then
    cat "$errfile" >&2
    echo "::error::${LABEL}: transient API failure persisted after ${ATTEMPTS} attempts (last exit ${rc})" >&2
    exit "$EXIT_TRANSIENT_EXHAUSTED"
  fi

  cat "$errfile" >&2
  echo "::warning::${LABEL}: transient API failure (attempt ${i}/${ATTEMPTS}, exit ${rc}); retrying in ${BACKOFF}s" >&2
  sleep "$BACKOFF"
  BACKOFF=$((BACKOFF * 2))
done
