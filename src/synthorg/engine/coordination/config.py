"""Coordination configuration."""

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr


class CoordinationConfig(BaseModel):
    """Configuration for a multi-agent coordination run.

    Attributes:
        max_concurrency_per_wave: Max parallel agents per wave
            (``None`` = unlimited).
        fail_fast: Stop on first wave failure instead of continuing.
        enable_workspace_isolation: Create isolated workspaces for
            multi-agent execution.
        base_branch: Git branch to use for workspace isolation.
        max_delegation_rounds: Soft cap on delegation rounds
            (default 3, ge=1, le=20). Warning emitted at this
            limit; hard abort at 2x the value.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    max_concurrency_per_wave: int | None = Field(
        default=5,
        ge=1,
        description="Max parallel agents per wave (default 5; None disables the cap)",
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
    max_delegation_rounds: int = Field(
        default=3,
        ge=1,
        le=20,
        description=(
            "Soft cap on delegation rounds. Warning emitted at this "
            "limit; hard abort at 2x (default: abort at 6)."
        ),
    )
