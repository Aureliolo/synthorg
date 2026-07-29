"""Memory feature state slice.

Holds the shared memory backend (used by admin delete endpoints + MCP
delete), the fine-tune orchestrator, and the memory facade service.
All ``None`` until wired (the backend during the training-service
auto-wire path); the memory admin controller and MCP handlers raise
503 on a ``None`` field.
"""

from pydantic import ConfigDict

from synthorg._core.features import BaseFeatureStateSlice, require_service
from synthorg.api.state_slices import AppStateSliceMixin
from synthorg.memory.consolidation.cycle_scheduler import (
    MemoryConsolidationScheduler,
)
from synthorg.memory.embedding.fine_tune_orchestrator import (
    FineTuneOrchestrator,
)
from synthorg.memory.org.protocol import OrgMemoryBackend
from synthorg.memory.protocol import MemoryBackend
from synthorg.memory.service import MemoryService


class MemoryStateSlice(BaseFeatureStateSlice):
    """Application-state slice owned by the memory feature."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    backend: MemoryBackend | None = None
    embedder_ref: str | None = None
    fine_tune_orchestrator: FineTuneOrchestrator | None = None
    service: MemoryService | None = None
    org_memory_backend: OrgMemoryBackend | None = None
    consolidation_scheduler: MemoryConsolidationScheduler | None = None


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


def memory_backend_or_none(
    app_state: AppStateSliceMixin,
) -> MemoryBackend | None:
    """Resolve the shared memory backend from its slice, or ``None``.

    Returns ``None`` (never raises) so optional consumers -- e.g.
    ``SelfImprovementService`` outcome learning, which accepts
    ``memory_backend=None`` -- can wire up on deployments with no memory
    backend instead of failing the whole path on a hard ``require_service``.

    Returns:
        The wired :class:`MemoryBackend`, or ``None`` when unwired.
    """
    return app_state.slice(MemoryStateSlice).backend


def org_memory_backend_of(
    app_state: AppStateSliceMixin,
) -> OrgMemoryBackend | None:
    """Resolve the org-memory backend from its slice, or ``None``.

    Returns ``None`` (never raises) so optional consumers -- HR snapshot
    strategies, the ontology admin sync -- degrade gracefully when the
    org-memory substrate is disabled or persistence is absent.

    Returns:
        The wired :class:`OrgMemoryBackend`, or ``None`` when unwired.
    """
    return app_state.slice(MemoryStateSlice).org_memory_backend
