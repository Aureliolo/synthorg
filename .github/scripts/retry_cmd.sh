#!/usr/bin/env bash
# Retry an arbitrary IDEMPOTENT, side-effecting command with bounded
# exponential-with-cap backoff, then fail loud with the command's own
# exit code once the budget is exhausted.
#
# This is the generic sibling of the purpose-built helpers:
#   - gh_with_retry.sh         : `gh` calls; CAPTURES stdout for command
#                                substitution; stderr-allowlist classifier;
#                                soft-skip exit 75 on exhaustion.
#   - docker_push_with_retry.sh / cosign_sign_with_retry.sh : registry / Sigstore
#                                writes with a transient-signature allowlist.
#   - go-mod-download-retry    : a composite action dedicated to `go mod download`.
#
# retry_cmd.sh covers the remaining class those do not: a pure
# network-fetch install whose ONLY non-zero failure modes are transient
# (a CDN / package-index / apt-mirror blip) or a deterministic error that
# would fail again anyway. `uv sync --frozen`, `uv python install`, an apt
# install from a freshly-added repo, a binary download via curl. For those
# the safe posture is the same as go-mod-download-retry: retry EVERY
# non-zero exit. A genuine non-transient failure (a stale frozen lockfile,
# a missing package) still fails -- it just costs the bounded backoff
# budget first, which is acceptable for the rare deterministic case and is
# exactly what kills the common transient one.
#
# Two properties make retry-everything safe HERE that would be unsafe for a
# generic mutation:
#   1. The wrapped commands are idempotent: re-running `uv sync` /
#      `apt-get install` / a `curl` download converges to the same state.
#      NEVER wrap a non-idempotent mutation (a POST that creates a commit /
#      release / ref) with this helper -- a transient error after the write
#      landed would double-create. Use gh_with_retry.sh (definitive-4xx
#      fast-fail) or leave such writes un-retried so they page.
#   2. Output is STREAMED through, not captured: these commands are run for
#      their side effects, not a value, so there is no command-substitution
#      contamination concern.
#
# Posture: 5 attempts, 15s base doubling to a 120s cap
# (15 + 30 + 60 + 120 (capped) = ~3m45s of wait before the final attempt).
# Tunable via RETRY_CMD_ATTEMPTS / RETRY_CMD_BASE_DELAY / RETRY_CMD_MAX_DELAY
# (also the seam the self-test drives with zero backoff).
#
# Usage:
#   retry_cmd.sh "label for log" <command> [args...]
#
# Behaviour / contract:
#   - exit 0           : the command succeeded (on some attempt).
#   - exit <wrapped rc>: the command still failed after every attempt; the
#                        LAST attempt's exit code is bubbled so the job
#                        fails loud (fail-closed -- never a soft-skip).
#
# NOTE: deliberately ``set -uo pipefail`` WITHOUT ``-e``. The wrapped
# command's failure is captured explicitly via ``|| rc=$?``, and the
# ``DELAY=$((DELAY * 2))`` arithmetic returns exit status 1 whenever the
# result is 0 -- which the zero-backoff test seam (RETRY_CMD_BASE_DELAY=0)
# hits every iteration. Under ``-e`` that benign arithmetic would abort the
# loop. The loop has no other unchecked commands, so ``-e`` buys nothing here.
set -uo pipefail

LABEL="${1:?missing label}"
shift
if [ "$#" -eq 0 ]; then
  echo "::error::retry_cmd.sh: no command supplied" >&2
  exit 2
fi

ATTEMPTS="${RETRY_CMD_ATTEMPTS:-5}"
DELAY="${RETRY_CMD_BASE_DELAY:-15}"
MAX_DELAY="${RETRY_CMD_MAX_DELAY:-120}"

# Fail fast on a non-numeric tunable. Without ``-e``, a non-numeric value
# makes the ``[ "$attempt" -ge "$ATTEMPTS" ]`` exhaustion test error out
# (rc 2, treated as false), so a failing command would loop until the job
# timeout instead of bubbling its exit code. ATTEMPTS must be >= 1; the
# delays may be 0 (the zero-backoff self-test seam).
if ! [[ "$ATTEMPTS" =~ ^[1-9][0-9]*$ ]]; then
  echo "::error::retry_cmd.sh: RETRY_CMD_ATTEMPTS must be a positive integer (got '$ATTEMPTS')" >&2
  exit 2
fi
if ! [[ "$DELAY" =~ ^[0-9]+$ ]]; then
  echo "::error::retry_cmd.sh: RETRY_CMD_BASE_DELAY must be a non-negative integer (got '$DELAY')" >&2
  exit 2
fi
if ! [[ "$MAX_DELAY" =~ ^[0-9]+$ ]]; then
  echo "::error::retry_cmd.sh: RETRY_CMD_MAX_DELAY must be a non-negative integer (got '$MAX_DELAY')" >&2
  exit 2
fi

attempt=0
while :; do
  attempt=$((attempt + 1))
  # `|| rc=$?` keeps `set -e`-style aborts away and captures the real exit
  # code, sidestepping the `if cmd; then` idiom that resets $? to 0.
  rc=0
  "$@" || rc=$?
  if [ "$rc" -eq 0 ]; then
    if [ "$attempt" -gt 1 ]; then
      echo "::notice::${LABEL} succeeded on attempt ${attempt}" >&2
    fi
    exit 0
  fi
  if [ "$attempt" -ge "$ATTEMPTS" ]; then
    echo "::error::${LABEL} failed after ${attempt} attempts (last exit ${rc})" >&2
    exit "$rc"
  fi
  echo "::warning::${LABEL} failed (attempt ${attempt}/${ATTEMPTS}, exit ${rc}); retrying in ${DELAY}s" >&2
  sleep "$DELAY"
  DELAY=$((DELAY * 2))
  if [ "$DELAY" -gt "$MAX_DELAY" ]; then
    DELAY="$MAX_DELAY"
  fi
done
