# module-kind: code
"""Factory for the Chief-of-Staff run narrator.

``build_chief_of_staff_narrator`` is the ghost-wiring manifest entry the
startup hook calls. It returns ``None`` when documentary mode is disabled
or any required collaborator is absent, so the pipeline trigger stays a
no-op rather than failing.
"""

from synthorg.budget.tracker import CostTracker
from synthorg.docs_engine.service import DocsService
from synthorg.meta.chief_of_staff.config import ChiefOfStaffConfig
from synthorg.meta.chief_of_staff.narrative.reader import NarrativeReader
from synthorg.meta.chief_of_staff.narrative.service import ChiefOfStaffNarrator
from synthorg.meta.chief_of_staff.narrative.synthesiser import NarrativeSynthesiser
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.persistence.flight_recorder_protocol import (
    FlightRecorderFrameRepository,
)
from synthorg.persistence.task_protocol import TaskRepository
from synthorg.project_brain.service import ProjectBrainService
from synthorg.providers.protocol import CompletionProvider

logger = get_logger(__name__)


def build_chief_of_staff_narrator(  # noqa: PLR0913 -- keyword-only DI of every collaborator
    config: ChiefOfStaffConfig,
    *,
    provider: CompletionProvider | None,
    docs_service: DocsService | None,
    brain_service: ProjectBrainService | None,
    frames: FlightRecorderFrameRepository | None,
    task_repo: TaskRepository | None,
    cost_tracker: CostTracker | None = None,
) -> ChiefOfStaffNarrator | None:
    """Construct the run narrator, or ``None`` when it cannot be wired.

    Args:
        config: Chief-of-Staff configuration (gates on ``narrative_enabled``).
        provider: Completion provider for the connective-prose call.
        docs_service: Living-docs engine the narrative is persisted through.
        brain_service: Project-brain read seam (decisions, open items).
        frames: Flight-recorder frame store (who did what, metrics).
        task_repo: Task read seam (brief title, final status).
        cost_tracker: Optional cost tracker for the prose call.

    Returns:
        A ready :class:`ChiefOfStaffNarrator`, or ``None`` when
        documentary mode is disabled or any collaborator is missing.
    """
    if not config.narrative_enabled:
        return None
    if (
        provider is None
        or docs_service is None
        or brain_service is None
        or frames is None
        or task_repo is None
    ):
        return None
    reader = NarrativeReader(frames=frames, brain=brain_service, task_repo=task_repo)
    synthesiser = NarrativeSynthesiser(
        provider=provider, config=config, cost_tracker=cost_tracker
    )
    narrator = ChiefOfStaffNarrator(
        reader=reader, synthesiser=synthesiser, docs=docs_service
    )
    logger.info(API_APP_STARTUP, service="chief_of_staff_narrator", note="built")
    return narrator
