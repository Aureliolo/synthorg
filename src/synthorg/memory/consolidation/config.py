"""Memory consolidation configuration models.

Frozen Pydantic models for consolidation interval, retention,
and archival settings.
"""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.enums import ConsolidationInterval
from synthorg.memory.consolidation.models import RetentionRule  # noqa: TC001


class RetentionConfig(BaseModel):
    """Per-category retention configuration.

    Retention rules apply uniformly across all agents.  Per-agent
    retention overrides (e.g. longer retention for senior agents) are
    not yet supported and are a known scope gap for a future iteration.

    Attributes:
        rules: Per-category retention rules (unique categories).
        default_retention_days: Default retention in days
            (``None`` = keep forever).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    rules: tuple[RetentionRule, ...] = Field(
        default=(),
        description="Per-category retention rules",
    )
    default_retention_days: int | None = Field(
        default=None,
        ge=1,
        description="Default retention in days (None = forever)",
    )

    @model_validator(mode="after")
    def _validate_unique_categories(self) -> Self:
        """Ensure each category appears at most once in rules."""
        categories = [rule.category for rule in self.rules]
        if len(categories) != len(set(categories)):
            dupes = sorted(c.value for c in categories if categories.count(c) > 1)
            msg = f"Duplicate retention categories: {dupes}"
            raise ValueError(msg)
        return self


class ArchivalConfig(BaseModel):
    """Archival configuration.

    Attributes:
        enabled: Whether archival is enabled.
        age_threshold_days: Minimum age in days before archival.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    enabled: bool = Field(
        default=False,
        description="Whether archival is enabled",
    )
    age_threshold_days: int = Field(
        default=90,
        ge=1,
        description="Minimum age in days before archival",
    )


class ConsolidationConfig(BaseModel):
    """Top-level memory consolidation configuration.

    Attributes:
        interval: How often to run consolidation.
        max_memories_per_agent: Upper bound on memories per agent.
        retention: Per-category retention settings.
        archival: Archival settings.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    interval: ConsolidationInterval = Field(
        default=ConsolidationInterval.DAILY,
        description="How often to run consolidation",
    )
    max_memories_per_agent: int = Field(
        default=10_000,
        ge=1,
        description="Upper bound on memories per agent",
    )
    retention: RetentionConfig = Field(
        default_factory=RetentionConfig,
        description="Per-category retention settings",
    )
    archival: ArchivalConfig = Field(
        default_factory=ArchivalConfig,
        description="Archival settings",
    )
