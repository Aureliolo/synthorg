"""Knowledge feature state slice.

Holds the knowledge + provenance substrate service and its per-task agent
tool factory. Both are ``None`` until wired at boot (knowledge needs a
connected persistence backend and a memory backend); the knowledge
controllers and MCP handlers raise 503 on a ``None`` field.
"""

from typing import TYPE_CHECKING

from pydantic import ConfigDict

from synthorg._core.features import BaseFeatureStateSlice, require_service
from synthorg.knowledge.service import KnowledgeService
from synthorg.knowledge.tool_factory import (
    KnowledgeToolFactory,
)

if TYPE_CHECKING:
    from synthorg.api.state_slices import AppStateSliceMixin


class KnowledgeStateSlice(BaseFeatureStateSlice):
    """Application-state slice owned by the knowledge feature."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    service: KnowledgeService | None = None
    tool_factory: KnowledgeToolFactory | None = None


def knowledge_service_of(app_state: AppStateSliceMixin) -> KnowledgeService:
    """Resolve the knowledge service from its slice, or raise 503.

    Returns:
        The wired knowledge service.
    """
    return require_service(
        app_state.slice(KnowledgeStateSlice).service, "Knowledge Service"
    )
