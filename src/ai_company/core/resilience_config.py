"""Resilience configuration models (retry + rate limiting).

Defined in ``core/`` to avoid circular imports between ``config.schema``
and ``providers.resilience``.  Both modules import from here.
"""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_company.observability import get_logger
from ai_company.observability.events.config import CONFIG_VALIDATION_FAILED

logger = get_logger(__name__)


class RetryConfig(BaseModel):
    """Configuration for automatic retry of transient provider errors.

    Attributes:
        max_retries: Maximum number of retry attempts (0 disables retries).
        base_delay: Initial delay in seconds before the first retry.
        max_delay: Upper bound on computed delay in seconds.
        exponential_base: Multiplier for exponential backoff.
        jitter: Whether to add random jitter to delay.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    max_retries: int = Field(
        default=3,
        ge=0,
        le=10,
        description="Maximum number of retry attempts (0 disables retries)",
    )
    base_delay: float = Field(
        default=1.0,
        gt=0.0,
        description="Initial delay in seconds before the first retry",
    )
    max_delay: float = Field(
        default=60.0,
        gt=0.0,
        description="Upper bound on computed delay in seconds",
    )
    exponential_base: float = Field(
        default=2.0,
        gt=1.0,
        description="Multiplier for exponential backoff",
    )
    jitter: bool = Field(
        default=True,
        description="Whether to add random jitter to delay",
    )

    @model_validator(mode="after")
    def _validate_delay_ordering(self) -> Self:
        """Ensure base_delay does not exceed max_delay."""
        if self.base_delay > self.max_delay:
            msg = (
                f"base_delay ({self.base_delay}) must be"
                f" <= max_delay ({self.max_delay})"
            )
            logger.warning(
                CONFIG_VALIDATION_FAILED,
                model="RetryConfig",
                field="base_delay/max_delay",
                base_delay=self.base_delay,
                max_delay=self.max_delay,
                reason=msg,
            )
            raise ValueError(msg)
        return self


class RateLimiterConfig(BaseModel):
    """Configuration for client-side rate limiting.

    Attributes:
        max_requests_per_minute: Maximum requests per minute
            (0 means unlimited).
        max_concurrent: Maximum concurrent in-flight requests
            (0 means unlimited).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    max_requests_per_minute: int = Field(
        default=0,
        ge=0,
        description="Maximum requests per minute (0 = unlimited)",
    )
    max_concurrent: int = Field(
        default=0,
        ge=0,
        description="Maximum concurrent in-flight requests (0 = unlimited)",
    )
