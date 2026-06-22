# module-kind: orchestrator
"""On-startup wiring for the deliverable-receipts feature engine.

Best-effort + idempotent like every other ``_wire_*`` helper: a missing
collaborator leaves the receipt controllers to 503 rather than poisoning
startup.
"""

from synthorg.api.state import AppState
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_APP_STARTUP

logger = get_logger(__name__)


async def _wire_deliverable_receipts(app_state: AppState) -> None:
    """Wire the deliverable-receipts service once persistence + docs exist.

    Gated on a connected persistence backend and a wired docs service
    (no docs => no deliverables to attach receipts to). The brain is
    optional (decisions degrade to empty without it) and so is the
    red-team report store published on the security slice by the runtime
    wiring (the red_team receipt section degrades to empty without it).
    The built service is parked on its slice and injected into the review
    gate so a completed deliverable emits its provenance receipt.
    """
    from synthorg.approval.state import ApprovalStateSlice  # noqa: PLC0415
    from synthorg.deliverable_receipts.state_slice import (  # noqa: PLC0415
        DeliverableReceiptStateSlice,
    )
    from synthorg.docs_engine.state import DocsStateSlice  # noqa: PLC0415
    from synthorg.persistence.state import (  # noqa: PLC0415
        PersistenceStateSlice,
        persistence_of,
    )
    from synthorg.project_brain.state import ProjectBrainStateSlice  # noqa: PLC0415
    from synthorg.security.state import SecurityStateSlice  # noqa: PLC0415

    if app_state.slice(PersistenceStateSlice).backend is None:
        return
    docs_service = app_state.slice(DocsStateSlice).service
    if docs_service is None:
        return
    if app_state.slice(DeliverableReceiptStateSlice).service is not None:
        return

    from synthorg.api.auto_wire_phase1 import resolve_cassette_config  # noqa: PLC0415
    from synthorg.deliverable_receipts.factory import (  # noqa: PLC0415
        build_deliverable_receipt_service,
    )

    service = build_deliverable_receipt_service(
        persistence=persistence_of(app_state),
        docs_service=docs_service,
        clock=app_state.clock,
        default_currency=app_state.config.budget.currency,
        brain_service=app_state.slice(ProjectBrainStateSlice).service,
        redteam_reports=app_state.slice(SecurityStateSlice).red_team_reports,
        cassette_config=resolve_cassette_config(),
    )
    app_state.swap_slice(DeliverableReceiptStateSlice(service=service))
    review_gate = app_state.slice(ApprovalStateSlice).review_gate
    if review_gate is not None:
        review_gate.set_receipt_service(service)
    logger.info(API_APP_STARTUP, service="deliverable_receipts", note="wired")
