"""Tests for approvals controller helper functions."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.api.controllers._approval_review_gate import (
    preflight_review_gate,
    try_review_gate_transition,
)
from synthorg.api.controllers.approvals import (
    _log_approval_decision,
    _publish_approval_event,
    _resolve_decision,
    _signal_resume_intent,
)
from synthorg.api.state import AppState
from synthorg.core.approval import ApprovalItem
from synthorg.core.domain_errors import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ServiceUnavailableError,
    UnauthorizedError,
)
from synthorg.core.enums import ApprovalRiskLevel, ApprovalStatus
from synthorg.engine.errors import (
    SelfReviewError,
    TaskInternalError,
    TaskNotFoundError,
    TaskVersionConflictError,
)

pytestmark = pytest.mark.unit


def _make_pending_item(approval_id: str = "approval-1") -> ApprovalItem:
    from datetime import UTC, datetime

    return ApprovalItem(
        id=approval_id,
        action_type="deploy:production",
        title="Deploy to prod",
        description="Deploy v2.0",
        requested_by="agent-1",
        risk_level=ApprovalRiskLevel.HIGH,
        status=ApprovalStatus.PENDING,
        created_at=datetime.now(UTC),
    )


def _make_request(*, user: object = None) -> MagicMock:
    request = MagicMock()
    request.scope = {"user": user}
    request.app.plugins = []
    return request


def _make_auth_user(username: str = "admin") -> MagicMock:
    from synthorg.core.auth.models import AuthenticatedUser

    user = MagicMock(spec=AuthenticatedUser)
    user.username = username
    return user


class TestResolveDecision:
    """_resolve_decision() pre-checks."""

    def test_raises_conflict_when_not_pending(self) -> None:
        request = _make_request(user=_make_auth_user())
        item = _make_pending_item().model_copy(
            update={"status": ApprovalStatus.APPROVED},
        )
        with pytest.raises(ConflictError, match="not pending"):
            _resolve_decision(request, item, "approval-1")

    def test_raises_unauthorized_when_no_user(self) -> None:
        request = _make_request(user=None)
        item = _make_pending_item()
        with pytest.raises(UnauthorizedError, match="Authentication"):
            _resolve_decision(request, item, "approval-1")

    def test_raises_unauthorized_when_wrong_user_type(self) -> None:
        request = _make_request(user="not-an-auth-user")
        item = _make_pending_item()
        with pytest.raises(UnauthorizedError, match="Authentication"):
            _resolve_decision(request, item, "approval-1")

    def test_returns_auth_user_when_valid(self) -> None:
        auth_user = _make_auth_user("ceo")
        request = _make_request(user=auth_user)
        item = _make_pending_item()
        result = _resolve_decision(request, item, "approval-1")
        assert result is auth_user


class TestLogApprovalDecision:
    """_log_approval_decision() logs correctly."""

    def test_logs_approved(self) -> None:
        # Should not raise
        _log_approval_decision(
            "approval-1",
            approved=True,
            decided_by="admin",
        )

    def test_logs_rejected(self) -> None:
        _log_approval_decision(
            "approval-1",
            approved=False,
            decided_by="reviewer",
        )


class TestSignalResumeIntent:
    """_signal_resume_intent() resume + review-gate dispatch."""

    async def test_no_gate_no_review_gate_is_noop(self) -> None:
        """When both gates are None, function is a no-op."""
        app_state = MagicMock(spec=AppState)
        app_state.approval_gate = None
        app_state.review_gate_service = None
        await _signal_resume_intent(
            app_state,
            "approval-1",
            approved=True,
            decided_by="admin",
        )

    async def test_flow1_parked_context_dispatches_and_skips_review(
        self,
    ) -> None:
        """A parked context dispatches a resume; Flow 2 is skipped."""
        mock_gate = MagicMock()
        mock_gate.has_parked_context = AsyncMock(return_value=True)
        mock_worker = MagicMock()
        mock_worker.dispatch_resume = AsyncMock()
        mock_review = MagicMock()
        mock_review.complete_review = AsyncMock()

        app_state = MagicMock(spec=AppState)
        app_state.approval_gate = mock_gate
        app_state.worker_execution_service = mock_worker
        app_state.review_gate_service = mock_review

        await _signal_resume_intent(
            app_state,
            "approval-1",
            approved=True,
            decided_by="admin",
            task_id="task-1",
        )

        mock_gate.has_parked_context.assert_awaited_once_with("approval-1")
        mock_worker.dispatch_resume.assert_awaited_once_with(
            approval_id="approval-1",
            approved=True,
            decided_by="admin",
            decision_reason=None,
        )
        # Flow 2 must NOT run -- the mid-execution flow owns this id.
        mock_review.complete_review.assert_not_awaited()

    async def test_flow1_no_parked_context_falls_through(self) -> None:
        """No parked context -> Flow 2 (review gate) runs."""
        mock_gate = MagicMock()
        mock_gate.has_parked_context = AsyncMock(return_value=False)
        mock_worker = MagicMock()
        mock_worker.dispatch_resume = AsyncMock()
        mock_review = MagicMock()
        mock_review.complete_review = AsyncMock()

        app_state = MagicMock(spec=AppState)
        app_state.approval_gate = mock_gate
        app_state.worker_execution_service = mock_worker
        app_state.review_gate_service = mock_review

        await _signal_resume_intent(
            app_state,
            "approval-1",
            approved=True,
            decided_by="admin",
            task_id="task-1",
        )

        mock_worker.dispatch_resume.assert_not_awaited()
        mock_review.complete_review.assert_awaited_once_with(
            task_id="task-1",
            requested_by="admin",
            approved=True,
            decided_by="admin",
            reason=None,
            approval_id="approval-1",
        )

    async def test_flow1_existence_check_error_returns_early(self) -> None:
        """An indeterminate existence check does NOT fall through.

        A parked context may still exist, so running the review-gate
        transition would double-handle the decision.
        """
        mock_gate = MagicMock()
        mock_gate.has_parked_context = AsyncMock(
            side_effect=RuntimeError("db error"),
        )
        mock_review = MagicMock()
        mock_review.complete_review = AsyncMock()

        app_state = MagicMock(spec=AppState)
        app_state.approval_gate = mock_gate
        app_state.review_gate_service = mock_review

        await _signal_resume_intent(
            app_state,
            "approval-1",
            approved=True,
            decided_by="admin",
            task_id="task-1",
        )

        mock_review.complete_review.assert_not_awaited()

    async def test_flow1_dispatch_failure_is_swallowed_not_5xx(self) -> None:
        """A dispatch failure is logged, not raised (decision persisted).

        The decision is already saved before resume is signalled; a
        worker dispatch failure must not 5xx the approve/reject
        response, and must still suppress the review-gate fall-through.
        """
        mock_gate = MagicMock()
        mock_gate.has_parked_context = AsyncMock(return_value=True)
        mock_worker = MagicMock()
        mock_worker.dispatch_resume = AsyncMock(
            side_effect=RuntimeError("runtime not configured"),
        )
        mock_review = MagicMock()
        mock_review.complete_review = AsyncMock()

        app_state = MagicMock(spec=AppState)
        app_state.approval_gate = mock_gate
        app_state.worker_execution_service = mock_worker
        app_state.review_gate_service = mock_review

        await _signal_resume_intent(
            app_state,
            "approval-1",
            approved=True,
            decided_by="admin",
            task_id="task-1",
        )

        mock_review.complete_review.assert_not_awaited()

    async def test_flow2_review_gate_called_with_task_id(self) -> None:
        """When no approval_gate and task_id provided, review gate runs."""
        mock_review = MagicMock()
        mock_review.complete_review = AsyncMock()

        app_state = MagicMock(spec=AppState)
        app_state.approval_gate = None
        app_state.review_gate_service = mock_review

        await _signal_resume_intent(
            app_state,
            "approval-1",
            approved=False,
            decided_by="reviewer",
            decision_reason="Needs rework",
            task_id="task-42",
        )

        mock_review.complete_review.assert_awaited_once_with(
            task_id="task-42",
            requested_by="reviewer",
            approved=False,
            decided_by="reviewer",
            reason="Needs rework",
            approval_id="approval-1",
        )

    async def test_flow2_skipped_when_no_task_id(self) -> None:
        """When task_id is None, review gate is not called."""
        mock_review = MagicMock()
        mock_review.complete_review = AsyncMock()

        app_state = MagicMock(spec=AppState)
        app_state.approval_gate = None
        app_state.review_gate_service = mock_review

        await _signal_resume_intent(
            app_state,
            "approval-1",
            approved=True,
            decided_by="admin",
            task_id=None,
        )

        mock_review.complete_review.assert_not_awaited()

    async def test_flow2_unknown_exception_propagates(self) -> None:
        """Unknown errors from the review gate propagate -- not swallowed.

        The old behavior of catching ``Exception`` and logging a warning
        masked real workflow failures (task mutation errors, persistence
        failures, etc.) while returning 200 OK to the caller.  The fix
        narrows exception handling to specific typed errors the API
        layer knows how to map (SelfReviewError -> 403, TaskNotFoundError
        -> 404, TaskVersionConflictError -> 409).  Everything else
        propagates to the caller as an unhandled error.
        """
        mock_review = MagicMock()
        mock_review.complete_review = AsyncMock(
            side_effect=RuntimeError("transition failed"),
        )

        app_state = MagicMock(spec=AppState)
        app_state.approval_gate = None
        app_state.review_gate_service = mock_review

        with pytest.raises(RuntimeError, match="transition failed"):
            await _signal_resume_intent(
                app_state,
                "approval-1",
                approved=True,
                decided_by="admin",
                task_id="task-1",
            )

        mock_review.complete_review.assert_awaited_once()

    @pytest.mark.parametrize(
        "error_cls",
        [MemoryError, RecursionError],
        ids=["MemoryError", "RecursionError"],
    )
    async def test_flow1_memory_error_propagates(
        self, error_cls: type[BaseException]
    ) -> None:
        """MemoryError/RecursionError from the existence check propagates."""
        mock_gate = MagicMock()
        mock_gate.has_parked_context = AsyncMock(
            side_effect=error_cls("fatal"),
        )

        app_state = MagicMock(spec=AppState)
        app_state.approval_gate = mock_gate
        app_state.review_gate_service = None

        with pytest.raises(error_cls):
            await _signal_resume_intent(
                app_state,
                "approval-1",
                approved=True,
                decided_by="admin",
            )

    @pytest.mark.parametrize(
        "error_cls",
        [MemoryError, RecursionError],
        ids=["MemoryError", "RecursionError"],
    )
    async def test_flow2_memory_error_propagates(
        self, error_cls: type[BaseException]
    ) -> None:
        """MemoryError/RecursionError from review gate propagates."""
        mock_review = MagicMock()
        mock_review.complete_review = AsyncMock(
            side_effect=error_cls("fatal"),
        )

        app_state = MagicMock(spec=AppState)
        app_state.approval_gate = None
        app_state.review_gate_service = mock_review

        with pytest.raises(error_cls):
            await _signal_resume_intent(
                app_state,
                "approval-1",
                approved=True,
                decided_by="admin",
                task_id="task-1",
            )


class TestPreflightReviewGate:
    """preflight_review_gate maps engine errors to API errors.

    Coverage guard for the self-review enforcement pathway: validates
    that the preflight runs BEFORE the approval is persisted and that
    each engine error is translated to the correct HTTP status code
    with a generic user-facing message that never leaks internal
    identifiers.
    """

    async def test_passes_through_when_authorized(self) -> None:
        """Happy path: preflight returns without raising."""
        review_gate = MagicMock()
        review_gate.check_can_decide = AsyncMock(return_value=MagicMock())

        await preflight_review_gate(
            review_gate,
            "approval-1",
            "task-1",
            decided_by="bob",
        )
        review_gate.check_can_decide.assert_awaited_once_with(
            task_id="task-1",
            decided_by="bob",
        )

    async def test_self_review_raises_forbidden(self) -> None:
        """SelfReviewError maps to ForbiddenError with a generic message."""
        review_gate = MagicMock()
        review_gate.check_can_decide = AsyncMock(
            side_effect=SelfReviewError(task_id="task-1", agent_id="alice"),
        )

        with pytest.raises(ForbiddenError) as exc_info:
            await preflight_review_gate(
                review_gate,
                "approval-1",
                "task-1",
                decided_by="alice",
            )
        # Generic message -- never leak task_id or agent_id to the client.
        msg = str(exc_info.value)
        assert "Self-review is not permitted" in msg
        assert "task-1" not in msg
        assert "alice" not in msg

    async def test_task_not_found_raises_404(self) -> None:
        """TaskNotFoundError maps to NotFoundError with a generic message."""
        review_gate = MagicMock()
        review_gate.check_can_decide = AsyncMock(
            side_effect=TaskNotFoundError("Task 'task-xyz' not found"),
        )

        with pytest.raises(NotFoundError) as exc_info:
            await preflight_review_gate(
                review_gate,
                "approval-1",
                "task-xyz",
                decided_by="bob",
            )
        # Generic message -- never leak task_id via 404.
        assert "task-xyz" not in str(exc_info.value)

    async def test_task_internal_error_raises_503(self) -> None:
        """TaskInternalError maps to ServiceUnavailableError (503)."""
        review_gate = MagicMock()
        review_gate.check_can_decide = AsyncMock(
            side_effect=TaskInternalError("Persistence backend offline"),
        )

        with pytest.raises(ServiceUnavailableError):
            await preflight_review_gate(
                review_gate,
                "approval-1",
                "task-1",
                decided_by="bob",
            )


class TestTryReviewGateTransition:
    """try_review_gate_transition maps engine errors to API errors.

    Regression guard for the narrow-exception-handling refactor: each
    typed engine error must surface as the correct HTTP status code.
    Anything else (e.g., RuntimeError) propagates to the caller
    instead of being silently swallowed as 200 OK.
    """

    async def test_passes_approval_id_to_service(self) -> None:
        """approval_id is threaded through for audit cross-reference."""
        review_gate = MagicMock()
        review_gate.complete_review = AsyncMock()

        await try_review_gate_transition(
            review_gate,
            "approval-42",
            "task-1",
            approved=True,
            decided_by="bob",
            decision_reason=None,
        )
        review_gate.complete_review.assert_awaited_once_with(
            task_id="task-1",
            requested_by="bob",
            approved=True,
            decided_by="bob",
            reason=None,
            approval_id="approval-42",
        )

    async def test_self_review_race_raises_forbidden(self) -> None:
        """Late SelfReviewError (reassignment between preflight and transition)."""
        review_gate = MagicMock()
        review_gate.complete_review = AsyncMock(
            side_effect=SelfReviewError(task_id="task-1", agent_id="alice"),
        )

        with pytest.raises(ForbiddenError) as exc_info:
            await try_review_gate_transition(
                review_gate,
                "approval-1",
                "task-1",
                approved=True,
                decided_by="alice",
                decision_reason=None,
            )
        msg = str(exc_info.value)
        assert "task-1" not in msg
        assert "alice" not in msg

    async def test_task_version_conflict_raises_409(self) -> None:
        """TaskVersionConflictError maps to ConflictError (409)."""
        review_gate = MagicMock()
        review_gate.complete_review = AsyncMock(
            side_effect=TaskVersionConflictError("Version 3 != 2"),
        )

        with pytest.raises(ConflictError) as exc_info:
            await try_review_gate_transition(
                review_gate,
                "approval-1",
                "task-1",
                approved=True,
                decided_by="bob",
                decision_reason=None,
            )
        # Generic message -- never leak task_id via 409.
        assert "task-1" not in str(exc_info.value)

    async def test_task_not_found_raises_404(self) -> None:
        """TaskNotFoundError maps to NotFoundError with a generic message."""
        review_gate = MagicMock()
        review_gate.complete_review = AsyncMock(
            side_effect=TaskNotFoundError("Task 'task-xyz' not found"),
        )

        with pytest.raises(NotFoundError) as exc_info:
            await try_review_gate_transition(
                review_gate,
                "approval-1",
                "task-xyz",
                approved=True,
                decided_by="bob",
                decision_reason=None,
            )
        assert "task-xyz" not in str(exc_info.value)

    async def test_task_internal_error_raises_503(self) -> None:
        """TaskInternalError maps to ServiceUnavailableError."""
        review_gate = MagicMock()
        review_gate.complete_review = AsyncMock(
            side_effect=TaskInternalError("Persistence backend offline"),
        )

        with pytest.raises(ServiceUnavailableError):
            await try_review_gate_transition(
                review_gate,
                "approval-1",
                "task-1",
                approved=True,
                decided_by="bob",
                decision_reason=None,
            )


class TestPublishApprovalEvent:
    """_publish_approval_event() best-effort WebSocket publishing."""

    def test_logs_warning_when_no_channels_plugin(self) -> None:
        from synthorg.api.ws_models import WsEventType

        request = _make_request()
        request.app.plugins = []  # No ChannelsPlugin
        item = _make_pending_item()
        # Should not raise -- best-effort
        _publish_approval_event(
            request,
            WsEventType.APPROVAL_SUBMITTED,
            item,
        )

    def test_publishes_when_plugin_available(self) -> None:
        from litestar.channels import ChannelsPlugin

        from synthorg.api.ws_models import WsEventType

        plugin = MagicMock(spec=ChannelsPlugin)
        request = _make_request()
        request.app.plugins = [plugin]
        item = _make_pending_item()

        _publish_approval_event(
            request,
            WsEventType.APPROVAL_SUBMITTED,
            item,
        )
        plugin.publish.assert_called_once()

    def test_logs_warning_when_publish_fails(self) -> None:
        from litestar.channels import ChannelsPlugin

        from synthorg.api.ws_models import WsEventType

        plugin = MagicMock(spec=ChannelsPlugin)
        plugin.publish.side_effect = RuntimeError("not started")
        request = _make_request()
        request.app.plugins = [plugin]
        item = _make_pending_item()

        # Should not raise -- best-effort
        _publish_approval_event(
            request,
            WsEventType.APPROVAL_SUBMITTED,
            item,
        )
