"""Calendar ceremony scheduling strategy.

Ceremonies fire on wall-clock cadence using ``MeetingFrequency`` intervals,
regardless of task progress.  Sprints auto-transition at the configured
``duration_days`` boundary.  Rolling averages are weighted by sprint
duration.  This is a time-based strategy with minimal internal state
for tracking when each ceremony last fired.
"""

from collections.abc import Mapping

from synthorg.engine.workflow.ceremony_context import CeremonyEvalContext
from synthorg.engine.workflow.ceremony_policy import CeremonyStrategyType
from synthorg.engine.workflow.sprint_config import (
    SprintCeremonyConfig,
    SprintConfig,
)
from synthorg.engine.workflow.sprint_lifecycle import Sprint, SprintStatus
from synthorg.engine.workflow.strategies._helpers import (
    KEY_DURATION_DAYS,
    KEY_FREQUENCY,
    SECONDS_PER_DAY,
    VALID_FREQUENCIES,
    resolve_duration_days,
    resolve_interval,
    validate_duration_days,
)
from synthorg.engine.workflow.velocity_types import VelocityCalcType
from synthorg.observability import get_logger
from synthorg.observability.events.workflow import (
    SPRINT_AUTO_TRANSITION,
    SPRINT_CEREMONY_SKIPPED,
    SPRINT_CEREMONY_TRIGGERED,
)

logger = get_logger(__name__)

_KNOWN_CONFIG_KEYS: frozenset[str] = frozenset({KEY_DURATION_DAYS, KEY_FREQUENCY})


class CalendarStrategy:
    """Calendar ceremony scheduling strategy.

    Ceremonies fire on a wall-clock cadence defined by
    ``MeetingFrequency`` intervals:

    - ``daily``, ``weekly``, ``bi_weekly``, ``per_sprint_day``,
      ``monthly``.

    The frequency is resolved from ``ceremony.frequency`` first,
    then falls back to ``strategy_config["frequency"]``.

    Auto-transition: ACTIVE to IN_REVIEW when elapsed time reaches
    the configured ``duration_days`` boundary.  Task completion
    does **not** trigger transition.

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
        """Fire when wall-clock interval has elapsed since last fire.

        Resolves the interval from ``ceremony.frequency`` (primary)
        or ``strategy_config["frequency"]`` (fallback).

        Args:
            ceremony: The ceremony being evaluated.
            sprint: Current sprint state.
            context: Evaluation context with elapsed time.

        Returns:
            ``True`` if the ceremony should fire.
        """
        interval = resolve_interval(ceremony, "calendar")
        if interval is None:
            logger.debug(
                SPRINT_CEREMONY_SKIPPED,
                ceremony=ceremony.name,
                reason="no_frequency",
                strategy="calendar",
            )
            return False

        last_fire = self._last_fire_elapsed.get(ceremony.name, 0.0)
        if context.elapsed_seconds - last_fire >= interval:
            self._last_fire_elapsed[ceremony.name] = context.elapsed_seconds
            logger.info(
                SPRINT_CEREMONY_TRIGGERED,
                ceremony=ceremony.name,
                strategy="calendar",
                elapsed_seconds=context.elapsed_seconds,
                interval_seconds=interval,
            )
            return True

        logger.debug(
            SPRINT_CEREMONY_SKIPPED,
            ceremony=ceremony.name,
            strategy="calendar",
            elapsed_seconds=context.elapsed_seconds,
            next_fire_at=last_fire + interval,
        )
        return False

    def should_transition_sprint(
        self,
        sprint: Sprint,
        config: SprintConfig,
        context: CeremonyEvalContext,
    ) -> SprintStatus | None:
        """Return IN_REVIEW when the duration_days boundary is reached.

        Only transitions from ACTIVE status.  Task completion does
        not affect calendar-based transition.

        Args:
            sprint: Current sprint state.
            config: Sprint configuration.
            context: Evaluation context with elapsed time.

        Returns:
            ``SprintStatus.IN_REVIEW`` if boundary reached, else ``None``.
        """
        if sprint.status is not SprintStatus.ACTIVE:
            return None

        duration_days = resolve_duration_days(config, "calendar")
        duration_seconds = duration_days * SECONDS_PER_DAY
        if context.elapsed_seconds >= duration_seconds:
            logger.info(
                SPRINT_AUTO_TRANSITION,
                strategy="calendar",
                reason="time_elapsed",
                sprint_id=sprint.id,
                elapsed_seconds=context.elapsed_seconds,
                duration_seconds=duration_seconds,
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

    # -- Lifecycle hooks (no-op for calendar strategy) ----------------

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
        payload: Mapping[str, object],
    ) -> None:
        """No-op."""

    @property
    def strategy_type(self) -> CeremonyStrategyType:
        """Return CALENDAR."""
        return CeremonyStrategyType.CALENDAR

    def get_default_velocity_calculator(self) -> VelocityCalcType:
        """Return CALENDAR velocity calculator."""
        return VelocityCalcType.CALENDAR

    def validate_strategy_config(
        self,
        config: Mapping[str, object],
    ) -> None:
        """Validate calendar strategy config.

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

        freq = config.get(KEY_FREQUENCY)
        if freq is not None and freq not in VALID_FREQUENCIES:
            msg = (
                f"Invalid frequency {freq!r}. "
                f"Valid frequencies: {sorted(VALID_FREQUENCIES)}"
            )
            raise ValueError(msg)
