"""``SearchLivingDocsTool`` -- explicit doc-only retrieval for agents.

This is the dedicated tool path called when an agent wants to query
the project's living-docs corpus directly (e.g. "list deliverables
tagged checkout"). The transparent RAG path lives on
:class:`ProjectAwareMemoryFacade` and surfaces docs alongside other
memories on the normal ``memory.retrieve`` call.
"""

from typing import ClassVar, override

from pydantic import BaseModel, JsonValue

from synthorg.api.boundary import parse_typed
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.docs_engine.models import DocSearchHit
from synthorg.docs_engine.service import DocsService
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.docs import (
    DOC_SEARCH_COMPLETE,
    DOC_SEARCH_FAILED,
    DOC_SEARCH_START,
)
from synthorg.security.autonomy.enums import ActionType, ToolCategory
from synthorg.tools.base import BaseTool, ToolExecutionResult
from synthorg.tools.docs._args import SearchLivingDocsArgs

logger = get_logger(__name__)


class SearchLivingDocsTool(BaseTool):
    """Agent tool that searches living docs for the active project."""

    args_model: ClassVar[type[BaseModel] | None] = SearchLivingDocsArgs

    def __init__(
        self,
        *,
        docs_service: DocsService,
        project_id: NotBlankStr,
    ) -> None:
        super().__init__(
            name="search_living_docs",
            description=(
                "Search the project's living documentation corpus "
                "(status reports, deliverables, knowledge notes) for "
                "passages relevant to a query."
            ),
            parameters_schema=SearchLivingDocsArgs.model_json_schema(),
            category=ToolCategory.MEMORY,
            action_type=ActionType.MEMORY_READ.value,
        )
        self._docs_service = docs_service
        self._project_id = project_id

    @override
    async def execute(
        self,
        *,
        arguments: dict[str, object],
    ) -> ToolExecutionResult:
        """Dispatch a ``search_living_docs`` invocation to :class:`DocsService`.

        Returns:
            Result of type ``ToolExecutionResult``.
        """
        try:
            parsed = parse_typed("mcp.tool", arguments, SearchLivingDocsArgs)
            logger.info(
                DOC_SEARCH_START,
                project_id=self._project_id,
                limit=parsed.limit,
            )
            doc_types = frozenset(parsed.doc_types) if parsed.doc_types else None
            hits = await self._docs_service.search(
                project_id=self._project_id,
                query=parsed.query,
                doc_types=doc_types,
                limit=parsed.limit,
            )
        except (ValueError, TypeError) as exc:
            logger.warning(
                DOC_SEARCH_FAILED,
                project_id=self._project_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return ToolExecutionResult(
                content=(
                    f"Search failed: invalid argument shape "
                    f"({safe_error_description(exc)})"
                ),
                is_error=True,
            )
        except Exception as exc:
            reraise_critical(exc)
            log_exception_redacted(
                logger, DOC_SEARCH_FAILED, exc, project_id=self._project_id
            )
            return ToolExecutionResult(
                content=(
                    f"Search failed: {type(exc).__name__} "
                    f"({safe_error_description(exc)})"
                ),
                is_error=True,
            )
        logger.info(
            DOC_SEARCH_COMPLETE,
            project_id=self._project_id,
            hit_count=len(hits),
        )
        hit_dicts: list[JsonValue] = [
            {
                "doc_slug": h.doc_slug,
                "doc_type": h.doc_type.value,
                "relevance_score": h.relevance_score,
            }
            for h in hits
        ]
        return ToolExecutionResult(
            content=_format_hits(hits),
            metadata={"hit_count": len(hits), "hits": hit_dicts},
        )


def _format_hits(hits: tuple[DocSearchHit, ...]) -> str:
    """Format hits.

    Returns:
        Result of type ``str``.
    """
    if not hits:
        return "No matching living docs for this project."
    lines: list[str] = []
    for h in hits:
        lines.append(
            f"[{h.doc_type.value}] {h.doc_slug} (score={h.relevance_score:.2f}):"
        )
        lines.append(h.chunk_text)
        lines.append("")
    return "\n".join(lines).rstrip()


__all__ = ["SearchLivingDocsTool"]
