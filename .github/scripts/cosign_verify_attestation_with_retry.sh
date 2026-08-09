#!/usr/bin/env bash
# Prove an OpenVEX attestation is attached to a published digest AND was
# signed by one of the workflows allowed to sign it.
#
# An attestation that failed to land leaves the image claiming a triage it
# does not carry, and the consumer's scanner reports findings we promised were
# answered. cosign resolves the attestation the same way that scanner will,
# which no referrer-index probe can promise.
#
# GHCR is eventually consistent on a referrer it just accepted, so the check
# is a bounded poll rather than a single shot. The last attempt runs with
# cosign's diagnostics intact, so a genuinely missing attestation names itself
# instead of exiting on a swallowed error.
#
# Identity is pinned here rather than passed in. The pins name the two
# reusable workflows that can reach the composite calling this, on main only,
# in this repository. A caller-supplied pattern would let the thing being
# checked choose the check, which is the failure `check_signing_identity_pins.py`
# exists to prevent one layer up.
#
# The SAN alone would not be enough. Our reusable workflows are public, and a
# `workflow_call` job's certificate names the reusable workflow's own path, so
# any repository calling ours mints the same SAN. Binding the repository is
# what makes this a check on our artefact rather than on our recipe.
#
# Identity is not the whole check. A digest that was published before, with
# an older ledger, still carries that older attestation, and it satisfies the
# identity policy exactly as a current one does. So the document's `@id` --
# content-addressed over its statements -- is compared against the document
# this run set out to publish. Without that, "an attestation is attached"
# would be mistaken for "the triage on this image is the one just reviewed".
#
# Usage:
#   cosign_verify_attestation_with_retry.sh <ref> <predicate>
#     <ref> is the full image reference by digest, e.g.
#     ghcr.io/aureliolo/synthorg-sandbox@sha256:abc...
#     <predicate> is the OpenVEX document that should be attached.
#
# Behaviour:
#   - Exits 0 only when cosign verifies an OpenVEX attestation for <ref>
#     against the pinned identity AND that attestation carries <predicate>'s
#     `@id`.
#   - Exits non-zero on a genuinely missing, unsigned, wrongly signed, or
#     stale attestation, after the bounded poll is exhausted.
set -euo pipefail

REF="${1:-}"
PREDICATE="${2:-}"
if [ -z "$REF" ] || [ -z "$PREDICATE" ]; then
  echo "::error::usage: cosign_verify_attestation_with_retry.sh <ref> <predicate>" >&2
  exit 2
fi
if [[ "$REF" != *@sha256:* ]]; then
  echo "::error::ref must pin a digest (…@sha256:…); got '${REF}'" >&2
  exit 2
fi
if [ ! -f "$PREDICATE" ]; then
  echo "::error::predicate not found at ${PREDICATE}" >&2
  exit 2
fi

EXPECTED_ID="$(jq -er '.["@id"]' "$PREDICATE")"

# Anchored at both ends: cosign matches with Go's `regexp.MatchString`, which
# is a search rather than a full match, so an unanchored pattern would accept
# any identity merely containing this one.
SAN_RE='^https://github\.com/Aureliolo/synthorg/\.github/workflows/reusable-publish-image(-loaded)?\.yml@refs/heads/main$'
OIDC_ISSUER='https://token.actions.githubusercontent.com'
SIGNING_REPOSITORY='Aureliolo/synthorg'

ATTEMPTS="${COSIGN_VERIFY_RETRY_ATTEMPTS:-5}"
BACKOFF="${COSIGN_VERIFY_RETRY_BACKOFF:-2}"
if ! [[ "$ATTEMPTS" =~ ^[1-9][0-9]*$ ]]; then
  echo "::error::COSIGN_VERIFY_RETRY_ATTEMPTS must be a positive integer; got '${ATTEMPTS}'" >&2
  exit 2
fi
if ! [[ "$BACKOFF" =~ ^[0-9]+$ ]]; then
  echo "::error::COSIGN_VERIFY_RETRY_BACKOFF must be a non-negative integer; got '${BACKOFF}'" >&2
  exit 2
fi

verify() {
  cosign verify-attestation --type openvex \
    --certificate-identity-regexp "$SAN_RE" \
    --certificate-oidc-issuer "$OIDC_ISSUER" \
    --certificate-github-workflow-repository "$SIGNING_REPOSITORY" \
    "$REF"
}

# cosign emits one DSSE envelope per verified attestation, newline-delimited
# when there is more than one. `payload` is base64 of an in-toto statement
# whose `predicate` is the OpenVEX document. Envelope shape is DSSE's, not
# cosign's, so it is stable across cosign releases.
attested_ids() {
  jq -r '.payload | @base64d | fromjson | .predicate["@id"] // empty'
}

attempt() {
  local envelopes ids
  envelopes="$(verify)" || return 1
  ids="$(attested_ids <<<"$envelopes")" || return 1
  if [ -z "$ids" ]; then
    # Distinguished from "no attestation": cosign verified something, and
    # nothing in it looked like an OpenVEX document. Failing closed rather
    # than reading an unparsed envelope as agreement.
    echo "::error::verified an attestation for ${REF} carrying no OpenVEX '@id'; refusing to read that as a match" >&2
    return 1
  fi
  if ! grep -qxF "$EXPECTED_ID" <<<"$ids"; then
    echo "::error::attestation on ${REF} carries a different triage than this run published (expected ${EXPECTED_ID}, found: $(tr '\n' ' ' <<<"$ids"))" >&2
    return 1
  fi
  return 0
}

for ((i = 1; i < ATTEMPTS; i++)); do
  if attempt 2>/dev/null; then
    echo "OpenVEX attestation present, signed, and current for ${REF}"
    exit 0
  fi
  echo "::warning::OpenVEX attestation for ${REF} not yet verifiable (attempt ${i}/${ATTEMPTS}); GHCR propagation lag, retrying in ${BACKOFF}s" >&2
  sleep "$BACKOFF"
  BACKOFF=$((BACKOFF * 2))
done

# No `if` around this one: under `set -e` a non-zero return here ends the
# script, so there is no path that reaches the success line without cosign
# having verified the attestation AND its `@id` having matched.
attempt
echo "OpenVEX attestation present, signed, and current for ${REF}"
