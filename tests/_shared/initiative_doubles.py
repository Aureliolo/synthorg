"""Shared test doubles for the initiative tail's ports.

The rollup asks a :class:`ReplanTriggerPort` whether a stalled or tail-failed
plan still has an automatic route; several test tiers (unit rollup, unit
evaluate, integration evaluate) drive it and assert on what it was asked for.
Keeping one recording double here stops the three copies drifting apart when
the port's signature changes.
"""

from synthorg.core.plan import Plan, PlanItem
from synthorg.core.plan_tree import PlanTree
from synthorg.engine.initiative.completion import ReplanDisposition, StallReason
from synthorg.engine.initiative.ports import PlanDriver
from synthorg.engine.initiative.slice_state import SliceDisposition


class RecordingReplanTrigger:
    """A replan trigger that records the stalls it was asked about.

    Args:
        disposition: What every ask answers with. The default is the ordinary
            case; a test exercising a refusal sets ``DISABLED`` or
            ``BUDGET_EXHAUSTED`` and asserts on what the caller did with it.
        slice_disposition: What every slice ask answers with, independent of
            *disposition*: a stall and a wave-completion slice are different
            questions and a test exercising one must not have to care about
            the other's default.
    """

    def __init__(
        self,
        disposition: ReplanDisposition = ReplanDisposition.SCHEDULED,
        *,
        slice_disposition: SliceDisposition = SliceDisposition.GRAFTED,
    ) -> None:
        self.fired: list[tuple[str, StallReason]] = []
        self.details: list[str | None] = []
        self.granted: list[tuple[str, StallReason, str]] = []
        self.drained: list[float] = []
        self.disposition = disposition
        self.slice_disposition = slice_disposition
        self.slices_considered: list[tuple[str, str]] = []
        self.slice_drives: list[PlanDriver | None] = []
        self.slices_granted: list[tuple[str, str, str]] = []

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

    async def consider_slice(
        self,
        *,
        plan: Plan,
        tree: PlanTree,
        workstream: PlanItem,
        leaf: PlanItem,
        drive: PlanDriver | None,
    ) -> SliceDisposition:
        """Record the ask and answer with the configured slice disposition.

        Returns:
            The disposition this double was built with.
        """
        del tree, workstream
        self.slices_considered.append((str(plan.id), leaf.id))
        self.slice_drives.append(drive)
        return self.slice_disposition

    async def grant_slice(
        self,
        *,
        plan: Plan,
        leaf: PlanItem,
        drive: PlanDriver | None,
        requested_by: str,
    ) -> bool:
        """Record a human-authorised slice grant.

        Returns:
            ``True``, the started answer, so a caller asserting on the grant
            reads the path a wired trigger takes.
        """
        del drive
        self.slices_granted.append((str(plan.id), leaf.id, requested_by))
        return True

    async def drain(self, *, timeout_sec: float) -> None:
        self.drained.append(timeout_sec)
