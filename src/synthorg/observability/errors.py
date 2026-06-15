"""Domain errors for the observability sink layer.

Kept dependency-light (only :mod:`synthorg.core.domain_errors` and the
error taxonomy) so the log-sink builders can raise a typed,
RFC 9457-aware error without coupling observability to higher layers.
"""

from typing import ClassVar

from synthorg.core.domain_errors import DomainError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode


class SinkConstructionError(DomainError):
    """Raised when a log-shipping sink handler cannot be constructed.

    Covers genuine construction / environment failures (for example a
    syslog endpoint that refuses the OS-level socket connection), as
    distinct from a user-correctable invalid config (empty host / URL),
    which the builders surface as ``ValueError`` so the sink-test
    endpoint can return a structured ``valid=False`` body.
    """

    default_message: ClassVar[str] = "Failed to construct log sink handler"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.SINK_CONSTRUCTION_ERROR
    status_code: ClassVar[int] = 500
