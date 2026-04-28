"""Tests for API error hierarchy and RFC 9457 error taxonomy."""

import pytest

from synthorg.api.errors import (
    ApiError,
    ApiValidationError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ServiceUnavailableError,
    SessionRevokedError,
    UnauthorizedError,
    VersionConflictError,
)
from synthorg.core.error_taxonomy import (
    CATEGORY_TITLES,
    CODE_CATEGORY_PREFIX,
    ErrorCategory,
    ErrorCode,
    category_title,
    category_type_uri,
)

_ERROR_DOCS_BASE = "https://synthorg.io/docs/errors"

pytestmark = pytest.mark.unit

_EXPECTED_CODES: dict[str, int] = {
    # 1xxx -- auth
    "UNAUTHORIZED": 1000,
    "FORBIDDEN": 1001,
    "SESSION_REVOKED": 1002,
    "ACCOUNT_LOCKED": 1003,
    "CSRF_REJECTED": 1004,
    "REFRESH_TOKEN_INVALID": 1005,
    "SESSION_LIMIT_EXCEEDED": 1006,
    "TOOL_PERMISSION_DENIED": 1007,
    # 2xxx -- validation
    "VALIDATION_ERROR": 2000,
    "REQUEST_VALIDATION_ERROR": 2001,
    "ARTIFACT_TOO_LARGE": 2002,
    "TOOL_PARAMETER_ERROR": 2003,
    # 3xxx -- not_found
    "RESOURCE_NOT_FOUND": 3000,
    "RECORD_NOT_FOUND": 3001,
    "ROUTE_NOT_FOUND": 3002,
    "PROJECT_NOT_FOUND": 3003,
    "TASK_NOT_FOUND": 3004,
    "SUBWORKFLOW_NOT_FOUND": 3005,
    "WORKFLOW_EXECUTION_NOT_FOUND": 3006,
    "CHANNEL_NOT_FOUND": 3007,
    "TOOL_NOT_FOUND": 3008,
    "ONTOLOGY_NOT_FOUND": 3009,
    "CONNECTION_NOT_FOUND": 3010,
    "MODEL_NOT_FOUND": 3011,
    "ESCALATION_NOT_FOUND": 3012,
    # 4xxx -- conflict
    "RESOURCE_CONFLICT": 4000,
    "DUPLICATE_RECORD": 4001,
    "VERSION_CONFLICT": 4002,
    "TASK_VERSION_CONFLICT": 4003,
    "ONTOLOGY_DUPLICATE": 4004,
    "CHANNEL_ALREADY_EXISTS": 4005,
    "ESCALATION_ALREADY_DECIDED": 4006,
    "MIXED_CURRENCY_AGGREGATION": 4007,
    # 5xxx -- rate_limit
    "RATE_LIMITED": 5000,
    "PER_OPERATION_RATE_LIMITED": 5001,
    "CONCURRENCY_LIMIT_EXCEEDED": 5002,
    # 6xxx -- budget_exhausted
    "BUDGET_EXHAUSTED": 6000,
    "DAILY_LIMIT_EXCEEDED": 6001,
    "RISK_BUDGET_EXHAUSTED": 6002,
    "PROJECT_BUDGET_EXHAUSTED": 6003,
    "QUOTA_EXHAUSTED": 6004,
    # 7xxx -- provider_error
    "PROVIDER_ERROR": 7000,
    "PROVIDER_TIMEOUT": 7001,
    "PROVIDER_CONNECTION": 7002,
    "PROVIDER_INTERNAL": 7003,
    "PROVIDER_AUTHENTICATION_FAILED": 7004,
    "PROVIDER_INVALID_REQUEST": 7005,
    "PROVIDER_CONTENT_FILTERED": 7006,
    "INTEGRATION_ERROR": 7007,
    "OAUTH_ERROR": 7008,
    "WEBHOOK_ERROR": 7009,
    # 8xxx -- internal
    "INTERNAL_ERROR": 8000,
    "SERVICE_UNAVAILABLE": 8001,
    "PERSISTENCE_ERROR": 8002,
    "ENGINE_ERROR": 8003,
    "ONTOLOGY_ERROR": 8004,
    "COMMUNICATION_ERROR": 8005,
    "TOOL_ERROR": 8006,
    "ARTIFACT_STORAGE_FULL": 8007,
    "TOOL_EXECUTION_ERROR": 8008,
}


class TestErrorCategory:
    """ErrorCategory enum completeness and values."""

    def test_has_all_eight_members(self) -> None:
        assert len(ErrorCategory) == 8

    @pytest.mark.parametrize(
        ("member", "value"),
        [
            (ErrorCategory.AUTH, "auth"),
            (ErrorCategory.VALIDATION, "validation"),
            (ErrorCategory.NOT_FOUND, "not_found"),
            (ErrorCategory.CONFLICT, "conflict"),
            (ErrorCategory.RATE_LIMIT, "rate_limit"),
            (ErrorCategory.BUDGET_EXHAUSTED, "budget_exhausted"),
            (ErrorCategory.PROVIDER_ERROR, "provider_error"),
            (ErrorCategory.INTERNAL, "internal"),
        ],
    )
    def test_member_values(self, member: ErrorCategory, value: str) -> None:
        assert member.value == value


class TestErrorCode:
    """ErrorCode enum completeness and values."""

    def test_has_all_codes(self) -> None:
        assert len(ErrorCode) == len(_EXPECTED_CODES)

    @pytest.mark.parametrize(
        ("name", "value"),
        list(_EXPECTED_CODES.items()),
    )
    def test_code_values(self, name: str, value: int) -> None:
        assert ErrorCode[name].value == value

    def test_no_duplicate_values(self) -> None:
        values = [c.value for c in ErrorCode]
        assert len(values) == len(set(values))

    def test_code_category_prefix_consistency(self) -> None:
        """First digit of each code matches CODE_CATEGORY_PREFIX."""
        for code in ErrorCode:
            prefix = code.value // 1000
            assert prefix in CODE_CATEGORY_PREFIX, (
                f"{code.name} has prefix {prefix} not in mapping"
            )


class TestCategoryMetadata:
    """CATEGORY_TITLES map and helper functions."""

    def test_category_titles_covers_all_members(self) -> None:
        """Every ErrorCategory member has a title entry."""
        for cat in ErrorCategory:
            assert cat in CATEGORY_TITLES, f"Missing title for {cat.name}"

    def test_category_titles_has_no_extra_keys(self) -> None:
        """No stale keys in CATEGORY_TITLES."""
        assert len(CATEGORY_TITLES) == len(ErrorCategory)

    @pytest.mark.parametrize(
        ("cat", "expected_title"),
        [
            (ErrorCategory.AUTH, "Authentication Error"),
            (ErrorCategory.VALIDATION, "Validation Error"),
            (ErrorCategory.NOT_FOUND, "Resource Not Found"),
            (ErrorCategory.CONFLICT, "Resource Conflict"),
            (ErrorCategory.RATE_LIMIT, "Rate Limit Exceeded"),
            (ErrorCategory.BUDGET_EXHAUSTED, "Budget Exhausted"),
            (ErrorCategory.PROVIDER_ERROR, "Provider Error"),
            (ErrorCategory.INTERNAL, "Internal Server Error"),
        ],
    )
    def test_category_title_values(
        self,
        cat: ErrorCategory,
        expected_title: str,
    ) -> None:
        assert category_title(cat) == expected_title

    @pytest.mark.parametrize("cat", list(ErrorCategory))
    def test_category_type_uri_format(self, cat: ErrorCategory) -> None:
        uri = category_type_uri(cat)
        assert uri == f"{_ERROR_DOCS_BASE}#{cat.value}"
        assert uri.startswith("https://")

    def test_category_type_uri_examples(self) -> None:
        assert (
            category_type_uri(ErrorCategory.AUTH)
            == "https://synthorg.io/docs/errors#auth"
        )
        assert (
            category_type_uri(ErrorCategory.INTERNAL)
            == "https://synthorg.io/docs/errors#internal"
        )


class TestApiErrorMetadata:
    """Class-level error metadata on ApiError hierarchy."""

    @pytest.mark.parametrize(
        ("cls", "category", "code", "retryable"),
        [
            (ApiError, ErrorCategory.INTERNAL, ErrorCode.INTERNAL_ERROR, False),
            (
                NotFoundError,
                ErrorCategory.NOT_FOUND,
                ErrorCode.RESOURCE_NOT_FOUND,
                False,
            ),
            (
                ApiValidationError,
                ErrorCategory.VALIDATION,
                ErrorCode.VALIDATION_ERROR,
                False,
            ),
            (
                ConflictError,
                ErrorCategory.CONFLICT,
                ErrorCode.RESOURCE_CONFLICT,
                False,
            ),
            (ForbiddenError, ErrorCategory.AUTH, ErrorCode.FORBIDDEN, False),
            (UnauthorizedError, ErrorCategory.AUTH, ErrorCode.UNAUTHORIZED, False),
            (
                ServiceUnavailableError,
                ErrorCategory.INTERNAL,
                ErrorCode.SERVICE_UNAVAILABLE,
                True,
            ),
            (
                VersionConflictError,
                ErrorCategory.CONFLICT,
                ErrorCode.VERSION_CONFLICT,
                False,
            ),
            (
                SessionRevokedError,
                ErrorCategory.AUTH,
                ErrorCode.SESSION_REVOKED,
                False,
            ),
        ],
    )
    def test_class_level_metadata(
        self,
        cls: type[ApiError],
        category: ErrorCategory,
        code: ErrorCode,
        retryable: bool,
    ) -> None:
        assert cls.error_category == category
        assert cls.error_code == code
        assert cls.retryable is retryable

    def test_service_unavailable_is_retryable(self) -> None:
        exc = ServiceUnavailableError()
        assert exc.retryable is True
        assert exc.status_code == 503

    def test_init_subclass_rejects_mismatched_category(self) -> None:
        """Subclass with code/category mismatch raises TypeError."""
        with pytest.raises(TypeError, match="implies category"):

            class _BadError(ApiError):
                error_category = ErrorCategory.AUTH
                error_code = ErrorCode.INTERNAL_ERROR  # 8xxx != auth


class TestResourceNotFoundFactory:
    """``resource_not_found`` builds NotFoundError with structured codes."""

    def test_default_code_is_generic(self) -> None:
        from synthorg.api.errors import resource_not_found

        err = resource_not_found("task", "abc-123")
        assert isinstance(err, NotFoundError)
        assert err.error_code == ErrorCode.RESOURCE_NOT_FOUND
        assert err.status_code == 404
        assert "task 'abc-123' not found" in str(err)

    def test_custom_not_found_code_preserved(self) -> None:
        from synthorg.api.errors import resource_not_found

        err = resource_not_found("task", "abc", code=ErrorCode.TASK_NOT_FOUND)
        assert err.error_code == ErrorCode.TASK_NOT_FOUND
        assert err.status_code == 404

    def test_rejects_non_not_found_code(self) -> None:
        """Factory refuses codes outside the 3xxx NOT_FOUND band.

        A 404 response carrying an AUTH or VALIDATION machine code
        would break client error-routing; the factory enforces the
        category invariant at the construction boundary.
        """
        from synthorg.api.errors import resource_not_found

        with pytest.raises(ValueError, match="NOT_FOUND"):
            resource_not_found(
                "task",
                "abc",
                code=ErrorCode.UNAUTHORIZED,  # 1000 -- auth, not NOT_FOUND
            )

    def test_rejects_validation_code(self) -> None:
        from synthorg.api.errors import resource_not_found

        with pytest.raises(ValueError, match="NOT_FOUND"):
            resource_not_found(
                "task",
                "abc",
                code=ErrorCode.VALIDATION_ERROR,  # 2000 -- validation
            )

    def test_every_not_found_code_in_taxonomy_is_accepted(self) -> None:
        """Smoke test: every declared 3xxx code round-trips the factory."""
        from synthorg.api.errors import resource_not_found

        for code in ErrorCode:
            if code.value // 1000 != 3:
                continue
            err = resource_not_found("thing", "id", code=code)
            assert err.error_code == code
