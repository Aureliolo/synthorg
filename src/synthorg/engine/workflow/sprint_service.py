# module-kind: service
"""Runtime orchestration for agile sprints in ``agile_kanban`` orgs.

The service:

* registers as a :class:`TaskEngine` observer. On a task entering
  ``ASSIGNED`` for a project with no open sprint, it auto-creates a
  sprint seeded with the project's open tasks and starts it (``ACTIVE``);
  on a task entering ``COMPLETED``, it marks the task done in the open
  sprint's backlog and forwards to the :class:`CeremonyScheduler`, which
  fires ceremonies and auto-transitions the sprint;
* drives the tail of the lifecycle (``IN_REVIEW -> RETROSPECTIVE ->
  COMPLETED``) once the backlog is fully delivered, then deactivates the
  scheduler;
* exposes explicit control (``create_sprint`` builds an empty PLANNING
  shell; ``add_task`` / ``start_sprint`` / ``advance_sprint`` are the
  REST overrides);
* answers ``is_task_workable`` for the advisory Kanban board gate.

The DB writes (backlog + lifecycle CAS) run inline; the ceremony-scheduler
notifications, which can fire LLM-backed meetings, run in tracked
background tasks so the single-consumer observer-dispatch loop never
blocks on a meeting.

Persistence is the sole source of truth: the sprint is read back from
the repository on every operation, mutated via the immutable domain
functions, and written straight back. The per-service ``asyncio.Lock``
serialises the read-modify-write sections **within one process**; the
repository's ``transition_if`` CAS is the only cross-process guard.
"""

import asyncio
from collections.abc import Coroutine
from typing import Final
from uuid import uuid4

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus
from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import (
    SprintBacklogFullError,
    SprintNotFoundError,
    SprintTransitionConflictError,
)
from synthorg.engine.task_engine_models import TaskStateChanged
from synthorg.engine.workflow._sprint_ops import (
    NON_TERMINAL_TASK_STATUSES,
    OPEN_SPRINT_STATUSES,
    next_status,
    open_backlog_tasks,
    story_points_for,
    transition_overrides,
)
from synthorg.engine.workflow.ceremony_policy import CeremonyStrategyType
from synthorg.engine.workflow.ceremony_scheduler import CeremonyScheduler
from synthorg.engine.workflow.enums import WorkflowType
from synthorg.engine.workflow.sprint_backlog import (
    add_task_to_sprint,
    complete_task_in_sprint,
)
from synthorg.engine.workflow.sprint_config import SprintConfig
from synthorg.engine.workflow.sprint_lifecycle import Sprint, SprintStatus
from synthorg.engine.workflow.sprint_strategy_factory import strategy_for
from synthorg.engine.workflow.sprint_velocity import VelocityRecord, record_velocity
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
)
from synthorg.observability.events.workflow import (
    SPRINT_CREATED,
    SPRINT_SERVICE_OBSERVER_FAILED,
    SPRINT_STATUS_TRANSITIONED,
    SPRINT_TASK_COMPLETED,
    SPRINT_TRANSITION_LOST,
)
from synthorg.persistence.sprint_protocol import SprintFilterSpec, SprintRepository
from synthorg.persistence.task_protocol import TaskFilterSpec, TaskRepository
from synthorg.settings.resolver_protocol import ConfigResolverProtocol

logger = get_logger(__name__)

_SETTINGS_NS: Final[str] = "engine"


class SprintService:
    """Create, run, and advance sprints for ``agile_kanban`` projects.

    Args:
        sprint_repository: Durable sprint store.
        task_repository: Task store, read to seed a sprint backlog.
        ceremony_scheduler: Runtime ceremony coordinator, or ``None`` when
            the meeting stack is unwired (advancement still persists; no
            ceremonies fire).
        config_resolver: Settings resolver for the hot ``sprint_enabled``
            and ``workflow_type`` gates.
        sprint_config: Effective sprint configuration (duration, backlog
            cap, ceremony policy). Captured at wire time; locked per sprint.
        clock: Clock seam for the start/end timestamps.
    """

    __slots__ = (
        "_bg_tasks",
        "_ceremony_scheduler",
        "_clock",
        "_config_resolver",
        "_lock",
        "_sprint_config",
        "_sprints",
        "_tasks",
    )

    def __init__(  # noqa: PLR0913 -- service needs its full dependency set
        self,
        *,
        sprint_repository: SprintRepository,
        task_repository: TaskRepository,
        ceremony_scheduler: CeremonyScheduler | None,
        config_resolver: ConfigResolverProtocol,
        sprint_config: SprintConfig | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._sprints = sprint_repository
        self._tasks = task_repository
        self._ceremony_scheduler = ceremony_scheduler
        self._config_resolver = config_resolver
        self._sprint_config = sprint_config or SprintConfig()
        self._clock = clock or SystemClock()
        # Serialises the read-modify-write critical sections so two
        # concurrent completions in this process cannot both advance the
        # same sprint; transition_if is the cross-process backstop.
        self._lock = asyncio.Lock()
        # In-flight ceremony-forwarding tasks spawned off the observer so
        # a meeting never blocks the dispatch loop; drained on shutdown.
        self._bg_tasks: set[asyncio.Task[None]] = set()

    def _spawn(self, coro: Coroutine[object, object, None]) -> None:
        """Run *coro* as a tracked background task (never blocks the caller)."""
        task = asyncio.create_task(self._guard(coro))
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    async def _guard(self, coro: Coroutine[object, object, None]) -> None:
        """Await *coro*, redacting any non-critical failure off the dispatch loop."""
        try:
            await coro
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            log_exception_redacted(
                logger,
                SPRINT_SERVICE_OBSERVER_FAILED,
                exc,
                phase="ceremony_forward",
            )

    async def drain(self) -> None:
        """Await all in-flight background ceremony tasks.

        Used by graceful shutdown and by tests that assert on the state a
        forwarded ceremony leaves behind.
        """
        while self._bg_tasks:
            await asyncio.gather(*tuple(self._bg_tasks), return_exceptions=True)

    # -- Read surface --------------------------------------------------

    async def get_sprint(self, sprint_id: str) -> Sprint | None:
        """Return the sprint with *sprint_id*, or ``None`` when absent."""
        return await self._sprints.get(NotBlankStr(sprint_id))

    async def list_sprints(
        self,
        *,
        project: str | None = None,
        status: SprintStatus | None = None,
    ) -> tuple[Sprint, ...]:
        """Return sprints matching the optional project / status filter."""
        return await self._sprints.query(
            SprintFilterSpec(
                project=NotBlankStr(project) if project is not None else None,
                status=status,
            )
        )

    async def active_sprint(self, project: str | None) -> Sprint | None:
        """Return the open (ACTIVE / IN_REVIEW) sprint for *project*."""
        return await self._open_sprint(project)

    async def is_task_workable(self, task_id: str, project: str | None) -> bool:
        """Whether *task_id* may be pulled into flow under the sprint gate.

        Advisory: returns ``True`` (no gating) when sprints are disabled,
        the workflow is not ``agile_kanban``, or the project has no open
        sprint. Otherwise the task must sit in the open sprint's backlog.

        Returns:
            ``True`` when the task may be worked, ``False`` when the
            active sprint gates it out.
        """
        if not await self._sprints_active():
            return True
        sprint = await self._open_sprint(project)
        if sprint is None:
            return True
        return task_id in sprint.task_ids

    # -- Explicit control surface --------------------------------------

    async def create_sprint(self, project: str | None) -> Sprint:
        """Create and persist a new ``PLANNING`` sprint for *project*.

        Returns:
            The persisted PLANNING sprint.
        """
        async with self._lock:
            sprint = await self._build_planning_sprint(project)
            await self._sprints.save(sprint)
        logger.info(
            SPRINT_CREATED,
            sprint_id=sprint.id,
            project=project,
            sprint_number=sprint.sprint_number,
        )
        return sprint

    async def add_task(
        self, sprint_id: str, task_id: str, story_points: float
    ) -> Sprint:
        """Add a task to a ``PLANNING`` sprint backlog (capacity-checked).

        Returns:
            The sprint with the task added to its backlog.

        Raises:
            SprintNotFoundError: When *sprint_id* has no row.
            SprintTransitionConflictError: When the sprint is not
                ``PLANNING`` (tasks may only be added while planning).
            SprintBacklogFullError: When the backlog is at
                ``max_tasks_per_sprint``.
        """
        async with self._lock:
            sprint = await self._require(sprint_id)
            if sprint.status is not SprintStatus.PLANNING:
                msg = f"Sprint {sprint_id!r} is not in 'planning'; cannot add tasks"
                raise SprintTransitionConflictError(msg)
            if len(sprint.task_ids) >= self._sprint_config.max_tasks_per_sprint:
                msg = (
                    f"Sprint {sprint_id!r} backlog is full "
                    f"({self._sprint_config.max_tasks_per_sprint} tasks)"
                )
                raise SprintBacklogFullError(msg)
            updated = add_task_to_sprint(sprint, NotBlankStr(task_id), story_points)
            await self._sprints.save(updated)
            return updated

    async def start_sprint(self, sprint_id: str) -> Sprint:
        """Transition a ``PLANNING`` sprint to ``ACTIVE`` and start ceremonies.

        Returns:
            The started ACTIVE sprint.

        Raises:
            SprintNotFoundError: When *sprint_id* has no row.
            SprintTransitionConflictError: When the sprint is not
                ``PLANNING`` (the CAS found a different state).
        """
        async with self._lock:
            sprint = await self._require(sprint_id)
            if sprint.status is not SprintStatus.PLANNING:
                msg = f"Sprint {sprint_id!r} is not in 'planning'"
                raise SprintTransitionConflictError(msg)
            started = sprint.with_transition(
                SprintStatus.ACTIVE, start_date=self._now_iso()
            )
            ok = await self._sprints.transition_if(
                NotBlankStr(sprint_id),
                SprintStatus.PLANNING,
                SprintStatus.ACTIVE,
                start_date=started.start_date,
            )
            if not ok:
                msg = f"Sprint {sprint_id!r} is not in 'planning'"
                raise SprintTransitionConflictError(msg)
        await self._activate_scheduler(started)
        self._log_transition(started, SprintStatus.PLANNING)
        return started

    async def advance_sprint(self, sprint_id: str) -> Sprint:
        """Advance a sprint one hop along its linear lifecycle.

        The explicit override the REST surface exposes; the ceremony
        scheduler drives most transitions automatically. Stamps
        ``start_date`` on activation and ``end_date`` on completion.

        Returns:
            The advanced sprint.

        Raises:
            SprintNotFoundError: When *sprint_id* has no row.
            SprintTransitionConflictError: When the sprint is terminal or
                the CAS finds a different state.
        """
        async with self._lock:
            sprint = await self._require(sprint_id)
            target = next_status(sprint)
            overrides = transition_overrides(sprint, target, now_iso=self._now_iso())
            advanced = sprint.with_transition(target, **overrides)
            ok = await self._sprints.transition_if(
                NotBlankStr(sprint_id), sprint.status, target, **overrides
            )
            if not ok:
                msg = f"Sprint {sprint_id!r} is not in {sprint.status.value!r}"
                raise SprintTransitionConflictError(msg)
        await self._reconcile_scheduler(sprint.status, advanced)
        self._log_transition(advanced, sprint.status)
        return advanced

    # -- Task-engine observer ------------------------------------------

    async def on_task_state_changed(self, event: TaskStateChanged) -> None:
        """Best-effort observer: keep the sprint in step with task state.

        On a task entering ``ASSIGNED`` for an ``agile_kanban`` project
        with no open sprint, auto-create + start one seeded with the
        project's open tasks. On a task entering ``COMPLETED``, mark it
        done in the open sprint and forward to the ceremony scheduler.
        Never raises into the engine's observer dispatch.
        """
        phase = "unknown"
        try:
            if event.task is None or event.new_status is None:
                return
            if not await self._sprints_active():
                return
            if event.new_status is TaskStatus.ASSIGNED:
                phase = "ensure_sprint_for_work"
                await self._ensure_sprint_for_work(event.task)
            elif event.new_status is TaskStatus.COMPLETED:
                phase = "handle_completion"
                await self._handle_completion(event.task)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            log_exception_redacted(
                logger,
                SPRINT_SERVICE_OBSERVER_FAILED,
                exc,
                phase=phase,
                task_id=event.task_id,
                project=event.task.project if event.task is not None else None,
                new_status=event.new_status.value if event.new_status else None,
            )

    # -- Auto lifecycle ------------------------------------------------

    async def _ensure_sprint_for_work(self, task: Task) -> None:
        """Create + start a sprint for *task*'s project when none is open."""
        started: Sprint | None = None
        async with self._lock:
            existing = await self._sprints.query(
                SprintFilterSpec(project=self._project_of(task))
            )
            if any(s.status is not SprintStatus.COMPLETED for s in existing):
                return
            sprint = await self._build_planning_sprint(self._project_of(task))
            for backlog_task in await self._collect_backlog(task):
                sprint = add_task_to_sprint(
                    sprint,
                    NotBlankStr(str(backlog_task.id)),
                    story_points_for(backlog_task),
                )
            await self._sprints.save(sprint)
            started = sprint.with_transition(
                SprintStatus.ACTIVE, start_date=self._now_iso()
            )
            if not await self._sprints.transition_if(
                NotBlankStr(sprint.id),
                SprintStatus.PLANNING,
                SprintStatus.ACTIVE,
                start_date=started.start_date,
            ):
                logger.warning(
                    SPRINT_TRANSITION_LOST,
                    sprint_id=sprint.id,
                    from_status=SprintStatus.PLANNING.value,
                    to_status=SprintStatus.ACTIVE.value,
                    note="post_create_activation",
                )
                return
            logger.info(
                SPRINT_CREATED,
                sprint_id=sprint.id,
                project=self._project_of(task),
                sprint_number=sprint.sprint_number,
                seeded_tasks=len(sprint.task_ids),
            )
        self._log_transition(started, SprintStatus.PLANNING)
        self._spawn(self._activate_scheduler(started))

    async def _handle_completion(self, task: Task) -> None:
        """Mark *task* done in the open sprint, then advance off-loop.

        The backlog write commits inline (source of truth); the ceremony
        forward + lifecycle tail run in a background task so the observer
        dispatch loop never waits on a meeting.
        """
        task_id = str(task.id)
        async with self._lock:
            sprint = await self._open_sprint(self._project_of(task))
            if sprint is None or task_id not in sprint.task_ids:
                return
            if task_id in sprint.completed_task_ids:
                return
            points = sprint.task_points.get(task_id, 0.0)
            updated = complete_task_in_sprint(sprint, NotBlankStr(task_id))
            await self._sprints.save(updated)
        logger.info(SPRINT_TASK_COMPLETED, sprint_id=updated.id, task_id=task_id)
        self._spawn(self._forward_and_finalize(updated, task_id, points))

    async def _forward_and_finalize(
        self, sprint: Sprint, task_id: str, points: float
    ) -> None:
        """Off-loop tail: forward the completion to the scheduler, then finalize."""
        transitioned = await self._forward_completion(sprint, task_id, points)
        await self._finalize_if_delivered(transitioned)

    async def _forward_completion(
        self, sprint: Sprint, task_id: str, points: float
    ) -> Sprint:
        """Forward a completion to the scheduler; persist any auto-transition.

        Returns:
            The sprint after any scheduler-driven auto-transition.
        """
        if self._ceremony_scheduler is None:
            return sprint
        transitioned = await self._ceremony_scheduler.on_task_completed(
            sprint, task_id, points
        )
        if transitioned.status is sprint.status:
            return sprint
        if not await self._sprints.transition_if(
            NotBlankStr(sprint.id), sprint.status, transitioned.status
        ):
            logger.warning(
                SPRINT_TRANSITION_LOST,
                sprint_id=sprint.id,
                from_status=sprint.status.value,
                to_status=transitioned.status.value,
                note="ceremony_auto_transition",
            )
            return sprint
        self._log_transition(transitioned, sprint.status)
        return transitioned

    async def _finalize_if_delivered(self, sprint: Sprint) -> None:
        """Walk IN_REVIEW -> RETROSPECTIVE -> COMPLETED when all tasks are done."""
        if sprint.status is not SprintStatus.IN_REVIEW:
            return
        if not sprint.task_ids:
            return
        if len(sprint.completed_task_ids) < len(sprint.task_ids):
            return
        async with self._lock:
            if not await self._sprints.transition_if(
                NotBlankStr(sprint.id),
                SprintStatus.IN_REVIEW,
                SprintStatus.RETROSPECTIVE,
            ):
                logger.debug(
                    SPRINT_TRANSITION_LOST,
                    sprint_id=sprint.id,
                    from_status=SprintStatus.IN_REVIEW.value,
                    to_status=SprintStatus.RETROSPECTIVE.value,
                    note="finalize_review_to_retro",
                )
                return
            completed = sprint.model_copy(
                update={"status": SprintStatus.RETROSPECTIVE}
            ).with_transition(SprintStatus.COMPLETED, end_date=self._now_iso())
            if not await self._sprints.transition_if(
                NotBlankStr(sprint.id),
                SprintStatus.RETROSPECTIVE,
                SprintStatus.COMPLETED,
                end_date=completed.end_date,
            ):
                logger.warning(
                    SPRINT_TRANSITION_LOST,
                    sprint_id=sprint.id,
                    from_status=SprintStatus.RETROSPECTIVE.value,
                    to_status=SprintStatus.COMPLETED.value,
                    note="finalize_retro_to_completed",
                )
                return
        self._log_transition(completed, SprintStatus.IN_REVIEW)
        if self._ceremony_scheduler is not None:
            await self._ceremony_scheduler.deactivate_sprint()

    # -- Scheduler bridge ----------------------------------------------

    async def _activate_scheduler(self, sprint: Sprint) -> None:
        """Activate the ceremony scheduler for a freshly-started sprint."""
        if self._ceremony_scheduler is None:
            return
        policy = self._sprint_config.ceremony_policy
        strategy = strategy_for(
            policy.strategy or CeremonyStrategyType.TASK_DRIVEN,
            clock=self._clock,
        )
        history = await self._velocity_history(sprint.project)
        await self._ceremony_scheduler.activate_sprint(
            sprint, self._sprint_config, strategy, velocity_history=history
        )

    async def _reconcile_scheduler(
        self, previous: SprintStatus, sprint: Sprint
    ) -> None:
        """Start / stop the scheduler after an explicit ``advance_sprint``."""
        if self._ceremony_scheduler is None:
            return
        if previous is SprintStatus.PLANNING and sprint.status is SprintStatus.ACTIVE:
            await self._activate_scheduler(sprint)
        elif sprint.status is SprintStatus.COMPLETED:
            await self._ceremony_scheduler.deactivate_sprint()

    async def _velocity_history(
        self, project: NotBlankStr | None
    ) -> tuple[VelocityRecord, ...]:
        """Reconstruct velocity records from completed sprints, oldest-first.

        Returns:
            The velocity records for the project's completed sprints,
            oldest-first (the order the rolling-average window expects).
        """
        completed = await self._sprints.query(
            SprintFilterSpec(project=project, status=SprintStatus.COMPLETED)
        )
        return tuple(record_velocity(sprint) for sprint in reversed(completed))

    # -- Helpers -------------------------------------------------------

    async def _sprints_active(self) -> bool:
        """Whether sprint machinery applies to the current org (hot-read).

        Returns:
            ``True`` when ``sprint_enabled`` is on and the workflow type
            is ``agile_kanban``.
        """
        if not await self._config_resolver.get_bool(_SETTINGS_NS, "sprint_enabled"):
            return False
        workflow = await self._config_resolver.get_enum(
            _SETTINGS_NS, "workflow_type", WorkflowType
        )
        return workflow is WorkflowType.AGILE_KANBAN

    async def _open_sprint(self, project: NotBlankStr | None) -> Sprint | None:
        """Return the project's ACTIVE / IN_REVIEW sprint, newest-first."""
        for sprint in await self._sprints.query(SprintFilterSpec(project=project)):
            if sprint.status in OPEN_SPRINT_STATUSES:
                return sprint
        return None

    async def _require(self, sprint_id: str) -> Sprint:
        """Return a sprint or raise :class:`SprintNotFoundError`.

        Returns:
            The persisted sprint.

        Raises:
            SprintNotFoundError: When *sprint_id* has no row.
        """
        sprint = await self._sprints.get(NotBlankStr(sprint_id))
        if sprint is None:
            msg = f"Sprint {sprint_id!r} not found"
            raise SprintNotFoundError(msg)
        return sprint

    async def _build_planning_sprint(self, project: str | None) -> Sprint:
        """Construct a fresh ``PLANNING`` sprint with the next number.

        Returns:
            An unsaved PLANNING sprint numbered after the project's latest.
        """
        existing = await self._sprints.query(
            SprintFilterSpec(
                project=NotBlankStr(project) if project is not None else None
            )
        )
        number = 1 + max((s.sprint_number for s in existing), default=0)
        return Sprint(
            id=NotBlankStr(str(uuid4())),
            project=NotBlankStr(project) if project is not None else None,
            name=NotBlankStr(f"Sprint {number}"),
            sprint_number=number,
            duration_days=self._sprint_config.duration_days,
        )

    async def _collect_backlog(self, trigger: Task) -> tuple[Task, ...]:
        """Return the project's open tasks to seed a new sprint (capped).

        Queries per non-terminal status so a project whose first rows are
        terminal cannot starve the backlog of the open tasks that exist.

        Returns:
            The non-terminal tasks for the trigger's project (at most
            ``max_tasks_per_sprint``).
        """
        cap = self._sprint_config.max_tasks_per_sprint
        project = self._project_of(trigger)
        collected: list[Task] = []
        seen: set[object] = set()
        for status in NON_TERMINAL_TASK_STATUSES:
            if len(collected) >= cap:
                break
            for task in await self._tasks.query(
                TaskFilterSpec(project=project, status=status), limit=cap
            ):
                if task.id not in seen:
                    seen.add(task.id)
                    collected.append(task)
        return open_backlog_tasks(tuple(collected), cap=cap)

    @staticmethod
    def _project_of(task: Task) -> NotBlankStr:
        """Return the task's project id as the sprint scope key.

        Returns:
            The task's project id.
        """
        return task.project

    def _now_iso(self) -> str:
        """Return the current UTC time as an ISO-8601 string."""
        return self._clock.now().isoformat()

    def _log_transition(self, sprint: Sprint, previous: SprintStatus) -> None:
        """Emit the state-transition INFO log after a persisted hop."""
        logger.info(
            SPRINT_STATUS_TRANSITIONED,
            sprint_id=sprint.id,
            from_status=previous.value,
            to_status=sprint.status.value,
        )


__all__ = ["SprintService"]
