"""Best-effort post-startup wiring for durable security/HR subsystems.

The trust service and the audit hash chain are built in the construction
phase, before persistence is connected, so they start in-memory only.
These helpers attach their durable repositories once a backend exists and
rehydrate the in-memory caches from storage, so trust state and the
tamper-evident audit chain survive a restart. Each helper is idempotent
and never poisons startup: the broad-except funnels through
:func:`reraise_critical` then logs and swallows.
"""

from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP

logger = get_logger(__name__)


async def _try_wire_trust_persistence(app_state: AppState) -> None:
    """Attach durable trust repos and hydrate the trust service.

    A persistence-less boot (tests/dev) or a DISABLED trust strategy
    leaves the service in-memory-only.
    """
    from synthorg.persistence.state import (  # noqa: PLC0415
        PersistenceStateSlice,
        persistence_of,
    )
    from synthorg.security.state import SecurityStateSlice  # noqa: PLC0415

    trust_service = app_state.slice(SecurityStateSlice).trust_service
    if trust_service is None or app_state.slice(PersistenceStateSlice).backend is None:
        return
    try:
        persistence = persistence_of(app_state)
        trust_service.attach_persistence(
            state_repo=persistence.trust_states,
            history_repo=persistence.trust_change_history,
        )
        await trust_service.hydrate()
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            service="trust_persistence",
            note="trust persistence wiring failed; in-memory only",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


async def _try_wire_audit_chain_persistence(app_state: AppState) -> None:
    """Make the audit hash chain durable: hydrate + start the writer.

    The ``AuditChainSink`` keeps its chain in memory, so without this the
    tamper-evident chain and its tail hash are lost on every restart.
    Here the durable writer is attached to each live sink, rehydrating
    the chain from storage and draining new appends to the repository. A
    persistence-less boot leaves the chain in-memory-only.
    """
    from synthorg.observability.audit_chain.durable_writer import (  # noqa: PLC0415
        DurableAuditChainWriter,
    )
    from synthorg.observability.audit_chain.sink import (  # noqa: PLC0415
        AuditChainSink,
    )
    from synthorg.observability.startup_wiring import (  # noqa: PLC0415
        _iter_logging_handlers,
    )
    from synthorg.persistence.state import (  # noqa: PLC0415
        PersistenceStateSlice,
        persistence_of,
    )

    if app_state.slice(PersistenceStateSlice).backend is None:
        return
    repo = persistence_of(app_state).audit_chain_entries
    for handler in _iter_logging_handlers():
        if not isinstance(handler, AuditChainSink):
            continue
        try:
            writer = DurableAuditChainWriter(repo)
            await handler.attach_persistence(writer)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                API_APP_STARTUP,
                service="audit_chain_persistence",
                note="audit-chain persistence wiring failed; in-memory only",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
