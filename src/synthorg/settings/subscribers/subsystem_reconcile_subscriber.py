"""Subsystem reconcile settings subscriber.

Turns an operator's settings write into a reconcile pass. The watched set is
derived from the subsystem declarations themselves (``SubsystemSpec.settings``
and ``enabled_by``), so a subsystem that starts reading a new setting cannot
forget to register it here: there is no second list to keep in step.

The pass is level-triggered, so this is a hint that state moved rather than an
instruction. A write to a key nothing declares is still safe; it simply
converges a system that is already converged.
"""

from synthorg.api.state import AppState
from synthorg.api.subsystems.registry import SUBSYSTEMS
from synthorg.observability import get_logger
from synthorg.observability.events.settings import SETTINGS_SUBSCRIBER_NOTIFIED
from synthorg.settings.service import SettingsService

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

    async def on_settings_changed(self, namespace: str, key: str) -> None:
        """Reconcile so a subsystem built from this setting is replaced."""
        from synthorg.api.subsystems.runtime import (  # noqa: PLC0415
            reconcile_subsystems,
        )

        report = await reconcile_subsystems(
            self._app_state,
            trigger=f"setting:{namespace}.{key}",
        )
        logger.info(
            SETTINGS_SUBSCRIBER_NOTIFIED,
            subscriber=self.subscriber_name,
            namespace=namespace,
            key=key,
            activated=len(report.activated) if report is not None else 0,
            deactivated=len(report.deactivated) if report is not None else 0,
        )
