# module-kind: code
"""What a reconcile pass observed, and what it changed.

Read by the status endpoint and by the callers that gate on a pass, so it
lives apart from the reconciler that produces it.
"""

from dataclasses import dataclass

from synthorg.api.subsystems.spec import (
    PHASES_NAMING_UNMET,
    PHASES_WITH_DETAIL,
    CapabilityId,
    SubsystemPhase,
)


@dataclass(frozen=True, slots=True)
class SubsystemStatus:
    """What the last reconcile pass observed about one subsystem.

    Attributes:
        name: The subsystem's stable identifier.
        phase: Its resting state after the pass.
        waiting_on: Unmet requirements, populated for ``WAITING``, for
            ``UNREACHABLE`` (which is waiting with no exit), and for
            ``DEGRADED``, which is up with a requirement gone. Names every
            missing capability, not just the first.
        detail: Why this subsystem is not simply up. A redacted failure
            description on ``FAILED``, what the activation declined on for
            ``BLOCKED``, and which owner will never supply the requirement for
            ``UNREACHABLE``. Absent on every phase that has nothing to add.
    """

    name: str
    phase: SubsystemPhase
    waiting_on: tuple[CapabilityId, ...] = ()
    detail: str | None = None

    def __post_init__(self) -> None:
        """Refuse a status whose payload contradicts its phase.

        Raises:
            ValueError: When ``waiting_on`` is populated on a phase that names
                no unmet requirement, or ``detail`` on a phase that has nothing
                to explain. An operator reads this to find out why something is
                off, so a stale field from a previous phase is worse than none.
        """
        if self.waiting_on and self.phase not in PHASES_NAMING_UNMET:
            msg = (
                "waiting_on is only valid on WAITING, UNREACHABLE or DEGRADED,"
                f" got {self.phase.value}"
            )
            raise ValueError(msg)
        if self.detail is not None and self.phase not in PHASES_WITH_DETAIL:
            msg = (
                "detail is only valid on FAILED, BLOCKED or UNREACHABLE, got "
                f"{self.phase.value}"
            )
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class ReconcileReport:
    """The outcome of one pass.

    Attributes:
        statuses: Per-subsystem observation, in activation order.
        activated: Names brought up during this pass.
        deactivated: Names taken down during this pass, and still down at the
            end of it. A rebuild is teardown-then-activate, so it reports as
            activated: naming it here would read as an outage.
        deferred: Whether the caller's trigger was handed to a pass already in
            flight rather than run here. The statuses are then a snapshot taken
            mid-pass, so an absent failure means only that the pass has not
            reached that subsystem yet. A caller gating on the outcome has to
            refuse this report; one merely reporting current state can use it.
    """

    statuses: tuple[SubsystemStatus, ...]
    activated: tuple[str, ...] = ()
    deactivated: tuple[str, ...] = ()
    deferred: bool = False

    @property
    def failed(self) -> tuple[str, ...]:
        """Names whose activation raised on this pass.

        Returns:
            The failing subsystem names, in activation order.
        """
        return tuple(
            status.name
            for status in self.statuses
            if status.phase is SubsystemPhase.FAILED
        )
