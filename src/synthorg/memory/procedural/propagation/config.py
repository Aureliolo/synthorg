"""Configuration for memory propagation strategies."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PropagationConfig(BaseModel):
    """Configuration for selecting and configuring propagation strategies.

    Attributes:
        type: Strategy type ("none", "role_scoped", or "department_scoped").
        max_propagation_targets: Maximum agents to propagate to
            (default 10).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    type: Literal["none", "role_scoped", "department_scoped"] = Field(
        default="none",
        description="Propagation strategy type",
    )
    max_propagation_targets: int = Field(
        default=10,
        ge=1,
        description="Maximum agents to propagate to",
    )
