# module-kind: declarative
"""Loop prevention configuration (see Communication design page).

Lives beside the guards that read it rather than in the communication
config module: ``rate_limit``, ``circuit_breaker`` and ``guard`` are the
only consumers.
"""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RateLimitConfig(BaseModel):
    """Per-pair message rate limit configuration.

    Maps to the Communication design page ``rate_limit``.

    Attributes:
        max_per_pair_per_minute: Maximum messages per agent pair per minute.
        burst_allowance: Extra burst capacity above the rate limit.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    max_per_pair_per_minute: int = Field(
        default=10,
        gt=0,
        description="Max messages per agent pair per minute",
    )
    burst_allowance: int = Field(
        default=3,
        ge=0,
        description="Extra burst capacity",
    )


class CircuitBreakerConfig(BaseModel):
    """Circuit breaker configuration for agent-pair communication.

    Maps to the Communication design page ``circuit_breaker``.

    Attributes:
        bounce_threshold: Bounce count before the circuit opens.
        cooldown_seconds: Seconds to wait before retrying after trip.
        max_cooldown_seconds: Ceiling on the exponential backoff.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    bounce_threshold: int = Field(
        default=3,
        gt=0,
        description="Bounce count before circuit opens",
    )
    cooldown_seconds: int = Field(
        default=300,
        gt=0,
        description="Cooldown period in seconds",
    )
    max_cooldown_seconds: int = Field(
        default=3600,
        gt=0,
        description="Maximum cooldown period in seconds (caps exponential backoff)",
    )

    @model_validator(mode="after")
    def _validate_cooldown_bounds(self) -> Self:
        """Ensure the exponential backoff cap is not below the base cooldown.

        Returns:
            The validated config.

        Raises:
            ValueError: If ``max_cooldown_seconds`` is below
                ``cooldown_seconds``.
        """
        if self.max_cooldown_seconds < self.cooldown_seconds:
            msg = "max_cooldown_seconds must be >= cooldown_seconds"
            raise ValueError(msg)
        return self


class LoopPreventionConfig(BaseModel):
    """Loop prevention safeguards.

    Maps to the Communication design page.  ``ancestry_tracking`` is always on
    and cannot be disabled.

    Attributes:
        max_delegation_depth: Hard limit on delegation chain length.
        rate_limit: Per-pair rate limit settings.
        dedup_window_seconds: Deduplication window in seconds.
        circuit_breaker: Circuit breaker settings.
        ancestry_tracking: Must always be ``True``.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    max_delegation_depth: int = Field(
        default=5,
        gt=0,
        description="Hard limit on delegation chain length",
    )
    rate_limit: RateLimitConfig = Field(
        default_factory=RateLimitConfig,
        description="Per-pair rate limit settings",
    )
    dedup_window_seconds: int = Field(
        default=60,
        gt=0,
        description="Deduplication window in seconds",
    )
    circuit_breaker: CircuitBreakerConfig = Field(
        default_factory=CircuitBreakerConfig,
        description="Circuit breaker settings",
    )
    ancestry_tracking: Literal[True] = Field(
        default=True,
        description="Task ancestry tracking (always on, not configurable)",
    )
