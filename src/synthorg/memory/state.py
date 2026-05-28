"""Memory feature state slice.

Holds the shared memory backend (used by admin delete endpoints + MCP
delete), the fine-tune orchestrator, and the memory facade service.
All ``None`` until wired (the backend during the training-service
auto-wire path); the memory admin controller and MCP handlers raise
503 on a ``None`` field.
"""

from typing import TYPE_CHECKING

from pydantic import ConfigDict

from synthorg._core.features import BaseFeatureStateSlice, require_service
from synthorg.memory.embedding.fine_tune_orchestrator import (
    FineTuneOrchestrator,
)
from synthorg.memory.protocol import MemoryBackend
from synthorg.memory.service import MemoryService

if TYPE_CHECKING:
    from synthorg.api.state_slices import AppStateSliceMixin


class MemoryStateSlice(BaseFeatureStateSlice):
    """Application-state slice owned by the memory feature."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    backend: MemoryBackend | None = None
    fine_tune_orchestrator: FineTuneOrchestrator | None = None
    service: MemoryService | None = None


def memory_service_of(app_state: AppStateSliceMixin) -> MemoryService:
    """Resolve the memory service from its slice, or raise 503.

    Returns:
        The wired memory service.
    """
    return require_service(app_state.slice(MemoryStateSlice).service, "Memory Service")


def memory_backend_of(app_state: AppStateSliceMixin) -> MemoryBackend:
    """Resolve the shared memory backend from its slice, or raise 503.

    Returns:
        The wired memory backend.
    """
    return require_service(app_state.slice(MemoryStateSlice).backend, "Memory Backend")
