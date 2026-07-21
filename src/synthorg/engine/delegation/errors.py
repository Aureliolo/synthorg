# module-kind: declarative
"""Error hierarchy for blocking sub-agent delegation."""

from typing import ClassVar

from synthorg.core.domain_errors import NotFoundError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode
from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import EngineError


class DelegationError(EngineError):
    """Base for blocking-delegation failures.

    Inherits the 5xx INTERNAL defaults from :class:`EngineError`;
    subclasses narrow ``status_code`` / ``error_code`` for meaningful
    exposure and structured logs.
    """


class DelegationTargetNotFoundError(DelegationError, NotFoundError):
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
        self.target: NotBlankStr = target
