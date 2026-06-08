"""Pagination and window validation for the activity feed service.

Extracted from ``activity_service`` so the service module stays within
its size budget. Service-layer error paths log at WARNING with context
before raising so bad MCP requests are visible in the audit trail (per
CLAUDE.md ``## Logging``).
"""

from synthorg.observability import get_logger
from synthorg.observability.events.hr import HR_ACTIVITY_INVALID_REQUEST

logger = get_logger(__name__)

_MAX_WINDOW_HOURS: int = 720  # 30 days -- upper cap for pathological queries.


def validate_pagination(*, offset: int, limit: int) -> None:
    """Validate offset and limit, logging before each raise.

    Raises:
        ValueError: If an argument fails domain validation.
    """
    if offset < 0:
        logger.warning(
            HR_ACTIVITY_INVALID_REQUEST,
            param="offset",
            value=offset,
        )
        msg = f"offset must be >= 0, got {offset}"
        raise ValueError(msg)
    if limit < 1:
        logger.warning(
            HR_ACTIVITY_INVALID_REQUEST,
            param="limit",
            value=limit,
        )
        msg = f"limit must be >= 1, got {limit}"
        raise ValueError(msg)


def validate_window(*, window_hours: int) -> None:
    """Validate window_hours; logged before raise.

    Raises:
        ValueError: If an argument fails domain validation.
    """
    if window_hours < 1 or window_hours > _MAX_WINDOW_HOURS:
        logger.warning(
            HR_ACTIVITY_INVALID_REQUEST,
            param="window_hours",
            value=window_hours,
            max_allowed=_MAX_WINDOW_HOURS,
        )
        msg = (
            f"window_hours must be between 1 and {_MAX_WINDOW_HOURS}, "
            f"got {window_hours}"
        )
        raise ValueError(msg)


def validate_request(
    *,
    agent_id: str,
    offset: int,
    limit: int,
    window_hours: int,
) -> None:
    """Validate pagination + window inputs, logging before each raise.

    Service-layer error paths must log at WARNING with context before
    raising so bad MCP requests are visible in the audit trail (per
    CLAUDE.md ``## Logging``).

    Raises:
        ValueError: If an argument fails domain validation.
    """
    if offset < 0:
        logger.warning(
            HR_ACTIVITY_INVALID_REQUEST,
            agent_id=agent_id,
            param="offset",
            value=offset,
        )
        msg = f"offset must be >= 0, got {offset}"
        raise ValueError(msg)
    if limit < 1:
        logger.warning(
            HR_ACTIVITY_INVALID_REQUEST,
            agent_id=agent_id,
            param="limit",
            value=limit,
        )
        msg = f"limit must be >= 1, got {limit}"
        raise ValueError(msg)
    if window_hours < 1 or window_hours > _MAX_WINDOW_HOURS:
        logger.warning(
            HR_ACTIVITY_INVALID_REQUEST,
            agent_id=agent_id,
            param="window_hours",
            value=window_hours,
            max_allowed=_MAX_WINDOW_HOURS,
        )
        msg = (
            f"window_hours must be between 1 and {_MAX_WINDOW_HOURS}, "
            f"got {window_hours}"
        )
        raise ValueError(msg)
