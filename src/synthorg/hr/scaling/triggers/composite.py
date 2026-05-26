"""Composite scaling trigger -- combines multiple triggers with OR."""

from typing import TYPE_CHECKING

from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger

if TYPE_CHECKING:
    from synthorg.hr.scaling.protocols import ScalingTrigger


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
