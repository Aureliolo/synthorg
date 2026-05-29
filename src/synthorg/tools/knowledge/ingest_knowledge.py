"""``IngestKnowledgeTool`` -- register + index a source for an agent.

Admin-tier (``knowledge:ingest`` action type): ingestion pulls external
content into the corpus, so the permission / autonomy layer gates it. The
tool binds its project scope per task; ``None`` ingests a global source.
"""

import builtins
from typing import TYPE_CHECKING, Any, ClassVar, override

from pydantic import BaseModel

from synthorg.api.boundary import parse_typed
from synthorg.core.enums import ActionType, ToolCategory
from synthorg.core.types import NotBlankStr
from synthorg.engine.prompt_safety import TAG_TASK_DATA, wrap_untrusted
from synthorg.knowledge.errors import KnowledgeError
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.knowledge import (
    KNOWLEDGE_INGEST_FAILED,
    KNOWLEDGE_SOURCE_INGESTED,
)
from synthorg.tools.base import BaseTool, ToolExecutionResult
from synthorg.tools.knowledge._args import IngestKnowledgeArgs

if TYPE_CHECKING:
    from synthorg.knowledge.service import KnowledgeService

logger = get_logger(__name__)


class IngestKnowledgeTool(BaseTool):
    """Agent tool that ingests (registers + indexes) a knowledge source."""

    args_model: ClassVar[type[BaseModel] | None] = IngestKnowledgeArgs

    def __init__(
        self,
        *,
        service: KnowledgeService,
        project_id: NotBlankStr | None,
    ) -> None:
        super().__init__(
            name="ingest_knowledge",
            description=(
                "Ingest a source (PDF, web page, or repository) into the "
                "knowledge corpus so its content becomes searchable with "
                "citations. Re-ingesting re-indexes only changed chunks."
            ),
            parameters_schema=IngestKnowledgeArgs.model_json_schema(),
            category=ToolCategory.MEMORY,
            action_type=ActionType.KNOWLEDGE_INGEST.value,
        )
        self._service = service
        self._project_id = project_id

    @override
    async def execute(self, *, arguments: dict[str, Any]) -> ToolExecutionResult:
        """Dispatch an ``ingest_knowledge`` invocation to the service.

        Returns:
            Result of type ``ToolExecutionResult``.

        Raises:
            MemoryError: If the related operation fails.
            RecursionError: If the related operation fails.
        """
        try:
            parsed = parse_typed("mcp.tool", arguments, IngestKnowledgeArgs)
            source = await self._service.ingest(
                source_type=parsed.source_type,
                uri=NotBlankStr(parsed.uri),
                title=NotBlankStr(parsed.title),
                project_id=self._project_id,
            )
        except (ValueError, TypeError) as exc:
            logger.warning(
                KNOWLEDGE_INGEST_FAILED,
                project_id=self._project_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            safe_err = wrap_untrusted(TAG_TASK_DATA, safe_error_description(exc))
            return ToolExecutionResult(
                content=f"Ingest failed: invalid arguments ({safe_err})",
                is_error=True,
            )
        except builtins.MemoryError, RecursionError:
            raise
        except KnowledgeError as exc:
            log_exception_redacted(
                logger, KNOWLEDGE_INGEST_FAILED, exc, project_id=self._project_id
            )
            safe_err = wrap_untrusted(TAG_TASK_DATA, safe_error_description(exc))
            return ToolExecutionResult(
                content=f"Ingest failed: {safe_err}",
                is_error=True,
            )
        logger.info(
            KNOWLEDGE_SOURCE_INGESTED,
            source_id=source.source_id,
            chunk_count=source.chunk_count,
        )
        # source.title is user-supplied input that may carry an injection;
        # wrap it before placing it in model-facing tool content.
        safe_title = wrap_untrusted(TAG_TASK_DATA, source.title)
        return ToolExecutionResult(
            content=(
                f"Ingested {source.source_type.value} source {safe_title!r}: "
                f"{source.chunk_count} chunks indexed (status {source.status.value})."
            ),
            metadata={
                "source_id": source.source_id,
                "status": source.status.value,
                "chunk_count": source.chunk_count,
            },
        )


__all__ = ["IngestKnowledgeTool"]
