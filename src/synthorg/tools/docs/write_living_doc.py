"""``WriteLivingDocTool`` -- agents author / update living documents.

The tool reads ``project_id`` from the agent execution context (passed
to ``execute()`` via the optional ``context`` kwarg the invoker
forwards). Cross-project authority is checked by callers via the
:class:`TrustService` seam, not by the tool itself.
"""

from typing import TYPE_CHECKING, Any, ClassVar, override

from pydantic import BaseModel

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.enums import (
    ActionType,
    ToolCategory,
)
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.docs import (
    DOC_WRITE_FAILED,
    DOC_WRITTEN,
)
from synthorg.tools.base import BaseTool, ToolExecutionResult
from synthorg.tools.docs._args import (
    WriteLivingDocArgs,
    WriteLivingDocBlockArg,
)

if TYPE_CHECKING:
    from synthorg.core.types import NotBlankStr
    from synthorg.docs_engine.models import DocBlock
    from synthorg.docs_engine.service import DocsService

logger = get_logger(__name__)

SHORT_SHA_LENGTH: int = 12


class WriteLivingDocTool(BaseTool):
    """Agent tool that creates or updates a living document.

    Args:
        docs_service: Engine entry point used to persist the doc.
        project_id: Active project for this tool instance. Wired by
            the per-task tool factory from the execution context.
        author_agent_id: The calling agent's identifier; embedded into
            the doc metadata so the wiki can show attribution.
    """

    args_model: ClassVar[type[BaseModel] | None] = WriteLivingDocArgs

    def __init__(
        self,
        *,
        docs_service: DocsService,
        project_id: NotBlankStr,
        author_agent_id: NotBlankStr,
    ) -> None:
        super().__init__(
            name="write_living_doc",
            description=(
                "Create or update a living document (status report, "
                "deliverable, or knowledge note) for the active project. "
                "Pass a structured body of typed blocks; the service "
                "derives a stable slug from the title."
            ),
            parameters_schema=WriteLivingDocArgs.model_json_schema(),
            category=ToolCategory.OTHER,
            action_type=ActionType.DOCS_WRITE.value,
        )
        self._docs_service = docs_service
        self._project_id = project_id
        self._author_agent_id = author_agent_id

    @override
    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        """Translate args to a :class:`LivingDocument` body and persist.

        Returns:
            Result of type ``ToolExecutionResult``.
        """
        try:
            parsed = WriteLivingDocArgs.model_validate(arguments)
            body = _materialise_body(parsed.body)
            metadata = await self._docs_service.write_doc(
                project_id=self._project_id,
                title=parsed.title,
                doc_type=parsed.doc_type,
                author_agent_id=self._author_agent_id,
                body=body,
                tags=parsed.tags,
                related_task_ids=parsed.related_task_ids,
                slug=parsed.slug,
            )
        except (ValueError, TypeError) as exc:
            logger.warning(
                DOC_WRITE_FAILED,
                project_id=self._project_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return ToolExecutionResult(
                content=(
                    f"Write failed: invalid argument shape "
                    f"({safe_error_description(exc)})"
                ),
                is_error=True,
            )
        except Exception as exc:
            reraise_critical(exc)
            log_exception_redacted(
                logger, DOC_WRITE_FAILED, exc, project_id=self._project_id
            )
            return ToolExecutionResult(
                content=(
                    f"Write failed: {type(exc).__name__} "
                    f"({safe_error_description(exc)})"
                ),
                is_error=True,
            )
        logger.info(
            DOC_WRITTEN,
            project_id=self._project_id,
            slug=metadata.slug,
            doc_type=metadata.doc_type.value,
        )
        return ToolExecutionResult(
            content=(
                f"Wrote living doc {metadata.doc_type.value}/{metadata.slug}"
                f" at {metadata.head_commit_sha[:SHORT_SHA_LENGTH]}"
            ),
            metadata={
                "slug": metadata.slug,
                "doc_type": metadata.doc_type.value,
                "head_commit_sha": metadata.head_commit_sha,
            },
        )


def _materialise_body(
    blocks: tuple[WriteLivingDocBlockArg, ...],
) -> tuple[DocBlock, ...]:
    """Convert agent-facing block args to typed :data:`DocBlock` instances.

    Returns:
        Tuple of ``DocBlock``.
    """
    return tuple(block.to_block() for block in blocks)


__all__ = ["WriteLivingDocTool"]
