"""Engine recovery configuration.

Discriminator for :func:`synthorg.engine.recovery_factory.build_recovery_strategy`.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


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

    Attributes:
        strategy: Which strategy to instantiate at boot. Defaults to
            ``FAIL_REASSIGN`` so existing deployments keep their
            current behaviour without explicit opt-in.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    strategy: RecoveryStrategyType = Field(
        default=RecoveryStrategyType.FAIL_REASSIGN,
        description="Recovery strategy discriminator",
    )
