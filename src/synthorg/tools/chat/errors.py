"""Domain error hierarchy for the chat agent tools.

Every failure path raises a ``Chat<Condition>Error`` subclass of
:class:`synthorg.tools.errors.ToolError` so the
``check_domain_error_hierarchy.py`` gate stays clean and callers can
discriminate failures by ``error_code`` / class. The tool maps the
lower-level ``ChatApi*`` client errors onto these.
"""

from typing import ClassVar

from pydantic import JsonValue

from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode
from synthorg.tools.errors import ToolError


class ChatToolError(ToolError):
    """Base for all chat agent-tool domain errors."""

    status_code: ClassVar[int] = 500
    error_code: ClassVar[ErrorCode] = ErrorCode.TOOL_EXECUTION_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    default_message: ClassVar[str] = "Chat tool failure"


class ChatToolArgumentError(ChatToolError):
    """Arguments were structurally valid but semantically unusable."""

    status_code: ClassVar[int] = 422
    error_code: ClassVar[ErrorCode] = ErrorCode.TOOL_PARAMETER_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.VALIDATION
    default_message: ClassVar[str] = "Chat tool arguments invalid"


class ChatConnectionNotFoundError(
    ChatToolError
):  # lint-allow: error-code-uniqueness -- twin of ConnectionNotFoundError
    """The configured chat connection is absent from the catalog."""

    status_code: ClassVar[int] = 404
    error_code: ClassVar[ErrorCode] = ErrorCode.CONNECTION_NOT_FOUND
    error_category: ClassVar[ErrorCategory] = ErrorCategory.NOT_FOUND
    default_message: ClassVar[str] = "Chat connection not found"


class ChatUnsupportedError(
    ChatToolError
):  # lint-allow: error-code-uniqueness -- twin of FeatureNotImplementedError
    """The bound connection type has no chat Web API client."""

    status_code: ClassVar[int] = 501
    error_code: ClassVar[ErrorCode] = ErrorCode.FEATURE_NOT_IMPLEMENTED
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    default_message: ClassVar[str] = "Chat platform not supported for this connection"


class ChatCredentialError(ChatToolError):
    """Credentials could not be brokered for the chat connection."""

    status_code: ClassVar[int] = 500
    error_code: ClassVar[ErrorCode] = ErrorCode.TOOL_EXECUTION_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    default_message: ClassVar[str] = "Chat credential brokering failed"


class ChatRateLimitedError(ChatToolError):
    """The chat platform rate-limited the request.

    Carries the platform-advertised cooldown so the agent (the outer
    retry loop) can honour it rather than immediately re-hammering the
    chat API.
    """

    status_code: ClassVar[int] = 429
    error_code: ClassVar[ErrorCode] = ErrorCode.RATE_LIMITED
    error_category: ClassVar[ErrorCategory] = ErrorCategory.RATE_LIMIT
    default_message: ClassVar[str] = "Chat rate limit exceeded"

    retry_after_seconds: float | None

    def __init__(
        self,
        message: str,
        *,
        retry_after_seconds: float | None = None,
        context: dict[str, JsonValue] | None = None,
    ) -> None:
        """Initialise with an optional platform-advertised cooldown.

        Args:
            message: Human-readable error description.
            retry_after_seconds: Seconds to wait before retrying, when
                the platform advertised a ``Retry-After``.
            context: Arbitrary error metadata.
        """
        super().__init__(message, context=context)
        self.retry_after_seconds = retry_after_seconds


class ChatUpstreamError(ChatToolError):
    """The chat request failed (auth, transport, or API error)."""

    status_code: ClassVar[int] = 502
    error_code: ClassVar[ErrorCode] = ErrorCode.TOOL_EXECUTION_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    default_message: ClassVar[str] = "Chat upstream request failed"


__all__ = [
    "ChatConnectionNotFoundError",
    "ChatCredentialError",
    "ChatRateLimitedError",
    "ChatToolArgumentError",
    "ChatToolError",
    "ChatUnsupportedError",
    "ChatUpstreamError",
]
