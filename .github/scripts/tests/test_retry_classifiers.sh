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
VERIFY_HELPER="$SCRIPT_DIR/cosign_verify_attestation_with_retry.sh"
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
if [ "$rc" -eq 0 ] && grep -q 'already recorded in Rekor' <<<"$out"; then
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

# A predicate file for the attest mode cases (cleaned by the STUB_DIR trap).
vex_file="$STUB_DIR/synthorg.openvex.json"
printf '{"@context":"https://openvex.dev/ns/v0.2.0","statements":[]}\n' >"$vex_file"

# --- cosign attest mode: a Rekor tlog timeout must be retried -----------
# The VEX attestation reaches the same Fulcio/Rekor backends as image
# signing, and it runs in the publish path, so a transient failure there
# would leave a published image without the triage it claims to carry.
cat >"$STUB_DIR/cosign" <<STUB
#!/usr/bin/env bash
$REKOR_TIMEOUT
STUB
chmod +x "$STUB_DIR/cosign"
out="$(PATH="$STUB_DIR:$PATH" \
  COSIGN_SIGN_RETRY_ATTEMPTS=2 COSIGN_SIGN_RETRY_BACKOFF=0 \
  bash "$COSIGN_HELPER" attest "ghcr.io/example/image@${fake_digest}" \
  --type openvex --predicate "$vex_file" 2>&1)" || true
if grep -q 'hit transient error' <<<"$out" && ! grep -q 'non-transient error' <<<"$out"; then
  pass "cosign attest retries a Rekor network timeout"
else
  fail "cosign attest did not retry a Rekor network timeout (attest mode or Rekor signature broken)"
  printf '%s\n' "$out" | tail -n 3 >&2 || true
fi

# --- cosign attest mode: a genuine error must NOT be retried ------------
cat >"$STUB_DIR/cosign" <<'STUB'
#!/usr/bin/env bash
echo "denied: requested access to the resource is denied"
exit 1
STUB
chmod +x "$STUB_DIR/cosign"
out="$(PATH="$STUB_DIR:$PATH" \
  COSIGN_SIGN_RETRY_ATTEMPTS=3 COSIGN_SIGN_RETRY_BACKOFF=0 \
  bash "$COSIGN_HELPER" attest "ghcr.io/example/image@${fake_digest}" \
  --type openvex --predicate "$vex_file" 2>&1)" || true
if grep -q 'non-transient error' <<<"$out" && ! grep -q 'hit transient error' <<<"$out"; then
  pass "cosign attest does not retry a genuine non-transient error"
else
  fail "cosign attest retried a non-transient error (classifier too broad)"
  printf '%s\n' "$out" | tail -n 3 >&2 || true
fi

# --- cosign attest mode: a re-attested digest is success ----------------
# A Rekor conflict means the exact canonicalized entry being submitted is
# already logged, which only a resubmission of this invocation's own
# envelope produces. Treating that as failure would red-light every re-run
# of an already-published image.
cat >"$STUB_DIR/cosign" <<'STUB'
#!/usr/bin/env bash
echo "error: ... createLogEntryConflict: ... already exists"
exit 1
STUB
chmod +x "$STUB_DIR/cosign"
rc=0
out="$(PATH="$STUB_DIR:$PATH" \
  COSIGN_SIGN_RETRY_ATTEMPTS=3 COSIGN_SIGN_RETRY_BACKOFF=0 \
  bash "$COSIGN_HELPER" attest "ghcr.io/example/image@${fake_digest}" \
  --type openvex --predicate "$vex_file" 2>&1)" || rc=$?
if [ "$rc" -eq 0 ] && grep -q 'already recorded in Rekor' <<<"$out"; then
  pass "cosign attest treats createLogEntryConflict as success"
else
  fail "cosign attest did not treat createLogEntryConflict as idempotent success (rc=${rc})"
  printf '%s\n' "$out" | tail -n 3 >&2 || true
fi

# --- cosign attest mode: the ref goes last, the flags stay in order -----
# `cosign attest` takes the image as its final positional. A helper that
# forwarded them the other way round would still exit 0 against a stub and
# only fail against the real binary, in the publish path, after merge.
cat >"$STUB_DIR/cosign" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*"
STUB
chmod +x "$STUB_DIR/cosign"
out="$(PATH="$STUB_DIR:$PATH" \
  bash "$COSIGN_HELPER" attest "ghcr.io/example/image@${fake_digest}" \
  --type openvex --predicate "$vex_file" 2>&1)" || true
if grep -q "attest --yes --type openvex --predicate ${vex_file} ghcr.io/example/image@${fake_digest}\$" <<<"$out"; then
  pass "cosign attest passes the ref last, after the forwarded flags"
else
  fail "cosign attest built the wrong argument order"
  printf '%s\n' "$out" | tail -n 3 >&2 || true
fi

# The document this run would publish, and a stub that answers with a DSSE
# envelope carrying a given `@id` (the shape cosign emits per verified
# attestation: base64 `payload` over an in-toto statement).
published_doc="$STUB_DIR/published.openvex.json"
published_id="https://github.com/Aureliolo/synthorg/.github/vex/synthorg-openvex-aaaa"
printf '{"@id":"%s","statements":[]}\n' "$published_id" >"$published_doc"

write_envelope_stub() {
  local attested_id="$1"
  local payload
  payload="$(printf '{"predicate":{"@id":"%s"}}' "$attested_id" | base64 -w0)"
  cat >"$STUB_DIR/cosign" <<STUB
#!/usr/bin/env bash
printf '{"payloadType":"application/vnd.in-toto+json","payload":"%s"}\n' "$payload"
STUB
  chmod +x "$STUB_DIR/cosign"
}

# --- verify-attestation: the published document passes ------------------
write_envelope_stub "$published_id"
rc=0
out="$(PATH="$STUB_DIR:$PATH" \
  COSIGN_VERIFY_RETRY_ATTEMPTS=3 COSIGN_VERIFY_RETRY_BACKOFF=0 \
  bash "$VERIFY_HELPER" "ghcr.io/example/image@${fake_digest}" "$published_doc" 2>&1)" || rc=$?
if [ "$rc" -eq 0 ] && grep -q 'present, signed, and current' <<<"$out"; then
  pass "verify-attestation passes when the published document is attached"
else
  fail "verify-attestation failed on its own published document (rc=${rc})"
  printf '%s\n' "$out" | tail -n 3 >&2 || true
fi

# --- verify-attestation: an absent attestation FAILS the job ------------
# The one property that matters. This step exists so a published image
# cannot claim a triage it does not carry, and a poll that fell through to
# exit 0 would assert exactly that, silently, on every image.
cat >"$STUB_DIR/cosign" <<'STUB'
#!/usr/bin/env bash
echo "Error: no matching attestations" >&2
exit 1
STUB
chmod +x "$STUB_DIR/cosign"
rc=0
out="$(PATH="$STUB_DIR:$PATH" \
  COSIGN_VERIFY_RETRY_ATTEMPTS=2 COSIGN_VERIFY_RETRY_BACKOFF=0 \
  bash "$VERIFY_HELPER" "ghcr.io/example/image@${fake_digest}" "$published_doc" 2>&1)" || rc=$?
if [ "$rc" -ne 0 ] && ! grep -q 'present, signed, and current' <<<"$out"; then
  pass "verify-attestation fails closed when nothing resolves"
else
  fail "verify-attestation exited 0 with no attestation present (rc=${rc})"
  printf '%s\n' "$out" | tail -n 3 >&2 || true
fi

# --- verify-attestation: a STALE attestation FAILS the job --------------
# A digest published earlier under an older ledger still carries that
# older attestation, and it satisfies the identity policy exactly as a
# current one does. Identity alone would read that as success and let this
# run report a triage it never actually attached.
write_envelope_stub "https://github.com/Aureliolo/synthorg/.github/vex/synthorg-openvex-bbbb"
rc=0
out="$(PATH="$STUB_DIR:$PATH" \
  COSIGN_VERIFY_RETRY_ATTEMPTS=2 COSIGN_VERIFY_RETRY_BACKOFF=0 \
  bash "$VERIFY_HELPER" "ghcr.io/example/image@${fake_digest}" "$published_doc" 2>&1)" || rc=$?
if [ "$rc" -ne 0 ] && grep -q 'different triage than this run published' <<<"$out"; then
  pass "verify-attestation fails closed on a stale attestation"
else
  fail "verify-attestation accepted an attestation of a different document (rc=${rc})"
  printf '%s\n' "$out" | tail -n 3 >&2 || true
fi

# --- verify-attestation: a verified envelope with no @id is not a match -
# cosign verified something and nothing in it looked like an OpenVEX
# document. Reading an unparsed envelope as agreement is the one way this
# check could pass while having checked nothing.
cat >"$STUB_DIR/cosign" <<'STUB'
#!/usr/bin/env bash
printf '{"payloadType":"application/vnd.in-toto+json","payload":"eyJwcmVkaWNhdGUiOnt9fQ=="}\n'
STUB
chmod +x "$STUB_DIR/cosign"
rc=0
out="$(PATH="$STUB_DIR:$PATH" \
  COSIGN_VERIFY_RETRY_ATTEMPTS=2 COSIGN_VERIFY_RETRY_BACKOFF=0 \
  bash "$VERIFY_HELPER" "ghcr.io/example/image@${fake_digest}" "$published_doc" 2>&1)" || rc=$?
if [ "$rc" -ne 0 ] && grep -q "carrying no OpenVEX" <<<"$out"; then
  pass "verify-attestation fails closed on an envelope carrying no OpenVEX id"
else
  fail "verify-attestation read an unparsed envelope as a match (rc=${rc})"
  printf '%s\n' "$out" | tail -n 3 >&2 || true
fi

# --- verify-attestation: GHCR propagation lag is polled through ---------
# GHCR is eventually consistent on a referrer it just accepted, so a first
# miss is expected rather than terminal.
counter="$STUB_DIR/verify_attempts"
printf '0\n' >"$counter"
envelope_payload="$(printf '{"predicate":{"@id":"%s"}}' "$published_id" | base64 -w0)"
cat >"$STUB_DIR/cosign" <<STUB
#!/usr/bin/env bash
n=\$(cat "$counter")
n=\$((n + 1))
printf '%s\n' "\$n" >"$counter"
[ "\$n" -ge 2 ] || exit 1
printf '{"payloadType":"application/vnd.in-toto+json","payload":"%s"}\n' "$envelope_payload"
STUB
chmod +x "$STUB_DIR/cosign"
rc=0
out="$(PATH="$STUB_DIR:$PATH" \
  COSIGN_VERIFY_RETRY_ATTEMPTS=4 COSIGN_VERIFY_RETRY_BACKOFF=0 \
  bash "$VERIFY_HELPER" "ghcr.io/example/image@${fake_digest}" "$published_doc" 2>&1)" || rc=$?
if [ "$rc" -eq 0 ] && grep -q 'not yet verifiable' <<<"$out"; then
  pass "verify-attestation polls through GHCR propagation lag"
else
  fail "verify-attestation did not retry a first miss (rc=${rc})"
  printf '%s\n' "$out" | tail -n 3 >&2 || true
fi

# --- verify-attestation: the identity pins actually reach cosign --------
# The SAN alone is shared by every repository that calls our public
# reusable workflows, so the repository binding is what makes this a check
# on our artefact. An anchored SAN matters just as much: cosign matches
# with a search, not a full match.
verify_args="$STUB_DIR/verify_args"
cat >"$STUB_DIR/cosign" <<STUB
#!/usr/bin/env bash
printf '%s\n' "\$*" >"$verify_args"
printf '{"payloadType":"application/vnd.in-toto+json","payload":"%s"}\n' "$envelope_payload"
STUB
chmod +x "$STUB_DIR/cosign"
PATH="$STUB_DIR:$PATH" \
  COSIGN_VERIFY_RETRY_ATTEMPTS=2 COSIGN_VERIFY_RETRY_BACKOFF=0 \
  bash "$VERIFY_HELPER" "ghcr.io/example/image@${fake_digest}" "$published_doc" >/dev/null 2>&1 || true
args="$(cat "$verify_args" 2>/dev/null || true)"
if grep -q -- '--certificate-github-workflow-repository Aureliolo/synthorg' <<<"$args" &&
  grep -q -- '--certificate-oidc-issuer https://token.actions.githubusercontent.com' <<<"$args" &&
  grep -q -- '--type openvex' <<<"$args" &&
  grep -qE -- '--certificate-identity-regexp \^[^ ]+\$( |$)' <<<"$args"; then
  pass "verify-attestation pins an anchored SAN, the issuer and the repository"
else
  fail "verify-attestation dropped or loosened an identity pin"
  printf '%s\n' "$args" >&2 || true
fi

# --- verify-attestation: a tag-shaped ref is refused --------------------
# An attestation is attached to a digest. Verifying a tag would check
# whatever the tag points at now, which a later push can change.
rc=0
out="$(PATH="$STUB_DIR:$PATH" \
  bash "$VERIFY_HELPER" "ghcr.io/example/image:latest" "$published_doc" 2>&1)" || rc=$?
if [ "$rc" -ne 0 ] && grep -q 'must pin a digest' <<<"$out"; then
  pass "verify-attestation refuses a ref that does not pin a digest"
else
  fail "verify-attestation accepted a tag-shaped ref (rc=${rc})"
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
