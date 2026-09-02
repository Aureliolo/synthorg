"""Sandbox lifecycle configuration model."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class LifecycleStrategy(StrEnum):
    """Which lifecycle a sandbox backend reuses containers under.

    A closed vocabulary rather than three string constants, so the factory,
    the owner-key resolution, the execution-service release and the
    reclamation sweep each dispatch on it exhaustively: a member added here
    is one every ``match`` over it has to name.
    """

    PER_AGENT = "per-agent"
    PER_TASK = "per-task"
    PER_CALL = "per-call"


class SandboxLifecycleConfig(BaseModel):
    """Configuration for sandbox container lifecycle strategy.

    Attributes:
        strategy: Which lifecycle strategy to use.
        grace_period_seconds: Seconds to keep a container alive after
            ``release()`` before destroying it (per-agent only).
        health_check_interval_seconds: Seconds between container health
            checks for long-lived strategies.
        max_idle_seconds: Force-destroy containers idle beyond this
            threshold.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    strategy: LifecycleStrategy = LifecycleStrategy.PER_AGENT
    grace_period_seconds: float = Field(default=30.0, ge=0.0)
    health_check_interval_seconds: float = Field(default=10.0, ge=1.0)
    max_idle_seconds: float = Field(default=300.0, ge=0.0)
