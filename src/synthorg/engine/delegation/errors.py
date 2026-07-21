# module-kind: declarative
"""Error hierarchy for blocking sub-agent delegation.

Named ``SubAgentDelegation*`` to stay distinct from the async,
authority-based ``synthorg.communication.errors.DelegationError`` family:
the two subsystems are unrelated, and sharing a bare ``DelegationError``
symbol would let an ``except DelegationError`` bind the wrong hierarchy.
"""

from typing import ClassVar

from synthorg.core.domain_errors import NotFoundError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode
from synthorg.core.types import NotBlankStr, require_not_blank
from synthorg.engine.errors import EngineError


class SubAgentDelegationError(EngineError):
    """Base for blocking sub-agent delegation failures.

    Inherits the 5xx INTERNAL defaults from :class:`EngineError`;
    subclasses narrow ``status_code`` / ``error_code`` for meaningful
    exposure and structured logs.
    """


class SubAgentDelegationTargetNotFoundError(SubAgentDelegationError, NotFoundError):
    """Raised when the delegation target resolves to no registered agent.

    The wire message stays generic to avoid leaking the requested
    identifier; the ``target`` attribute is for structured logs only.
    """

    status_code: ClassVar[int] = 404
    error_code: ClassVar[ErrorCode] = ErrorCode.DELEGATION_TARGET_NOT_FOUND
    error_category: ClassVar[ErrorCategory] = ErrorCategory.NOT_FOUND
    default_message: ClassVar[str] = "Delegation target agent not found"

    def __init__(self, *, target: NotBlankStr) -> None:
        super().__init__(self.default_message)
        self.target: NotBlankStr = require_not_blank(target, "target")


class SubAgentDelegationDepthExceededError(SubAgentDelegationError):
    """Raised when a delegation would exceed the maximum chain depth.

    Covers both a straight chain deeper than ``engine.delegation_max_depth``
    and a cycle (the target already appears as an ancestor's assignee): both
    are unbounded-recursion conditions that must be refused before the child
    run is dispatched. Maps to 409 (Conflict). The ``depth`` / ``max_depth``
    attributes are for structured logs only.
    """

    status_code: ClassVar[int] = 409
    error_code: ClassVar[ErrorCode] = ErrorCode.SUB_AGENT_DELEGATION_DEPTH_EXCEEDED
    error_category: ClassVar[ErrorCategory] = ErrorCategory.CONFLICT
    default_message: ClassVar[str] = "Delegation chain depth limit exceeded"

    def __init__(self, *, depth: int, max_depth: int) -> None:
        super().__init__(self.default_message)
        self.depth: int = depth
        self.max_depth: int = max_depth
