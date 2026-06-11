#!/usr/bin/env bash
# Regression test for the broken-pipe misclassification in the cosign and
# docker-push retry helpers.
#
# The bug: the helpers classified a transient registry error with
#   printf '%s' "$out" | grep -qiE "$TRANSIENT_RE"
# under `set -o pipefail`. When `$out` is a GHCR 5xx HTML error body that
# overruns the 64 KB pipe buffer and the match token sits at the very START,
# `grep -q` exits on first match and closes the pipe, `printf` takes EPIPE,
# and `pipefail` makes the pipeline report the writer's non-zero status --
# so the match is masked, a genuinely-transient error is misclassified as
# terminal, and NOT retried. That is what left a sandbox image
# pushed-but-unsigned and the Verify Image Signatures gate red.
#
# This test feeds exactly that shape (transient token first, >64 KB
# trailing body) through the REAL helpers and asserts they classify it as
# transient and retry. On Linux (CI) the old piped form reproduces the
# EPIPE and fails this test; the here-string form passes. On platforms
# whose pipe semantics never raise EPIPE the here-string form still
# passes, so this is a one-way regression guard, never a flake. A negative
# control proves the classifier did not become match-everything.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)" # .github/scripts
COSIGN_HELPER="$SCRIPT_DIR/cosign_sign_with_retry.sh"
PUSH_HELPER="$SCRIPT_DIR/docker_push_with_retry.sh"

FAILED=0
pass() { printf 'PASS: %s\n' "$*"; }
fail() {
  printf 'FAIL: %s\n' "$*" >&2
  FAILED=1
}

# Emit a >64 KB body with the transient match token FIRST, then filler --
# the exact shape that triggers EPIPE under the buggy piped `grep -q`.
BIG_5XX='printf "502 Bad Gateway: "; head -c 100000 </dev/zero | tr "\0" x; printf "\n"; exit 1'

# --- docker_push_with_retry.sh: a large transient 5xx must be retried ---
out="$(DOCKER_PUSH_RETRY_ATTEMPTS=2 DOCKER_PUSH_RETRY_BACKOFF=0 \
  bash "$PUSH_HELPER" "selftest-push" bash -c "$BIG_5XX" 2>&1)" || true
if grep -q 'hit transient registry error' <<<"$out" \
  && ! grep -q 'non-transient error' <<<"$out"; then
  pass "docker_push retries a large 5xx body"
else
  fail "docker_push misclassified a large 5xx body (broken-pipe regression)"
  printf '%s\n' "$out" | tail -n 3 >&2 || true
fi

# --- docker_push_with_retry.sh: a genuine error must NOT be retried -----
out="$(DOCKER_PUSH_RETRY_ATTEMPTS=3 DOCKER_PUSH_RETRY_BACKOFF=0 \
  bash "$PUSH_HELPER" "selftest-neg" \
  bash -c 'echo "denied: requested access to the resource is denied"; exit 1' 2>&1)" || true
if grep -q 'non-transient error' <<<"$out" && ! grep -q 'hit transient' <<<"$out"; then
  pass "docker_push does not retry a genuine non-transient error"
else
  fail "docker_push retried a non-transient error (classifier too broad)"
  printf '%s\n' "$out" | tail -n 3 >&2 || true
fi

# --- cosign_sign_with_retry.sh: a large transient 5xx must be retried ---
# The helper hardcodes `cosign sign`, so stub a `cosign` on PATH that emits
# the same large 5xx body and fails.
STUB_DIR="$(mktemp -d)"
trap 'rm -rf "$STUB_DIR"' EXIT
cat >"$STUB_DIR/cosign" <<STUB
#!/usr/bin/env bash
$BIG_5XX
STUB
chmod +x "$STUB_DIR/cosign"

fake_digest="sha256:$(printf 'a%.0s' {1..64})"
out="$(PATH="$STUB_DIR:$PATH" \
  COSIGN_SIGN_RETRY_ATTEMPTS=2 COSIGN_SIGN_RETRY_BACKOFF=0 \
  bash "$COSIGN_HELPER" "ghcr.io/example/image@${fake_digest}" 2>&1)" || true
if grep -q 'hit transient error' <<<"$out" && ! grep -q 'non-transient error' <<<"$out"; then
  pass "cosign_sign retries a large 5xx body"
else
  fail "cosign_sign misclassified a large 5xx body (broken-pipe regression)"
  printf '%s\n' "$out" | tail -n 3 >&2 || true
fi

# --- cosign_sign_with_retry.sh: a genuine error must NOT be retried -----
# Symmetric to the docker_push negative control: proves the cosign wrapper
# (which sources TRANSIENT_RE from the sibling helper and guards on it
# being non-empty) does not retry an auth denial. Catches a future
# over-broad regex OR a removed empty-regex guard.
cat >"$STUB_DIR/cosign" <<'STUB'
#!/usr/bin/env bash
echo "denied: requested access to the resource is denied"
exit 1
STUB
chmod +x "$STUB_DIR/cosign"
out="$(PATH="$STUB_DIR:$PATH" \
  COSIGN_SIGN_RETRY_ATTEMPTS=3 COSIGN_SIGN_RETRY_BACKOFF=0 \
  bash "$COSIGN_HELPER" "ghcr.io/example/image@${fake_digest}" 2>&1)" || true
if grep -q 'non-transient error' <<<"$out" && ! grep -q 'hit transient error' <<<"$out"; then
  pass "cosign_sign does not retry a genuine non-transient error"
else
  fail "cosign_sign retried a non-transient error (classifier too broad)"
  printf '%s\n' "$out" | tail -n 3 >&2 || true
fi

# --- cosign_sign_with_retry.sh: an already-signed digest is success -----
# A re-sign that loses the Rekor createLogEntry race returns a
# `createLogEntryConflict`; the helper treats that as success (exit 0,
# `::notice::`), not a retry or failure, so a re-run of an already-signed
# image does not false-red the build.
cat >"$STUB_DIR/cosign" <<'STUB'
#!/usr/bin/env bash
echo "error: ... createLogEntryConflict: ... already exists"
exit 1
STUB
chmod +x "$STUB_DIR/cosign"
rc=0
out="$(PATH="$STUB_DIR:$PATH" \
  COSIGN_SIGN_RETRY_ATTEMPTS=3 COSIGN_SIGN_RETRY_BACKOFF=0 \
  bash "$COSIGN_HELPER" "ghcr.io/example/image@${fake_digest}" 2>&1)" || rc=$?
if [ "$rc" -eq 0 ] && grep -q 'already signed' <<<"$out"; then
  pass "cosign_sign treats createLogEntryConflict as success"
else
  fail "cosign_sign did not treat createLogEntryConflict as idempotent success (rc=${rc})"
  printf '%s\n' "$out" | tail -n 3 >&2 || true
fi

if [ "$FAILED" -ne 0 ]; then
  printf '\nretry-helper self-test FAILED\n' >&2
  exit 1
fi
printf '\nretry-helper self-test passed\n'
