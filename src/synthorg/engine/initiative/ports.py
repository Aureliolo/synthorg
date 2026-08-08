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
from typing import Protocol, runtime_checkable
from uuid import UUID

from synthorg.core.plan import Plan, PlanItem
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.project import Project
from synthorg.core.types import NotBlankStr
from synthorg.engine.initiative.completion import StallReason


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

        *failure_reason* is required exactly when *status* is FAILED (the plan
        model and the column check both enforce it) and rejected otherwise, so
        Plan Review can always show why a failed initiative failed.

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
    this on the edge a plan first reads as stalled. Like the retro trigger it
    must not block or raise: the call schedules detached work and returns.
    """

    def schedule(
        self,
        *,
        plan: Plan,
        reason: StallReason,
        detail: str | None = None,
    ) -> None:
        """Schedule a replan for a plan that can no longer advance.

        *detail* carries whatever the caller knows that the item statuses do
        not: the evaluate stage passes its unmet criteria and their evidence,
        which is the only account of what the delivered whole failed at.
        """
        ...

    async def drain(self, *, timeout_sec: float) -> None:
        """Await outstanding replans at shutdown, bounded by *timeout_sec*."""
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
