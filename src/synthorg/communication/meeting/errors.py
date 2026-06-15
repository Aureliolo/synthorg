"""Meeting protocol error hierarchy (see Communication design page).

All meeting errors extend ``CommunicationError`` and inherit its
immutable context mapping for structured metadata.
"""

from synthorg.communication.errors import CommunicationError


class MeetingError(CommunicationError):
    """Base exception for all meeting-related errors."""


class MeetingBudgetExhaustedError(MeetingError):
    """Token budget exhausted during meeting execution."""


class MeetingProtocolNotFoundError(MeetingError):
    """Requested meeting protocol type is not registered."""


class MeetingEmbedderUnavailableError(MeetingError):
    """The selected text-embedding backend could not be constructed.

    Raised when the ``sentence_transformer`` embedder strategy is
    selected but the optional ``sentence-transformers`` extra is not
    installed.
    """


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
