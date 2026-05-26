"""Hybrid (first-wins) ceremony scheduling strategy.

Both calendar and task-driven triggers exist on each ceremony.
Whichever fires first wins and resets the calendar timer.  Calendar
provides a guaranteed minimum cadence; task triggers fire on
throughput milestones.
"""

from collections.abc import Mapping  # noqa: TC003 -- used at runtime
from typing import TYPE_CHECKING, Any

from synthorg.engine.workflow.ceremony_context import (
    CeremonyEvalContext,  # noqa: TC001 -- used at runtime
)
from synthorg.engine.workflow.ceremony_policy import CeremonyStrategyType
from synthorg.engine.workflow.sprint_lifecycle import Sprint, SprintStatus
from synthorg.engine.workflow.strategies._helpers import (
    DEFAULT_TRANSITION_THRESHOLD,
    KEY_DURATION_DAYS,
    KEY_EVERY_N,
    KEY_FREQUENCY,
    KEY_SPRINT_PERCENTAGE,
    KEY_TRIGGER,
    SECONDS_PER_DAY,
    STICKY_TRIGGERS,
    evaluate_task_trigger,
    resolve_duration_days,
    resolve_interval,
    validate_duration_days,
    validate_every_n,
    validate_frequency,
    validate_sprint_percentage,
    validate_trigger,
)
from synthorg.engine.workflow.velocity_types import VelocityCalcType
from synthorg.observability import get_logger
from synthorg.observability.events.workflow import (
    SPRINT_AUTO_TRANSITION,
    SPRINT_CEREMONY_SKIPPED,
    SPRINT_CEREMONY_TRIGGERED,
)

if TYPE_CHECKING:
    from synthorg.engine.workflow.sprint_config import (
        SprintCeremonyConfig,
        SprintConfig,
    )

logger = get_logger(__name__)

_KNOWN_CONFIG_KEYS: frozenset[str] = frozenset(
    {
        KEY_DURATION_DAYS,
        KEY_EVERY_N,
        KEY_FREQUENCY,
        KEY_SPRINT_PERCENTAGE,
        KEY_TRIGGER,
    }
)


class HybridStrategy:
    """Hybrid (first-wins) ceremony scheduling strategy.

    Combines calendar and task-driven triggers.  Whichever fires
    first wins and resets the cadence:

    - **Calendar leg**: fires when wall-clock interval elapses
      (resolved from ``ceremony.frequency`` or
      ``strategy_config["frequency"]``).
    - **Task-driven leg**: fires on ``every_n_completions`` or
      ``sprint_percentage`` thresholds (from
      ``strategy_config``).

    When either leg fires, the calendar timer resets so that the
    next calendar check starts from the fire time.  The task-driven
    leg is also suppressed until the next calendar interval to
    prevent percentage-based triggers from firing repeatedly once
    their threshold is crossed.

    Auto-transition: ACTIVE to IN_REVIEW on whichever comes first:
    task completion threshold (only when tasks exist) *or* calendar
    duration boundary.

    This strategy maintains a small ``_last_fire_elapsed`` dict to
    track when each ceremony last fired, preventing double-firing
    within the same interval.  State is cleared on sprint lifecycle
    transitions.
    """

    __slots__ = ("_last_fire_elapsed",)

    def __init__(self) -> None:
        self._last_fire_elapsed: dict[str, float] = {}

    def should_fire_ceremony(
        self,
        ceremony: SprintCeremonyConfig,
        sprint: Sprint,  # noqa: ARG002
        context: CeremonyEvalContext,
    ) -> bool:
        """Fire when either calendar interval or task threshold is met.

        Whichever fires first wins.  On fire, the calendar timer
        resets to the current ``elapsed_seconds``.

        Args:
            ceremony: The ceremony being evaluated.
            sprint: Current sprint state.
            context: Evaluation context with counters and timings.

        Returns:
            ``True`` if the ceremony should fire.
        """
        calendar_fires = self._check_calendar(ceremony, context)
        task_fires = (
            False if calendar_fires else self._check_task_driven(ceremony, context)
        )

        fires = calendar_fires or task_fires
        if fires:
            # Reset calendar timer regardless of which leg fired.
            self._last_fire_elapsed[ceremony.name] = context.elapsed_seconds
            logger.info(
                SPRINT_CEREMONY_TRIGGERED,
                ceremony=ceremony.name,
                strategy="hybrid",
                calendar_fired=calendar_fires,
                task_fired=task_fires,
                elapsed_seconds=context.elapsed_seconds,
            )
        else:
            logger.debug(
                SPRINT_CEREMONY_SKIPPED,
                ceremony=ceremony.name,
                strategy="hybrid",
            )
        return fires

    def should_transition_sprint(
        self,
        sprint: Sprint,
        config: SprintConfig,
        context: CeremonyEvalContext,
    ) -> SprintStatus | None:
        """Return IN_REVIEW when either time or task threshold is met.

        Calendar leg: ``elapsed_seconds >= duration_days * 86400``.
        Task leg: ``sprint_percentage_complete >= transition_threshold``
        (only when there are tasks).

        Args:
            sprint: Current sprint state.
            config: Sprint configuration.
            context: Evaluation context.

        Returns:
            ``SprintStatus.IN_REVIEW`` if either threshold met,
            else ``None``.
        """
        if sprint.status is not SprintStatus.ACTIVE:
            return None

        # Calendar leg.
        duration_days = resolve_duration_days(config, "hybrid")
        duration_seconds = duration_days * SECONDS_PER_DAY
        if context.elapsed_seconds >= duration_seconds:
            logger.info(
                SPRINT_AUTO_TRANSITION,
                strategy="hybrid",
                reason="time_elapsed",
                sprint_id=sprint.id,
                elapsed_seconds=context.elapsed_seconds,
                duration_seconds=duration_seconds,
            )
            return SprintStatus.IN_REVIEW

        # Task-driven leg.
        if context.total_tasks_in_sprint > 0:
            threshold: float = (
                config.ceremony_policy.transition_threshold
                if config.ceremony_policy.transition_threshold is not None
                else DEFAULT_TRANSITION_THRESHOLD
            )
            if context.sprint_percentage_complete >= threshold:
                logger.info(
                    SPRINT_AUTO_TRANSITION,
                    strategy="hybrid",
                    reason="task_threshold",
                    sprint_id=sprint.id,
                    sprint_pct=context.sprint_percentage_complete,
                    threshold=threshold,
                )
                return SprintStatus.IN_REVIEW

        return None

    # -- Lifecycle hooks (clear state on sprint transitions) ----------

    async def on_sprint_activated(
        self,
        sprint: Sprint,  # noqa: ARG002
        config: SprintConfig,  # noqa: ARG002
    ) -> None:
        """Clear fire tracking for a new sprint."""
        self._last_fire_elapsed.clear()

    async def on_sprint_deactivated(self) -> None:
        """Clear fire tracking when sprint ends."""
        self._last_fire_elapsed.clear()

    # -- Lifecycle hooks (no-op for hybrid strategy) ------------------

    async def on_task_completed(
        self,
        sprint: Sprint,
        task_id: str,
        story_points: float,
        context: CeremonyEvalContext,
    ) -> None:
        """No-op."""

    async def on_task_added(
        self,
        sprint: Sprint,
        task_id: str,
    ) -> None:
        """No-op."""

    async def on_task_blocked(
        self,
        sprint: Sprint,
        task_id: str,
    ) -> None:
        """No-op."""

    async def on_budget_updated(
        self,
        sprint: Sprint,
        budget_consumed_fraction: float,
    ) -> None:
        """No-op."""

    async def on_external_event(
        self,
        sprint: Sprint,
        event_name: str,
        payload: Mapping[str, Any],
    ) -> None:
        """No-op."""

    @property
    def strategy_type(self) -> CeremonyStrategyType:
        """Return HYBRID."""
        return CeremonyStrategyType.HYBRID

    def get_default_velocity_calculator(self) -> VelocityCalcType:
        """Return MULTI_DIMENSIONAL velocity calculator."""
        return VelocityCalcType.MULTI_DIMENSIONAL

    def validate_strategy_config(
        self,
        config: Mapping[str, Any],
    ) -> None:
        """Validate hybrid strategy config.

        Accepts keys from both calendar and task-driven strategies.

        Args:
            config: Strategy config to validate.

        Raises:
            ValueError: If the config contains invalid keys or values.
        """
        unknown = set(config) - _KNOWN_CONFIG_KEYS
        if unknown:
            msg = f"Unknown config keys: {sorted(unknown)}"
            raise ValueError(msg)

        validate_duration_days(config.get(KEY_DURATION_DAYS))
        validate_trigger(config.get(KEY_TRIGGER))
        validate_every_n(config.get(KEY_EVERY_N))
        validate_sprint_percentage(config.get(KEY_SPRINT_PERCENTAGE))
        validate_frequency(config.get(KEY_FREQUENCY))

    # -- Internal helpers -----------------------------------------------

    def _check_calendar(
        self,
        ceremony: SprintCeremonyConfig,
        context: CeremonyEvalContext,
    ) -> bool:
        """Check the calendar (time-based) leg.

        Returns:
            ``True`` when the elapsed time since the ceremony's last
            fire meets the configured interval; ``False`` otherwise.
        """
        interval = resolve_interval(ceremony, "hybrid")
        if interval is None:
            return False
        last_fire = self._last_fire_elapsed.get(ceremony.name, 0.0)
        return context.elapsed_seconds - last_fire >= interval

    def _check_task_driven(
        self,
        ceremony: SprintCeremonyConfig,
        context: CeremonyEvalContext,
    ) -> bool:
        """Check the task-driven leg.

        For sticky triggers (sprint_end, sprint_midpoint,
        sprint_percentage) whose condition stays permanently true
        once crossed, the task leg is suppressed if the ceremony
        has already fired within the current calendar interval.
        Non-sticky triggers (every_n_completions) reset naturally
        via ``completions_since_last_trigger`` and are not suppressed.

        Returns:
            ``True`` when the task-driven trigger fires (and is not
            suppressed by the sticky-fire window); ``False``
            otherwise.
        """
        if ceremony.policy_override is None:
            return False
        config = ceremony.policy_override.strategy_config or {}
        trigger = config.get(KEY_TRIGGER)
        if trigger is None:
            return False

        # Suppress sticky triggers if the ceremony has already fired
        # and the calendar interval has not yet elapsed.  This prevents
        # infinite re-firing for percentage-based triggers whose
        # condition stays true once crossed.  Only applies after at
        # least one fire -- the task leg must be free to fire *before*
        # the first calendar interval to preserve "first-wins"
        # semantics.
        if trigger in STICKY_TRIGGERS:
            interval = resolve_interval(ceremony, "hybrid")
            if interval is not None and ceremony.name in self._last_fire_elapsed:
                last_fire = self._last_fire_elapsed[ceremony.name]
                if context.elapsed_seconds - last_fire < interval:
                    return False

        return evaluate_task_trigger(trigger, config, context)
