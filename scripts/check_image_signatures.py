#!/usr/bin/env python3
r"""Verify cosign signatures and SLSA provenance for SynthOrg images on GHCR.

Run as a workflow gate after all publish jobs finish: for every
``(image, tag)`` pair pushed by the workflow, resolve the tag to its
manifest digest, then confirm both a cosign signature artifact and a
SLSA provenance attestation (both published into the ``sha256-<hex>``
referrer tag) exist for that digest. Fails loudly if any tag is
unsigned, unattested, or if two tags of the same image diverge to
different digests.

Provenance is checked, not just the signature: a signature proves who
pushed the bytes, provenance proves how they were built, and the project
claims SLSA Build L3 on the strength of the latter. Checking only the
signature would leave that claim asserted rather than enforced.

Designed to catch the failure mode where two concurrent ``build-images.yml``
runs (e.g. main-push + tag-push for a release SHA) race on shared
per-arch tags and end up signing different manifest list digests --
leaving the user-facing tag (``synthorg-sandbox:0.7.6-dev.9``)
unsigned. The per-publish-step verification in
``.github/actions/publish-image/action.yml`` covers the in-step path;
this gate covers cross-job races and post-sign tag overwrites.

Usage:

    python3 scripts/check_image_signatures.py \
        --repo-prefix aureliolo/synthorg- \
        --image-tags backend:0.7.6-dev.9,backend:dev,backend:sha-abc1234 \
        --image-tags sandbox:0.7.6-dev.9,sandbox:dev,sandbox:sha-abc1234

Auth: reads ``GHCR_TOKEN`` (a GitHub Actions ``GITHUB_TOKEN`` is
sufficient for read-only access to public GHCR packages). Falls back
to ``GITHUB_TOKEN``, then anonymous (works for public packages, may
rate-limit).

Exit codes:
    0 on success, 1 on signature-verification failure, 2 on usage
    error (bad args, no inputs).
"""

import argparse
import base64
import dataclasses
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Final, override

GHCR_REGISTRY: Final[str] = "ghcr.io"
REPO_PREFIX_DEFAULT: Final[str] = "aureliolo/synthorg-"
MANIFEST_ACCEPT: Final[str] = (
    "application/vnd.docker.distribution.manifest.list.v2+json,"
    "application/vnd.oci.image.index.v1+json,"
    "application/vnd.docker.distribution.manifest.v2+json,"
    "application/vnd.oci.image.manifest.v1+json"
)
SIG_ACCEPT: Final[str] = "application/vnd.oci.image.index.v1+json"
HTTP_TIMEOUT_SECONDS: Final[int] = 30
HTTP_OK: Final[int] = 200
HTTP_NOT_FOUND: Final[int] = 404

# `actions/attest` publishes a sigstore bundle referrer carrying the
# predicate type in this annotation.
PROVENANCE_PREDICATE: Final[str] = "https://slsa.dev/provenance/v1"
_PREDICATE_ANNOTATION: Final[str] = "dev.sigstore.bundle.predicateType"
USAGE_EXIT_CODE: Final[int] = 2
FAILURE_EXIT_CODE: Final[int] = 1

# GHCR surfaces a freshly-published cosign referrer (the `sha256-<hex>`
# tag) with eventual consistency. In the typical workflow topology this
# gate runs as a separate job minutes after the publish jobs, so
# propagation is normally settled by the time it runs; the short retry only
# covers the rare case where the gap between signing and this check is
# unusually short. Kept deliberately short -- the gate's purpose is to FAIL
# a genuinely-unsigned image, so a persistent 404 must surface quickly
# rather than be masked by a long wait.
SIG_PROPAGATION_ATTEMPTS: Final[int] = 3
SIG_PROPAGATION_BACKOFF_SECONDS: Final[int] = 3

# Transient-error retry budget for the registry calls that are NOT the
# eventual-consistency referrer poll: the token mint and the tag->digest
# HEAD. A single GHCR network blip (``i/o timeout``, connection reset) or a
# 5xx on either of these would otherwise red the whole verify-signatures
# gate for an image that is in fact correctly published and signed -- the
# same class of failure the publish actions already absorb via
# docker_push_with_retry.sh. Kept short: a 4xx (auth, genuine 404) is a real
# answer and is returned immediately, never retried.
REGISTRY_RETRY_ATTEMPTS: Final[int] = 4
REGISTRY_RETRY_BACKOFF_SECONDS: Final[int] = 5
HTTP_SERVER_ERROR_MIN: Final[int] = 500

# Anchored allowlist patterns, applied to every value that flows into
# the registry URL path. Closes the partial-SSRF window CodeQL flags
# (rule py/partial-ssrf): the registry hostname is hardcoded to
# `ghcr.io`, but the path-component values come from CLI args. Without
# strict validation, a value containing `..`, `/`, NUL, or CR/LF could
# coerce the request into a different host or endpoint.
#
# Repo prefix grammar: lowercase-with-dash component segments separated
# by `/`, ending in a literal `-` (we use it as a prefix to the image
# name). Mirrors the OCI distribution spec name grammar
# (https://github.com/opencontainers/distribution-spec/blob/main/spec.md#pulling-manifests).
# `\Z` (not `$`) so a trailing newline in user input doesn't slip past
# the anchor; with `$`, "foo\n" would match the empty string before the
# newline.
_REPO_PREFIX_RE: Final = re.compile(
    r"\A(?:[a-z0-9]+(?:[._-][a-z0-9]+)*/)+(?:[a-z0-9]+(?:[._-][a-z0-9]+)*)-\Z"
)
# Image suffix appended to repo_prefix: lowercase Docker name segment
# with no `/` (already in prefix).
_IMAGE_NAME_RE: Final = re.compile(r"\A[a-z0-9]+(?:[._-][a-z0-9]+)*\Z")
# OCI tag grammar: 1-128 chars from [A-Za-z0-9_], plus `.` and `-` but
# not as the first character.
_TAG_RE: Final = re.compile(r"\A[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}\Z")

# Immutable build-identity tags: a tag that pins to exactly ONE build and
# must never be repointed. Two metadata-action conventions qualify:
#   - ``sha-<short>`` (commit-pinned; `type=sha,prefix=sha-`)
#   - full semver ``X.Y.Z`` / ``X.Y.Z-dev.N`` (`type=semver,pattern={{version}}`)
# Every OTHER tag the workflow pushes is FLOATING (``dev``, ``latest``,
# ``X.Y``, ``X``): it advances to the newest build by design, so a
# concurrent later release legitimately repoints it. Convergence is only an
# invariant ACROSS immutable tags; floating tags are still individually
# signature-checked by ``_verify_pair``. Keying on these two fixed naming
# conventions (rather than enumerating floating names) keeps the rule from
# drifting if the floating-tag policy grows.
#
# The semver pattern tolerates an optional ``v``/``V`` prefix. The current
# metadata-action ``{{version}}`` policy strips it (image tags are
# ``0.8.9`` / ``0.8.9-dev.51``, never ``v0.8.9``), but a future tag-policy
# change emitting a ``v``-prefixed immutable tag would otherwise be
# misclassified as floating and silently dropped from the convergence check.
_SHA_TAG_RE: Final = re.compile(r"\Asha-[0-9a-f]{7,}\Z")
_FULL_SEMVER_TAG_RE: Final = re.compile(
    r"\A[vV]?[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?\Z"
)


def _is_immutable_tag(tag: str) -> bool:
    """Return True for tags that pin to exactly one build.

    ``sha-<short>`` and full ``X.Y.Z[-pre]`` semver tags identify a single
    build and must agree on its digest, so the convergence check applies to
    them. Floating tags (``dev``, ``latest``, ``X.Y``, ``X``) advance to the
    newest build by design and are excluded; they remain signature-checked
    individually upstream.
    """
    return bool(_SHA_TAG_RE.match(tag) or _FULL_SEMVER_TAG_RE.match(tag))


@dataclasses.dataclass(frozen=True)
class ImageTag:
    """An ``(image, tag)`` pair to verify."""

    image: str
    tag: str

    @override
    def __str__(self) -> str:
        """Render as ``image:tag`` for log output."""
        return f"{self.image}:{self.tag}"


def _validate_repo_prefix(repo_prefix: str) -> None:
    """Reject a malformed ``--repo-prefix`` value.

    Raises:
        SystemExit: with code 2 if the prefix doesn't match the
            sanctioned grammar.
    """
    if not _REPO_PREFIX_RE.match(repo_prefix):
        msg = (
            f"::error::--repo-prefix {repo_prefix!r} must match the OCI repo grammar "
            "(lowercase, '.', '_', '-', '/'), and end with '-' (e.g. "
            "'aureliolo/synthorg-')"
        )
        print(msg, file=sys.stderr)
        raise SystemExit(USAGE_EXIT_CODE)


def _validate_image_tag(pair: ImageTag) -> None:
    """Reject malformed image or tag values.

    Raises:
        SystemExit: with code 2 if either component fails the OCI grammar.
    """
    if not _IMAGE_NAME_RE.match(pair.image):
        msg = (
            f"::error::image name {pair.image!r} must match OCI name grammar "
            "(lowercase alphanumerics with '.', '_', '-')"
        )
        print(msg, file=sys.stderr)
        raise SystemExit(USAGE_EXIT_CODE)
    if not _TAG_RE.match(pair.tag):
        msg = (
            f"::error::tag {pair.tag!r} must match OCI tag grammar "
            "(1-128 chars from [A-Za-z0-9_.-], not starting with '.' or '-')"
        )
        print(msg, file=sys.stderr)
        raise SystemExit(USAGE_EXIT_CODE)


def parse_image_tag_groups(args: list[str]) -> list[ImageTag]:
    """Parse repeated ``--image-tags image:tag,image:tag,...`` arguments.

    Raises:
        SystemExit: with code 2 on malformed input.
    """
    out: list[ImageTag] = []
    for group in args:
        for raw in group.split(","):
            entry = raw.strip()
            if not entry:
                continue
            if ":" not in entry:
                print(
                    f"::error::--image-tags entry {entry!r} must be in 'image:tag' form",
                    file=sys.stderr,
                )
                raise SystemExit(USAGE_EXIT_CODE)
            image, tag = entry.split(":", 1)
            image = image.strip()
            tag = tag.strip()
            if not image or not tag:
                print(
                    f"::error::--image-tags entry {entry!r} has empty image or tag",
                    file=sys.stderr,
                )
                raise SystemExit(USAGE_EXIT_CODE)
            out.append(ImageTag(image=image, tag=tag))
    return out


def mint_pull_token(repo_path: str, ghcr_token: str | None) -> str | None:
    """Exchange a GitHub token for a GHCR pull-scoped token.

    Returns None when no input token is available. Only GHCR is
    supported; other registries would need a different auth flow.

    The ``repo_path`` is double-validated by the time it reaches this
    function: ``_validate_repo_prefix`` + ``_validate_image_tag`` reject
    anything outside the OCI grammar at startup, and the
    ``urllib.parse.quote`` call below percent-encodes anything the
    regex pass would have allowed but URLs disallow. CodeQL recognises
    ``quote()`` as a sanitizer for ``py/partial-ssrf`` (regex-based
    validators are not currently part of its sanitizer model), so the
    quote call closes the static-analysis warning even though the
    regex already prevents path-component escapes.

    Raises:
        urllib.error.URLError: network failure (also covers
            ``HTTPError``, e.g. an authn rejection from the token
            endpoint).
        json.JSONDecodeError: registry returned a non-JSON body.
        UnicodeDecodeError: registry returned a non-UTF-8 body.
        OSError: low-level socket error (timeout, connection reset).
    """
    if not ghcr_token:
        return None
    safe_repo = urllib.parse.quote(repo_path, safe="")
    url = (
        f"https://{GHCR_REGISTRY}/token?service={GHCR_REGISTRY}"
        f"&scope=repository:{safe_repo}:pull"
    )
    headers = {"Authorization": f"Basic {_basic_auth('x-access-token', ghcr_token)}"}
    # Retry a transient blip on the single token mint that gates the whole
    # gate: a 5xx / reset here would otherwise fail verification for a
    # correctly-published image. A persistent non-200 (auth rejection, or a
    # 5xx that outlasts the budget) is raised as URLError so the caller
    # reports a network/auth error rather than an empty token.
    status, _, body = _request_with_retry("GET", url, headers)
    if status != HTTP_OK:
        msg = f"token endpoint returned HTTP {status} for {url}"
        raise urllib.error.URLError(msg)
    payload = json.loads(body.decode())
    token = payload.get("token")
    return token if isinstance(token, str) else None


def _basic_auth(user: str, password: str) -> str:
    """Encode ``user:password`` for an HTTP Basic ``Authorization`` header."""
    return base64.b64encode(f"{user}:{password}".encode()).decode()


def _request(
    method: str,
    url: str,
    headers: dict[str, str],
) -> tuple[int, dict[str, str], bytes]:
    """Send an HTTP request and return ``(status, headers, body)``.

    HTTPError is converted to a tuple. Other ``URLError`` / ``OSError``
    subclasses (timeouts, DNS, refused connections) propagate.
    """
    req = urllib.request.Request(url, method=method, headers=headers)  # noqa: S310 -- registry URL constructed from validated inputs
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as resp:  # noqa: S310
            body = resp.read()
            return resp.status, dict(resp.headers), body
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), exc.read()


def _request_with_retry(
    method: str,
    url: str,
    headers: dict[str, str],
) -> tuple[int, dict[str, str], bytes]:
    """``_request`` with bounded retry on transient network errors and 5xx.

    See docs/reference/retry-patterns.md: Pattern C/Sync -- a standalone CI
    gate that cannot import ``synthorg.core.resilience.GeneralRetryHandler``.

    A connection-level failure (timeout, reset, refused) or a server-side
    5xx is transient: GHCR was momentarily unreachable, not a real answer
    about the image. Both are retried a few times with a fixed backoff.
    A 4xx (auth, genuine 404) is a definitive answer and is returned on
    the first attempt -- never retried. Mirrors the transient/terminal
    split docker_push_with_retry.sh applies on the publish path.

    On exhaustion the two transient classes diverge:
      - a persistent network error re-raises (no status to return);
      - a persistent 5xx returns its ``(status, headers, body)`` tuple,
        so the caller decides whether a lingering 5xx is fatal. Callers
        that must not treat a 5xx as a real answer (``resolve_digest``,
        ``mint_pull_token``) check ``status`` and raise themselves.

    Raises:
        urllib.error.URLError / OSError: a network-level failure that
            persisted across every attempt (matches ``_request``).
    """
    for attempt in range(1, REGISTRY_RETRY_ATTEMPTS + 1):
        try:
            status, response_headers, body = _request(method, url, headers)
        except urllib.error.URLError, OSError:
            if attempt < REGISTRY_RETRY_ATTEMPTS:
                time.sleep(REGISTRY_RETRY_BACKOFF_SECONDS)
                continue
            raise
        if status >= HTTP_SERVER_ERROR_MIN and attempt < REGISTRY_RETRY_ATTEMPTS:
            time.sleep(REGISTRY_RETRY_BACKOFF_SECONDS)
            continue
        return status, response_headers, body
    # Unreachable: the loop either returns or raises on the final attempt.
    msg = f"exhausted retry budget for {method} {url}"
    raise urllib.error.URLError(msg)


def resolve_digest(
    repo_path: str,
    tag: str,
    auth_header: dict[str, str],
) -> tuple[str | None, str | None]:
    """Resolve a tag to its manifest digest.

    Returns ``(digest, None)`` on success, or ``(None, error_message)``
    on failure. Distinguishes 404 (tag missing) from 200-without-digest
    (registry corruption / unexpected response).

    Both ``repo_path`` and ``tag`` are pre-validated against anchored
    OCI grammar regexes; the additional ``urllib.parse.quote`` calls
    below double down on path-component encoding so CodeQL's
    ``py/partial-ssrf`` data-flow recognises the URL as sanitised.

    Raises:
        urllib.error.URLError: a 5xx that persisted through the retry
            budget. A lingering server error is a registry outage, not
            evidence the tag is absent, so it surfaces as a network error
            (``_verify_pair`` reports it as such) rather than a misleading
            "tag does not resolve" verdict. Mirrors ``signature_present``.
    """
    safe_repo = urllib.parse.quote(repo_path, safe="/")
    safe_tag = urllib.parse.quote(tag, safe="")
    url = f"https://{GHCR_REGISTRY}/v2/{safe_repo}/manifests/{safe_tag}"
    headers = {"Accept": MANIFEST_ACCEPT, **auth_header}
    status, response_headers, _ = _request_with_retry("HEAD", url, headers)
    if status >= HTTP_SERVER_ERROR_MIN:
        msg = f"registry returned HTTP {status} for {url} after retries"
        raise urllib.error.URLError(msg)
    if status != HTTP_OK:
        return None, f"tag does not resolve in registry (HTTP {status})"
    for k, v in response_headers.items():
        if k.lower() == "docker-content-digest":
            return v.strip(), None
    return None, "registry returned 200 without docker-content-digest header"


def signature_present(
    repo_path: str,
    digest: str,
    auth_header: dict[str, str],
) -> bool:
    """Return True if a cosign signature referrer artifact exists for this digest.

    Retries a non-200 (and a transient network error) a few times with a
    short fixed backoff (``SIG_PROPAGATION_ATTEMPTS`` /
    ``SIG_PROPAGATION_BACKOFF_SECONDS``) to absorb GHCR's
    eventual-consistency window on a freshly-published referrer; a
    genuinely-unsigned digest still returns False once the short budget is
    spent. Only a 404 counts as "absent" -- a network error OR a non-404
    registry error (502/503/429/...) that persists past the budget is
    re-raised (the caller turns it into a "network error" message) rather
    than being reported as a false "unsigned" verdict.

    ``repo_path`` is pre-validated; ``digest`` is checked to start with
    the literal ``sha256:`` prefix and the hex tail is character-class
    constrained by definition. The ``urllib.parse.quote`` call closes
    the CodeQL ``py/partial-ssrf`` data-flow.

    Raises:
        urllib.error.URLError: a network-level failure (a raw ``OSError``
            is normalised to ``URLError`` with the original kept as
            ``__cause__``), OR a non-404 registry error (502/503/429/...),
            that persisted across every attempt.
    """
    if not digest.startswith("sha256:"):
        return False
    sig_tag = "sha256-" + digest.removeprefix("sha256:")
    safe_repo = urllib.parse.quote(repo_path, safe="/")
    safe_sig_tag = urllib.parse.quote(sig_tag, safe="")
    url = f"https://{GHCR_REGISTRY}/v2/{safe_repo}/manifests/{safe_sig_tag}"
    headers = {"Accept": SIG_ACCEPT, **auth_header}
    for attempt in range(1, SIG_PROPAGATION_ATTEMPTS + 1):
        try:
            status, _, _ = _request("HEAD", url, headers)
        except (urllib.error.URLError, OSError) as exc:
            # A network blip on the referrer check is itself transient:
            # retry within the same budget. If it persists, re-raise on the
            # final attempt so the caller reports a network error rather
            # than a false "unsigned" verdict. URLError is itself an OSError
            # subclass; a raw (non-URLError) OSError is normalised to URLError
            # so the raised type matches the documented contract and callers
            # only need to catch urllib.error.URLError.
            if attempt < SIG_PROPAGATION_ATTEMPTS:
                time.sleep(SIG_PROPAGATION_BACKOFF_SECONDS)
                continue
            if isinstance(exc, urllib.error.URLError):
                raise
            msg = f"network error on referrer check for {url}"
            raise urllib.error.URLError(msg) from exc
        if status == HTTP_OK:
            return True
        # A 404 is a real "referrer not (yet) present" answer: retry within
        # the propagation budget, then return False (genuinely unsigned). Any
        # OTHER non-200 (502/503/429/...) is a registry error, NOT evidence of
        # a missing signature; if it persists to the final attempt, raise so
        # the caller reports a network/registry error rather than a false
        # "unsigned" verdict.
        if status != HTTP_NOT_FOUND and attempt == SIG_PROPAGATION_ATTEMPTS:
            msg = f"registry error: HTTP {status} on referrer check for {url}"
            raise urllib.error.URLError(msg)
        if attempt < SIG_PROPAGATION_ATTEMPTS:
            time.sleep(SIG_PROPAGATION_BACKOFF_SECONDS)
    return False


def _resolve_token() -> tuple[str | None, str]:
    """Pick the registry token from the environment.

    Returns ``(token, source_label)``; ``source_label`` is one of
    ``"GHCR_TOKEN"`` / ``"GITHUB_TOKEN"`` / ``"none"`` so callers can
    log which env var supplied the token.
    """
    for env_var in ("GHCR_TOKEN", "GITHUB_TOKEN"):
        token = os.environ.get(env_var)
        if token:
            return token, env_var
    return None, "none"


def _check_per_image_convergence(
    pair_to_digest: dict[ImageTag, str],
) -> list[str]:
    """Return failure messages for any image whose IMMUTABLE tags diverge.

    Only immutable build-identity tags (``sha-<short>`` + full semver)
    participate: they pin to one build and must agree on its digest.
    Floating tags (``dev``, ``latest``, ``X.Y``) are excluded -- a
    concurrent later release legitimately repoints them to a newer build,
    so requiring them to converge produced a false-positive failure
    whenever two dev builds landed close together. Floating tags stay
    individually signature-checked upstream in ``_verify_pair``.

    Order-independent: collects every immutable ``(tag, digest)`` per image,
    then reports the full set of divergent digests if cardinality > 1.
    """
    by_image: dict[str, list[tuple[str, str]]] = {}
    for pair, digest in pair_to_digest.items():
        if not _is_immutable_tag(pair.tag):
            continue
        by_image.setdefault(pair.image, []).append((pair.tag, digest))

    failures: list[str] = []
    for image, observed in by_image.items():
        unique_digests = {digest for _, digest in observed}
        if len(unique_digests) > 1:
            by_digest: dict[str, list[str]] = {}
            for tag, digest in observed:
                by_digest.setdefault(digest, []).append(tag)
            variant_lines = sorted(
                f"    {digest} -> tags [{', '.join(sorted(tags))}]"
                for digest, tags in by_digest.items()
            )
            failures.append(
                f"{image}: divergent digests across immutable tags "
                f"(concurrent run overwrote a tag after we signed):\n"
                + "\n".join(variant_lines)
            )
    return failures


_NetworkExceptions: Final = (
    urllib.error.URLError,
    json.JSONDecodeError,
    UnicodeDecodeError,
    OSError,
)


def _parse_args() -> argparse.Namespace:
    """Build the argparse Namespace for this script."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n", 1)[0])
    parser.add_argument(
        "--repo-prefix",
        default=REPO_PREFIX_DEFAULT,
        help=f"Repository name prefix (default: {REPO_PREFIX_DEFAULT})",
    )
    parser.add_argument(
        "--image-tags",
        action="append",
        default=[],
        required=True,
        help=(
            "Comma-separated 'image:tag' pairs; may be repeated. "
            "Example: --image-tags backend:dev,backend:sha-abc1234"
        ),
    )
    return parser.parse_args()


def provenance_present(
    repo_path: str,
    digest: str,
    auth_header: dict[str, str],
) -> bool:
    """Return True if a SLSA provenance attestation refers to this digest.

    A signature proves who pushed the bytes; provenance proves how they were
    built. The project claims SLSA Build L3 on the strength of
    ``actions/attest``, so verifying the signature alone would leave
    that claim asserted rather than enforced -- an image whose attestation
    step silently failed would still pass.

    GHCR does not serve these through the OCI Referrers API; both cosign and
    ``actions/attest`` publish into the same ``sha256-<hex>`` tag
    that ``signature_present`` HEADs. That tag is an index whose children are
    the sigstore bundles, so provenance is one GET further: fetch the index
    and look for a child annotated with the SLSA predicate type.

    Carries the same propagation budget as ``signature_present``, and needs it
    more: signing and attestation are SEPARATE writes into that one tag, so
    the index can already exist, and answer 200, while the attestation child
    has not landed. A single GET would read that partial index as "never
    attested" and fail a release that was in fact attested correctly.

    Raises:
        urllib.error.URLError: a network-level failure, or a non-404 registry
            error, that persisted across every attempt.
    """
    if not digest.startswith("sha256:"):
        return False
    sig_tag = "sha256-" + digest.removeprefix("sha256:")
    safe_repo = urllib.parse.quote(repo_path, safe="/")
    safe_sig_tag = urllib.parse.quote(sig_tag, safe="")
    url = f"https://{GHCR_REGISTRY}/v2/{safe_repo}/manifests/{safe_sig_tag}"
    headers = {"Accept": SIG_ACCEPT, **auth_header}
    for attempt in range(1, SIG_PROPAGATION_ATTEMPTS + 1):
        status, _, body = _request_with_retry("GET", url, headers)
        # `_request_with_retry` hands back a lingering 5xx rather than raising,
        # so the caller decides. Reporting one as "no provenance" would fail a
        # release that is correctly attested, so only a 404 means absent.
        if status >= HTTP_SERVER_ERROR_MIN or status not in (
            HTTP_OK,
            HTTP_NOT_FOUND,
        ):
            msg = f"registry error HTTP {status} on provenance check for {url}"
            raise urllib.error.URLError(msg)
        if status == HTTP_OK and _has_provenance_child(body):
            return True
        if attempt < SIG_PROPAGATION_ATTEMPTS:
            time.sleep(SIG_PROPAGATION_BACKOFF_SECONDS)
    return False


def _has_provenance_child(body: bytes) -> bool:
    """Return True if the referrer index carries a SLSA provenance child."""
    try:
        index = json.loads(body)
    except json.JSONDecodeError:
        return False
    if not isinstance(index, dict):
        return False
    manifests = index.get("manifests")
    if not isinstance(manifests, list):
        return False
    for child in manifests:
        if not isinstance(child, dict):
            continue
        annotations = child.get("annotations")
        if not isinstance(annotations, dict):
            continue
        if annotations.get(_PREDICATE_ANNOTATION) == PROVENANCE_PREDICATE:
            return True
    return False


def _verify_pair(
    pair: ImageTag,
    repo_prefix: str,
    auth_header: dict[str, str],
) -> tuple[str | None, str | None]:
    """Verify a single ``(image, tag)`` pair against the registry.

    Caller supplies the pre-built ``auth_header`` (one per repo_path,
    cached by ``main``) so we don't mint a fresh GHCR pull token for
    every tag of the same image -- three tags of one image now share
    one token mint instead of three.

    Returns ``(digest, None)`` on a fully-signed result, ``(None, error)``
    on any failure. Network failures across the two constituent calls
    (``resolve_digest``, ``signature_present``) are captured and
    returned as the error string instead of propagating, so a transient
    failure on one pair doesn't halt verification of the rest of the
    inventory.
    """
    repo_path = f"{repo_prefix}{pair.image}"
    try:
        digest, err = resolve_digest(repo_path, pair.tag, auth_header)
        if digest is None:
            return None, err
        if not signature_present(repo_path, digest, auth_header):
            sig_hex = digest.removeprefix("sha256:")
            return None, (
                f"no cosign signature artifact for {digest} "
                f"(referrer tag sha256-{sig_hex} returned non-200)"
            )
        if not provenance_present(repo_path, digest, auth_header):
            return None, (
                f"no SLSA provenance attestation for {digest} "
                f"(no referrer with predicateType {PROVENANCE_PREDICATE})"
            )
    except _NetworkExceptions as exc:
        return None, f"network error ({type(exc).__name__}: {exc!r})"
    return digest, None


def _auth_header_for_repo(
    repo_path: str,
    ghcr_token: str | None,
    cache: dict[str, dict[str, str]],
) -> tuple[dict[str, str] | None, str | None]:
    """Return the cached Bearer header for ``repo_path``, minting on miss.

    Returns ``(header, None)`` on success, ``(None, error_message)`` if
    the token mint failed (network error). The cache is mutated in
    place so subsequent calls for the same ``repo_path`` reuse the
    minted token.
    """
    if repo_path in cache:
        return cache[repo_path], None
    try:
        reg_token = mint_pull_token(repo_path, ghcr_token)
    except _NetworkExceptions as exc:
        return (
            None,
            f"failed to mint pull token ({type(exc).__name__}: {exc!r})",
        )
    header: dict[str, str] = (
        {"Authorization": f"Bearer {reg_token}"} if reg_token else {}
    )
    cache[repo_path] = header
    return header, None


def _print_failures(failures: list[str]) -> None:
    """Render the failure block to stderr as a Checks-tab annotation."""
    print(file=sys.stderr)
    print("::error::Signature verification FAILED:", file=sys.stderr)
    for line in failures:
        print(f"  - {line}", file=sys.stderr)


def main() -> int:
    """Parse arguments and verify every ``(image, tag)`` pair."""
    args = _parse_args()
    _validate_repo_prefix(args.repo_prefix)
    pairs = parse_image_tag_groups(args.image_tags)
    if not pairs:
        print("::error::no --image-tags pairs provided", file=sys.stderr)
        return USAGE_EXIT_CODE
    for pair in pairs:
        _validate_image_tag(pair)

    ghcr_token, token_source = _resolve_token()
    print(f"auth: token source = {token_source}")

    failures: list[str] = []
    pair_to_digest: dict[ImageTag, str] = {}
    auth_cache: dict[str, dict[str, str]] = {}

    for pair in pairs:
        repo_path = f"{args.repo_prefix}{pair.image}"
        auth_header, auth_err = _auth_header_for_repo(repo_path, ghcr_token, auth_cache)
        if auth_err is not None:
            failures.append(f"{pair}: {auth_err}")
            continue
        assert auth_header is not None  # noqa: S101 -- err is None, so header is set
        digest, err = _verify_pair(pair, args.repo_prefix, auth_header)
        if err is not None:
            failures.append(f"{pair}: {err}")
            if digest is not None:
                pair_to_digest[pair] = digest
            continue
        assert digest is not None  # noqa: S101 -- err is None, so digest is set
        pair_to_digest[pair] = digest
        print(
            f"OK  {GHCR_REGISTRY}/{repo_path}:{pair.tag} -> {digest} "
            "(signed + SLSA provenance)"
        )

    failures.extend(_check_per_image_convergence(pair_to_digest))

    if failures:
        _print_failures(failures)
        return FAILURE_EXIT_CODE

    print()
    print(f"All {len(pairs)} (image, tag) pairs verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
