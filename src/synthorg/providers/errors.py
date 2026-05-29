"""Provider error hierarchy.

Every provider error carries a ``is_retryable`` flag so retry logic
can decide whether to attempt again without inspecting concrete
exception types.
"""

import copy
import math
from types import MappingProxyType
from typing import Any, ClassVar, Final, Literal, override

from synthorg.core.domain_errors import ConflictError, DomainError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode


class ProviderLifecycleConflictError(ConflictError):
    """Raised when ``ProviderHealthProber.start()`` is called after a timed-out stop.

    Mirrors :class:`BackupUnrestartableError` -- a stuck drain leaves
    the prober's loop alive on the original instance, so the canonical
    lifecycle pattern marks the prober unrestartable rather than
    layering a second loop on top of an orphan task.
    """

    default_message: ClassVar[str] = (
        "ProviderHealthProber is unrestartable after a timed-out stop"
    )


ProviderErrorLabel = Literal[
    "rate_limit",
    "timeout",
    "connection",
    "internal",
    "invalid_request",
    "auth",
    "content_filter",
    "not_found",
    "other",
]
"""Bounded Prometheus label value returned by :func:`classify_provider_error`.

Kept in lockstep with
:data:`synthorg.observability.prometheus_labels.VALID_PROVIDER_ERROR_CLASSES`
by the record helper; updating either requires updating both.
"""

_REDACTED_KEYS: frozenset[str] = frozenset(
    {"api_key", "token", "secret", "password", "authorization"},
)


def _is_sensitive_key(key: str) -> bool:
    """Check if a context key should be redacted (case-insensitive).

    Returns:
        ``True`` when *key* names a sensitive field that must be redacted.
    """
    return key.lower() in _REDACTED_KEYS


class ProviderError(DomainError):
    """Base exception for all provider-layer errors.

    Attributes:
        message: Human-readable error description.
        context: Immutable metadata about the error (provider, model, etc.).
        is_retryable: Whether the caller should retry the request.

    Class Attributes:
        status_code: HTTP 502 Bad Gateway (upstream failure).
        error_code: RFC 9457 error code; subclasses override.
        error_category: ``PROVIDER_ERROR``.
        retryable: Alias of ``is_retryable`` for the exception handler.
        default_message: Generic message safe for 5xx scrubbing.

    Note:
        When converted to string, sensitive context keys (api_key, token,
        secret, password, authorization) are automatically redacted
        regardless of casing.
    """

    is_retryable: bool = False
    retryable: ClassVar[bool] = False
    status_code: ClassVar[int] = 502
    error_code: ClassVar[ErrorCode] = ErrorCode.PROVIDER_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.PROVIDER_ERROR
    default_message: ClassVar[str] = "Provider error"

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
        # Deep-copy so nested lists/dicts in ``context`` cannot be
        # mutated by the caller after the exception is raised /
        # logged: ``MappingProxyType`` only freezes the outer mapping.
        self.context: MappingProxyType[str, Any] = MappingProxyType(
            copy.deepcopy(dict(context)) if context else {},
        )
        super().__init__(message)

    @override
    def __str__(self) -> str:
        """Format error with optional context metadata.

        Sensitive keys (api_key, token, etc.) are redacted to prevent
        accidental secret leakage in logs and tracebacks.

        Returns:
            ``"<message> (<key>=<val>, ...)"`` with sensitive context
            keys redacted, or just ``"<message>"`` when context is empty.
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
    status_code: ClassVar[int] = 502
    error_code: ClassVar[ErrorCode] = ErrorCode.PROVIDER_AUTHENTICATION_FAILED
    default_message: ClassVar[str] = "Provider authentication failed"


class RateLimitError(ProviderError):
    """Provider rate limit exceeded."""

    is_retryable = True
    retryable: ClassVar[bool] = True
    status_code: ClassVar[int] = 429
    error_code: ClassVar[ErrorCode] = ErrorCode.RATE_LIMITED
    error_category: ClassVar[ErrorCategory] = ErrorCategory.RATE_LIMIT
    default_message: ClassVar[str] = "Rate limit exceeded"

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

        Raises:
            ValueError: If ``retry_after`` is negative or non-finite.
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
    status_code: ClassVar[int] = 404
    error_code: ClassVar[ErrorCode] = ErrorCode.MODEL_NOT_FOUND
    error_category: ClassVar[ErrorCategory] = ErrorCategory.NOT_FOUND
    default_message: ClassVar[str] = "Model not found"


class InvalidRequestError(ProviderError):
    """Malformed request (bad parameters, too many tokens, etc.).

    The HTTP status stays at 422 (the provider rejected the request
    as invalid), but the RFC 9457 ``error_category`` is
    ``PROVIDER_ERROR`` to match the 7xxx ``error_code`` prefix --
    the underlying signal originates from the upstream provider, not
    from local boundary validation.  ``DomainError.__init_subclass__``
    enforces the prefix-vs-category alignment at class-definition
    time.
    """

    is_retryable = False
    status_code: ClassVar[int] = 422
    error_code: ClassVar[ErrorCode] = ErrorCode.PROVIDER_INVALID_REQUEST
    error_category: ClassVar[ErrorCategory] = ErrorCategory.PROVIDER_ERROR
    default_message: ClassVar[str] = "Invalid provider request"


class ContentFilterError(ProviderError):
    """Request or response blocked by the provider's content filter.

    Same prefix-vs-category alignment fix as :class:`InvalidRequestError`:
    the 7xxx ``error_code`` keeps its semantic ``PROVIDER_ERROR``
    category; HTTP status stays 422.
    """

    is_retryable = False
    status_code: ClassVar[int] = 422
    error_code: ClassVar[ErrorCode] = ErrorCode.PROVIDER_CONTENT_FILTERED
    error_category: ClassVar[ErrorCategory] = ErrorCategory.PROVIDER_ERROR
    default_message: ClassVar[str] = "Content filtered by provider"


class ProviderTimeoutError(ProviderError):
    """Request timed out waiting for provider response."""

    is_retryable = True
    retryable: ClassVar[bool] = True
    status_code: ClassVar[int] = 504
    error_code: ClassVar[ErrorCode] = ErrorCode.PROVIDER_TIMEOUT
    default_message: ClassVar[str] = "Provider request timed out"


class ProviderConnectionError(ProviderError):
    """Network-level failure connecting to the provider."""

    is_retryable = True
    retryable: ClassVar[bool] = True
    status_code: ClassVar[int] = 502
    error_code: ClassVar[ErrorCode] = ErrorCode.PROVIDER_CONNECTION
    default_message: ClassVar[str] = "Provider connection failed"


class ProviderInternalError(ProviderError):
    """Provider returned a server-side error (5xx)."""

    is_retryable = True
    retryable: ClassVar[bool] = True
    status_code: ClassVar[int] = 502
    error_code: ClassVar[ErrorCode] = ErrorCode.PROVIDER_INTERNAL
    default_message: ClassVar[str] = "Provider internal error"


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


class ProviderAlreadyExistsError(ProviderError):
    """A provider with this name already exists.

    409 Conflict: provider name uniqueness violation, not a 502
    upstream failure.  Override the parent's 502 default so the
    domain handler maps directly without a controller-level catch +
    re-raise as ``ConflictError``.
    """

    is_retryable = False
    status_code: ClassVar[int] = 409
    error_code: ClassVar[ErrorCode] = ErrorCode.RESOURCE_CONFLICT
    error_category: ClassVar[ErrorCategory] = ErrorCategory.CONFLICT


class ProviderNotFoundError(ProviderError):
    """A provider with this name does not exist.

    404 Not Found: provider does not exist locally, not a 502
    upstream failure.  Override the parent's 502 default so the
    domain handler maps directly.
    """

    is_retryable = False
    status_code: ClassVar[int] = 404
    error_code: ClassVar[ErrorCode] = ErrorCode.RESOURCE_NOT_FOUND
    error_category: ClassVar[ErrorCategory] = ErrorCategory.NOT_FOUND


class ProviderModelNotFoundError(ProviderError):
    """A model identifier does not exist on the provider.

    404 Not Found: distinct from ``ProviderNotFoundError`` (the whole
    provider is missing) and from ``ProviderValidationError`` (the
    request shape is wrong).  Lets the API controller route missing-
    model errors to HTTP 404 without parsing free-form validation
    text.
    """

    is_retryable = False
    status_code: ClassVar[int] = 404
    error_code: ClassVar[ErrorCode] = ErrorCode.RESOURCE_NOT_FOUND
    error_category: ClassVar[ErrorCategory] = ErrorCategory.NOT_FOUND


class ProviderValidationError(ProviderError):
    """Provider configuration failed validation.

    422 Unprocessable Entity: input shape is wrong, not a 502
    upstream failure.  Override the parent's 502 default so the
    domain handler maps directly.
    """

    is_retryable = False
    status_code: ClassVar[int] = 422
    error_code: ClassVar[ErrorCode] = ErrorCode.VALIDATION_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.VALIDATION


_ERROR_CLASS_MAP: Final[dict[type[BaseException], ProviderErrorLabel]] = {
    RateLimitError: "rate_limit",
    ProviderTimeoutError: "timeout",
    ProviderConnectionError: "connection",
    ProviderInternalError: "internal",
    InvalidRequestError: "invalid_request",
    AuthenticationError: "auth",
    ContentFilterError: "content_filter",
    ModelNotFoundError: "not_found",
}


def classify_provider_error(exc: BaseException) -> ProviderErrorLabel:
    """Classify *exc* into one of nine bounded Prometheus label values.

    Falls back to ``"other"`` for any exception not in the direct
    canonical map, which guarantees the label set in
    :data:`VALID_PROVIDER_ERROR_CLASSES` stays finite even as driver
    implementations add new error types.

    Uses a direct-type lookup first (cheapest), then falls back to
    ``isinstance`` for the hierarchy so subclasses of the canonical
    provider-error types are bucketed with their parents.  Any
    ``ProviderError`` subclass that is not in the direct map (e.g.
    ``DriverNotRegisteredError``, ``ProviderValidationError``) and
    unknown (non-``ProviderError``) exception types both resolve to
    ``"other"``; the Prometheus label set therefore stays bounded
    regardless of what the provider driver raises.

    Returns:
        One of the :data:`ProviderErrorLabel` literal values; the
        return type gives static guarantees to callers (e.g. the
        Prometheus collector's ``record_provider_error``) that only
        allowlisted labels flow through.
    """
    exc_type = type(exc)
    direct = _ERROR_CLASS_MAP.get(exc_type)
    if direct is not None:
        return direct
    for cls, label in _ERROR_CLASS_MAP.items():
        if isinstance(exc, cls):
            return label
    return "other"
