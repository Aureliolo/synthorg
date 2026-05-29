"""Communication error hierarchy (see Communication design page).

All communication errors carry an immutable context mapping for
structured metadata, following the same pattern as ``ToolError``.
"""

import copy
from types import MappingProxyType
from typing import Any, ClassVar, override

from synthorg.core.domain_errors import DomainError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode


class CommunicationError(DomainError):
    """Base exception for all communication-layer errors.

    Attributes:
        message: Human-readable error description.
        context: Immutable metadata about the error.

    Class Attributes:
        status_code: HTTP 500 default.
        error_code: ``COMMUNICATION_ERROR``.
        error_category: ``INTERNAL``.
        retryable: ``False``.
        default_message: Generic 5xx-safe message.
    """

    status_code: ClassVar[int] = 500
    error_code: ClassVar[ErrorCode] = ErrorCode.COMMUNICATION_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    retryable: ClassVar[bool] = False
    default_message: ClassVar[str] = "Communication error"

    def __init__(
        self,
        message: str,
        *,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Initialize a communication error.

        Args:
            message: Human-readable error description.
            context: Arbitrary metadata about the error. Stored as an
                immutable mapping; defaults to empty if not provided.
        """
        self.message = message
        self.context: MappingProxyType[str, Any] = MappingProxyType(
            copy.deepcopy(context) if context else {},
        )
        super().__init__(message)

    @override
    def __str__(self) -> str:
        """Format error with optional context metadata."""
        if self.context:
            ctx = ", ".join(f"{k}={v!r}" for k, v in self.context.items())
            return f"{self.message} ({ctx})"
        return self.message


class ChannelNotFoundError(CommunicationError):
    """Requested channel does not exist."""

    status_code: ClassVar[int] = 404
    error_code: ClassVar[ErrorCode] = ErrorCode.CHANNEL_NOT_FOUND
    error_category: ClassVar[ErrorCategory] = ErrorCategory.NOT_FOUND
    default_message: ClassVar[str] = "Channel not found"


class ChannelAlreadyExistsError(CommunicationError):
    """Channel with the given name already exists."""

    status_code: ClassVar[int] = 409
    error_code: ClassVar[ErrorCode] = ErrorCode.CHANNEL_ALREADY_EXISTS
    error_category: ClassVar[ErrorCategory] = ErrorCategory.CONFLICT
    default_message: ClassVar[str] = "Channel already exists"


class NotSubscribedError(CommunicationError):
    """Agent is not subscribed to the specified channel."""


class MessageBusNotRunningError(CommunicationError):
    """Operation attempted on a message bus that is not running."""


class MessageBusAlreadyRunningError(CommunicationError):
    """start() called on a message bus that is already running."""


class DelegationError(CommunicationError):
    """Base exception for delegation-related errors."""


class DelegationAuthorityError(DelegationError):
    """Delegator lacks authority to delegate to the target agent."""


class DelegationLoopError(DelegationError):
    """Base for loop prevention mechanism rejections."""


class DelegationDepthError(DelegationLoopError):
    """Delegation chain exceeds maximum depth."""


class DelegationAncestryError(DelegationLoopError):
    """Delegation would create a cycle in the task ancestry."""


class DelegationRateLimitError(DelegationLoopError):
    """Delegation rate limit exceeded for agent pair."""


class DelegationCircuitOpenError(DelegationLoopError):
    """Circuit breaker is open for agent pair."""


class DelegationDuplicateError(DelegationLoopError):
    """Duplicate delegation detected within dedup window."""


class HierarchyResolutionError(CommunicationError):
    """Error resolving organizational hierarchy."""


class ConflictResolutionError(CommunicationError):
    """Base exception for conflict resolution errors."""


class ConflictStrategyError(ConflictResolutionError):
    """Error within a conflict resolution strategy."""


class ConflictHierarchyError(ConflictResolutionError):
    """No common manager found for cross-department conflict."""


class EscalationDecisionError(ConflictResolutionError):
    """A human-supplied escalation decision cannot be applied.

    Concrete subclasses distinguish the failure mode: the decision shape
    is not accepted by the active processor variant
    (:class:`EscalationDecisionShapeError`) or the decision references
    an agent outside the conflict
    (:class:`EscalationDecisionAgentError`).
    """


class EscalationDecisionShapeError(EscalationDecisionError):
    """The decision variant is not accepted by the active processor."""


class EscalationDecisionAgentError(EscalationDecisionError):
    """A winner decision references an agent outside the conflict."""
