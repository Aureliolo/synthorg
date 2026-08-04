#!/usr/bin/env bash
# Guards the classification decisions in the cosign and docker-push retry
# helpers, driving the real scripts rather than a copy of their regex.
#
# The invariant that needs a test is that classification must not run
# through a pipe. Under `set -o pipefail`, `printf '%s' "$out" | grep -qiE`
# reports the WRITER's status when the reader exits first: a GHCR 5xx HTML
# error body overruns the 64 KB pipe buffer, so a match token near the
# start makes `grep -q` exit and close the pipe while `printf` is still
# writing, `printf` takes EPIPE, and the successful match is reported as
# failure. A transient error then reads as terminal and is not retried,
# which on the signing path leaves an image pushed but unsigned. Both
# helpers classify against a here-string, which is not a pipeline, so the
# status is purely grep's.
#
# The cases below feed that exact shape (match token first, >64 KB
# trailing body) through the helpers and assert a retry. Where pipe
# semantics never raise EPIPE the here-string form passes anyway, so this
# is a one-way guard and never a flake. Negative controls throughout prove
# the classifier did not become match-everything, and the positive cases
# assert the exit status as well as the log text, because "retried" is
# only correct if the failure still ends non-zero.
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

# Emit a >64 KB body with the transient match token FIRST, then filler:
# the shape that triggers EPIPE when classification runs through a pipe.
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

# --- shared classifier: a Sigstore Rekor/Fulcio tlog timeout retries -----
# The shared TRANSIENT_RE (single source in docker_push_with_retry.sh, also
# consumed by cosign sign / sign-blob via --print-transient-re) must
# classify a Rekor "network timeout" / "error fetching tlog entry" as
# transient. Driven through the docker_push helper, the canonical source,
# so a regression that drops the pattern fails here regardless of which
# consumer lost it.
out="$(DOCKER_PUSH_RETRY_ATTEMPTS=2 DOCKER_PUSH_RETRY_BACKOFF=0 \
  bash "$PUSH_HELPER" "selftest-rekor" \
  bash -c 'echo "error fetching tlog entry: network timeout at: https://rekor.sigstore.dev/api/v1/log/entries/108e9186"; exit 1' 2>&1)" || true
if grep -q 'hit transient registry error' <<<"$out" && ! grep -q 'non-transient error' <<<"$out"; then
  pass "shared classifier retries a Rekor tlog network timeout"
else
  fail "shared TRANSIENT_RE missing the Sigstore Rekor/Fulcio network-timeout signature"
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

# A local blob file for the sign-blob mode cases (cleaned by the STUB_DIR
# trap). The helper forwards everything after `sign-blob` to cosign, but
# the stub ignores its args and only controls the exit body, so the exact
# file contents do not matter.
blob_file="$STUB_DIR/checksums.txt"
printf 'deadbeef  artifact.tar.gz\n' >"$blob_file"

# --- cosign sign-blob mode: a Rekor tlog timeout must be retried --------
# sign-blob reaches the same Fulcio/Rekor backends as image signing; a
# transient "network timeout" reading/writing the Rekor tlog must retry,
# not fail. Reuses the >64 KB transient-token-first body so the blob path
# is also guarded against the broken-pipe misclassification.
REKOR_TIMEOUT='printf "Error: signing checksums.txt: network timeout at: https://rekor.sigstore.dev/api/v1/log/entries/108e9186 "; head -c 100000 </dev/zero | tr "\0" x; printf "\n"; exit 1'
cat >"$STUB_DIR/cosign" <<STUB
#!/usr/bin/env bash
$REKOR_TIMEOUT
STUB
chmod +x "$STUB_DIR/cosign"
out="$(PATH="$STUB_DIR:$PATH" \
  COSIGN_SIGN_RETRY_ATTEMPTS=2 COSIGN_SIGN_RETRY_BACKOFF=0 \
  bash "$COSIGN_HELPER" sign-blob "$blob_file" --bundle "${blob_file}.bundle" 2>&1)" || true
if grep -q 'hit transient error' <<<"$out" && ! grep -q 'non-transient error' <<<"$out"; then
  pass "cosign sign-blob retries a Rekor network timeout"
else
  fail "cosign sign-blob did not retry a Rekor network timeout (sign-blob mode or Rekor signature broken)"
  printf '%s\n' "$out" | tail -n 3 >&2 || true
fi

# --- cosign sign-blob mode: a genuine error must NOT be retried ---------
# Symmetric negative control for blob mode: an auth denial is terminal and
# must bubble immediately without retrying.
cat >"$STUB_DIR/cosign" <<'STUB'
#!/usr/bin/env bash
echo "denied: requested access to the resource is denied"
exit 1
STUB
chmod +x "$STUB_DIR/cosign"
out="$(PATH="$STUB_DIR:$PATH" \
  COSIGN_SIGN_RETRY_ATTEMPTS=3 COSIGN_SIGN_RETRY_BACKOFF=0 \
  bash "$COSIGN_HELPER" sign-blob "$blob_file" --bundle "${blob_file}.bundle" 2>&1)" || true
if grep -q 'non-transient error' <<<"$out" && ! grep -q 'hit transient error' <<<"$out"; then
  pass "cosign sign-blob does not retry a genuine non-transient error"
else
  fail "cosign sign-blob retried a non-transient error (classifier too broad)"
  printf '%s\n' "$out" | tail -n 3 >&2 || true
fi

# --- GHCR token-endpoint denial: transient, and only in that form -------
# GHCR answers a refused token exchange with `DENIED`, which is also how it
# answers a throttled one, so the classifier cannot read intent from the
# word and keys on the token endpoint instead.
#
# The fixture is an unmodified on-wire response (real scope string, real
# digest) rather than a reconstruction, so the classifier is exercised
# against the shape GHCR actually emits. Its phrasing is ggcr's, the client
# cosign signs through; `docker push` wraps its own denials differently.
# The docker_push case below therefore proves the SHARED classifier reaches
# that helper, not that a push emits this text.
#
# Exported rather than spliced into the stub's source: an interpolated
# heredoc would execute any `$(...)` or backtick a future edit introduced,
# at heredoc-expansion time, in this test's own shell.
export GHCR_TOKEN_DENIED_MSG='Error: signing [ghcr.io/aureliolo/synthorg-sandbox-base@sha256:dfe98965abdab49f17ac1a3e1057c7d3a66da3e39c8e6faaa49c526f1f9f58c6]: signing digest: failed to upload layer: GET https://ghcr.io/token?scope=repository%3Aaureliolo%2Fsynthorg-sandbox-base%3Apush%2Cpull&service=ghcr.io: DENIED: denied'

# rc is asserted alongside the log text on both positive cases. Matching
# the transient pattern only earns more attempts; if an exhausted ladder
# ever returned 0 the retry would have converted a denial into a silent
# success, and a log-text-only assertion would still pass.
rc=0
out="$(DOCKER_PUSH_RETRY_ATTEMPTS=2 DOCKER_PUSH_RETRY_BACKOFF=0 \
  bash "$PUSH_HELPER" "selftest-token-denied" \
  bash -c 'printf "%s\n" "$GHCR_TOKEN_DENIED_MSG"; exit 7' 2>&1)" || rc=$?
if [ "$rc" -eq 7 ] && grep -q 'hit transient registry error' <<<"$out" \
  && ! grep -q 'non-transient error' <<<"$out"; then
  pass "docker_push retries a GHCR token-endpoint denial and still fails"
else
  fail "docker_push mishandled a GHCR token-endpoint denial (rc=${rc}, want 7)"
  printf '%s\n' "$out" | tail -n 3 >&2 || true
fi

# Exhausting the ladder on this pattern must name it. The generic
# exhaustion line is indistinguishable from an outage, and the retry only
# exists because the response is ambiguous, so the terminal message has to
# carry that ambiguity to whoever reads the annotation panel. Asserted
# separately from the retry above because they are different defects: a
# lost retry and a lost diagnostic fail for different reasons, and each
# helper owns its own exhaustion branch, so neither one's assertion covers
# the other.
if grep -q 'refused to mint a token every time' <<<"$out"; then
  pass "docker_push names the ambiguity when a token denial exhausts the ladder"
else
  fail "docker_push fell back to the generic exhaustion message"
  printf '%s\n' "$out" | tail -n 3 >&2 || true
fi

cat >"$STUB_DIR/cosign" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$GHCR_TOKEN_DENIED_MSG"
exit 7
STUB
chmod +x "$STUB_DIR/cosign"
rc=0
out="$(PATH="$STUB_DIR:$PATH" \
  COSIGN_SIGN_RETRY_ATTEMPTS=2 COSIGN_SIGN_RETRY_BACKOFF=0 \
  bash "$COSIGN_HELPER" "ghcr.io/example/image@${fake_digest}" 2>&1)" || rc=$?
if [ "$rc" -eq 7 ] && grep -q 'hit transient error' <<<"$out" \
  && ! grep -q 'non-transient error' <<<"$out"; then
  pass "cosign_sign retries a GHCR token-endpoint denial and still fails"
else
  fail "cosign_sign mishandled a GHCR token-endpoint denial (rc=${rc}, want 7)"
  printf '%s\n' "$out" | tail -n 3 >&2 || true
fi

# The cosign helper's own exhaustion branch, held to the same bar; see the
# docker_push case above for why the generic line is not enough.
if grep -q 'refused to mint a token every time' <<<"$out"; then
  pass "cosign_sign names the ambiguity when a token denial exhausts the ladder"
else
  fail "cosign_sign fell back to the generic exhaustion message"
  printf '%s\n' "$out" | tail -n 3 >&2 || true
fi

# The token-endpoint prefix is what carries the classification, not the
# word. An uppercase `DENIED: denied` with no token URL must still be
# terminal, so the pattern cannot be trimmed to a bare `DENIED` without
# turning every repository-level permission error into a 4-attempt stall.
cat >"$STUB_DIR/cosign" <<'STUB'
#!/usr/bin/env bash
echo "unexpected status from PUT request to https://ghcr.io/v2/example/image/manifests/latest: DENIED: denied"
exit 1
STUB
chmod +x "$STUB_DIR/cosign"
out="$(PATH="$STUB_DIR:$PATH" \
  COSIGN_SIGN_RETRY_ATTEMPTS=3 COSIGN_SIGN_RETRY_BACKOFF=0 \
  bash "$COSIGN_HELPER" "ghcr.io/example/image@${fake_digest}" 2>&1)" || true
if grep -q 'non-transient error' <<<"$out" && ! grep -q 'hit transient error' <<<"$out"; then
  pass "cosign_sign keeps a non-token-endpoint DENIED terminal"
else
  fail "cosign_sign retried a DENIED outside the token endpoint (pattern too broad)"
  printf '%s\n' "$out" | tail -n 3 >&2 || true
fi

# The token endpoint answers a real permission gap with a plain repository
# denial carrying no token URL, so that shape must stay terminal and fast:
# it is the reason the retry's cost does not land on a misconfigured
# workflow. Guards against widening the pattern to the bare word.
cat >"$STUB_DIR/cosign" <<'STUB'
#!/usr/bin/env bash
echo "ERROR: failed to authorize: failed to fetch anonymous token: unexpected status from GET request to https://ghcr.io/token?scope=repository%3Aexample%2Fimage%3Apull&service=ghcr.io: 403 Forbidden"
exit 1
STUB
chmod +x "$STUB_DIR/cosign"
out="$(PATH="$STUB_DIR:$PATH" \
  COSIGN_SIGN_RETRY_ATTEMPTS=3 COSIGN_SIGN_RETRY_BACKOFF=0 \
  bash "$COSIGN_HELPER" "ghcr.io/example/image@${fake_digest}" 2>&1)" || true
if grep -q 'non-transient error' <<<"$out" && ! grep -q 'hit transient error' <<<"$out"; then
  pass "cosign_sign keeps a 403 token-endpoint refusal terminal"
else
  fail "cosign_sign retried a 403 token-endpoint refusal (pattern too broad)"
  printf '%s\n' "$out" | tail -n 3 >&2 || true
fi

if [ "$FAILED" -ne 0 ]; then
  printf '\nretry-helper self-test FAILED\n' >&2
  exit 1
fi
printf '\nretry-helper self-test passed\n'
