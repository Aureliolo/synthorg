# module-kind: orchestrator
"""On-startup wiring for durable provider latching failures.

Builds the per-backend :class:`ProviderLatchRepository`, attaches it to the
health tracker, and reads the outstanding latches back in so a pair that was
refusing billed calls before the restart is still refusing after it.

Persistence-gated by declaration: a latch that survives nothing is the
condition this closes, so with no backend the subsystem declines and says
so rather than coming up as a store that forgets.
"""

from synthorg.api.state import AppState
from synthorg.api.subsystems.errors import SubsystemDeclinedError
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.persistence.provider_latch_protocol import ProviderLatchRepository
from synthorg.providers.state import ProvidersStateSlice

logger = get_logger(__name__)


async def wire_provider_latches(app_state: AppState) -> None:
    """Attach the durable latch store and restore the outstanding latches.

    Args:
        app_state: The application state to wire onto.

    Raises:
        SubsystemDeclinedError: No persistence backend, or no health tracker
            to hold the store.
    """
    from synthorg.persistence.state import PersistenceStateSlice  # noqa: PLC0415

    providers = app_state.slice(ProvidersStateSlice)
    if providers.latch_store is not None:
        return
    tracker = providers.health_tracker
    if tracker is None:
        msg = "no provider health tracker; there is nothing to hold the latches"
        raise SubsystemDeclinedError(msg)
    if app_state.slice(PersistenceStateSlice).backend is None:
        msg = "no persistence backend; a latch that survives nothing is the defect"
        raise SubsystemDeclinedError(msg)
    store = _build_repo(app_state)
    tracker.bind_latch_store(store)
    restored = await tracker.restore_latches()
    app_state.wire(ProvidersStateSlice, latch_store=store)
    logger.info(API_APP_STARTUP, service="provider_latches", restored=restored)


def _build_repo(app_state: AppState) -> ProviderLatchRepository:
    from synthorg.persistence.backend_dispatch import build_for_backend  # noqa: PLC0415
    from synthorg.persistence.db_handle import (  # noqa: PLC0415
        postgres_pool,
        sqlite_connection,
    )
    from synthorg.persistence.state import persistence_of  # noqa: PLC0415

    persistence = persistence_of(app_state)

    def _sqlite() -> ProviderLatchRepository:
        from synthorg.persistence.sqlite.provider_latch_repo import (  # noqa: PLC0415
            SQLiteProviderLatchRepository,
        )

        return SQLiteProviderLatchRepository(
            sqlite_connection(persistence), write_context=persistence.write_context
        )

    def _postgres() -> ProviderLatchRepository:
        from synthorg.persistence.postgres.provider_latch_repo import (  # noqa: PLC0415
            PostgresProviderLatchRepository,
        )

        return PostgresProviderLatchRepository(postgres_pool(persistence))

    return build_for_backend(persistence, sqlite=_sqlite, postgres=_postgres)


__all__ = ["wire_provider_latches"]
