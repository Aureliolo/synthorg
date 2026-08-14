# module-kind: service
"""Un-park what a staffing gap parked, once the gap closes.

A completion gate that finds nobody holding its role fails closed and parks
the task under its own blocked reason. Nothing about that park is a decision
a human owes an answer to, so nothing will ever arrive to release it: an
agent can be given the role from the dashboard, hired through an approval, or
loaded from config at boot, and none of those announce themselves to the gate
that parked the work.

So this sweep asks, level-triggered, on a cadence: for each parked task, does
somebody hold the role it waits on now? The sweep only un-parks. Whether the
work actually passes is still the gate's decision, taken again from scratch
the moment the task is back in review.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.core.role_catalog import (
    COMPLETION_REVIEWER_ROLE_NAME,
    RED_TEAM_ROLE_NAME,
    get_builtin_role,
)
from synthorg.core.task import Task
from synthorg.core.task_enums import BlockedReason, TaskStatus
from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import TaskEngineError
from synthorg.engine.routing_policy.capability_ladder import required_capability_for
from synthorg.engine.task_engine import TaskEngine
from synthorg.hr.errors import HRError
from synthorg.hr.hiring_service import HiringService
from synthorg.hr.role_staffing import (
    RoleStaffingSelection,
    RoleStaffingService,
    load_project_for_selection,
)
from synthorg.notifications.dispatcher import NotificationDispatcher
from synthorg.notifications.models import (
    Notification,
    NotificationCategory,
    NotificationSeverity,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.review_staffing import (
    REVIEW_STAFFING_HIRE_ALREADY_OPEN,
    REVIEW_STAFFING_HIRE_COMPLETED,
    REVIEW_STAFFING_HIRE_COMPLETION_FAILED,
    REVIEW_STAFFING_HIRE_REQUEST_FAILED,
    REVIEW_STAFFING_HIRE_REQUESTED,
    REVIEW_STAFFING_PROJECT_READ_FAILED,
    REVIEW_STAFFING_SWEEP_COMPLETE,
    REVIEW_STAFFING_SWEEP_STARTED,
    REVIEW_STAFFING_TASK_RELEASE_FAILED,
    REVIEW_STAFFING_TASK_RELEASED,
    REVIEW_STAFFING_TASK_STILL_PARKED,
)
from synthorg.persistence.project_protocol import ProjectRepository
from synthorg.persistence.task_protocol import TaskFilterSpec, TaskRepository

logger = get_logger(__name__)

#: Which role each unstaffed park waits on. A park that could not name its
#: role would leave the sweep guessing, which is why the two gates carry
#: separate blocked reasons rather than one shared "a gate was unstaffed".
_ROLE_BY_REASON: Final[Mapping[BlockedReason, str]] = MappingProxyType(
    {
        BlockedReason.REVIEWER_UNSTAFFED: COMPLETION_REVIEWER_ROLE_NAME,
        BlockedReason.RED_TEAM_UNSTAFFED: RED_TEAM_ROLE_NAME,
    }
)

#: Rows read per query. A sweep walks every parked task, so it pages rather
#: than assuming the backlog fits one read.
_PAGE_SIZE: Final[int] = 100

#: Ceiling on pages per reason per pass. A pass is a periodic sweep, not a
#: drain: past this the remainder waits for the next one rather than holding
#: the loop open against a backlog that keeps growing.
_MAX_PAGES: Final[int] = 20

_ACTOR: Final[str] = "review-staffing-reconciler"
_RELEASE_REASON: Final[str] = (
    "The role this task's completion gate needs is staffed again; "
    "returning it for review."
)


class ReviewStaffingPass(BaseModel):
    """What one sweep did.

    Attributes:
        trigger: What ran the pass, so a periodic sweep and an event-driven
            one are told apart in the logs.
        released: Tasks moved back into review.
        still_parked: Tasks left parked because the role is still unheld.
        hires_requested: Roles a fresh hire request was opened for.
        hires_completed: Approved requests this pass finished instantiating.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    trigger: NotBlankStr = Field(description="What ran this pass")
    released: int = Field(default=0, ge=0, description="Tasks returned to review")
    still_parked: int = Field(default=0, ge=0, description="Tasks left parked")
    hires_requested: int = Field(default=0, ge=0, description="Hire requests opened")
    hires_completed: int = Field(default=0, ge=0, description="Approved hires finished")


class ReviewStaffingReconciler:
    """Releases gate-unstaffed parks once the role has a holder.

    Args:
        task_repo: Read side for the parked backlog.
        task_engine: Writes the release transition, so the hop goes through
            the same validation and audit trail as every other one.
        staffing: Answers whether an eligible holder exists for a task.
        project_repo: Reads the reviewed project, so the sweep asks the same
            question the gate did (an on-team holder is preferred). Optional:
            without it every selection is judged org-wide, which is the
            widened answer rather than a wrong one.
        hiring: Opens the approval-gated hire for an unstaffed role and
            finishes approved ones. Optional: without it the sweep still
            releases and still says what is missing, it just cannot ask for
            anybody.
        notifications: Tells the operator a role is unstaffed. Optional.
    """

    def __init__(
        self,
        *,
        task_repo: TaskRepository,
        task_engine: TaskEngine,
        staffing: RoleStaffingService,
        project_repo: ProjectRepository | None = None,
        hiring: HiringService | None = None,
        notifications: NotificationDispatcher | None = None,
    ) -> None:
        self._task_repo = task_repo
        self._task_engine = task_engine
        self._staffing = staffing
        self._project_repo = project_repo
        self._hiring = hiring
        self._notifications = notifications

    async def reconcile(self, *, trigger: str) -> ReviewStaffingPass:
        """Run one idempotent pass.

        Args:
            trigger: What ran the pass, carried into the logs.

        Returns:
            What the pass released, left parked, and asked for.
        """
        logger.info(REVIEW_STAFFING_SWEEP_STARTED, trigger=trigger)
        completed = await self._finish_approved_hires()
        released = 0
        parked = 0
        requested = 0
        for reason, role in _ROLE_BY_REASON.items():
            reason_released, reason_parked = await self._sweep(reason, role)
            released += reason_released
            parked += reason_parked
            if reason_parked and await self._ensure_hire_open(role):
                requested += 1
        result = ReviewStaffingPass(
            trigger=NotBlankStr(trigger),
            released=released,
            still_parked=parked,
            hires_requested=requested,
            hires_completed=completed,
        )
        logger.info(
            REVIEW_STAFFING_SWEEP_COMPLETE,
            trigger=trigger,
            released=released,
            still_parked=parked,
            hires_requested=requested,
            hires_completed=completed,
        )
        return result

    async def _finish_approved_hires(self) -> int:
        """Instantiate every request a human approved but nobody hired.

        Returns:
            How many approved requests became registered agents.
        """
        if self._hiring is None:
            return 0
        completed = 0
        for request in self._hiring.find_approved_requests():
            try:
                identity = await self._hiring.instantiate_agent(request)
            except (HRError, ServiceUnavailableError) as exc:
                # Deliberately not fatal to the pass: one request blocked on
                # its own condition (an unbound new-hire pair) must not stop
                # the others, and the next pass retries this one anyway.
                logger.warning(
                    REVIEW_STAFFING_HIRE_COMPLETION_FAILED,
                    request_id=str(request.id),
                    role=str(request.role),
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                continue
            completed += 1
            logger.info(
                REVIEW_STAFFING_HIRE_COMPLETED,
                request_id=str(request.id),
                role=str(request.role),
                agent_id=str(identity.id),
            )
        return completed

    async def _sweep(self, reason: BlockedReason, role: str) -> tuple[int, int]:
        """Walk every task parked under *reason*.

        Args:
            reason: The park to sweep.
            role: The role such a park waits on.

        Returns:
            How many tasks were released and how many stayed parked.

        Raises:
            PersistenceError: If the parked backlog cannot be read. A sweep
                that cannot see the backlog has nothing to report, and a
                zero it invented would read as "nothing is parked".
        """
        released = 0
        parked = 0
        offset = 0
        for _ in range(_MAX_PAGES):
            page = await self._task_repo.query(
                TaskFilterSpec(status=TaskStatus.BLOCKED, blocked_reason=reason),
                limit=_PAGE_SIZE,
                offset=offset,
            )
            for task in page:
                if await self._try_release(task, role):
                    released += 1
                else:
                    parked += 1
            if len(page) < _PAGE_SIZE:
                break
            # Released tasks have left the filtered set, so the next window
            # starts after the ones this pass could NOT move. Advancing by the
            # page size instead would skip exactly as many rows as were
            # released, and those rows would wait a whole cadence for nothing.
            offset = parked
        return released, parked

    async def _try_release(self, task: Task, role: str) -> bool:
        """Return *task* to review when *role* now has an eligible holder.

        Args:
            task: The parked task.
            role: The role it waits on.

        Returns:
            ``True`` when the task was released.
        """
        selection = await self._select(task, role)
        if selection is None:
            logger.info(
                REVIEW_STAFFING_TASK_STILL_PARKED,
                task_id=str(task.id),
                role=role,
                blocked_reason=(
                    task.blocked_reason.value if task.blocked_reason else None
                ),
                reason="no_eligible_holder",
            )
            return False
        try:
            await self._task_engine.transition_task(
                str(task.id),
                TaskStatus.IN_REVIEW,
                requested_by=_ACTOR,
                reason=_RELEASE_REASON,
            )
        except TaskEngineError as exc:
            # The task keeps its park, so the next pass retries it. Failing
            # the whole sweep here would let one contended row stop every
            # other task from being released.
            logger.warning(
                REVIEW_STAFFING_TASK_RELEASE_FAILED,
                task_id=str(task.id),
                role=role,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return False
        logger.info(
            REVIEW_STAFFING_TASK_RELEASED,
            task_id=str(task.id),
            role=role,
            holder_agent_id=str(selection.agent.id),
            capability_fit=selection.capability_fit,
            source=selection.source,
        )
        return True

    async def _select(self, task: Task, role: str) -> RoleStaffingSelection | None:
        """Ask staffing whether *task* has an eligible holder for *role*.

        Args:
            task: The parked task.
            role: The role it waits on.

        Returns:
            The selection, or ``None`` when nobody eligible holds the role.
        """
        return await self._staffing.select_holder(
            role=NotBlankStr(role),
            required_capability=required_capability_for(
                task.stakes, task.estimated_complexity
            ),
            # The same exclusion the gate applies: an executor may never be
            # offered as its own reviewer, so a solo assignee does not read
            # as staffed.
            exclude_agent_id=NotBlankStr(task.assigned_to or str(task.id)),
            project=await load_project_for_selection(
                self._project_repo,
                task.project,
                failure_event=REVIEW_STAFFING_PROJECT_READ_FAILED,
            ),
        )

    async def _ensure_hire_open(self, role: str) -> bool:
        """Keep exactly one approval-gated hire request open for *role*.

        Args:
            role: The unstaffed role.

        Returns:
            ``True`` when this pass opened a new request.
        """
        if self._hiring is None:
            return False
        if (existing := self._hiring.find_open_request_for_role(role)) is not None:
            logger.info(
                REVIEW_STAFFING_HIRE_ALREADY_OPEN,
                role=role,
                request_id=str(existing.id),
            )
            return False
        catalogued = get_builtin_role(role)
        if catalogued is None:
            logger.warning(
                REVIEW_STAFFING_HIRE_REQUEST_FAILED,
                role=role,
                error="role is not in the built-in catalog; cannot describe the hire",
            )
            return False
        try:
            request = await self._hiring.create_request(
                requested_by=NotBlankStr(_ACTOR),
                department=NotBlankStr(catalogued.department),
                role=NotBlankStr(catalogued.name),
                required_skills=tuple(
                    NotBlankStr(s) for s in catalogued.required_skills
                ),
                reason=NotBlankStr(
                    f"No agent holds {catalogued.name}, so completion gates "
                    "that need it park their work instead of reviewing it."
                ),
            )
            with_candidate = await self._hiring.generate_candidate(request)
            submitted = await self._hiring.submit_for_approval(
                with_candidate, str(with_candidate.candidates[0].id)
            )
        except HRError as exc:
            # The gap stays visible in the still-parked log and the next pass
            # tries again, so a failed request must not fail the sweep.
            logger.warning(
                REVIEW_STAFFING_HIRE_REQUEST_FAILED,
                role=role,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return False
        logger.info(
            REVIEW_STAFFING_HIRE_REQUESTED,
            role=role,
            request_id=str(submitted.id),
            approval_id=submitted.approval_id,
        )
        await self._notify(catalogued.name)
        return True

    async def _notify(self, role: str) -> None:
        """Tell the operator a role is unstaffed and a hire is waiting.

        Sent once per opened request rather than once per pass: the request
        is the thing needing an answer, and repeating the same alert every
        cadence trains the operator to ignore it.

        Args:
            role: The unstaffed role.
        """
        if self._notifications is None:
            return
        await self._notifications.dispatch(
            Notification(
                category=NotificationCategory.APPROVAL,
                severity=NotificationSeverity.WARNING,
                title=NotBlankStr(f"No agent holds {role}"),
                body=(
                    f"Completion gates needing {role} are parking work instead "
                    "of reviewing it. A hire is waiting for your approval; "
                    "giving an existing agent the role resolves it too."
                ),
                source=NotBlankStr(_ACTOR),
                metadata={"role": role},
            )
        )


__all__ = ["ReviewStaffingPass", "ReviewStaffingReconciler"]
