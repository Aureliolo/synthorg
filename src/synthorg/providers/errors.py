"""Provider error hierarchy.

Every provider error carries a ``is_retryable`` flag so retry logic
can decide whether to attempt again without inspecting concrete
exception types.
"""

import copy
import math
from types import MappingProxyType
from typing import ClassVar, Final, Literal, override

from pydantic import JsonValue

from synthorg.core.domain_errors import ConflictError, DomainError, NotFoundError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode


class UpgradeRecommendationNotFoundError(NotFoundError):
    """Raised when an upgrade-recommendation lookup fails (404)."""

    default_message: ClassVar[str] = "Upgrade recommendation not found"
    error_code: ClassVar[ErrorCode] = ErrorCode.UPGRADE_RECOMMENDATION_NOT_FOUND


class UpgradeRecommendationAlreadyDecidedError(ConflictError):
    """Raised when approving/rejecting an already-decided recommendation (409)."""

    default_message: ClassVar[str] = "Upgrade recommendation already decided"
    error_code: ClassVar[ErrorCode] = ErrorCode.UPGRADE_RECOMMENDATION_ALREADY_DECIDED


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
    "quota_exceeded",
    "payment_required",
    "timeout",
    "connection",
    "internal",
    "overloaded",
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
        context: dict[str, JsonValue] | None = None,
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
        self.context: MappingProxyType[str, JsonValue] = MappingProxyType(
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
        context: dict[str, JsonValue] | None = None,
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


class ProviderQuotaExceededError(RateLimitError):
    """Plan usage quota/allowance exhausted -- NOT a transient rate limit.

    ollama cloud bills a flat plan with session/weekly usage limits and exposes
    no API to pre-check remaining quota (tracking: ollama/ollama#12532; the
    quota-API requests ollama/ollama#15663 and #16448 are also open). We cannot
    avoid the block ahead of time, so the caller runs until ollama returns it
    and it surfaces here. Marked non-retryable: retrying a depleted allowance
    cannot recover it within the window. ``RATE_LIMITED`` is a generic
    per-category fallback in the error-code-uniqueness gate's ``SHAREABLE_CODES``
    list, so inheriting it from ``RateLimitError`` is allowed; clients branch on
    ``is_retryable`` (not ``error_code``) to tell a depleted quota from a
    transient rate limit.
    """

    is_retryable = False
    retryable: ClassVar[bool] = False
    default_message: ClassVar[str] = "Provider usage quota exceeded"


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


class ProviderImageGenerationUnsupportedError(InvalidRequestError):
    """Raised when a provider or model cannot generate images.

    Inherits :class:`InvalidRequestError` (an inheritance alias for the
    error-code-uniqueness gate): asking an image-incapable model to
    generate an image is a bad request, non-retryable, 422.
    """

    default_message: ClassVar[str] = "Provider does not support image generation"


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


class ProviderOverloadedError(ProviderInternalError):
    """The model is queueing rather than broken (upstream 503).

    Collapsing this into the generic 5xx bucket loses the one distinction an
    operator can act on: a model returning 503 on most calls while its
    siblings on the same account answer in under two seconds is overloaded,
    not down, and the fix is to stop sending it work rather than to
    investigate an outage. Inherits :class:`ProviderInternalError` (an
    inheritance alias for the error-code-uniqueness gate) so a client still
    branches on one wire code for "upstream server-side problem"; the
    distinction lives in the serviceability label.
    """

    status_code: ClassVar[int] = 503
    default_message: ClassVar[str] = "Provider model is overloaded"


class ProviderPaymentRequiredError(ProviderError):
    """Billing must be topped up before this model will serve (upstream 402).

    Distinct from :class:`ProviderQuotaExceededError`, which is a plan
    allowance that resets on its own schedule. An empty extra-usage balance
    resets when an operator pays, so it is non-retryable in the strongest
    sense: the condition cannot clear while the process waits, and every
    retry spends the full ladder to rediscover the same answer.
    """

    is_retryable = False
    status_code: ClassVar[int] = 402
    error_code: ClassVar[ErrorCode] = ErrorCode.PROVIDER_PAYMENT_REQUIRED
    default_message: ClassVar[str] = "Provider requires payment to serve this model"


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


class ProviderModelNotFoundError(ModelNotFoundError):
    """A model identifier does not exist on the provider's stored config.

    The management-service raise site for "model not found", distinct
    from ``ProviderNotFoundError`` (the whole provider is missing) and
    ``ProviderValidationError`` (the request shape is wrong).

    Inherits :class:`ModelNotFoundError` so both the management path
    (this class) and the driver upstream-404 path (the parent) carry the
    single ``MODEL_NOT_FOUND`` wire code: clients branch on one code
    regardless of raise site, and the inheritance makes the alias
    explicit for the error-code-uniqueness gate.
    """


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


class ProviderConfigUnreadableError(ProviderValidationError):
    """The persisted provider config could not be read at all.

    Raised instead of returning an empty provider set, because the two
    say opposite things and only one of them is actionable. A deployment
    with no providers configured is a deployment waiting to be set up; a
    deployment whose providers cannot be read has a configuration the
    operator can see in the dashboard and a system behaving as though it
    were absent. Reporting the second as the first is what let a single
    unreadable entry present as a first-run empty company.

    Narrows :class:`ProviderValidationError` rather than carrying its own
    wire code: to anything outside, this is a provider configuration that
    failed validation, and the distinction that matters is at the raise
    site.
    """

    default_message: ClassVar[str] = "Persisted provider configuration is unreadable"


class ProviderSerializationError(ProviderError):
    """Serialising the provider config blob for persistence failed.

    500 Internal: a serialise failure is an internal defect (the config
    validated but could not be turned into a storable JSON value), not a
    client input error nor a 502 upstream failure.  Kept distinct from
    :class:`ProviderPersistenceError` so the failure stage is
    unambiguous in logs and to callers.
    """

    is_retryable = False
    status_code: ClassVar[int] = 500
    error_code: ClassVar[ErrorCode] = ErrorCode.INTERNAL_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    default_message: ClassVar[str] = "Failed to serialise provider configuration"


class ProviderPersistenceError(ProviderError):
    """Persisting the provider config blob (or its hot-reload) failed.

    500 Internal: a DB-write or in-memory hot-reload failure.  On a
    hot-reload failure the prior persisted blob is rolled back first so
    the database and the running registry never diverge.
    """

    is_retryable = False
    status_code: ClassVar[int] = 500
    error_code: ClassVar[ErrorCode] = ErrorCode.PERSISTENCE_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    default_message: ClassVar[str] = "Failed to persist provider configuration"


# Insertion order is load-bearing: the ``isinstance`` fallback below walks this
# mapping in order, so every subclass must precede the parent it narrows, or a
# subclass instance is bucketed with the parent and the distinction is lost.
_ERROR_CLASS_MAP: Final[dict[type[BaseException], ProviderErrorLabel]] = {
    # Before the RateLimitError fallback so a depleted plan quota
    # (non-retryable) is countable apart from a transient throttle.
    ProviderQuotaExceededError: "quota_exceeded",
    RateLimitError: "rate_limit",
    ProviderPaymentRequiredError: "payment_required",
    ProviderTimeoutError: "timeout",
    ProviderConnectionError: "connection",
    # Before ProviderInternalError, which it narrows: a queueing model and a
    # broken endpoint are the same 5xx family and different operator actions.
    ProviderOverloadedError: "overloaded",
    ProviderInternalError: "internal",
    InvalidRequestError: "invalid_request",
    AuthenticationError: "auth",
    ContentFilterError: "content_filter",
    ModelNotFoundError: "not_found",
}

#: Bound on how far :func:`classify_provider_error` will unwrap a nested
#: retry wrapper. One level is the shape the retry handler produces; the
#: bound exists so a pathological chain cannot spin.
_MAX_CAUSE_UNWRAP: Final[int] = 4


def classify_provider_error(exc: BaseException) -> ProviderErrorLabel:
    """Classify *exc* into one of the bounded label values.

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
    ``"other"``; the label set therefore stays bounded regardless of what
    the provider driver raises.

    A retry wrapper is classified by the error it wrapped. Every call the
    retry handler gives up on arrives here wearing ``RetryExhaustedError``,
    which is in no bucket, so classifying the wrapper by its own type would
    file the entire retried population under ``"other"`` -- the traffic an
    operator most needs classified.

    Returns:
        One of the :data:`ProviderErrorLabel` literal values; the
        return type gives static guarantees to callers (e.g. the
        Prometheus collector's ``record_provider_error``) that only
        allowlisted labels flow through.
    """
    for candidate in _unwrap_causes(exc):
        direct = _ERROR_CLASS_MAP.get(type(candidate))
        if direct is not None:
            return direct
        for cls, label in _ERROR_CLASS_MAP.items():
            if isinstance(candidate, cls):
                return label
    return "other"


def _unwrap_causes(exc: BaseException) -> list[BaseException]:
    """Return *exc* followed by the retry causes it wraps, outermost first.

    ``RetryExhaustedError`` lives in :mod:`synthorg.providers.resilience.errors`,
    which imports this module, so the type is resolved at call time rather
    than at import.

    Returns:
        The exception and each wrapped cause, bounded by
        :data:`_MAX_CAUSE_UNWRAP`.
    """
    from .resilience.errors import RetryExhaustedError  # noqa: PLC0415

    chain: list[BaseException] = [exc]
    current = exc
    for _ in range(_MAX_CAUSE_UNWRAP):
        if not isinstance(current, RetryExhaustedError):
            break
        current = current.original_error
        chain.append(current)
    return chain
