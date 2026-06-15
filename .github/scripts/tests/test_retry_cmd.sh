#!/usr/bin/env bash
# Self-test for retry_cmd.sh: the generic retry-everything, fail-closed,
# streamed-output helper used to wrap idempotent network installs (uv
# sync, apt, curl downloads, GHCR token mint).
#
# Asserts the three contract properties:
#   1. A command that fails then succeeds is retried to success (exit 0).
#   2. A command that always fails bubbles the LAST exit code (fail-closed),
#      NOT 0 and NOT the gh_with_retry soft-skip 75.
#   3. A command that succeeds first try is not retried (no wasted budget).
# All cases run with zero backoff via RETRY_CMD_BASE_DELAY=0.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)" # .github/scripts
HELPER="$SCRIPT_DIR/retry_cmd.sh"

FAILED=0
pass() { printf 'PASS: %s\n' "$*"; }
fail() {
  printf 'FAIL: %s\n' "$*" >&2
  FAILED=1
}

# A counter file lets a stubbed command fail a fixed number of times then
# succeed, exercising the retry-then-succeed path deterministically.
WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

# --- 1. fails twice, then succeeds: retried to success (exit 0) ---------
# A self-contained stub that fails on attempts 1-2 and succeeds on attempt 3
# by incrementing a counter file. Written to a file (rather than a
# `bash -c '...' $0`-positional trick) so the counter path is explicit and
# robust to however retry_cmd.sh forwards "$@".
counter="$WORK_DIR/count1"
printf '0' >"$counter"
flaky_script="$WORK_DIR/flaky.sh"
cat >"$flaky_script" <<EOF
#!/usr/bin/env bash
c=\$(cat "$counter")
n=\$((c + 1))
printf '%s' "\$n" >"$counter"
[ "\$n" -ge 3 ]
EOF
chmod +x "$flaky_script"
rc=0
out="$(RETRY_CMD_ATTEMPTS=5 RETRY_CMD_BASE_DELAY=0 \
  bash "$HELPER" "selftest-flaky" "$flaky_script" 2>&1)" || rc=$?
if [ "$rc" -eq 0 ] && grep -q 'succeeded on attempt 3' <<<"$out"; then
  pass "retry_cmd retries a flaky command to success"
else
  fail "retry_cmd did not retry-to-success (rc=${rc})"
  printf '%s\n' "$out" | tail -n 3 >&2 || true
fi

# --- 2. always fails: bubbles the command's exit code, NOT 0 / NOT 75 ---
rc=0
out="$(RETRY_CMD_ATTEMPTS=3 RETRY_CMD_BASE_DELAY=0 \
  bash "$HELPER" "selftest-hardfail" bash -c 'exit 7' 2>&1)" || rc=$?
if [ "$rc" -eq 7 ] && grep -q 'failed after 3 attempts' <<<"$out"; then
  pass "retry_cmd fails closed with the command's own exit code"
else
  fail "retry_cmd did not fail closed with the wrapped exit code (rc=${rc}, expected 7)"
  printf '%s\n' "$out" | tail -n 3 >&2 || true
fi

# --- 3. succeeds first try: no retry, no warning noise -------------------
rc=0
out="$(RETRY_CMD_ATTEMPTS=5 RETRY_CMD_BASE_DELAY=0 \
  bash "$HELPER" "selftest-clean" bash -c 'exit 0' 2>&1)" || rc=$?
if [ "$rc" -eq 0 ] && ! grep -q 'retrying' <<<"$out" && ! grep -q 'succeeded on attempt' <<<"$out"; then
  pass "retry_cmd does not retry a first-try success"
else
  fail "retry_cmd added retry noise to a clean success (rc=${rc})"
  printf '%s\n' "$out" | tail -n 3 >&2 || true
fi

# --- 4. no command after the label: usage error exit 2 ------------------
rc=0
out="$(bash "$HELPER" "selftest-nocmd" 2>&1)" || rc=$?
if [ "$rc" -eq 2 ] && grep -q 'no command supplied' <<<"$out"; then
  pass "retry_cmd exits 2 with a usage error when no command is supplied"
else
  fail "retry_cmd did not reject a missing command with exit 2 (rc=${rc})"
  printf '%s\n' "$out" | tail -n 3 >&2 || true
fi

# --- 5. delay is capped at RETRY_CMD_MAX_DELAY --------------------------
# base=1, cap=1, 4 attempts on an always-failing command: each backoff
# warning must say "retrying in 1s" and never "in 2s" (which is what an
# uncapped doubling 1 -> 2 -> 4 would emit). ~3s of real sleep.
rc=0
out="$(RETRY_CMD_ATTEMPTS=4 RETRY_CMD_BASE_DELAY=1 RETRY_CMD_MAX_DELAY=1 \
  bash "$HELPER" "selftest-cap" bash -c 'exit 1' 2>&1)" || rc=$?
if grep -q 'retrying in 1s' <<<"$out" && ! grep -q 'retrying in 2s' <<<"$out"; then
  pass "retry_cmd caps the backoff delay at RETRY_CMD_MAX_DELAY"
else
  fail "retry_cmd did not cap the backoff delay (rc=${rc})"
  printf '%s\n' "$out" | tail -n 3 >&2 || true
fi

if [ "$FAILED" -ne 0 ]; then
  printf '\nretry_cmd self-test FAILED\n' >&2
  exit 1
fi
printf '\nretry_cmd self-test passed\n'
