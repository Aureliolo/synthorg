"""Meeting protocol error hierarchy (see Communication design page).

All meeting errors extend ``CommunicationError`` and carry an
immutable context mapping for structured metadata.
"""

from ai_company.communication.errors import CommunicationError


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
