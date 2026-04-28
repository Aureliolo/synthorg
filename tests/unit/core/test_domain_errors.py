"""Tests for ``synthorg.core.domain_errors``."""

import pytest

from synthorg.core.domain_errors import (
    AccountLockedError,
    ArtifactRejectedTooLargeError,
    ArtifactStorageRejectedFullError,
    ConcurrencyLimitExceededError,
    ConflictError,
    DomainError,
    ForbiddenError,
    NotFoundError,
    PerOperationRateLimitError,
    ServiceUnavailableError,
    SessionRevokedError,
    UnauthorizedError,
    ValidationError,
    VersionConflictError,
    resource_not_found,
)
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode

pytestmark = pytest.mark.unit


class TestDomainErrorBase:
    """``DomainError`` carries the canonical ClassVars and validates subclasses."""

    def test_default_classvars(self) -> None:
        assert DomainError.default_message == "Internal server error"
        assert DomainError.error_category == ErrorCategory.INTERNAL
        assert DomainError.error_code == ErrorCode.INTERNAL_ERROR
        assert DomainError.retryable is False
        assert DomainError.status_code == 500

    def test_init_uses_default_message_when_unspecified(self) -> None:
        exc = DomainError()
        assert str(exc) == "Internal server error"

    def test_init_uses_supplied_message(self) -> None:
        exc = DomainError("custom")
        assert str(exc) == "custom"

    def test_init_subclass_rejects_mismatched_category(self) -> None:
        """error_code prefix must match error_category."""
        with pytest.raises(TypeError, match="implies category"):

            class _BadError(DomainError):
                error_category = ErrorCategory.AUTH
                error_code = ErrorCode.INTERNAL_ERROR  # 8xxx != auth

    @pytest.mark.parametrize(
        ("category", "code"),
        [
            # Each pair is wrong: category does not match the first
            # digit of error_code.value.  The validator must fire for
            # every cross-category mismatch, not just one.
            (ErrorCategory.AUTH, ErrorCode.VALIDATION_ERROR),  # 1 vs 2
            (ErrorCategory.VALIDATION, ErrorCode.RESOURCE_NOT_FOUND),  # 2 vs 3
            (ErrorCategory.NOT_FOUND, ErrorCode.RESOURCE_CONFLICT),  # 3 vs 4
            (ErrorCategory.CONFLICT, ErrorCode.RATE_LIMITED),  # 4 vs 5
            (ErrorCategory.RATE_LIMIT, ErrorCode.BUDGET_EXHAUSTED),  # 5 vs 6
            (ErrorCategory.BUDGET_EXHAUSTED, ErrorCode.PROVIDER_ERROR),  # 6 vs 7
            (ErrorCategory.PROVIDER_ERROR, ErrorCode.INTERNAL_ERROR),  # 7 vs 8
            (ErrorCategory.INTERNAL, ErrorCode.UNAUTHORIZED),  # 8 vs 1
        ],
    )
    def test_init_subclass_rejects_every_cross_category_mismatch(
        self,
        category: ErrorCategory,
        code: ErrorCode,
    ) -> None:
        """The validator fires for every cross-category mismatch, not just one.

        ``__init_subclass__`` runs once at class-body completion, so the
        broken subclass is constructed via the ``type(name, bases, dict)``
        metaclass call -- equivalent to a class statement, but a single
        expression so it fits inside ``pytest.raises``.
        """
        with pytest.raises(TypeError, match="implies category"):
            type(
                "_BadError",
                (DomainError,),
                {"error_category": category, "error_code": code},
            )

    @pytest.mark.parametrize(
        ("category", "code"),
        [
            (ErrorCategory.AUTH, ErrorCode.UNAUTHORIZED),
            (ErrorCategory.VALIDATION, ErrorCode.VALIDATION_ERROR),
            (ErrorCategory.NOT_FOUND, ErrorCode.RESOURCE_NOT_FOUND),
            (ErrorCategory.CONFLICT, ErrorCode.RESOURCE_CONFLICT),
            (ErrorCategory.RATE_LIMIT, ErrorCode.RATE_LIMITED),
            (ErrorCategory.BUDGET_EXHAUSTED, ErrorCode.BUDGET_EXHAUSTED),
            (ErrorCategory.PROVIDER_ERROR, ErrorCode.PROVIDER_ERROR),
            (ErrorCategory.INTERNAL, ErrorCode.INTERNAL_ERROR),
        ],
    )
    def test_init_subclass_accepts_matched_pair(
        self,
        category: ErrorCategory,
        code: ErrorCode,
    ) -> None:
        """Each prefix-matching pair successfully creates a subclass."""

        class _OkError(DomainError):
            pass

        _OkError.error_category = category
        _OkError.error_code = code
        # If the trigger fired, the assertion above would have raised.
        assert _OkError.error_category == category


class TestConcreteClassMetadata:
    """Class-level ClassVars on every concrete subclass."""

    @pytest.mark.parametrize(
        ("cls", "category", "code", "status_code", "retryable"),
        [
            (
                NotFoundError,
                ErrorCategory.NOT_FOUND,
                ErrorCode.RESOURCE_NOT_FOUND,
                404,
                False,
            ),
            (
                ConflictError,
                ErrorCategory.CONFLICT,
                ErrorCode.RESOURCE_CONFLICT,
                409,
                False,
            ),
            (
                ValidationError,
                ErrorCategory.VALIDATION,
                ErrorCode.VALIDATION_ERROR,
                422,
                False,
            ),
            (
                VersionConflictError,
                ErrorCategory.CONFLICT,
                ErrorCode.VERSION_CONFLICT,
                409,
                False,
            ),
            (ForbiddenError, ErrorCategory.AUTH, ErrorCode.FORBIDDEN, 403, False),
            (
                UnauthorizedError,
                ErrorCategory.AUTH,
                ErrorCode.UNAUTHORIZED,
                401,
                False,
            ),
            (
                SessionRevokedError,
                ErrorCategory.AUTH,
                ErrorCode.SESSION_REVOKED,
                401,
                False,
            ),
            (
                AccountLockedError,
                ErrorCategory.AUTH,
                ErrorCode.ACCOUNT_LOCKED,
                429,
                True,
            ),
            (
                ServiceUnavailableError,
                ErrorCategory.INTERNAL,
                ErrorCode.SERVICE_UNAVAILABLE,
                503,
                True,
            ),
            (
                ArtifactRejectedTooLargeError,
                ErrorCategory.VALIDATION,
                ErrorCode.ARTIFACT_TOO_LARGE,
                413,
                False,
            ),
            (
                ArtifactStorageRejectedFullError,
                ErrorCategory.INTERNAL,
                ErrorCode.ARTIFACT_STORAGE_FULL,
                507,
                False,
            ),
            (
                PerOperationRateLimitError,
                ErrorCategory.RATE_LIMIT,
                ErrorCode.PER_OPERATION_RATE_LIMITED,
                429,
                True,
            ),
            (
                ConcurrencyLimitExceededError,
                ErrorCategory.RATE_LIMIT,
                ErrorCode.CONCURRENCY_LIMIT_EXCEEDED,
                429,
                True,
            ),
        ],
    )
    def test_metadata(
        self,
        cls: type[DomainError],
        category: ErrorCategory,
        code: ErrorCode,
        status_code: int,
        retryable: bool,
    ) -> None:
        assert cls.error_category == category
        assert cls.error_code == code
        assert cls.status_code == status_code
        assert cls.retryable is retryable

    def test_session_revoked_inherits_unauthorized(self) -> None:
        """``SessionRevokedError`` is catchable via ``UnauthorizedError``."""
        assert issubclass(SessionRevokedError, UnauthorizedError)
        exc = SessionRevokedError()
        assert isinstance(exc, UnauthorizedError)

    def test_version_conflict_inherits_conflict(self) -> None:
        assert issubclass(VersionConflictError, ConflictError)

    def test_concurrency_inherits_per_op_rate_limit(self) -> None:
        assert issubclass(ConcurrencyLimitExceededError, PerOperationRateLimitError)

    def test_every_concrete_class_inherits_domain_error(self) -> None:
        for cls in (
            NotFoundError,
            ConflictError,
            ValidationError,
            VersionConflictError,
            ForbiddenError,
            UnauthorizedError,
            SessionRevokedError,
            AccountLockedError,
            ServiceUnavailableError,
            ArtifactRejectedTooLargeError,
            ArtifactStorageRejectedFullError,
            PerOperationRateLimitError,
            ConcurrencyLimitExceededError,
        ):
            assert issubclass(cls, DomainError), cls


class TestRetryAfterCarriers:
    """``AccountLockedError`` / ``PerOperationRateLimitError`` instance attrs."""

    def test_account_locked_default_retry_after(self) -> None:
        exc = AccountLockedError()
        assert exc.retry_after == 0

    def test_account_locked_explicit_retry_after(self) -> None:
        exc = AccountLockedError(retry_after=120)
        assert exc.retry_after == 120

    def test_account_locked_negative_clamped_to_zero(self) -> None:
        exc = AccountLockedError(retry_after=-5)
        assert exc.retry_after == 0

    def test_per_op_rate_limit_default_retry_after(self) -> None:
        exc = PerOperationRateLimitError()
        assert exc.retry_after == 1

    def test_per_op_rate_limit_explicit_retry_after(self) -> None:
        exc = PerOperationRateLimitError(retry_after=30)
        assert exc.retry_after == 30

    def test_per_op_rate_limit_zero_clamped_to_one(self) -> None:
        """Hot-loop guard: ``retry_after`` must be at least 1."""
        exc = PerOperationRateLimitError(retry_after=0)
        assert exc.retry_after == 1

    def test_per_op_rate_limit_negative_clamped_to_one(self) -> None:
        """``PerOperationRateLimitError`` floor (1) differs from
        ``AccountLockedError`` floor (0); pin the asymmetry explicitly."""
        exc = PerOperationRateLimitError(retry_after=-10)
        assert exc.retry_after == 1

    def test_concurrency_inherits_retry_after(self) -> None:
        exc = ConcurrencyLimitExceededError(retry_after=5)
        assert exc.retry_after == 5


class TestResourceNotFoundFactory:
    """``resource_not_found`` builds NotFoundError with structured codes."""

    def test_default_code_is_generic(self) -> None:
        err = resource_not_found("task", "abc-123")
        assert isinstance(err, NotFoundError)
        assert err.error_code == ErrorCode.RESOURCE_NOT_FOUND
        assert err.status_code == 404
        assert "task 'abc-123' not found" in str(err)

    def test_custom_not_found_code_preserved(self) -> None:
        err = resource_not_found("task", "abc", code=ErrorCode.TASK_NOT_FOUND)
        assert err.error_code == ErrorCode.TASK_NOT_FOUND
        assert err.status_code == 404

    def test_rejects_non_not_found_code(self) -> None:
        """Factory refuses codes outside the 3xxx NOT_FOUND band."""
        with pytest.raises(ValueError, match="NOT_FOUND"):
            resource_not_found(
                "task",
                "abc",
                code=ErrorCode.UNAUTHORIZED,  # 1000 -- auth, not NOT_FOUND
            )

    def test_rejects_validation_code(self) -> None:
        with pytest.raises(ValueError, match="NOT_FOUND"):
            resource_not_found(
                "task",
                "abc",
                code=ErrorCode.VALIDATION_ERROR,  # 2000 -- validation
            )

    def test_every_not_found_code_in_taxonomy_is_accepted(self) -> None:
        """Smoke test: every declared 3xxx code round-trips the factory."""
        for code in ErrorCode:
            if code.value // 1000 != 3:
                continue
            err = resource_not_found("thing", "id", code=code)
            assert err.error_code == code

    def test_factory_does_not_mutate_class_level_classvar(self) -> None:
        """``resource_not_found`` shadows ``error_code`` on the instance only.

        Regression guard: the factory assigns ``error.error_code = code`` as
        an instance attribute (per the documented carve-out for the
        otherwise-immutable ClassVar).  This must not leak to the class
        and corrupt subsequent constructions.
        """
        before = NotFoundError.error_code
        instance = resource_not_found("task", "abc", code=ErrorCode.TASK_NOT_FOUND)
        assert instance.error_code == ErrorCode.TASK_NOT_FOUND
        assert NotFoundError.error_code == before
        # Fresh construction without the factory still sees the class default.
        plain = NotFoundError()
        assert plain.error_code == before


class TestInstanceConstruction:
    """Subclass instances pick up correct status/message."""

    def test_not_found_uses_default_message(self) -> None:
        exc = NotFoundError()
        assert str(exc) == "Resource not found"
        assert exc.status_code == 404

    def test_not_found_uses_supplied_message(self) -> None:
        exc = NotFoundError("project 'abc' not found")
        assert str(exc) == "project 'abc' not found"

    def test_conflict_default_status(self) -> None:
        exc = ConflictError()
        assert exc.status_code == 409

    def test_validation_default_status(self) -> None:
        exc = ValidationError()
        assert exc.status_code == 422

    def test_service_unavailable_is_retryable(self) -> None:
        exc = ServiceUnavailableError()
        assert exc.retryable is True
        assert exc.status_code == 503
