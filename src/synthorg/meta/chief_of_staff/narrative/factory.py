# module-kind: code
"""Factory for the Chief-of-Staff run narrator.

``build_chief_of_staff_narrator`` is the ghost-wiring manifest entry the
startup hook calls. It returns ``None`` when documentary mode is disabled
or any required collaborator is absent, so the pipeline trigger stays a
no-op rather than failing.
"""

from synthorg.budget.currency import DEFAULT_CURRENCY, CurrencyCode
from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.docs_engine.service import DocsService
from synthorg.meta.chief_of_staff.config import ChiefOfStaffConfig
from synthorg.meta.chief_of_staff.narrative.reader import NarrativeReader
from synthorg.meta.chief_of_staff.narrative.service import ChiefOfStaffNarrator
from synthorg.meta.chief_of_staff.narrative.synthesiser import NarrativeSynthesiser
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.observability.events.chief_of_staff import COS_NARRATIVE_SKIPPED
from synthorg.persistence.flight_recorder_protocol import (
    FlightRecorderFrameRepository,
)
from synthorg.persistence.task_protocol import TaskRepository
from synthorg.project_brain.service import ProjectBrainService
from synthorg.providers.protocol import CompletionProvider
from synthorg.settings.resolver import ConfigResolver

logger = get_logger(__name__)


def build_chief_of_staff_narrator(  # noqa: PLR0913 -- keyword-only DI of every collaborator
    config: ChiefOfStaffConfig,
    *,
    provider: CompletionProvider | None,
    docs_service: DocsService | None,
    brain_service: ProjectBrainService | None,
    frames: FlightRecorderFrameRepository | None,
    task_repo: TaskRepository | None,
    cost_tracker: CostTrackerProtocol | None = None,
    currency: CurrencyCode = DEFAULT_CURRENCY,
    config_resolver: ConfigResolver | None = None,
    master_enabled: bool = True,
) -> ChiefOfStaffNarrator | None:
    """Construct the run narrator, or ``None`` when a collaborator is absent.

    The narrator is built unconditionally of ``narrative_enabled``: it gates
    documentary mode live per run, so the instance must exist for the flag to
    flip on without a restart. Returns ``None`` only when a required
    collaborator is missing.

    Args:
        config: Chief-of-Staff configuration (baked fallback for the gate).
        provider: Completion provider for the connective-prose call.
        docs_service: Living-docs engine the narrative is persisted through.
        brain_service: Project-brain read seam (decisions, open items).
        frames: Flight-recorder frame store (who did what, metrics).
        task_repo: Task read seam (brief title, final status).
        cost_tracker: Optional cost tracker for the prose call.
        currency: ISO 4217 code the run's costs are denominated in.
        config_resolver: Optional resolver for the live ``narrative_enabled``
            per-run gate and the per-call ``narrative_model`` read.
        master_enabled: Baked ``self_improvement.chief_of_staff_enabled``
            persona switch, the gate's master fallback on resolver outage.

    Returns:
        A ready :class:`ChiefOfStaffNarrator`, or ``None`` when any
        collaborator is missing.
    """
    if (
        provider is None
        or docs_service is None
        or brain_service is None
        or frames is None
        or task_repo is None
    ):
        logger.debug(
            COS_NARRATIVE_SKIPPED,
            service="chief_of_staff_narrator",
            reason="collaborator_absent",
        )
        return None
    reader = NarrativeReader(
        frames=frames, brain=brain_service, task_repo=task_repo, currency=currency
    )
    synthesiser = NarrativeSynthesiser(
        provider=provider,
        config=config,
        cost_tracker=cost_tracker,
        config_resolver=config_resolver,
    )
    narrator = ChiefOfStaffNarrator(
        reader=reader,
        synthesiser=synthesiser,
        docs=docs_service,
        config=config,
        config_resolver=config_resolver,
        master_enabled=master_enabled,
    )
    logger.info(API_APP_STARTUP, service="chief_of_staff_narrator", note="built")
    return narrator
