"""Cross-layer domain exceptions with RFC 9457 metadata.

Every error class here inherits from :class:`DomainError`, which carries
the ClassVars the API exception handler reads when building an RFC 9457
response (``status_code``, ``error_code``, ``error_category``,
``retryable``, ``default_message``).  ``__init_subclass__`` validates
that the first digit of ``error_code`` matches the declared
``error_category`` so a typo cannot ship a 404 carrying an auth code.

This module is dependency-free apart from
:mod:`synthorg.core.error_taxonomy`, keeping it usable from the CLI and
any future extension that wants to raise / catch SynthOrg errors without
pulling in :mod:`synthorg.api`.
"""

from typing import ClassVar, override

from synthorg.core.error_taxonomy import (
    CODE_CATEGORY_PREFIX,
    ErrorCategory,
    ErrorCode,
)


class DomainError(Exception):
    """Base for every RFC 9457-aware exception in the project.

    Class Attributes:
        default_message: 5xx-safe fallback used when no message is given
            and (for 5xx responses) when the handler scrubs internal
            detail before sending to the client.
        error_category: RFC 9457 high-level category.
        error_code: RFC 9457 4-digit machine-readable code; subclasses
            override.  ``__init_subclass__`` enforces that the first
            digit of this value matches ``error_category``.
        retryable: Whether the client should retry the request.
        status_code: HTTP status code for the API exception handler.
            Subclasses override; default 500 covers genuine internal
            errors that escape without explicit annotation.
    """

    default_message: ClassVar[str] = "Internal server error"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.INTERNAL_ERROR
    retryable: ClassVar[bool] = False
    status_code: ClassVar[int] = 500

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.default_message)

    @override
    def __init_subclass__(cls, **kwargs: object) -> None:
        """Validate that error_code's first digit matches error_category.

        Validation runs at class-definition time only.  Rebinding
        ``cls.error_code`` or ``cls.error_category`` post-definition
        (e.g. from a test fixture or dynamic patching) silently
        bypasses this check; treat the ClassVars as immutable in
        production code.

        Raises:
            TypeError: When the first digit of ``error_code`` does
                not match the declared ``error_category``.
        """
        super().__init_subclass__(**kwargs)
        prefix = cls.error_code.value // 1000
        expected = CODE_CATEGORY_PREFIX.get(prefix)
        if expected is not None and cls.error_category != expected:
            msg = (
                f"{cls.__name__}: error_code {cls.error_code.name} "
                f"(prefix {prefix}) implies category {expected.name}, "
                f"but error_category is {cls.error_category.name}"
            )
            raise TypeError(msg)


# ── Generic domain exceptions ─────────────────────────────────────


class NotFoundError(DomainError):
    """Raised when a requested resource does not exist (404).

    Subclasses below override ``error_code`` so API clients can
    discriminate which resource type was missing without parsing the
    message.  Choose the most specific subclass at the raise site;
    fall back to :class:`ResourceNotFoundError` only when no
    dedicated code exists for the resource yet.
    """

    default_message: ClassVar[str] = "Resource not found"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.NOT_FOUND
    error_code: ClassVar[ErrorCode] = ErrorCode.RESOURCE_NOT_FOUND
    status_code: ClassVar[int] = 404


class ResourceNotFoundError(NotFoundError):
    """Fallback 404 for resources without a dedicated typed subclass.

    Used as the default ``error_class`` for
    :func:`synthorg.api.responses.require_resource_or_404` and as the
    raise target for controllers whose resource type has no
    layer-specific NotFound subclass (e.g. cost forecasts, messages).
    Resources WITH a typed NotFound class should raise that class
    directly (``TaskNotFoundError`` from ``synthorg.engine.errors``,
    ``ConnectionNotFoundError`` from ``synthorg.integrations.errors``,
    ``WorkflowDefinitionNotFoundError`` from
    ``synthorg.engine.workflow.service``, etc.) so the wire
    ``error_code`` discriminates the missing resource.
    """

    error_code: ClassVar[ErrorCode] = ErrorCode.RESOURCE_NOT_FOUND


class MemoryEntryNotFoundError(NotFoundError):
    """Raised when an agent memory entry lookup fails (404)."""

    default_message: ClassVar[str] = "Memory entry not found"
    error_code: ClassVar[ErrorCode] = ErrorCode.MEMORY_ENTRY_NOT_FOUND


class AbTestNotFoundError(NotFoundError):
    """Raised when an A/B-test proposal record lookup fails (404)."""

    default_message: ClassVar[str] = "A/B test not found"
    error_code: ClassVar[ErrorCode] = ErrorCode.AB_TEST_NOT_FOUND


class ConflictError(DomainError):
    """Raised when a resource conflict occurs (409)."""

    default_message: ClassVar[str] = "Resource conflict"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.CONFLICT
    error_code: ClassVar[ErrorCode] = ErrorCode.RESOURCE_CONFLICT
    status_code: ClassVar[int] = 409


class ValidationError(DomainError):
    """Raised when request data fails validation (422)."""

    default_message: ClassVar[str] = "Validation error"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.VALIDATION
    error_code: ClassVar[ErrorCode] = ErrorCode.VALIDATION_ERROR
    status_code: ClassVar[int] = 422


class VersionConflictError(ConflictError):
    """Raised when an ETag/If-Match version check fails (409).

    Used for optimistic concurrency at the API boundary (settings ETags,
    workflow definition writes, etc.).  Distinct from
    :class:`synthorg.core.persistence_errors.VersionConflictError`,
    which signals an optimistic-concurrency race at the row level.
    """

    default_message: ClassVar[str] = "Version conflict"
    error_code: ClassVar[ErrorCode] = ErrorCode.VERSION_CONFLICT


class ForbiddenError(DomainError):
    """Raised when access is denied (403)."""

    default_message: ClassVar[str] = "Forbidden"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.AUTH
    error_code: ClassVar[ErrorCode] = ErrorCode.FORBIDDEN
    status_code: ClassVar[int] = 403


class UnauthorizedError(DomainError):
    """Raised when authentication is required or invalid (401)."""

    default_message: ClassVar[str] = "Authentication required"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.AUTH
    error_code: ClassVar[ErrorCode] = ErrorCode.UNAUTHORIZED
    status_code: ClassVar[int] = 401


class SessionRevokedError(UnauthorizedError):
    """Raised when a revoked session token is used (401).

    Distinct ``error_code`` (``SESSION_REVOKED``) lets clients render
    a "you were logged out" message instead of a generic auth failure.
    """

    default_message: ClassVar[str] = "Session has been revoked"
    error_code: ClassVar[ErrorCode] = ErrorCode.SESSION_REVOKED


class RefreshTokenInvalidError(UnauthorizedError):
    """Raised when a presented refresh token cannot rotate (401).

    Covers a missing / expired / replayed refresh token and a
    revoked session. Distinct ``error_code``
    (``REFRESH_TOKEN_INVALID``) lets the client distinguish "your
    refresh failed, log in again" from a generic 401.
    """

    default_message: ClassVar[str] = "Refresh token is invalid or expired"
    error_code: ClassVar[ErrorCode] = ErrorCode.REFRESH_TOKEN_INVALID


class AccountLockedError(DomainError):
    """Raised when login is blocked by account lockout (429).

    Uses HTTP 429 (Too Many Requests) with an optional ``Retry-After``
    header indicating when the lockout expires.

    Attributes:
        retry_after: Seconds the client should wait before retrying;
            clamped to non-negative.  Declared in the class body so
            external readers and IDE tooling see the field without
            needing to inspect ``__init__``.
    """

    default_message: ClassVar[str] = "Account temporarily locked"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.AUTH
    error_code: ClassVar[ErrorCode] = ErrorCode.ACCOUNT_LOCKED
    retryable: ClassVar[bool] = True
    status_code: ClassVar[int] = 429

    retry_after: int

    def __init__(
        self,
        message: str | None = None,
        *,
        retry_after: int = 0,
    ) -> None:
        super().__init__(message)
        self.retry_after = max(0, int(retry_after))


class ServiceUnavailableError(DomainError):
    """Raised when a required service is not configured or is down (503)."""

    default_message: ClassVar[str] = "Service unavailable"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.SERVICE_UNAVAILABLE
    retryable: ClassVar[bool] = True
    status_code: ClassVar[int] = 503


class FeatureNotImplementedError(DomainError):
    """Raised when a feature is not supported by the active backend (501).

    Distinct from ``ServiceUnavailableError`` (503): 503 means a known
    service is currently down or unconfigured, retry later might
    succeed.  501 means the active backend or deployment fundamentally
    does not implement the requested operation -- retrying without
    changing configuration will not help.
    """

    default_message: ClassVar[str] = "Feature not implemented"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.FEATURE_NOT_IMPLEMENTED
    status_code: ClassVar[int] = 501


class ArtifactRejectedTooLargeError(DomainError):
    """Raised when an artifact upload exceeds the configured size limit (413).

    Distinct from :class:`synthorg.core.persistence_errors.ArtifactTooLargeError`,
    which fires when a storage backend rejects an oversized object.
    """

    default_message: ClassVar[str] = "Artifact content is too large"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.VALIDATION
    error_code: ClassVar[ErrorCode] = ErrorCode.ARTIFACT_TOO_LARGE
    status_code: ClassVar[int] = 413


class ProviderTierCoverageInsufficientError(ValidationError):
    """Raised when configured providers expose no models during setup (422).

    The setup-wizard contract requires at least one configured provider
    that exposes at least one model before the company step can apply
    a template. Distinct ``error_code`` lets the dashboard route the
    operator back to the providers step instead of showing a generic
    Retry button (the action would always fail until a model is added).
    """

    default_message: ClassVar[str] = (
        "No configured provider exposes any models. "
        "Go back to the Providers step, add at least one model "
        "to a provider, then return here to apply the template."
    )
    error_code: ClassVar[ErrorCode] = ErrorCode.PROVIDER_TIER_COVERAGE_INSUFFICIENT


class AgentRuntimeNotConfiguredError(ConflictError):
    """Raised when work is submitted but no LLM provider is configured (409).

    The company is running in empty mode: with no provider, no agent can
    execute. Surfaced at the task-submission boundary (and, as
    defence-in-depth, at the worker-execute seam) so the operator gets a
    clear, actionable message instead of a task that silently never
    runs. Distinct ``error_code`` lets the dashboard route the operator
    to the setup / providers step.
    """

    default_message: ClassVar[str] = (
        "No LLM provider is configured. Add a provider in setup before "
        "submitting tasks; the company is running in empty mode."
    )
    error_code: ClassVar[ErrorCode] = ErrorCode.AGENT_RUNTIME_NOT_CONFIGURED


class ArtifactStorageRejectedFullError(DomainError):
    """Raised when the artifact-storage subsystem reports it is full (507).

    Distinct from
    :class:`synthorg.core.persistence_errors.ArtifactStorageFullError`,
    which is the persistence-layer signal; this is the API-boundary
    rejection raised when the controller observes the same condition.
    """

    default_message: ClassVar[str] = "Artifact storage is full"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.ARTIFACT_STORAGE_FULL
    status_code: ClassVar[int] = 507


class ArtifactPersistenceNoStorageError(DomainError):
    """Raised when an artifact-content delete is requested without a backend.

    Surfaces a controller-helper bug: ``ArtifactService`` was constructed
    without a ``storage`` dependency, so ``delete_with_content`` cannot
    run.  Distinct ``error_code`` so dashboards can identify the
    misconfiguration.
    """

    default_message: ClassVar[str] = "Artifact service is missing a storage backend"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.ARTIFACT_NO_STORAGE_BACKEND
    status_code: ClassVar[int] = 500


class PerOperationRateLimitError(DomainError):
    """Raised when a per-operation rate limit is exceeded (429).

    Produced by :func:`synthorg.api.rate_limits.guard.per_op_rate_limit`
    guards.  The exception handler emits ``Retry-After`` when
    ``retry_after`` is set.

    Attributes:
        retry_after: Seconds the client should wait before retrying;
            always at least 1 to keep clients from hot-looping on a
            sub-second window.
    """

    default_message: ClassVar[str] = "Rate limit exceeded"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.RATE_LIMIT
    error_code: ClassVar[ErrorCode] = ErrorCode.PER_OPERATION_RATE_LIMITED
    retryable: ClassVar[bool] = True
    status_code: ClassVar[int] = 429

    retry_after: int

    def __init__(
        self,
        message: str | None = None,
        *,
        retry_after: int = 1,
    ) -> None:
        super().__init__(message)
        self.retry_after = max(1, int(retry_after))


class ImmutableFieldMismatchError(ValidationError):
    """Raised when a restore/rollback would change an immutable field (422).

    Distinct ``error_code`` lets the dashboard tell "the snapshot is
    incompatible because id/name/department differ" apart from a generic
    validation failure, so it can surface the specific blocked fields
    instead of a retry button that would always fail.
    """

    default_message: ClassVar[str] = "Cannot apply: immutable field mismatch"
    error_code: ClassVar[ErrorCode] = ErrorCode.IMMUTABLE_FIELD_MISMATCH


class AgentIdentityRollbackError(DomainError):
    """Raised when an agent-identity rollback fails unexpectedly (500).

    Distinct from :class:`ImmutableFieldMismatchError` (422, operator
    error): this is an unexpected server fault during the rollback
    write, not a rejected request.
    """

    default_message: ClassVar[str] = "Rollback failed due to an unexpected server error"
    error_code: ClassVar[ErrorCode] = ErrorCode.AGENT_IDENTITY_ROLLBACK_FAILED


class CheckpointOperationConflictError(ConflictError):
    """Raised when a fine-tune checkpoint deploy/delete conflicts (409).

    Distinct ``error_code`` separates "checkpoint operation rejected by
    its current state" (e.g. deleting the active checkpoint) from a
    generic resource conflict so clients can message it precisely.
    """

    default_message: ClassVar[str] = "Checkpoint operation conflict"
    error_code: ClassVar[ErrorCode] = ErrorCode.CHECKPOINT_OPERATION_CONFLICT


class FineTuneRunActiveError(ConflictError):
    """Raised when a fine-tune run is already active (409).

    Start/resume is rejected because the single-run invariant holds.
    Distinct ``error_code`` lets clients render "a run is already in
    progress" instead of a generic conflict.
    """

    default_message: ClassVar[str] = "A fine-tuning run is already active"
    error_code: ClassVar[ErrorCode] = ErrorCode.FINE_TUNE_RUN_ACTIVE


class TrainingPlanNotModifiableError(ConflictError):
    """Raised when a training plan is edited after execution/failure (409).

    Distinct ``error_code`` tells the dashboard the plan is frozen by
    its lifecycle status rather than a transient conflict, so it hides
    the edit form instead of offering a retry.
    """

    default_message: ClassVar[str] = "Cannot modify plan after execution or failure"
    error_code: ClassVar[ErrorCode] = ErrorCode.TRAINING_PLAN_NOT_MODIFIABLE


class ConcurrencyLimitExceededError(PerOperationRateLimitError):
    """Raised when a per-operation concurrency (inflight) cap is hit (429).

    Produced by ``PerOpConcurrencyMiddleware`` when a user already has
    ``max_inflight`` requests running for the guarded operation.
    Inherits from :class:`PerOperationRateLimitError` so the existing
    429 / ``Retry-After`` / RFC 9457 handling applies unchanged; a
    distinct ``error_code`` lets clients discriminate concurrency
    denials ("you already have one running") from window denials
    ("try again after the bucket refills").
    """

    default_message: ClassVar[str] = "Concurrency limit exceeded"
    error_code: ClassVar[ErrorCode] = ErrorCode.CONCURRENCY_LIMIT_EXCEEDED
