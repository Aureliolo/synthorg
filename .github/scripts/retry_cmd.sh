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
#   3. A KILLED attempt leaves nothing that defeats the next one. Idempotent
#      is not enough once RETRY_CMD_ATTEMPT_TIMEOUT is set: the kill only
#      reaches what this process may signal, so a command that escalates to
#      root survives it holding whatever it held. `playwright install
#      --with-deps` sudo's to apt, and a timed-out attempt left apt-get alive
#      on /var/lib/apt/lists/lock, so both remaining attempts died in under a
#      second on "Could not get lock" and the ladder could not succeed. Wrap
#      such a command with the deadline as its only bound, or split the part
#      that escalates out of the retried unit.
#
# Posture: 5 attempts, 15s base doubling to a 120s cap
# (15 + 30 + 60 + 120 (capped) = ~3m45s of wait before the final attempt).
# Tunable via RETRY_CMD_ATTEMPTS / RETRY_CMD_BASE_DELAY / RETRY_CMD_MAX_DELAY
# (also the seam the self-test drives with zero backoff).
#
# Per-attempt timeout: retry_cmd retries on a non-zero EXIT, so a command
# that HANGS -- a connection that opens then stalls with no data and no
# error -- would sit untouched until the job's own timeout-minutes reaps
# the whole runner, never reaching the retry ladder. RETRY_CMD_ATTEMPT_TIMEOUT
# (seconds; 0 = off, the default) wraps each attempt in `timeout` so a hang
# becomes a retryable 124 and the ladder engages. Off by default so existing
# callers are byte-for-byte unchanged; opt in per-call.
#
# Total deadline: an attempt timeout multiplies. N attempts of T seconds plus
# the backoff is the real worst case, and when that exceeds the enclosing
# job's timeout-minutes the ladder can never exhaust -- the runner is reaped
# mid-retry and the failure surfaces as an opaque "cancelled" job instead of
# the loud message below, with the last attempts unreachable dead
# configuration. RETRY_CMD_DEADLINE (seconds; 0 = off, the default) bounds
# the whole ladder in wall-clock: every attempt is clamped to the time left,
# and the loop stops rather than sleeping into a backoff it cannot afford.
# Clamping is what lets a caller keep a generous per-attempt timeout on a
# short job, and it bounds a caller that sets no attempt timeout at all --
# with a deadline set, a hang is killed by whatever remains of it. Set it
# comfortably below the job budget so teardown still fits.
#
# Usage:
#   retry_cmd.sh "label for log" <command> [args...]
#
# Behaviour / contract:
#   - exit 0           : the command succeeded (on some attempt).
#   - exit <wrapped rc>: the command still failed after every attempt, or the
#                        deadline left no room for another; the LAST attempt's
#                        exit code is bubbled so the job fails loud
#                        (fail-closed -- never a soft-skip).
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
ATTEMPT_TIMEOUT="${RETRY_CMD_ATTEMPT_TIMEOUT:-0}"
DEADLINE="${RETRY_CMD_DEADLINE:-0}"
# SECONDS counts wall-clock from here, so every deadline test below measures
# the ladder's own elapsed time rather than trusting an external clock.
SECONDS=0

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
if ! [[ "$ATTEMPT_TIMEOUT" =~ ^[0-9]+$ ]]; then
  echo "::error::retry_cmd.sh: RETRY_CMD_ATTEMPT_TIMEOUT must be a non-negative integer (got '$ATTEMPT_TIMEOUT')" >&2
  exit 2
fi
if ! [[ "$DEADLINE" =~ ^[0-9]+$ ]]; then
  echo "::error::retry_cmd.sh: RETRY_CMD_DEADLINE must be a non-negative integer (got '$DEADLINE')" >&2
  exit 2
fi

# Resolve the per-attempt timeout wrapper once, before the loop. macOS ships
# coreutils' `timeout` as `gtimeout` (or not at all); when a timeout was asked
# for but neither exists, warn ONCE so the unguarded fallback is visible in the
# log rather than silently skipped -- and warning here, not per attempt, keeps
# the retry output clean.
TIMEOUT_CMD=""
if [ "$ATTEMPT_TIMEOUT" -gt 0 ] || [ "$DEADLINE" -gt 0 ]; then
  if command -v timeout >/dev/null 2>&1; then
    TIMEOUT_CMD="timeout"
  elif command -v gtimeout >/dev/null 2>&1; then
    TIMEOUT_CMD="gtimeout"
  else
    echo "::warning::retry_cmd.sh: a per-attempt bound was requested (RETRY_CMD_ATTEMPT_TIMEOUT=${ATTEMPT_TIMEOUT}, RETRY_CMD_DEADLINE=${DEADLINE}) but neither 'timeout' nor 'gtimeout' is available; attempts run UNBOUNDED and the deadline is only checked between them" >&2
  fi
fi

attempt=0
while :; do
  attempt=$((attempt + 1))
  # Clamp this attempt to whatever the deadline leaves. The loop only starts
  # an attempt when the tail check below proved time remained, so this stays
  # positive. A caller with a deadline but no attempt timeout gets the
  # remaining budget as its bound, which is what stops a hang there too.
  this_timeout="$ATTEMPT_TIMEOUT"
  if [ "$DEADLINE" -gt 0 ]; then
    remaining=$((DEADLINE - SECONDS))
    if [ "$this_timeout" -eq 0 ] || [ "$this_timeout" -gt "$remaining" ]; then
      this_timeout="$remaining"
    fi
  fi
  # `|| rc=$?` keeps `set -e`-style aborts away and captures the real exit
  # code, sidestepping the `if cmd; then` idiom that resets $? to 0.
  rc=0
  if [ -n "$TIMEOUT_CMD" ] && [ "$this_timeout" -gt 0 ]; then
    # --kill-after escalates to SIGKILL if the command ignores the initial
    # SIGTERM. A timeout exits 124 (or 137 on the SIGKILL escalation), which
    # the loop treats as any other retryable non-zero exit.
    #
    # The kill reaches only what this process may signal. A command that
    # escalates to root (anything running sudo underneath) leaves that child
    # ALIVE, because an unprivileged signal to a root process is refused, and
    # whatever it holds -- an apt lock, a port, a pidfile -- is still held when
    # the next attempt starts. So an attempt timeout is only sound over a
    # command whose kill leaves nothing behind: pair it with sudo and the
    # retries report the wreckage of attempt one instead of the fault that
    # timed it out.
    "$TIMEOUT_CMD" --kill-after=10s "$this_timeout" "$@" || rc=$?
  else
    "$@" || rc=$?
  fi
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
  # Stop once the backoff would run past the deadline. Sleeping into it would
  # hand the job reaper a loop mid-retry, replacing this loud exit with the
  # opaque cancellation the deadline exists to prevent.
  if [ "$DEADLINE" -gt 0 ] && [ $((SECONDS + DELAY)) -ge "$DEADLINE" ]; then
    echo "::error::${LABEL} exhausted its ${DEADLINE}s deadline after ${attempt} attempts (last exit ${rc})" >&2
    exit "$rc"
  fi
  echo "::warning::${LABEL} failed (attempt ${attempt}/${ATTEMPTS}, exit ${rc}); retrying in ${DELAY}s" >&2
  sleep "$DELAY"
  DELAY=$((DELAY * 2))
  if [ "$DELAY" -gt "$MAX_DELAY" ]; then
    DELAY="$MAX_DELAY"
  fi
done
