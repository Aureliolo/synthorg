# module-kind: service
"""Boot-time assembly for the deliverable-receipts service.

Composes the builder, validator, and renderer over a connected
persistence backend plus the optional brain, red-team, and cassette
collaborators, and returns the orchestrating service.
"""

from synthorg.budget.currency import CurrencyCode
from synthorg.core.clock import Clock
from synthorg.deliverable_receipts.builder import ReceiptBuilder
from synthorg.deliverable_receipts.renderer import ReceiptRenderer
from synthorg.deliverable_receipts.service import DeliverableReceiptService
from synthorg.deliverable_receipts.validator import ReceiptValidator
from synthorg.docs_engine.service import DocsService
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.project_brain.service import ProjectBrainService
from synthorg.providers.cassette.mode import CassetteConfig
from synthorg.security.redteam.protocol import RedTeamReportRepository


def build_deliverable_receipt_service(  # noqa: PLR0913 -- cohesive boot wiring
    *,
    persistence: PersistenceBackend,
    docs_service: DocsService,
    clock: Clock,
    default_currency: CurrencyCode,
    brain_service: ProjectBrainService | None = None,
    redteam_reports: RedTeamReportRepository | None = None,
    cassette_config: CassetteConfig | None = None,
) -> DeliverableReceiptService:
    """Assemble the deliverable-receipt service from its collaborators.

    Args:
        persistence: Connected backend providing the receipt, capture,
            cost, docs, knowledge-source, and flight-recorder repos.
        docs_service: Living-docs service for the receipt projection.
        clock: Clock seam stamped onto issued receipts.
        default_currency: Currency used when a task has no cost records.
        brain_service: Project brain for decisions (optional).
        redteam_reports: Process-local red-team report store (optional).
        cassette_config: Active cassette configuration (optional).

    Returns:
        A wired :class:`DeliverableReceiptService`.
    """
    builder = ReceiptBuilder(
        cost_records=persistence.cost_records,
        knowledge_usage_records=persistence.knowledge_usage_records,
        knowledge_sources=persistence.knowledge_sources,
        code_execution_records=persistence.code_execution_records,
        clock=clock,
        default_currency=default_currency,
        brain_service=brain_service,
        redteam_reports=redteam_reports,
        cassette_config=cassette_config,
    )
    validator = ReceiptValidator(
        knowledge_sources=persistence.knowledge_sources,
        code_execution_records=persistence.code_execution_records,
        redteam_reports=redteam_reports,
    )
    renderer = ReceiptRenderer(docs_service=docs_service)
    return DeliverableReceiptService(
        receipts=persistence.deliverable_receipts,
        builder=builder,
        validator=validator,
        renderer=renderer,
        docs=persistence.project_docs,
        docs_service=docs_service,
        flight_recorder=persistence.flight_recorder_frames,
    )
