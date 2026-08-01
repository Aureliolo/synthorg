# module-kind: code
"""What a reconcile pass observed, and what it changed.

Read by the status endpoint and by the callers that gate on a pass, so it
lives apart from the reconciler that produces it.
"""

from dataclasses import dataclass

from synthorg.api.subsystems.spec import CapabilityId, SubsystemPhase


@dataclass(frozen=True, slots=True)
class SubsystemStatus:
    """What the last reconcile pass observed about one subsystem.

    Attributes:
        name: The subsystem's stable identifier.
        phase: Its resting state after the pass.
        waiting_on: Unmet requirements, populated for ``WAITING`` and for
            ``DEGRADED``, which is up with a requirement gone. Names every
            missing capability, not just the first.
        detail: Redacted failure description, populated only for ``FAILED``.
    """

    name: str
    phase: SubsystemPhase
    waiting_on: tuple[CapabilityId, ...] = ()
    detail: str | None = None

    def __post_init__(self) -> None:
        """Refuse a status whose payload contradicts its phase.

        Raises:
            ValueError: When ``waiting_on`` is populated on a phase that names
                no unmet requirement, or ``detail`` on anything but
                ``FAILED``. An operator reads this to find out why something
                is off, so a stale field from a previous phase is worse than
                none. ``DEGRADED`` carries it for the same reason ``WAITING``
                does: it is up, but the requirement it names is gone.
        """
        names_unmet = {SubsystemPhase.WAITING, SubsystemPhase.DEGRADED}
        if self.waiting_on and self.phase not in names_unmet:
            msg = (
                "waiting_on is only valid on WAITING or DEGRADED, got "
                f"{self.phase.value}"
            )
            raise ValueError(msg)
        if self.detail is not None and self.phase is not SubsystemPhase.FAILED:
            msg = f"detail is only valid on FAILED, got {self.phase.value}"
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
    """

    statuses: tuple[SubsystemStatus, ...]
    activated: tuple[str, ...] = ()
    deactivated: tuple[str, ...] = ()

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
