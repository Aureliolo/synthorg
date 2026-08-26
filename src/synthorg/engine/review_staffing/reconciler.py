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

import asyncio
from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.domain_errors import DomainError
from synthorg.core.role_catalog import (
    COMPLETION_REVIEWER_ROLE_NAME,
    RED_TEAM_ROLE_NAME,
)
from synthorg.core.task import Task
from synthorg.core.task_enums import (
    STAFFING_BLOCKED_REASONS,
    BlockedReason,
    TaskStatus,
)
from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import TaskEngineError
from synthorg.engine.initiative.contributors import contributors_or_empty
from synthorg.engine.review.pipeline import ReviewPipeline
from synthorg.engine.review_gate import ReviewGateService
from synthorg.engine.review_staffing.hiring_pass import (
    ensure_hire_open,
    finish_approved_hires,
)
from synthorg.engine.review_staffing.notices import (
    ACTOR as _ACTOR,
)
from synthorg.engine.review_staffing.notices import notify_standing_gap
from synthorg.engine.review_staffing.rejudge import rejudge_released_task
from synthorg.engine.review_staffing.unroutable import unroutable_by_role
from synthorg.engine.task_engine import TaskEngine
from synthorg.hr.hiring_service import HiringService
from synthorg.hr.role_staffing import RoleStaffingSelection, RoleStaffingService
from synthorg.notifications.dispatcher import NotificationDispatcher
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.review_staffing import (
    REVIEW_STAFFING_HIRE_COMPLETION_FAILED,
    REVIEW_STAFFING_PROJECT_READ_FAILED,
    REVIEW_STAFFING_ROLE_SWEEP_FAILED,
    REVIEW_STAFFING_ROLE_UNSTAFFED,
    REVIEW_STAFFING_SWEEP_COMPLETE,
    REVIEW_STAFFING_SWEEP_STARTED,
    REVIEW_STAFFING_TASK_RELEASE_FAILED,
    REVIEW_STAFFING_TASK_RELEASED,
    REVIEW_STAFFING_TASK_STILL_PARKED,
    REVIEW_STAFFING_UNROUTABLE_ROLELESS,
)
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

if set(_ROLE_BY_REASON) != STAFFING_BLOCKED_REASONS:
    # A staffing park this map does not name is a park nothing ever sweeps:
    # the task waits on a role, and no human owes it an answer, so it would
    # sit BLOCKED for the life of the org. Caught at import because a third
    # gate role is added by editing these two declarations, and forgetting
    # one of them has no other symptom.
    _missing = sorted(r.value for r in STAFFING_BLOCKED_REASONS - set(_ROLE_BY_REASON))
    _extra = sorted(r.value for r in set(_ROLE_BY_REASON) - STAFFING_BLOCKED_REASONS)
    _msg = (
        f"review-staffing role map disagrees with STAFFING_BLOCKED_REASONS; "
        f"unswept: {_missing}, unknown: {_extra}"
    )
    raise ValueError(_msg)

#: Rows read per query. A sweep walks every parked task, so it pages rather
#: than assuming the backlog fits one read.
_PAGE_SIZE: Final[int] = 100

#: Ceiling on pages per reason per pass. A pass is a periodic sweep, not a
#: drain: past this the remainder waits for the next one rather than holding
#: the loop open against a backlog that keeps growing.
_MAX_PAGES: Final[int] = 20

_RELEASE_REASON: Final[str] = (
    "The role this task's completion gate needs is staffed again; "
    "returning it for review."
)


class ReviewStaffingPass(BaseModel):
    """What one sweep did.

    Returned to whoever ran the pass and logged; deliberately not published
    as state. These are per-run statistics, and the state an operator asks
    about is already answerable from the durable rows: the parked backlog is
    ``GET /tasks`` filtered by the staffing blocked reason, and the hire this
    sweep opened is an item in the approvals queue. A counter parked on the
    scheduler would be a second, staler answer to both.

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
        task_repo: Read side for the parked backlog, and the source of the
            contributor list so the sweep asks the same question the gate did
            (a holder who already worked the initiative is preferred).
        task_engine: Writes the release transition, so the hop goes through
            the same validation and audit trail as every other one.
        staffing: Answers whether an eligible holder exists for a task.
        review_gate: Re-drives the review after a release. A status hop into
            IN_REVIEW is watched by nothing, so without this the sweep would
            move a task somewhere no judge ever looks.
        review_pipeline: The staged pipeline the gate runs.
        hiring: Reads the live hiring pipeline, which opens the approval-gated
            hire for an unstaffed role and finishes approved ones. Genuinely
            optional: a boot with no approval store has none, and the sweep
            still releases what it can and still names what is missing. Read
            through a callable so a pipeline wired after this sweep started is
            picked up on the next pass rather than never.
        notifications: Reads the live dispatcher when there is something to
            tell the operator. A callable for the same reason, and one more:
            boot replaces the dispatcher after the subsystems come up and
            closes the one that was current, so a captured instance is already
            shut by the time the first role goes unstaffed.
    """

    def __init__(
        self,
        *,
        task_repo: TaskRepository,
        task_engine: TaskEngine,
        staffing: RoleStaffingService,
        review_gate: ReviewGateService,
        review_pipeline: ReviewPipeline,
        hiring: Callable[[], HiringService | None] | None = None,
        notifications: Callable[[], NotificationDispatcher | None] | None = None,
    ) -> None:
        self._task_repo = task_repo
        self._task_engine = task_engine
        self._staffing = staffing
        self._review_gate = review_gate
        self._review_pipeline = review_pipeline
        self._hiring = hiring
        self._notifications = notifications
        # Roles already warned about while still unstaffed. The gap is a
        # standing condition, not an event, so re-announcing it every cadence
        # would train the operator to dismiss the one alert that means their
        # org cannot finish anything.
        self._warned_roles: set[str] = set()
        # Built on first use rather than here, so the reconciler is not
        # captive to whichever loop happened to construct it.
        self._pass_lock: asyncio.Lock | None = None

    async def reconcile(self, *, trigger: str) -> ReviewStaffingPass:
        """Run one idempotent pass, serialised against any other.

        More than one thing fires this: the periodic sweep, and every
        settings write that could have closed a staffing gap. Two passes
        overlapping would each read the same gap and each act on it, and
        the hire half is a check-then-act (is a request already in flight;
        if not, open one) with awaits between the halves. Concurrently, both
        pass the check and the operator is asked to approve two hires for one
        role. Serialised, the second pass reads what the first one opened.

        Waiting rather than skipping, because a pass is bounded and level
        triggered: whoever asked gets a real answer measured after the
        in-flight pass, not a result computed before their trigger existed.

        Args:
            trigger: What ran the pass, carried into the logs.

        Returns:
            What the pass released, left parked, and asked for.

        Raises:
            asyncio.CancelledError: Propagated so a stopping scheduler is
                not recorded as a hire-completion failure.
        """
        if self._pass_lock is None:
            self._pass_lock = asyncio.Lock()
        async with self._pass_lock:
            return await self._reconcile_once(trigger=trigger)

    async def _reconcile_once(self, *, trigger: str) -> ReviewStaffingPass:
        """Run the pass body, with the serialising lock already held.

        Args:
            trigger: What ran the pass, carried into the logs.

        Returns:
            What the pass released, left parked, and asked for.

        Raises:
            asyncio.CancelledError: Propagated so a stopping scheduler is
                not recorded as a hire-completion failure.
        """
        logger.info(REVIEW_STAFFING_SWEEP_STARTED, trigger=trigger)
        # Contained like _sweep_role's body: finishing an approved hire and
        # releasing a parked task are independent halves of the pass, so a
        # failure in the first must not take the sweep down with it and leave
        # every parked task waiting a full cadence on an unrelated fault.
        try:
            completed = await self._finish_approved_hires()
        except asyncio.CancelledError:
            raise
        except DomainError as exc:
            logger.warning(
                REVIEW_STAFFING_HIRE_COMPLETION_FAILED,
                trigger=trigger,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            completed = 0
        released = 0
        parked = 0
        requested = 0
        for reason, role in _ROLE_BY_REASON.items():
            await self._warn_if_unstaffed(role)
            reason_released, reason_parked, reason_requested = await self._sweep_role(
                reason, role
            )
            released += reason_released
            parked += reason_parked
            requested += reason_requested
        (
            unroutable_released,
            unroutable_parked,
            unroutable_requested,
        ) = await self._sweep_unroutable()
        released += unroutable_released
        parked += unroutable_parked
        requested += unroutable_requested
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

    async def _warn_if_unstaffed(self, role: str) -> None:
        """Tell the operator a gate role has no holder, before work needs it.

        The hire path answers a park, so its notification cannot arrive until
        a task has already run, been paid for, and stopped. An org whose
        roster holds nobody for a gate role is one that cannot complete a
        single task, and that is knowable at boot: this pass runs on a cadence
        from start-up, so the operator hears it before filing anything.

        Reported once per gap. When the role is staffed the warning re-arms,
        so an agent later stood down produces a fresh alert rather than
        silence.

        Args:
            role: The gate role to check for any ACTIVE holder.

        Raises:
            asyncio.CancelledError: Propagated so a stopping scheduler is not
                recorded as an unreadable roster.
        """
        try:
            staffed = await self._staffing.has_holder(NotBlankStr(role))
        except asyncio.CancelledError:
            raise
        except DomainError as exc:
            # A roster read that failed says nothing about staffing, and a
            # warning invented from it would be an alert about the reader.
            logger.warning(
                REVIEW_STAFFING_ROLE_SWEEP_FAILED,
                role=role,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                note="roster unreadable; staffing gap not assessed this pass",
            )
            return
        if staffed:
            self._warned_roles.discard(role)
            return
        logger.warning(REVIEW_STAFFING_ROLE_UNSTAFFED, role=role)
        if role in self._warned_roles:
            return
        self._warned_roles.add(role)
        await notify_standing_gap(self._notifications, role)

    async def _sweep_role(
        self, reason: BlockedReason, role: str
    ) -> tuple[int, int, int]:
        """Sweep one role's parks and keep its hire open, in isolation.

        The two gate roles are independent: staffing one releases nothing
        parked on the other. So one role's failure is contained here rather
        than allowed to abort the pass, which would silently cost the other
        role a whole cadence for a reason unrelated to it.

        Args:
            reason: The park to sweep.
            role: The role such a park waits on.

        Returns:
            Released, still-parked, and hire-requests-opened counts.

        Raises:
            asyncio.CancelledError: Propagated so a stopping scheduler is
                not mistaken for a role that failed.
        """
        try:
            released, parked = await self._sweep(reason, role)
            requested = int(bool(parked) and await self._ensure_hire_open(role))
        except asyncio.CancelledError:
            raise
        except DomainError as exc:
            logger.warning(
                REVIEW_STAFFING_ROLE_SWEEP_FAILED,
                role=role,
                blocked_reason=reason.value,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return 0, 0, 0
        return released, parked, requested

    async def _sweep_unroutable(self) -> tuple[int, int, int]:
        """Sweep the tasks no agent could take, and offer to hire for them.

        Unlike the two gate parks, this one names no fixed role: each row
        waits on whatever role its plan item asked for, recorded on the task
        when it was parked. So the backlog is grouped by that role and each
        group swept as its own gate park would be, which also means one hire
        request per role rather than one per stranded task.

        A row parked before its role was recorded, or by a plan that asked
        for none, is counted as parked and left alone: there is nothing to
        offer to hire, and inventing a role would be worse than saying so.

        Returns:
            Released, still-parked, and hire-requests-opened counts.

        Raises:
            asyncio.CancelledError: Propagated so a stopping scheduler is not
                mistaken for a failed sweep.
        """
        try:
            by_role, roleless = await unroutable_by_role(
                self._task_repo, page_size=_PAGE_SIZE, max_pages=_MAX_PAGES
            )
        except asyncio.CancelledError:
            raise
        except DomainError as exc:
            logger.warning(
                REVIEW_STAFFING_ROLE_SWEEP_FAILED,
                blocked_reason=BlockedReason.NO_CAPABLE_AGENT.value,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return 0, 0, 0
        if roleless:
            logger.warning(
                REVIEW_STAFFING_UNROUTABLE_ROLELESS,
                task_count=roleless,
                note=(
                    "These tasks name no role, so no hire can be offered for"
                    " them; they wait on an operator directly"
                ),
            )
        released = 0
        parked = roleless
        requested = 0
        cache: dict[str, tuple[NotBlankStr, ...]] = {}
        for role, tasks in sorted(by_role.items()):
            role_released, role_parked, role_requested = await self._sweep_role_tasks(
                role, tasks, cache=cache
            )
            released += role_released
            parked += role_parked
            requested += role_requested
        return released, parked, requested

    async def _sweep_role_tasks(
        self,
        role: str,
        tasks: list[Task],
        *,
        cache: dict[str, tuple[NotBlankStr, ...]],
    ) -> tuple[int, int, int]:
        """Release what *role* can now take, and offer a hire if any remain.

        Args:
            role: The role this group of parked tasks waits on.
            tasks: The parked tasks that named it.
            cache: The pass's per-initiative contributor reads.

        Returns:
            Released, still-parked, and hire-requests-opened counts.

        Raises:
            asyncio.CancelledError: Propagated so a stopping scheduler is not
                mistaken for a role that failed.
        """
        released = 0
        parked = 0
        try:
            for task in tasks:
                if await self._try_release(task, role, cache=cache):
                    released += 1
                else:
                    parked += 1
            requested = int(bool(parked) and await self._ensure_hire_open(role))
        except asyncio.CancelledError:
            raise
        except DomainError as exc:
            logger.warning(
                REVIEW_STAFFING_ROLE_SWEEP_FAILED,
                role=role,
                blocked_reason=BlockedReason.NO_CAPABLE_AGENT.value,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return released, parked, 0
        return released, parked, requested

    async def _finish_approved_hires(self) -> int:
        """Instantiate every request a human approved but nobody hired.

        Returns:
            How many approved requests became registered agents.

        Raises:
            asyncio.CancelledError: Propagated so a stopping scheduler is
                not recorded as a hydration failure.
        """
        return await finish_approved_hires(
            self._hiring() if self._hiring is not None else None,
            notifications=self._notifications,
        )

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
        # One contributor read per initiative per pass. The parked tasks of an
        # initiative all resolve the same list, so without this the sweep
        # costs a read per parked task rather than per initiative.
        contributors_cache: dict[str, tuple[NotBlankStr, ...]] = {}
        for _ in range(_MAX_PAGES):
            page = await self._task_repo.query(
                TaskFilterSpec(status=TaskStatus.BLOCKED, blocked_reason=reason),
                limit=_PAGE_SIZE,
                offset=offset,
            )
            for task in page:
                if await self._try_release(task, role, cache=contributors_cache):
                    released += 1
                else:
                    parked += 1
            if len(page) < _PAGE_SIZE:
                break
            # Released tasks have left the filtered set, so the next window
            # starts after the ones this pass could NOT move. Advancing by the
            # page size instead would skip exactly as many rows as were
            # released, and those rows would wait a whole cadence for nothing.
            # This accounts for removals, not insertions: a task parked by a
            # gate mid-pass can land ahead of the cursor and be missed. The
            # next pass sees it, which is what level-triggered means.
            offset = parked
        return released, parked

    async def _try_release(
        self,
        task: Task,
        role: str,
        *,
        cache: dict[str, tuple[NotBlankStr, ...]],
    ) -> bool:
        """Return *task* to review when *role* now has an eligible holder.

        Args:
            task: The parked task.
            role: The role it waits on.
            cache: The pass's contributor reads, keyed by project.

        Returns:
            ``True`` when the task was released.

        Raises:
            CancelledError: When the sweep stops between the release and the
                re-judge, which is logged before it propagates because the
                released task no longer matches the query that found it.
        """
        selection = await self._select(task, role, cache=cache)
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
        try:
            await rejudge_released_task(
                task,
                review_gate=self._review_gate,
                review_pipeline=self._review_pipeline,
                task_engine=self._task_engine,
                actor=_ACTOR,
            )
        except asyncio.CancelledError:
            # The release has already committed, and it cleared the park that
            # put this task in the sweep's query, so no later pass will find
            # it again and nothing watches IN_REVIEW. Naming it on the way out
            # is what leaves an operator something to act on; swallowing the
            # cancellation would be worse, so it goes straight back up.
            logger.warning(
                REVIEW_STAFFING_TASK_RELEASED,
                task_id=str(task.id),
                role=role,
                holder_agent_id=str(selection.agent.id),
                note=(
                    "Released but not re-judged before the sweep stopped;"
                    " the task waits in review for a human decision."
                ),
            )
            raise
        return True

    async def _select(
        self,
        task: Task,
        role: str,
        *,
        cache: dict[str, tuple[NotBlankStr, ...]],
    ) -> RoleStaffingSelection | None:
        """Ask staffing whether *task* has an eligible holder for *role*.

        Args:
            task: The parked task.
            role: The role it waits on.
            cache: The pass's contributor reads, keyed by project. A failed
                read caches its empty result too: it already degrades to
                choosing org-wide, and retrying it per parked task would pay
                the failing round trip once per row.

        Returns:
            The selection, or ``None`` when nobody eligible holds the role,
            and when the task names no executor to exclude. Substituting a
            non-agent id there would exclude nobody, so the sweep would read
            staffed what the gate is about to re-park, once per cadence
            forever.
        """
        executor = task.assigned_to
        if executor is None:
            logger.info(
                REVIEW_STAFFING_TASK_STILL_PARKED,
                task_id=str(task.id),
                role=role,
                reason="task names no executor to exclude from review",
            )
            return None
        project = task.project
        contributors = cache.get(str(project))
        if contributors is None:
            contributors = await contributors_or_empty(
                self._task_repo,
                project_id=project,
                failure_event=REVIEW_STAFFING_PROJECT_READ_FAILED,
            )
            cache[str(project)] = contributors
        return await self._staffing.select_holder(
            role=NotBlankStr(role),
            stakes=task.stakes,
            complexity=task.estimated_complexity,
            # The same exclusion the gate applies: an executor may never be
            # offered as its own reviewer, so a solo assignee does not read
            # as staffed.
            exclude_agent_id=NotBlankStr(executor),
            contributors=contributors,
            project_id=project,
        )

    async def _ensure_hire_open(self, role: str) -> bool:
        """Keep exactly one approval-gated hire request open for *role*.

        Args:
            role: The unstaffed role.

        Returns:
            ``True`` when this pass opened a new request.
        """
        return await ensure_hire_open(
            self._hiring() if self._hiring is not None else None,
            role,
            notifications=self._notifications,
            actor=_ACTOR,
        )


__all__ = ["ReviewStaffingPass", "ReviewStaffingReconciler"]
