"""Engine recovery configuration.

Discriminator for :func:`synthorg.engine.recovery_factory.build_recovery_strategy`.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from synthorg.engine.checkpoint.models import CheckpointConfig


class RecoveryStrategyType(StrEnum):
    """Which crash recovery strategy ``AgentEngine`` uses.

    Attributes:
        FAIL_REASSIGN: Transition the failed execution to FAILED and
            report reassignment eligibility based on ``retry_count``.
            The default; matches the previous hardcoded behaviour.
        CHECKPOINT: Resume from the most recent persisted checkpoint
            when available; falls back to FAIL_REASSIGN otherwise.
            Requires a connected :class:`CheckpointRepository`.
    """

    FAIL_REASSIGN = "fail_reassign"
    CHECKPOINT = "checkpoint"


class EngineRecoveryConfig(BaseModel):
    """Configuration block for engine recovery strategy selection.

    The CHECKPOINT strategy additionally requires a
    ``CheckpointRepository`` (and an optional ``HeartbeatRepository``)
    supplied by the active ``PersistenceBackend`` lifecycle; the
    operator-tunable ``CheckpointConfig`` lives here on
    :attr:`checkpoint`. Selecting CHECKPOINT here without a connected
    backend raises :class:`synthorg.engine.errors.RecoveryConfigError`
    at the moment
    :func:`synthorg.engine.recovery_factory.build_recovery_strategy`
    runs at boot, not at first recovery. The repository dependency
    cannot be expressed as a Pydantic validator because the
    collaborator lives outside this config model; treat the factory
    call as the parse-time boundary for that invariant.

    Attributes:
        strategy: Which strategy to instantiate at boot. Defaults to
            ``FAIL_REASSIGN`` so existing deployments keep their
            current behaviour without explicit opt-in.
        checkpoint: Tuning for the CHECKPOINT strategy
            (``persist_every_n_turns``, ``heartbeat_interval_seconds``,
            ``max_resume_attempts``). Ignored by FAIL_REASSIGN.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    strategy: RecoveryStrategyType = Field(
        default=RecoveryStrategyType.FAIL_REASSIGN,
        description="Recovery strategy discriminator",
    )
    checkpoint: CheckpointConfig = Field(
        default_factory=CheckpointConfig,
        description="Checkpoint-strategy tuning (ignored by fail-reassign)",
    )
