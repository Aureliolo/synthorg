"""Composite scaling trigger -- combines multiple triggers with OR."""

from synthorg.core.types import NotBlankStr
from synthorg.hr.scaling.models import ScalingSignal
from synthorg.hr.scaling.protocols import ScalingTrigger, SignalAwareTrigger
from synthorg.observability import get_logger

logger = get_logger(__name__)


class CompositeScalingTrigger:
    """Combines multiple triggers with OR semantics.

    Fires if any child trigger fires.

    Args:
        triggers: Child triggers to combine.
    """

    def __init__(
        self,
        *,
        triggers: tuple[ScalingTrigger, ...],
    ) -> None:
        self._triggers = triggers

    @property
    def name(self) -> NotBlankStr:
        """Trigger name."""
        return NotBlankStr("composite")

    async def should_trigger(self) -> bool:
        """Trigger if any child trigger fires.

        OR semantics: the first firing child short-circuits, so a
        ``should_trigger`` that also consumes state (a signal-threshold
        crossing) on a later child is intentionally left unpolled this
        cycle and surfaces on the next poll. That defers a simultaneous
        second crossing by one cycle rather than dropping it.

        Returns:
            ``True`` when the predicate holds, ``False`` otherwise.
        """
        for trigger in self._triggers:
            if await trigger.should_trigger():
                return True
        return False

    async def record_run(self) -> None:
        """Forward record_run to all child triggers."""
        for trigger in self._triggers:
            await trigger.record_run()

    async def update_signal(self, signal: ScalingSignal) -> None:
        """Forward a pushed signal to every signal-aware child trigger.

        Children that do not consume signals (e.g. the time-interval
        ``batched`` trigger) are skipped, so a composite combining a
        batched trigger with a signal-threshold trigger primes only the
        latter.

        Args:
            signal: Current signal value to track for crossings.
        """
        for trigger in self._triggers:
            if isinstance(trigger, SignalAwareTrigger):
                await trigger.update_signal(signal)
