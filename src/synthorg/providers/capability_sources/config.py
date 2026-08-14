# module-kind: code
"""What the operator decided about each capability source.

An absent configuration means every shipped source is enabled on its
registry URL. That is a deliberate default rather than an empty one: the
grading this feeds exists because the proxy it replaces was wrong, so
shipping with it switched off would leave the defect in place for anybody
who never found the setting.

An entry names a source and may disable it or point it somewhere else. A
URL an operator supplies is validated against the SSRF allowlist before
anything fetches it, and an entry naming a source that is not registered
is dropped with a warning rather than guessed at.
"""

from collections.abc import Mapping
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr

CAPABILITY_SOURCE_CONFIG_SCHEMA_VERSION: Final[int] = 1


class CapabilitySourceSetting(BaseModel):
    """One source's operator configuration.

    Attributes:
        label: Which registered source this configures.
        enabled: Whether it contributes evidence.
        feed_url: Where to fetch from. Empty means the registry default;
            anything else is validated against the SSRF allowlist first.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    label: NotBlankStr = Field(description="Registered source label")
    enabled: bool = Field(default=True, description="Whether it contributes")
    feed_url: str = Field(
        default="",
        description="Operator feed URL; empty uses the registry default",
    )


class CapabilitySourceConfig(BaseModel):
    """Versioned envelope for the persisted source configuration."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    schema_version: int = Field(
        default=CAPABILITY_SOURCE_CONFIG_SCHEMA_VERSION,
        description="Schema version of the persisted blob",
    )
    sources: tuple[CapabilitySourceSetting, ...] = Field(
        default=(),
        description="Per-source operator configuration",
    )

    @model_validator(mode="after")
    def _one_entry_per_source(self) -> CapabilitySourceConfig:
        """Reject two entries configuring the same source.

        Returns:
            The validated envelope.

        Raises:
            ValueError: When a label appears twice, which would make the
                effective setting depend on tuple order.
        """
        labels = [str(s.label) for s in self.sources]
        if len(set(labels)) != len(labels):
            dupes = sorted({label for label in labels if labels.count(label) > 1})
            msg = f"duplicate capability source entries for {dupes}"
            raise ValueError(msg)
        return self

    def by_label(self) -> Mapping[str, CapabilitySourceSetting]:
        """Index the entries by source label.

        Returns:
            The entries keyed by label.
        """
        return {str(s.label): s for s in self.sources}


__all__ = [
    "CAPABILITY_SOURCE_CONFIG_SCHEMA_VERSION",
    "CapabilitySourceConfig",
    "CapabilitySourceSetting",
]
