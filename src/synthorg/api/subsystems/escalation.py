# module-kind: code
"""Tell an operator when a subsystem stops being able to come up.

``GET /subsystems`` answers "why is this not up" for anyone who asks, and
nothing in the system asks. A subsystem that declines is therefore visible
only to someone already looking at a health payload or a log stream, so it
can stay down indefinitely while the org keeps executing around the hole.

The phases split cleanly into states worth interrupting someone over and
states that are simply how things are. ``WAITING`` and ``DISABLED`` are
resting: a dependency is late, or an operator switched the subsystem off.
``DEGRADED`` is serving. ``BLOCKED`` and ``FAILED`` are neither: one declined
on a condition it cannot resolve by waiting, the other raised on its way up,
and both will stay that way until a person does something.

``UNREACHABLE`` reads like a third member of that pair and is deliberately
not one. The reconciler only produces it for a subsystem whose dependency
has an owner that is itself BLOCKED or DISABLED, so escalating it would
either double-report a condition the owner already raised or interrupt an
operator about the direct consequence of a switch they threw themselves.
The subsystem that can actually be acted on is the one that gets the alert.
"""

import asyncio
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
    API_SUBSYSTEM_ESCALATION_UNDELIVERED,
    API_SUBSYSTEM_ESCALATION_UNROUTED,
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

    A condition is remembered only once a sink has actually accepted it.
    Nothing in the dispatch chain retries: the dispatcher and every sink are
    one-shot best-effort, and the reconciler re-runs this pass for as long as
    the subsystem stays stuck. Marking a condition alerted before delivery
    would let one transient outage suppress that condition permanently, which
    is the failure this class exists to prevent.

    Delivery is what the dispatcher reports, not what it returns without
    raising. It drops a notification silently when it is shutting down, when
    the kill-switch is off, when no sink is registered and when the severity
    falls below its floor, so a clean return covers both "every sink took it"
    and "nobody was told". Only a non-zero accepted count claims the
    condition; the absent-dispatcher case leaves it pending for the same
    reason, since nothing has been reported to anyone yet.
    """

    __slots__ = ("_alerted", "_unrouted_logged")

    def __init__(self) -> None:
        self._alerted: set[tuple[str, str, str]] = set()
        self._unrouted_logged = False

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
        fresh = [s for s in stuck if self._key(s) not in self._alerted]
        if not fresh:
            return
        dispatcher = app_state.slice(NotificationsStateSlice).dispatcher
        self._report_unrouted(dispatcher)
        for status in fresh:
            logger.warning(
                API_SUBSYSTEM_ESCALATED,
                subsystem=status.name,
                phase=status.phase.value,
                reason=status.detail or _NO_REASON,
            )
        if dispatcher is None:
            return
        # Concurrently, because the reconciler awaits this while still holding
        # its pass lock: sequentially, a broad outage that blocks many
        # subsystems at once would hold that lock for the sum of every sink's
        # timeout, stalling every other trigger on the loop behind it.
        async with asyncio.TaskGroup() as group:
            sent = [
                (status, group.create_task(self._dispatch(dispatcher, status)))
                for status in fresh
            ]
        self._alerted.update(self._key(s) for s, task in sent if task.result())

    def _report_unrouted(self, dispatcher: NotificationDispatcher | None) -> None:
        """Say once that stuck subsystems have nowhere to be reported to.

        Every shipped launcher wires a dispatcher during construction, so a
        missing one means an assembly this code did not expect. Without this
        the escalator would look identical to one whose sinks are all healthy
        and simply have nothing to say.
        """
        if dispatcher is not None:
            self._unrouted_logged = False
            return
        if self._unrouted_logged:
            return
        self._unrouted_logged = True
        logger.error(API_SUBSYSTEM_ESCALATION_UNROUTED)

    def _forget_recovered(self, still_stuck: set[str]) -> None:
        """Drop remembered alerts for subsystems that are no longer stuck."""
        self._alerted = {key for key in self._alerted if key[0] in still_stuck}

    @staticmethod
    def _key(status: SubsystemStatus) -> tuple[str, str, str]:
        """Return the dedup identity of one stuck condition.

        Returns:
            The ``(subsystem, phase, reason)`` triple this condition is
            remembered under.
        """
        return (status.name, status.phase.value, status.detail or _NO_REASON)

    async def _dispatch(
        self,
        dispatcher: NotificationDispatcher,
        status: SubsystemStatus,
    ) -> bool:
        """Send one notification, swallowing sink faults.

        Returns:
            True when at least one sink accepted the notification. False
            leaves the condition unremembered so the next pass tries again,
            covering a raise and a silent drop alike.

        Raises:
            MemoryError: Re-raised via ``reraise_critical``.
            RecursionError: Re-raised via ``reraise_critical``.
        """
        reason = status.detail or _NO_REASON
        try:
            accepted = await dispatcher.dispatch(
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
            return False
        if accepted == 0:
            logger.warning(
                API_SUBSYSTEM_ESCALATION_UNDELIVERED,
                subsystem=status.name,
                phase=status.phase.value,
            )
            return False
        return True


__all__ = ["SubsystemEscalator"]
