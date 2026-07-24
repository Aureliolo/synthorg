"""Shared test doubles for the initiative tail's ports.

The rollup fires a :class:`ReplanTriggerPort` on the edge into a stalled or
tail-failed plan; several test tiers (unit rollup, unit evaluate, integration
evaluate) drive it and assert on what it was fired for. Keeping one recording
double here stops the three copies drifting apart when the port's signature
changes.
"""

from synthorg.core.plan import Plan
from synthorg.engine.initiative.completion import StallReason


class RecordingReplanTrigger:
    """A replan trigger that records the stalls it was fired for."""

    def __init__(self) -> None:
        self.fired: list[tuple[str, StallReason]] = []
        self.details: list[str | None] = []
        self.drained: list[float] = []

    def schedule(
        self,
        *,
        plan: Plan,
        reason: StallReason,
        detail: str | None = None,
    ) -> None:
        self.fired.append((str(plan.id), reason))
        self.details.append(detail)

    async def drain(self, *, timeout_sec: float) -> None:
        self.drained.append(timeout_sec)
