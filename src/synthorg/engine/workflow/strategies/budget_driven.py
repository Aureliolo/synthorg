"""Budget-driven ceremony scheduling strategy.

Ceremonies fire at cost-consumption thresholds.  Ties directly into
the budget module (cost tracking, quota degradation).

**Config keys** (per-ceremony ``policy_override.strategy_config``):

- ``budget_thresholds`` (list[float]): percentages at which to fire
  the ceremony (e.g. ``[25, 50, 75]``).

**Config keys** (sprint-level ``ceremony_policy.strategy_config``):

- ``transition_threshold`` (float): budget percentage that triggers
  sprint auto-transition (default: 100.0).
"""

import math
from typing import TYPE_CHECKING, Any

from synthorg.engine.workflow.ceremony_policy import (
    CeremonyStrategyType,
)
from synthorg.engine.workflow.sprint_lifecycle import Sprint, SprintStatus
from synthorg.engine.workflow.strategies._helpers import get_ceremony_config
from synthorg.engine.workflow.velocity_types import VelocityCalcType
from synthorg.observability import get_logger
from synthorg.observability.events.workflow import (
    SPRINT_AUTO_TRANSITION_BUDGET,
    SPRINT_CEREMONY_BUDGET_THRESHOLD_ALREADY_FIRED,
    SPRINT_CEREMONY_BUDGET_THRESHOLD_CROSSED,
    SPRINT_CEREMONY_SKIPPED,
    SPRINT_STRATEGY_CONFIG_INVALID,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from synthorg.engine.workflow.ceremony_context import CeremonyEvalContext
    from synthorg.engine.workflow.sprint_config import (
        SprintCeremonyConfig,
        SprintConfig,
    )

logger = get_logger(__name__)

# -- Config keys ---------------------------------------------------------------

_KEY_BUDGET_THRESHOLDS: str = "budget_thresholds"
_KEY_TRANSITION_THRESHOLD: str = "transition_threshold"

_KNOWN_CONFIG_KEYS: frozenset[str] = frozenset(
    {
        _KEY_BUDGET_THRESHOLDS,
        _KEY_TRANSITION_THRESHOLD,
    }
)

_DEFAULT_TRANSITION_THRESHOLD_PCT: float = 100.0
_MAX_THRESHOLD_PCT: float = 100.0
_MAX_THRESHOLD_COUNT: int = 20


def _check_threshold_element(t: object) -> None:
    """Validate a single threshold element (raises on invalid).

    Raises:
        ValueError: When ``t`` is not a finite number in ``(0, 100]``.
    """
    # bool is a subclass of int; check first
    if (
        isinstance(t, bool)
        or not isinstance(t, int | float)
        or not math.isfinite(t)
        or not (0 < t <= _MAX_THRESHOLD_PCT)
    ):
        msg = f"Each budget threshold must be a finite number in (0, 100], got {t!r}"
        logger.warning(
            SPRINT_STRATEGY_CONFIG_INVALID,
            strategy="budget_driven",
            key=_KEY_BUDGET_THRESHOLDS,
            value=t,
        )
        raise ValueError(msg)


def _coerce_threshold(t: object, ceremony_name: str) -> float | None:
    """Coerce a single threshold element, returning None on failure.

    Returns:
        The threshold as ``float`` when ``t`` is a finite numeric in
        ``(0, 100]``; ``None`` otherwise (a warning is logged).
    """
    if isinstance(t, bool) or not isinstance(t, int | float):
        logger.warning(
            SPRINT_CEREMONY_SKIPPED,
            ceremony=ceremony_name,
            reason="invalid_threshold_element",
            value=t,
            strategy="budget_driven",
        )
        return None
    if not math.isfinite(t) or not (0 < t <= _MAX_THRESHOLD_PCT):
        logger.warning(
            SPRINT_CEREMONY_SKIPPED,
            ceremony=ceremony_name,
            reason="threshold_out_of_range",
            value=t,
            strategy="budget_driven",
        )
        return None
    return float(t)


class BudgetDrivenStrategy:
    """Ceremony scheduling strategy driven by budget consumption.

    Ceremonies fire when ``context.budget_consumed_fraction`` crosses
    configured percentage thresholds.  Each threshold fires at most
    once per sprint.  When multiple thresholds are crossed
    simultaneously, only the lowest unfired threshold fires per
    evaluation call (the scheduler calls repeatedly, so thresholds
    catch up quickly without cascading ceremonies in a single cycle).

    State is tracked per-sprint and cleared on sprint transitions.

    Note:
        Unlike the protocol's description of stateless core methods,
        ``should_fire_ceremony`` mutates internal tracking state on
        each call (marking thresholds as fired).
    """

    __slots__ = ("_fired_thresholds",)

    def __init__(self) -> None:
        self._fired_thresholds: dict[str, set[float]] = {}

    # -- Core evaluation -------------------------------------------------------

    def should_fire_ceremony(
        self,
        ceremony: SprintCeremonyConfig,
        sprint: Sprint,  # noqa: ARG002
        context: CeremonyEvalContext,
    ) -> bool:
        """Check if a budget threshold has been crossed.

        Fires the lowest unfired threshold that the current budget
        fraction has reached.  Returns ``True`` for at most one
        threshold per call.

        Args:
            ceremony: The ceremony being evaluated.
            sprint: Current sprint state.
            context: Evaluation context with ``budget_consumed_fraction``.

        Returns:
            ``True`` if a threshold was newly crossed.

        Note:
            This method mutates internal state -- it marks the
            fired threshold as consumed.  It is not a pure predicate.
        """
        config = get_ceremony_config(ceremony)
        valid = self._resolve_thresholds(
            config.get(_KEY_BUDGET_THRESHOLDS),
            ceremony.name,
        )
        if not valid:
            return False

        budget_pct = context.budget_consumed_fraction * _MAX_THRESHOLD_PCT
        return self._find_crossed_threshold(
            ceremony.name,
            valid,
            budget_pct,
        )

    def should_transition_sprint(
        self,
        sprint: Sprint,
        config: SprintConfig,
        context: CeremonyEvalContext,
    ) -> SprintStatus | None:
        """Return IN_REVIEW when budget consumption reaches threshold.

        Only transitions from ACTIVE status.

        Args:
            sprint: Current sprint state.
            config: Sprint configuration.
            context: Evaluation context.

        Returns:
            ``SprintStatus.IN_REVIEW`` if budget threshold met,
            else ``None``.
        """
        if sprint.status is not SprintStatus.ACTIVE:
            return None

        transition_pct = self._get_transition_threshold(config)
        budget_pct = context.budget_consumed_fraction * _MAX_THRESHOLD_PCT

        if budget_pct >= transition_pct:
            logger.info(
                SPRINT_AUTO_TRANSITION_BUDGET,
                budget_pct=budget_pct,
                transition_threshold=transition_pct,
                strategy="budget_driven",
            )
            return SprintStatus.IN_REVIEW

        return None

    # -- Lifecycle hooks -------------------------------------------------------

    async def on_sprint_activated(
        self,
        sprint: Sprint,  # noqa: ARG002
        config: SprintConfig,  # noqa: ARG002
    ) -> None:
        """Reset all fired-threshold tracking for the new sprint cycle.

        Clears tracking across all ceremonies, not only the activated
        sprint's ceremonies.

        Args:
            sprint: The activated sprint.
            config: Sprint configuration.
        """
        self._fired_thresholds.clear()

    async def on_sprint_deactivated(self) -> None:
        """Clear all internal state."""
        self._fired_thresholds.clear()

    async def on_task_completed(
        self,
        sprint: Sprint,
        task_id: str,
        story_points: float,
        context: CeremonyEvalContext,
    ) -> None:
        """No-op -- budget strategy does not track task events.

        Args:
            sprint: Current sprint state.
            task_id: The completed task ID.
            story_points: Points earned for the task.
            context: Evaluation context.
        """

    async def on_task_added(
        self,
        sprint: Sprint,
        task_id: str,
    ) -> None:
        """No-op.

        Args:
            sprint: Current sprint state.
            task_id: The added task ID.
        """

    async def on_task_blocked(
        self,
        sprint: Sprint,
        task_id: str,
    ) -> None:
        """No-op.

        Args:
            sprint: Current sprint state.
            task_id: The blocked task ID.
        """

    async def on_budget_updated(
        self,
        sprint: Sprint,
        budget_consumed_fraction: float,
    ) -> None:
        """No-op -- budget fraction is read from context in evaluation.

        Args:
            sprint: Current sprint state.
            budget_consumed_fraction: Budget consumed fraction.
        """

    async def on_external_event(
        self,
        sprint: Sprint,
        event_name: str,
        payload: Mapping[str, Any],
    ) -> None:
        """No-op.

        Args:
            sprint: Current sprint state.
            event_name: Name of the external event.
            payload: Event payload data.
        """

    # -- Metadata --------------------------------------------------------------

    @property
    def strategy_type(self) -> CeremonyStrategyType:
        """Return BUDGET_DRIVEN."""
        return CeremonyStrategyType.BUDGET_DRIVEN

    def get_default_velocity_calculator(self) -> VelocityCalcType:
        """Return BUDGET."""
        return VelocityCalcType.BUDGET

    def validate_strategy_config(
        self,
        config: Mapping[str, Any],
    ) -> None:
        """Validate budget-driven strategy config.

        Args:
            config: Strategy config to validate.

        Raises:
            ValueError: If the config contains invalid keys or values.
        """
        unknown = set(config) - _KNOWN_CONFIG_KEYS
        if unknown:
            msg = f"Unknown config keys: {sorted(unknown)}"
            logger.warning(
                SPRINT_STRATEGY_CONFIG_INVALID,
                strategy="budget_driven",
                unknown_keys=sorted(unknown),
            )
            raise ValueError(msg)

        thresholds = config.get(_KEY_BUDGET_THRESHOLDS)
        if thresholds is not None:
            try:
                self._validate_thresholds(thresholds)
            except TypeError as exc:
                raise ValueError(str(exc)) from exc

        transition = config.get(_KEY_TRANSITION_THRESHOLD)
        if transition is not None and (
            isinstance(transition, bool)
            or not isinstance(transition, int | float)
            or not math.isfinite(transition)
            or not (0 < transition <= _MAX_THRESHOLD_PCT)
        ):
            msg = (
                f"'{_KEY_TRANSITION_THRESHOLD}' must be a number "
                f"in (0, 100], got {transition!r}"
            )
            logger.warning(
                SPRINT_STRATEGY_CONFIG_INVALID,
                strategy="budget_driven",
                key=_KEY_TRANSITION_THRESHOLD,
                value=transition,
            )
            raise ValueError(msg)

    # -- Private helpers -------------------------------------------------------

    @staticmethod
    def _validate_thresholds(thresholds: object) -> None:
        """Validate budget_thresholds list values (strict).

        Raises:
            TypeError: When ``thresholds`` is not a list.
            ValueError: When the list exceeds the entry cap, contains
                an invalid element, or duplicates a value.
        """
        if not isinstance(thresholds, list):
            msg = (
                f"'{_KEY_BUDGET_THRESHOLDS}' must be a list, "
                f"got {type(thresholds).__name__}"
            )
            logger.warning(
                SPRINT_STRATEGY_CONFIG_INVALID,
                strategy="budget_driven",
                key=_KEY_BUDGET_THRESHOLDS,
                got_type=type(thresholds).__name__,
            )
            raise TypeError(msg)
        if len(thresholds) > _MAX_THRESHOLD_COUNT:
            msg = (
                f"'{_KEY_BUDGET_THRESHOLDS}' must not exceed "
                f"{_MAX_THRESHOLD_COUNT} entries, "
                f"got {len(thresholds)}"
            )
            logger.warning(
                SPRINT_STRATEGY_CONFIG_INVALID,
                strategy="budget_driven",
                key=_KEY_BUDGET_THRESHOLDS,
                count=len(thresholds),
                limit=_MAX_THRESHOLD_COUNT,
            )
            raise ValueError(msg)
        seen: set[float] = set()
        for t in thresholds:
            _check_threshold_element(t)
            if t in seen:
                msg = f"Duplicate budget threshold: {t}"
                logger.warning(
                    SPRINT_STRATEGY_CONFIG_INVALID,
                    strategy="budget_driven",
                    key=_KEY_BUDGET_THRESHOLDS,
                    duplicate=t,
                )
                raise ValueError(msg)
            seen.add(t)

    @staticmethod
    def _resolve_thresholds(
        raw: object,
        ceremony_name: str,
    ) -> list[float] | None:
        """Validate and filter budget thresholds at read time.

        Returns:
            A deduplicated sorted list of valid numeric thresholds,
            or ``None`` when ``raw`` is missing, not a list,
            over-sized, or every entry is invalid.
        """
        if not raw:
            logger.debug(
                SPRINT_CEREMONY_SKIPPED,
                ceremony=ceremony_name,
                reason="no_budget_thresholds",
                strategy="budget_driven",
            )
            return None
        if not isinstance(raw, list):
            logger.warning(
                SPRINT_CEREMONY_SKIPPED,
                ceremony=ceremony_name,
                reason="invalid_budget_thresholds",
                value=raw,
                strategy="budget_driven",
            )
            return None
        if len(raw) > _MAX_THRESHOLD_COUNT:
            logger.warning(
                SPRINT_CEREMONY_SKIPPED,
                ceremony=ceremony_name,
                reason="threshold_count_exceeded",
                value=len(raw),
                strategy="budget_driven",
            )
            return None
        valid: list[float] = []
        seen: set[float] = set()
        for t in raw:
            coerced = _coerce_threshold(t, ceremony_name)
            if coerced is None or coerced in seen:
                continue
            seen.add(coerced)
            valid.append(coerced)
        return valid or None

    @staticmethod
    def _get_transition_threshold(config: SprintConfig) -> float:
        """Resolve transition threshold with type + range validation.

        Returns:
            The configured threshold as ``float`` when valid;
            :data:`_DEFAULT_TRANSITION_THRESHOLD_PCT` otherwise.
        """
        strategy_config = config.ceremony_policy.strategy_config or {}
        raw = strategy_config.get(
            _KEY_TRANSITION_THRESHOLD,
            _DEFAULT_TRANSITION_THRESHOLD_PCT,
        )
        if (
            isinstance(raw, bool)
            or not isinstance(raw, int | float)
            or not math.isfinite(raw)
            or not (0 < raw <= _MAX_THRESHOLD_PCT)
        ):
            logger.warning(
                SPRINT_CEREMONY_SKIPPED,
                reason="invalid_transition_threshold",
                value=raw,
                fallback=_DEFAULT_TRANSITION_THRESHOLD_PCT,
                strategy="budget_driven",
            )
            return _DEFAULT_TRANSITION_THRESHOLD_PCT
        return float(raw)

    def _find_crossed_threshold(
        self,
        ceremony_name: str,
        thresholds: list[float],
        budget_pct: float,
    ) -> bool:
        """Find and fire the lowest unfired threshold crossed.

        Returns:
            ``True`` when ``budget_pct`` crossed an unfired threshold
            (which is now marked fired); ``False`` otherwise.
        """
        fired = self._fired_thresholds.setdefault(ceremony_name, set())
        for threshold in sorted(thresholds):
            if threshold in fired:
                logger.debug(
                    SPRINT_CEREMONY_BUDGET_THRESHOLD_ALREADY_FIRED,
                    ceremony=ceremony_name,
                    threshold=threshold,
                    strategy="budget_driven",
                )
                continue
            if budget_pct >= threshold:
                fired.add(threshold)
                logger.info(
                    SPRINT_CEREMONY_BUDGET_THRESHOLD_CROSSED,
                    ceremony=ceremony_name,
                    threshold=threshold,
                    budget_pct=budget_pct,
                    strategy="budget_driven",
                )
                return True
        logger.debug(
            SPRINT_CEREMONY_SKIPPED,
            ceremony=ceremony_name,
            budget_pct=budget_pct,
            strategy="budget_driven",
        )
        return False
