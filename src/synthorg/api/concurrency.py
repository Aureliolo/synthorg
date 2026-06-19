"""Optimistic concurrency via ETag / If-Match.

Provides utilities for computing strong ETags from resource state
and validating ``If-Match`` request headers to detect concurrent
modification conflicts.  Strong ETags are required for
``If-Match`` per RFC 7232 / RFC 9110.
"""

import hashlib
from typing import Final

from synthorg.core.domain_errors import ValidationError, VersionConflictError
from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    API_CONCURRENCY_CONFLICT,
)

logger = get_logger(__name__)

# An ``If-Match`` header longer than this is rejected before parsing so a
# pathological value cannot drive unbounded split/serialisation work.
_MAX_IF_MATCH_LENGTH: Final[int] = 512


def compute_etag(value: str, updated_at: str) -> str:
    """Compute a strong ETag from value and timestamp.

    Uses SHA-256 truncated to 16 hex characters.  Strong ETags
    are required for ``If-Match`` precondition checks per
    RFC 7232 / RFC 9110.

    Args:
        value: Resource value (e.g. setting value, config JSON).
        updated_at: Last-modified timestamp string.

    Returns:
        Strong ETag string like ``"a1b2c3d4e5f67890"``.
    """
    digest = hashlib.sha256(
        f"{value}:{updated_at}".encode(),
    ).hexdigest()[:16]
    return f'"{digest}"'


def check_if_match(
    request_etag: str | None,
    current_etag: str,
    resource_name: str,
) -> None:
    """Raise ``VersionConflictError`` if If-Match doesn't match.

    When ``request_etag`` is ``None`` or empty, the check is
    skipped (backward compatible -- clients not sending
    ``If-Match`` bypass optimistic concurrency).

    Supports RFC 7232 syntax: ``*`` matches any version, and
    comma-separated entity-tag lists are parsed to check if
    ``current_etag`` is among them.

    Args:
        request_etag: Value from the ``If-Match`` request header.
        current_etag: Current ETag of the resource.
        resource_name: For error messages and logging.

    Raises:
        ValidationError: When the ``If-Match`` header exceeds the maximum
            permitted length (HTTP 400).
        VersionConflictError: On ETag mismatch (HTTP 409).
    """
    if not request_etag:
        return

    if len(request_etag) > _MAX_IF_MATCH_LENGTH:
        logger.warning(
            API_CONCURRENCY_CONFLICT,
            resource=resource_name,
            reason="if_match_too_long",
            request_etag_length=len(request_etag),
            max_if_match_length=_MAX_IF_MATCH_LENGTH,
        )
        msg = (
            f"If-Match header too long for {resource_name}: "
            f"{len(request_etag)} > {_MAX_IF_MATCH_LENGTH} characters"
        )
        raise ValidationError(msg)

    stripped = request_etag.strip()

    # RFC 7232: "*" matches any current entity.
    if stripped == "*":
        return

    # Parse comma-separated entity-tag list.
    tags = [t.strip() for t in stripped.split(",")]
    if current_etag in tags:
        return

    logger.warning(
        API_CONCURRENCY_CONFLICT,
        resource=resource_name,
        request_etag=request_etag,
        current_etag=current_etag,
    )
    msg = (
        f"Version conflict on {resource_name}: "
        f"expected {current_etag}, got {request_etag}"
    )
    raise VersionConflictError(msg)
