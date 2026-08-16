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
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.observability.events.provider import PROVIDER_LATCH_RESTORE_FAILED
from synthorg.persistence.provider_latch_protocol import ProviderLatchRepository
from synthorg.providers.state import ProvidersStateSlice

logger = get_logger(__name__)


async def wire_provider_latches(app_state: AppState) -> None:
    """Attach the durable latch store and restore the outstanding latches.

    The store is bound before the read-back, so a failed restore still leaves
    fresh refusals persisting, and the capability is published only once the
    read-back has actually happened: an unreadable table is a declined
    subsystem an operator can see on ``GET /subsystems``, not a restore that
    reports zero and reads exactly like a company that had no latches.

    Args:
        app_state: The application state to wire onto.

    Raises:
        SubsystemDeclinedError: No persistence backend, no health tracker to
            hold the store, a backend that hands out no database handle, or
            stored latches that could not be read.
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
    try:
        store = _build_repo(app_state)
    except Exception as exc:
        # A backend is wired but hands out no usable handle (an unregistered
        # kind, or one that answers the connection request with a raise). The
        # condition belongs on ``GET /subsystems`` beside the other three; a
        # bare raise here fails the whole reconcile pass instead, which is how
        # one unavailable store came to 500 an operator's setup completion.
        reraise_critical(exc)
        msg = (
            "the persistence backend supplied no database handle, so there is"
            " nowhere to write a latch that outlives the process"
        )
        logger.warning(
            API_APP_STARTUP,
            service="provider_latches",
            note="latch store could not be built; latches stay in-process",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise SubsystemDeclinedError(msg) from exc
    tracker.bind_latch_store(store)
    try:
        restored = await tracker.restore_latches()
    except Exception as exc:
        # Same posture as the build path above, for the same reason: a store
        # that raises anything other than PersistenceError (a driver error, a
        # decode failure) would otherwise escape and fail the whole reconcile
        # pass, rather than declining this one subsystem with its condition.
        reraise_critical(exc)
        logger.error(
            PROVIDER_LATCH_RESTORE_FAILED,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = (
            "the stored latches could not be read, so a pair that was refusing"
            " before the restart would come back serving"
        )
        raise SubsystemDeclinedError(msg) from exc
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
