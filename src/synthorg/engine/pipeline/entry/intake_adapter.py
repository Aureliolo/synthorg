"""Client-request intake work-entry adapter.

Maps a stored :class:`ClientRequest` onto a ``WorkItem`` with
``source=INTAKE`` and drives the pipeline spine. Thin by design: it
holds no request store, no lock, and performs no request-lifecycle
reconciliation (the controller background task owns that). Manual
scope flow is preserved by folding any reviewer ``scoping_notes`` from
the request metadata into the work item's intent body.
"""

from typing import TYPE_CHECKING

from synthorg.engine.pipeline.models import WorkItem, WorkSource
from synthorg.observability import get_logger
from synthorg.observability.events.review_pipeline import INTAKE_REQUEST_RECEIVED

if TYPE_CHECKING:
    from synthorg.client.models import ClientRequest
    from synthorg.core.types import NotBlankStr
    from synthorg.engine.pipeline.models import WorkPipelineResult
    from synthorg.engine.pipeline.protocol import WorkPipeline

logger = get_logger(__name__)

_ORIGIN_ADAPTER_ID = "intake-entry-adapter"
_SCOPING_NOTES_KEY = "scoping_notes"
_SCOPING_NOTES_HEADING = "## Reviewer scoping notes"


class IntakeEntryAdapter:
    """Feeds approved client requests into the pipeline spine."""

    __slots__ = ("_default_project", "_work_pipeline")

    def __init__(
        self,
        *,
        work_pipeline: WorkPipeline,
        default_project: NotBlankStr,
    ) -> None:
        """Initialize the adapter.

        Args:
            work_pipeline: The composed pipeline spine to drive.
            default_project: Project every intake work item is filed
                into (the same value the intake strategy creates the
                task in and that is ensured to exist at boot).
        """
        self._work_pipeline = work_pipeline
        self._default_project = default_project

    @property
    def source(self) -> WorkSource:
        """Provenance stamp for items this adapter produces."""
        return WorkSource.INTAKE

    async def submit(self, request: ClientRequest) -> WorkPipelineResult:
        """Map ``request`` onto a work item and drive the spine.

        Args:
            request: The stored client request to enter.

        Returns:
            The terminal :class:`WorkPipelineResult`.

        Raises:
            WorkPipelineError: Propagated unchanged from the spine.
        """
        requirement = request.requirement
        work_item = WorkItem(
            origin_adapter_id=_ORIGIN_ADAPTER_ID,
            source=WorkSource.INTAKE,
            title=requirement.title,
            raw_intent=self._build_raw_intent(request),
            project=self._default_project,
            requested_by=request.client_id,
            priority=requirement.priority,
            task_type=requirement.task_type,
            estimated_complexity=requirement.estimated_complexity,
            acceptance_criteria=requirement.acceptance_criteria,
            correlation_id=request.request_id,
        )
        logger.info(
            INTAKE_REQUEST_RECEIVED,
            request_id=request.request_id,
            client_id=request.client_id,
            project=self._default_project,
        )
        return await self._work_pipeline.run(work_item)

    @staticmethod
    def _build_raw_intent(request: ClientRequest) -> str:
        """Compose the intent body, folding in reviewer scope notes."""
        body = request.requirement.description
        notes = request.metadata.get(_SCOPING_NOTES_KEY)
        if isinstance(notes, str) and notes.strip():
            return f"{body}\n\n{_SCOPING_NOTES_HEADING}\n\n{notes.strip()}"
        return body
