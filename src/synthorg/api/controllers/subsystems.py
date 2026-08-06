# module-kind: controller
"""Subsystem status endpoint.

Answers the question an operator actually has when something is missing:
not "is the deployment healthy" but "why is this particular thing off, and
what is it waiting for". Both come from the same declarations the
reconciler acts on, so the answer cannot drift from the behaviour.
"""

from collections import Counter

from litestar import Controller, get
from litestar.datastructures import State
from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.api.dto import ApiResponse
from synthorg.api.guards import require_read_access
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.state import AppState
from synthorg.api.subsystems.runtime import reconciler_of
from synthorg.api.subsystems.spec import SubsystemPhase
from synthorg.core.types import NotBlankStr


class SubsystemReport(BaseModel):
    """One subsystem's current state.

    Attributes:
        name: Stable identifier, also the reconciler's key for it.
        phase: Its resting state. ``waiting`` and ``disabled`` are ordinary,
            not faults: the first will come up when its dependency arrives.
            ``unreachable`` is the one that will not, because the dependency's
            owner is off or has declined; ``rebuilding`` is a subsystem down
            and coming back inside the pass currently running.
        waiting_on: Every unmet dependency, not just the first, so an
            operator fixes them in one pass rather than one per round trip.
        detail: Why this subsystem is not simply up: a redacted failure
            description on ``failed``, what the activation declined on for
            ``blocked``, and which owner will never supply the dependency for
            ``unreachable``.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    name: NotBlankStr = Field(description="Subsystem identifier")
    phase: SubsystemPhase = Field(description="Current resting state")
    waiting_on: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Unmet dependencies, when waiting, unreachable or degraded",
    )
    detail: str | None = Field(
        default=None,
        description="Why it is not up, when failed, blocked or unreachable",
    )

    @model_validator(mode="after")
    def _validate_payload_matches_phase(self) -> SubsystemReport:
        """Reject a report whose payload contradicts its phase.

        Returns:
            The validated report.

        Raises:
            ValueError: When ``waiting_on`` is populated on a phase that names
                no unmet requirement, or ``detail`` on anything but ``failed``.
                This is what an operator reads to find out why something is
                off, so a field left over from a previous phase is worse than
                an empty one. ``degraded`` carries ``waiting_on`` for the same
                reason ``waiting`` does: it is up, but a requirement it names
                has gone away.
        """
        names_unmet = {
            SubsystemPhase.WAITING,
            SubsystemPhase.UNREACHABLE,
            SubsystemPhase.DEGRADED,
        }
        if self.waiting_on and self.phase not in names_unmet:
            msg = (
                "waiting_on is only valid on waiting, unreachable or degraded,"
                f" got {self.phase.value}"
            )
            raise ValueError(msg)
        explains = {
            SubsystemPhase.FAILED,
            SubsystemPhase.BLOCKED,
            SubsystemPhase.UNREACHABLE,
        }
        if self.detail is not None and self.phase not in explains:
            msg = (
                "detail is only valid on failed, blocked or unreachable, got "
                f"{self.phase.value}"
            )
            raise ValueError(msg)
        return self


class SubsystemsResponse(BaseModel):
    """Every declared subsystem, in activation order.

    Attributes:
        subsystems: The reports, ordered as the reconciler activates them,
            so a dependency always appears before what waits on it.
        active: How many are up.
        degraded: How many are up while a requirement is gone.
        waiting: How many are waiting on a named dependency.
        unreachable: How many are waiting on a dependency no pass will
            supply, because its owner is switched off or declined.
        rebuilding: How many are down and coming back inside the pass
            currently running.
        blocked: How many have every dependency but declined to activate.
        failed: How many raised on their last activation attempt.
        disabled: How many an operator has switched off.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    subsystems: tuple[SubsystemReport, ...] = Field(
        description="Declared subsystems in activation order",
    )
    active: int = Field(ge=0, description="Count in the active phase")
    degraded: int = Field(ge=0, description="Count up with a missing requirement")
    waiting: int = Field(ge=0, description="Count waiting on a dependency")
    unreachable: int = Field(ge=0, description="Count waiting with no exit")
    rebuilding: int = Field(ge=0, description="Count mid-rebuild in this pass")
    blocked: int = Field(ge=0, description="Count that declined to activate")
    failed: int = Field(ge=0, description="Count whose activation raised")
    disabled: int = Field(ge=0, description="Count an operator switched off")


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
        counts = Counter(report.phase for report in reports)
        return ApiResponse(
            data=SubsystemsResponse(
                subsystems=reports,
                active=counts[SubsystemPhase.ACTIVE],
                degraded=counts[SubsystemPhase.DEGRADED],
                waiting=counts[SubsystemPhase.WAITING],
                unreachable=counts[SubsystemPhase.UNREACHABLE],
                rebuilding=counts[SubsystemPhase.REBUILDING],
                blocked=counts[SubsystemPhase.BLOCKED],
                failed=counts[SubsystemPhase.FAILED],
                disabled=counts[SubsystemPhase.DISABLED],
            ),
        )
