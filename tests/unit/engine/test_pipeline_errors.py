"""Unit tests for work pipeline domain errors."""

import pytest

from synthorg.core.domain_errors import DomainError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode
from synthorg.engine.pipeline.errors import (
    WorkIntakeRejectedError,
    WorkPipelineError,
    WorkPipelineTeamPathUnavailableError,
    WorkRoutingUndecidableError,
)

pytestmark = pytest.mark.unit


class TestWorkPipelineErrors:
    def test_all_inherit_domain_error(self) -> None:
        for exc in (
            WorkPipelineError,
            WorkIntakeRejectedError,
            WorkRoutingUndecidableError,
            WorkPipelineTeamPathUnavailableError,
        ):
            assert issubclass(exc, WorkPipelineError)
            assert issubclass(exc, DomainError)

    @pytest.mark.parametrize(
        ("exc", "status", "category", "code"),
        [
            (
                WorkIntakeRejectedError,
                422,
                ErrorCategory.VALIDATION,
                ErrorCode.VALIDATION_ERROR,
            ),
            (
                WorkRoutingUndecidableError,
                500,
                ErrorCategory.INTERNAL,
                ErrorCode.INTERNAL_ERROR,
            ),
            (
                WorkPipelineTeamPathUnavailableError,
                503,
                ErrorCategory.INTERNAL,
                ErrorCode.SERVICE_UNAVAILABLE,
            ),
        ],
    )
    def test_rfc9457_metadata(
        self,
        exc: type[WorkPipelineError],
        status: int,
        category: ErrorCategory,
        code: ErrorCode,
    ) -> None:
        assert exc.status_code == status
        assert exc.error_category is category
        assert exc.error_code is code

    def test_team_path_unavailable_is_retryable(self) -> None:
        assert WorkPipelineTeamPathUnavailableError.retryable is True

    def test_message_override_preserved(self) -> None:
        err = WorkIntakeRejectedError("requirements too vague")
        assert str(err) == "requirements too vague"
