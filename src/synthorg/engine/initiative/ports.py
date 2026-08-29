# module-kind: code
"""Ports the initiative rollup depends on.

The rollup lives in ``engine`` but the audited plan-status write path lives in
``api.services``, which ``engine`` may not import (see the
``no-business-logic-upward-into-api`` import contract). Inverting the
dependency keeps a single plan-status write path for the whole product rather
than growing a second one inside the engine that could drift from the audited
one.
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable
from uuid import UUID

from synthorg.core.plan import Plan, PlanItem
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.project import Project
from synthorg.core.types import NotBlankStr
from synthorg.engine.initiative.completion import ReplanDisposition, StallReason


@runtime_checkable
class PlanStatusWriter(Protocol):
    """The audited plan-status write, as the rollup needs it.

    Structurally satisfied by ``api.services.plan_service.PlanService``, which
    is what the wiring injects.
    """

    async def sync_status(
        self,
        existing: Plan,
        status: PlanStatus,
        *,
        requested_by: str | None = None,
        reason: str | None = None,
        failure_reason: NotBlankStr | None = None,
    ) -> Plan:
        """Persist *status* onto *existing*, validating the transition.

        *failure_reason* is required exactly when *status* is FAILED and
        refused for every other status, so Plan Review can always show why a
        failed initiative failed and never shows a stale reason on a live one.
        The implementation raises on both halves rather than dropping either.

        Returns:
            The persisted plan carrying the new status.
        """
        ...


@runtime_checkable
class InitiativeReplanPort(Protocol):
    """Retire a plan and open its successor, as the replan trigger needs it.

    Structurally satisfied by the adapter the API wiring builds over
    ``api.controllers._plan_replan.replan_initiative``, which owns the
    compensated ordering across the plan service, the project repository, and
    the task engine. The engine states what it needs and the API supplies it,
    so there is one re-plan path whether a human or the org asked for it.
    """

    async def replan(
        self,
        existing: Plan,
        *,
        items: tuple[PlanItem, ...],
        requested_by: str,
        replan_generation: int,
    ) -> Plan:
        """Open the successor that replaces *existing*.

        Returns:
            The persisted successor, awaiting review.
        """
        ...


@runtime_checkable
class ReplanTriggerPort(Protocol):
    """The stalled-initiative replan trigger, as the rollup needs it.

    Structurally satisfied by
    ``engine.initiative.replan_trigger.ReplanTriggerService``. The rollup calls
    it while a plan reads as stalled. Like the retro trigger it must not block
    or raise: the call starts detached work and returns.

    It answers rather than merely acting, because the two refusals it owns (the
    master switch and the generation cap) are invisible to every caller. A
    caller reading "a trigger is attached" as "a replan will happen" schedules
    against a refusal for ever, which is exactly what left one initiative
    reading ``executing`` with every item dead.
    """

    async def consider(
        self,
        *,
        plan: Plan,
        reason: StallReason,
        detail: str | None = None,
    ) -> ReplanDisposition:
        """Replan *plan* if the org may still do so unasked, and say which.

        Applies the master switch and the generation cap, because this is the
        automatic authority: it answers "may the organisation replan this
        without being asked". A refusal is returned, never swallowed.

        *detail* carries whatever the caller knows that the item statuses do
        not: the evaluate stage passes its unmet criteria and their evidence,
        which is the only account of what the delivered whole failed at.

        Returns:
            What became of the ask.
        """
        ...

    async def grant(
        self,
        *,
        plan: Plan,
        reason: StallReason,
        requested_by: str,
        detail: str | None = None,
    ) -> bool:
        """Replan *plan* once on a person's authority, cap and switch aside.

        The other door on the same owner. ``consider`` asks whether the org
        may act unasked; this is the answer to a person who has just asked, so
        neither ``auto_replan_enabled`` nor the generation cap applies and the
        successor carries generation zero, on the shipped rule that a human
        decision is not a runaway. The stall is still re-confirmed, because a
        decision taken hours after it was raised may be answering a plan that
        has since recovered or been replanned by hand.

        Returns:
            Whether the detached replan started.
        """
        ...

    async def drain(self, *, timeout_sec: float) -> None:
        """Await outstanding replans at shutdown, bounded by *timeout_sec*."""
        ...


@runtime_checkable
class SkeletonPort(Protocol):
    """The SKELETON stage, as the rollup needs it.

    Structurally satisfied by
    ``engine.initiative.skeleton.SkeletonStageService``. The rollup calls this
    while a plan reads as SKELETON; the stage itself is idempotent (its task id
    is derived from the plan id), so a repeated call is a no-op. It must not
    block or raise: the call schedules detached work and returns.

    Separate from :class:`IntegrationPort` rather than one "stage port" both
    satisfy, because the rollup holds them in different fields and an unwired
    one has a different consequence at each end: unwired here parks a plan
    before anything is built, which is safe; unwired there parks one after
    everything is, which is not.
    """

    def schedule(self, *, plan: Plan, attempt: int = 0) -> None:
        """Schedule contract attempt *attempt* for a plan in SKELETON."""
        ...

    async def drain(self, *, timeout_sec: float) -> None:
        """Await outstanding dispatches at shutdown, bounded by *timeout_sec*."""
        ...


@runtime_checkable
class IntegrationPort(Protocol):
    """The INTEGRATE stage, as the rollup needs it.

    Structurally satisfied by
    ``engine.initiative.integrate.IntegrationStageService``. The rollup calls
    this while a plan reads as INTEGRATING; the stage itself is idempotent (its
    task id is derived from the plan id), so a repeated call is a no-op. It
    must not block or raise: the call schedules detached work and returns.
    """

    def schedule(self, *, plan: Plan, attempt: int = 0) -> None:
        """Schedule assembly attempt *attempt* for a plan in INTEGRATING."""
        ...

    async def drain(self, *, timeout_sec: float) -> None:
        """Await outstanding dispatches at shutdown, bounded by *timeout_sec*."""
        ...


@runtime_checkable
class EvaluationPort(Protocol):
    """The EVALUATE stage, as the rollup needs it.

    Structurally satisfied by
    ``engine.initiative.evaluate.EvaluationStageService``. The rollup calls
    this while a plan reads as EVALUATING; the stage collapses duplicates
    itself and completes the plan through the audited write path when its
    verdict passes. It must not block or raise.
    """

    def schedule(self, *, plan: Plan) -> None:
        """Schedule the judgement for a plan that entered EVALUATING."""
        ...

    async def drain(self, *, timeout_sec: float) -> None:
        """Await outstanding judgements at shutdown, bounded by *timeout_sec*."""
        ...


class DriveOutcome(StrEnum):
    """What happened when a plan was handed to a driver.

    Three values rather than a boolean, because the two ways of not driving
    demand opposite responses and a boolean cannot tell them apart. ``HELD``
    means somebody else already owns the plan, which is correct and needs
    nothing. ``REFUSED`` means this deployment cannot drive the plan at all,
    which is a dead end: the recovery sweep logged a rescue on every pass for a
    plan whose objective task was gone, so a permanently undrivable run read as
    being saved every ten minutes for ever.

    ``DRIVING``: a drive now owns the plan. ``HELD``: one already did.
    ``REFUSED``: nothing can, and the caller owns what to do about it.
    """

    DRIVING = "driving"
    HELD = "held"
    REFUSED = "refused"


@runtime_checkable
class PlanDriver(Protocol):
    """Runs a dispatched plan's remaining waves.

    A port rather than a call, because driving a plan needs the coordinator,
    the agent roster and the objective task, which are assembled in the API
    layer. Stating it here keeps the engine's own dependencies to the graph it
    reads.

    Two callers, and they ask the same question at different moments. The
    rollup asks it once, when the skeleton passes and the plan's units become
    dispatchable for the first time. The recovery sweep asks it on a cadence,
    for a plan whose driver died with the process that held it. One protocol,
    because a plan being driven is a plan being driven; what differs is only
    what each caller does with a refusal.
    """

    async def __call__(self, plan: Plan) -> DriveOutcome:
        """Drive *plan*'s remaining waves to whatever they reach.

        Returns:
            Which of the three things happened.
        """
        ...


@dataclass(frozen=True, slots=True)
class StagePorts:
    """The staged jobs a plan can sit in, as one argument.

    Bundled rather than passed one by one because they are one concept and they
    arrive together: each is resolved in the same later boot phase, each is
    optional for the same reason, and an unwired one parks the plan in its own
    status. Passing them separately also put the rollup's constructor over the
    argument cap the moment a third stage existed, which is the cap doing its
    job: three parameters that are always handled identically are one.

    Attributes:
        skeleton: The SKELETON stage, or ``None`` to park before dispatch.
        integration: The INTEGRATE stage, or ``None`` to park before assembly.
        evaluation: The EVALUATE stage, or ``None`` to park before delivery.
    """

    skeleton: SkeletonPort | None = None
    integration: IntegrationPort | None = None
    evaluation: EvaluationPort | None = None


#: Reads whichever replan trigger the rollup is holding *now*. The EVALUATE
#: stage resolves it per unmet verdict rather than capturing one at
#: construction, because the two attach on their own schedules: a coordinator
#: that arrives after the provider registry would otherwise leave a stage
#: permanently holding the ``None`` it was built with, and every unmet
#: initiative parked instead of replanned.
ReplanTriggerResolver = Callable[[], ReplanTriggerPort | None]


@runtime_checkable
class PlanReconcilePort(Protocol):
    """Re-derive an initiative's status graph, as the tail stages need it.

    Structurally satisfied by ``ProjectRollupService.recompute``. The rollup
    normally re-derives on a task event, but the EVALUATE stage's verdict
    writes a plan status while mutating no task, so nothing would follow it:
    the project, the objective task, and the retrospective all hang off a
    recompute that would never happen. The stage calls this once its verdict
    lands to close that gap.
    """

    async def recompute(self, plan_id: UUID) -> None:
        """Re-derive and persist the status graph behind *plan_id*."""
        ...

    async def report_stage_stall(
        self,
        plan_id: UUID,
        reason: StallReason,
        disposition: ReplanDisposition | None,
    ) -> None:
        """Escalate a stall the stage saw and no derivation over items can.

        ``recompute`` finds an item-derived stall on its own. A tail-stage
        verdict is invisible to it, because every item IS done when
        integration fails or the objective goes unmet, so a stage that keeps
        its verdict to itself leaves the initiative parked with nobody asked.

        Args:
            plan_id: The initiative that cannot advance.
            reason: The stage's verdict.
            disposition: What the trigger already answered the stage, or
                ``None`` when the stage found no trigger at all.
        """
        ...


@runtime_checkable
class RetroCapturePort(Protocol):
    """The SHIP-time retrospective trigger, as the rollup needs it.

    Structurally satisfied by
    ``engine.initiative.retro_capture.ShipRetroCaptureService``. The rollup
    calls this exactly once, on the edge a project first reaches COMPLETED, so
    finished work feeds back into memory. It must not block or raise: the call
    schedules detached work and returns immediately.
    """

    def schedule(self, *, plan: Plan, project: Project) -> None:
        """Schedule retrospective capture for a just-completed objective."""
        ...

    async def drain(self, *, timeout_sec: float) -> None:
        """Await outstanding capture tasks at shutdown, bounded by *timeout_sec*.

        Called before the memory backends the captures write to are
        disconnected, so an in-flight retrospective is not stranded mid-write.
        """
        ...
