"""Unit tests for task controller helper functions."""

import pytest

from synthorg.api.controllers.tasks import _extract_requester, _map_task_engine_errors
from synthorg.api.errors import (
    ApiValidationError,
    ConflictError,
    NotFoundError,
    ServiceUnavailableError,
)
from synthorg.engine.errors import (
    TaskEngineNotRunningError,
    TaskEngineQueueFullError,
    TaskInternalError,
    TaskMutationError,
    TaskNotFoundError,
    TaskVersionConflictError,
)

# ── _extract_requester ───────────────────────────────────────


@pytest.mark.unit
class TestExtractRequester:
    """Tests for extracting requester identity from state."""

    def test_returns_user_id_when_present(self) -> None:
        """Auth middleware sets _connection_user with user_id."""

        class FakeUser:
            user_id = "user-123"

        class FakeState:
            _connection_user = FakeUser()

        assert _extract_requester(FakeState()) == "user-123"  # type: ignore[arg-type]

    def test_returns_api_fallback_when_no_user(self) -> None:
        class FakeState:
            pass

        assert _extract_requester(FakeState()) == "api"  # type: ignore[arg-type]

    def test_returns_api_when_user_has_no_user_id(self) -> None:
        class FakeUser:
            pass

        class FakeState:
            _connection_user = FakeUser()

        assert _extract_requester(FakeState()) == "api"  # type: ignore[arg-type]


# ── _map_task_engine_errors ──────────────────────────────────


@pytest.mark.unit
class TestMapTaskEngineErrors:
    """Tests for mapping engine errors to API errors."""

    def test_not_found_maps_to_not_found_error(self) -> None:
        exc = TaskNotFoundError("Task 'x' not found")
        result = _map_task_engine_errors(exc, task_id="x")
        assert isinstance(result, NotFoundError)

    def test_not_found_without_task_id(self) -> None:
        exc = TaskNotFoundError("not found")
        result = _map_task_engine_errors(exc)
        assert isinstance(result, NotFoundError)

    def test_not_running_maps_to_service_unavailable(self) -> None:
        exc = TaskEngineNotRunningError("not running")
        result = _map_task_engine_errors(exc)
        assert isinstance(result, ServiceUnavailableError)
        assert str(result) == "Service temporarily unavailable"

    def test_queue_full_maps_to_service_unavailable(self) -> None:
        exc = TaskEngineQueueFullError("queue full")
        result = _map_task_engine_errors(exc)
        assert isinstance(result, ServiceUnavailableError)
        assert str(result) == "Service temporarily unavailable"

    def test_internal_error_maps_to_service_unavailable(self) -> None:
        exc = TaskInternalError("internal fault")
        result = _map_task_engine_errors(exc)
        assert isinstance(result, ServiceUnavailableError)
        assert str(result) == "Internal server error"

    def test_version_conflict_maps_to_conflict_error(self) -> None:
        exc = TaskVersionConflictError("version mismatch")
        result = _map_task_engine_errors(exc, task_id="task-1")
        assert isinstance(result, ConflictError)
        assert result.status_code == 409

    def test_version_conflict_preserves_message(self) -> None:
        exc = TaskVersionConflictError("expected 2, current 3")
        result = _map_task_engine_errors(exc)
        assert isinstance(result, ConflictError)
        assert "expected 2, current 3" in str(result)

    def test_mutation_error_maps_to_validation_error(self) -> None:
        exc = TaskMutationError("bad input")
        result = _map_task_engine_errors(exc)
        assert isinstance(result, ApiValidationError)

    def test_unknown_error_wraps_as_service_unavailable(self) -> None:
        exc = RuntimeError("unexpected")
        result = _map_task_engine_errors(exc)
        assert isinstance(result, ServiceUnavailableError)
        assert "Unexpected engine error" in str(result)
