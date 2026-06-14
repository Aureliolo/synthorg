"""Living-documentation feature state slice.

Holds the docs service, the project-aware RAG memory facade, and the
per-task agent doc-tool factory. All ``None`` until wired at boot (gated on
persistence + a project workspace + memory backend); the docs controllers
and MCP handlers raise 503 on a ``None`` service.
"""

from typing import TYPE_CHECKING

from pydantic import ConfigDict

from synthorg._core.features import BaseFeatureStateSlice, require_service
from synthorg.docs_engine.retrieval_facade import (
    ProjectAwareMemoryFacade,
)
from synthorg.docs_engine.service import DocsService
from synthorg.docs_engine.tool_factory import DocsToolFactory

if TYPE_CHECKING:
    from synthorg.api.state_slices import AppStateSliceMixin


class DocsStateSlice(BaseFeatureStateSlice):
    """Application-state slice owned by the living-documentation feature."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    service: DocsService | None = None
    memory_facade: ProjectAwareMemoryFacade | None = None
    tool_factory: DocsToolFactory | None = None


def docs_service_of(app_state: AppStateSliceMixin) -> DocsService:
    """Resolve the docs service from its slice, or raise 503.

    Returns:
        The wired docs service.
    """
    return require_service(app_state.slice(DocsStateSlice).service, "Docs Service")
