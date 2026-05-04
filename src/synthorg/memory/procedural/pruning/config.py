"""Configuration for memory pruning strategies."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PruningConfig(BaseModel):
    """Configuration for selecting and configuring pruning strategies.

    Attributes:
        type: Strategy type ("ttl", "pareto", or "hybrid").
        max_age_days: Maximum entry age for TTL strategy (default 90).
        max_entries: Maximum entries to keep for Pareto strategy
            (default 100).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    type: Literal["ttl", "pareto", "hybrid"] = Field(
        default="ttl",
        description="Pruning strategy type",
    )
    max_age_days: int = Field(
        default=90,
        ge=1,
        description="Maximum age in days for TTL strategy",
    )
    max_entries: int = Field(
        default=100,
        ge=1,
        description="Maximum entries to keep for Pareto strategy",
    )
