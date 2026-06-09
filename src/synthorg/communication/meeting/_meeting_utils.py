# module-kind: code
"""Shared helpers for the meeting subpackage."""

from collections import Counter
from typing import NoReturn

from synthorg.communication.meeting.errors import MeetingParticipantError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.meeting import MEETING_VALIDATION_FAILED

logger = get_logger(__name__)


def _fail_participant(
    meeting_id: str,
    error: str,
    msg: str,
    **context: object,
) -> NoReturn:
    """Log a participant-validation failure and raise.

    Args:
        meeting_id: The meeting being validated.
        error: Short structured-log reason.
        msg: Human-readable exception message.
        **context: Extra fields surfaced in BOTH the log event and the
            raised error's ``context`` (e.g. ``duplicates`` / ``leader_id``).

    Raises:
        MeetingParticipantError: Always.
    """
    logger.warning(
        MEETING_VALIDATION_FAILED,
        meeting_id=meeting_id,
        error=error,
        **context,
    )
    raise MeetingParticipantError(
        msg,
        context={"meeting_id": meeting_id, **context},
    )


def format_exception(exc: BaseException) -> str:
    """Format an exception for error messages.

    Flattens ``BaseExceptionGroup`` (produced by ``asyncio.TaskGroup``
    when multiple concurrent tasks fail) into a single human-readable
    string.  Handles nested groups recursively.  Non-group exceptions
    are returned via ``safe_error_description()`` so callers never
    embed unredacted ``str(exc)`` into log/UI fields.

    ``BaseExceptionGroup`` is checked rather than ``ExceptionGroup`` so
    a group mixing ``Exception`` and ``BaseException`` (e.g. a
    ``CancelledError`` alongside a failure) still flattens correctly:
    ``TaskGroup`` raises a bare ``BaseExceptionGroup`` in that case, and
    ``ExceptionGroup`` is a subclass so plain groups are still caught.

    Args:
        exc: The exception to format, possibly a ``BaseExceptionGroup``.

    Returns:
        A flattened, scrubbed, human-readable description of the
        exception (recursing into ``BaseExceptionGroup``s).
    """
    if isinstance(exc, BaseExceptionGroup):
        parts: list[str] = []
        for sub in exc.exceptions:
            if isinstance(sub, BaseExceptionGroup):
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
        _fail_participant(
            meeting_id,
            "at least one participant is required",
            "At least one participant is required",
        )
    if len(participant_ids) != len(set(participant_ids)):
        dupes = sorted(v for v, c in Counter(participant_ids).items() if c > 1)
        _fail_participant(
            meeting_id,
            "duplicate participant_ids",
            f"Duplicate participant IDs: {dupes}",
            duplicates=dupes,
        )
    if leader_id in participant_ids:
        _fail_participant(
            meeting_id,
            "leader in participant_ids",
            (
                f"Leader {leader_id!r} must not be in participant_ids "
                f"(leader participates implicitly)"
            ),
            leader_id=leader_id,
        )
