"""Configuration models for analytics tools."""

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr


class AnalyticsToolsConfig(BaseModel):
    """Top-level configuration for analytics tools.

    Attributes:
        query_timeout: Maximum query execution time in seconds.
        max_rows: Maximum rows returned from aggregation queries.
        allowed_metrics: Optional whitelist of metric names agents
            can query.  ``None`` means all metrics are accessible.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    query_timeout: float = Field(
        default=60.0,
        gt=0,
        le=300.0,
        description="Query timeout (seconds)",
    )
    max_rows: int = Field(
        default=10_000,
        gt=0,
        le=100_000,
        description="Maximum rows in aggregation results",
    )
    allowed_metrics: frozenset[NotBlankStr] | None = Field(
        default=None,
        description="Metric whitelist (None = all allowed)",
    )
