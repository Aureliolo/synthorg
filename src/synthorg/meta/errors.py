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
