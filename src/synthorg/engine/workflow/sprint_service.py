# module-kind: service
"""Runtime orchestration for agile sprints in ``agile_kanban`` orgs.

The service:

* registers as a :class:`TaskEngine` observer. On a task entering
  ``ASSIGNED`` for a project with no open sprint, it assembles a sprint
  from the project's open tasks and inserts it ALREADY ``ACTIVE``, in one
  write: there is no PLANNING row in between, because that sprint is
  never offered to an operator and a window in which it sits PLANNING is
  a window in which a dead process leaves a row nothing activates, which
  then holds the scope's one open slot for good. On a task entering
  ``COMPLETED``, it marks the task done in the open sprint's backlog and
  advances ``ACTIVE -> IN_REVIEW`` once every task in the backlog has
  been delivered;
* drives the tail of the lifecycle (``IN_REVIEW -> RETROSPECTIVE ->
  COMPLETED``) once the backlog is fully delivered;
* exposes explicit control (``create_sprint`` builds an empty PLANNING
  shell; ``add_task`` / ``start_sprint`` / ``advance_sprint`` are the
  REST overrides);
* answers ``is_task_workable`` for the advisory Kanban board gate.

The DB writes (backlog + lifecycle CAS) run inline; the tail advancement
runs in tracked background tasks so the single-consumer observer-dispatch
loop never blocks on a sequence of CAS writes.

Persistence is the sole source of truth: the sprint is read back from the
repository on every operation and every mutation is a guarded write. Each
of the four decisions a sprint takes is settled by the database rather
than by this process, because each is a read followed by a write that two
processes can enter at once:

* **which task is delivered** -- ``complete_task_if``, one conditional
  statement that appends to ``completed_task_ids`` only when the id is
  absent, so a concurrent completion of a different task cannot be
  overwritten by a caller holding a stale pre-image;
* **what is in the backlog** -- ``add_task_if_planning``, the same shape
  one state earlier, so two requests each adding a different task cannot
  end with one of them silently absent;
* **which lifecycle state the sprint is in** -- ``transition_if``;
* **who opens a scope's sprint** -- a partial unique index admitting one
  non-completed sprint per scope, so the loser of the create race is
  refused rather than producing a second sprint holding the same tasks.

The per-service ``asyncio.Lock`` remains, and is now only what its name
says: an in-process serialiser that keeps this process from doing
obviously redundant work. Correctness does not rest on it.
"""

import asyncio
from collections.abc import Coroutine
from typing import Final

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.persistence_errors import ConstraintViolationError
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus
from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import (
    SprintBacklogFullError,
    SprintBacklogInvalidError,
    SprintNotFoundError,
    SprintTransitionConflictError,
)
from synthorg.engine.task_engine_models import TaskStateChanged
from synthorg.engine.workflow._sprint_ops import (
    NON_TERMINAL_TASK_STATUSES,
    log_sprint_transition,
    next_status,
    open_backlog_tasks,
    story_points_for,
    transition_overrides,
)
from synthorg.engine.workflow.enums import WorkflowType
from synthorg.engine.workflow.sprint_backlog import add_task_to_sprint
from synthorg.engine.workflow.sprint_config import SprintConfig
from synthorg.engine.workflow.sprint_lifecycle import (
    OPEN_SPRINT_STATUSES,
    STORY_POINTS_CEILING,
    Sprint,
    SprintStatus,
)
from synthorg.engine.workflow.sprint_scope import (
    build_planning_sprint,
    log_refusal,
    require_scope_free,
    scope_occupant,
    scope_occupied_error,
    scope_spec,
)
from synthorg.engine.workflow.sprint_tail import advance_tail
from synthorg.engine.workflow.sprint_writes import (
    record_completion,
    rejected_add_error,
)
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
)
from synthorg.observability.events.workflow import (
    SPRINT_CREATE_RACE_LOST,
    SPRINT_CREATED,
    SPRINT_SERVICE_OBSERVER_FAILED,
    SPRINT_TAIL_DRAIN_TIMED_OUT,
    SPRINT_TAIL_NOT_SPAWNED,
    SPRINT_TASK_ADDED,
    SPRINT_TASK_COMPLETED,
)
from synthorg.persistence.sprint_protocol import SprintFilterSpec, SprintRepository
from synthorg.persistence.task_protocol import TaskFilterSpec, TaskRepository
from synthorg.settings.resolver_protocol import ConfigResolverProtocol

logger = get_logger(__name__)

_SETTINGS_NS: Final[str] = "engine"

#: How long :meth:`SprintService.drain` waits for the in-flight tail walks.
#: Each is a short chain of compare-and-sets, so this is generous for the
#: work and tight enough to leave the shutdown window room: the runner adds
#: its own grace on top of this value rather than repeating a number, so the
#: outer backstop cannot drift below the inner deadline it is meant to
#: exceed.
SPRINT_TAIL_DRAIN_DEADLINE_SECONDS: Final[float] = 5.0


class SprintService:
    """Create, run, and advance sprints for ``agile_kanban`` projects.

    Args:
        sprint_repository: Durable sprint store.
        task_repository: Task store, read to seed a sprint backlog.
        config_resolver: Settings resolver for the hot ``sprint_enabled``
            and ``workflow_type`` gates.
        sprint_config: Effective sprint configuration (duration, backlog
            cap, velocity window). Captured at wire time; locked per sprint.
        clock: Clock seam for the start/end timestamps.
    """

    __slots__ = (
        "_bg_tasks",
        "_clock",
        "_config_resolver",
        "_draining",
        "_lock",
        "_sprint_config",
        "_sprints",
        "_tasks",
    )

    def __init__(
        self,
        *,
        sprint_repository: SprintRepository,
        task_repository: TaskRepository,
        config_resolver: ConfigResolverProtocol,
        sprint_config: SprintConfig | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._sprints = sprint_repository
        self._tasks = task_repository
        self._config_resolver = config_resolver
        self._sprint_config = sprint_config or SprintConfig()
        self._clock = clock or SystemClock()
        # Serialises the read-modify-write critical sections so two
        # concurrent completions in this process cannot both advance the
        # same sprint; transition_if is the cross-process backstop.
        self._lock = asyncio.Lock()
        # In-flight tail-advancement tasks spawned off the observer so a
        # chain of CAS writes never blocks the dispatch loop; drained on
        # shutdown.
        self._bg_tasks: set[asyncio.Task[None]] = set()
        self._draining = False

    def _spawn(self, coro: Coroutine[object, object, None]) -> None:
        """Run *coro* as a tracked background task (never blocks the caller).

        Refused once a drain has begun. The observer stays registered until
        late in shutdown, so without this a completion arriving mid-drain
        adds a task the drain has already decided its budget for, and the
        drain either waits past its deadline or abandons a walk it never
        counted. Whatever this declines is a lifecycle hop, and the
        recovery sweep is what picks those up.
        """
        if self._draining:
            logger.info(SPRINT_TAIL_NOT_SPAWNED, reason="draining")
            coro.close()
            return
        task = asyncio.create_task(self._guard(coro))
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    async def _guard(self, coro: Coroutine[object, object, None]) -> None:
        """Await *coro*, redacting any non-critical failure off the dispatch loop."""
        try:
            await coro
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- best-effort side channel
            reraise_critical(exc)
            log_exception_redacted(
                logger,
                SPRINT_SERVICE_OBSERVER_FAILED,
                exc,
                phase="tail_advance",
            )

    async def drain(self) -> None:
        """Await all in-flight background tail-advancement tasks.

        Used by graceful shutdown and by tests that assert on the state a
        spawned advancement leaves behind. Closes the service to new tail
        work first, so the set this waits on can only shrink: without that
        the loop is unbounded by construction, since each walk it awaits
        can be joined by another the still-registered observer spawns.

        Bounded on top of that, because a hop blocked on an unreachable
        store would otherwise hold the shutdown window open with no
        ceiling. What a timed-out drain leaves half-walked is exactly what
        the next process's boot recovery pass picks up.
        """
        self._draining = True
        try:
            async with asyncio.timeout(SPRINT_TAIL_DRAIN_DEADLINE_SECONDS):
                while self._bg_tasks:
                    await asyncio.gather(*tuple(self._bg_tasks), return_exceptions=True)
        except TimeoutError:
            logger.warning(
                SPRINT_TAIL_DRAIN_TIMED_OUT,
                pending_count=len(self._bg_tasks),
                deadline_seconds=SPRINT_TAIL_DRAIN_DEADLINE_SECONDS,
            )
        finally:
            self._draining = False

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
        if not await self.sprints_active():
            return True
        sprint = await self._open_sprint(project)
        if sprint is None:
            return True
        return task_id in sprint.task_ids

    # -- Explicit control surface --------------------------------------

    async def create_sprint(self, project: str | None) -> Sprint:
        """Create and persist a new ``PLANNING`` sprint for *project*.

        A scope runs one sprint at a time, so this refuses while the scope
        still has one that is not completed. The check here is what names
        the sprint in the way; the partial unique index is what makes the
        answer true across processes, and its refusal is translated to the
        same error so both callers are told the same thing.

        Returns:
            The persisted PLANNING sprint.

        Raises:
            SprintAlreadyOpenError: When the scope already has a sprint
                that has not completed.
        """
        async with self._lock:
            await require_scope_free(project, sprints=self._sprints)
            sprint = await build_planning_sprint(
                project, sprints=self._sprints, config=self._sprint_config
            )
            try:
                await self._sprints.save(sprint)
            except ConstraintViolationError as exc:
                # Bound before the raise so the statement names an error
                # rather than the builder that returns one; raised inline,
                # the docstring gate reads `scope_occupied_error` itself as
                # the exception type and asks for it by that name.
                occupied = scope_occupied_error(project)
                raise occupied from exc
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

        The append itself is one guarded statement, so two requests each
        adding a different task cannot both write a backlog assembled from
        the same pre-image. The capacity and points checks stay here
        because they are this service's configuration rather than the
        row's invariants, and a caller is owed the reason.

        Returns:
            The sprint with the task added to its backlog.

        Raises:
            SprintNotFoundError: When *sprint_id* has no row.
            SprintTransitionConflictError: When the sprint is not
                ``PLANNING`` (tasks may only be added while planning).
            SprintBacklogFullError: When the backlog is at
                ``max_tasks_per_sprint``.
            SprintBacklogInvalidError: When the points are negative, or
                the task is already in the backlog.
        """
        async with self._lock:
            sprint = await self._require(sprint_id)
            if sprint.status is not SprintStatus.PLANNING:
                msg = f"Sprint {sprint_id!r} is not in 'planning'; cannot add tasks"
                log_refusal(
                    reason="not_planning",
                    sprint_id=sprint_id,
                    status=sprint.status.value,
                )
                raise SprintTransitionConflictError(msg)
            if len(sprint.task_ids) >= self._sprint_config.max_tasks_per_sprint:
                msg = (
                    f"Sprint {sprint_id!r} backlog is full "
                    f"({self._sprint_config.max_tasks_per_sprint} tasks)"
                )
                log_refusal(
                    reason="backlog_full",
                    sprint_id=sprint_id,
                    cap=self._sprint_config.max_tasks_per_sprint,
                )
                raise SprintBacklogFullError(msg)
            if story_points < 0:
                msg = f"story_points must be >= 0, got {story_points}"
                log_refusal(
                    reason="negative_points",
                    sprint_id=sprint_id,
                    task_id=task_id,
                )
                raise SprintBacklogInvalidError(msg)
            # Checked against the RESULTING total, not this task's points.
            # The API bounds each task by the ceiling separately, so two
            # admissible calls can carry the sprint past it, and the
            # statement derives the total rather than being handed one.
            committed = sprint.story_points_committed + story_points
            if committed > STORY_POINTS_CEILING:
                msg = (
                    f"Adding {story_points} points would take sprint "
                    f"{sprint_id!r} to {committed}, past the "
                    f"{STORY_POINTS_CEILING} ceiling"
                )
                log_refusal(
                    reason="points_ceiling",
                    sprint_id=sprint_id,
                    task_id=task_id,
                    would_commit=committed,
                )
                raise SprintBacklogInvalidError(msg)
            updated = await self._sprints.add_task_if_planning(
                NotBlankStr(sprint_id), NotBlankStr(task_id), story_points
            )
        if updated is None:
            log_refusal(
                reason="add_guard_declined", sprint_id=sprint_id, task_id=task_id
            )
            raise await rejected_add_error(sprint_id, task_id, sprints=self._sprints)
        logger.info(
            SPRINT_TASK_ADDED,
            sprint_id=sprint_id,
            task_id=task_id,
            story_points=story_points,
        )
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
        # One message for both refusals: they are the same fact reached two
        # ways, before the write and by the write, and a caller cannot act
        # on the difference.
        msg = f"Sprint {sprint_id!r} is not in 'planning'"
        async with self._lock:
            sprint = await self._require(sprint_id)
            if sprint.status is not SprintStatus.PLANNING:
                log_refusal(
                    reason="not_planning",
                    sprint_id=sprint_id,
                    status=sprint.status.value,
                )
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
                log_refusal(reason="activation_cas_lost", sprint_id=sprint_id)
                raise SprintTransitionConflictError(msg)
        log_sprint_transition(started, SprintStatus.PLANNING)
        return started

    async def advance_sprint(self, sprint_id: str) -> Sprint:
        """Advance a sprint one hop along its linear lifecycle.

        The explicit override the REST surface exposes; delivery of the
        whole backlog advances the sprint on its own. Stamps
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
                log_refusal(
                    reason="advance_cas_lost",
                    sprint_id=sprint_id,
                    status=sprint.status.value,
                    target=target.value,
                )
                raise SprintTransitionConflictError(msg)
        log_sprint_transition(advanced, sprint.status)
        return advanced

    # -- Task-engine observer ------------------------------------------

    async def on_task_state_changed(self, event: TaskStateChanged) -> None:
        """Best-effort observer: keep the sprint in step with task state.

        On a task entering ``ASSIGNED`` for an ``agile_kanban`` project
        with no open sprint, auto-create + start one seeded with the
        project's open tasks. On a task entering ``COMPLETED``, mark it
        done in the open sprint and open review once the backlog is
        delivered. Never raises into the engine's observer dispatch.
        """
        phase = "unknown"
        try:
            if event.task is None or event.new_status is None:
                return
            if not await self.sprints_active():
                return
            if event.new_status is TaskStatus.ASSIGNED:
                phase = "ensure_sprint_for_work"
                await self._ensure_sprint_for_work(event.task)
            elif event.new_status is TaskStatus.COMPLETED:
                phase = "handle_completion"
                await self._handle_completion(event.task)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- best-effort side channel
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
        """Create an already-ACTIVE sprint for *task*'s project when none is open.

        Assembled whole in memory and inserted once, ACTIVE. There is
        deliberately no PLANNING row in between: the auto-created sprint is
        never offered to an operator to edit, so a window in which it sits
        PLANNING is a window in which the process can die and leave a row
        nothing activates, holding the scope's one open slot against every
        later task while contributing no work. Building it whole also means
        the insert is the entire decision, so the index refusing it is the
        only outcome a lost race has.
        """
        scope = self._project_of(task)
        async with self._lock:
            if await scope_occupant(scope, sprints=self._sprints) is not None:
                return
            sprint = await build_planning_sprint(
                scope, sprints=self._sprints, config=self._sprint_config
            )
            for backlog_task in await self._collect_backlog(task):
                sprint = add_task_to_sprint(
                    sprint,
                    NotBlankStr(str(backlog_task.id)),
                    story_points_for(backlog_task),
                )
            started = sprint.with_transition(
                SprintStatus.ACTIVE, start_date=self._now_iso()
            )
            try:
                await self._sprints.save(started)
            except ConstraintViolationError:
                # lint-allow: swallow-ok -- losing this race is the correct
                # outcome, not a failure: the check above and the index below
                # ask the same question, and another writer answered it first.
                # The scope has the sprint it needs; this process joins it on
                # the next event rather than opening a second one.
                logger.info(
                    SPRINT_CREATE_RACE_LOST,
                    project=scope,
                    sprint_number=started.sprint_number,
                )
                return
            logger.info(
                SPRINT_CREATED,
                sprint_id=started.id,
                project=scope,
                sprint_number=started.sprint_number,
                seeded_tasks=len(started.task_ids),
            )
        log_sprint_transition(started, SprintStatus.PLANNING)

    async def _handle_completion(self, task: Task) -> None:
        """Mark *task* done in the open sprint, then advance off-loop.

        The backlog write commits inline (source of truth) through the
        guarded append, so a concurrent completion of a different task in
        the same sprint cannot be overwritten. The lifecycle tail runs in a
        background task so the observer dispatch loop never waits on a
        chain of CAS writes.
        """
        task_id = str(task.id)
        async with self._lock:
            sprint = await self._open_sprint(self._project_of(task))
            if sprint is None or task_id not in sprint.task_ids:
                return
            delivered = await record_completion(sprint, task_id, sprints=self._sprints)
            if delivered is None:
                return
        logger.info(SPRINT_TASK_COMPLETED, sprint_id=delivered.id, task_id=task_id)
        self._spawn(self._advance_tail(delivered))

    async def _advance_tail(self, sprint: Sprint) -> None:
        """Off-loop tail: open review when delivered, then walk to COMPLETED."""
        await advance_tail(sprint, sprints=self._sprints, clock=self._clock)

    # -- Helpers -------------------------------------------------------

    async def sprints_active(self) -> bool:
        """Whether sprint machinery applies to the current org (hot-read).

        Public because the recovery sweep asks the same question, and it
        has to be asked live in both places: an operator turning sprints
        off should stop the observer AND the sweep from the next event or
        tick, without a restart.

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
        """Return the scope's ACTIVE / IN_REVIEW sprint, newest-first.

        Returns:
            The open sprint for *project*'s scope, or ``None``.
        """
        for sprint in await self._sprints.query(scope_spec(project)):
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


__all__ = ["SprintService"]
