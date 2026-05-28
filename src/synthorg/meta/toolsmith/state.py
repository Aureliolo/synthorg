"""Toolsmith feature state slice.

Holds the self-extending toolkit runtime service. ``None`` until wired at
boot (gated on ``tools.tool_creation_enabled`` plus a provider).
"""

from pydantic import ConfigDict

from synthorg._core.features import BaseFeatureStateSlice
from synthorg.meta.toolsmith.service import ToolsmithService


class ToolsmithStateSlice(BaseFeatureStateSlice):
    """Application-state slice owned by the toolsmith feature."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    service: ToolsmithService | None = None
