"""Read-only Knowledge Architect memory tools.

Four ``BaseTool`` subclasses backing the read surface for the
Knowledge Architect role: ``memory.guide``, ``memory.search``,
``memory.read``, ``memory.browse_wiki``.  None mutate org memory, so
they carry no autonomy gating.
"""

from typing import ClassVar, Final, override

from pydantic import BaseModel

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.memory.consolidation.wiki_export import WikiExporter
from synthorg.memory.enums import OrgFactCategory
from synthorg.memory.org.models import OrgMemoryQuery
from synthorg.memory.org.protocol import OrgMemoryBackend
from synthorg.memory.tools._args import (
    KnowledgeArchitectBrowseWikiArgs,
    KnowledgeArchitectGuideArgs,
    KnowledgeArchitectReadArgs,
    KnowledgeArchitectSearchArgs,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.memory import (
    KNOWLEDGE_ARCHITECT_BROWSE_WIKI_FAILED,
    KNOWLEDGE_ARCHITECT_READ_FAILED,
    KNOWLEDGE_ARCHITECT_SEARCH_FAILED,
)
from synthorg.security.autonomy.enums import ToolCategory
from synthorg.tools.base import BaseTool, ToolExecutionResult

logger = get_logger(__name__)

# Org memory exposes no direct get-by-id, so ``memory.read`` queries by
# context and filters; this caps how many candidates it scans per lookup.
_READ_BY_ID_SCAN_LIMIT: Final[int] = 100


_GUIDE_TEXT = (
    "Knowledge Architect Memory Tools:\n"
    "- memory.guide: This help text\n"
    "- memory.search: Search org memory by query + category\n"
    "- memory.read: Read a specific entry by ID\n"
    "- memory.write: Create/update extended knowledge (ADRs, "
    "procedures, style guides)\n"
    "- memory.delete: Archive an entry (soft delete via MVCC)\n"
    "- memory.browse_wiki: Export and browse memory as wiki\n\n"
    "Core policy writes always require human approval."
)


class KnowledgeArchitectGuideTool(BaseTool):
    """``memory.guide`` -- returns mechanics doc for the architect."""

    args_model: ClassVar[type[BaseModel] | None] = KnowledgeArchitectGuideArgs

    def __init__(self) -> None:
        super().__init__(
            name="memory.guide",
            description="Returns memory tools guide for the architect",
            parameters_schema=KnowledgeArchitectGuideArgs.model_json_schema(),
            category=ToolCategory.MEMORY,
        )

    @override
    async def execute(
        self,
        *,
        arguments: dict[str, object],
    ) -> ToolExecutionResult:
        """Return the mechanics guide.

        Returns:
            Result of type ``ToolExecutionResult``.
        """
        return ToolExecutionResult(content=_GUIDE_TEXT, is_error=False)


class KnowledgeArchitectSearchTool(BaseTool):
    """``memory.search`` -- search org memory."""

    args_model: ClassVar[type[BaseModel] | None] = KnowledgeArchitectSearchArgs

    def __init__(
        self,
        *,
        org_backend: OrgMemoryBackend,
    ) -> None:
        super().__init__(
            name="memory.search",
            description="Search organizational memory",
            parameters_schema=KnowledgeArchitectSearchArgs.model_json_schema(),
            category=ToolCategory.MEMORY,
        )
        self._org_backend = org_backend

    @override
    async def execute(
        self,
        *,
        arguments: dict[str, object],
    ) -> ToolExecutionResult:
        """Execute org memory search.

        Returns:
            Result of type ``ToolExecutionResult``.
        """
        try:
            args = KnowledgeArchitectSearchArgs.model_validate(arguments)
            category_str = args.category
            categories = None
            if category_str:
                try:
                    categories = frozenset({OrgFactCategory(category_str)})
                except ValueError:
                    return ToolExecutionResult(
                        content=f"Invalid category: {category_str!r}",
                        is_error=True,
                    )
            query = OrgMemoryQuery(
                context=args.query,
                limit=args.limit,
                categories=categories,
            )
            facts = await self._org_backend.query(query)
        except Exception as exc:
            reraise_critical(exc)
            safe_error = safe_error_description(exc)
            logger.warning(
                KNOWLEDGE_ARCHITECT_SEARCH_FAILED,
                error_type=type(exc).__name__,
                error=safe_error,
            )
            return ToolExecutionResult(
                content=f"Search failed: {safe_error}",
                is_error=True,
            )
        if not facts:
            return ToolExecutionResult(
                content="No results found.",
                is_error=False,
            )
        lines = [f"[{f.id}] ({f.category.value}) {f.content}" for f in facts]
        return ToolExecutionResult(
            content="\n".join(lines),
            is_error=False,
        )


class KnowledgeArchitectReadTool(BaseTool):
    """``memory.read`` -- read a specific org memory entry."""

    args_model: ClassVar[type[BaseModel] | None] = KnowledgeArchitectReadArgs

    def __init__(
        self,
        *,
        org_backend: OrgMemoryBackend,
    ) -> None:
        super().__init__(
            name="memory.read",
            description="Read a specific organizational memory entry",
            parameters_schema=KnowledgeArchitectReadArgs.model_json_schema(),
            category=ToolCategory.MEMORY,
        )
        self._org_backend = org_backend

    @override
    async def execute(
        self,
        *,
        arguments: dict[str, object],
    ) -> ToolExecutionResult:
        """Read an org memory entry by ID.

        Returns:
            Result of type ``ToolExecutionResult``.
        """
        args = KnowledgeArchitectReadArgs.model_validate(arguments)
        entry_id = args.entry_id
        try:
            query = OrgMemoryQuery(
                context=entry_id,
                limit=_READ_BY_ID_SCAN_LIMIT,
            )
            facts = await self._org_backend.query(query)
            match = next(
                (f for f in facts if f.id == entry_id),
                None,
            )
        except Exception as exc:
            reraise_critical(exc)
            safe_error = safe_error_description(exc)
            logger.warning(
                KNOWLEDGE_ARCHITECT_READ_FAILED,
                entry_id=entry_id,
                error_type=type(exc).__name__,
                error=safe_error,
            )
            return ToolExecutionResult(
                content=f"Read failed: {safe_error}",
                is_error=True,
            )
        if match is None:
            return ToolExecutionResult(
                content=f"Entry {entry_id!r} not found.",
                is_error=True,
            )
        return ToolExecutionResult(
            content=(
                f"ID: {match.id}\n"
                f"Category: {match.category.value}\n"
                f"Content: {match.content}"
            ),
            is_error=False,
        )


class KnowledgeArchitectBrowseWikiTool(BaseTool):
    """``memory.browse_wiki`` -- export and browse wiki."""

    args_model: ClassVar[type[BaseModel] | None] = KnowledgeArchitectBrowseWikiArgs

    def __init__(
        self,
        *,
        wiki_exporter: WikiExporter | None = None,
        agent_id: NotBlankStr,
    ) -> None:
        super().__init__(
            name="memory.browse_wiki",
            description="Export and browse memory as wiki",
            parameters_schema=KnowledgeArchitectBrowseWikiArgs.model_json_schema(),
            category=ToolCategory.MEMORY,
        )
        self._wiki_exporter = wiki_exporter
        self._agent_id = agent_id

    @override
    async def execute(
        self,
        *,
        arguments: dict[str, object],
    ) -> ToolExecutionResult:
        """Trigger wiki export and return summary.

        The ``include_raw`` argument controls whether the raw-tier
        count is surfaced in the human-readable summary.  Raw
        artifact content is always exported; the flag only toggles
        how the summary is presented to the agent.

        Returns:
            Result of type ``ToolExecutionResult``.
        """
        include_raw = bool(arguments.get("include_raw", False))
        if self._wiki_exporter is None:
            return ToolExecutionResult(
                content="Wiki export is not configured.",
                is_error=True,
            )
        try:
            result = await self._wiki_exporter.export(self._agent_id)
        except Exception as exc:
            reraise_critical(exc)
            safe_error = safe_error_description(exc)
            logger.warning(
                KNOWLEDGE_ARCHITECT_BROWSE_WIKI_FAILED,
                agent_id=self._agent_id,
                error_type=type(exc).__name__,
                error=safe_error,
            )
            return ToolExecutionResult(
                content=f"Wiki export failed: {safe_error}",
                is_error=True,
            )
        lines = ["Wiki exported:"]
        if include_raw:
            lines.append(f"- Raw entries: {result.raw_count}")
        lines.append(f"- Compressed entries: {result.compressed_count}")
        lines.append(f"- Location: {result.export_root}")
        return ToolExecutionResult(
            content="\n".join(lines),
            is_error=False,
        )
