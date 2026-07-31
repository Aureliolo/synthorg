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
