# module-kind: orchestrator
"""Orchestrates the run-narrative pipeline and persists the result.

:class:`ChiefOfStaffNarrator` wires the reader, reducer, synthesiser, and
assembler into one post-run step and writes the result as a
``run_narrative`` living doc. It is idempotent per brief: re-completing a
brief refreshes the same doc (keyed on the brief's task tag) rather than
spawning a duplicate, while the execution tag records which run produced
the latest narrative. A run with no recorded activity is a benign skip
(returns ``None``).
"""

from synthorg.core.enums import DocType
from synthorg.core.types import NotBlankStr
from synthorg.docs_engine.models import DocBlock, DocMetadata
from synthorg.docs_engine.service import DocsService
from synthorg.meta.chief_of_staff.narrative.assembler import assemble_blocks
from synthorg.meta.chief_of_staff.narrative.constants import (
    EXECUTION_TAG_PREFIX,
    NARRATIVE_TAG,
    NARRATOR_AGENT_ID,
    TASK_TAG_PREFIX,
)
from synthorg.meta.chief_of_staff.narrative.errors import (
    NarrativeSourceUnavailableError,
)
from synthorg.meta.chief_of_staff.narrative.models import ReducedRun
from synthorg.meta.chief_of_staff.narrative.reader import NarrativeReader
from synthorg.meta.chief_of_staff.narrative.reducer import reduce_run
from synthorg.meta.chief_of_staff.narrative.synthesiser import NarrativeSynthesiser
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.chief_of_staff import (
    COS_NARRATIVE_GENERATED,
    COS_NARRATIVE_GENERATION_STARTED,
    COS_NARRATIVE_SOURCE_UNAVAILABLE,
)

logger = get_logger(__name__)

_TITLE_PREFIX: str = "Run narrative: "
_TITLE_MAX: int = 512


class ChiefOfStaffNarrator:
    """Generates and persists a run narrative for a completed brief."""

    __slots__ = ("_docs", "_reader", "_synthesiser")

    def __init__(
        self,
        *,
        reader: NarrativeReader,
        synthesiser: NarrativeSynthesiser,
        docs: DocsService,
    ) -> None:
        self._reader = reader
        self._synthesiser = synthesiser
        self._docs = docs

    async def generate(
        self,
        *,
        task_id: NotBlankStr,
        project_id: NotBlankStr,
    ) -> DocMetadata | None:
        """Produce and persist the narrative for one completed brief.

        Args:
            task_id: The brief / root task id.
            project_id: The owning project.

        Returns:
            The persisted :class:`DocMetadata`, or ``None`` when the run
            recorded no activity to narrate.
        """
        logger.info(
            COS_NARRATIVE_GENERATION_STARTED,
            task_id=task_id,
            project_id=project_id,
        )
        try:
            inputs = await self._reader.gather(task_id=task_id, project_id=project_id)
        except NarrativeSourceUnavailableError as exc:
            logger.info(
                COS_NARRATIVE_SOURCE_UNAVAILABLE,
                task_id=task_id,
                project_id=project_id,
                reason=safe_error_description(exc),
            )
            return None
        reduced = reduce_run(inputs)
        prose = await self._synthesiser.write_prose(reduced)
        body = assemble_blocks(reduced, prose)
        metadata = await self._persist(reduced, body)
        logger.info(
            COS_NARRATIVE_GENERATED,
            task_id=task_id,
            project_id=project_id,
            execution_id=reduced.execution_id,
            slug=metadata.slug,
            decision_count=len(reduced.decisions),
            open_item_count=len(reduced.open_items),
        )
        return metadata

    async def _persist(
        self, reduced: ReducedRun, body: tuple[DocBlock, ...]
    ) -> DocMetadata:
        """Write the narrative doc, updating the brief's doc in place.

        The lookup keys on the brief (task) tag, not the execution tag: a
        fresh execution id is minted per run, so re-completing the same
        brief must refresh the one narrative rather than spawn duplicates.
        The execution tag is still stamped, recording which run produced
        the latest narrative.

        Returns:
            The persisted :class:`DocMetadata`.
        """
        task_tag = NotBlankStr(f"{TASK_TAG_PREFIX}{reduced.task_id}")
        execution_tag = NotBlankStr(f"{EXECUTION_TAG_PREFIX}{reduced.execution_id}")
        existing = await self._docs.list_docs(
            project_id=reduced.project_id,
            doc_type=DocType.RUN_NARRATIVE,
            tag=task_tag,
        )
        slug = existing[0].slug if existing else None
        return await self._docs.write_doc(
            project_id=reduced.project_id,
            title=_title(reduced.brief_title),
            doc_type=DocType.RUN_NARRATIVE,
            author_agent_id=NARRATOR_AGENT_ID,
            body=body,
            tags=(NARRATIVE_TAG, task_tag, execution_tag),
            related_task_ids=(reduced.task_id,),
            slug=slug,
        )


def _title(brief_title: str) -> NotBlankStr:
    """Build the narrative doc title from the brief title.

    Returns:
        The bounded, prefixed title.
    """
    return NotBlankStr(f"{_TITLE_PREFIX}{brief_title}"[:_TITLE_MAX])
