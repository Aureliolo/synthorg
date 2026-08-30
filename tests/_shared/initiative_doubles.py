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
from synthorg.engine.initiative.extension_state import ExtensionDisposition
from synthorg.engine.initiative.ports import PlanDriver


class RecordingReplanTrigger:
    """A replan trigger that records the stalls it was asked about.

    Args:
        disposition: What every ask answers with. The default is the ordinary
            case; a test exercising a refusal sets ``DISABLED`` or
            ``BUDGET_EXHAUSTED`` and asserts on what the caller did with it.
        extension_disposition: What every extension ask answers with,
            independent of *disposition*: a stall and a wave-completion
            extension are different questions and a test exercising one must
            not have to care about the other's default.
    """

    def __init__(
        self,
        disposition: ReplanDisposition = ReplanDisposition.SCHEDULED,
        *,
        extension_disposition: ExtensionDisposition = ExtensionDisposition.GRAFTED,
    ) -> None:
        self.fired: list[tuple[str, StallReason]] = []
        self.details: list[str | None] = []
        self.granted: list[tuple[str, StallReason, str]] = []
        self.drained: list[float] = []
        self.disposition = disposition
        self.extension_disposition = extension_disposition
        self.extensions_considered: list[tuple[str, str]] = []
        self.extension_drives: list[PlanDriver | None] = []
        self.extensions_granted: list[tuple[str, str, str]] = []
        self.extension_grant_drives: list[PlanDriver | None] = []

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

    async def consider_extension(
        self,
        *,
        plan: Plan,
        tree: PlanTree,
        workstream: PlanItem,
        leaf: PlanItem,
        drive: PlanDriver | None,
    ) -> ExtensionDisposition:
        """Record the ask and answer with the configured extension disposition.

        Returns:
            The disposition this double was built with.
        """
        del tree, workstream
        self.extensions_considered.append((str(plan.id), leaf.id))
        self.extension_drives.append(drive)
        return self.extension_disposition

    async def grant_extension(
        self,
        *,
        plan: Plan,
        workstream: PlanItem,
        leaf: PlanItem,
        drive: PlanDriver | None,
        requested_by: str,
    ) -> bool:
        """Record a human-authorised extension grant.

        Returns:
            ``True``, the started answer, so a caller asserting on the grant
            reads the path a wired trigger takes.
        """
        del workstream
        self.extensions_granted.append((str(plan.id), leaf.id, requested_by))
        self.extension_grant_drives.append(drive)
        return True

    async def drain(self, *, timeout_sec: float) -> None:
        self.drained.append(timeout_sec)
