#!/usr/bin/env bash
# Retry transient GHCR push failures.
#
# Both `docker push <repo>:<tag>` and `docker manifest push <repo>:<tag>` are
# idempotent for content we control: identical bytes -> identical digest, so
# re-pushing on a transient registry error never publishes anything different
# from the first successful attempt.
#
# Real failures (auth denied, permission, repository not found, malformed
# image) are NOT retried -- their output never matches the transient
# patterns below, so this wrapper exits 1 on the first attempt for them.
#
# Usage:
#   docker_push_with_retry.sh "label for log" docker push <ref>
#   docker_push_with_retry.sh "label for log" docker manifest push <ref>
#   docker_push_with_retry.sh --print-transient-re   # canonical regex, for callers
#                                                    # that need to share the list
#
# Behaviour:
#   - Captures combined stdout+stderr of the wrapped command.
#   - On success: prints captured output to stdout, exits 0.
#   - On a transient signature with attempts remaining: prints output and a
#     warning to stderr, sleeps with exponential backoff, retries.
#   - On a non-transient error or exhausted attempts: prints captured
#     output to stdout (so callers that capture it can dump it) and exits
#     with the wrapped command's exit code.
set -euo pipefail

# Patterns that indicate the registry (or the network path to it) is the
# problem, not the image. Case-insensitive. Anchored loosely so format
# changes upstream do not silently disable the retry.
#
# `tls handshake` and `net/http: TLS handshake` are kept (transient handshake
# failures); a bare `tls: ` is intentionally NOT included because it would
# also match non-transient configuration errors like
# `tls: failed to verify certificate` or `tls: bad certificate`.
#
# `context deadline exceeded` / `Client.Timeout exceeded` / `timeout awaiting
# response headers` / `timeout awaiting response body` / `request canceled`
# cover Go ``net/http`` client-side timeout strings emitted by Docker / buildx
# when GHCR fails to respond to a request within the per-request deadline.
# These are the canonical transient signatures `i/o timeout` misses on the
# GHCR HTTP path: the underlying socket may be healthy while the HTTP
# response just never arrives in time. Both the headers and body variants
# are kept so a stall after the upload starts streaming also retries --
# `docker push` is idempotent, so re-pushing on a body-timeout is safe.
TRANSIENT_RE='page is taking too long|unknown blob|blob unknown|blob upload invalid|manifest unknown|received unexpected HTTP status: 5[0-9]{2}|HTTP/[0-9.]+ 5[0-9]{2}|HTTP 5[0-9]{2}|status: 5[0-9]{2}|429 Too Many Requests|temporarily unavailable|server is currently unable|service unavailable|bad gateway|gateway time-?out|i/o timeout|tls handshake|connection reset|connection refused|EOF|unexpected EOF|read: connection|net/http: TLS handshake|context deadline exceeded|Client\.Timeout exceeded|timeout awaiting response headers|timeout awaiting response body|request canceled'

# Discovery flag: callers that need to share the same regex (for example the
# inline retag-inspect retry loop, which must drop a couple of patterns the
# inspect path cannot benefit from) source it from here so a new pattern added
# here automatically propagates.
if [ "${1:-}" = "--print-transient-re" ]; then
  printf '%s\n' "$TRANSIENT_RE"
  exit 0
fi

LABEL="${1:?missing label}"
shift
if [ "$#" -eq 0 ]; then
  echo "::error::docker_push_with_retry.sh: no command supplied" >&2
  exit 2
fi

# 4 attempts, backoff 15s -> 30s -> 60s = ~1m45s of wait in the worst case
# before the final attempt. Long enough to ride through GHCR's typical 30-90s
# unicorn windows; short enough to fail loudly on a real outage. Overridable
# via env (mirrors gh_with_retry.sh's GH_RETRY_*) so the regression self-test
# can drive the classifier with a zero backoff instead of waiting ~1m45s.
ATTEMPTS="${DOCKER_PUSH_RETRY_ATTEMPTS:-4}"
BACKOFF="${DOCKER_PUSH_RETRY_BACKOFF:-15}"
# A zero or non-numeric ATTEMPTS would make the loop body never run and
# the script exit 0 WITHOUT running the wrapped command (fail-open).
# Reject it; a zero BACKOFF is legitimate (tests use it).
if ! [[ "$ATTEMPTS" =~ ^[1-9][0-9]*$ ]]; then
  echo "::error::DOCKER_PUSH_RETRY_ATTEMPTS must be a positive integer; got '${ATTEMPTS}'" >&2
  exit 2
fi
if ! [[ "$BACKOFF" =~ ^[0-9]+$ ]]; then
  echo "::error::DOCKER_PUSH_RETRY_BACKOFF must be a non-negative integer; got '${BACKOFF}'" >&2
  exit 2
fi

for ((i = 1; i <= ATTEMPTS; i++)); do
  out=""
  rc=0
  # Capture combined stdout+stderr; on failure, save the wrapped exit
  # code via `|| rc=$?` so set -e does not abort and the `if cmd; then`
  # idiom does not silently overwrite it (a failed `if cmd` clears $?
  # to 0 once the if-statement completes, which is why this uses an
  # explicit `||` instead of `if ! ... ; then`).
  out="$("$@" 2>&1)" || rc=$?
  if [ "$rc" -eq 0 ]; then
    printf '%s\n' "$out"
    exit 0
  fi

  # Final attempt: surface output to caller and bubble up the failure.
  if [ "$i" -eq "$ATTEMPTS" ]; then
    printf '%s\n' "$out"
    echo "::error::${LABEL} failed after ${ATTEMPTS} attempts (last exit ${rc})" >&2
    exit "$rc"
  fi

  # Mid-loop: only retry if the error looks transient. Echo the captured
  # output to stderr so the build log shows what happened on each attempt
  # without contaminating the caller's captured stdout.
  #
  # Grep a here-string, never `printf "$out" | grep -qi`: under `set -o
  # pipefail` a large `$out` (a GHCR 5xx HTML error body easily exceeds
  # the 64 KB pipe buffer) makes the early-exiting `grep -q` close the
  # pipe while printf is still writing, so printf takes EPIPE and the
  # pipeline inherits its non-zero status -- the match is masked and a
  # transient push error is misclassified as terminal. A here-string is
  # not a pipeline, so the status is purely grep's.
  if grep -qiE "$TRANSIENT_RE" <<<"$out"; then
    printf '%s\n' "$out" >&2
    echo "::warning::${LABEL} hit transient registry error (attempt ${i}/${ATTEMPTS}, rc=${rc}); sleeping ${BACKOFF}s before retry" >&2
    sleep "$BACKOFF"
    BACKOFF=$((BACKOFF * 2))
    continue
  fi

  # Non-transient: do not retry. Surface output and bubble up.
  printf '%s\n' "$out"
  echo "::error::${LABEL} failed with non-transient error (exit ${rc}); not retrying" >&2
  exit "$rc"
done
