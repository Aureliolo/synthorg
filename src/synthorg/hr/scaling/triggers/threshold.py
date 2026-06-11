"""Signal threshold trigger.

Fires when a named signal crosses a configured threshold.
Tracks previous values to avoid repeated firing on the same
crossing.
"""

import asyncio

from synthorg.core.types import NotBlankStr
from synthorg.hr.scaling.models import ScalingSignal
from synthorg.observability import get_logger
from synthorg.observability.events.hr import (
    HR_SCALING_TRIGGER_REQUESTED,
    HR_SCALING_TRIGGER_SKIPPED,
)

logger = get_logger(__name__)


class SignalThresholdTrigger:
    """Trigger that fires when a signal crosses a threshold.

    Only fires on the transition (not while the signal remains
    above/below the threshold).

    Args:
        signal_name: Name of the signal to watch.
        threshold: Threshold value.
        above: If True, fires when signal goes above threshold;
            if False, fires when signal goes below.
    """

    def __init__(
        self,
        *,
        signal_name: NotBlankStr,
        threshold: float,
        above: bool = True,
    ) -> None:
        self._signal_name = signal_name
        self._threshold = threshold
        self._above = above
        self._was_crossed = False
        self._previously_over: bool | None = None
        self._lock = asyncio.Lock()

    @property
    def name(self) -> NotBlankStr:
        """Trigger name."""
        return NotBlankStr("signal_threshold")

    async def should_trigger(self) -> bool:
        """Check whether the signal has crossed the threshold.

        Call ``update_signal`` first to provide current values.

        Returns:
            ``True`` when the predicate holds, ``False`` otherwise.
        """
        async with self._lock:
            if self._was_crossed:
                self._was_crossed = False
                logger.info(
                    HR_SCALING_TRIGGER_REQUESTED,
                    trigger="signal_threshold",
                    signal=self._signal_name,
                    threshold=self._threshold,
                )
                return True

            logger.debug(
                HR_SCALING_TRIGGER_SKIPPED,
                trigger="signal_threshold",
                signal=self._signal_name,
                reason="no_crossing",
            )
            return False

    async def record_run(self) -> None:
        """Record that an evaluation cycle completed.

        SignalThresholdTrigger has no in-progress state to reset;
        crossings are consumed by ``should_trigger`` directly.
        """

    async def update_signal(self, signal: ScalingSignal) -> None:
        """Update the tracked signal value and detect crossings.

        Only fires on a transition from below to above (or above to
        below when ``above=False``), not while the signal remains on
        the triggered side.

        Args:
            signal: Current signal value.
        """
        if signal.name != self._signal_name:
            return

        async with self._lock:
            is_over = (
                signal.value > self._threshold
                if self._above
                else signal.value < self._threshold
            )
            if self._previously_over is None:
                # First signal: just initialize the state, don't trigger
                self._previously_over = is_over
            elif is_over and not self._previously_over:
                # Crossing detected: signal transitioned from below to above
                self._was_crossed = True
                self._previously_over = is_over
                logger.info(
                    HR_SCALING_TRIGGER_REQUESTED,
                    trigger="signal_threshold",
                    signal=self._signal_name,
                    threshold=self._threshold,
                    action="crossing_detected",
                )
            else:
                # No crossing: just update state
                self._previously_over = is_over
