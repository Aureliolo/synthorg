"""Project-brain feature state slice.

Holds the brain service and the per-task agent brain-tool factory. Both are
``None`` until wired at boot (gated on persistence + a project workspace +
memory backend); the brain controllers and MCP handlers raise 503 on a ``None``
service. Brain state surfaces transparently through the docs engine's shared
retrieval facade, so no facade is held here.
"""

from typing import TYPE_CHECKING

from pydantic import ConfigDict

from synthorg._core.features import BaseFeatureStateSlice, require_service
from synthorg.project_brain.service import ProjectBrainService
from synthorg.project_brain.tool_factory import ProjectBrainToolFactory

if TYPE_CHECKING:
    from synthorg.api.state_slices import AppStateSliceMixin


class ProjectBrainStateSlice(BaseFeatureStateSlice):
    """Application-state slice owned by the project-brain feature."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    service: ProjectBrainService | None = None
    tool_factory: ProjectBrainToolFactory | None = None


def project_brain_service_of(app_state: AppStateSliceMixin) -> ProjectBrainService:
    """Resolve the project-brain service from its slice, or raise 503.

    Returns:
        The wired project-brain service.
    """
    return require_service(
        app_state.slice(ProjectBrainStateSlice).service,
        "Project Brain Service",
    )
