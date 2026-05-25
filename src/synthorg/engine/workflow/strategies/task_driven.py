"""Task-driven ceremony scheduling strategy.

Ceremonies fire at task-count milestones. Sprints complete when tasks
reach terminal status, not on a timer. This is the reference
implementation for the ``CeremonySchedulingStrategy`` protocol.
"""

from typing import TYPE_CHECKING, Any

from synthorg.engine.workflow.ceremony_policy import (
    TRIGGER_EVERY_N,
    TRIGGER_SPRINT_END,
    TRIGGER_SPRINT_MIDPOINT,
    TRIGGER_SPRINT_PERCENTAGE,
    TRIGGER_SPRINT_START,
    CeremonyStrategyType,
)
from synthorg.engine.workflow.sprint_lifecycle import Sprint, SprintStatus
from synthorg.engine.workflow.strategies._helpers import get_ceremony_config
from synthorg.engine.workflow.velocity_types import VelocityCalcType
from synthorg.observability import get_logger
from synthorg.observability.events.workflow import (
    SPRINT_CEREMONY_SKIPPED,
    SPRINT_CEREMONY_TRIGGERED,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from synthorg.engine.workflow.ceremony_context import CeremonyEvalContext
    from synthorg.engine.workflow.sprint_config import (
        SprintCeremonyConfig,
        SprintConfig,
    )

logger = get_logger(__name__)

# Strategy config keys.
_KEY_EVERY_N = "every_n_completions"
_KEY_SPRINT_PERCENTAGE = "sprint_percentage"
_KEY_TRIGGER = "trigger"

_MIDPOINT_THRESHOLD: float = 0.5
_DEFAULT_EVERY_N: int = 5
_DEFAULT_SPRINT_PCT: float = 50.0
_MAX_SPRINT_PCT: float = 100.0
_DEFAULT_TRANSITION_THRESHOLD: float = 1.0

_VALID_TRIGGERS: frozenset[str] = frozenset(
    {
        TRIGGER_SPRINT_START,
        TRIGGER_SPRINT_END,
        TRIGGER_SPRINT_MIDPOINT,
        TRIGGER_EVERY_N,
        TRIGGER_SPRINT_PERCENTAGE,
    }
)

_KNOWN_CONFIG_KEYS: frozenset[str] = frozenset(
    {_KEY_TRIGGER, _KEY_EVERY_N, _KEY_SPRINT_PERCENTAGE}
)


class TaskDrivenStrategy:
    """Task-driven ceremony scheduling strategy.

    Ceremonies fire at task-count milestones:

    - ``every_n_completions``: fire after every N task completions.
    - ``sprint_percentage``: fire when X% of tasks are complete.
    - ``sprint_start``: fire once on sprint activation.
    - ``sprint_end``: fire once when all tasks complete.
    - ``sprint_midpoint``: fire once at 50% task completion.

    Auto-transition: ACTIVE to IN_REVIEW when the fraction of completed
    tasks meets or exceeds the configured ``transition_threshold``.

    This is a stateless strategy -- all lifecycle hooks are no-ops.
    """

    __slots__ = ()

    def should_fire_ceremony(
        self,
        ceremony: SprintCeremonyConfig,
        sprint: Sprint,  # noqa: ARG002
        context: CeremonyEvalContext,
    ) -> bool:
        """Evaluate whether a ceremony should fire.

        Reads the ceremony's ``policy_override.strategy_config`` to
        determine the trigger type and parameters.

        Args:
            ceremony: The ceremony being evaluated.
            sprint: Current sprint state.
            context: Evaluation context with counters and metrics.

        Returns:
            ``True`` if the ceremony should fire.
        """
        config = get_ceremony_config(ceremony)
        trigger = config.get(_KEY_TRIGGER)

        if trigger is None:
            return False

        result = self._evaluate_trigger(trigger, config, context)

        if result:
            logger.info(
                SPRINT_CEREMONY_TRIGGERED,
                ceremony=ceremony.name,
                trigger=trigger,
                strategy="task_driven",
            )
        else:
            logger.debug(
                SPRINT_CEREMONY_SKIPPED,
                ceremony=ceremony.name,
                trigger=trigger,
                strategy="task_driven",
            )
        return result

    def should_transition_sprint(
        self,
        sprint: Sprint,
        config: SprintConfig,
        context: CeremonyEvalContext,
    ) -> SprintStatus | None:
        """Return IN_REVIEW when completion threshold is met.

        Only transitions from ACTIVE status.

        Args:
            sprint: Current sprint state.
            config: Sprint configuration.
            context: Evaluation context.

        Returns:
            ``SprintStatus.IN_REVIEW`` if threshold met, else ``None``.
        """
        if sprint.status is not SprintStatus.ACTIVE:
            return None
        if context.total_tasks_in_sprint == 0:
            return None

        threshold: float = (
            config.ceremony_policy.transition_threshold
            if config.ceremony_policy.transition_threshold is not None
            else _DEFAULT_TRANSITION_THRESHOLD
        )

        if context.sprint_percentage_complete >= threshold:
            return SprintStatus.IN_REVIEW
        return None

    # -- Lifecycle hooks (all no-ops for stateless strategy) -----------

    async def on_sprint_activated(
        self,
        sprint: Sprint,
        config: SprintConfig,
    ) -> None:
        """No-op."""

    async def on_sprint_deactivated(self) -> None:
        """No-op."""

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
        """Return TASK_DRIVEN."""
        return CeremonyStrategyType.TASK_DRIVEN

    def get_default_velocity_calculator(self) -> VelocityCalcType:
        """Return TASK_DRIVEN velocity calculator."""
        return VelocityCalcType.TASK_DRIVEN

    def validate_strategy_config(
        self,
        config: Mapping[str, Any],
    ) -> None:
        """Validate task-driven strategy config.

        Args:
            config: Strategy config to validate.

        Raises:
            ValueError: If the config contains invalid keys or values.
        """
        unknown = set(config) - _KNOWN_CONFIG_KEYS
        if unknown:
            msg = f"Unknown config keys: {sorted(unknown)}"
            raise ValueError(msg)

        trigger = config.get(_KEY_TRIGGER)
        if trigger is not None and trigger not in _VALID_TRIGGERS:
            msg = (
                f"Invalid trigger {trigger!r}. "
                f"Valid triggers: {sorted(_VALID_TRIGGERS)}"
            )
            raise ValueError(msg)

        every_n = config.get(_KEY_EVERY_N)
        if every_n is not None and (not isinstance(every_n, int) or every_n < 1):
            msg = f"{_KEY_EVERY_N} must be a positive integer, got {every_n!r}"
            raise ValueError(msg)

        pct = config.get(_KEY_SPRINT_PERCENTAGE)
        if pct is not None and (
            not isinstance(pct, int | float) or pct <= 0 or pct > _MAX_SPRINT_PCT
        ):
            msg = (
                f"{_KEY_SPRINT_PERCENTAGE} must be between "
                f"0 (exclusive) and {_MAX_SPRINT_PCT} (inclusive),"
                f" got {pct!r}"
            )
            raise ValueError(msg)

    # -- Internal helpers ----------------------------------------------

    @staticmethod
    def _evaluate_trigger(
        trigger: str,
        config: Mapping[str, Any],
        context: CeremonyEvalContext,
    ) -> bool:
        """Evaluate a single trigger condition.

        Returns:
            ``True`` when the named trigger fires for the current
            context; ``False`` otherwise (sprint_start is excluded
            from per-task evaluation).
        """
        has_tasks = context.total_tasks_in_sprint > 0
        pct = context.sprint_percentage_complete

        if trigger == TRIGGER_SPRINT_START:
            # Handled as one-shot by the scheduler, not per-task.
            return False

        if trigger == TRIGGER_SPRINT_END:
            return has_tasks and pct >= _DEFAULT_TRANSITION_THRESHOLD

        if trigger == TRIGGER_SPRINT_MIDPOINT:
            return has_tasks and pct >= _MIDPOINT_THRESHOLD

        if trigger == TRIGGER_EVERY_N:
            n: int = config.get(_KEY_EVERY_N, _DEFAULT_EVERY_N)
            return context.completions_since_last_trigger >= n

        if trigger == TRIGGER_SPRINT_PERCENTAGE:
            threshold: float = config.get(
                _KEY_SPRINT_PERCENTAGE,
                _DEFAULT_SPRINT_PCT,
            )
            return has_tasks and pct >= (threshold / _MAX_SPRINT_PCT)

        logger.warning(
            SPRINT_CEREMONY_SKIPPED,
            trigger=trigger,
            reason="unrecognized_trigger",
            valid_triggers=sorted(_VALID_TRIGGERS),
        )
        return False
