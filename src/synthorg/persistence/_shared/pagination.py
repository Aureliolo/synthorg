"""Shared pagination-argument validation for SQLite + Postgres repos.

Both backends accept ``limit: int`` / ``offset: int`` on their cursor
paginated read methods; the validation is the same in both places
(reject non-int, reject ``bool``, reject ``limit < 1`` / ``offset < 0``)
and was previously duplicated verbatim across two files. Extracting
the helper keeps the validation rule in one place; backend-specific
bits stay in the call sites (the ``event`` constant + extra context
kwargs to log).
"""

from synthorg.core.persistence_errors import QueryError
from synthorg.observability import get_logger

logger = get_logger(__name__)


def validate_pagination_args(
    limit: object,
    offset: object,
    *,
    event: str,
    **context: object,
) -> None:
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
