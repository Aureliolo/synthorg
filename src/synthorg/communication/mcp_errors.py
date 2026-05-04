"""Shared errors for the communication MCP facades.

:class:`CapabilityNotSupportedError` is raised by a facade method whose
underlying primitive does not yet expose the required operation.  The
MCP handler layer catches it and emits a typed ``err(...,
domain_code="not_supported")`` envelope -- different from the
:func:`capability_gap` placeholder path because the request reached a
real service before the gap was detected.
"""

from typing import ClassVar

from synthorg.core.domain_errors import FeatureNotImplementedError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode


class CapabilityNotSupportedError(FeatureNotImplementedError):
    """Raised when a facade method's underlying primitive cannot satisfy the op.

    Attributes:
        domain_code: Stable wire identifier (``"not_supported"``).
        capability: Which capability was missing; surfaces in the
            error message for operator observability.
    """

    domain_code: ClassVar[str] = "not_supported"
    default_message: ClassVar[str] = "Capability not supported by underlying primitive"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.FEATURE_NOT_IMPLEMENTED
    status_code: ClassVar[int] = 501

    def __init__(self, capability: str, detail: str) -> None:
        """Initialise with a capability name and reason.

        Args:
            capability: Short identifier for the missing capability.
            detail: Human-readable reason suitable for the error
                envelope's ``message`` field (already scrub-safe since
                it never contains caller-supplied data).
        """
        super().__init__(f"{capability}: {detail}")
        self.capability = capability
