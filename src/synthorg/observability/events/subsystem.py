"""Subsystem reconciliation event names."""

from typing import Final

SUBSYSTEM_RECONCILE_STARTED: Final[str] = "subsystem.reconcile.started"
SUBSYSTEM_RECONCILE_COMPLETED: Final[str] = "subsystem.reconcile.completed"
SUBSYSTEM_RECONCILE_ABORTED: Final[str] = "subsystem.reconcile.aborted"
SUBSYSTEM_ACTIVATED: Final[str] = "subsystem.activated"
SUBSYSTEM_DEACTIVATED: Final[str] = "subsystem.deactivated"
SUBSYSTEM_ACTIVATION_FAILED: Final[str] = "subsystem.activation.failed"
SUBSYSTEM_DEACTIVATION_FAILED: Final[str] = "subsystem.deactivation.failed"
SUBSYSTEM_WAITING: Final[str] = "subsystem.waiting"
SUBSYSTEM_RESYNC_STARTED: Final[str] = "subsystem.resync.started"
SUBSYSTEM_RESYNC_STOPPED: Final[str] = "subsystem.resync.stopped"
