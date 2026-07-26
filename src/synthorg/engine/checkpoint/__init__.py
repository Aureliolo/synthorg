"""Checkpoint recovery for agent crash recovery.

Persists ``AgentContext`` snapshots at configurable turn intervals
and resumes from the last checkpoint on crash, preserving progress.

``resume`` and ``strategy`` are deliberately not re-exported here: both
reach into the concrete loop classes, so pulling either into this package
init makes every importer of the leaf ``CheckpointCallback`` alias drag in
the whole loop tier, and closes a cold-import cycle back through it. Import
them directly from ``synthorg.engine.checkpoint.resume`` and
``synthorg.engine.checkpoint.strategy``, which is what every caller does.
"""

from synthorg.engine.checkpoint.callback import CheckpointCallback
from synthorg.engine.checkpoint.callback_factory import make_checkpoint_callback
from synthorg.engine.checkpoint.models import (
    Checkpoint,
    CheckpointConfig,
    Heartbeat,
)

__all__ = [
    "Checkpoint",
    "CheckpointCallback",
    "CheckpointConfig",
    "Heartbeat",
    "make_checkpoint_callback",
]
