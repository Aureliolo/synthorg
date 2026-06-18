"""Unit tests for task controller helper functions."""

import pytest
from litestar.datastructures import State

from synthorg.api.controllers.tasks import _extract_requester
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode
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

        state = State({"_connection_user": FakeUser()})
        assert _extract_requester(state) == "user-123"

    def test_returns_api_fallback_when_no_user(self) -> None:
        assert _extract_requester(State({})) == "api"

    def test_returns_api_when_user_has_no_user_id(self) -> None:
        class FakeUser:
            pass

        state = State({"_connection_user": FakeUser()})
        assert _extract_requester(state) == "api"


# ── TaskEngine error HTTP metadata (replaces the deleted mapper) ─────


@pytest.mark.unit
class TestTaskEngineErrorMetadata:
    """TaskEngine errors must carry the HTTP metadata that ``handle_domain_error``
    reads when building the RFC 9457 envelope. The previous ``_map_task_engine_errors``
    helper translated each class into a generic domain error at the controller
    boundary; now the engine classes own their metadata directly."""

    def test_not_found_metadata(self) -> None:
        exc = TaskNotFoundError("Task 'x' not found")
        assert exc.status_code == 404
        assert exc.error_code is ErrorCode.TASK_NOT_FOUND
        assert exc.error_category is ErrorCategory.NOT_FOUND

    def test_not_running_metadata(self) -> None:
        exc = TaskEngineNotRunningError("not running")
        assert exc.status_code == 503
        assert exc.error_code is ErrorCode.TASK_ENGINE_NOT_RUNNING
        assert exc.error_category is ErrorCategory.INTERNAL
        assert exc.retryable is True

    def test_queue_full_metadata(self) -> None:
        exc = TaskEngineQueueFullError("queue full")
        assert exc.status_code == 503
        assert exc.error_code is ErrorCode.TASK_ENGINE_QUEUE_FULL
        assert exc.error_category is ErrorCategory.INTERNAL
        assert exc.retryable is True

    def test_internal_error_metadata(self) -> None:
        exc = TaskInternalError("internal fault")
        assert exc.status_code == 500
        assert exc.error_code is ErrorCode.ENGINE_ERROR
        assert exc.error_category is ErrorCategory.INTERNAL
        # ``handle_domain_error`` picks ``default_message`` for 5xx responses
        # to avoid leaking the raise-site message; assert the contract here.
        assert exc.default_message == "Internal server error"
        assert "internal fault" not in exc.default_message

    def test_version_conflict_metadata(self) -> None:
        exc = TaskVersionConflictError("version mismatch")
        assert exc.status_code == 409
        assert exc.error_code is ErrorCode.TASK_VERSION_CONFLICT
        assert exc.error_category is ErrorCategory.CONFLICT

    def test_mutation_error_metadata(self) -> None:
        exc = TaskMutationError("bad input")
        assert exc.status_code == 422
        assert exc.error_code is ErrorCode.VALIDATION_ERROR
        assert exc.error_category is ErrorCategory.VALIDATION
