"""Research feature state slice.

Holds the multi-source research subsystem service and its per-task agent
tool factory. Both are ``None`` until wired at boot (research mode enabled
plus a provider + model); the research MCP handlers raise 503 on a ``None``
field.
"""

from typing import TYPE_CHECKING

from pydantic import ConfigDict

from synthorg._core.features import BaseFeatureStateSlice, require_service
from synthorg.research.service import ResearchService
from synthorg.research.tool_factory import (
    ResearchToolFactory,
)

if TYPE_CHECKING:
    # ``api.state_slices`` is kept under TYPE_CHECKING: the ``api`` layer wires
    # this feature slice, so a runtime import back up into ``api`` closes a
    # circular import. PEP 649 makes the bare annotation below safe at load.
    from synthorg.api.state_slices import AppStateSliceMixin


class ResearchStateSlice(BaseFeatureStateSlice):
    """Application-state slice owned by the research feature."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    service: ResearchService | None = None
    tool_factory: ResearchToolFactory | None = None


def research_service_of(app_state: AppStateSliceMixin) -> ResearchService:
    """Resolve the research service from its slice, or raise 503.

    Returns:
        The wired research service.
    """
    return require_service(
        app_state.slice(ResearchStateSlice).service, "Research Service"
    )
