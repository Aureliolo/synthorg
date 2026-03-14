"""Checkpoint recovery for agent crash recovery.

Persists ``AgentContext`` snapshots at configurable turn intervals
and resumes from the last checkpoint on crash, preserving progress.
"""

from ai_company.engine.checkpoint.callback import CheckpointCallback
from ai_company.engine.checkpoint.callback_factory import make_checkpoint_callback
from ai_company.engine.checkpoint.models import (
    Checkpoint,
    CheckpointConfig,
    Heartbeat,
)
from ai_company.engine.checkpoint.resume import (
    cleanup_checkpoint_artifacts,
    deserialize_and_reconcile,
    make_loop_with_callback,
)
from ai_company.engine.checkpoint.strategy import CheckpointRecoveryStrategy

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
