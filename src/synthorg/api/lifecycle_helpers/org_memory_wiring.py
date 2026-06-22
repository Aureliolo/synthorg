# module-kind: code
"""Boot wiring for the hybrid org-memory backend.

Builds the persistence-backed :class:`HybridPromptRetrievalBackend` and
publishes it on :class:`MemoryStateSlice` so HR promotion / offboarding
snapshots, the ontology admin sync, and the knowledge-architect tools
resolve one live backend instead of receiving ``None``.
"""

from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP

logger = get_logger(__name__)


async def wire_org_memory_backend(app_state: AppState) -> None:
    """Wire the hybrid org-memory backend once persistence is connected.

    Best-effort + idempotent. The backend wraps the shared
    ``persistence.org_facts`` store (its connection is owned by the main
    backend) plus the operator's core policies. Gated only on connected
    persistence -- the org-fact store is persistence-backed and independent
    of the vector memory backend. A build/connect failure logs a warning
    and leaves the slice unset (consumers degrade to ``None``) rather than
    poisoning startup.
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
    try:
        backend = build_org_memory_backend(
            app_state.config.org_memory,
            persistence_of(app_state).org_facts,
        )
        await backend.connect()
    except Exception as exc:  # noqa: BLE001 -- best-effort startup wiring
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            service="org_memory_backend",
            note="wiring failed; consumers degrade to None",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return
    app_state.wire(MemoryStateSlice, org_memory_backend=backend)
    logger.info(API_APP_STARTUP, service="org_memory_backend", note="wired")
