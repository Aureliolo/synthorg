"""Unit tests for the red-team error hierarchy."""

import pytest

from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode
from synthorg.engine.errors import EngineError
from synthorg.security.redteam.errors import (
    RedTeamDispatchError,
    RedTeamError,
    RedTeamReportAlreadyExistsError,
    RedTeamReportNotFoundError,
    RedTeamReportValidationError,
)


@pytest.mark.unit
class TestRedTeamErrorHierarchy:
    """All concrete errors descend from RedTeamError and EngineError."""

    @pytest.mark.parametrize(
        "subclass",
        [
            RedTeamReportNotFoundError,
            RedTeamReportValidationError,
            RedTeamDispatchError,
            RedTeamReportAlreadyExistsError,
        ],
    )
    def test_descends_from_red_team_error(self, subclass: type) -> None:
        assert issubclass(subclass, RedTeamError)

    def test_red_team_error_descends_from_engine_error(self) -> None:
        assert issubclass(RedTeamError, EngineError)


@pytest.mark.unit
class TestRedTeamReportNotFoundError:
    """500 INTERNAL with execution_id attribute."""

    def test_attributes(self) -> None:
        err = RedTeamReportNotFoundError(execution_id="exec-1")
        assert err.execution_id == "exec-1"
        assert err.status_code == 500
        assert err.error_code is ErrorCode.ENGINE_ERROR
        assert err.error_category is ErrorCategory.INTERNAL


@pytest.mark.unit
class TestRedTeamReportValidationError:
    """422 VALIDATION."""

    def test_attributes(self) -> None:
        err = RedTeamReportValidationError("bad payload")
        assert err.status_code == 422
        assert err.error_code is ErrorCode.REQUEST_VALIDATION_ERROR
        assert err.error_category is ErrorCategory.VALIDATION


@pytest.mark.unit
class TestRedTeamReportAlreadyExistsError:
    """409 CONFLICT."""

    def test_attributes(self) -> None:
        err = RedTeamReportAlreadyExistsError(execution_id="exec-1")
        assert err.execution_id == "exec-1"
        assert err.status_code == 409
        assert err.error_code is ErrorCode.RESOURCE_CONFLICT
        assert err.error_category is ErrorCategory.CONFLICT
