"""Sandbox lifecycle configuration model."""

from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

# Canonical strategy discriminators.  Single source of truth so the
# string never drifts across the factory, the Docker backend's owner
# resolution, and the execution-service release boundary.
STRATEGY_PER_AGENT: Final[str] = "per-agent"
STRATEGY_PER_TASK: Final[str] = "per-task"
STRATEGY_PER_CALL: Final[str] = "per-call"


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

    strategy: Literal["per-agent", "per-task", "per-call"] = "per-agent"
    grace_period_seconds: float = Field(default=30.0, ge=0.0)
    health_check_interval_seconds: float = Field(default=10.0, ge=1.0)
    max_idle_seconds: float = Field(default=300.0, ge=0.0)
