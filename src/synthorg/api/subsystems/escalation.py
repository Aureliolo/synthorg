# module-kind: code
"""Tell an operator when a subsystem stops being able to come up.

``GET /subsystems`` answers "why is this not up" for anyone who asks. Nothing
asked. A memory backend whose embedding model went unreachable sat BLOCKED
while the org kept executing tasks with no recall, and the only trace was a
field in a health payload and a line in the log.

The phases split cleanly into states worth interrupting someone over and
states that are simply how things are. ``WAITING`` and ``DISABLED`` are
resting: a dependency is late, or an operator switched the subsystem off.
``DEGRADED`` is serving. ``BLOCKED`` and ``FAILED`` are neither: one declined
on a condition it cannot resolve by waiting, the other raised on its way up,
and both will stay that way until a person does something.
"""

from collections.abc import Sequence

from synthorg.api.state import AppState
from synthorg.api.subsystems.report import SubsystemStatus
from synthorg.api.subsystems.spec import SubsystemPhase
from synthorg.core.critical_errors import reraise_critical
from synthorg.notifications.dispatcher import NotificationDispatcher
from synthorg.notifications.models import (
    Notification,
    NotificationCategory,
    NotificationSeverity,
)
from synthorg.notifications.state import NotificationsStateSlice
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_SUBSYSTEM_ESCALATED,
    API_SUBSYSTEM_ESCALATION_FAILED,
)

logger = get_logger(__name__)

_SEVERITY_BY_PHASE = {
    SubsystemPhase.BLOCKED: NotificationSeverity.WARNING,
    SubsystemPhase.FAILED: NotificationSeverity.ERROR,
}

_NO_REASON = "no reason reported"


class SubsystemEscalator:
    """Raise one operator notification per distinct stuck condition.

    Deduplicated on ``(subsystem, phase, reason)`` rather than rate-limited:
    the reconciler runs a full pass on every settings write and every periodic
    sweep, so alerting per pass would turn one unreachable embedder into a
    notification every sweep for as long as it stayed unreachable, and an
    operator would filter the channel. Keyed on the reason too, so a subsystem
    that moves from one blocking condition to another says so.

    An entry is dropped once its subsystem leaves the stuck phases, so a fault
    that returns after a genuine recovery alerts again rather than being
    silently absorbed as a repeat.
    """

    __slots__ = ("_alerted",)

    def __init__(self) -> None:
        self._alerted: set[tuple[str, str, str]] = set()

    async def escalate(
        self,
        app_state: AppState,
        statuses: Sequence[SubsystemStatus],
    ) -> None:
        """Notify about every newly stuck subsystem in *statuses*.

        Best-effort throughout: this runs at the tail of a reconcile pass that
        has already done the real work, so a missing dispatcher or a flaky sink
        must not turn a successful convergence into a failed one.

        Args:
            app_state: Application state carrying the notification dispatcher.
            statuses: What the pass observed, one entry per subsystem.
        """
        stuck = [s for s in statuses if s.phase in _SEVERITY_BY_PHASE]
        self._forget_recovered({s.name for s in stuck})
        fresh = [s for s in stuck if self._claim(s)]
        if not fresh:
            return
        dispatcher = app_state.slice(NotificationsStateSlice).dispatcher
        for status in fresh:
            reason = status.detail or _NO_REASON
            logger.warning(
                API_SUBSYSTEM_ESCALATED,
                subsystem=status.name,
                phase=status.phase.value,
                reason=reason,
            )
            if dispatcher is None:
                continue
            await self._dispatch(dispatcher, status)

    def _forget_recovered(self, still_stuck: set[str]) -> None:
        """Drop remembered alerts for subsystems that are no longer stuck."""
        self._alerted = {key for key in self._alerted if key[0] in still_stuck}

    def _claim(self, status: SubsystemStatus) -> bool:
        """Whether *status* is a condition not yet alerted on.

        Returns:
            True the first time this exact condition is seen.
        """
        key = (status.name, status.phase.value, status.detail or _NO_REASON)
        if key in self._alerted:
            return False
        self._alerted.add(key)
        return True

    async def _dispatch(
        self,
        dispatcher: NotificationDispatcher,
        status: SubsystemStatus,
    ) -> None:
        """Send one notification, swallowing sink faults.

        Raises:
            MemoryError: Re-raised via ``reraise_critical``.
            RecursionError: Re-raised via ``reraise_critical``.
        """
        reason = status.detail or _NO_REASON
        try:
            await dispatcher.dispatch(
                Notification(
                    category=NotificationCategory.HEALTH,
                    severity=_SEVERITY_BY_PHASE[status.phase],
                    title=f"Subsystem {status.name!r} is {status.phase.value}",
                    body=(
                        f"{status.name} did not come up: {reason}. It stays "
                        f"this way until the condition is resolved; the "
                        f"reconciler re-attempts it on every pass."
                    ),
                    source="api.subsystems",
                ),
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- the pass already converged; a sink
            # fault must not undo it, and the WARNING above still records the
            # condition.
            reraise_critical(exc)
            logger.warning(
                API_SUBSYSTEM_ESCALATION_FAILED,
                subsystem=status.name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )


__all__ = ["SubsystemEscalator"]
