"""Shared test doubles for the initiative tail's ports.

The rollup asks a :class:`ReplanTriggerPort` whether a stalled or tail-failed
plan still has an automatic route; several test tiers (unit rollup, unit
evaluate, integration evaluate) drive it and assert on what it was asked for.
Keeping one recording double here stops the three copies drifting apart when
the port's signature changes.
"""

from synthorg.core.plan import Plan
from synthorg.engine.initiative.completion import ReplanDisposition, StallReason


class RecordingReplanTrigger:
    """A replan trigger that records the stalls it was asked about.

    Args:
        disposition: What every ask answers with. The default is the ordinary
            case; a test exercising a refusal sets ``DISABLED`` or
            ``BUDGET_EXHAUSTED`` and asserts on what the caller did with it.
    """

    def __init__(
        self, disposition: ReplanDisposition = ReplanDisposition.SCHEDULED
    ) -> None:
        self.fired: list[tuple[str, StallReason]] = []
        self.details: list[str | None] = []
        self.granted: list[tuple[str, StallReason, str]] = []
        self.drained: list[float] = []
        self.disposition = disposition

    async def consider(
        self,
        *,
        plan: Plan,
        reason: StallReason,
        detail: str | None = None,
    ) -> ReplanDisposition:
        """Record the ask and answer with the configured disposition.

        Returns:
            The disposition this double was built with.
        """
        self.fired.append((str(plan.id), reason))
        self.details.append(detail)
        return self.disposition

    async def grant(
        self,
        *,
        plan: Plan,
        reason: StallReason,
        requested_by: str,
        detail: str | None = None,
    ) -> bool:
        """Record a human-authorised replan.

        Returns:
            ``True``, the started answer, so a caller asserting on the grant
            reads the path a wired trigger takes.
        """
        self.granted.append((str(plan.id), reason, requested_by))
        self.details.append(detail)
        return True

    async def drain(self, *, timeout_sec: float) -> None:
        self.drained.append(timeout_sec)
