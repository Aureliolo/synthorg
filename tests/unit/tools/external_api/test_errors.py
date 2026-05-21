"""Tests for the external-access tool domain error hierarchy."""

import pytest

from synthorg.core.domain_errors import DomainError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode
from synthorg.tools.errors import ToolError
from synthorg.tools.external_api.errors import (
    ExternalApiApprovalMismatchError,
    ExternalApiArgumentError,
    ExternalApiConnectionNotFoundError,
    ExternalApiCredentialError,
    ExternalApiEgressBlockedError,
    ExternalApiError,
    ExternalApiRateLimitedError,
    ExternalApiResponseError,
)

_ALL_ERRORS = (
    ExternalApiError,
    ExternalApiArgumentError,
    ExternalApiConnectionNotFoundError,
    ExternalApiEgressBlockedError,
    ExternalApiCredentialError,
    ExternalApiApprovalMismatchError,
    ExternalApiRateLimitedError,
    ExternalApiResponseError,
)


@pytest.mark.unit
class TestExternalApiErrorHierarchy:
    @pytest.mark.parametrize("err_cls", _ALL_ERRORS)
    def test_subclasses_tool_error_and_domain_error(
        self, err_cls: type[ExternalApiError]
    ) -> None:
        assert issubclass(err_cls, ExternalApiError)
        assert issubclass(err_cls, ToolError)
        assert issubclass(err_cls, DomainError)

    @pytest.mark.parametrize(
        ("err_cls", "code", "category", "status"),
        [
            (
                ExternalApiArgumentError,
                ErrorCode.TOOL_PARAMETER_ERROR,
                ErrorCategory.VALIDATION,
                422,
            ),
            (
                ExternalApiConnectionNotFoundError,
                ErrorCode.CONNECTION_NOT_FOUND,
                ErrorCategory.NOT_FOUND,
                404,
            ),
            (
                ExternalApiEgressBlockedError,
                ErrorCode.FORBIDDEN,
                ErrorCategory.AUTH,
                403,
            ),
            (
                ExternalApiApprovalMismatchError,
                ErrorCode.RESOURCE_CONFLICT,
                ErrorCategory.CONFLICT,
                409,
            ),
            (
                ExternalApiRateLimitedError,
                ErrorCode.RATE_LIMITED,
                ErrorCategory.RATE_LIMIT,
                429,
            ),
        ],
    )
    def test_code_category_status(
        self,
        err_cls: type[ExternalApiError],
        code: ErrorCode,
        category: ErrorCategory,
        status: int,
    ) -> None:
        assert err_cls.error_code is code
        assert err_cls.error_category is category
        assert err_cls.status_code == status

    def test_raisable_with_context(self) -> None:
        err = ExternalApiEgressBlockedError(
            "blocked",
            context={"connection": "crm-api"},
        )
        assert err.context["connection"] == "crm-api"
        assert "blocked" in str(err)
