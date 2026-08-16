"""Unit tests for the review-staffing sweep.

The sweep is the only thing that ever releases a gate-unstaffed park, so its
two answers (a holder exists, or one does not) are what these pin down.
"""

from datetime import date
from unittest.mock import AsyncMock

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.domain_errors import ConflictError
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
from synthorg.engine.review_staffing.reconciler import ReviewStaffingReconciler
from synthorg.engine.task_engine import TaskEngine
from synthorg.hr.enums import AgentStatus, HiringRequestStatus
from synthorg.hr.errors import OnboardingError
from synthorg.hr.hiring_service import HiringService
from synthorg.hr.models import HiringRequest
from synthorg.hr.registry import AgentRegistryService
from synthorg.hr.role_staffing import RoleStaffingService
from synthorg.notifications.dispatcher import NotificationDispatcher
from tests._shared import as_uuid, mock_of, sid
from tests._shared.model_binding import bound_ref, model_ref_resolver
from tests._shared.staffing import roster_capability_policy
from tests.unit.api.fakes import FakeTaskRepository

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


async def _approved_request(hiring: HiringService, role: str) -> HiringRequest:
    """Drive a request all the way to a human's approval.

    Args:
        hiring: The pipeline to open the request through.
        role: The role being hired for.

    Returns:
        The submitted request, now APPROVED and awaiting instantiation.
    """
    request = await hiring.create_request(
        requested_by=NotBlankStr("operator"),
        department=NotBlankStr("quality-assurance"),
        role=NotBlankStr(role),
        reason=NotBlankStr(f"Somebody has to hold {role}"),
    )
    with_candidate = await hiring.generate_candidate(request)
    submitted = await hiring.submit_for_approval(
        with_candidate, str(with_candidate.candidates[0].id)
    )
    await hiring.approve_request(str(submitted.id), decided_by="operator")
    return submitted


def _status(hiring: HiringService, request: HiringRequest) -> HiringRequestStatus:
    """Return where *request* has got to.

    Returns:
        The tracked request's current status.
    """
    tracked = hiring.get_request(str(request.id))
    assert tracked is not None
    return tracked.status


async def _build(
    *,
    tasks: tuple[Task, ...],
    holders: tuple[AgentIdentity, ...] = (),
    with_hiring: bool = True,
    transition: AsyncMock | None = None,
    run_pipeline: AsyncMock | None = None,
    dispatch: AsyncMock | None = None,
    registry: AgentRegistryService | None = None,
) -> tuple[ReviewStaffingReconciler, HiringService | None, AsyncMock]:
    """Assemble a reconciler over in-memory collaborators.

    Args:
        tasks: Rows the parked backlog starts with.
        holders: Agents already on the roster.
        with_hiring: Whether a hiring pipeline is attached.
        transition: Override for the task engine's transition double.
        run_pipeline: Override for the review gate's re-judge double.
        dispatch: Double for the notification dispatcher's ``dispatch``, so a
            test can assert on what reached the operator.
        registry: A roster to build on, so a test can staff a role between
            passes and watch the reconciler notice.

    Returns:
        The reconciler, its hiring pipeline (or ``None``), and the transition
        double so a test can assert on the hop it wrote.
    """
    task_repo = FakeTaskRepository()
    for task in tasks:
        await task_repo.save(task)
    registry = registry if registry is not None else AgentRegistryService()
    for holder in holders:
        await registry.register(holder)
    engine_transition = transition or _transition_double(
        return_value=(tasks[0], TaskStatus.BLOCKED) if tasks else None
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
        staffing=RoleStaffingService(
            registry=registry,
            capability=roster_capability_policy(),
        ),
        review_gate=mock_of[ReviewGateService](run_pipeline=rejudge),
        review_pipeline=mock_of[ReviewPipeline](),
        hiring=(lambda: hiring) if hiring is not None else None,
        notifications=(
            None
            if dispatch is None
            else (lambda: mock_of[NotificationDispatcher](dispatch=dispatch))
        ),
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


class TestStandingGapAlert:
    """The gap is announced before any work needs the role.

    The hire path answers a park, so its alert cannot arrive until a task has
    already run and been paid for. An org whose roster holds nobody for a gate
    role cannot complete a single task, and that is knowable at boot.
    """

    @staticmethod
    def _titles(dispatch: AsyncMock) -> list[str]:
        return [str(call.args[0].title) for call in dispatch.await_args_list]

    async def test_an_empty_backlog_still_alerts_on_an_unstaffed_role(self) -> None:
        dispatch = AsyncMock()
        reconciler, _, transition = await _build(
            tasks=(), with_hiring=False, dispatch=dispatch
        )

        result = await reconciler.reconcile(trigger="test")

        # Nothing was parked, so the hire path had nothing to answer, and the
        # operator is told anyway.
        assert (result.released, result.still_parked, result.hires_requested) == (
            0,
            0,
            0,
        )
        transition.assert_not_awaited()
        assert self._titles(dispatch) == [
            f"No agent holds {COMPLETION_REVIEWER_ROLE_NAME}",
            f"No agent holds {RED_TEAM_ROLE_NAME}",
        ]

    async def test_the_alert_is_sent_once_per_gap(self) -> None:
        """A standing condition repeated every cadence trains dismissal."""
        dispatch = AsyncMock()
        reconciler, _, _ = await _build(tasks=(), with_hiring=False, dispatch=dispatch)

        await reconciler.reconcile(trigger="first")
        await reconciler.reconcile(trigger="second")

        assert self._titles(dispatch) == [
            f"No agent holds {COMPLETION_REVIEWER_ROLE_NAME}",
            f"No agent holds {RED_TEAM_ROLE_NAME}",
        ]

    async def test_staffing_the_role_re_arms_the_alert(self) -> None:
        """An agent later stood down produces a fresh alert, not silence."""
        dispatch = AsyncMock()
        registry = AgentRegistryService()
        reconciler, _, _ = await _build(
            tasks=(), with_hiring=False, dispatch=dispatch, registry=registry
        )
        await reconciler.reconcile(trigger="gap")

        reviewer = _identity("reviewer-1", role=COMPLETION_REVIEWER_ROLE_NAME)
        await registry.register(reviewer)
        await reconciler.reconcile(trigger="staffed")
        await registry.update_status(
            NotBlankStr(str(reviewer.id)), AgentStatus.TERMINATED
        )
        await reconciler.reconcile(trigger="gap-again")

        reviewer_alerts = [
            title
            for title in self._titles(dispatch)
            if title == f"No agent holds {COMPLETION_REVIEWER_ROLE_NAME}"
        ]
        assert len(reviewer_alerts) == 2

    async def test_a_staffed_role_is_never_alerted_on(self) -> None:
        dispatch = AsyncMock()
        reconciler, _, _ = await _build(
            tasks=(),
            with_hiring=False,
            dispatch=dispatch,
            holders=(
                _identity("reviewer-1", role=COMPLETION_REVIEWER_ROLE_NAME),
                _identity("red-1", role=RED_TEAM_ROLE_NAME),
            ),
        )

        await reconciler.reconcile(trigger="test")

        dispatch.assert_not_awaited()


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
        opened = hiring.find_in_flight_request_for_role(COMPLETION_REVIEWER_ROLE_NAME)
        assert opened is not None

        second = await reconciler.reconcile(trigger="test")

        # The counter is incremented exactly where a request is created, and
        # the same request is still the one in flight: a second approval item
        # for a decision the operator already has would be the failure here.
        assert second.hires_requested == 0
        still_open = hiring.find_in_flight_request_for_role(
            COMPLETION_REVIEWER_ROLE_NAME
        )
        assert still_open is not None
        assert still_open.id == opened.id

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
        submitted = await _approved_request(hiring, COMPLETION_REVIEWER_ROLE_NAME)

        result = await reconciler.reconcile(trigger="test")
        assert result.hires_completed == 1
        assert _status(hiring, submitted) is HiringRequestStatus.INSTANTIATED

    async def test_one_failed_instantiation_leaves_the_others_alone(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Both gate roles can be waiting on a hire at once.

        Letting one request's own condition abort the loop would cost the
        other role a whole cadence for a reason unrelated to it, and the
        operator already approved both.
        """
        reconciler, hiring, _ = await _build(tasks=(_parked("task-1"),))
        assert hiring is not None
        doomed = await _approved_request(hiring, COMPLETION_REVIEWER_ROLE_NAME)
        healthy = await _approved_request(hiring, RED_TEAM_ROLE_NAME)
        real_instantiate = hiring.instantiate_agent

        async def _one_fails(request: HiringRequest) -> AgentIdentity:
            """Fail the doomed request, hire the rest.

            Returns:
                The registered identity for every other request.

            Raises:
                OnboardingError: For the doomed request only.
            """
            if request.id == doomed.id:
                msg = "onboarding hit a registry outage"
                raise OnboardingError(msg)
            return await real_instantiate(request)

        monkeypatch.setattr(hiring, "instantiate_agent", _one_fails)

        result = await reconciler.reconcile(trigger="test")

        assert result.hires_completed == 1
        # The failed one keeps its approval so the next pass retries it,
        # rather than being marked done or re-approved from scratch.
        assert _status(hiring, doomed) is HiringRequestStatus.APPROVED
        assert _status(hiring, healthy) is HiringRequestStatus.INSTANTIATED

    async def test_a_refused_request_leaves_the_gap_visible(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Opening the hire is best-effort; the park it explains is not.

        Submitting writes an approval item, so a durable-store refusal
        arrives as a sibling of ``HRError`` rather than a subclass. Either
        way the sweep reports the task as still parked instead of failing
        the pass and costing the other role its release.
        """
        reconciler, hiring, _ = await _build(tasks=(_parked("task-1"),))
        assert hiring is not None

        async def _refuse(**_kwargs: object) -> HiringRequest:
            """Refuse to open the request.

            Raises:
                ConflictError: Always.
            """
            msg = "approval store rejected the item"
            raise ConflictError(msg)

        monkeypatch.setattr(hiring, "create_request", _refuse)

        result = await reconciler.reconcile(trigger="test")

        assert result.hires_requested == 0
        assert result.still_parked == 1
        in_flight = hiring.find_in_flight_request_for_role(
            COMPLETION_REVIEWER_ROLE_NAME
        )
        assert in_flight is None

    async def test_an_uncatalogued_role_asks_for_nobody(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A hire is described from the catalog, so an absent entry stops it.

        Guessing a department and a skill set would open an approval item
        the operator cannot evaluate, for a role the org does not define.
        """
        reconciler, hiring, _ = await _build(tasks=(_parked("task-1"),))
        assert hiring is not None
        monkeypatch.setattr(
            "synthorg.engine.review_staffing.reconciler.get_builtin_role",
            lambda _role: None,
        )

        result = await reconciler.reconcile(trigger="test")

        assert result.hires_requested == 0
        assert result.still_parked == 1
        in_flight = hiring.find_in_flight_request_for_role(
            COMPLETION_REVIEWER_ROLE_NAME
        )
        assert in_flight is None
