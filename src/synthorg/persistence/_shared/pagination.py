"""Shared pagination-argument validation for SQLite + Postgres repos.

Both backends accept ``limit: int`` / ``offset: int`` on their cursor
paginated read methods. The validation rule (reject non-int, reject
``bool``, reject ``limit < 1`` / ``offset < 0``) is identical across
backends and lives here as a single helper so a future tightening
applies everywhere atomically. Backend-specific concerns (the
``event`` constant emitted on rejection and extra context kwargs to
log) stay in the call sites.
"""

from typing import Final

from synthorg.core.persistence_errors import QueryError
from synthorg.observability import get_logger

logger = get_logger(__name__)

# Canonical default page size for ``list_*`` / ``query`` repository
# methods. Lives here so every repo and Protocol shares a single named
# constant rather than embedding an inline ``100`` literal that trips
# the magic-numbers gate. Matches the established convention across
# the codebase (30+ repositories already default ``limit`` to 100).
DEFAULT_LIST_LIMIT: Final[int] = 100

# Hard upper bound on ``list_*`` / ``query`` page sizes regardless of
# caller-supplied limit. Defense-in-depth: the API layer's
# ``CursorLimit`` already caps caller input at 200, but internal
# service calls that bypass the API would not see that bound. The
# 10_000 ceiling matches the established per-repo ``_MAX_LIST_ROWS``
# precedent in ``persistence/{sqlite,postgres}/project_repo.py``.
MAX_LIST_LIMIT: Final[int] = 10_000


def validate_pagination_args(
    limit: object,
    offset: object,
    *,
    event: str,
    **context: object,
) -> int:
    """Type-check + bounds-check pagination args; log + raise on failure.

    Args:
        limit: Caller-supplied page size. Must be ``int`` (and not
            ``bool``). The numeric bound is ``>= 1``.
        offset: Caller-supplied page offset. Must be ``int`` (and not
            ``bool``). The numeric bound is ``>= 0``.
        event: Domain event constant the call site uses to namespace
            its query-failure logs (e.g.
            ``PERSISTENCE_DECISION_RECORD_QUERY_FAILED``).
        **context: Extra structured fields the caller wants to attach
            to the log line (e.g. ``task_id`` or ``agent_id``).

    Returns:
        ``limit`` clamped to ``[1, MAX_LIST_LIMIT]`` so repository
        callers cannot trigger an unbounded scan even when an internal
        path bypasses the API layer's ``CursorLimit`` bound.

    Raises:
        QueryError: If either argument fails the type or bounds check.
            The structured warning is emitted before the raise so
            operators can correlate the rejected call.
    """
    for name, value in (("limit", limit), ("offset", offset)):
        # ``isinstance(value, bool)`` accepts True / False as int
        # subclasses; we explicitly reject them so callers don't
        # accidentally page with a flag.
        if isinstance(value, bool) or not isinstance(value, int):
            msg = f"{name} must be int, got {type(value).__name__}"
            logger.warning(
                event,
                error=msg,
                param=name,
                provided_type=type(value).__name__,
                **context,
            )
            raise QueryError(msg)
    assert isinstance(limit, int)  # noqa: S101  -- type narrows for mypy
    assert isinstance(offset, int)  # noqa: S101  -- type narrows for mypy
    if limit < 1:
        msg = f"limit must be >= 1, got {limit}"
        logger.warning(
            event,
            error=msg,
            param="limit",
            value=limit,
            **context,
        )
        raise QueryError(msg)
    if offset < 0:
        msg = f"offset must be >= 0, got {offset}"
        logger.warning(
            event,
            error=msg,
            param="offset",
            value=offset,
            **context,
        )
        raise QueryError(msg)
    return min(limit, MAX_LIST_LIMIT)
