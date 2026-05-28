"""Org memory configuration models.

Frozen Pydantic models for organizational memory behaviour settings.
"""

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr
from synthorg.memory.org.access_control import WriteAccessConfig


class ExtendedStoreConfig(BaseModel):
    """Configuration for the extended org facts store.

    Attributes:
        max_retrieved_per_query: Maximum facts to retrieve per query.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    max_retrieved_per_query: int = Field(
        default=5,
        ge=1,
        le=50,
        description="Maximum facts to retrieve per query",
    )


class OrgMemoryConfig(BaseModel):
    """Top-level organizational memory configuration.

    Attributes:
        core_policies: Core policy texts injected into system prompts.
        extended_store: Extended facts store configuration.
        write_access: Write access control configuration.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    core_policies: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Core policy texts injected into system prompts",
    )
    extended_store: ExtendedStoreConfig = Field(
        default_factory=ExtendedStoreConfig,
        description="Extended facts store configuration",
    )
    write_access: WriteAccessConfig = Field(
        default_factory=WriteAccessConfig,
        description="Write access control configuration",
    )
