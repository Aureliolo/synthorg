"""Unit tests for task controller helper functions."""

import pytest

from synthorg.api.auth.context import (
    AuthContextMissingError,
    authenticated_user_scope,
)
from synthorg.api.controllers._requester import extract_requester
from synthorg.core.auth.models import AuthenticatedUser, AuthMethod
from synthorg.core.auth.roles import HumanRole
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode
from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import (
    TaskEngineNotRunningError,
    TaskEngineQueueFullError,
    TaskInternalError,
    TaskMutationError,
    TaskNotFoundError,
    TaskVersionConflictError,
)

# ── extract_requester ────────────────────────────────────────


@pytest.mark.unit
class TestExtractRequester:
    """Naming the operator behind an audited write.

    Driven through the request-scoped binding production installs, and not
    a hand-built shape agreeing only with the lookup under test: a lookup
    and its test can agree with each other and with nothing the runtime
    does, leaving every audited write naming the transport while the suite
    stays green.
    """

    async def test_the_bound_user_is_who_the_write_names(self) -> None:
        user = AuthenticatedUser(
            user_id=NotBlankStr("user-123"),
            username=NotBlankStr("Aurelio"),
            role=HumanRole.CEO,
            auth_method=AuthMethod.JWT,
        )

        async with authenticated_user_scope(user):
            assert extract_requester() == "user-123"

    async def test_no_bound_user_raises_rather_than_inventing_one(self) -> None:
        """An audited write that cannot name its actor is not completed."""
        with pytest.raises(AuthContextMissingError):
            extract_requester()


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
