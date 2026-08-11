"""Meeting protocol error hierarchy (see Communication design page).

All meeting errors extend ``CommunicationError`` and inherit its
immutable context mapping for structured metadata.
"""

from typing import ClassVar

from synthorg.communication.errors import CommunicationError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode


class MeetingError(CommunicationError):
    """Base exception for all meeting-related errors."""


class MeetingBudgetExhaustedError(MeetingError):
    """Token budget exhausted during meeting execution."""


class MeetingProtocolNotFoundError(MeetingError):
    """Requested meeting protocol type is not registered."""


class MeetingParticipantError(MeetingError):
    """Invalid participant configuration (e.g. empty list, leader in participants)."""


class MeetingAgentError(MeetingError):
    """An agent invocation failed during a meeting."""


class MeetingPhaseSlotError(MeetingError):
    """A meeting-phase TaskGroup slot was unexpectedly empty after success.

    Programming-invariant violation: the per-participant fan-out
    completed without raising (the ``TaskGroup`` would have propagated
    an ``ExceptionGroup`` otherwise), yet a result slot is still
    ``None``. Routes through ``handle_domain_error`` so the API surfaces
    a structured ``COMMUNICATION_ERROR`` envelope (inheriting the
    ancestor's code) rather than a bare 500 from the ``Exception``
    catch-all.
    """


class MeetingSchedulerError(MeetingError):
    """Base exception for meeting scheduler errors."""


class NoParticipantsResolvedError(MeetingSchedulerError):
    """All participant entries resolved to empty."""


class SchedulerAlreadyRunningError(MeetingSchedulerError):
    """start() called on a scheduler that is already running."""


class MeetingCeremonyRegistrationError(MeetingSchedulerError):
    """A sprint's ceremony meeting types cannot be registered.

    Either a type carries no trigger (ceremony cadence belongs to the
    ceremony scheduler, so a ceremony type is reachable only by its
    trigger), or its name collides with a configured meeting type, whose
    per-type cooldown it would silently share.

    Both are refusals of operator-authored configuration, so this is a
    422 rather than the 500 its ``CommunicationError`` ancestor would
    imply: the sprint's ceremonies name something the meeting scheduler
    cannot accept, and the operator can correct the template and retry.
    """

    status_code: ClassVar[int] = 422
    error_code: ClassVar[ErrorCode] = ErrorCode.CEREMONY_REGISTRATION_INVALID
    error_category: ClassVar[ErrorCategory] = ErrorCategory.VALIDATION
    default_message: ClassVar[str] = "Ceremony meeting types cannot be registered"


class MeetingCooldownCleanupError(MeetingSchedulerError):
    """A ceremony's durable cooldown row outlived the sprint that set it.

    Cooldown rows are keyed by meeting type name and sprints reuse
    ceremony names, so a row a teardown failed to delete is read back by
    the next start's hydrate and suppresses the next sprint's first run
    of that ceremony. Reporting the teardown clean is therefore the one
    outcome that hides the defect the deletion exists to prevent.

    The names stay queued on the scheduler and the next teardown retries
    them, so this reports an incomplete cleanup rather than a lost one.
    """
