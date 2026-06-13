"""``SearchKnowledgeTool`` -- explicit cited corpus retrieval for agents.

Returns knowledge hits whose citations resolve to the exact source
chunk (PDF page, code line span, web offset). Each chunk's text is
wrapped via ``wrap_untrusted`` because ingested corpus content -- from
any source type -- may carry injected instructions.
"""

import builtins
from typing import ClassVar, override

from pydantic import BaseModel, JsonValue

from synthorg.core.boundary import parse_typed
from synthorg.core.types import NotBlankStr
from synthorg.engine.prompt_safety import TAG_MEMORY_ENTRY, wrap_untrusted
from synthorg.knowledge.errors import KnowledgeError
from synthorg.knowledge.models import Citation, KnowledgeHit
from synthorg.knowledge.service import KnowledgeService
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.knowledge import (
    KNOWLEDGE_SEARCH_FAILED,
    KNOWLEDGE_SEARCHED,
)
from synthorg.security.autonomy.enums import ActionType, ToolCategory
from synthorg.tools.base import BaseTool, ToolExecutionResult
from synthorg.tools.knowledge._args import SearchKnowledgeArgs

logger = get_logger(__name__)


class SearchKnowledgeTool(BaseTool):
    """Agent tool that searches the knowledge corpus with citations."""

    args_model: ClassVar[type[BaseModel] | None] = SearchKnowledgeArgs

    def __init__(
        self,
        *,
        service: KnowledgeService,
        project_id: NotBlankStr | None,
    ) -> None:
        super().__init__(
            name="search_knowledge",
            description=(
                "Search the ingested knowledge corpus (PDFs, web pages, "
                "repos) for passages relevant to a query. Each hit carries "
                "a citation that resolves to the exact source location."
            ),
            parameters_schema=SearchKnowledgeArgs.model_json_schema(),
            category=ToolCategory.MEMORY,
            action_type=ActionType.MEMORY_READ.value,
        )
        self._service = service
        self._project_id = project_id

    @override
    async def execute(self, *, arguments: dict[str, object]) -> ToolExecutionResult:
        """Dispatch a ``search_knowledge`` invocation to the service.

        Returns:
            Result of type ``ToolExecutionResult``.

        Raises:
            MemoryError: If the related operation fails.
            RecursionError: If the related operation fails.
        """
        try:
            parsed = parse_typed("mcp.tool", arguments, SearchKnowledgeArgs)
            hits = await self._service.search(
                query=parsed.query,
                project_id=self._project_id,
                limit=parsed.limit,
            )
        except (ValueError, TypeError) as exc:
            logger.warning(
                KNOWLEDGE_SEARCH_FAILED,
                project_id=self._project_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return ToolExecutionResult(
                content=(
                    f"Search failed: invalid arguments ({safe_error_description(exc)})"
                ),
                is_error=True,
            )
        except builtins.MemoryError, RecursionError:
            raise
        except KnowledgeError as exc:
            log_exception_redacted(
                logger, KNOWLEDGE_SEARCH_FAILED, exc, project_id=self._project_id
            )
            return ToolExecutionResult(
                content=f"Search failed: {safe_error_description(exc)}",
                is_error=True,
            )
        logger.info(
            KNOWLEDGE_SEARCHED,
            project_id=self._project_id,
            hit_count=len(hits),
        )
        citations: list[JsonValue] = [_citation_dict(h.citation) for h in hits]
        return ToolExecutionResult(
            content=_format_hits(hits),
            metadata={"hit_count": len(hits), "citations": citations},
        )


def _format_citation(citation: Citation) -> str:
    """Render a concise human-readable source locator.

    Returns:
        Result of type ``str``.
    """
    locator = citation.locator
    if locator.locator_kind == "pdf":
        where = f"page {locator.page}"
    elif locator.locator_kind == "code":
        where = f"{locator.path}:{locator.line_start}-{locator.line_end}"
    elif locator.locator_kind == "ticket":
        where = f"ticket {locator.ticket_id}"
    else:
        where = locator.url
    return f"{citation.title} ({citation.source_type.value}, {where})"


def _citation_dict(citation: Citation) -> dict[str, JsonValue]:
    """Structured citation for tool metadata (programmatic consumers).

    Returns:
        Mapping from ``str`` to ``JsonValue``.
    """
    return {
        "source_id": citation.source_id,
        "chunk_id": citation.chunk_id,
        "source_type": citation.source_type.value,
        "uri": citation.uri,
        "locator_kind": citation.locator.locator_kind,
        "content_hash": citation.content_hash,
    }


def _format_hits(hits: tuple[KnowledgeHit, ...]) -> str:
    """Render hits with citations; wrap each chunk's untrusted text.

    Returns:
        Result of type ``str``.
    """
    if not hits:
        return "No matching knowledge for this query."
    blocks: list[str] = []
    for hit in hits:
        # Citation text (title + locator) is third-party-derived just
        # like the chunk text; wrap both so neither can carry an injection.
        citation = wrap_untrusted(TAG_MEMORY_ENTRY, _format_citation(hit.citation))
        wrapped = wrap_untrusted(TAG_MEMORY_ENTRY, hit.chunk_text)
        blocks.append(f"[{citation}] (score={hit.relevance_score:.2f})\n{wrapped}")
    return "\n\n".join(blocks)


__all__ = ["SearchKnowledgeTool"]
