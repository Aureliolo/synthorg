# module-kind: complex_service
"""Ceremony scheduler -- runtime coordination between sprints and meetings.

The ``CeremonyScheduler`` owns ceremony trigger state (counters,
fired-once tracking) and delegates scheduling decisions to the active
``CeremonySchedulingStrategy``.  It bridges triggered ceremonies into
``MeetingScheduler.trigger_event()`` calls.

See ``docs/design/ceremony-scheduling.md`` for the full design.
"""

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
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
from synthorg.engine.workflow.ceremony_strategy import (
    CeremonySchedulingStrategy,
)
from synthorg.engine.workflow.sprint_config import (
    SprintCeremonyConfig,
    SprintConfig,
)
from synthorg.engine.workflow.sprint_lifecycle import Sprint, SprintStatus
from synthorg.engine.workflow.sprint_velocity import VelocityRecord
from synthorg.engine.workflow.strategy_migration import (
    StrategyMigrationInfo,
    detect_strategy_migration,
)
from synthorg.observability import get_logger, log_exception_redacted
from synthorg.observability.events.workflow import (
    SPRINT_AUTO_TRANSITION,
    SPRINT_CEREMONY_BUDGET_SNAPSHOT_FAILED,
    SPRINT_CEREMONY_DEACTIVATION_HOOK_FAILED,
    SPRINT_CEREMONY_SCHEDULER_START_FAILED,
    SPRINT_CEREMONY_SCHEDULER_STARTED,
    SPRINT_CEREMONY_SCHEDULER_STOPPED,
    SPRINT_CEREMONY_SKIPPED,
    SPRINT_CEREMONY_STRATEGY_CHANGED,
    SPRINT_CEREMONY_STRATEGY_HOOK_FAILED,
    SPRINT_CEREMONY_TRIGGER_FAILED,
    SPRINT_CEREMONY_TRIGGERED,
    SPRINT_STATUS_TRANSITIONED,
)
from synthorg.persistence.ceremony_scheduler_state_protocol import (
    CeremonySchedulerStateRecord,
    CeremonySchedulerStateRepository,
)

if TYPE_CHECKING:
    from synthorg.communication.meeting.scheduler import MeetingScheduler

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
        "_budget_snapshot",
        "_clock",
        "_completion_counters",
        "_fired_once_triggers",
        "_lock",
        "_meeting_scheduler",
        "_running",
        "_sprint_config",
        "_state_repo",
        "_total_completions",
        "_velocity_history",
    )

    def __init__(
        self,
        *,
        meeting_scheduler: MeetingScheduler,
        clock: Clock | None = None,
        budget_snapshot: Callable[[], tuple[float, float]] | None = None,
        state_repo: CeremonySchedulerStateRepository | None = None,
    ) -> None:
        """Wire the scheduler against the meeting subsystem.

        Args:
            meeting_scheduler: The MeetingScheduler that dispatches
                ceremony meetings.
            clock: Optional Clock for the activation timestamp seam.
            budget_snapshot: Optional sync callable returning the
                current ``(consumed_fraction, remaining)`` pair. When
                provided, the scheduler threads the values into every
                CeremonyEvalContext so budget-driven strategies can
                evaluate against live spend. When ``None`` the context
                fields fall back to ``(0.0, 0.0)`` and the runtime
                logs a single ``SPRINT_CEREMONY_BUDGET_BRIDGE_OFF``
                event at scheduler activation so operators know the
                strategy is running blind.
            state_repo: Optional persistence repository for ceremony
                scheduler state snapshots. When supplied the scheduler
                writes one snapshot per mutation (under its own lock)
                and rehydrates the four state attributes at sprint
                activation so trigger position survives restarts.
        """
        self._meeting_scheduler = meeting_scheduler
        self._clock = clock or SystemClock()
        self._budget_snapshot = budget_snapshot
        self._state_repo = state_repo
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

    def _resolve_budget_snapshot(self) -> tuple[float, float]:
        """Return the live budget snapshot, or zeros when none is wired.

        Errors from the snapshot callable are swallowed (with a
        warning) so a transient budget-service failure cannot break
        ceremony evaluation. The strategy then evaluates against
        ``(0.0, 0.0)`` for that one tick.
        """
        if self._budget_snapshot is None:
            return (0.0, 0.0)
        try:
            return self._budget_snapshot()
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                SPRINT_CEREMONY_BUDGET_SNAPSHOT_FAILED,
                error_type=type(exc).__name__,
            )
            return (0.0, 0.0)

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
            self._activation_time = self._clock.monotonic()
            self._running = True

            # Hydrate persisted sprint state so trigger counters resume
            # after a process restart mid-sprint instead of restarting
            # from zero. Runs after the reset above so a sprint with no
            # prior persisted state still starts cleanly; a persisted
            # row is merged onto the freshly-reset attributes.
            await self._hydrate_state_from_repo(sprint.id)

            try:
                await strategy.on_sprint_activated(sprint, config)
                # Decide the sprint-start ceremonies while holding the
                # lock (pure); fire them after releasing it.
                start_ceremonies = self._select_sprint_start_ceremonies(config)
                # Persist inside the protected block so a failed
                # snapshot write rolls back like any other activation
                # failure -- otherwise the scheduler stays running with
                # state half-written and a retry double-triggers.
                await self._save_state_unlocked(sprint.id)
            except Exception as exc:
                reraise_critical(exc)
                log_exception_redacted(
                    logger,
                    SPRINT_CEREMONY_SCHEDULER_START_FAILED,
                    exc,
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

            migration = self._detect_migration(
                previous_strategy_type,
                strategy,
                sprint,
                previous_velocity_history_size,
            )

        # Fire sprint-start ceremonies OUTSIDE the lock: the AI-backed
        # meeting chain must not run under ``self._lock`` (deadlock /
        # serialisation risk). Mark the fired one-shots and re-persist,
        # but only if this sprint is still the active one (a concurrent
        # deactivate/re-activate could have moved on).
        fired = await self._fire_ceremonies(start_ceremonies, sprint)
        if fired:
            async with self._lock:
                if (
                    self._running
                    and self._active_sprint is not None
                    and self._active_sprint.id == sprint.id
                ):
                    self._fired_once_triggers.update(fired)
                    await self._save_state_unlocked(sprint.id)
        return migration

    async def _hydrate_state_from_repo(self, sprint_id: str) -> None:
        """Load persisted ceremony state for ``sprint_id`` if available.

        Called from inside ``activate_sprint`` AFTER the fresh-state
        reset, so a sprint with no prior persisted state continues
        to start cleanly. The caller already holds ``self._lock``.
        """
        if self._state_repo is None:
            return
        try:
            record = await self._state_repo.get(NotBlankStr(sprint_id))
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                SPRINT_CEREMONY_SCHEDULER_START_FAILED,
                sprint_id=sprint_id,
                note="state_repo_get_failed",
                error_type=type(exc).__name__,
            )
            return
        if record is None:
            return

        # Decode AND validate before mutating any state: a partially
        # corrupt or stale row must leave the freshly-seeded zeroed
        # state intact rather than abort activate_sprint or strand a
        # half-applied snapshot. ``ValidationError`` is a ``ValueError``
        # subclass, so model_validate failures are covered here too.
        try:
            counters = json.loads(record.completion_counters_json)
            triggers = json.loads(record.fired_once_triggers_json)
            history_raw = json.loads(record.velocity_history_json)
            velocity_history = tuple(
                VelocityRecord.model_validate(r) for r in history_raw
            )
        except (ValueError, TypeError) as exc:
            logger.warning(
                SPRINT_CEREMONY_SCHEDULER_START_FAILED,
                sprint_id=sprint_id,
                note="state_repo_payload_decode_failed",
                error_type=type(exc).__name__,
            )
            return

        # Structurally validate the decoded payloads before touching
        # any state: a stale / hand-edited row whose JSON parses but
        # carries the wrong shape (non-int counts, non-string triggers)
        # must leave the freshly-seeded zeroed state intact rather than
        # poison the in-memory counters.
        counters_ok = isinstance(counters, dict) and all(
            isinstance(k, str)
            and isinstance(v, int)
            and not isinstance(v, bool)
            and v >= 0
            for k, v in counters.items()
        )
        triggers_ok = isinstance(triggers, list) and all(
            isinstance(t, str) for t in triggers
        )
        if not (counters_ok and triggers_ok):
            logger.warning(
                SPRINT_CEREMONY_SCHEDULER_START_FAILED,
                sprint_id=sprint_id,
                note="state_repo_payload_shape_invalid",
            )
            return

        # Merge persisted counts onto the seeded map instead of
        # replacing it: a ceremony present in the current config but
        # absent from an older snapshot keeps its zero seed rather than
        # vanishing (which would KeyError on its next completion), and a
        # stale snapshot key for a removed ceremony is ignored.
        for name, value in counters.items():
            if name in self._completion_counters:
                self._completion_counters[name] = value
        self._fired_once_triggers = set(triggers)
        self._total_completions = record.total_completions
        self._velocity_history = velocity_history

    async def _save_state_unlocked(self, sprint_id: str) -> None:
        """Persist the current ceremony state snapshot under the existing lock.

        Caller already holds ``self._lock``; this method does NOT
        acquire it. Best-effort: a persistence failure logs at WARNING
        and propagates so the surrounding mutation method can decide
        whether to re-raise. The mutation has already landed in
        memory, so a missed save just means a restart will rehydrate
        from the prior persisted snapshot rather than the most recent.
        """
        if self._state_repo is None:
            return
        record = CeremonySchedulerStateRecord(
            sprint_id=NotBlankStr(sprint_id),
            completion_counters_json=json.dumps(
                self._completion_counters, sort_keys=True
            ),
            fired_once_triggers_json=json.dumps(sorted(self._fired_once_triggers)),
            total_completions=self._total_completions,
            velocity_history_json=json.dumps(
                [r.model_dump(mode="json") for r in self._velocity_history],
                sort_keys=True,
            ),
            updated_at=datetime.now(UTC),
        )
        try:
            await self._state_repo.save(record)
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                SPRINT_CEREMONY_SCHEDULER_START_FAILED,
                sprint_id=sprint_id,
                note="state_repo_save_failed",
                error_type=type(exc).__name__,
            )
            raise

    def _detect_migration(
        self,
        previous_strategy_type: CeremonyStrategyType | None,
        strategy: CeremonySchedulingStrategy,
        sprint: Sprint,
        previous_velocity_history_size: int,
    ) -> StrategyMigrationInfo | None:
        """Detect and log a strategy migration (if any).

        Returns:
            A :class:`StrategyMigrationInfo` describing the change when
            the active strategy type differs from the previous sprint's
            strategy, ``None`` when there is no migration.
        """
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
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                log_exception_redacted(
                    logger,
                    SPRINT_CEREMONY_DEACTIVATION_HOOK_FAILED,
                    exc,
                    sprint_id=self._active_sprint.id
                    if self._active_sprint
                    else "unknown",
                )

        sprint_id = self._active_sprint.id if self._active_sprint else "unknown"

        # Drop the persisted snapshot when the sprint deactivates so a
        # future activation of a different sprint reusing the same id
        # starts from clean state rather than stale counters.
        if self._state_repo is not None and self._active_sprint is not None:
            try:
                await self._state_repo.delete(
                    NotBlankStr(self._active_sprint.id),
                )
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                logger.warning(
                    SPRINT_CEREMONY_SCHEDULER_STOPPED,
                    sprint_id=sprint_id,
                    note="state_repo_delete_failed",
                    error_type=type(exc).__name__,
                )

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
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                log_exception_redacted(
                    logger,
                    SPRINT_CEREMONY_STRATEGY_HOOK_FAILED,
                    exc,
                    task_id=task_id,
                    sprint_id=sprint.id,
                )
                return sprint
            # Decide which ceremonies fire WHILE the lock is held (pure,
            # no I/O); fire them after releasing it.
            per_task = self._select_per_task_ceremonies(sprint)
            one_shot = self._select_one_shot_ceremonies(context)
            transitioned = self._check_auto_transition(sprint, context)

        # Fire OUTSIDE the lock so the AI-backed meeting chain does not
        # serialise unrelated callers and a meeting-driven re-entrant
        # ``on_task_completed`` cannot deadlock on ``self._lock``.
        fired_per_task = await self._fire_ceremonies(per_task, sprint)
        fired_one_shot = await self._fire_ceremonies(one_shot, sprint)

        async with self._lock:
            for name in fired_per_task:
                if name in self._completion_counters:
                    self._completion_counters[name] = 0
            self._fired_once_triggers.update(fired_one_shot)
            await self._save_state_unlocked(sprint.id)
        return transitioned

    # -- Ceremony evaluation -------------------------------------------

    def _select_per_task_ceremonies(self, sprint: Sprint) -> list[str]:
        """Decide which per-task ceremonies should fire (no I/O).

        Pure selection run under ``self._lock``: it increments the
        per-ceremony completion counters and consults the strategy's
        ``should_fire_ceremony`` decision, but does NOT call
        ``trigger_event`` (which drives the AI-backed meeting chain).
        Firing happens outside the lock; the caller resets the counters
        for successfully-fired ceremonies afterwards.

        Returns:
            The names of the per-task ceremonies selected to fire.
        """
        assert self._sprint_config is not None  # noqa: S101
        assert self._active_strategy is not None  # noqa: S101

        selected: list[str] = []
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
                selected.append(ceremony.name)
        return selected

    def _check_auto_transition(
        self,
        sprint: Sprint,
        context: CeremonyEvalContext,
    ) -> Sprint:
        """Check and apply auto-transition if strategy says so.

        Returns:
            The (possibly transitioned) sprint object. When the
            strategy targets a transition while the sprint is
            ``ACTIVE``, returns the sprint after
            :py:meth:`Sprint.with_transition` is applied; otherwise
            returns ``sprint`` unchanged.
        """
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
            previous_status = sprint.status.value
            logger.info(
                SPRINT_AUTO_TRANSITION,
                sprint_id=sprint.id,
                from_status=previous_status,
                to_status=target.value,
                strategy=self._active_strategy.strategy_type.value,
            )
            sprint = sprint.with_transition(target)
            self._active_sprint = sprint
            # Sprint state is in-memory on the scheduler (no sprint
            # repository exists today), so this is a transition of the
            # cached object, not a persistence write. Logged at DEBUG
            # so it does not get treated as an audit-grade transition
            # event alongside the persisted ``client.request`` family.
            logger.debug(
                SPRINT_STATUS_TRANSITIONED,
                sprint_id=sprint.id,
                from_status=previous_status,
                to_status=sprint.status.value,
            )
        return sprint

    # -- One-shot ceremonies -------------------------------------------

    @staticmethod
    def _select_sprint_start_ceremonies(config: SprintConfig) -> list[str]:
        """Names of ceremonies configured with the sprint_start trigger.

        Returns:
            The sprint-start ceremony names (pure; no I/O).
        """
        return [
            ceremony.name
            for ceremony in config.ceremonies
            if _get_trigger(ceremony) == TRIGGER_SPRINT_START
        ]

    def _select_one_shot_ceremonies(
        self,
        context: CeremonyEvalContext,
    ) -> list[str]:
        """Decide which midpoint/end one-shot ceremonies should fire.

        Pure selection run under ``self._lock``; firing and the
        ``_fired_once_triggers`` marking happen outside the lock.

        Returns:
            The names of the one-shot ceremonies selected to fire.
        """
        if self._sprint_config is None:
            return []

        selected: list[str] = []
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
                selected.append(ceremony.name)
        return selected

    async def _fire_ceremonies(
        self,
        names: list[str],
        sprint: Sprint,
    ) -> list[str]:
        """Fire ceremonies in parallel, OUTSIDE any lock.

        ``_trigger_ceremony`` drives ``MeetingScheduler.trigger_event``
        (the AI-backed meeting orchestration chain), so this must never
        run while ``self._lock`` is held: a coarse lock across those
        network-bound calls serialised unrelated callers and risked a
        re-entrant deadlock if a meeting run called back into
        ``on_task_completed``. State writes driven by the outcome
        (counter resets, one-shot marking) are applied by the caller
        under the lock after this returns.

        Returns:
            The names of ceremonies that fired successfully.
        """
        if not names:
            return []

        async def _fire(name: str) -> tuple[str, bool]:
            success = await self._trigger_ceremony(name, sprint)
            return (name, success)

        try:
            async with asyncio.TaskGroup() as tg:
                tasks = [tg.create_task(_fire(name)) for name in names]
        except BaseExceptionGroup as group:
            # ``_trigger_ceremony`` swallows every non-critical Exception
            # (returns False), so the only thing that escapes a child task
            # is an interpreter-critical (MemoryError/RecursionError)
            # re-raised by ``reraise_critical``. Unwrap and propagate it
            # directly so callers see the fatal condition rather than a
            # TaskGroup wrapper.
            criticals, _ = group.split((MemoryError, RecursionError))
            if criticals is not None:
                raise criticals.exceptions[0] from None
            raise

        fired: list[str] = []
        for task in tasks:
            name, success = task.result()
            if success:
                fired.append(name)
        return fired

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

        Returns:
            A :class:`CeremonyEvalContext` populated with sprint-wide
            progress, budget snapshot, velocity history, and zero
            per-ceremony counters.
        """
        total_tasks, _, pct = self._compute_sprint_progress(sprint)
        consumed_fraction, remaining = self._resolve_budget_snapshot()

        return CeremonyEvalContext(
            completions_since_last_trigger=0,
            total_completions_this_sprint=self._total_completions,
            total_tasks_in_sprint=total_tasks,
            elapsed_seconds=self._clock.monotonic() - self._activation_time,
            budget_consumed_fraction=consumed_fraction,
            budget_remaining=remaining,
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
        """Build context for a specific ceremony (per-ceremony counter).

        Returns:
            A :class:`CeremonyEvalContext` carrying the per-ceremony
            completions counter alongside the same sprint-wide metrics
            as :py:meth:`_build_context`.
        """
        total_tasks, _, pct = self._compute_sprint_progress(sprint)
        consumed_fraction, remaining = self._resolve_budget_snapshot()

        return CeremonyEvalContext(
            completions_since_last_trigger=self._completion_counters.get(
                ceremony_name,
                0,
            ),
            total_completions_this_sprint=self._total_completions,
            total_tasks_in_sprint=total_tasks,
            elapsed_seconds=self._clock.monotonic() - self._activation_time,
            budget_consumed_fraction=consumed_fraction,
            budget_remaining=remaining,
            velocity_history=self._velocity_history,
            external_events=(),
            sprint_percentage_complete=pct,
            story_points_completed=sprint.story_points_completed,
            story_points_committed=sprint.story_points_committed,
        )

    # -- Trigger execution ---------------------------------------------

    def _is_one_shot_fired(self, ceremony_name: str) -> bool:
        """Check if a one-shot ceremony has already fired.

        Returns:
            ``True`` if the ceremony was already fired this sprint,
            ``False`` otherwise.
        """
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
        context: dict[str, object] = {
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
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            log_exception_redacted(
                logger,
                SPRINT_CEREMONY_TRIGGER_FAILED,
                exc,
                ceremony=ceremony_name,
                sprint_id=sprint.id,
                note="trigger_event failed",
            )
            return False
        return True


def _get_trigger(ceremony: SprintCeremonyConfig) -> str | None:
    """Extract the trigger string from a ceremony's policy override.

    Returns:
        The configured trigger string, or ``None`` when the ceremony
        has no policy override or no ``trigger`` key in its strategy
        config.
    """
    if ceremony.policy_override is None:
        return None
    sc = ceremony.policy_override.strategy_config or {}
    return cast("str | None", sc.get("trigger"))
