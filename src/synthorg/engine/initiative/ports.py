# module-kind: code
"""Ports the initiative rollup depends on.

The rollup lives in ``engine`` but the audited plan-status write path lives in
``api.services``, which ``engine`` may not import (see the
``no-business-logic-upward-into-api`` import contract). Inverting the
dependency keeps a single plan-status write path for the whole product rather
than growing a second one inside the engine that could drift from the audited
one.
"""

from typing import Protocol, runtime_checkable

from synthorg.core.plan import Plan
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.project import Project


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
    ) -> Plan:
        """Persist *status* onto *existing*, validating the transition.

        Returns:
            The persisted plan carrying the new status.
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
