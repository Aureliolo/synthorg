"""Checkpoint recovery event constants for structured logging."""

from typing import Final

# Checkpoint lifecycle
CHECKPOINT_SAVED: Final[str] = "checkpoint.saved"
CHECKPOINT_SAVE_FAILED: Final[str] = "checkpoint.save_failed"
CHECKPOINT_LOADED: Final[str] = "checkpoint.loaded"
CHECKPOINT_LOAD_FAILED: Final[str] = "checkpoint.load_failed"
CHECKPOINT_DELETED: Final[str] = "checkpoint.deleted"
CHECKPOINT_DELETE_FAILED: Final[str] = "checkpoint.delete_failed"
CHECKPOINT_SKIPPED: Final[str] = "checkpoint.skipped"

# Heartbeat lifecycle
HEARTBEAT_UPDATED: Final[str] = "heartbeat.updated"
HEARTBEAT_UPDATE_FAILED: Final[str] = "heartbeat.update_failed"
HEARTBEAT_STALE_DETECTED: Final[str] = "heartbeat.stale_detected"
HEARTBEAT_DELETED: Final[str] = "heartbeat.deleted"
HEARTBEAT_DELETE_FAILED: Final[str] = "heartbeat.delete_failed"

# Loop integration
CHECKPOINT_UNSUPPORTED_LOOP: Final[str] = "checkpoint.unsupported_loop"

# Recovery flow
CHECKPOINT_RECOVERY_START: Final[str] = "checkpoint.recovery.start"
CHECKPOINT_RECOVERY_RESUME: Final[str] = "checkpoint.recovery.resume"
CHECKPOINT_RECOVERY_FALLBACK: Final[str] = "checkpoint.recovery.fallback"
CHECKPOINT_RECOVERY_NO_CHECKPOINT: Final[str] = "checkpoint.recovery.no_checkpoint"
CHECKPOINT_RECOVERY_RECONCILIATION: Final[str] = "checkpoint.recovery.reconciliation"
CHECKPOINT_RECOVERY_DESERIALIZE_FAILED: Final[str] = (
    "checkpoint.recovery.deserialize_failed"
)
