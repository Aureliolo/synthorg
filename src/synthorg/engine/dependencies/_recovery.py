# module-kind: declarative
"""What happens when a run does not finish, and what proves that it did."""

from dataclasses import dataclass

from synthorg.engine.artifacts.baseline_scope import RunBaselineProbe
from synthorg.engine.checkpoint.wiring import CheckpointWiring
from synthorg.engine.recovery import RecoveryStrategy


@dataclass(frozen=True, slots=True, kw_only=True)
class EngineRecovery:
    """What a run falls back on, and what it is measured against.

    Attributes:
        recovery_strategy: What happens to a run that failed. Carries no
            default because a module-level shared strategy is exactly the
            invisible wiring this package removes.
        run_probe: Captures how the workspace looked before the run, so
            "did this run deliver" is a question about the run rather
            than about the workspace. ``None`` leaves the delivery check
            on the weaker evidence it has, which never fails a run that
            delivered.
        checkpointing: Both repositories and their config, or ``None``
            for a run that does not survive its process.
    """

    recovery_strategy: RecoveryStrategy
    run_probe: RunBaselineProbe | None
    checkpointing: CheckpointWiring | None


__all__ = ["CheckpointWiring", "EngineRecovery"]
