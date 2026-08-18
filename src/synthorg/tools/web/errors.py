"""Domain error hierarchy for the native web search and fetch providers.

Every failure path raises a ``WebSearch<Condition>Error`` or
``WebFetch<Condition>Error`` subclass of
:class:`synthorg.tools.errors.ToolError` so the
``check_domain_error_hierarchy.py`` gate stays clean and the tool can
surface a stable, credential-free message to the agent. All subclasses
reuse the shareable ``TOOL_EXECUTION_ERROR`` code (the same code the
governed external-access errors share), so the error-code-uniqueness
gate stays satisfied without per-line exemptions.
"""

from typing import ClassVar

from pydantic import JsonValue

from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode
from synthorg.tools.errors import ToolError


class WebSearchError(ToolError):
    """Base for all native web-search domain errors."""

    status_code: ClassVar[int] = 502
    error_code: ClassVar[ErrorCode] = ErrorCode.TOOL_EXECUTION_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    default_message: ClassVar[str] = "Web search failed"


class WebSearchConfigurationError(WebSearchError):
    """The selected provider or its bound connection is misconfigured."""

    status_code: ClassVar[int] = 500
    error_code: ClassVar[ErrorCode] = ErrorCode.TOOL_EXECUTION_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    default_message: ClassVar[str] = "Web search provider is misconfigured"


class WebSearchEgressBlockedError(WebSearchError):
    """The provider endpoint failed the SSRF / network-policy check."""

    status_code: ClassVar[int] = 403
    error_code: ClassVar[ErrorCode] = ErrorCode.TOOL_EXECUTION_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    default_message: ClassVar[str] = "Web search endpoint blocked by network policy"


class WebSearchResponseError(WebSearchError):
    """The provider returned a non-retryable error status or malformed body."""

    status_code: ClassVar[int] = 502
    error_code: ClassVar[ErrorCode] = ErrorCode.TOOL_EXECUTION_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    default_message: ClassVar[str] = "Web search upstream returned an error"


class WebSearchTransientError(WebSearchError):
    """A transient upstream failure (timeout, transport error, 429/5xx).

    Carries an optional ``retry_after_seconds`` parsed from a ``Retry-After``
    response header so the retry handler can honour the server's own cooldown
    instead of the fixed exponential schedule.
    """

    status_code: ClassVar[int] = 503
    error_code: ClassVar[ErrorCode] = ErrorCode.TOOL_EXECUTION_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    default_message: ClassVar[str] = "Web search upstream is temporarily unavailable"

    def __init__(
        self,
        message: str,
        *,
        context: dict[str, JsonValue] | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        """Initialise, capturing an optional server-supplied cooldown."""
        super().__init__(message, context=context)
        self.retry_after_seconds = retry_after_seconds


class WebFetchError(ToolError):
    """Base for all page-fetch domain errors."""

    status_code: ClassVar[int] = 502
    error_code: ClassVar[ErrorCode] = ErrorCode.TOOL_EXECUTION_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    default_message: ClassVar[str] = "Web fetch failed"


class WebFetchConfigurationError(WebFetchError):
    """The selected fetch rung or its bound connection is misconfigured."""

    status_code: ClassVar[int] = 500
    error_code: ClassVar[ErrorCode] = ErrorCode.TOOL_EXECUTION_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    default_message: ClassVar[str] = "Web fetch backend is misconfigured"


class WebFetchEgressBlockedError(WebFetchError):
    """The fetch target failed the SSRF / network-policy check."""

    status_code: ClassVar[int] = 403
    error_code: ClassVar[ErrorCode] = ErrorCode.TOOL_EXECUTION_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    default_message: ClassVar[str] = "Web fetch target blocked by network policy"


class WebFetchResponseError(WebFetchError):
    """The target or reader returned a non-retryable status or bad body."""

    status_code: ClassVar[int] = 502
    error_code: ClassVar[ErrorCode] = ErrorCode.TOOL_EXECUTION_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    default_message: ClassVar[str] = "Web fetch upstream returned an error"


class WebFetchTransientError(WebFetchError):
    """A transient fetch failure (timeout, transport error, 429/5xx).

    Carries an optional ``retry_after_seconds`` parsed from a ``Retry-After``
    header so the retry handler honours the server's own cooldown.
    """

    status_code: ClassVar[int] = 503
    error_code: ClassVar[ErrorCode] = ErrorCode.TOOL_EXECUTION_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    default_message: ClassVar[str] = "Web fetch upstream is temporarily unavailable"

    def __init__(
        self,
        message: str,
        *,
        context: dict[str, JsonValue] | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        """Initialise, capturing an optional server-supplied cooldown."""
        super().__init__(message, context=context)
        self.retry_after_seconds = retry_after_seconds
