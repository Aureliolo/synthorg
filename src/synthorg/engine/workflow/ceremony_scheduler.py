"""Ceremony scheduler -- runtime coordination between sprints and meetings.

The ``CeremonyScheduler`` owns ceremony trigger state (counters,
fired-once tracking) and delegates scheduling decisions to the active
``CeremonySchedulingStrategy``.  It bridges triggered ceremonies into
``MeetingScheduler.trigger_event()`` calls.

See ``docs/design/ceremony-scheduling.md`` for the full design.
"""

import asyncio
import time
from typing import TYPE_CHECKING, Any

from synthorg.engine.workflow.ceremony_bridge import (
    build_trigger_event_name,
)
from synthorg.engine.workflow.ceremony_context import CeremonyEvalContext
from synthorg.engine.workflow.ceremony_policy import (
    TRIGGER_SPRINT_END,
    TRIGGER_SPRINT_MIDPOINT,
    TRIGGER_SPRINT_START,
    CeremonyStrategyType,
)
from synthorg.engine.workflow.sprint_lifecycle import Sprint, SprintStatus
from synthorg.engine.workflow.strategy_migration import (
    StrategyMigrationInfo,
    detect_strategy_migration,
)
from synthorg.observability import get_logger
from synthorg.observability.events.workflow import (
    SPRINT_AUTO_TRANSITION,
    SPRINT_CEREMONY_DEACTIVATION_HOOK_FAILED,
    SPRINT_CEREMONY_SCHEDULER_START_FAILED,
    SPRINT_CEREMONY_SCHEDULER_STARTED,
    SPRINT_CEREMONY_SCHEDULER_STOPPED,
    SPRINT_CEREMONY_SKIPPED,
    SPRINT_CEREMONY_STRATEGY_CHANGED,
    SPRINT_CEREMONY_STRATEGY_HOOK_FAILED,
    SPRINT_CEREMONY_TRIGGER_FAILED,
    SPRINT_CEREMONY_TRIGGERED,
)

if TYPE_CHECKING:
    from synthorg.communication.meeting.scheduler import MeetingScheduler
    from synthorg.engine.workflow.ceremony_strategy import (
        CeremonySchedulingStrategy,
    )
    from synthorg.engine.workflow.sprint_config import (
        SprintCeremonyConfig,
        SprintConfig,
    )
    from synthorg.engine.workflow.sprint_velocity import VelocityRecord

logger = get_logger(__name__)

_MIDPOINT_THRESHOLD: float = 0.5
_COMPLETE_THRESHOLD: float = 1.0

_ONE_SHOT_TRIGGERS: frozenset[str] = frozenset(
    {TRIGGER_SPRINT_START, TRIGGER_SPRINT_END, TRIGGER_SPRINT_MIDPOINT}
)


class CeremonyScheduler:
    """Runtime coordinator between sprint lifecycle and meeting system.

    Owns ceremony trigger state (counters, fired-once tracking).
    Delegates scheduling decisions to the active
    ``CeremonySchedulingStrategy``.  Delegates meeting execution to the
    existing ``MeetingScheduler``.

    Strategy is locked per-sprint (set at ``activate_sprint`` time).
    Counters are ephemeral and reset per sprint.

    All public async methods are serialized via an internal
    ``asyncio.Lock`` to prevent counter corruption from concurrent
    task-completion events.

    Args:
        meeting_scheduler: The existing MeetingScheduler for executing
            ceremonies as meetings.
    """

    __slots__ = (
        "_activation_time",
        "_active_sprint",
        "_active_strategy",
        "_completion_counters",
        "_fired_once_triggers",
        "_lock",
        "_meeting_scheduler",
        "_running",
        "_sprint_config",
        "_total_completions",
        "_velocity_history",
    )

    def __init__(
        self,
        *,
        meeting_scheduler: MeetingScheduler,
    ) -> None:
        self._meeting_scheduler = meeting_scheduler
        self._active_strategy: CeremonySchedulingStrategy | None = None
        self._active_sprint: Sprint | None = None
        self._sprint_config: SprintConfig | None = None
        self._completion_counters: dict[str, int] = {}
        self._fired_once_triggers: set[str] = set()
        self._total_completions: int = 0
        self._running = False
        self._activation_time: float = 0.0
        self._velocity_history: tuple[VelocityRecord, ...] = ()
        self._lock = asyncio.Lock()

    @property
    def running(self) -> bool:
        """Whether the scheduler has an active sprint."""
        return self._running

    @property
    def active_sprint(self) -> Sprint | None:
        """The currently active sprint, or None."""
        return self._active_sprint

    @property
    def active_strategy(self) -> CeremonySchedulingStrategy | None:
        """The currently active strategy, or None.

        Note: eventually consistent -- callers needing a consistent
        (strategy, sprint) pair should use ``get_active_info`` instead.
        """
        return self._active_strategy

    async def get_active_info(
        self,
    ) -> tuple[CeremonySchedulingStrategy | None, Sprint | None]:
        """Read active strategy and sprint atomically under the lock.

        Returns:
            Tuple of (active_strategy, active_sprint), both None when
            no sprint is running.
        """
        async with self._lock:
            return self._active_strategy, self._active_sprint

    async def activate_sprint(
        self,
        sprint: Sprint,
        config: SprintConfig,
        strategy: CeremonySchedulingStrategy,
        *,
        velocity_history: tuple[VelocityRecord, ...] = (),
    ) -> StrategyMigrationInfo | None:
        """Start tracking ceremonies for the given sprint.

        Initializes counters, locks the strategy, and calls the
        strategy's ``on_sprint_activated`` hook.

        Validates strategy config before activation.  Fires any
        ``sprint_start`` one-shot ceremonies immediately.  If
        activation fails partway through, the scheduler is
        deactivated to avoid partial state.

        The caller is responsible for invoking
        ``notify_strategy_migration()`` with the returned info
        and an ``AgentMessenger`` when migration is detected.

        Args:
            sprint: The sprint to activate (should be ACTIVE).
            config: Sprint configuration.
            strategy: The ceremony scheduling strategy to use.
            velocity_history: Recent velocity records for context.

        Returns:
            Migration info if the strategy type changed from the
            previous sprint, else ``None``.

        Raises:
            Exception: Any exception from
                ``strategy.on_sprint_activated()`` or sprint-start
                ceremony firing propagates after the scheduler is
                deactivated.  ``MemoryError`` and
                ``RecursionError`` propagate immediately without
                cleanup.
        """
        async with self._lock:
            previous_strategy_type = (
                self._active_strategy.strategy_type if self._active_strategy else None
            )
            previous_velocity_history_size = len(self._velocity_history)

            if self._running:
                await self._deactivate_sprint_unlocked()

            strategy.validate_strategy_config(
                config.ceremony_policy.strategy_config or {},
            )

            self._active_sprint = sprint
            self._sprint_config = config
            self._active_strategy = strategy
            self._velocity_history = velocity_history
            self._completion_counters = {c.name: 0 for c in config.ceremonies}
            self._fired_once_triggers = set()
            self._total_completions = 0
            self._activation_time = time.monotonic()
            self._running = True

            try:
                await strategy.on_sprint_activated(sprint, config)
                await self._fire_sprint_start_ceremonies(sprint, config)
            except MemoryError, RecursionError:
                raise
            except Exception:
                logger.exception(
                    SPRINT_CEREMONY_SCHEDULER_START_FAILED,
                    sprint_id=sprint.id,
                    note="activation failed, deactivating",
                )
                await self._deactivate_sprint_unlocked()
                raise

            logger.info(
                SPRINT_CEREMONY_SCHEDULER_STARTED,
                sprint_id=sprint.id,
                strategy=strategy.strategy_type.value,
                ceremony_count=len(config.ceremonies),
            )

            return self._detect_migration(
                previous_strategy_type,
                strategy,
                sprint,
                previous_velocity_history_size,
            )

    def _detect_migration(
        self,
        previous_strategy_type: CeremonyStrategyType | None,
        strategy: CeremonySchedulingStrategy,
        sprint: Sprint,
        previous_velocity_history_size: int,
    ) -> StrategyMigrationInfo | None:
        """Detect and log a strategy migration (if any)."""
        migration = detect_strategy_migration(
            previous_strategy_type,
            strategy.strategy_type,
            sprint.id,
            previous_velocity_history_size,
        )
        if migration is not None:
            logger.info(
                SPRINT_CEREMONY_STRATEGY_CHANGED,
                sprint_id=sprint.id,
                previous_strategy=migration.previous_strategy.value,
                new_strategy=migration.new_strategy.value,
                velocity_history_size=migration.velocity_history_size,
            )
        return migration

    async def deactivate_sprint(self) -> None:
        """Stop tracking the current sprint's ceremonies.

        Calls the strategy's ``on_sprint_deactivated`` hook.
        No-op if the scheduler is not running.
        """
        async with self._lock:
            await self._deactivate_sprint_unlocked()

    async def _deactivate_sprint_unlocked(self) -> None:
        """Deactivate without acquiring the lock (caller holds it)."""
        if not self._running:
            logger.debug(
                SPRINT_CEREMONY_SCHEDULER_STOPPED,
                note="already_inactive",
            )
            return

        if self._active_strategy is not None:
            try:
                await self._active_strategy.on_sprint_deactivated()
            except MemoryError, RecursionError:
                raise
            except Exception:
                logger.exception(
                    SPRINT_CEREMONY_DEACTIVATION_HOOK_FAILED,
                    sprint_id=(
                        self._active_sprint.id if self._active_sprint else "unknown"
                    ),
                )

        sprint_id = self._active_sprint.id if self._active_sprint else "unknown"

        self._active_sprint = None
        self._sprint_config = None
        self._active_strategy = None
        self._completion_counters = {}
        self._fired_once_triggers = set()
        self._total_completions = 0
        self._running = False

        logger.info(
            SPRINT_CEREMONY_SCHEDULER_STOPPED,
            sprint_id=sprint_id,
        )

    async def on_task_completed(
        self,
        sprint: Sprint,
        task_id: str,
        story_points: float,
    ) -> Sprint:
        """Handle a task completion event.

        Evaluates all trigger-based ceremonies via the active strategy,
        fires matching ones via ``MeetingScheduler.trigger_event()``,
        and checks auto-transition.

        Args:
            sprint: Current sprint state (after task completion).
            task_id: The completed task ID.
            story_points: Points earned.

        Returns:
            The sprint, possibly auto-transitioned by the active
            strategy.
        """
        async with self._lock:
            if not self._running or self._active_strategy is None:
                logger.debug(
                    SPRINT_CEREMONY_SKIPPED,
                    note="scheduler_not_active",
                    task_id=task_id,
                )
                return sprint
            assert self._sprint_config is not None  # noqa: S101

            self._active_sprint = sprint
            self._total_completions += 1

            context = self._build_context(sprint)
            try:
                await self._active_strategy.on_task_completed(
                    sprint,
                    task_id,
                    story_points,
                    context,
                )
            except MemoryError, RecursionError:
                raise
            except Exception:
                logger.exception(
                    SPRINT_CEREMONY_STRATEGY_HOOK_FAILED,
                    task_id=task_id,
                    sprint_id=sprint.id,
                )
                return sprint
            await self._evaluate_ceremonies(sprint)
            await self._check_one_shot_triggers(sprint, context)
            return self._check_auto_transition(sprint, context)

    # -- Ceremony evaluation -------------------------------------------

    async def _evaluate_ceremonies(self, sprint: Sprint) -> None:
        """Evaluate and fire per-task ceremonies."""
        assert self._sprint_config is not None  # noqa: S101
        assert self._active_strategy is not None  # noqa: S101

        for ceremony in self._sprint_config.ceremonies:
            if self._is_one_shot_fired(ceremony.name):
                continue

            trigger = _get_trigger(ceremony)
            if trigger in _ONE_SHOT_TRIGGERS:
                continue

            self._completion_counters[ceremony.name] += 1

            ctx = self._build_ceremony_context(ceremony.name, sprint)
            if self._active_strategy.should_fire_ceremony(
                ceremony,
                sprint,
                ctx,
            ):
                success = await self._trigger_ceremony(
                    ceremony.name,
                    sprint,
                )
                if success:
                    self._completion_counters[ceremony.name] = 0

    def _check_auto_transition(
        self,
        sprint: Sprint,
        context: CeremonyEvalContext,
    ) -> Sprint:
        """Check and apply auto-transition if strategy says so."""
        assert self._active_strategy is not None  # noqa: S101
        assert self._sprint_config is not None  # noqa: S101

        policy = self._sprint_config.ceremony_policy
        if policy.auto_transition is False:
            return sprint

        target = self._active_strategy.should_transition_sprint(
            sprint,
            self._sprint_config,
            context,
        )
        if target is not None and sprint.status is SprintStatus.ACTIVE:
            logger.info(
                SPRINT_AUTO_TRANSITION,
                sprint_id=sprint.id,
                from_status=sprint.status.value,
                to_status=target.value,
                strategy=self._active_strategy.strategy_type.value,
            )
            sprint = sprint.with_transition(target)
            self._active_sprint = sprint
        return sprint

    # -- One-shot ceremonies -------------------------------------------

    async def _fire_sprint_start_ceremonies(
        self,
        sprint: Sprint,
        config: SprintConfig,
    ) -> None:
        """Fire ceremonies configured with sprint_start trigger."""
        tasks: list[tuple[str, Sprint]] = []
        for ceremony in config.ceremonies:
            trigger = _get_trigger(ceremony)
            if trigger == TRIGGER_SPRINT_START:
                tasks.append((ceremony.name, sprint))

        await self._fire_ceremonies_parallel(tasks)

    async def _check_one_shot_triggers(
        self,
        sprint: Sprint,
        context: CeremonyEvalContext,
    ) -> None:
        """Check and fire midpoint/end one-shot ceremonies."""
        if self._sprint_config is None:
            return

        tasks: list[tuple[str, Sprint]] = []
        for ceremony in self._sprint_config.ceremonies:
            trigger = _get_trigger(ceremony)
            if trigger is None:
                continue
            not_fired = ceremony.name not in self._fired_once_triggers
            pct = context.sprint_percentage_complete

            is_midpoint = (
                trigger == TRIGGER_SPRINT_MIDPOINT and pct >= _MIDPOINT_THRESHOLD
            )
            is_end = trigger == TRIGGER_SPRINT_END and pct >= _COMPLETE_THRESHOLD
            if not_fired and (is_midpoint or is_end):
                tasks.append((ceremony.name, sprint))

        await self._fire_ceremonies_parallel(tasks)

    async def _fire_ceremonies_parallel(
        self,
        ceremonies: list[tuple[str, Sprint]],
    ) -> None:
        """Fire multiple ceremonies in parallel, marking one-shots."""
        if not ceremonies:
            return

        async def _fire(
            name: str,
            sprint: Sprint,
        ) -> tuple[str, bool]:
            success = await self._trigger_ceremony(name, sprint)
            return (name, success)

        async with asyncio.TaskGroup() as tg:
            tasks = [tg.create_task(_fire(name, sprint)) for name, sprint in ceremonies]

        for task in tasks:
            name, success = task.result()
            if success:
                self._fired_once_triggers.add(name)

    # -- Context building ----------------------------------------------

    @staticmethod
    def _compute_sprint_progress(
        sprint: Sprint,
    ) -> tuple[int, int, float]:
        """Compute task progress metrics from a sprint.

        Returns:
            Tuple of (total_tasks, completed, percentage_complete).
        """
        total_tasks = len(sprint.task_ids)
        completed = len(sprint.completed_task_ids)
        pct = completed / total_tasks if total_tasks > 0 else 0.0
        return total_tasks, completed, pct

    def _build_context(self, sprint: Sprint) -> CeremonyEvalContext:
        """Build a CeremonyEvalContext for the current state.

        In the global context (used for strategy hooks and
        auto-transition), ``completions_since_last_trigger`` is set
        to 0 because there is no specific ceremony in scope.
        Per-ceremony contexts use ``_build_ceremony_context`` instead.
        """
        total_tasks, _, pct = self._compute_sprint_progress(sprint)

        return CeremonyEvalContext(
            completions_since_last_trigger=0,
            total_completions_this_sprint=self._total_completions,
            total_tasks_in_sprint=total_tasks,
            elapsed_seconds=time.monotonic() - self._activation_time,
            # Budget integration is a follow-up.
            budget_consumed_fraction=0.0,
            budget_remaining=0.0,
            velocity_history=self._velocity_history,
            external_events=(),
            sprint_percentage_complete=pct,
            story_points_completed=sprint.story_points_completed,
            story_points_committed=sprint.story_points_committed,
        )

    def _build_ceremony_context(
        self,
        ceremony_name: str,
        sprint: Sprint,
    ) -> CeremonyEvalContext:
        """Build context for a specific ceremony (per-ceremony counter)."""
        total_tasks, _, pct = self._compute_sprint_progress(sprint)

        return CeremonyEvalContext(
            completions_since_last_trigger=self._completion_counters.get(
                ceremony_name,
                0,
            ),
            total_completions_this_sprint=self._total_completions,
            total_tasks_in_sprint=total_tasks,
            elapsed_seconds=time.monotonic() - self._activation_time,
            # Budget integration is a follow-up.
            budget_consumed_fraction=0.0,
            budget_remaining=0.0,
            velocity_history=self._velocity_history,
            external_events=(),
            sprint_percentage_complete=pct,
            story_points_completed=sprint.story_points_completed,
            story_points_committed=sprint.story_points_committed,
        )

    # -- Trigger execution ---------------------------------------------

    def _is_one_shot_fired(self, ceremony_name: str) -> bool:
        """Check if a one-shot ceremony has already fired."""
        return ceremony_name in self._fired_once_triggers

    async def _trigger_ceremony(
        self,
        ceremony_name: str,
        sprint: Sprint,
    ) -> bool:
        """Fire a ceremony via MeetingScheduler.trigger_event.

        Returns:
            ``True`` if the ceremony was successfully triggered,
            ``False`` if the trigger failed (logged and swallowed).
        """
        event_name = build_trigger_event_name(ceremony_name, sprint.id)
        context: dict[str, Any] = {
            "sprint_id": sprint.id,
            "ceremony": ceremony_name,
            "completed_tasks": len(sprint.completed_task_ids),
            "total_tasks": len(sprint.task_ids),
        }

        logger.info(
            SPRINT_CEREMONY_TRIGGERED,
            ceremony=ceremony_name,
            sprint_id=sprint.id,
            event_name=event_name,
        )

        try:
            await self._meeting_scheduler.trigger_event(
                event_name,
                context=context,
            )
        except MemoryError, RecursionError:
            raise
        except Exception:
            logger.exception(
                SPRINT_CEREMONY_TRIGGER_FAILED,
                ceremony=ceremony_name,
                sprint_id=sprint.id,
                note="trigger_event failed",
            )
            return False
        return True


def _get_trigger(ceremony: SprintCeremonyConfig) -> str | None:
    """Extract the trigger string from a ceremony's policy override."""
    if ceremony.policy_override is None:
        return None
    sc = ceremony.policy_override.strategy_config or {}
    return sc.get("trigger")
