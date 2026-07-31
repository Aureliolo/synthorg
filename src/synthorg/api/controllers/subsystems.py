# module-kind: controller
"""Subsystem status endpoint.

Answers the question an operator actually has when something is missing:
not "is the deployment healthy" but "why is this particular thing off, and
what is it waiting for". Both come from the same declarations the
reconciler acts on, so the answer cannot drift from the behaviour.
"""

from litestar import Controller, get
from litestar.datastructures import State
from pydantic import BaseModel, ConfigDict, Field

from synthorg.api.dto import ApiResponse
from synthorg.api.guards import require_read_access
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.state import AppState
from synthorg.api.subsystems.runtime import reconciler_of
from synthorg.api.subsystems.spec import SubsystemPhase


class SubsystemReport(BaseModel):
    """One subsystem's current state.

    Attributes:
        name: Stable identifier, also the reconciler's key for it.
        phase: Its resting state. ``waiting`` and ``disabled`` are ordinary,
            not faults: the first will come up when its dependency arrives.
        waiting_on: Every unmet dependency, not just the first, so an
            operator fixes them in one pass rather than one per round trip.
        detail: Redacted failure description, present only for ``failed``.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    name: str = Field(description="Subsystem identifier")
    phase: SubsystemPhase = Field(description="Current resting state")
    waiting_on: tuple[str, ...] = Field(
        default=(),
        description="Unmet dependencies, when waiting",
    )
    detail: str | None = Field(
        default=None,
        description="Failure description, when failed",
    )


class SubsystemsResponse(BaseModel):
    """Every declared subsystem, in activation order.

    Attributes:
        subsystems: The reports, ordered as the reconciler activates them,
            so a dependency always appears before what waits on it.
        active: How many are up.
        waiting: How many are waiting on a named dependency.
        blocked: How many have every dependency but declined to activate.
        failed: How many raised on their last activation attempt.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    subsystems: tuple[SubsystemReport, ...] = Field(
        description="Declared subsystems in activation order",
    )
    active: int = Field(ge=0, description="Count in the active phase")
    waiting: int = Field(ge=0, description="Count waiting on a dependency")
    blocked: int = Field(ge=0, description="Count that declined to activate")
    failed: int = Field(ge=0, description="Count whose activation raised")


class SubsystemsController(Controller):
    """Reports what is wired, what is not, and what it is waiting for.

    Behind ``require_read_access`` for the same reason the health detail is:
    the set of subsystems a deployment is missing describes its topology,
    which is not something to hand an unauthenticated caller.
    """

    path = "/subsystems"
    tags = ("health",)
    guards = [require_read_access]  # noqa: RUF012

    @get(guards=[per_op_rate_limit_from_policy("health.subsystems", key="user")])
    async def subsystems(self, state: State) -> ApiResponse[SubsystemsResponse]:
        """Return every declared subsystem's current state.

        Reads live state without reconciling. A read that activated things
        would make an operator refreshing a page a cause of change.

        Returns:
            ``ApiResponse[SubsystemsResponse]`` instance.
        """
        app_state: AppState = state.app_state
        statuses = reconciler_of(app_state).statuses(app_state)
        reports = tuple(
            SubsystemReport(
                name=status.name,
                phase=status.phase,
                waiting_on=tuple(cap.value for cap in status.waiting_on),
                detail=status.detail,
            )
            for status in statuses
        )
        return ApiResponse(
            data=SubsystemsResponse(
                subsystems=reports,
                active=sum(1 for r in reports if r.phase is SubsystemPhase.ACTIVE),
                waiting=sum(1 for r in reports if r.phase is SubsystemPhase.WAITING),
                blocked=sum(1 for r in reports if r.phase is SubsystemPhase.BLOCKED),
                failed=sum(1 for r in reports if r.phase is SubsystemPhase.FAILED),
            ),
        )
