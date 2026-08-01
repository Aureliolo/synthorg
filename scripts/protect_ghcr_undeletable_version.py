#!/usr/bin/env python3
r"""Tag a GHCR version that the API permanently refuses to delete.

GHCR rejects a delete once a publicly visible version passes 5000
downloads, and ``ghcr-cleanup`` treats any 400 as fatal, so one such
version reds the weekly prune forever. The download count is absent from
the REST API, so the offender can only be named after a refusal; the
prune's tracking issue quotes the digest to paste here.

Digest-targeted, not a blanket sweep: most untagged attestation indices
SHOULD be pruned, and protecting the class would trade a red leg for
unbounded growth of the objects the prune exists to remove.

The manifest bytes are PUT back verbatim, so the digest is preserved.
Re-serialising (what ``buildx imagetools create`` does) would mint a
second copy instead of naming the existing one.

Usage::

    GITHUB_TOKEN=... uv run python scripts/protect_ghcr_undeletable_version.py \
        --owner Aureliolo --package synthorg-sidecar-base \
        --digest sha256:6c235fee3b21403a959f988185f02f9223ccdef3685657bba5fdecc38ced762f
"""

import argparse
import base64
import json
import os
import re
import sys
import urllib.error
import urllib.request
from typing import Final, NoReturn

_REGISTRY: Final[str] = "https://ghcr.io"
_TAG_PREFIX: Final[str] = "keep-undeletable-"
_DIGEST_RE: Final[re.Pattern[str]] = re.compile(r"^sha256:[0-9a-f]{64}$")
_DIGEST_TAG_CHARS: Final[int] = 12

_HTTP_OK: Final[int] = 200
_HTTP_CREATED: Final[int] = 201
_ERROR_BODY_CHARS: Final[int] = 200

_INDEX_TYPES: Final[frozenset[str]] = frozenset(
    {
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
    }
)

_ACCEPT: Final[str] = (
    "application/vnd.oci.image.index.v1+json, "
    "application/vnd.oci.image.manifest.v1+json, "
    "application/vnd.docker.distribution.manifest.list.v2+json, "
    "application/vnd.docker.distribution.manifest.v2+json"
)


class GhcrProtectionError(Exception):
    """Raised when the registry cannot be reached or the digest is unsafe."""


def _fail(message: str) -> NoReturn:
    """Raise the module error, keeping call sites inside ``try`` blocks flat."""
    raise GhcrProtectionError(message)


def _request(
    url: str,
    *,
    authorization: str,
    accept: str = "application/json",
    method: str = "GET",
    body: bytes | None = None,
    content_type: str | None = None,
) -> tuple[int, bytes, dict[str, str]]:
    """Issue one HTTP call, returning status, body and headers."""
    if not url.startswith(_REGISTRY):
        _fail(f"refusing to call a non-registry URL: {url}")
    req = urllib.request.Request(url, method=method, data=body)  # noqa: S310 -- URL is asserted to be under the https-only _REGISTRY constant above
    req.add_header("Authorization", authorization)
    req.add_header("Accept", accept)
    if content_type is not None:
        req.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 -- same assertion as above
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers)
    except urllib.error.URLError as exc:
        message = f"{method} {url} failed: {exc.reason}"
        raise GhcrProtectionError(message) from exc


def _registry_token(owner: str, package: str, *, github_token: str, push: bool) -> str:
    """Exchange the workflow token for a registry token.

    The distribution token endpoint authenticates with Basic, not Bearer:
    a Bearer github token is rejected 403 even when it carries the rights.
    """
    scope = f"repository:{owner.lower()}/{package}:pull"
    if push:
        scope += ",push"
    url = f"{_REGISTRY}/token?service=ghcr.io&scope={scope}"
    basic = base64.b64encode(f"{owner}:{github_token}".encode()).decode()
    status, payload, _ = _request(url, authorization=f"Basic {basic}")
    if status != _HTTP_OK:
        _fail(f"registry token exchange returned {status} for scope '{scope}'")
    return str(json.loads(payload)["token"])


def _assert_safe_to_protect(manifest: dict[str, object], media_type: str) -> None:
    """Refuse to protect a live platform image.

    A ``keep-*`` tag exempts its target from every prune pass for good, so
    naming a real multi-arch image would pin a release outside cleanup.
    """
    if media_type not in _INDEX_TYPES:
        return
    children = manifest.get("manifests")
    if not isinstance(children, list):
        return
    for child in children:
        if isinstance(child, dict) and child.get("platform") is not None:
            _fail(
                "refusing to protect a platform-bearing image index: this "
                "digest is a real multi-arch image, not an undeletable orphan"
            )


def _fetch_manifest(repo: str, digest: str, *, token: str) -> tuple[bytes, str]:
    """Return the raw manifest bytes and its media type."""
    status, payload, headers = _request(
        f"{_REGISTRY}/v2/{repo}/manifests/{digest}",
        authorization=f"Bearer {token}",
        accept=_ACCEPT,
    )
    if status != _HTTP_OK:
        _fail(
            f"manifest fetch for {digest} returned {status}; "
            "the version may already be gone"
        )
    return payload, headers.get("Content-Type", "").split(";")[0].strip()


def _apply_tag(
    repo: str, tag: str, *, payload: bytes, media_type: str, token: str
) -> None:
    """PUT the manifest bytes verbatim under ``tag``."""
    status, body, _ = _request(
        f"{_REGISTRY}/v2/{repo}/manifests/{tag}",
        authorization=f"Bearer {token}",
        method="PUT",
        body=payload,
        content_type=media_type,
    )
    if status not in (_HTTP_OK, _HTTP_CREATED):
        detail = body.decode("utf-8", "replace")[:_ERROR_BODY_CHARS]
        _fail(f"tagging as {tag} returned {status}: {detail}")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    """Build and run the argument parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--package", required=True)
    parser.add_argument(
        "--digest",
        required=True,
        help="Full sha256:... digest GHCR refused to delete.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve and safety-check the digest without writing the tag.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Tag one undeletable GHCR version so the prune stops targeting it."""
    args = _parse_args(argv)

    if not _DIGEST_RE.match(args.digest):
        print(
            f"::error::--digest must be sha256:<64 hex>, got '{args.digest}'",
            file=sys.stderr,
        )
        return 2

    github_token = os.environ.get("GITHUB_TOKEN", "")
    if not github_token:
        print("::error::GITHUB_TOKEN is required", file=sys.stderr)
        return 2

    repo = f"{args.owner.lower()}/{args.package}"
    tag = f"{_TAG_PREFIX}{args.digest.removeprefix('sha256:')[:_DIGEST_TAG_CHARS]}"

    try:
        token = _registry_token(
            args.owner,
            args.package,
            github_token=github_token,
            push=not args.dry_run,
        )
        payload, media_type = _fetch_manifest(repo, args.digest, token=token)
        _assert_safe_to_protect(json.loads(payload), media_type)

        if args.dry_run:
            print(f"would tag {args.digest} as {repo}:{tag} ({media_type})")
            return 0

        _apply_tag(repo, tag, payload=payload, media_type=media_type, token=token)
    except GhcrProtectionError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1

    print(f"tagged {args.digest} as {repo}:{tag}; the prune will now skip it")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
