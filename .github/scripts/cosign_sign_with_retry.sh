#!/usr/bin/env bash
# Retry cosign sign on transient registry / Rekor / Fulcio failures.
#
# `cosign sign` against a published digest is idempotent: signing the
# same digest twice either succeeds again or hits a Rekor
# `createLogEntryConflict` (already-logged) response, which callers
# treat as success. Transient GHCR/Rekor/Fulcio errors (5xx, 429, TLS
# handshake stalls, connection resets) almost always settle inside the
# next attempt window, so a bounded retry turns a noisy infra blip
# into a green run instead of failing the whole Docker workflow.
#
# Usage:
#   cosign_sign_with_retry.sh <ref>
#     <ref> is the full image reference, e.g.
#     ghcr.io/aureliolo/synthorg-sandbox-base@sha256:abc...
#
# Behaviour:
#   - Captures combined stdout+stderr of `cosign sign --yes <ref>`.
#   - On exit 0: prints captured output, exits 0.
#   - On exit non-0:
#     * Output contains `createLogEntryConflict` -> already signed,
#       emit `::notice::` and exit 0 (preserves the idempotency branch
#       the inline shell blocks used to carry).
#     * Output matches the shared transient regex sourced from
#       docker_push_with_retry.sh (single source of truth for
#       "is this a registry-side flake?") -> warn + sleep + retry
#       with exponential backoff.
#     * Otherwise -> non-transient cosign / Rekor / Fulcio error,
#       surface output and exit with cosign's exit code.
#   - Final attempt with no terminal classification: surface all
#     output and exit with cosign's exit code.
set -euo pipefail

REF="${1:?usage: cosign_sign_with_retry.sh <ref>}"

# Same regex the docker push helper uses; `--print-transient-re` keeps
# both scripts in lockstep so a new transient signature added in one
# place automatically protects every signing call too.
SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"
# Invoke the sibling helper via `bash` rather than relying on the
# execute bit. Both scripts ship as git mode 100644 (no execute bit),
# matching the existing publish-* action call sites which always use
# `bash "$RETRY" ...`; a bare `"$SCRIPT_DIR/..."` would fail
# "Permission denied" on the Linux runner.
TRANSIENT_RE="$(bash "$SCRIPT_DIR/docker_push_with_retry.sh" --print-transient-re)"

# 4 attempts, backoff 15s -> 30s -> 60s = ~1m45s of wait in the worst
# case before the final attempt. Matches docker_push_with_retry.sh so
# cosign rides through GHCR's typical 30-90s unicorn windows under the
# same budget the push step already does.
ATTEMPTS=4
BACKOFF=15

for ((i = 1; i <= ATTEMPTS; i++)); do
  out=""
  rc=0
  out="$(cosign sign --yes "$REF" 2>&1)" || rc=$?
  if [ "$rc" -eq 0 ]; then
    printf '%s\n' "$out"
    exit 0
  fi

  # Idempotency branch: a re-sign that lost the createLogEntry race is
  # success, not a transient error. Check this BEFORE the regex so
  # attempt 1 -> 5xx -> attempt 2 -> conflict resolves cleanly.
  #
  # Grep a here-string, never `printf "$out" | grep -q`: under `set -o
  # pipefail` a large `$out` (a GHCR 5xx HTML error body easily exceeds
  # the 64 KB pipe buffer) makes the early-exiting `grep -q` close the
  # pipe while printf is still writing, so printf takes EPIPE and the
  # pipeline inherits its non-zero status -- the match is masked and a
  # transient error is misclassified as terminal. A here-string is not a
  # pipeline, so the status is purely grep's.
  if grep -q 'createLogEntryConflict' <<<"$out"; then
    printf '%s\n' "$out"
    echo "::notice::Image ${REF} already signed -- skipping"
    exit 0
  fi

  if [ "$i" -eq "$ATTEMPTS" ]; then
    printf '%s\n' "$out"
    echo "::error::cosign sign ${REF} failed after ${ATTEMPTS} attempts (last exit ${rc})" >&2
    exit "$rc"
  fi

  # Guard against an empty `$TRANSIENT_RE` (e.g. sibling helper drift
  # that silently prints nothing) - `grep -E ""` matches every line,
  # which would retry auth failures and other non-transient errors.
  #
  # Grep a here-string, not `printf "$out" | grep -qi`: see the
  # idempotency branch above. A GHCR 5xx HTML error body overruns the
  # 64 KB pipe buffer, so the piped form takes EPIPE on the early
  # `grep -q` exit and `pipefail` masks the match -- which is exactly
  # how a transient `502 Bad Gateway` got misclassified as terminal and
  # left an image unsigned.
  if [[ -n "$TRANSIENT_RE" ]] && grep -qiE "$TRANSIENT_RE" <<<"$out"; then
    printf '%s\n' "$out" >&2
    echo "::warning::cosign sign ${REF} hit transient error (attempt ${i}/${ATTEMPTS}, rc=${rc}); sleeping ${BACKOFF}s before retry" >&2
    sleep "$BACKOFF"
    BACKOFF=$((BACKOFF * 2))
    continue
  fi

  # Non-transient: surface output and bubble up immediately. Auth
  # denials, malformed digests, Rekor schema rejections, etc. will
  # never improve on a retry.
  printf '%s\n' "$out"
  echo "::error::cosign sign ${REF} failed with non-transient error (exit ${rc}); not retrying" >&2
  exit "$rc"
done
