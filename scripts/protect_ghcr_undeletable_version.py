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
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Final, NoReturn

_REGISTRY: Final[str] = "https://ghcr.io"
_REGISTRY_HOST: Final[str] = "ghcr.io"
_TAG_PREFIX: Final[str] = "keep-undeletable-"
_DIGEST_RE: Final[re.Pattern[str]] = re.compile(r"sha256:[0-9a-f]{64}")
# OCI path-component grammar and the GitHub login grammar respectively. Both
# reach the registry URL and the token scope, where a `/`, `?`, `#` or `..`
# would retarget the request or widen the scope; `_assert_registry_url` only
# constrains the host, so it cannot catch either.
_PACKAGE_RE: Final[re.Pattern[str]] = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*")
_OWNER_RE: Final[re.Pattern[str]] = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
)
_DIGEST_TAG_CHARS: Final[int] = 12

# Buildx writes attestation children with a deliberately non-runnable platform
# so no runtime tries to execute them. They are not evidence of a real image.
_UNKNOWN_PLATFORM: Final[str] = "unknown"

_HTTP_OK: Final[int] = 200
_HTTP_CREATED: Final[int] = 201
_HTTP_SERVER_ERROR_MIN: Final[int] = 500
_ERROR_BODY_CHARS: Final[int] = 200
_REQUEST_TIMEOUT_SECONDS: Final[int] = 30

# This is a repair tool run against a registry that is already misbehaving:
# the prune it unblocks fails on GHCR's spurious 401s under concurrent load,
# so the repair path must survive the same conditions it exists to clean up.
_RETRY_ATTEMPTS: Final[int] = 4
_RETRY_BACKOFF_SECONDS: Final[int] = 3
_RETRYABLE_STATUSES: Final[frozenset[int]] = frozenset({401, 408, 429})

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


def _assert_registry_url(url: str) -> None:
    """Refuse any URL that is not this registry over https.

    Compares the parsed host, not a string prefix: ``https://ghcr.io.evil``
    and ``https://ghcr.io@evil`` both pass a ``startswith`` check.
    """
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or parts.hostname != _REGISTRY_HOST:
        _fail(f"refusing to call a non-registry URL: {url}")


def _request_once(
    url: str,
    *,
    authorization: str,
    accept: str,
    method: str,
    body: bytes | None,
    content_type: str | None,
) -> tuple[int, bytes, dict[str, str]]:
    """Issue one HTTP call, returning status, body and headers."""
    _assert_registry_url(url)
    req = urllib.request.Request(url, method=method, data=body)  # noqa: S310 -- URL is asserted https + ghcr.io by _assert_registry_url above
    req.add_header("Authorization", authorization)
    req.add_header("Accept", accept)
    if content_type is not None:
        req.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT_SECONDS) as resp:  # noqa: S310 -- same assertion as above
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers)


def _request(
    url: str,
    *,
    authorization: str,
    accept: str = "application/json",
    method: str = "GET",
    body: bytes | None = None,
    content_type: str | None = None,
) -> tuple[int, bytes, dict[str, str]]:
    """Issue an HTTP call, retrying transient registry failures.

    Retries network errors, 5xx, and the 401/408/429 class GHCR is known to
    return spuriously under load. A definitive 4xx returns immediately: the
    caller decides, and retrying a real permission or shape error only delays
    the diagnosis.
    """
    last_error: urllib.error.URLError | None = None
    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        try:
            status, payload, headers = _request_once(
                url,
                authorization=authorization,
                accept=accept,
                method=method,
                body=body,
                content_type=content_type,
            )
        except urllib.error.URLError as exc:
            last_error = exc
        else:
            transient = (
                status >= _HTTP_SERVER_ERROR_MIN or status in _RETRYABLE_STATUSES
            )
            if not transient or attempt == _RETRY_ATTEMPTS:
                return status, payload, headers
            print(
                f"{method} {url} returned {status}; retrying "
                f"({attempt}/{_RETRY_ATTEMPTS})",
                file=sys.stderr,
            )
        if attempt == _RETRY_ATTEMPTS:
            break
        time.sleep(_RETRY_BACKOFF_SECONDS * attempt)

    message = f"{method} {url} failed after {_RETRY_ATTEMPTS} attempts"
    if last_error is not None:
        message = f"{message}: {last_error.reason}"
        raise GhcrProtectionError(message) from last_error
    raise GhcrProtectionError(message)


def _registry_token(owner: str, package: str, *, github_token: str, push: bool) -> str:
    """Exchange the workflow token for a registry token.

    The distribution token endpoint authenticates with Basic, not Bearer:
    a Bearer github token is rejected 403 even when it carries the rights.
    """
    scope = f"repository:{owner.lower()}/{package}:pull"
    if push:
        scope += ",push"
    query = urllib.parse.urlencode({"service": _REGISTRY_HOST, "scope": scope})
    url = f"{_REGISTRY}/token?{query}"
    basic = base64.b64encode(f"{owner}:{github_token}".encode()).decode()
    status, payload, _ = _request(url, authorization=f"Basic {basic}")
    if status != _HTTP_OK:
        _fail(f"registry token exchange returned {status} for scope '{scope}'")
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        msg = f"registry token response was not JSON: {exc.msg}"
        raise GhcrProtectionError(msg) from exc
    if not isinstance(parsed, dict) or "token" not in parsed:
        _fail("registry token response carried no 'token' field")
    return str(parsed["token"])


def _is_real_platform(child: object) -> bool:
    """Return True for a child describing a runnable platform.

    A Buildx attestation child carries ``{"architecture": "unknown", "os":
    "unknown"}``, which is present-but-meaningless. Treating any non-null
    ``platform`` as real refused every attestation index -- precisely the
    orphan class this tool exists to protect.
    """
    if not isinstance(child, dict):
        return False
    platform = child.get("platform")
    if not isinstance(platform, dict):
        return False
    architecture = platform.get("architecture")
    operating_system = platform.get("os")
    return not (
        architecture == _UNKNOWN_PLATFORM and operating_system == _UNKNOWN_PLATFORM
    )


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
        if _is_real_platform(child):
            _fail(
                "refusing to protect a platform-bearing image index: this "
                "digest is a real multi-arch image, not an undeletable orphan"
            )


def _resolve_media_type(payload: bytes, headers: dict[str, str]) -> str:
    """Resolve the manifest media type, body first, header as fallback.

    ``_request_once`` returns ``dict(resp.headers)``, which drops
    ``HTTPMessage``'s case-insensitive lookup, so a registry answering
    ``content-type`` yielded an empty string. That is not a harmless blank:
    an empty type is outside ``_INDEX_TYPES``, so ``_assert_safe_to_protect``
    returned before inspecting ``manifests`` and the multi-arch guard silently
    failed open. An OCI manifest carries its own ``mediaType``, so the body is
    the authority and the header only covers a manifest that omits it.
    """
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        declared = parsed.get("mediaType")
        if isinstance(declared, str) and declared.strip():
            return declared.split(";")[0].strip()
    header_value = next(
        (v for k, v in headers.items() if k.lower() == "content-type"), ""
    )
    return header_value.split(";")[0].strip()


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
    return payload, _resolve_media_type(payload, headers)


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

    # `fullmatch`, not `match`: `$` also matches before a trailing newline, so
    # a digest pasted out of a tracking issue could carry one into the URL.
    if not _DIGEST_RE.fullmatch(args.digest):
        print(
            f"::error::--digest must be sha256:<64 hex>, got '{args.digest}'",
            file=sys.stderr,
        )
        return 2

    if not _OWNER_RE.fullmatch(args.owner):
        print(
            f"::error::--owner must be a GitHub login, got '{args.owner}'",
            file=sys.stderr,
        )
        return 2

    if not _PACKAGE_RE.fullmatch(args.package):
        print(
            f"::error::--package must be a package name, got '{args.package}'",
            file=sys.stderr,
        )
        return 2

    github_token = os.environ.get("GITHUB_TOKEN", "")
    if not github_token:
        print("::error::GITHUB_TOKEN is required", file=sys.stderr)
        return 2

    # Both components are already constrained to characters with no meaning in
    # a URL; quoting states that for a reader and for CodeQL's data-flow.
    owner_segment = urllib.parse.quote(args.owner.lower(), safe="")
    package_segment = urllib.parse.quote(args.package, safe="")
    repo = f"{owner_segment}/{package_segment}"
    tag = f"{_TAG_PREFIX}{args.digest.removeprefix('sha256:')[:_DIGEST_TAG_CHARS]}"

    try:
        token = _registry_token(
            args.owner,
            args.package,
            github_token=github_token,
            push=not args.dry_run,
        )
        payload, media_type = _fetch_manifest(repo, args.digest, token=token)
        if not media_type:
            # An unresolved type would slip past the index check and then be
            # PUT back with an empty Content-Type. Both are silent; refuse.
            _fail(
                f"could not resolve a media type for {args.digest} from the "
                "manifest body or the response headers; refusing to re-PUT it "
                "unguarded"
            )
        try:
            manifest = json.loads(payload)
        except json.JSONDecodeError as exc:
            msg = f"manifest for {args.digest} was not JSON: {exc.msg}"
            raise GhcrProtectionError(msg) from exc
        if not isinstance(manifest, dict):
            _fail(f"manifest for {args.digest} was not a JSON object")
        _assert_safe_to_protect(manifest, media_type)

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
