"""``SearchLivingDocsTool`` -- explicit doc-only retrieval for agents.

This is the dedicated tool path called when an agent wants to query
the project's living-docs corpus directly (e.g. "list deliverables
tagged checkout"). The transparent RAG path lives on
:class:`ProjectAwareMemoryFacade` and surfaces docs alongside other
memories on the normal ``memory.retrieve`` call.
"""

from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import BaseModel  # noqa: TC002 -- ClassVar runtime ref

from synthorg.api.boundary import parse_typed
from synthorg.core.enums import (
    ActionType,
    ToolCategory,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.docs import (
    DOC_SEARCH_COMPLETE,
    DOC_SEARCH_FAILED,
    DOC_SEARCH_START,
)
from synthorg.tools.base import BaseTool, ToolExecutionResult
from synthorg.tools.docs._args import SearchLivingDocsArgs

if TYPE_CHECKING:
    from synthorg.core.types import NotBlankStr
    from synthorg.docs_engine.models import DocSearchHit
    from synthorg.docs_engine.service import DocsService

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

    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        """Dispatch a ``search_living_docs`` invocation to :class:`DocsService`."""
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
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.error(
                DOC_SEARCH_FAILED,
                project_id=self._project_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
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
        return ToolExecutionResult(
            content=_format_hits(hits),
            metadata={
                "hit_count": len(hits),
                "hits": tuple(
                    {
                        "doc_slug": h.doc_slug,
                        "doc_type": h.doc_type.value,
                        "relevance_score": h.relevance_score,
                    }
                    for h in hits
                ),
            },
        )


def _format_hits(hits: tuple[DocSearchHit, ...]) -> str:
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
