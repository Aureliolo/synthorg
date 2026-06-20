# module-kind: orchestrator
"""On-startup wiring for the HR pruning service.

The durable ``pruning_requests`` repository ships, and
``PruningService.__init__`` accepts a ``request_repo``, but the service was
never constructed at boot, leaving the repo dead and ``rehydrate_pending()``
unrun. This hook constructs :class:`PruningService` over the wired registry /
tracker / approval store, injects the per-backend durable
:class:`PruningRequestRepository`, and rehydrates any pending requests so a
restart recovers in-flight prune approvals.

Best-effort + idempotent + persistence-gated: an already-wired service
short-circuits, and a missing collaborator (no persistence / registry /
tracker / approval store) leaves the service absent rather than poisoning
startup.
"""

from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.hr.state import HrStateSlice
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.persistence.pruning_request_protocol import PruningRequestRepository

logger = get_logger(__name__)


async def wire_pruning(app_state: AppState) -> None:
    """Construct the pruning service + durable repo at boot and rehydrate.

    Args:
        app_state: The application state holding the collaborator slices.
    """
    from synthorg.approval.state import ApprovalStateSlice  # noqa: PLC0415
    from synthorg.persistence.state import PersistenceStateSlice  # noqa: PLC0415

    hr = app_state.slice(HrStateSlice)
    if hr.pruning_service is not None:
        return
    if app_state.slice(PersistenceStateSlice).backend is None:
        logger.info(
            API_APP_STARTUP,
            service="pruning",
            note="persistence absent; pruning service unwired",
        )
        return
    approval_store = app_state.slice(ApprovalStateSlice).store
    if (
        hr.agent_registry is None
        or hr.performance_tracker is None
        or (approval_store is None)
    ):
        logger.info(
            API_APP_STARTUP,
            service="pruning",
            note="registry / tracker / approval store absent; pruning unwired",
        )
        return
    try:
        await _wire(
            app_state,
            registry=hr.agent_registry,
            tracker=hr.performance_tracker,
            approval_store=approval_store,
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            service="pruning",
            note="pruning wiring failed; service stays unwired",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


async def _wire(
    app_state: AppState,
    *,
    registry: object,
    tracker: object,
    approval_store: object,
) -> None:
    from synthorg.hr.offboarding_service import OffboardingService  # noqa: PLC0415
    from synthorg.hr.performance.tracker import PerformanceTracker  # noqa: PLC0415
    from synthorg.hr.pruning.service import PruningService  # noqa: PLC0415
    from synthorg.hr.registry import AgentRegistryService  # noqa: PLC0415

    assert isinstance(registry, AgentRegistryService)  # noqa: S101
    assert isinstance(tracker, PerformanceTracker)  # noqa: S101
    repo = _build_pruning_request_repo(app_state)
    offboarding = OffboardingService(registry=registry)
    service = PruningService(
        policies=(),
        registry=registry,
        tracker=tracker,
        approval_store=approval_store,  # type: ignore[arg-type]
        offboarding_service=offboarding,
        request_repo=repo,
    )
    await service.rehydrate_pending()
    app_state.wire(HrStateSlice, pruning_service=service)
    logger.info(API_APP_STARTUP, service="pruning", note="wired (durable)")


def _build_pruning_request_repo(app_state: AppState) -> PruningRequestRepository:
    from synthorg.persistence.backend_dispatch import build_for_backend  # noqa: PLC0415
    from synthorg.persistence.db_handle import (  # noqa: PLC0415
        postgres_pool,
        sqlite_connection,
    )
    from synthorg.persistence.state import persistence_of  # noqa: PLC0415

    persistence = persistence_of(app_state)

    def _sqlite() -> PruningRequestRepository:
        from synthorg.persistence.sqlite.pruning_request_repo import (  # noqa: PLC0415
            SQLitePruningRequestRepository,
        )

        return SQLitePruningRequestRepository(
            sqlite_connection(persistence), write_context=persistence.write_context
        )

    def _postgres() -> PruningRequestRepository:
        from synthorg.persistence.postgres.pruning_request_repo import (  # noqa: PLC0415
            PostgresPruningRequestRepository,
        )

        return PostgresPruningRequestRepository(postgres_pool(persistence))

    return build_for_backend(persistence, sqlite=_sqlite, postgres=_postgres)


__all__ = ["wire_pruning"]
