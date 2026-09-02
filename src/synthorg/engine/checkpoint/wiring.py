# module-kind: declarative
"""The two repositories and the config that make checkpointing work.

They travel together because the invariant is not a preference: a
checkpoint nothing declares stale is a resume point that can be handed to
two runners at once. Expressed as three optional parameters the invalid
combination is representable, and every consumer then has to notice it at
runtime, each in its own words or (as one did) by silently building no
callback. Expressed as one type it cannot be built at all.
"""

from dataclasses import dataclass

from synthorg.engine.checkpoint.models import CheckpointConfig
from synthorg.persistence.checkpoint_protocol import (
    CheckpointRepository,
    HeartbeatRepository,
)


@dataclass(frozen=True, slots=True, kw_only=True)
class CheckpointWiring:
    """A conversation on disk, with the liveness half that makes it safe.

    Attributes:
        checkpoint_repo: Where a session's conversation is persisted,
            every turn.
        heartbeat_repo: What declares a checkpoint stale, so a resume
            point is handed to one runner rather than two.
        config: How often a checkpoint is written and how many resume
            attempts a run gets before its fallback takes over.
    """

    checkpoint_repo: CheckpointRepository
    heartbeat_repo: HeartbeatRepository
    config: CheckpointConfig


__all__ = ["CheckpointWiring"]
