"""Checkpoint recovery for agent crash recovery.

Persists ``AgentContext`` snapshots at configurable turn intervals
and resumes from the last checkpoint on crash, preserving progress.
"""

from synthorg.engine.checkpoint.callback import CheckpointCallback
from synthorg.engine.checkpoint.callback_factory import make_checkpoint_callback
from synthorg.engine.checkpoint.models import (
    Checkpoint,
    CheckpointConfig,
    Heartbeat,
)
from synthorg.engine.checkpoint.resume import (
    cleanup_checkpoint_artifacts,
    deserialize_and_reconcile,
    make_loop_with_callback,
)
from synthorg.engine.checkpoint.strategy import CheckpointRecoveryStrategy

__all__ = [
    "Checkpoint",
    "CheckpointCallback",
    "CheckpointConfig",
    "CheckpointRecoveryStrategy",
    "Heartbeat",
    "cleanup_checkpoint_artifacts",
    "deserialize_and_reconcile",
    "make_checkpoint_callback",
    "make_loop_with_callback",
]
