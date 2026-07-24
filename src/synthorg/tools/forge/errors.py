"""Domain error hierarchy for the forge agent tools.

Every failure path raises a ``Forge<Condition>Error`` subclass of
:class:`synthorg.tools.errors.ToolError` so the
``check_domain_error_hierarchy.py`` gate stays clean and callers can
discriminate failures by ``error_code`` / class. The tool maps the
lower-level forge-client errors (``GitBackendForge*``,
``FeatureNotImplementedError``) onto these.
"""

from typing import ClassVar

from pydantic import JsonValue

from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode
from synthorg.tools.errors import ToolError


class ForgeToolError(ToolError):
    """Base for all forge agent-tool domain errors."""

    status_code: ClassVar[int] = 500
    error_code: ClassVar[ErrorCode] = ErrorCode.TOOL_EXECUTION_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    default_message: ClassVar[str] = "Forge tool failure"


class ForgeToolArgumentError(ForgeToolError):
    """Arguments were structurally valid but semantically unusable."""

    status_code: ClassVar[int] = 422
    error_code: ClassVar[ErrorCode] = ErrorCode.TOOL_PARAMETER_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.VALIDATION
    default_message: ClassVar[str] = "Forge tool arguments invalid"


class ForgeConnectionNotFoundError(
    ForgeToolError
):  # lint-allow: error-code-uniqueness -- twin of ConnectionNotFoundError
    """The configured forge connection is absent from the catalog."""

    status_code: ClassVar[int] = 404
    error_code: ClassVar[ErrorCode] = ErrorCode.CONNECTION_NOT_FOUND
    error_category: ClassVar[ErrorCategory] = ErrorCategory.NOT_FOUND
    default_message: ClassVar[str] = "Forge connection not found"


class ForgeUnsupportedError(
    ForgeToolError
):  # lint-allow: error-code-uniqueness -- twin of FeatureNotImplementedError
    """The bound forge (or operation) has no agent-operations client."""

    status_code: ClassVar[int] = 501
    error_code: ClassVar[ErrorCode] = ErrorCode.FEATURE_NOT_IMPLEMENTED
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    default_message: ClassVar[str] = "Forge operation not supported for this forge"


class ForgeCredentialError(ForgeToolError):
    """Credentials could not be brokered for the forge connection."""

    status_code: ClassVar[int] = 500
    error_code: ClassVar[ErrorCode] = ErrorCode.TOOL_EXECUTION_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    default_message: ClassVar[str] = "Forge credential brokering failed"


class ForgeRateLimitedError(ForgeToolError):
    """The forge rate-limited the request.

    Carries the forge-advertised cooldown so the agent (the outer retry
    loop) can honour it rather than immediately re-hammering the forge.
    """

    status_code: ClassVar[int] = 429
    error_code: ClassVar[ErrorCode] = ErrorCode.RATE_LIMITED
    error_category: ClassVar[ErrorCategory] = ErrorCategory.RATE_LIMIT
    # A rate limit is transient by definition; match every sibling
    # RateLimitError leaf so the wire/retry contract stays consistent.
    retryable: ClassVar[bool] = True
    default_message: ClassVar[str] = "Forge rate limit exceeded"

    retry_after_seconds: float | None

    def __init__(
        self,
        message: str,
        *,
        retry_after_seconds: float | None = None,
        context: dict[str, JsonValue] | None = None,
    ) -> None:
        """Initialise with an optional forge-advertised cooldown.

        Args:
            message: Human-readable error description.
            retry_after_seconds: Seconds to wait before retrying, when
                the forge advertised a ``Retry-After``.
            context: Arbitrary error metadata.
        """
        super().__init__(message, context=context)
        self.retry_after_seconds = retry_after_seconds


class ForgeUpstreamError(ForgeToolError):
    """Base for a failed forge request (auth, transport, or non-2xx).

    Split into a non-retryable auth leaf and a retryable API leaf so a
    permanent auth failure and a transient upstream failure are never
    conflated into one retryability verdict (mirrors the engine-layer
    ``GitBackendForgeAuthError`` / ``GitBackendForgeApiError`` split).
    """

    status_code: ClassVar[int] = 502
    error_code: ClassVar[ErrorCode] = ErrorCode.TOOL_EXECUTION_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    default_message: ClassVar[str] = "Forge upstream request failed"


class ForgeUpstreamAuthError(
    ForgeUpstreamError
):  # lint-allow: error-code-uniqueness -- inheritance alias of ForgeUpstreamError
    """Forge authentication failed; a deterministic, non-retryable failure."""

    retryable: ClassVar[bool] = False
    default_message: ClassVar[str] = "Forge authentication failed"


class ForgeUpstreamApiError(
    ForgeUpstreamError
):  # lint-allow: error-code-uniqueness -- inheritance alias of ForgeUpstreamError
    """Forge returned a non-2xx / transport failure; retryable."""

    retryable: ClassVar[bool] = True
    default_message: ClassVar[str] = "Forge upstream request failed"


class ForgeRepoScopeError(ForgeToolError):
    """The requested repository is outside the connection's allowed scope.

    Repo scope is least-privilege and fail-closed: a connection with no
    repositories selected denies every repository, and a selected scope
    admits only its ``owner/repo`` entries (``owner/*`` globs permitted).
    """

    status_code: ClassVar[int] = 403
    error_code: ClassVar[ErrorCode] = ErrorCode.FORBIDDEN
    error_category: ClassVar[ErrorCategory] = ErrorCategory.AUTH
    default_message: ClassVar[str] = "Repository is outside the connection's scope"


__all__ = [
    "ForgeConnectionNotFoundError",
    "ForgeCredentialError",
    "ForgeRateLimitedError",
    "ForgeRepoScopeError",
    "ForgeToolArgumentError",
    "ForgeToolError",
    "ForgeUnsupportedError",
    "ForgeUpstreamApiError",
    "ForgeUpstreamAuthError",
    "ForgeUpstreamError",
]
