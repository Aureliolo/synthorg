"""Throughput-adaptive ceremony scheduling strategy.

Ceremonies fire when the team's rolling throughput rate changes
significantly -- not "every N tasks" but "when pace anomaly detected."
Like an automated burndown chart monitor.

**Config keys** (sprint-level ``ceremony_policy.strategy_config``):

- ``velocity_drop_threshold_pct`` (int/float): fire ceremony when
  velocity drops this percentage from baseline (default: 30).
- ``velocity_spike_threshold_pct`` (int/float): fire ceremony when
  velocity spikes this percentage above baseline (default: 50).
- ``measurement_window_tasks`` (int): rolling window size for rate
  calculation (default: 10).

**Config keys** (per-ceremony ``policy_override.strategy_config``):

- ``on_drop`` (bool): fire this ceremony on velocity drop
  (default: True).
- ``on_spike`` (bool): fire this ceremony on velocity spike
  (default: False).
"""

from collections import deque
from typing import TYPE_CHECKING, Any

from synthorg.core.clock import Clock, SystemClock
from synthorg.engine.workflow.ceremony_policy import (
    CeremonyStrategyType,
)
from synthorg.engine.workflow.sprint_lifecycle import Sprint, SprintStatus
from synthorg.engine.workflow.strategies._helpers import get_ceremony_config
from synthorg.engine.workflow.strategies._throughput_adaptive_config import (
    _DEFAULT_DROP_THRESHOLD_PCT,
    _DEFAULT_SPIKE_THRESHOLD_PCT,
    _DEFAULT_TRANSITION_THRESHOLD,
    _DEFAULT_WINDOW_SIZE,
    _KEY_VELOCITY_DROP_THRESHOLD_PCT,
    _KEY_VELOCITY_SPIKE_THRESHOLD_PCT,
    _KNOWN_CONFIG_KEYS,
    _MIN_WINDOW_SIZE,
    resolve_bool,
    resolve_threshold,
    resolve_window_size,
    validate_threshold_key,
    validate_window_key,
)
from synthorg.engine.workflow.velocity_types import VelocityCalcType
from synthorg.observability import get_logger
from synthorg.observability.events.workflow import (
    SPRINT_AUTO_TRANSITION,
    SPRINT_CEREMONY_THROUGHPUT_BASELINE_SET,
    SPRINT_CEREMONY_THROUGHPUT_COLD_START,
    SPRINT_CEREMONY_THROUGHPUT_DROP_DETECTED,
    SPRINT_CEREMONY_THROUGHPUT_SPIKE_DETECTED,
    SPRINT_CEREMONY_TRIGGERED,
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

# Per-ceremony keys (read via get_ceremony_config, not sprint-level config).
_KEY_ON_DROP: str = "on_drop"
_KEY_ON_SPIKE: str = "on_spike"
_KNOWN_CEREMONY_KEYS: frozenset[str] = frozenset({_KEY_ON_DROP, _KEY_ON_SPIKE})


class ThroughputAdaptiveStrategy:
    """Ceremony scheduling strategy based on throughput rate anomalies.

    Tracks task completion timestamps in a rolling window and
    establishes a baseline rate from the first full window.  The
    baseline is frozen after the first full window and remains
    fixed for the sprint's duration -- all anomaly thresholds are
    relative to this initial rate.

    Anomaly detection is **edge-triggered**: a ceremony fires only
    on the transition into an anomaly state (drop or spike), not
    on every evaluation while the anomaly persists.

    This is a **stateful strategy** -- lifecycle hooks maintain internal
    rate-tracking state that is reset per sprint.
    """

    __slots__ = (
        "_baseline_rate",
        "_blocked_count",
        "_clock",
        "_completion_timestamps",
        "_drop_threshold_pct",
        "_last_anomaly_state",
        "_spike_threshold_pct",
        "_window_size",
    )

    def __init__(self, *, clock: Clock | None = None) -> None:
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._completion_timestamps: deque[float] = deque(
            maxlen=_DEFAULT_WINDOW_SIZE,
        )
        self._baseline_rate: float | None = None
        self._blocked_count: int = 0
        self._drop_threshold_pct: float = _DEFAULT_DROP_THRESHOLD_PCT
        self._last_anomaly_state: dict[str, str | None] = {}
        self._spike_threshold_pct: float = _DEFAULT_SPIKE_THRESHOLD_PCT
        self._window_size: int = _DEFAULT_WINDOW_SIZE

    # -- Core evaluation -------------------------------------------------------

    def should_fire_ceremony(
        self,
        ceremony: SprintCeremonyConfig,
        sprint: Sprint,  # noqa: ARG002
        context: CeremonyEvalContext,  # noqa: ARG002
    ) -> bool:
        """Return True when throughput anomaly detected for this ceremony.

        Args:
            ceremony: The ceremony configuration being evaluated.
            sprint: Current sprint state.
            context: Evaluation context.

        Returns:
            ``True`` if the ceremony should fire.
        """
        config = get_ceremony_config(ceremony)
        on_drop = resolve_bool(config, _KEY_ON_DROP, default=True)
        on_spike = resolve_bool(config, _KEY_ON_SPIKE, default=False)

        if not on_drop and not on_spike:
            return False

        if self._baseline_rate is None:
            logger.debug(
                SPRINT_CEREMONY_THROUGHPUT_COLD_START,
                ceremony=ceremony.name,
                window_size=self._window_size,
                completions=len(self._completion_timestamps),
                strategy="throughput_adaptive",
            )
            return False

        current_rate = self._compute_current_rate()
        if current_rate is None:
            return False

        state = self._determine_anomaly_state(
            current_rate,
            on_drop=on_drop,
            on_spike=on_spike,
        )
        if self._update_and_check_edge_trigger(ceremony.name, state):
            self._log_anomaly_trigger(
                ceremony.name,
                current_rate,
                state,  # type: ignore[arg-type]  # guaranteed non-None
            )
            return True

        return False

    def _determine_anomaly_state(
        self,
        current_rate: float,
        *,
        on_drop: bool,
        on_spike: bool,
    ) -> str | None:
        """Classify the current rate as drop, spike, or normal.

        Returns:
            ``"drop"`` or ``"spike"`` when the corresponding anomaly
            is detected and enabled; ``None`` when no anomaly fires.
        """
        if on_drop and self._is_velocity_drop(current_rate):
            return "drop"
        if on_spike and self._is_velocity_spike(current_rate):
            return "spike"
        return None

    def _update_and_check_edge_trigger(
        self,
        ceremony_name: str,
        new_state: str | None,
    ) -> bool:
        """Return True only on transition into a non-None anomaly state."""
        last_state = self._last_anomaly_state.get(ceremony_name)
        self._last_anomaly_state[ceremony_name] = new_state
        return new_state is not None and new_state != last_state

    def _is_velocity_drop(self, current_rate: float) -> bool:
        """Return True if velocity has dropped beyond the threshold."""
        if self._baseline_rate is None or self._baseline_rate <= 0:
            return False
        drop_pct = ((self._baseline_rate - current_rate) / self._baseline_rate) * 100.0
        return drop_pct >= self._drop_threshold_pct

    def _is_velocity_spike(self, current_rate: float) -> bool:
        """Return True if velocity has spiked beyond the threshold."""
        if self._baseline_rate is None or self._baseline_rate <= 0:
            return False
        spike_pct = ((current_rate - self._baseline_rate) / self._baseline_rate) * 100.0
        return spike_pct >= self._spike_threshold_pct

    def _log_anomaly_trigger(
        self,
        ceremony_name: str,
        current_rate: float,
        anomaly_type: str,
    ) -> None:
        """Log a velocity anomaly detection and ceremony trigger."""
        if self._baseline_rate is None or self._baseline_rate <= 0:
            return  # pragma: no cover -- defensive
        if anomaly_type == "drop":
            drop_pct = (
                (self._baseline_rate - current_rate) / self._baseline_rate
            ) * 100.0
            logger.info(
                SPRINT_CEREMONY_THROUGHPUT_DROP_DETECTED,
                ceremony=ceremony_name,
                baseline_rate=self._baseline_rate,
                current_rate=current_rate,
                drop_pct=round(drop_pct, 1),
                threshold_pct=self._drop_threshold_pct,
                strategy="throughput_adaptive",
            )
        else:
            spike_pct = (
                (current_rate - self._baseline_rate) / self._baseline_rate
            ) * 100.0
            logger.info(
                SPRINT_CEREMONY_THROUGHPUT_SPIKE_DETECTED,
                ceremony=ceremony_name,
                baseline_rate=self._baseline_rate,
                current_rate=current_rate,
                spike_pct=round(spike_pct, 1),
                threshold_pct=self._spike_threshold_pct,
                strategy="throughput_adaptive",
            )
        logger.info(
            SPRINT_CEREMONY_TRIGGERED,
            ceremony=ceremony_name,
            reason=f"velocity_{anomaly_type}",
            strategy="throughput_adaptive",
        )

    def should_transition_sprint(
        self,
        sprint: Sprint,
        config: SprintConfig,
        context: CeremonyEvalContext,
    ) -> SprintStatus | None:
        """Return IN_REVIEW when task completion threshold is met.

        Uses the same completion-threshold logic as task-driven -- no
        anomaly-based transition.  Only transitions from ACTIVE status.

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
            logger.info(
                SPRINT_AUTO_TRANSITION,
                sprint_percentage_complete=context.sprint_percentage_complete,
                threshold=threshold,
                strategy="throughput_adaptive",
            )
            return SprintStatus.IN_REVIEW
        return None

    # -- Lifecycle hooks -------------------------------------------------------

    async def on_sprint_activated(
        self,
        sprint: Sprint,  # noqa: ARG002
        config: SprintConfig,
    ) -> None:
        """Initialize rate tracking from sprint config.

        Args:
            sprint: The activated sprint.
            config: Sprint configuration.
        """
        strategy_config = (
            config.ceremony_policy.strategy_config
            if config.ceremony_policy.strategy_config is not None
            else {}
        )

        self._drop_threshold_pct = resolve_threshold(
            strategy_config,
            _KEY_VELOCITY_DROP_THRESHOLD_PCT,
            _DEFAULT_DROP_THRESHOLD_PCT,
        )
        self._spike_threshold_pct = resolve_threshold(
            strategy_config,
            _KEY_VELOCITY_SPIKE_THRESHOLD_PCT,
            _DEFAULT_SPIKE_THRESHOLD_PCT,
        )
        self._window_size = resolve_window_size(strategy_config)
        self._completion_timestamps = deque(maxlen=self._window_size)
        self._baseline_rate = None
        self._blocked_count = 0
        self._last_anomaly_state = {}

    async def on_sprint_deactivated(self) -> None:
        """Clear all internal state."""
        self._completion_timestamps = deque(maxlen=_DEFAULT_WINDOW_SIZE)
        self._baseline_rate = None
        self._blocked_count = 0
        self._last_anomaly_state = {}
        self._drop_threshold_pct = _DEFAULT_DROP_THRESHOLD_PCT
        self._spike_threshold_pct = _DEFAULT_SPIKE_THRESHOLD_PCT
        self._window_size = _DEFAULT_WINDOW_SIZE

    async def on_task_completed(
        self,
        sprint: Sprint,  # noqa: ARG002
        task_id: str,  # noqa: ARG002
        story_points: float,  # noqa: ARG002
        context: CeremonyEvalContext,  # noqa: ARG002
    ) -> None:
        """Record completion timestamp and establish baseline if ready.

        Args:
            sprint: Current sprint state.
            task_id: The completed task ID.
            story_points: Points earned for the task.
            context: Evaluation context.
        """
        self._completion_timestamps.append(self._clock.monotonic())

        if (
            self._baseline_rate is None
            and len(self._completion_timestamps) == self._window_size
        ):
            self._try_establish_baseline()

    async def on_task_added(
        self,
        sprint: Sprint,
        task_id: str,
    ) -> None:
        """No-op."""

    async def on_task_blocked(
        self,
        sprint: Sprint,  # noqa: ARG002
        task_id: str,  # noqa: ARG002
    ) -> None:
        """Increment blocked task counter (informational).

        Blocked tasks indirectly reduce throughput (fewer
        completions), so the rate calculation already captures
        the effect.  This counter is retained for observability
        and potential future use in anomaly weighting.

        Args:
            sprint: Current sprint state.
            task_id: The blocked task ID.
        """
        self._blocked_count += 1

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

    # -- Metadata --------------------------------------------------------------

    @property
    def strategy_type(self) -> CeremonyStrategyType:
        """Return THROUGHPUT_ADAPTIVE."""
        return CeremonyStrategyType.THROUGHPUT_ADAPTIVE

    def get_default_velocity_calculator(self) -> VelocityCalcType:
        """Return TASK_DRIVEN."""
        return VelocityCalcType.TASK_DRIVEN

    def validate_strategy_config(
        self,
        config: Mapping[str, Any],
    ) -> None:
        """Validate throughput-adaptive strategy config.

        Args:
            config: Strategy config to validate.

        Raises:
            ValueError: If the config contains invalid keys, values,
                or wrongly-typed entries.
        """
        unknown = set(config) - _KNOWN_CONFIG_KEYS
        if unknown:
            msg = f"Unknown config keys: {sorted(unknown)}"
            logger.warning(
                SPRINT_STRATEGY_CONFIG_INVALID,
                strategy="throughput_adaptive",
                unknown_keys=sorted(unknown),
            )
            raise ValueError(msg)

        try:
            validate_threshold_key(config, _KEY_VELOCITY_DROP_THRESHOLD_PCT)
            validate_threshold_key(config, _KEY_VELOCITY_SPIKE_THRESHOLD_PCT)
            validate_window_key(config)
        except TypeError as exc:
            raise ValueError(str(exc)) from exc

    # -- Private helpers -------------------------------------------------------

    def _compute_current_rate(self) -> float | None:
        """Compute current throughput rate from the rolling window.

        Returns:
            Tasks per second, or ``None`` if rate cannot be computed.
        """
        if len(self._completion_timestamps) < _MIN_WINDOW_SIZE:
            return None
        time_span = self._completion_timestamps[-1] - self._completion_timestamps[0]
        if time_span <= 0:
            return None
        return len(self._completion_timestamps) / time_span

    def _try_establish_baseline(self) -> None:
        """Compute and freeze the baseline rate from the first full window."""
        time_span = self._completion_timestamps[-1] - self._completion_timestamps[0]
        if time_span <= 0:
            return
        self._baseline_rate = self._window_size / time_span
        logger.info(
            SPRINT_CEREMONY_THROUGHPUT_BASELINE_SET,
            baseline_rate=self._baseline_rate,
            window_size=self._window_size,
            time_span_seconds=round(time_span, 2),
            strategy="throughput_adaptive",
        )
