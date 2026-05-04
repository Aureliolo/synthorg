"""Configuration for identity version stores."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class IdentityStoreConfig(BaseModel):
    """Configuration for the identity version store.

    Attributes:
        type: Store implementation to use.
        max_versions_per_agent: Optional cap on stored versions
            per agent (None = unlimited).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    type: Literal["append_only", "copy_on_write"] = Field(
        default="append_only",
        description="Store type: append_only (audit) or copy_on_write (rollback)",
    )
    max_versions_per_agent: int | None = Field(
        default=None,
        ge=1,
        description="Maximum stored versions per agent (None = unlimited)",
    )
