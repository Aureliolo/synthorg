"""Domain error hierarchy for the governed external-access tool.

Every failure path raises an ``ExternalApi<Condition>Error`` subclass of
:class:`synthorg.tools.errors.ToolError` so the
``check_domain_error_hierarchy.py`` gate stays clean and callers can
discriminate failures by ``error_code`` / class.
"""

from typing import ClassVar

from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode
from synthorg.tools.errors import ToolError


class ExternalApiError(ToolError):
    """Base for all governed external-access tool domain errors."""

    status_code: ClassVar[int] = 500
    error_code: ClassVar[ErrorCode] = ErrorCode.TOOL_EXECUTION_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    default_message: ClassVar[str] = "External API tool failure"


class ExternalApiArgumentError(ExternalApiError):
    """Arguments violated a validation invariant the args model alone cannot express."""

    status_code: ClassVar[int] = 422
    error_code: ClassVar[ErrorCode] = ErrorCode.TOOL_PARAMETER_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.VALIDATION
    default_message: ClassVar[str] = "External API tool arguments invalid"


class ExternalApiConnectionNotFoundError(
    ExternalApiError
):  # lint-allow: error-code-uniqueness -- twin of ConnectionNotFoundError
    """Named connection is absent from the connection catalog."""

    status_code: ClassVar[int] = 404
    error_code: ClassVar[ErrorCode] = ErrorCode.CONNECTION_NOT_FOUND
    error_category: ClassVar[ErrorCategory] = ErrorCategory.NOT_FOUND
    default_message: ClassVar[str] = "External API connection not found"


class ExternalApiEgressBlockedError(ExternalApiError):
    """Target host failed the SSRF / connection-host allowlist check."""

    status_code: ClassVar[int] = 403
    error_code: ClassVar[ErrorCode] = ErrorCode.FORBIDDEN
    error_category: ClassVar[ErrorCategory] = ErrorCategory.AUTH
    default_message: ClassVar[str] = "External API egress blocked"


class ExternalApiCredentialError(ExternalApiError):
    """Credentials could not be brokered or applied for the connection."""

    status_code: ClassVar[int] = 500
    error_code: ClassVar[ErrorCode] = ErrorCode.TOOL_EXECUTION_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    default_message: ClassVar[str] = "External API credential brokering failed"


class ExternalApiApprovalMismatchError(ExternalApiError):
    """A supplied approval did not match the call, or was already consumed."""

    status_code: ClassVar[int] = 409
    error_code: ClassVar[ErrorCode] = ErrorCode.RESOURCE_CONFLICT
    error_category: ClassVar[ErrorCategory] = ErrorCategory.CONFLICT
    default_message: ClassVar[str] = (
        "External API approval mismatch or already consumed"
    )


class ExternalApiRateLimitedError(ExternalApiError):
    """The connection's rate-limit window is exhausted."""

    status_code: ClassVar[int] = 429
    error_code: ClassVar[ErrorCode] = ErrorCode.RATE_LIMITED
    error_category: ClassVar[ErrorCategory] = ErrorCategory.RATE_LIMIT
    default_message: ClassVar[str] = "External API rate limit exceeded"


class ExternalApiResponseError(ExternalApiError):
    """The upstream request failed (timeout, connection error, or 5xx)."""

    status_code: ClassVar[int] = 502
    error_code: ClassVar[ErrorCode] = ErrorCode.TOOL_EXECUTION_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    default_message: ClassVar[str] = "External API upstream request failed"
