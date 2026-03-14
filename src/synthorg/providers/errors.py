"""Provider error hierarchy.

Every provider error carries a ``is_retryable`` flag so retry logic
can decide whether to attempt again without inspecting concrete
exception types.
"""

import math
from types import MappingProxyType
from typing import Any

_REDACTED_KEYS: frozenset[str] = frozenset(
    {"api_key", "token", "secret", "password", "authorization"},
)


def _is_sensitive_key(key: str) -> bool:
    """Check if a context key should be redacted (case-insensitive)."""
    return key.lower() in _REDACTED_KEYS


class ProviderError(Exception):
    """Base exception for all provider-layer errors.

    Attributes:
        message: Human-readable error description.
        context: Immutable metadata about the error (provider, model, etc.).
        is_retryable: Whether the caller should retry the request.

    Note:
        When converted to string, sensitive context keys (api_key, token,
        secret, password, authorization) are automatically redacted
        regardless of casing.
    """

    is_retryable: bool = False

    def __init__(
        self,
        message: str,
        *,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Initialize a provider error.

        Args:
            message: Human-readable error description.
            context: Arbitrary metadata about the error. Stored as an
                immutable mapping; defaults to empty if not provided.
        """
        self.message = message
        self.context: MappingProxyType[str, Any] = MappingProxyType(
            dict(context) if context else {},
        )
        super().__init__(message)

    def __str__(self) -> str:
        """Format error with optional context metadata.

        Sensitive keys (api_key, token, etc.) are redacted to prevent
        accidental secret leakage in logs and tracebacks.
        """
        if self.context:
            ctx = ", ".join(
                f"{k}='***'" if _is_sensitive_key(k) else f"{k}={v!r}"
                for k, v in self.context.items()
            )
            return f"{self.message} ({ctx})"
        return self.message


class AuthenticationError(ProviderError):
    """Invalid or missing API credentials."""

    is_retryable = False


class RateLimitError(ProviderError):
    """Provider rate limit exceeded."""

    is_retryable = True

    def __init__(
        self,
        message: str,
        *,
        retry_after: float | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Initialize a rate limit error.

        Args:
            message: Human-readable error description.
            retry_after: Seconds to wait before retrying, if provided
                by the provider.
            context: Arbitrary metadata about the error.
        """
        if retry_after is not None and (
            retry_after < 0 or not math.isfinite(retry_after)
        ):
            msg = "retry_after must be a finite non-negative number"
            raise ValueError(msg)
        self.retry_after = retry_after
        super().__init__(message, context=context)


class ModelNotFoundError(ProviderError):
    """Requested model does not exist or is not available."""

    is_retryable = False


class InvalidRequestError(ProviderError):
    """Malformed request (bad parameters, too many tokens, etc.)."""

    is_retryable = False


class ContentFilterError(ProviderError):
    """Request or response blocked by the provider's content filter."""

    is_retryable = False


class ProviderTimeoutError(ProviderError):
    """Request timed out waiting for provider response."""

    is_retryable = True


class ProviderConnectionError(ProviderError):
    """Network-level failure connecting to the provider."""

    is_retryable = True


class ProviderInternalError(ProviderError):
    """Provider returned a server-side error (5xx)."""

    is_retryable = True


class DriverNotRegisteredError(ProviderError):
    """Requested provider driver is not registered in the registry."""

    is_retryable = False


class DriverAlreadyRegisteredError(ProviderError):
    """A driver with this name is already registered.

    Reserved for future use if the registry gains mutable operations
    (add/remove after construction).  Not currently raised.
    """

    is_retryable = False


class DriverFactoryNotFoundError(ProviderError):
    """No factory found for the requested driver type string."""

    is_retryable = False
