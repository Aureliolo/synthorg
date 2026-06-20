# module-kind: orchestrator
"""On-startup wiring for the durable evolution-outcome log.

Builds the per-backend :class:`EvolutionOutcomeRepository`, wraps it in a
:class:`DurableEvolutionOutcomeStore` (ring-buffer hot reads + durable
write-through), rehydrates the buffer from the durable log, and publishes
both the store and an :class:`EvolutionReadService` on ``MetaStateSlice``.

Runs BEFORE ``_install_runtime_services`` so the engine evolution loop
(built there) can read the store off the slice as its outcome sink, and
before ``_wire_signals_service`` so the signals aggregator reads the same
durable store. Best-effort + idempotent + persistence-gated: a missing
backend leaves the store absent and the signals evolution domain / read
endpoints degrade rather than poisoning startup.
"""

from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.persistence.evolution_outcome_protocol import (
    EvolutionOutcomeRepository,
)

logger = get_logger(__name__)


async def wire_evolution_outcomes(app_state: AppState) -> None:
    """Build + publish the durable evolution-outcome store and read service.

    Args:
        app_state: The application state to wire onto.
    """
    from synthorg.meta.state import MetaStateSlice  # noqa: PLC0415
    from synthorg.persistence.state import PersistenceStateSlice  # noqa: PLC0415

    if app_state.slice(MetaStateSlice).evolution_outcome_store is not None:
        return
    if app_state.slice(PersistenceStateSlice).backend is None:
        logger.info(
            API_APP_STARTUP,
            service="evolution_outcomes",
            note="persistence absent; durable outcome log unwired",
        )
        return
    try:
        await _wire(app_state)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            service="evolution_outcomes",
            note="evolution outcome wiring failed; store stays unwired",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


async def _wire(app_state: AppState) -> None:
    from synthorg.meta.evolution.durable_store import (  # noqa: PLC0415
        DurableEvolutionOutcomeStore,
    )
    from synthorg.meta.evolution.read_service import (  # noqa: PLC0415
        EvolutionReadService,
    )
    from synthorg.meta.state import MetaStateSlice  # noqa: PLC0415

    repo = _build_repo(app_state)
    store = DurableEvolutionOutcomeStore(repo=repo, clock=app_state.clock)
    await store.rehydrate()
    read_service = EvolutionReadService(repo=repo)
    app_state.wire(
        MetaStateSlice,
        evolution_outcome_store=store,
        evolution_read_service=read_service,
    )
    logger.info(API_APP_STARTUP, service="evolution_outcomes", note="wired (durable)")


def _build_repo(app_state: AppState) -> EvolutionOutcomeRepository:
    from synthorg.persistence.backend_dispatch import build_for_backend  # noqa: PLC0415
    from synthorg.persistence.db_handle import (  # noqa: PLC0415
        postgres_pool,
        sqlite_connection,
    )
    from synthorg.persistence.state import persistence_of  # noqa: PLC0415

    persistence = persistence_of(app_state)

    def _sqlite() -> EvolutionOutcomeRepository:
        from synthorg.persistence.sqlite.evolution_outcome_repo import (  # noqa: PLC0415
            SQLiteEvolutionOutcomeRepository,
        )

        return SQLiteEvolutionOutcomeRepository(
            sqlite_connection(persistence), write_context=persistence.write_context
        )

    def _postgres() -> EvolutionOutcomeRepository:
        from synthorg.persistence.postgres.evolution_outcome_repo import (  # noqa: PLC0415
            PostgresEvolutionOutcomeRepository,
        )

        return PostgresEvolutionOutcomeRepository(postgres_pool(persistence))

    return build_for_backend(persistence, sqlite=_sqlite, postgres=_postgres)


__all__ = ["wire_evolution_outcomes"]
