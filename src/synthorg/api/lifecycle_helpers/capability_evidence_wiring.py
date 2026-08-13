# module-kind: code
"""Boot seeding for the bundled capability snapshot.

An installation whose first refresh has not run, or one with no outbound
network at all, would grade every model on the size-and-price heuristic:
the proxy the whole evidence layer exists to replace. Seeding the shipped
snapshot on the first pass means the grading an operator sees out of the
box is the same one a connected installation sees, just older.

Seeding touches only a source with no attempt on record. Once anything has
been fetched here, that source has its own answer, and overwriting it with
a months-old snapshot would move the installation backwards.
"""

from synthorg.api.state import AppState
from synthorg.api.subsystems.errors import SubsystemDeclinedError
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP

logger = get_logger(__name__)


async def wire_capability_evidence_seed(app_state: AppState) -> None:
    """Seed the bundled capability snapshot once persistence is connected.

    Idempotent by construction: the seed skips any source that already has
    a status row, so a repeat pass writes nothing. The slice stamp marks
    the pass as done so the reconciler stops revisiting it.

    Raises:
        SubsystemDeclinedError: No persistence backend, so there is
            nowhere to put the rows.
    """
    from synthorg.persistence.state import PersistenceStateSlice  # noqa: PLC0415
    from synthorg.providers.state import ProvidersStateSlice  # noqa: PLC0415
    from synthorg.workers._capability_source_wiring import (  # noqa: PLC0415
        build_capability_ingest_service,
    )

    if app_state.slice(ProvidersStateSlice).capability_evidence_seeded_at is not None:
        return
    if app_state.slice(PersistenceStateSlice).backend is None:
        msg = "no persistence backend; the capability score table is persisted"
        raise SubsystemDeclinedError(msg)

    seeded_at = app_state.clock.now()
    try:
        service = await build_capability_ingest_service(app_state)
        seeded = () if service is None else await service.seed_from_bundle()
    except Exception as exc:  # noqa: BLE001 -- best-effort startup wiring
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            service="capability_evidence_seed",
            note="seeding failed; grading falls back to the heuristic",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return

    # Stamped even when nothing was seeded. An installation that already
    # fetched every source needs no snapshot, and leaving the capability
    # absent would have the reconciler retry a decided question forever.
    app_state.wire(ProvidersStateSlice, capability_evidence_seeded_at=seeded_at)
    logger.info(
        API_APP_STARTUP,
        service="capability_evidence_seed",
        seeded_sources=[str(s.source_label) for s in seeded],
        scores_written=sum(s.scores_written for s in seeded),
    )


__all__ = ["wire_capability_evidence_seed"]
