#!/usr/bin/env python3
r"""Verify cosign signatures for SynthOrg container images on GHCR.

Run as a workflow gate after all publish jobs finish: for every
``(image, tag)`` pair pushed by the workflow, resolve the tag to its
manifest digest, then confirm a cosign signature artifact (referrer
index entry, surfaced on GHCR as the ``sha256-<hex>`` referrer tag)
exists for that digest. Fails loudly if any tag is unsigned or any
two tags of the same image diverge to different digests.

Designed to catch the failure mode where two concurrent ``docker.yml``
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
import sys
import urllib.error
import urllib.request

GHCR_REGISTRY = "ghcr.io"
REPO_PREFIX_DEFAULT = "aureliolo/synthorg-"
MANIFEST_ACCEPT = (
    "application/vnd.docker.distribution.manifest.list.v2+json,"
    "application/vnd.oci.image.index.v1+json,"
    "application/vnd.docker.distribution.manifest.v2+json,"
    "application/vnd.oci.image.manifest.v1+json"
)
SIG_ACCEPT = "application/vnd.oci.image.index.v1+json"
HTTP_TIMEOUT_SECONDS = 30
HTTP_OK = 200
USAGE_EXIT_CODE = 2
FAILURE_EXIT_CODE = 1


@dataclasses.dataclass(frozen=True)
class ImageTag:
    """An ``(image, tag)`` pair to verify."""

    image: str
    tag: str

    def __str__(self) -> str:
        """Render as ``image:tag`` for log output."""
        return f"{self.image}:{self.tag}"


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
                    f"error: --image-tags entry {entry!r} must be in 'image:tag' form",
                    file=sys.stderr,
                )
                raise SystemExit(USAGE_EXIT_CODE)
            image, tag = entry.split(":", 1)
            image = image.strip()
            tag = tag.strip()
            if not image or not tag:
                print(
                    f"error: --image-tags entry {entry!r} has empty image or tag",
                    file=sys.stderr,
                )
                raise SystemExit(USAGE_EXIT_CODE)
            out.append(ImageTag(image=image, tag=tag))
    return out


def mint_pull_token(repo_path: str, ghcr_token: str | None) -> str | None:
    """Exchange a GitHub token for a GHCR pull-scoped token.

    Returns None when no input token is available. Only GHCR is
    supported; other registries would need a different auth flow.

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
    url = (
        f"https://{GHCR_REGISTRY}/token?service={GHCR_REGISTRY}"
        f"&scope=repository:{repo_path}:pull"
    )
    req = urllib.request.Request(  # noqa: S310 -- URL is constructed from constants + caller-provided repo path
        url,
        headers={"Authorization": f"Basic {_basic_auth('x-access-token', ghcr_token)}"},
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as resp:  # noqa: S310
        body = json.loads(resp.read().decode())
    token = body.get("token")
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


def resolve_digest(
    repo_path: str,
    tag: str,
    auth_header: dict[str, str],
) -> tuple[str | None, str | None]:
    """Resolve a tag to its manifest digest.

    Returns ``(digest, None)`` on success, or ``(None, error_message)``
    on failure. Distinguishes 404 (tag missing) from 200-without-digest
    (registry corruption / unexpected response).
    """
    url = f"https://{GHCR_REGISTRY}/v2/{repo_path}/manifests/{tag}"
    headers = {"Accept": MANIFEST_ACCEPT, **auth_header}
    status, response_headers, _ = _request("HEAD", url, headers)
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
    """Return True if a cosign signature referrer artifact exists for this digest."""
    if not digest.startswith("sha256:"):
        return False
    sig_tag = "sha256-" + digest[len("sha256:") :]
    url = f"https://{GHCR_REGISTRY}/v2/{repo_path}/manifests/{sig_tag}"
    headers = {"Accept": SIG_ACCEPT, **auth_header}
    status, _, _ = _request("HEAD", url, headers)
    return status == HTTP_OK


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
    """Return failure messages for any image whose tags diverge.

    Order-independent: collects every ``(tag, digest)`` per image, then
    reports the full set of divergent digests if cardinality > 1.
    """
    by_image: dict[str, list[tuple[str, str]]] = {}
    for pair, digest in pair_to_digest.items():
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
                f"{image}: divergent digests across tags (concurrent run "
                f"overwrote a tag after we signed):\n" + "\n".join(variant_lines)
            )
    return failures


def main() -> int:
    """Parse arguments and verify every ``(image, tag)`` pair."""
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
    args = parser.parse_args()

    pairs = parse_image_tag_groups(args.image_tags)
    if not pairs:
        print("error: no --image-tags pairs provided", file=sys.stderr)
        return USAGE_EXIT_CODE

    ghcr_token, token_source = _resolve_token()
    print(f"auth: token source = {token_source}")

    failures: list[str] = []
    pair_to_digest: dict[ImageTag, str] = {}

    for pair in pairs:
        repo_path = f"{args.repo_prefix}{pair.image}"
        try:
            reg_token = mint_pull_token(repo_path, ghcr_token)
        except (
            urllib.error.URLError,
            json.JSONDecodeError,
            UnicodeDecodeError,
            OSError,
        ) as exc:
            failures.append(
                f"{pair}: failed to mint pull token ({type(exc).__name__}: {exc})"
            )
            continue
        auth_header = {"Authorization": f"Bearer {reg_token}"} if reg_token else {}

        digest, err = resolve_digest(repo_path, pair.tag, auth_header)
        if digest is None:
            failures.append(f"{pair}: {err}")
            continue

        pair_to_digest[pair] = digest

        if not signature_present(repo_path, digest, auth_header):
            failures.append(
                f"{pair}: no cosign signature artifact for {digest} "
                f"(referrer tag sha256-{digest[len('sha256:') :]} returned non-200)"
            )
            continue

        print(f"OK  {GHCR_REGISTRY}/{repo_path}:{pair.tag} -> {digest} (signed)")

    failures.extend(_check_per_image_convergence(pair_to_digest))

    if failures:
        print(file=sys.stderr)
        print("Signature verification FAILED:", file=sys.stderr)
        for line in failures:
            print(f"  - {line}", file=sys.stderr)
        return FAILURE_EXIT_CODE

    print()
    print(f"All {len(pairs)} (image, tag) pairs verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
