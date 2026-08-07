"""Subsystem reconcile settings subscriber.

Turns an operator's settings write into a reconcile pass. The watched set is
derived from the subsystem declarations themselves (``SubsystemSpec.settings``
and ``enabled_by``), so a subsystem that starts reading a new setting cannot
forget to register it here: there is no second list to keep in step.

The pass is level-triggered, so this is a hint that state moved rather than an
instruction. A write to a key nothing declares is still safe; it simply
converges a system that is already converged.
"""

from collections.abc import Sequence

from synthorg.api.state import AppState
from synthorg.api.subsystems.registry import SUBSYSTEMS
from synthorg.observability import get_logger
from synthorg.observability.events.settings import SETTINGS_SUBSCRIBER_NOTIFIED
from synthorg.settings.service import SettingsService
from synthorg.settings.subscriber import describe_changes

logger = get_logger(__name__)


def _declared_keys() -> frozenset[tuple[str, str]]:
    """Collect every setting the subsystem declarations name.

    Returns:
        The ``(namespace, key)`` pairs a subsystem reads at activation or is
        gated by.
    """
    pairs: set[tuple[str, str]] = set()
    for spec in SUBSYSTEMS:
        entries = [*spec.settings]
        if spec.enabled_by is not None:
            entries.append(spec.enabled_by)
        for entry in entries:
            namespace, _, key = entry.partition(".")
            pairs.add((namespace, key))
    return frozenset(pairs)


_WATCHED: frozenset[tuple[str, str]] = _declared_keys()


class SubsystemReconcileSettingsSubscriber:
    """Run a reconcile pass when a subsystem-relevant setting changes.

    Args:
        app_state: Application state the pass reads and wires.
        settings_service: Held for symmetry with peer subscribers.
    """

    def __init__(
        self,
        app_state: AppState,
        settings_service: SettingsService,
    ) -> None:
        self._app_state = app_state
        self._settings_service = settings_service

    @property
    def watched_keys(self) -> frozenset[tuple[str, str]]:
        """Return the ``(namespace, key)`` pairs this subscriber watches."""
        return _WATCHED

    @property
    def subscriber_name(self) -> str:
        """Human-readable subscriber name for logs."""
        return "subsystem-reconcile"

    async def on_settings_changed(self, changes: Sequence[tuple[str, str]]) -> None:
        """Reconcile so every subsystem built from these settings is replaced.

        One pass per batch. A reconcile is level-triggered and idempotent: it
        evaluates every subsystem against current state regardless of what
        prompted it, so a pass per changed key would tear down and rebuild the
        same subsystems repeatedly for a single form save.

        Args:
            changes: The watched writes this pass carries.
        """
        from synthorg.api.subsystems.runtime import (  # noqa: PLC0415
            reconcile_subsystems,
        )

        trigger = describe_changes(changes)
        report = await reconcile_subsystems(
            self._app_state,
            trigger=f"setting:{trigger}",
        )
        if report is None:
            # A pass that could not run is not a pass that found nothing to
            # do. Reporting it as zero-and-zero reads as converged, which is
            # the one conclusion an operator must not draw from it.
            logger.info(
                SETTINGS_SUBSCRIBER_NOTIFIED,
                subscriber=self.subscriber_name,
                trigger=trigger,
                outcome="not_run",
            )
            return
        logger.info(
            SETTINGS_SUBSCRIBER_NOTIFIED,
            subscriber=self.subscriber_name,
            trigger=trigger,
            outcome="reconciled",
            activated=len(report.activated),
            deactivated=len(report.deactivated),
        )
