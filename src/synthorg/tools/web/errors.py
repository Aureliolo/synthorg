"""Domain error hierarchy for the native web-search provider.

Every failure path raises a ``WebSearch<Condition>Error`` subclass of
:class:`synthorg.tools.errors.ToolError` so the
``check_domain_error_hierarchy.py`` gate stays clean and the tool can
surface a stable, credential-free message to the agent. All subclasses
reuse the shareable ``TOOL_EXECUTION_ERROR`` code (the same code the
governed external-access errors share), so the error-code-uniqueness
gate stays satisfied without per-line exemptions.
"""

from typing import ClassVar

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
    """A transient upstream failure (timeout, transport error, 429/5xx)."""

    status_code: ClassVar[int] = 503
    error_code: ClassVar[ErrorCode] = ErrorCode.TOOL_EXECUTION_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    default_message: ClassVar[str] = "Web search upstream is temporarily unavailable"
