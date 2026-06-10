"""``SearchBrainTool`` -- explicit project-brain retrieval for agents.

The dedicated path for an agent querying the project's brain directly ("what
risks did we accept around payments"). The transparent RAG path lives on
:class:`ProjectAwareMemoryFacade`, which surfaces brain state alongside other
memories on the normal ``memory.retrieve`` call.
"""

from typing import ClassVar, override

from pydantic import BaseModel, JsonValue

from synthorg.api.boundary import parse_typed
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.engine.prompt_safety import TAG_BRAIN_STATE, wrap_untrusted
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.project_brain import (
    BRAIN_SEARCH_COMPLETE,
    BRAIN_SEARCH_FAILED,
    BRAIN_SEARCH_START,
)
from synthorg.project_brain.models import BrainSearchHit
from synthorg.project_brain.service import ProjectBrainService
from synthorg.security.autonomy.enums import ActionType, ToolCategory
from synthorg.tools.base import BaseTool, ToolExecutionResult
from synthorg.tools.brain._args import SearchBrainArgs

logger = get_logger(__name__)


class SearchBrainTool(BaseTool):
    """Agent tool that searches the project brain for the active project."""

    args_model: ClassVar[type[BaseModel] | None] = SearchBrainArgs

    def __init__(
        self,
        *,
        brain_service: ProjectBrainService,
        project_id: NotBlankStr,
    ) -> None:
        super().__init__(
            name="search_brain",
            description=(
                "Search the project's brain (decisions, open questions, "
                "blockers, risks, dependencies, plan revisions) for entries "
                "relevant to a query."
            ),
            parameters_schema=SearchBrainArgs.model_json_schema(),
            category=ToolCategory.MEMORY,
            action_type=ActionType.MEMORY_READ.value,
        )
        self._brain_service = brain_service
        self._project_id = project_id

    @override
    async def execute(
        self,
        *,
        arguments: dict[str, object],
    ) -> ToolExecutionResult:
        """Dispatch a ``search_brain`` invocation to :class:`ProjectBrainService`.

        Returns:
            Result of type ``ToolExecutionResult``.
        """
        try:
            parsed = parse_typed("mcp.tool", arguments, SearchBrainArgs)
            logger.info(
                BRAIN_SEARCH_START,
                project_id=self._project_id,
                limit=parsed.limit,
            )
            hits = await self._brain_service.query(
                project_id=self._project_id,
                query=parsed.query,
                limit=parsed.limit,
            )
        except (ValueError, TypeError) as exc:
            logger.warning(
                BRAIN_SEARCH_FAILED,
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
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            log_exception_redacted(
                logger, BRAIN_SEARCH_FAILED, exc, project_id=self._project_id
            )
            return ToolExecutionResult(
                content=(
                    f"Search failed: {type(exc).__name__} "
                    f"({safe_error_description(exc)})"
                ),
                is_error=True,
            )
        logger.info(
            BRAIN_SEARCH_COMPLETE,
            project_id=self._project_id,
            hit_count=len(hits),
        )
        hit_dicts: list[JsonValue] = [
            {
                "entry_id": h.entry_id,
                "entry_kind": h.entry_kind.value,
                "relevance_score": h.relevance_score,
            }
            for h in hits
        ]
        return ToolExecutionResult(
            content=_format_hits(hits),
            metadata={"hit_count": len(hits), "hits": hit_dicts},
        )


def _format_hits(hits: tuple[BrainSearchHit, ...]) -> str:
    """Render search hits to agent-readable text.

    The chunk body is attacker-influenceable (an upstream agent may have been
    prompt-injected when it authored the entry), so each one is fenced under
    ``TAG_BRAIN_STATE`` before it reaches the agent's context, matching the
    transparent retrieval path on :class:`ProjectAwareMemoryFacade`.

    Returns:
        A formatted multi-line summary, or a no-results notice.
    """
    if not hits:
        return "No matching brain entries for this project."
    lines: list[str] = []
    for h in hits:
        lines.append(
            f"[{h.entry_kind.value}] {h.entry_id} (score={h.relevance_score:.2f}):"
        )
        lines.append(wrap_untrusted(TAG_BRAIN_STATE, h.chunk_text))
        lines.append("")
    return "\n".join(lines).rstrip()


__all__ = ["SearchBrainTool"]
