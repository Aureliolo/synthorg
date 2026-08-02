"""Subsystem reconciliation event names."""

from typing import Final

SUBSYSTEM_RECONCILE_STARTED: Final[str] = "subsystem.reconcile.started"
SUBSYSTEM_RECONCILE_COMPLETED: Final[str] = "subsystem.reconcile.completed"
SUBSYSTEM_RECONCILE_ABORTED: Final[str] = "subsystem.reconcile.aborted"
SUBSYSTEM_RECONCILE_DEFERRED: Final[str] = "subsystem.reconcile.deferred"
SUBSYSTEM_ACTIVATED: Final[str] = "subsystem.activated"
SUBSYSTEM_DEACTIVATED: Final[str] = "subsystem.deactivated"
SUBSYSTEM_ACTIVATION_FAILED: Final[str] = "subsystem.activation.failed"
SUBSYSTEM_ACTIVATION_DECLINED: Final[str] = "subsystem.activation.declined"
SUBSYSTEM_DEACTIVATION_FAILED: Final[str] = "subsystem.deactivation.failed"
SUBSYSTEM_SETTINGS_UNREADABLE: Final[str] = "subsystem.settings.unreadable"
SUBSYSTEM_CAPABILITY_PROBE_FAILED: Final[str] = "subsystem.capability.probe_failed"
SUBSYSTEM_RESYNC_STARTED: Final[str] = "subsystem.resync.started"
SUBSYSTEM_RESYNC_STOPPED: Final[str] = "subsystem.resync.stopped"
SUBSYSTEM_RESYNC_FAILED: Final[str] = "subsystem.resync.failed"
