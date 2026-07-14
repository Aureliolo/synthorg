# module-kind: code
"""Best-effort post-startup wiring for the durable audit hash chain.

The audit hash chain is built in the construction phase, before persistence
is connected, so it starts in-memory only. This helper attaches its durable
repository once a backend exists and rehydrates the in-memory chain from
storage, so the tamper-evident audit chain survives a restart. It is
idempotent and never poisons startup: the broad-except funnels through
:func:`reraise_critical` then logs and swallows.
"""

from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_AUDIT_CHAIN_PERSISTENCE_DEGRADED,
)

logger = get_logger(__name__)


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
    from synthorg.observability.sinks import (  # noqa: PLC0415
        iter_logging_handlers,
    )
    from synthorg.persistence.state import (  # noqa: PLC0415
        PersistenceStateSlice,
        persistence_of,
    )

    if app_state.slice(PersistenceStateSlice).backend is None:
        return
    repo = persistence_of(app_state).audit_chain_entries
    for handler in iter_logging_handlers():
        if not isinstance(handler, AuditChainSink):
            continue
        try:
            writer = DurableAuditChainWriter(repo)
            await handler.attach_persistence(writer)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                API_AUDIT_CHAIN_PERSISTENCE_DEGRADED,
                note="audit-chain persistence wiring failed; in-memory only",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
