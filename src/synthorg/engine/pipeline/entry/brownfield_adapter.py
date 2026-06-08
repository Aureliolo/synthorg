"""Brownfield codebase-intake work-entry adapter.

The "merger/acquisition" entry mode. Unlike the other entry adapters
(stateless input->WorkItem mappers), this adapter first drives the
:class:`~synthorg.engine.brownfield.service.BrownfieldImportService` to
import, scan, and index the codebase, then maps an analysis directive
onto a :class:`WorkItem` (``source=BROWNFIELD``, ``task_type=ANALYSIS``)
so the agent analysis pass produces an architecture/health assessment
through the normal pipeline spine. The org then awaits human direction;
follow-up directives arrive via the task-board adapter and retrieve the
indexed structure map + analysis.
"""

from typing import TYPE_CHECKING

from synthorg.core.task_enums import TaskType
from synthorg.engine.pipeline.models import WorkItem, WorkSource
from synthorg.observability import get_logger
from synthorg.observability.events.brownfield import BROWNFIELD_IMPORT_COMPLETED

if TYPE_CHECKING:
    from synthorg.engine.brownfield.models import CodebaseImportSubmission
    from synthorg.engine.brownfield.service import BrownfieldImportService
    from synthorg.engine.pipeline.models import WorkPipelineResult
    from synthorg.engine.pipeline.protocol import WorkPipeline

logger = get_logger(__name__)

_ORIGIN_ADAPTER_ID = "brownfield-entry-adapter"
_ANALYSIS_INTENT = (
    "An existing codebase has been imported into this project's workspace and "
    "indexed into the knowledge store. Produce an architecture and health "
    "assessment of it as a CODEBASE_ANALYSIS living document. Use the "
    "query_structure_map tool to enumerate modules, entry points, tests, build "
    "files, and dependencies, and search_knowledge to read the imported code. "
    "Cover architecture, notable risks, test coverage posture, and dependency "
    "health, then await human direction on what to build next."
)


class BrownfieldEntryAdapter:
    """Imports an existing codebase, then drives the analysis pass."""

    __slots__ = ("_import_service", "_work_pipeline")

    def __init__(
        self,
        *,
        work_pipeline: WorkPipeline,
        import_service: BrownfieldImportService,
    ) -> None:
        """Initialise the adapter.

        Args:
            work_pipeline: The composed pipeline spine to drive.
            import_service: Service that imports, scans, and indexes the
                codebase before the analysis pass runs.
        """
        self._work_pipeline = work_pipeline
        self._import_service = import_service

    @property
    def source(self) -> WorkSource:
        """Provenance stamp for items this adapter produces."""
        return WorkSource.BROWNFIELD

    async def submit(self, request: CodebaseImportSubmission) -> WorkPipelineResult:
        """Import the codebase, then run the analysis pass through the spine.

        Args:
            request: The codebase import submission.

        Returns:
            The terminal :class:`WorkPipelineResult` of the analysis pass.

        Raises:
            BrownfieldError: Propagated unchanged from the import service.
            WorkPipelineError: Propagated unchanged from the spine.
        """
        result = await self._import_service.import_codebase(request)
        logger.info(
            BROWNFIELD_IMPORT_COMPLETED,
            project_id=request.project_id,
            unchanged=result.unchanged,
            module_count=result.module_count,
        )
        work_item = WorkItem(
            origin_adapter_id=_ORIGIN_ADAPTER_ID,
            source=WorkSource.BROWNFIELD,
            title=f"Analyse imported codebase: {request.title}",
            raw_intent=_ANALYSIS_INTENT,
            project=request.project_id,
            requested_by=request.requested_by,
            task_type=TaskType.ANALYSIS,
            acceptance_criteria=(
                "A CODEBASE_ANALYSIS living document assessing architecture "
                "and health is produced.",
                "The assessment is grounded in the structure map's modules "
                "and dependencies.",
            ),
        )
        return await self._work_pipeline.run(work_item)


__all__ = ["BrownfieldEntryAdapter"]
