"""Coordination configuration."""

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr  # noqa: TC001


class CoordinationConfig(BaseModel):
    """Configuration for a multi-agent coordination run.

    Attributes:
        max_concurrency_per_wave: Max parallel agents per wave
            (``None`` = unlimited).
        fail_fast: Stop on first wave failure instead of continuing.
        enable_workspace_isolation: Create isolated workspaces for
            multi-agent execution.
        base_branch: Git branch to use for workspace isolation.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_concurrency_per_wave: int | None = Field(
        default=None,
        ge=1,
        description="Max parallel agents per wave (None = unlimited)",
    )
    fail_fast: bool = Field(
        default=False,
        description="Stop on first wave failure",
    )
    enable_workspace_isolation: bool = Field(
        default=True,
        description="Create isolated workspaces for multi-agent execution",
    )
    base_branch: NotBlankStr = Field(
        default="main",
        description="Git branch for workspace isolation",
    )
