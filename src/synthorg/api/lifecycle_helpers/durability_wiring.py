# module-kind: code
"""Best-effort post-startup wiring for the durable audit hash chain.

The audit hash chain is built in the construction phase, before persistence
is connected, so it starts in-memory only. This helper attaches its durable
repository once a backend exists and rehydrates the in-memory chain from
storage, so the tamper-evident audit chain survives a restart. It also
starts the periodic re-verification scheduler, so a chain rewritten out of
band while this process keeps running is caught too, not only at the next
restart. It is idempotent and never poisons startup: the broad-except
funnels through :func:`reraise_critical` then logs and swallows.
"""

from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.audit_chain.verify_scheduler import (
    DEFAULT_VERIFY_INTERVAL_SECONDS,
)
from synthorg.observability.events.api import (
    API_AUDIT_CHAIN_PERSISTENCE_DEGRADED,
)
from synthorg.observability.state import ObservabilityStateSlice

logger = get_logger(__name__)


async def _try_wire_audit_chain_persistence(app_state: AppState) -> None:
    """Make the audit hash chain durable and its verification ongoing.

    The ``AuditChainSink`` keeps its chain in memory, so without this the
    tamper-evident chain and its tail hash are lost on every restart.
    Here the durable writer is attached to each live sink, rehydrating
    the chain from storage, verifying it, and draining new appends to the
    repository; the periodic verification scheduler is then started against
    the same sink. A persistence-less boot leaves the chain in-memory-only
    and starts no scheduler (there is nothing durable to re-check).
    """
    from synthorg.observability.audit_chain.durable_writer import (  # noqa: PLC0415
        DurableAuditChainWriter,
    )
    from synthorg.observability.audit_chain.sink import (  # noqa: PLC0415
        AuditChainSink,
    )
    from synthorg.observability.audit_chain.verify_scheduler import (  # noqa: PLC0415
        AuditChainVerificationScheduler,
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
            scheduler = AuditChainVerificationScheduler(
                handler,
                app_state,
                interval_seconds=DEFAULT_VERIFY_INTERVAL_SECONDS,
            )
            await scheduler.start()
            app_state.swap_slice(
                app_state.slice(ObservabilityStateSlice).model_copy(
                    update={"audit_chain_verify_scheduler": scheduler}
                )
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                API_AUDIT_CHAIN_PERSISTENCE_DEGRADED,
                note="audit-chain persistence wiring failed; in-memory only",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
