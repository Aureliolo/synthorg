"""Unit tests for ReviewGateService -- IN_REVIEW task transitions."""

from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.enums import DecisionOutcome
from synthorg.core.task import AcceptanceCriterion, Task
from synthorg.core.task_enums import Priority, Stakes, TaskStatus, TaskType
from synthorg.engine.decisions import DecisionRecord
from synthorg.engine.errors import SelfReviewError
from synthorg.engine.review_gate import ReviewGateService
from synthorg.engine.review_gate_inputs import DeliverableReviewInputBuilder
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.task_engine_models import TaskMutationResult
from synthorg.observability.background_tasks import BackgroundTaskRegistry
from synthorg.persistence.decision_protocol import DecisionRepository
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.security.redteam.models import (
    RedTeamGateResult,
    RedTeamReport,
    RedTeamReviewInput,
    RedTeamVerdict,
)
from synthorg.security.redteam.protocol import RedTeamGate
from tests._shared import as_uuid, mock_of, sid

pytestmark = pytest.mark.unit


def _make_mock_task_engine(
    return_value: TaskMutationResult | None = None,
    *,
    task: Task | None = None,
) -> MagicMock:
    """Build a mock TaskEngine with configurable submit behavior."""
    return cast(
        MagicMock,
        mock_of[TaskEngine](
            submit=AsyncMock(
                return_value=return_value
                or TaskMutationResult(
                    request_id="test",
                    success=True,
                    version=1,
                ),
            ),
            get_task=AsyncMock(return_value=task),
        ),
    )


def _make_mock_decision_repo(
    existing: tuple[DecisionRecord, ...] = (),
) -> MagicMock:
    """Build a mock DecisionRepository.

    ``append_with_next_version`` echoes the kwargs back as a record so
    tests can inspect the arguments the service passed in.
    """

    def _next_version_for(task_id: str) -> int:
        return (
            max(
                (r.version for r in existing if r.task_id == task_id),
                default=0,
            )
            + 1
        )

    async def _append(**kwargs: object) -> DecisionRecord:
        version = _next_version_for(str(kwargs["task_id"]))
        return DecisionRecord(
            id=str(kwargs["record_id"]),
            task_id=str(kwargs["task_id"]),
            approval_id=kwargs["approval_id"],  # type: ignore[arg-type]
            executing_agent_id=str(kwargs["executing_agent_id"]),
            reviewer_agent_id=str(kwargs["reviewer_agent_id"]),
            decision=kwargs["decision"],  # type: ignore[arg-type]
            reason=kwargs["reason"],  # type: ignore[arg-type]
            criteria_snapshot=kwargs["criteria_snapshot"],  # type: ignore[arg-type]
            recorded_at=kwargs["recorded_at"],  # type: ignore[arg-type]
            version=version,
        )

    return cast(
        MagicMock,
        mock_of[DecisionRepository](
            append_with_next_version=AsyncMock(side_effect=_append),
            list_by_task=AsyncMock(return_value=existing),
            get=AsyncMock(return_value=None),
            list_by_agent=AsyncMock(return_value=()),
        ),
    )


def _make_mock_persistence(repo: MagicMock) -> MagicMock:
    """Build a mock PersistenceBackend exposing the decision/identity repos.

    Each call builds a dedicated autospec instance and attaches the repo
    directly to it.  Using ``type(persistence).decision_records =
    PropertyMock(...)`` would mutate a class-level descriptor shared
    across parallel tests and cause cross-test coupling under
    pytest-xdist; per-instance attribute assignment keeps each fake
    isolated.
    """
    identity_versions = AsyncMock()
    identity_versions.get_latest_version.return_value = None
    return cast(
        MagicMock,
        mock_of[PersistenceBackend](
            decision_records=repo,
            identity_versions=identity_versions,
        ),
    )


def _make_task(
    *,
    task_id: str = "task-1",
    assigned_to: str | None = "alice",
    criteria: tuple[str, ...] = ("Login works", "Tests pass"),
    status: TaskStatus = TaskStatus.IN_REVIEW,
    stakes: Stakes = Stakes.NORMAL,
) -> Task:
    """Build a Task with configurable fields."""
    return Task(
        id=as_uuid(task_id),
        title="Test task",
        description="Test task description",
        type=TaskType.DEVELOPMENT,
        priority=Priority.HIGH,
        project="proj-1",
        created_by="manager",
        assigned_to=assigned_to,
        status=status,
        stakes=stakes,
        acceptance_criteria=tuple(AcceptanceCriterion(description=c) for c in criteria),
    )


class TestReviewGateServiceApprove:
    """Tests for the approve flow."""

    async def test_approve_transitions_to_completed(self) -> None:
        """Approving a review syncs COMPLETED status to task engine."""
        task = _make_task()
        mock_te = _make_mock_task_engine(task=task)
        repo = _make_mock_decision_repo()
        service = ReviewGateService(
            task_engine=mock_te,
            persistence=_make_mock_persistence(repo),
        )

        await service.complete_review(
            task_id="task-1",
            requested_by="bob",
            approved=True,
            decided_by="bob",
        )

        mock_te.submit.assert_awaited_once()
        mutation = mock_te.submit.call_args.args[0]
        assert mutation.target_status == TaskStatus.COMPLETED
        assert "approved" in mutation.reason.lower()
        assert "bob" in mutation.reason

    async def test_reject_transitions_to_in_progress(self) -> None:
        """Rejecting a review syncs IN_PROGRESS status to task engine."""
        task = _make_task()
        mock_te = _make_mock_task_engine(task=task)
        repo = _make_mock_decision_repo()
        service = ReviewGateService(
            task_engine=mock_te, persistence=_make_mock_persistence(repo)
        )

        await service.complete_review(
            task_id="task-1",
            requested_by="bob",
            approved=False,
            decided_by="bob",
            reason="needs rework on error handling",
        )

        mock_te.submit.assert_awaited_once()
        mutation = mock_te.submit.call_args.args[0]
        assert mutation.target_status == TaskStatus.IN_PROGRESS
        assert "rejected" in mutation.reason.lower()
        assert "needs rework on error handling" in mutation.reason

    async def test_reject_without_reason(self) -> None:
        """Rejecting without a reason still works."""
        task = _make_task()
        mock_te = _make_mock_task_engine(task=task)
        repo = _make_mock_decision_repo()
        service = ReviewGateService(
            task_engine=mock_te, persistence=_make_mock_persistence(repo)
        )

        await service.complete_review(
            task_id="task-1",
            requested_by="bob",
            approved=False,
            decided_by="bob",
        )

        mutation = mock_te.submit.call_args.args[0]
        assert mutation.target_status == TaskStatus.IN_PROGRESS
        assert "None" not in mutation.reason


class TestReviewGateServiceSelfReview:
    """Tests for self-review prevention."""

    async def test_self_review_raises(self) -> None:
        """When decided_by == task.assigned_to, SelfReviewError is raised."""
        task = _make_task(assigned_to="alice")
        mock_te = _make_mock_task_engine(task=task)
        repo = _make_mock_decision_repo()
        service = ReviewGateService(
            task_engine=mock_te, persistence=_make_mock_persistence(repo)
        )

        with pytest.raises(SelfReviewError) as exc_info:
            await service.complete_review(
                task_id="task-1",
                requested_by="alice",
                approved=True,
                decided_by="alice",
            )

        assert exc_info.value.task_id == sid("task-1")
        assert exc_info.value.agent_id == "alice"
        # No transition should have been attempted
        mock_te.submit.assert_not_awaited()
        repo.append_with_next_version.assert_not_awaited()

    async def test_different_reviewer_allowed(self) -> None:
        """When decided_by != task.assigned_to, review proceeds normally."""
        task = _make_task(assigned_to="alice")
        mock_te = _make_mock_task_engine(task=task)
        repo = _make_mock_decision_repo()
        service = ReviewGateService(
            task_engine=mock_te, persistence=_make_mock_persistence(repo)
        )

        await service.complete_review(
            task_id="task-1",
            requested_by="bob",
            approved=True,
            decided_by="bob",
        )

        mock_te.submit.assert_awaited_once()

    async def test_task_not_found_raises(self) -> None:
        """When task does not exist, the review cannot complete."""
        mock_te = _make_mock_task_engine(task=None)
        repo = _make_mock_decision_repo()
        service = ReviewGateService(
            task_engine=mock_te, persistence=_make_mock_persistence(repo)
        )

        from synthorg.engine.errors import TaskNotFoundError

        with pytest.raises(TaskNotFoundError):
            await service.complete_review(
                task_id="task-nonexistent",
                requested_by="bob",
                approved=True,
                decided_by="bob",
            )

    async def test_task_without_assignee_proceeds_and_skips_decision_record(
        self,
    ) -> None:
        """When task.assigned_to is None, the state transition runs but
        no decision record is persisted.

        The ``Task`` model itself rejects ``assigned_to=None`` for
        statuses that require an assignee (IN_REVIEW, IN_PROGRESS), so
        the only way to reach the unassigned-executor defensive code
        in ``_record_decision`` is through a ``CREATED`` task that
        somehow gets routed through the review gate.  This test
        exercises that defensive branch and asserts the new invariant:
        the service refuses to write an audit row for an unassigned
        executor rather than jamming a sentinel string through the
        ``NotBlankStr`` ``executing_agent_id`` field.
        """
        task = _make_task(assigned_to=None, status=TaskStatus.CREATED)
        mock_te = _make_mock_task_engine(task=task)
        repo = _make_mock_decision_repo()
        service = ReviewGateService(
            task_engine=mock_te, persistence=_make_mock_persistence(repo)
        )

        # Should not raise (no assignee to enforce self-review against)
        await service.complete_review(
            task_id="task-1",
            requested_by="bob",
            approved=True,
            decided_by="bob",
        )
        mock_te.submit.assert_awaited_once()
        # No audit record is persisted for unassigned tasks.
        repo.append_with_next_version.assert_not_awaited()

    async def test_no_persistence_preflight_still_enforces_self_review(
        self,
    ) -> None:
        """Preflight still runs when persistence is None.

        Gating ``ReviewGateService`` construction on persistence
        would disable the self-review / missing-task fail-fast in
        task-engine-only deployments.
        """
        task = _make_task(assigned_to="alice")
        mock_te = _make_mock_task_engine(task=task)
        service = ReviewGateService(task_engine=mock_te, persistence=None)

        with pytest.raises(SelfReviewError):
            await service.check_can_decide(task_id="task-1", decided_by="alice")

    async def test_no_persistence_complete_review_skips_decision_record(
        self,
    ) -> None:
        """With persistence=None, complete_review transitions but skips audit."""
        task = _make_task(assigned_to="alice")
        mock_te = _make_mock_task_engine(task=task)
        service = ReviewGateService(task_engine=mock_te, persistence=None)

        await service.complete_review(
            task_id="task-1",
            requested_by="bob",
            approved=True,
            decided_by="bob",
        )
        mock_te.submit.assert_awaited_once()


class TestReviewGateServiceDecisionRecording:
    """Tests for decision record append on complete_review."""

    async def test_approve_records_decision(self) -> None:
        """Approving appends a DecisionRecord with APPROVED outcome."""
        task = _make_task()
        mock_te = _make_mock_task_engine(task=task)
        repo = _make_mock_decision_repo()
        service = ReviewGateService(
            task_engine=mock_te, persistence=_make_mock_persistence(repo)
        )

        await service.complete_review(
            task_id="task-1",
            requested_by="bob",
            approved=True,
            decided_by="bob",
        )

        repo.append_with_next_version.assert_awaited_once()
        kwargs = repo.append_with_next_version.call_args.kwargs
        assert kwargs["task_id"] == sid("task-1")
        assert kwargs["executing_agent_id"] == "alice"
        assert kwargs["reviewer_agent_id"] == "bob"
        assert kwargs["decision"] is DecisionOutcome.APPROVED

    async def test_reject_records_decision(self) -> None:
        """Rejecting appends a DecisionRecord with REJECTED outcome."""
        task = _make_task()
        mock_te = _make_mock_task_engine(task=task)
        repo = _make_mock_decision_repo()
        service = ReviewGateService(
            task_engine=mock_te, persistence=_make_mock_persistence(repo)
        )

        await service.complete_review(
            task_id="task-1",
            requested_by="bob",
            approved=False,
            decided_by="bob",
            reason="needs rework",
        )

        kwargs = repo.append_with_next_version.call_args.kwargs
        assert kwargs["decision"] is DecisionOutcome.REJECTED
        assert kwargs["reason"] == "needs rework"

    async def test_gate_block_after_approval_records_gate_reason(self) -> None:
        """A gate that reworks a human-approved task records the gate reason.

        Regression: ``run_completion_gates`` rewrites ``transition_reason``
        with the block summary when it reroutes an approved task to rework,
        but the recorded ``DecisionRecord.reason`` used to keep the stale
        human note (``None`` here). The audit row then drifted from the
        task-history transition. The decision reason must track the gate's
        block reason, matching what the pipeline path already records.
        """
        # HIGH stakes so the stakes-gated red-team review fires (the gate is
        # reserved for work at or above the configured red_team_min_stakes).
        task = _make_task(stakes=Stakes.HIGH)
        mock_te = _make_mock_task_engine(task=task)
        repo = _make_mock_decision_repo()
        block_result = RedTeamGateResult(
            verdict=RedTeamVerdict.BLOCK,
            report=RedTeamReport(
                execution_id="exec-1",
                task_id="task-1",
                summary="HIGH: hardcoded credential in deliverable.",
            ),
            elapsed_seconds=0.0,
        )
        review_input = RedTeamReviewInput(
            task_id="task-1",
            execution_id="exec-1",
            deliverable_content="Service complete.",
            acceptance_criteria=("Login works",),
            assigned_agent_id="alice",
            autonomy=AutonomyLevel.SUPERVISED,
        )
        service = ReviewGateService(
            task_engine=mock_te,
            persistence=_make_mock_persistence(repo),
            red_team_gate=mock_of[RedTeamGate](
                evaluate=AsyncMock(return_value=block_result),
            ),
            red_team_input_builder=mock_of[DeliverableReviewInputBuilder](
                build=AsyncMock(return_value=review_input),
            ),
        )

        await service.complete_review(
            task_id="task-1",
            requested_by="bob",
            approved=True,
            decided_by="bob",
        )

        expected_reason = (
            "Red-team review blocked completion: "
            "HIGH: hardcoded credential in deliverable."
        )
        mutation = mock_te.submit.call_args.args[0]
        assert mutation.target_status == TaskStatus.IN_PROGRESS
        assert mutation.reason == expected_reason
        kwargs = repo.append_with_next_version.call_args.kwargs
        assert kwargs["decision"] is DecisionOutcome.REJECTED
        assert kwargs["reason"] == expected_reason

    async def test_decision_includes_criteria_snapshot(self) -> None:
        """Decision record includes deduped acceptance-criteria descriptions.

        ``Task.acceptance_criteria`` does not enforce uniqueness, but
        ``DecisionRecord.criteria_snapshot`` rejects duplicates via
        the unique-strings validator.  The service de-dupes at the
        boundary while preserving original order; this regression
        test pins that behavior so a future refactor that drops the
        dedup step does not silently surface as a ValidationError
        propagating out of ``complete_review``.

        Blank / whitespace-only descriptions are not tested here
        because ``AcceptanceCriterion.description`` is a
        ``NotBlankStr`` and rejects them at Task construction time;
        the ``.strip()`` guard in ``_record_decision`` is defensive
        against an unreachable state.
        """
        task = _make_task(
            criteria=("JWT login", "JWT login", "Refresh works", "Refresh works")
        )
        mock_te = _make_mock_task_engine(task=task)
        repo = _make_mock_decision_repo()
        service = ReviewGateService(
            task_engine=mock_te, persistence=_make_mock_persistence(repo)
        )

        await service.complete_review(
            task_id="task-1",
            requested_by="bob",
            approved=True,
            decided_by="bob",
        )

        kwargs = repo.append_with_next_version.call_args.kwargs
        # Deduped, order preserved from first occurrence.
        assert kwargs["criteria_snapshot"] == ("JWT login", "Refresh works")

    async def test_decision_version_assigned_by_repository(self) -> None:
        """Version is server-assigned via append_with_next_version.

        The service does not compute the version itself (TOCTOU-safe);
        the repository returns a record with the persisted version.
        The mock repo echoes ``max(existing) + 1``.
        """
        existing = (
            DecisionRecord(
                id="d-1",
                task_id="task-1",
                executing_agent_id="alice",
                reviewer_agent_id="carol",
                decision=DecisionOutcome.REJECTED,
                recorded_at=datetime(2026, 4, 4, 10, 0, tzinfo=UTC),
                version=1,
            ),
        )
        task = _make_task()
        mock_te = _make_mock_task_engine(task=task)
        repo = _make_mock_decision_repo(existing=existing)
        service = ReviewGateService(
            task_engine=mock_te, persistence=_make_mock_persistence(repo)
        )

        await service.complete_review(
            task_id="task-1",
            requested_by="bob",
            approved=True,
            decided_by="bob",
        )

        # The repo returned the echo record; the service does not need
        # to inspect it, but we verify the call was made.
        repo.append_with_next_version.assert_awaited_once()

    async def test_decision_record_failure_is_non_fatal(self) -> None:
        """Known persistence errors don't unwind the review transition.

        Narrowed to ``QueryError`` / ``DuplicateRecordError`` -- these
        are the transient audit-write failures the service treats as
        non-fatal.  Programming errors (ValidationError, TypeError)
        still propagate so schema drift surfaces loudly.
        """
        from synthorg.core.persistence_errors import QueryError

        task = _make_task()
        mock_te = _make_mock_task_engine(task=task)
        repo = _make_mock_decision_repo()
        repo.append_with_next_version = AsyncMock(
            side_effect=QueryError("disk full"),
        )
        service = ReviewGateService(
            task_engine=mock_te, persistence=_make_mock_persistence(repo)
        )

        # Should NOT raise
        await service.complete_review(
            task_id="task-1",
            requested_by="bob",
            approved=True,
            decided_by="bob",
        )
        # Transition still happened
        mock_te.submit.assert_awaited_once()

    async def test_decision_record_programming_error_propagates(self) -> None:
        """Programming errors in the audit append MUST propagate.

        Regression guard: narrowing the except clause in
        ``_record_decision`` must not silently swallow
        ``ValidationError`` / ``TypeError`` / ``AttributeError``
        from schema drift or a broken ``DecisionRecord`` constructor.
        """
        task = _make_task()
        mock_te = _make_mock_task_engine(task=task)
        repo = _make_mock_decision_repo()
        repo.append_with_next_version = AsyncMock(
            side_effect=TypeError("unexpected keyword argument 'bogus'"),
        )
        service = ReviewGateService(
            task_engine=mock_te, persistence=_make_mock_persistence(repo)
        )

        with pytest.raises(TypeError, match="bogus"):
            await service.complete_review(
                task_id="task-1",
                requested_by="bob",
                approved=True,
                decided_by="bob",
            )


class _RecordingReceiptSeam:
    """Structural ``DeliverableReceiptSeam`` double for the build seam.

    Records the tasks it was asked to build a receipt for and, when
    ``error`` is set, raises it to exercise the non-fatal failure path.
    """

    def __init__(self, *, error: Exception | None = None) -> None:
        self.calls: list[Task] = []
        self._error = error

    async def build_and_store(self, *, task: Task) -> object:
        """Record the task and optionally raise the configured error."""
        self.calls.append(task)
        if self._error is not None:
            raise self._error
        return object()


class TestReviewGateServiceReceiptSeam:
    """Tests for the deliverable-receipt build seam on completion."""

    async def test_completed_transition_triggers_receipt_build(self) -> None:
        """Approving a review builds a receipt for the completed task."""
        task = _make_task()
        mock_te = _make_mock_task_engine(task=task)
        repo = _make_mock_decision_repo()
        seam = _RecordingReceiptSeam()
        service = ReviewGateService(
            task_engine=mock_te,
            persistence=_make_mock_persistence(repo),
            receipt_service=seam,
        )

        await service.complete_review(
            task_id="task-1",
            requested_by="bob",
            approved=True,
            decided_by="bob",
        )

        assert len(seam.calls) == 1
        assert seam.calls[0].id == as_uuid("task-1")

    async def test_rejection_does_not_build_receipt(self) -> None:
        """Rejecting a review (IN_PROGRESS) never builds a receipt."""
        task = _make_task()
        mock_te = _make_mock_task_engine(task=task)
        repo = _make_mock_decision_repo()
        seam = _RecordingReceiptSeam()
        service = ReviewGateService(
            task_engine=mock_te,
            persistence=_make_mock_persistence(repo),
            receipt_service=seam,
        )

        await service.complete_review(
            task_id="task-1",
            requested_by="bob",
            approved=False,
            decided_by="bob",
            reason="needs rework",
        )

        assert seam.calls == []

    async def test_receipt_build_failure_does_not_roll_back_transition(
        self,
    ) -> None:
        """A receipt-build failure is swallowed; the transition stands.

        The COMPLETED transition has already committed before the seam
        fires, so a half-built provenance bundle must never propagate an
        exception that would surface to the approver as a failed review.
        """
        task = _make_task()
        mock_te = _make_mock_task_engine(task=task)
        repo = _make_mock_decision_repo()
        seam = _RecordingReceiptSeam(error=RuntimeError("builder exploded"))
        service = ReviewGateService(
            task_engine=mock_te,
            persistence=_make_mock_persistence(repo),
            receipt_service=seam,
        )

        # Must NOT raise despite the seam raising.
        await service.complete_review(
            task_id="task-1",
            requested_by="bob",
            approved=True,
            decided_by="bob",
        )

        # The transition committed and the seam was invoked exactly once.
        mock_te.submit.assert_awaited_once()
        mutation = mock_te.submit.call_args.args[0]
        assert mutation.target_status == TaskStatus.COMPLETED
        assert len(seam.calls) == 1

    async def test_set_receipt_service_attaches_seam_post_construction(
        self,
    ) -> None:
        """``set_receipt_service`` wires the seam after construction."""
        task = _make_task()
        mock_te = _make_mock_task_engine(task=task)
        repo = _make_mock_decision_repo()
        seam = _RecordingReceiptSeam()
        service = ReviewGateService(
            task_engine=mock_te,
            persistence=_make_mock_persistence(repo),
        )
        service.set_receipt_service(seam)

        await service.complete_review(
            task_id="task-1",
            requested_by="bob",
            approved=True,
            decided_by="bob",
        )

        assert len(seam.calls) == 1


class TestDispatchCompletion:
    """Tests for ``dispatch_completion`` inline-vs-background routing."""

    async def test_runs_inline_without_background_registry(self) -> None:
        """A gated approve with no background registry completes inline.

        ``dispatch_completion`` backgrounds a gated approve only when BOTH
        a red-team gate AND a background registry are attached. With the
        gate set but no registry (a boot that never wired one) the
        completion must run inline and the method must report ``False`` so
        the caller awaits the transition rather than expecting a deferred
        one.
        """
        task = _make_task()
        mock_te = _make_mock_task_engine(task=task)
        repo = _make_mock_decision_repo()
        service = ReviewGateService(
            task_engine=mock_te,
            persistence=_make_mock_persistence(repo),
            red_team_gate=mock_of[RedTeamGate](evaluate=AsyncMock()),
        )

        dispatched = await service.dispatch_completion(
            task_id="task-1",
            requested_by="bob",
            approved=True,
            decided_by="bob",
        )

        assert dispatched is False
        mock_te.submit.assert_awaited_once()
        mutation = mock_te.submit.call_args.args[0]
        assert mutation.target_status == TaskStatus.COMPLETED

    async def test_rejection_runs_inline_even_with_full_wiring(self) -> None:
        """A rejection is never backgrounded, even with gate + registry wired.

        The background path is reserved for gated *approvals* (the inline
        AgentEngine latency the registry hides). A reject has no gate to
        run, so it must complete inline and report ``False`` regardless of
        whether a registry is attached.
        """
        task = _make_task()
        mock_te = _make_mock_task_engine(task=task)
        repo = _make_mock_decision_repo()
        registry = mock_of[BackgroundTaskRegistry](spawn=MagicMock())
        service = ReviewGateService(
            task_engine=mock_te,
            persistence=_make_mock_persistence(repo),
            red_team_gate=mock_of[RedTeamGate](evaluate=AsyncMock()),
            background_tasks=registry,
        )

        dispatched = await service.dispatch_completion(
            task_id="task-1",
            requested_by="bob",
            approved=False,
            decided_by="bob",
            reason="missing tests",
        )

        assert dispatched is False
        registry.spawn.assert_not_called()
        mock_te.submit.assert_awaited_once()
        assert mock_te.submit.call_args.args[0].target_status == TaskStatus.IN_PROGRESS

    async def test_below_stakes_approve_runs_inline_despite_full_wiring(
        self,
    ) -> None:
        """A below-threshold approve runs inline even with gate + registry.

        The inline red-team evaluation only fires when the task's stakes
        meet the configured ``red_team_min_stakes`` (default HIGH). A
        NORMAL-stakes approve skips the gate, so backgrounding it would
        defer a no-op; ``dispatch_completion`` instead runs it inline and
        reports ``False`` rather than spawning a background task.
        """
        task = _make_task(stakes=Stakes.NORMAL)
        mock_te = _make_mock_task_engine(task=task)
        repo = _make_mock_decision_repo()
        registry = mock_of[BackgroundTaskRegistry](spawn=MagicMock())
        service = ReviewGateService(
            task_engine=mock_te,
            persistence=_make_mock_persistence(repo),
            red_team_gate=mock_of[RedTeamGate](evaluate=AsyncMock()),
            background_tasks=registry,
        )

        dispatched = await service.dispatch_completion(
            task_id="task-1",
            requested_by="bob",
            approved=True,
            decided_by="bob",
        )

        assert dispatched is False
        registry.spawn.assert_not_called()
        mock_te.submit.assert_awaited_once()
        assert mock_te.submit.call_args.args[0].target_status == TaskStatus.COMPLETED
