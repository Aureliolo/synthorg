# module-kind: code
"""Boot wiring for the hybrid org-memory backend.

Extracted from ``feature_wiring`` to keep that orchestrator under its
module-size budget. Builds the persistence-backed
:class:`HybridPromptRetrievalBackend` and publishes it on
:class:`MemoryStateSlice` so HR promotion / offboarding snapshots, the
ontology admin sync, and the knowledge-architect tools resolve one live
backend instead of receiving ``None``.
"""

from synthorg.api.state import AppState
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_APP_STARTUP

logger = get_logger(__name__)


async def wire_org_memory_backend(app_state: AppState) -> None:
    """Wire the hybrid org-memory backend once persistence is connected.

    Best-effort + idempotent. The backend wraps the shared
    ``persistence.org_facts`` store (its connection is owned by the main
    backend) plus the operator's core policies. Gated only on connected
    persistence -- the org-fact store is persistence-backed and independent
    of the vector memory backend.
    """
    from synthorg.memory.org.factory import (  # noqa: PLC0415
        build_org_memory_backend,
    )
    from synthorg.memory.state import MemoryStateSlice  # noqa: PLC0415
    from synthorg.persistence.state import (  # noqa: PLC0415
        PersistenceStateSlice,
        persistence_of,
    )

    if app_state.slice(MemoryStateSlice).org_memory_backend is not None:
        return
    if app_state.slice(PersistenceStateSlice).backend is None:
        return
    backend = build_org_memory_backend(
        app_state.config.org_memory,
        persistence_of(app_state).org_facts,
    )
    await backend.connect()
    app_state.wire(MemoryStateSlice, org_memory_backend=backend)
    logger.info(API_APP_STARTUP, service="org_memory_backend", note="wired")
