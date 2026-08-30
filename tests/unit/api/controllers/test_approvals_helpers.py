"""Tests for approvals controller helper functions."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from structlog.testing import capture_logs

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.controllers._approval_review_gate import (
    preflight_review_gate,
    signal_resume_intent,
    try_review_gate_transition,
)
from synthorg.api.controllers.approvals._notify import (
    _log_approval_decision,
    _publish_approval_event,
    _resolve_decision,
    _run_review_gate_preflight,
)
from synthorg.api.state import AppState
from synthorg.approval.enums import ApprovalRiskLevel, ApprovalSource, ApprovalStatus
from synthorg.approval.plan_review import PLAN_APPROVAL_ACTION_TYPE
from synthorg.approval.questions import CLARIFY_ACTION_TYPE, DECISION_ACTION_TYPE
from synthorg.approval.resume_annotations import DEFAULT_RESUME_ANNOTATIONS
from synthorg.approval.state import ApprovalStateSlice
from synthorg.approval.task_review import REVIEW_ACTION_TYPES
from synthorg.core.approval import ApprovalItem
from synthorg.core.auth.models import AuthenticatedUser
from synthorg.core.domain_errors import (
    AgentRuntimeNotConfiguredError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
)
from synthorg.core.task import Task
from synthorg.engine.approval_gate import ApprovalGate
from synthorg.engine.errors import (
    SelfReviewError,
    TaskInternalError,
    TaskMutationError,
    TaskNotFoundError,
    TaskVersionConflictError,
)
from synthorg.engine.review_gate import ReviewGateService
from synthorg.observability.events.approval_gate import APPROVAL_GATE_RESUME_FAILED
from synthorg.observability.events.security import (
    SECURITY_APPROVAL_APPROVED,
    SECURITY_APPROVAL_REJECTED,
)
from synthorg.workers.execution_service import WorkerExecutionService
from tests._shared import as_uuid, make_app_state, mock_of

pytestmark = pytest.mark.unit


def _make_pending_item(
    approval_id: str = "approval-1",
    *,
    source: ApprovalSource = ApprovalSource.REVIEW_GATE,
) -> ApprovalItem:
    from datetime import UTC, datetime

    return ApprovalItem(
        id=as_uuid(approval_id),
        action_type="deploy:production",
        title="Deploy to prod",
        description="Deploy v2.0",
        requested_by="agent-1",
        risk_level=ApprovalRiskLevel.HIGH,
        source=source,
        status=ApprovalStatus.PENDING,
        created_at=datetime.now(UTC),
    )


def _store(item: ApprovalItem | None) -> Any:  # type: ignore[explicit-any]  # mock_of returns Any by design
    """An approval-store double (``mock_of[ApprovalStore]``).

    Return type is ``Any`` to match ``mock_of``'s deliberate static
    signature (it returns ``Any`` so call sites need no cast); callers
    assign it to the typed ``app_state.approval_store`` slot.
    """
    return mock_of[ApprovalStore](get=AsyncMock(return_value=item))


def _app_state(
    *,
    gate: object = None,
    review_gate: object = None,
    store: object = None,
    worker: object = None,
) -> AppState:
    """An ``AppState`` double whose ``ApprovalStateSlice`` carries the doubles.

    The resume helpers read the approval store / gate / review gate
    through ``app_state.slice(ApprovalStateSlice)``; the worker
    execution service is still a direct attribute. ``model_construct``
    bypasses Pydantic validation so the test mocks slot in unchanged.

    Returns:
        ``Any`` instance.
    """
    return make_app_state(
        approval_store=store,
        worker_execution_service=worker,
        slices={ApprovalStateSlice: {"gate": gate, "review_gate": review_gate}},
    )


def _make_request(*, user: object = None) -> MagicMock:
    # MagicMock (not SimpleNamespace) because the helpers under test take a
    # typed ``Request`` parameter that an attribute-bag cannot satisfy.
    request = MagicMock()
    request.scope = {"user": user}
    request.app.plugins = []
    return request


def _make_auth_user(username: str = "admin") -> MagicMock:
    # MagicMock(spec=) not mock_of[...]: AuthenticatedUser is a Pydantic
    # model whose ``username`` field is not a settable attribute under the
    # autospec ``spec_set``, so only the looser spec= form can stamp it.
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
        with capture_logs() as logs:
            _log_approval_decision(
                "approval-1",
                approved=True,
                decided_by="admin",
            )
        entry = next(e for e in logs if e["event"] == SECURITY_APPROVAL_APPROVED)
        assert entry["approval_id"] == "approval-1"
        assert entry["decided_by"] == "admin"

    def test_logs_rejected(self) -> None:
        with capture_logs() as logs:
            _log_approval_decision(
                "approval-1",
                approved=False,
                decided_by="reviewer",
            )
        events = [e["event"] for e in logs]
        assert SECURITY_APPROVAL_REJECTED in events
        assert SECURITY_APPROVAL_APPROVED not in events


class TestSignalResumeIntent:
    """signal_resume_intent() resume + review-gate dispatch."""

    async def test_no_gate_no_review_gate_is_noop(self) -> None:
        """When both gates are None, function is a no-op."""
        app_state = _app_state(
            gate=None,
            review_gate=None,
            store=_store(_make_pending_item()),
        )
        await signal_resume_intent(
            app_state,
            "approval-1",
            approved=True,
            decided_by="admin",
        )

    async def test_flow1_parked_context_dispatches_and_skips_review(
        self,
    ) -> None:
        """A parked-context-sourced approval dispatches a resume.

        Routing is deterministic off the persisted ``source``; the
        live ``has_parked_context`` probe is not consulted.
        """
        mock_gate = mock_of[ApprovalGate](
            has_parked_context=AsyncMock(return_value=True)
        )
        mock_worker = mock_of[WorkerExecutionService](
            dispatch_resume=AsyncMock(),
        )
        mock_review = mock_of[ReviewGateService](dispatch_completion=AsyncMock())

        app_state = _app_state(
            gate=mock_gate,
            worker=mock_worker,
            review_gate=mock_review,
            store=_store(
                _make_pending_item(source=ApprovalSource.PARKED_CONTEXT),
            ),
        )

        await signal_resume_intent(
            app_state,
            "approval-1",
            approved=True,
            decided_by="admin",
            task_id="task-1",
        )

        # Deterministic source routing: the probe is bypassed.
        mock_gate.has_parked_context.assert_not_awaited()
        mock_worker.dispatch_resume.assert_awaited_once_with(
            approval_id="approval-1",
            approved=True,
            decided_by="admin",
            decision_reason=None,
            annotations=DEFAULT_RESUME_ANNOTATIONS,
        )
        # Flow 2 must NOT run -- the mid-execution flow owns this id.
        mock_review.dispatch_completion.assert_not_awaited()

    async def test_flow1_review_gate_source_falls_through(self) -> None:
        """A review-gate-sourced approval -> Flow 2 (review gate) runs."""
        mock_gate = mock_of[ApprovalGate](
            has_parked_context=AsyncMock(return_value=False)
        )
        mock_worker = mock_of[WorkerExecutionService](
            dispatch_resume=AsyncMock(),
        )
        mock_review = mock_of[ReviewGateService](dispatch_completion=AsyncMock())

        app_state = _app_state(
            gate=mock_gate,
            worker=mock_worker,
            review_gate=mock_review,
            store=_store(
                _make_pending_item(source=ApprovalSource.REVIEW_GATE),
            ),
        )

        await signal_resume_intent(
            app_state,
            "approval-1",
            approved=True,
            decided_by="admin",
            task_id="task-1",
        )

        # Deterministic source routing: REVIEW_GATE-sourced approvals
        # must bypass the parked-context probe entirely.
        mock_gate.has_parked_context.assert_not_awaited()
        mock_worker.dispatch_resume.assert_not_awaited()
        mock_review.dispatch_completion.assert_awaited_once_with(
            task_id="task-1",
            approved=True,
            decided_by="admin",
            reason=None,
            approval_id="approval-1",
        )

    async def test_flow1_existence_check_error_returns_early(self) -> None:
        """An indeterminate existence check does NOT fall through.

        A parked context may still exist, so running the review-gate
        transition would double-handle the decision. The probe is the
        fallback path, reached only when the approval row cannot be
        re-read (``get`` returns ``None``).
        """
        mock_gate = mock_of[ApprovalGate](
            has_parked_context=AsyncMock(side_effect=RuntimeError("db error"))
        )
        mock_review = mock_of[ReviewGateService](dispatch_completion=AsyncMock())

        app_state = _app_state(
            gate=mock_gate,
            review_gate=mock_review,
            store=_store(None),
        )

        await signal_resume_intent(
            app_state,
            "approval-1",
            approved=True,
            decided_by="admin",
            task_id="task-1",
        )

        # The fallback probe must actually have been exercised
        # (item is None -> probe), not short-circuited before it.
        mock_gate.has_parked_context.assert_awaited_once_with("approval-1")
        mock_review.dispatch_completion.assert_not_awaited()

    async def test_unreadable_store_stops_routing_before_the_review_gate(
        self,
    ) -> None:
        """A store outage during ownership routing must not reach Flow 2.

        Regression: ``_reread_approval_item`` used to swallow every reread
        failure to ``None``, which the ownership-probing flows (plan review,
        initiative stall, workstream extension) read as "not mine", letting
        an approval whose ``task_id`` happens to be set fall through into
        Flow 2's review-gate transition and be misread as an ordinary
        completion review.
        """
        mock_review = mock_of[ReviewGateService](dispatch_completion=AsyncMock())
        app_state = _app_state(
            gate=mock_of[ApprovalGate](),
            review_gate=mock_review,
            store=mock_of[ApprovalStore](
                get=AsyncMock(side_effect=RuntimeError("store down")),
            ),
        )

        with capture_logs() as logs:
            await signal_resume_intent(
                app_state,
                "approval-1",
                approved=True,
                decided_by="admin",
                task_id="task-1",
            )

        mock_review.dispatch_completion.assert_not_awaited()
        events = [e["event"] for e in logs]
        assert APPROVAL_GATE_RESUME_FAILED in events

    async def test_flow1_dispatch_failure_is_swallowed_not_5xx(self) -> None:
        """A dispatch failure is logged, not raised (decision persisted).

        The decision is already saved before resume is signalled; a
        worker dispatch failure must not 5xx the approve/reject
        response, and must still suppress the review-gate fall-through.
        """
        mock_worker = mock_of[WorkerExecutionService](
            dispatch_resume=AsyncMock(
                side_effect=RuntimeError("runtime not configured"),
            ),
        )
        mock_review = mock_of[ReviewGateService](dispatch_completion=AsyncMock())

        app_state = _app_state(
            gate=mock_of[ApprovalGate](),
            worker=mock_worker,
            review_gate=mock_review,
            store=_store(
                _make_pending_item(source=ApprovalSource.PARKED_CONTEXT),
            ),
        )

        await signal_resume_intent(
            app_state,
            "approval-1",
            approved=True,
            decided_by="admin",
            task_id="task-1",
        )

        # The dispatch path must actually have run (otherwise the test
        # would pass even if signal_resume_intent returned before
        # awaiting dispatch_resume, never exercising the swallow).
        mock_worker.dispatch_resume.assert_awaited_once()
        mock_review.dispatch_completion.assert_not_awaited()

    async def test_flow1_runtime_not_configured_propagates(self) -> None:
        """A runtime-misconfig dispatch failure must NOT be swallowed.

        AgentRuntimeNotConfiguredError means the parked run can never
        resume; returning True (handled) would silently strand it. It
        must propagate so the controller surfaces the real error.
        """
        mock_worker = mock_of[WorkerExecutionService](
            dispatch_resume=AsyncMock(
                side_effect=AgentRuntimeNotConfiguredError(
                    "no engine to resume into",
                ),
            ),
        )
        mock_review = mock_of[ReviewGateService](dispatch_completion=AsyncMock())

        app_state = _app_state(
            gate=mock_of[ApprovalGate](),
            worker=mock_worker,
            review_gate=mock_review,
            store=_store(
                _make_pending_item(source=ApprovalSource.PARKED_CONTEXT),
            ),
        )

        with pytest.raises(AgentRuntimeNotConfiguredError):
            await signal_resume_intent(
                app_state,
                "approval-1",
                approved=True,
                decided_by="admin",
                task_id="task-1",
            )

        mock_worker.dispatch_resume.assert_awaited_once()
        mock_review.dispatch_completion.assert_not_awaited()

    async def test_flow2_review_gate_called_with_task_id(self) -> None:
        """When no approval_gate and task_id provided, review gate runs."""
        mock_review = mock_of[ReviewGateService](dispatch_completion=AsyncMock())

        app_state = _app_state(
            gate=None,
            review_gate=mock_review,
            store=_store(_make_pending_item()),
        )

        await signal_resume_intent(
            app_state,
            "approval-1",
            approved=False,
            decided_by="reviewer",
            decision_reason="Needs rework",
            task_id="task-42",
        )

        mock_review.dispatch_completion.assert_awaited_once_with(
            task_id="task-42",
            approved=False,
            decided_by="reviewer",
            reason="Needs rework",
            approval_id="approval-1",
        )

    async def test_flow2_skipped_when_no_task_id(self) -> None:
        """When task_id is None, review gate is not called."""
        mock_review = mock_of[ReviewGateService](dispatch_completion=AsyncMock())

        app_state = _app_state(
            gate=None,
            review_gate=mock_review,
            store=_store(_make_pending_item()),
        )

        await signal_resume_intent(
            app_state,
            "approval-1",
            approved=True,
            decided_by="admin",
            task_id=None,
        )

        mock_review.dispatch_completion.assert_not_awaited()

    async def test_flow2_unknown_exception_propagates(self) -> None:
        """Unknown errors from the review gate propagate, not swallowed.

        Exception handling is narrowed to the specific typed errors the
        API layer knows how to map (SelfReviewError -> 403,
        TaskNotFoundError -> 404, TaskVersionConflictError -> 409);
        every other error propagates to the caller as an unhandled
        error rather than being masked behind a 200 OK.
        """
        mock_review = mock_of[ReviewGateService](
            dispatch_completion=AsyncMock(
                side_effect=RuntimeError("transition failed"),
            ),
        )

        app_state = _app_state(
            gate=None,
            review_gate=mock_review,
            store=_store(_make_pending_item()),
        )

        with pytest.raises(RuntimeError, match="transition failed"):
            await signal_resume_intent(
                app_state,
                "approval-1",
                approved=True,
                decided_by="admin",
                task_id="task-1",
            )

        mock_review.dispatch_completion.assert_awaited_once()

    @pytest.mark.parametrize(
        "error_cls",
        [MemoryError, RecursionError],
        ids=["MemoryError", "RecursionError"],
    )
    async def test_flow1_memory_error_propagates(
        self, error_cls: type[BaseException]
    ) -> None:
        """MemoryError/RecursionError from the fallback probe propagates."""
        mock_gate = mock_of[ApprovalGate](
            has_parked_context=AsyncMock(side_effect=error_cls("fatal"))
        )

        app_state = _app_state(
            gate=mock_gate,
            review_gate=None,
            store=_store(None),
        )

        with pytest.raises(error_cls):
            await signal_resume_intent(
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
        mock_review = mock_of[ReviewGateService](
            dispatch_completion=AsyncMock(
                side_effect=error_cls("fatal"),
            ),
        )

        app_state = _app_state(
            gate=None,
            review_gate=mock_review,
            store=_store(_make_pending_item()),
        )

        with pytest.raises(error_cls):
            await signal_resume_intent(
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
        review_gate = mock_of[ReviewGateService](
            check_can_decide=AsyncMock(return_value=mock_of[Task]()),
        )

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
        review_gate = mock_of[ReviewGateService](
            check_can_decide=AsyncMock(
                side_effect=SelfReviewError(task_id="task-1", agent_id="alice"),
            ),
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
        review_gate = mock_of[ReviewGateService](
            check_can_decide=AsyncMock(
                side_effect=TaskNotFoundError("Task 'task-xyz' not found"),
            ),
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

    async def test_task_internal_error_propagates_500(self) -> None:
        """TaskInternalError propagates as its faithful 500 ENGINE_ERROR."""
        review_gate = mock_of[ReviewGateService](
            check_can_decide=AsyncMock(
                side_effect=TaskInternalError("Persistence backend offline"),
            ),
        )

        with pytest.raises(TaskInternalError):
            await preflight_review_gate(
                review_gate,
                "approval-1",
                "task-1",
                decided_by="bob",
            )


class TestPreflightRunsOnlyForTaskReviews:
    """The completion gate's rules belong to the completion gate's approvals.

    A parked question and a plan approval both carry the objective task's id,
    because that is what they are ABOUT. Dispatching on ``task_id is not None``
    ran the self-review check against both: it warned that a task "reaching
    review" had no assignee for tasks that were not reaching review, and it
    would have refused an operator's answer to a question as self-review had
    the objective task ever carried them as its assignee.
    """

    def _item(self, action_type: str) -> ApprovalItem:
        return _make_pending_item().model_copy(
            update={"action_type": action_type, "task_id": "task-1"}
        )

    @pytest.mark.parametrize(
        "action_type",
        list(REVIEW_ACTION_TYPES),
        ids=list(REVIEW_ACTION_TYPES),
    )
    async def test_a_task_review_is_preflighted(self, action_type: str) -> None:
        review_gate = mock_of[ReviewGateService](
            check_can_decide=AsyncMock(return_value=mock_of[Task]()),
        )

        await _run_review_gate_preflight(
            _app_state(review_gate=review_gate),
            "approval-1",
            self._item(action_type),
            decided_by="bob",
        )

        review_gate.check_can_decide.assert_awaited_once()

    @pytest.mark.parametrize(
        "action_type",
        [CLARIFY_ACTION_TYPE, DECISION_ACTION_TYPE, PLAN_APPROVAL_ACTION_TYPE],
        ids=["clarify_question", "project_decision", "plan_approval"],
    )
    async def test_everything_else_that_names_a_task_is_left_alone(
        self, action_type: str
    ) -> None:
        review_gate = mock_of[ReviewGateService](
            check_can_decide=AsyncMock(return_value=mock_of[Task]()),
        )

        await _run_review_gate_preflight(
            _app_state(review_gate=review_gate),
            "approval-1",
            self._item(action_type),
            decided_by="bob",
        )

        review_gate.check_can_decide.assert_not_called()


class TestTryReviewGateTransition:
    """try_review_gate_transition maps engine errors to API errors.

    Regression guard for the narrow-exception-handling refactor: each
    typed engine error must surface as the correct HTTP status code.
    Anything else (e.g., RuntimeError) propagates to the caller
    instead of being silently swallowed as 200 OK.
    """

    async def test_passes_approval_id_to_service(self) -> None:
        """approval_id is threaded through for audit cross-reference."""
        review_gate = mock_of[ReviewGateService]()
        review_gate.dispatch_completion = AsyncMock()

        await try_review_gate_transition(
            review_gate,
            "approval-42",
            "task-1",
            approved=True,
            decided_by="bob",
            decision_reason=None,
        )
        review_gate.dispatch_completion.assert_awaited_once_with(
            task_id="task-1",
            approved=True,
            decided_by="bob",
            reason=None,
            approval_id="approval-42",
        )

    async def test_self_review_race_raises_forbidden(self) -> None:
        """Late SelfReviewError (reassignment between preflight and transition)."""
        review_gate = mock_of[ReviewGateService]()
        review_gate.dispatch_completion = AsyncMock(
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

    async def test_task_version_conflict_preserves_retryable_409(self) -> None:
        """Re-raised as TaskVersionConflictError: 409, retryable, redacted."""
        review_gate = mock_of[ReviewGateService]()
        review_gate.dispatch_completion = AsyncMock(
            side_effect=TaskVersionConflictError("Version 3 != 2"),
        )

        with pytest.raises(TaskVersionConflictError) as exc_info:
            await try_review_gate_transition(
                review_gate,
                "approval-1",
                "task-1",
                approved=True,
                decided_by="bob",
                decision_reason=None,
            )
        # Discriminating code + retryable preserved; internal detail redacted.
        assert exc_info.value.retryable is True
        assert exc_info.value.status_code == 409
        assert "Version 3 != 2" not in str(exc_info.value)
        assert "task-1" not in str(exc_info.value)

    async def test_task_not_found_preserves_code_404(self) -> None:
        """Re-raised as TaskNotFoundError (still a NotFoundError), redacted."""
        review_gate = mock_of[ReviewGateService]()
        review_gate.dispatch_completion = AsyncMock(
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
        assert isinstance(exc_info.value, TaskNotFoundError)
        assert "task-xyz" not in str(exc_info.value)

    async def test_invalid_edge_mutation_raises_conflict(self) -> None:
        """A base TaskMutationError (invalid edge) surfaces as a redacted 409."""
        review_gate = mock_of[ReviewGateService]()
        review_gate.dispatch_completion = AsyncMock(
            side_effect=TaskMutationError("task 'task-1' not in IN_REVIEW"),
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
        assert "task-1" not in str(exc_info.value)

    async def test_task_internal_error_propagates_500(self) -> None:
        """TaskInternalError propagates as its faithful 500 ENGINE_ERROR."""
        review_gate = mock_of[ReviewGateService]()
        review_gate.dispatch_completion = AsyncMock(
            side_effect=TaskInternalError("Persistence backend offline"),
        )

        with pytest.raises(TaskInternalError):
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

    async def test_logs_warning_when_no_channels_plugin(self) -> None:
        from synthorg.api.ws_models import WsEventType

        request = _make_request()
        request.app.plugins = []  # No ChannelsPlugin
        item = _make_pending_item()
        # Should not raise -- best-effort
        await _publish_approval_event(
            request,
            make_app_state(),
            WsEventType.APPROVAL_SUBMITTED,
            item,
        )

    async def test_publishes_when_plugin_available(self) -> None:
        from litestar.channels import ChannelsPlugin

        from synthorg.api.ws_models import WsEventType

        plugin = MagicMock(spec=ChannelsPlugin)
        request = _make_request()
        request.app.plugins = [plugin]
        item = _make_pending_item()

        await _publish_approval_event(
            request,
            make_app_state(),
            WsEventType.APPROVAL_SUBMITTED,
            item,
        )
        plugin.publish.assert_called_once()

    async def test_logs_warning_when_publish_fails(self) -> None:
        from litestar.channels import ChannelsPlugin

        from synthorg.api.ws_models import WsEventType

        plugin = MagicMock(spec=ChannelsPlugin)
        plugin.publish.side_effect = RuntimeError("not started")
        request = _make_request()
        request.app.plugins = [plugin]
        item = _make_pending_item()

        # Should not raise -- best-effort
        await _publish_approval_event(
            request,
            make_app_state(),
            WsEventType.APPROVAL_SUBMITTED,
            item,
        )
