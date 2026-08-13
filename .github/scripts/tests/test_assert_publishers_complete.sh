#!/usr/bin/env bash
# Self-test for assert_publishers_complete.sh, the completeness precondition
# on `verify-signatures`.
#
# Asserts the contract properties:
#   1. All-success passes.
#   2. `skipped` is safe (the job never started, so it pushed nothing).
#   3. `failure` blocks, naming the job.
#   4. `cancelled` blocks. This is the case a first version missed: a job
#      killed by its own timeout-minutes reports `cancelled`, not `failure`,
#      having possibly already mutated GHCR.
#   5. An unrecognised future result value blocks, because the test is
#      against the safe values rather than the known-unsafe ones.
#   6. Every unsafe job is named, not just the first.
#   7. Missing input is a usage error (exit 2), distinct from a refusal (1).
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)" # .github/scripts
HELPER="$SCRIPT_DIR/assert_publishers_complete.sh"

FAILED=0
pass() { printf 'PASS: %s\n' "$*"; }
fail() {
  printf 'FAIL: %s\n' "$*" >&2
  FAILED=1
}

# Run the helper against a needs-shaped JSON blob, capturing output + code.
run_case() {
  RUN_OUT="$(bash "$HELPER" "$1" 2>&1)"
  RUN_RC=$?
  return 0
}

# --- 1. every job succeeded: passes -------------------------------------
run_case '{"version":{"result":"success"},"retag-backend":{"result":"success"}}'
if [ "$RUN_RC" -eq 0 ]; then
  pass "all-success passes"
else
  fail "all-success should pass (rc=${RUN_RC}): ${RUN_OUT}"
fi

# --- 2. skipped is safe --------------------------------------------------
run_case '{"retag-backend":{"result":"skipped"},"build-backend-publish":{"result":"success"}}'
if [ "$RUN_RC" -eq 0 ]; then
  pass "skipped is treated as safe"
else
  fail "skipped should pass (rc=${RUN_RC}): ${RUN_OUT}"
fi

# --- 3. failure blocks and names the job ---------------------------------
run_case '{"retag-openhands":{"result":"failure"},"retag-web":{"result":"success"}}'
if [ "$RUN_RC" -eq 1 ] && grep -q 'retag-openhands=failure' <<<"$RUN_OUT"; then
  pass "failure blocks and names the job"
else
  fail "failure should block naming the job (rc=${RUN_RC}): ${RUN_OUT}"
fi

# --- 4. cancelled blocks -------------------------------------------------
# The regression case. A retag job reaped at timeout-minutes reports
# `cancelled` after its imagetools push already mutated GHCR.
run_case '{"retag-fine-tune":{"result":"cancelled"},"retag-web":{"result":"success"}}'
if [ "$RUN_RC" -eq 1 ] && grep -q 'retag-fine-tune=cancelled' <<<"$RUN_OUT"; then
  pass "cancelled blocks and names the job"
else
  fail "cancelled should block naming the job (rc=${RUN_RC}): ${RUN_OUT}"
fi

# --- 5. an unknown future result value blocks ----------------------------
run_case '{"retag-sidecar":{"result":"neutral"}}'
if [ "$RUN_RC" -eq 1 ] && grep -q 'retag-sidecar=neutral' <<<"$RUN_OUT"; then
  pass "an unrecognised result value blocks"
else
  fail "unknown result should block (rc=${RUN_RC}): ${RUN_OUT}"
fi

# --- 6. every unsafe job is named ----------------------------------------
run_case '{"a":{"result":"failure"},"b":{"result":"cancelled"},"c":{"result":"success"}}'
if [ "$RUN_RC" -eq 1 ] &&
  grep -q 'a=failure' <<<"$RUN_OUT" &&
  grep -q 'b=cancelled' <<<"$RUN_OUT"; then
  pass "every unsafe job is named, not just the first"
else
  fail "both unsafe jobs should be named (rc=${RUN_RC}): ${RUN_OUT}"
fi

# --- 7. missing input is a usage error, not a silent pass ----------------
RUN_OUT="$(bash "$HELPER" 2>&1)"
RUN_RC=$?
if [ "$RUN_RC" -eq 2 ]; then
  pass "missing input exits 2 (usage), never 0"
else
  fail "missing input should exit 2 (rc=${RUN_RC}): ${RUN_OUT}"
fi

if [ "$FAILED" -ne 0 ]; then
  printf '\nassert_publishers_complete self-test FAILED\n' >&2
  exit 1
fi
printf '\nassert_publishers_complete self-test passed\n'
