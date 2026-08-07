# module-kind: code
"""Subsystem-graph domain errors."""

from typing import ClassVar

from synthorg.core.domain_errors import DomainError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode


class SubsystemGraphInvalidError(DomainError):
    """The declared subsystem graph cannot be ordered.

    Raised at registry-build time for a dependency cycle or a required
    capability nothing provides. Both are declaration bugs that would
    otherwise surface as a subsystem that silently never activates.
    """

    default_message: ClassVar[str] = "Subsystem dependency graph is invalid"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.SUBSYSTEM_GRAPH_INVALID
    status_code: ClassVar[int] = 500


class SubsystemDeclinedError(DomainError):
    """An activation refused to install its capability, and says why.

    Not a failure: the subsystem is correctly and deliberately not up, and
    the next pass will try again. It exists because the reconciler can
    otherwise only guess at the reason from the declared settings, which
    leaves a subsystem that declined on an undeclared condition reading
    BLOCKED with nowhere for an operator to look. An activation that knows
    why it is declining raises this instead of returning quietly, and the
    reason reaches ``GET /subsystems`` verbatim.

    Args:
        reason: What is missing, phrased for an operator who has to fix it.
    """

    default_message: ClassVar[str] = "Subsystem activation declined"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.SUBSYSTEM_DECLINED
    status_code: ClassVar[int] = 503

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class SubsystemActivationError(DomainError):
    """A subsystem's activation raised during a pass whose caller cannot proceed.

    The reconciler records a failed activation and carries on, which is right
    for a sweep. A caller answering a one-shot question ("is this deployment
    configured?") raises this instead, so a fault is reported rather than
    written over.
    """

    default_message: ClassVar[str] = "Subsystem activation failed"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.SUBSYSTEM_ACTIVATION_FAILED
    status_code: ClassVar[int] = 500
