"""Domain errors for the self-improving meta-loop.

Errors here are raised by the service layer and translated to MCP /
REST envelopes by the handler layer. They carry enough context for
operators to disambiguate why a cycle could not run without leaking
internal config state.
"""

from typing import ClassVar

from synthorg.core.domain_errors import DomainError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode


class SelfImprovementError(DomainError):
    """Base class for self-improvement service domain errors."""

    default_message: ClassVar[str] = "Self-improvement operation failed"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.INTERNAL_ERROR
    status_code: ClassVar[int] = 500


class SelfImprovementTriggerError(SelfImprovementError):
    """Raised when ``SelfImprovementService.trigger_cycle`` cannot run.

    Triggers fail when prerequisites are missing -- for example, no
    snapshot builder is wired -- rather than running with degraded
    inputs that would produce misleading proposals.
    """


class RollbackMutationDeniedError(SelfImprovementError):
    """Raised by a rollback mutator when the underlying store refuses a write.

    Examples: a ``read_only_post_init`` setting whose value cannot be
    overwritten post-startup, a frozen entity that has been retired, or
    an architecture target that no longer exists. The rollback executor
    propagates this so the audit log records the refused operation
    rather than silently skipping it.
    """

    default_message: ClassVar[str] = "Rollback mutation denied"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.CONFLICT
    error_code: ClassVar[ErrorCode] = ErrorCode.RESOURCE_CONFLICT
    status_code: ClassVar[int] = 409


class UnknownArchitectureTargetError(RollbackMutationDeniedError):
    """Raised when ``ArchitectureRestoreRouter`` cannot parse the target.

    Target strings follow the ``"<type>:<id>[:<sub-id>]"`` convention
    (``"role:agent-007"``, ``"department:engineering"``,
    ``"workflow:wf-123:v4"``). Unknown ``<type>`` prefixes surface this
    error so the rollback executor logs a structured failure rather
    than silently no-op'ing.
    """

    default_message: ClassVar[str] = "Unknown architecture-restore target"


class ChiefOfStaffError(DomainError):
    """Base class for the conversational clarify-and-propose interface.

    Raised by ``ChiefOfStaffProposer`` and the ``/meta/chat/propose``
    boundary; translated to REST envelopes by the handler layer.
    """

    default_message: ClassVar[str] = "Chief of Staff operation failed"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.INTERNAL_ERROR
    status_code: ClassVar[int] = 500


class ConversationNotFoundError(ChiefOfStaffError):
    """Raised when a referenced conversation id does not exist.

    The message is deliberately generic; the ``conversation_id`` is
    available for structured logs but must NOT reach user-facing
    responses.

    Attributes:
        conversation_id: The conversation id that was not found.
    """

    default_message: ClassVar[str] = "Conversation not found"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.NOT_FOUND
    error_code: ClassVar[ErrorCode] = ErrorCode.CONVERSATION_NOT_FOUND
    status_code: ClassVar[int] = 404

    def __init__(self, *, conversation_id: str) -> None:
        super().__init__("Conversation not found")
        self.conversation_id: str = conversation_id


class ConversationClosedError(ChiefOfStaffError):
    """Raised when a turn is sent to a CLOSED conversation.

    A closed conversation is terminal; the caller must open a new one
    rather than resurrecting a finished thread.

    Attributes:
        conversation_id: The closed conversation id.
    """

    default_message: ClassVar[str] = "Conversation is closed"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.CONFLICT
    error_code: ClassVar[ErrorCode] = ErrorCode.CONVERSATION_CLOSED
    status_code: ClassVar[int] = 409

    def __init__(self, *, conversation_id: str) -> None:
        super().__init__("Conversation is closed")
        self.conversation_id: str = conversation_id


class ConversationalProposeUnavailableError(ChiefOfStaffError):
    """Raised when the propose path is not fully wired.

    Surfaces when ``propose_enabled`` is off, no provider is
    registered, the work pipeline is absent, or the conversational
    repositories were not connected. The operator can fix the
    underlying configuration and retry.
    """

    default_message: ClassVar[str] = "Conversational propose interface is unavailable"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.SERVICE_UNAVAILABLE
    status_code: ClassVar[int] = 503


class ConversationalProposeResponseInvalidError(ChiefOfStaffError):
    """Raised when the model's clarify-or-propose output is unparseable.

    The structured-output contract (``ProposeDecision``) was violated:
    the response was not valid JSON or failed schema validation. Never
    silently swallowed -- the turn fails loudly so the operator sees a
    real upstream problem rather than a dropped request.
    """

    default_message: ClassVar[str] = (
        "Chief of Staff produced an invalid proposal response"
    )
    error_category: ClassVar[ErrorCategory] = ErrorCategory.PROVIDER_ERROR
    error_code: ClassVar[ErrorCode] = ErrorCode.CONVERSATIONAL_PROPOSE_RESPONSE_INVALID
    status_code: ClassVar[int] = 502


class CharterError(DomainError):
    """Base class for the deep CEO interview to project charter flow.

    Raised by ``CharterInterviewService`` / ``CharterDispatcher`` and the
    ``/meta/charters`` boundary; translated to REST envelopes by the
    handler layer.
    """

    default_message: ClassVar[str] = "Charter operation failed"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.INTERNAL_ERROR
    status_code: ClassVar[int] = 500


class CharterNotFoundError(CharterError):
    """Raised when a referenced charter id does not exist.

    Attributes:
        charter_id: The charter id that was not found.
    """

    default_message: ClassVar[str] = "Charter not found"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.NOT_FOUND
    error_code: ClassVar[ErrorCode] = ErrorCode.CHARTER_NOT_FOUND
    status_code: ClassVar[int] = 404

    def __init__(self, *, charter_id: str) -> None:
        super().__init__("Charter not found")
        self.charter_id: str = charter_id


class CharterNotEditableError(CharterError):
    """Raised when an edit targets a charter that is no longer DRAFTED.

    Edits are only permitted while a charter is under review; once it is
    APPROVED (dispatched) or CANCELLED it is immutable.

    Attributes:
        charter_id: The charter id that could not be edited.
    """

    default_message: ClassVar[str] = "Charter is not editable"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.CONFLICT
    error_code: ClassVar[ErrorCode] = ErrorCode.CHARTER_NOT_EDITABLE
    status_code: ClassVar[int] = 409

    def __init__(self, *, charter_id: str) -> None:
        super().__init__("Charter is not editable")
        self.charter_id: str = charter_id


class CharterAlreadyDecidedError(CharterError):
    """Raised when approve / cancel targets a non-DRAFTED charter.

    The ``DRAFTED -> APPROVED | CANCELLED`` transition is a
    single-winner compare-and-set; a losing concurrent decision (or a
    replay) surfaces this rather than re-running the dispatch.

    Attributes:
        charter_id: The charter id whose decision was already taken.
    """

    default_message: ClassVar[str] = "Charter has already been decided"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.CONFLICT
    error_code: ClassVar[ErrorCode] = ErrorCode.CHARTER_ALREADY_DECIDED
    status_code: ClassVar[int] = 409

    def __init__(self, *, charter_id: str) -> None:
        super().__init__("Charter has already been decided")
        self.charter_id: str = charter_id


class CharterInterviewUnavailableError(CharterError):
    """Raised when the charter-interview path is not fully wired.

    Surfaces when ``interview_enabled`` is off, no provider is
    registered, or the persistence backend / charter store was not
    connected. The operator can fix the configuration and retry.
    """

    default_message: ClassVar[str] = "Charter interview interface is unavailable"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.SERVICE_UNAVAILABLE
    status_code: ClassVar[int] = 503


class CharterInterviewResponseInvalidError(CharterError):
    """Raised when the interview model output is unparseable.

    The structured-output contract (``InterviewDecision``) was violated:
    the response was not valid JSON or failed schema validation. Never
    silently swallowed; the turn fails loudly so the operator sees a
    real upstream problem rather than a dropped request.
    """

    default_message: ClassVar[str] = "Charter interviewer produced an invalid response"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.PROVIDER_ERROR
    error_code: ClassVar[ErrorCode] = ErrorCode.CHARTER_INTERVIEW_RESPONSE_INVALID
    status_code: ClassVar[int] = 502


class UnknownCharterStrategyError(CharterError):
    """Raised when the interview-strategy discriminator maps to no strategy.

    Mirrors the project-wide pluggable-subsystems contract: a
    misconfigured discriminator is a hard error at construction time,
    never a silent fallback to a default.

    Attributes:
        strategy: The unrecognised discriminator value.
    """

    default_message: ClassVar[str] = "Unknown charter interview strategy"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.VALIDATION
    error_code: ClassVar[ErrorCode] = ErrorCode.VALIDATION_ERROR
    status_code: ClassVar[int] = 422

    def __init__(self, *, strategy: str) -> None:
        super().__init__(f"Unknown charter interview strategy: {strategy!r}")
        self.strategy: str = strategy


class CharterStateInconsistentError(CharterError):
    """Raised when a successful state transition is followed by a missing row.

    ``transition_if`` returned ``True`` (the persistence layer reported
    a winning compare-and-set) but the immediate re-fetch returned
    ``None``. That contradicts the storage contract and would surface
    a stale pre-transition object to the caller; refusing to return is
    safer than smuggling a mismatched status downstream.

    Attributes:
        charter_id: The charter id whose state could not be re-read.
    """

    default_message: ClassVar[str] = (
        "Charter row vanished after a successful transition"
    )
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.PERSISTENCE_ERROR
    status_code: ClassVar[int] = 500

    def __init__(self, *, charter_id: str) -> None:
        super().__init__("Charter row vanished after a successful transition")
        self.charter_id: str = charter_id
