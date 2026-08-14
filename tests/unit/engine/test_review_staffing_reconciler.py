"""Unit tests for the review-staffing sweep.

The sweep is the only thing that ever releases a gate-unstaffed park, so its
two answers (a holder exists, or one does not) are what these pin down.
"""

from datetime import date
from unittest.mock import AsyncMock

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.project import Project
from synthorg.core.role_catalog import (
    COMPLETION_REVIEWER_ROLE_NAME,
    RED_TEAM_ROLE_NAME,
)
from synthorg.core.task import Task
from synthorg.core.task_enums import (
    BlockedReason,
    Complexity,
    Stakes,
    TaskStatus,
    TaskType,
)
from synthorg.core.types import CapabilityLevel, NotBlankStr
from synthorg.engine.errors import TaskMutationError
from synthorg.engine.review.pipeline import ReviewPipeline
from synthorg.engine.review_gate import ReviewGateService
from synthorg.engine.review_staffing_reconciler import ReviewStaffingReconciler
from synthorg.engine.task_engine import TaskEngine
from synthorg.hr.enums import AgentStatus, HiringRequestStatus
from synthorg.hr.hiring_service import HiringService
from synthorg.hr.registry import AgentRegistryService
from synthorg.hr.role_staffing import RoleStaffingService
from tests._shared import as_uuid, mock_of, sid
from tests._shared.model_binding import bound_ref, model_ref_resolver
from tests.unit.api.fakes import FakeProjectRepository, FakeTaskRepository

pytestmark = pytest.mark.unit

_EXECUTOR = str(as_uuid("executor-1"))
_PROJECT = sid("proj-1")


def _transition_double(
    *,
    return_value: tuple[Task, TaskStatus] | None = None,
    side_effect: Exception | None = None,
) -> AsyncMock:
    """Return a spec'd double for ``TaskEngine.transition_task``.

    Args:
        return_value: What the transition hands back on success.
        side_effect: Raised instead, for the refused-hop case.

    Returns:
        The configured double.
    """
    return AsyncMock(
        spec=TaskEngine.transition_task,
        return_value=return_value,
        side_effect=side_effect,
    )


def _identity(
    label: str,
    *,
    role: str,
    capability: CapabilityLevel | None = "capable",
    status: AgentStatus = AgentStatus.ACTIVE,
) -> AgentIdentity:
    return AgentIdentity(
        id=as_uuid(label),
        name=NotBlankStr(label),
        role=NotBlankStr(role),
        department=NotBlankStr("quality-assurance"),
        model=ModelConfig(
            provider=NotBlankStr("test-provider"),
            model_id=NotBlankStr("example-capable-001"),
            capability=capability,
        ),
        status=status,
        hiring_date=date(2026, 1, 1),
    )


def _parked(
    label: str,
    *,
    reason: BlockedReason = BlockedReason.REVIEWER_UNSTAFFED,
    assigned_to: str | None = _EXECUTOR,
) -> Task:
    return Task(
        id=as_uuid(label),
        title=NotBlankStr(f"Task {label}"),
        description=NotBlankStr(f"Work for {label}"),
        type=TaskType.DEVELOPMENT,
        project=NotBlankStr(_PROJECT),
        created_by=NotBlankStr("manager"),
        assigned_to=NotBlankStr(assigned_to) if assigned_to is not None else None,
        status=TaskStatus.BLOCKED,
        blocked_reason=reason,
        stakes=Stakes.NORMAL,
        estimated_complexity=Complexity.MEDIUM,
    )


async def _build(
    *,
    tasks: tuple[Task, ...],
    holders: tuple[AgentIdentity, ...] = (),
    with_hiring: bool = True,
    transition: AsyncMock | None = None,
    run_pipeline: AsyncMock | None = None,
) -> tuple[ReviewStaffingReconciler, HiringService | None, AsyncMock]:
    """Assemble a reconciler over in-memory collaborators.

    Args:
        tasks: Rows the parked backlog starts with.
        holders: Agents already on the roster.
        with_hiring: Whether a hiring pipeline is attached.
        transition: Override for the task engine's transition double.
        run_pipeline: Override for the review gate's re-judge double.

    Returns:
        The reconciler, its hiring pipeline (or ``None``), and the transition
        double so a test can assert on the hop it wrote.
    """
    task_repo = FakeTaskRepository()
    for task in tasks:
        await task_repo.save(task)
    project_repo = FakeProjectRepository()
    await project_repo.save(Project(id=as_uuid("proj-1"), name=NotBlankStr("Thing")))
    registry = AgentRegistryService()
    for holder in holders:
        await registry.register(holder)
    engine_transition = transition or _transition_double(
        return_value=(tasks[0], TaskStatus.BLOCKED)
    )
    hiring = (
        HiringService(
            registry=registry,
            approval_store=ApprovalStore(),
            config_resolver=model_ref_resolver(default=bound_ref()),
        )
        if with_hiring
        else None
    )
    rejudge = run_pipeline or AsyncMock(spec=ReviewGateService.run_pipeline)
    reconciler = ReviewStaffingReconciler(
        task_repo=task_repo,
        task_engine=mock_of[TaskEngine](transition_task=engine_transition),
        staffing=RoleStaffingService(registry=registry),
        review_gate=mock_of[ReviewGateService](run_pipeline=rejudge),
        review_pipeline=mock_of[ReviewPipeline](),
        project_repo=project_repo,
        hiring=(lambda: hiring) if hiring is not None else None,
        notifications=None,
    )
    return reconciler, hiring, engine_transition


class TestReleasing:
    async def test_a_park_with_a_holder_returns_to_review(self) -> None:
        reconciler, _, transition = await _build(
            tasks=(_parked("task-1"),),
            holders=(_identity("reviewer-1", role=COMPLETION_REVIEWER_ROLE_NAME),),
        )
        result = await reconciler.reconcile(trigger="test")
        assert result.released == 1
        assert result.still_parked == 0
        transition.assert_awaited_once()
        assert transition.await_args is not None
        assert transition.await_args.args[1] is TaskStatus.IN_REVIEW

    async def test_a_released_task_is_actually_re_judged(self) -> None:
        """Releasing without re-running the gates would strand the task.

        Nothing watches IN_REVIEW, and the hop clears ``blocked_reason``, so
        the task also leaves the only query this sweep runs. A release that
        did not ask the gates again would move work out of sight of every
        watcher it had and call that a heal.
        """
        rejudge = AsyncMock(spec=ReviewGateService.run_pipeline)
        reconciler, _, transition = await _build(
            tasks=(_parked("task-1"),),
            holders=(_identity("reviewer-1", role=COMPLETION_REVIEWER_ROLE_NAME),),
            run_pipeline=rejudge,
        )
        result = await reconciler.reconcile(trigger="test")
        assert result.released == 1
        transition.assert_awaited_once()
        rejudge.assert_awaited_once()
        assert rejudge.await_args is not None
        assert rejudge.await_args.kwargs["task_id"] == str(as_uuid("task-1"))

    async def test_a_failed_re_judge_keeps_the_release(self) -> None:
        """The hop already succeeded, so it is not undone by a review fault.

        The task waits for a human exactly as it would after an auto-review
        fault, which is a worse outcome than a clean re-judge but a better
        one than re-parking work that is genuinely ready to be judged.
        """
        rejudge = AsyncMock(
            spec=ReviewGateService.run_pipeline,
            side_effect=TaskMutationError("gate unavailable"),
        )
        reconciler, _, transition = await _build(
            tasks=(_parked("task-1"),),
            holders=(_identity("reviewer-1", role=COMPLETION_REVIEWER_ROLE_NAME),),
            run_pipeline=rejudge,
        )
        result = await reconciler.reconcile(trigger="test")
        assert result.released == 1
        transition.assert_awaited_once()

    async def test_a_park_with_no_holder_stays_parked(self) -> None:
        reconciler, _, transition = await _build(tasks=(_parked("task-1"),))
        result = await reconciler.reconcile(trigger="test")
        assert result.released == 0
        assert result.still_parked == 1
        transition.assert_not_awaited()

    async def test_a_park_naming_no_executor_stays_parked(self) -> None:
        """Nobody to exclude means the sweep cannot ask the gate's question.

        Substituting the task id would exclude nobody, so the sweep would
        read staffed what the gate is about to re-park, and the two would
        trade the task back and forth once per cadence forever.
        """
        reconciler, _, transition = await _build(
            tasks=(_parked("task-1", assigned_to=None),),
            holders=(_identity("reviewer-1", role=COMPLETION_REVIEWER_ROLE_NAME),),
        )
        result = await reconciler.reconcile(trigger="test")
        assert result.released == 0
        assert result.still_parked == 1
        transition.assert_not_awaited()

    async def test_the_executor_is_never_its_own_reviewer(self) -> None:
        """A solo assignee holding the role does not count as staffed."""
        executor_holds_it = _identity("executor-1", role=COMPLETION_REVIEWER_ROLE_NAME)
        reconciler, _, transition = await _build(
            tasks=(_parked("task-1"),), holders=(executor_holds_it,)
        )
        result = await reconciler.reconcile(trigger="test")
        assert result.still_parked == 1
        transition.assert_not_awaited()

    async def test_an_inactive_holder_does_not_count(self) -> None:
        reconciler, _, transition = await _build(
            tasks=(_parked("task-1"),),
            holders=(
                _identity(
                    "reviewer-1",
                    role=COMPLETION_REVIEWER_ROLE_NAME,
                    status=AgentStatus.ON_LEAVE,
                ),
            ),
        )
        result = await reconciler.reconcile(trigger="test")
        assert result.still_parked == 1
        transition.assert_not_awaited()

    async def test_a_human_escalation_is_never_touched(self) -> None:
        """``oracle_escalated`` waits on a person, not on staffing."""
        reconciler, _, transition = await _build(
            tasks=(_parked("task-1", reason=BlockedReason.ORACLE_ESCALATED),),
            holders=(_identity("reviewer-1", role=COMPLETION_REVIEWER_ROLE_NAME),),
        )
        result = await reconciler.reconcile(trigger="test")
        assert result.released == 0
        assert result.still_parked == 0
        transition.assert_not_awaited()

    async def test_each_park_waits_on_its_own_role(self) -> None:
        """A reviewer holder does not release a red-team park."""
        reconciler, _, transition = await _build(
            tasks=(
                _parked("task-1"),
                _parked("task-2", reason=BlockedReason.RED_TEAM_UNSTAFFED),
            ),
            holders=(_identity("reviewer-1", role=COMPLETION_REVIEWER_ROLE_NAME),),
        )
        result = await reconciler.reconcile(trigger="test")
        assert result.released == 1
        assert result.still_parked == 1
        assert transition.await_args is not None
        assert transition.await_args.args[0] == str(as_uuid("task-1"))

    async def test_a_red_team_holder_releases_a_red_team_park(self) -> None:
        reconciler, _, transition = await _build(
            tasks=(_parked("task-1", reason=BlockedReason.RED_TEAM_UNSTAFFED),),
            holders=(_identity("attacker-1", role=RED_TEAM_ROLE_NAME),),
        )
        result = await reconciler.reconcile(trigger="test")
        assert result.released == 1
        transition.assert_awaited_once()

    async def test_a_refused_hop_leaves_the_task_parked(self) -> None:
        """One contended row must not stop the rest of the sweep."""
        reconciler, _, transition = await _build(
            tasks=(_parked("task-1"),),
            holders=(_identity("reviewer-1", role=COMPLETION_REVIEWER_ROLE_NAME),),
            transition=_transition_double(
                side_effect=TaskMutationError("no longer blocked")
            ),
        )
        result = await reconciler.reconcile(trigger="test")
        assert result.released == 0
        assert result.still_parked == 1
        transition.assert_awaited_once()

    async def test_the_sweep_is_idempotent(self) -> None:
        """A second pass over the same world leaves it the same.

        The report differs by one field, and correctly so: the request the
        first pass opened is still open, so the second asks for nobody.
        """
        reconciler, _, transition = await _build(tasks=(_parked("task-1"),))
        first = await reconciler.reconcile(trigger="test")
        second = await reconciler.reconcile(trigger="test")
        assert (first.released, first.still_parked) == (0, 1)
        assert (second.released, second.still_parked) == (0, 1)
        assert (first.hires_requested, second.hires_requested) == (1, 0)
        transition.assert_not_awaited()


class TestHiring:
    async def test_an_unstaffed_role_opens_one_request(self) -> None:
        reconciler, hiring, _ = await _build(tasks=(_parked("task-1"),))
        assert hiring is not None
        result = await reconciler.reconcile(trigger="test")
        assert result.hires_requested == 1
        opened = hiring.find_in_flight_request_for_role(COMPLETION_REVIEWER_ROLE_NAME)
        assert opened is not None
        assert opened.status is HiringRequestStatus.PENDING
        assert opened.approval_id is not None

    async def test_a_second_pass_does_not_open_a_second_request(self) -> None:
        reconciler, hiring, _ = await _build(tasks=(_parked("task-1"),))
        assert hiring is not None
        await reconciler.reconcile(trigger="test")
        second = await reconciler.reconcile(trigger="test")
        assert second.hires_requested == 0
        pending = [
            r
            for r in hiring._requests.values()
            if r.status is HiringRequestStatus.PENDING
        ]
        assert len(pending) == 1

    async def test_a_staffed_role_asks_for_nobody(self) -> None:
        reconciler, hiring, _ = await _build(
            tasks=(_parked("task-1"),),
            holders=(_identity("reviewer-1", role=COMPLETION_REVIEWER_ROLE_NAME),),
        )
        assert hiring is not None
        result = await reconciler.reconcile(trigger="test")
        assert result.hires_requested == 0
        in_flight = hiring.find_in_flight_request_for_role(
            COMPLETION_REVIEWER_ROLE_NAME
        )
        assert in_flight is None

    async def test_without_a_pipeline_the_sweep_still_reports_the_gap(self) -> None:
        reconciler, hiring, _ = await _build(
            tasks=(_parked("task-1"),), with_hiring=False
        )
        assert hiring is None
        result = await reconciler.reconcile(trigger="test")
        assert result.still_parked == 1
        assert result.hires_requested == 0

    async def test_an_approved_request_is_finished_by_the_sweep(self) -> None:
        """The half-applied decision the approval flow can leave gets finished."""
        reconciler, hiring, _ = await _build(tasks=(_parked("task-1"),))
        assert hiring is not None
        request = await hiring.create_request(
            requested_by=NotBlankStr("operator"),
            department=NotBlankStr("quality-assurance"),
            role=NotBlankStr(COMPLETION_REVIEWER_ROLE_NAME),
            reason=NotBlankStr("Somebody has to review"),
        )
        with_candidate = await hiring.generate_candidate(request)
        submitted = await hiring.submit_for_approval(
            with_candidate, str(with_candidate.candidates[0].id)
        )
        await hiring.approve_request(str(submitted.id), decided_by="operator")

        result = await reconciler.reconcile(trigger="test")
        assert result.hires_completed == 1
        assert (
            hiring._requests[str(submitted.id)].status
            is HiringRequestStatus.INSTANTIATED
        )
