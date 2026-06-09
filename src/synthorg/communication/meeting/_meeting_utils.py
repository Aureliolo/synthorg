# module-kind: code
"""Shared helpers for the meeting subpackage."""

from collections import Counter

from synthorg.communication.meeting.errors import MeetingParticipantError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.meeting import MEETING_VALIDATION_FAILED

logger = get_logger(__name__)


def format_exception(exc: BaseException) -> str:
    """Format an exception for error messages.

    Flattens ``ExceptionGroup`` (produced by ``asyncio.TaskGroup``
    when multiple concurrent tasks fail) into a single human-readable
    string.  Handles nested groups recursively.  Non-group exceptions
    are returned via ``safe_error_description()`` so callers never
    embed unredacted ``str(exc)`` into log/UI fields.

    Args:
        exc: The exception to format, possibly an ``ExceptionGroup``.

    Returns:
        A flattened, scrubbed, human-readable description of the
        exception (recursing into ``ExceptionGroup``s).
    """
    if isinstance(exc, ExceptionGroup):
        parts: list[str] = []
        for sub in exc.exceptions:
            if isinstance(sub, ExceptionGroup):
                parts.append(format_exception(sub))
            else:
                parts.append(safe_error_description(sub))
        return f"Multiple errors: {'; '.join(parts)}"
    return safe_error_description(exc)


def validate_meeting_inputs(
    meeting_id: str,
    leader_id: str,
    participant_ids: tuple[str, ...],
    token_budget: int,
) -> None:
    """Validate meeting inputs.

    Raises:
        MeetingParticipantError: If participants are empty, contain
            duplicates, or the leader is in ``participant_ids``.
        ValueError: If ``token_budget`` is not positive.
    """
    if token_budget <= 0:
        logger.warning(
            MEETING_VALIDATION_FAILED,
            meeting_id=meeting_id,
            error=f"token_budget must be positive, got {token_budget}",
        )
        msg = f"token_budget must be positive, got {token_budget}"
        raise ValueError(msg)

    if not participant_ids:
        logger.warning(
            MEETING_VALIDATION_FAILED,
            meeting_id=meeting_id,
            error="at least one participant is required",
        )
        msg = "At least one participant is required"
        raise MeetingParticipantError(
            msg,
            context={"meeting_id": meeting_id},
        )
    if len(participant_ids) != len(set(participant_ids)):
        dupes = sorted(v for v, c in Counter(participant_ids).items() if c > 1)
        logger.warning(
            MEETING_VALIDATION_FAILED,
            meeting_id=meeting_id,
            error="duplicate participant_ids",
            duplicates=dupes,
        )
        msg = f"Duplicate participant IDs: {dupes}"
        raise MeetingParticipantError(
            msg,
            context={
                "meeting_id": meeting_id,
                "duplicates": dupes,
            },
        )
    if leader_id in participant_ids:
        logger.warning(
            MEETING_VALIDATION_FAILED,
            meeting_id=meeting_id,
            error="leader in participant_ids",
            leader_id=leader_id,
        )
        msg = (
            f"Leader {leader_id!r} must not be in participant_ids "
            f"(leader participates implicitly)"
        )
        raise MeetingParticipantError(
            msg,
            context={
                "meeting_id": meeting_id,
                "leader_id": leader_id,
            },
        )
