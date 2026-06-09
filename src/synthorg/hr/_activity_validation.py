# module-kind: code
"""Pagination and window validation for the activity feed service.

Service-layer error paths log at WARNING with context before raising so
bad MCP requests are visible in the audit trail.
"""

from synthorg.observability import get_logger
from synthorg.observability.events.hr import HR_ACTIVITY_INVALID_REQUEST

logger = get_logger(__name__)

_MAX_WINDOW_HOURS: int = 720  # 30 days -- upper cap for pathological queries.


def validate_pagination(
    *,
    offset: int,
    limit: int,
    agent_id: str | None = None,
) -> None:
    """Validate offset and limit, logging before each raise.

    ``agent_id`` is included in the warning only when supplied (the
    agent-scoped feed path); the no-agent feed omits it so log shape is
    unchanged for callers that never had an agent in context.

    Raises:
        ValueError: If an argument fails domain validation.
    """
    context = {"agent_id": agent_id} if agent_id is not None else {}
    if offset < 0:
        logger.warning(
            HR_ACTIVITY_INVALID_REQUEST,
            param="offset",
            value=offset,
            **context,
        )
        msg = f"offset must be >= 0, got {offset}"
        raise ValueError(msg)
    if limit < 1:
        logger.warning(
            HR_ACTIVITY_INVALID_REQUEST,
            param="limit",
            value=limit,
            **context,
        )
        msg = f"limit must be >= 1, got {limit}"
        raise ValueError(msg)


def validate_window(*, window_hours: int, agent_id: str | None = None) -> None:
    """Validate window_hours; logged before raise.

    ``agent_id`` is included in the warning only when supplied (see
    :func:`validate_pagination`).

    Raises:
        ValueError: If an argument fails domain validation.
    """
    context = {"agent_id": agent_id} if agent_id is not None else {}
    if window_hours < 1 or window_hours > _MAX_WINDOW_HOURS:
        logger.warning(
            HR_ACTIVITY_INVALID_REQUEST,
            param="window_hours",
            value=window_hours,
            max_allowed=_MAX_WINDOW_HOURS,
            **context,
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
    """Validate pagination + window inputs for the agent-scoped feed.

    Delegates to :func:`validate_pagination` and :func:`validate_window`
    with the ``agent_id`` threaded through so each WARNING carries the
    agent context.

    Raises:
        ValueError: If an argument fails domain validation.
    """
    validate_pagination(offset=offset, limit=limit, agent_id=agent_id)
    validate_window(window_hours=window_hours, agent_id=agent_id)
