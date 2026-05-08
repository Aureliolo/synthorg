"""Knowledge Architect memory tools.

Six ``BaseTool`` subclasses backing the ``memory.*`` tool surface for
the Knowledge Architect role: ``memory.guide``, ``memory.search``,
``memory.read``, ``memory.write``, ``memory.delete``,
``memory.browse_wiki``.

Write/delete tools enforce autonomy gating: ``FULL`` disabled,
``SEMI`` requires explicit opt-in, ``SUPERVISED`` / ``LOCKED``
allowed (upstream approval / plan-review gate expected).
"""

from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import BaseModel  # noqa: TC002 -- ClassVar type at runtime

from synthorg.core.enums import (
    AutonomyLevel,
    OrgFactCategory,
    SeniorityLevel,
    ToolCategory,
)
from synthorg.memory.org.models import (
    OrgFactAuthor,
    OrgFactWriteRequest,
    OrgMemoryQuery,
)
from synthorg.memory.tools._args import (
    KnowledgeArchitectBrowseWikiArgs,
    KnowledgeArchitectDeleteArgs,
    KnowledgeArchitectGuideArgs,
    KnowledgeArchitectReadArgs,
    KnowledgeArchitectSearchArgs,
    KnowledgeArchitectWriteArgs,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.memory import (
    KNOWLEDGE_ARCHITECT_BROWSE_WIKI_FAILED,
    KNOWLEDGE_ARCHITECT_DELETE,
    KNOWLEDGE_ARCHITECT_DELETE_FAILED,
    KNOWLEDGE_ARCHITECT_READ_FAILED,
    KNOWLEDGE_ARCHITECT_SEARCH_FAILED,
    KNOWLEDGE_ARCHITECT_WRITE,
    KNOWLEDGE_ARCHITECT_WRITE_DENIED,
    KNOWLEDGE_ARCHITECT_WRITE_FAILED,
)
from synthorg.tools.base import BaseTool, ToolExecutionResult

if TYPE_CHECKING:
    from synthorg.core.types import NotBlankStr
    from synthorg.memory.consolidation.wiki_export import WikiExporter
    from synthorg.memory.org.protocol import OrgMemoryBackend
    from synthorg.memory.org.store import OrgFactStore

logger = get_logger(__name__)


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

    async def execute(
        self,
        *,
        arguments: dict[str, Any],  # noqa: ARG002
    ) -> ToolExecutionResult:
        """Return the mechanics guide."""
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

    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        """Execute org memory search."""
        try:
            category_str = arguments.get("category")
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
                context=arguments["query"],
                limit=arguments.get("limit", 10),
                categories=categories,
            )
            facts = await self._org_backend.query(query)
        except Exception as exc:
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

    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        """Read an org memory entry by ID."""
        entry_id = arguments["entry_id"]
        try:
            query = OrgMemoryQuery(
                context=entry_id,
                limit=100,
            )
            facts = await self._org_backend.query(query)
            match = next(
                (f for f in facts if f.id == entry_id),
                None,
            )
        except Exception as exc:
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


class KnowledgeArchitectWriteTool(BaseTool):
    """``memory.write`` -- write to org memory with autonomy gating.

    Per-autonomy gating policy:

    * ``FULL``       -- disabled (no architect writes).
    * ``SEMI``       -- disabled unless ``architect_writes_enabled`` is
      True in the tool config.
    * ``SUPERVISED`` -- allowed; plan review gate MUST fire upstream
      before constructing this tool.
    * ``LOCKED``     -- allowed; plan review + post-write audit MUST
      fire upstream.

    The tool itself enforces the FULL/SEMI gates.  SUPERVISED/LOCKED
    gating is enforced by the agent runtime before invoking the tool
    (``ApprovalItem`` / plan review infrastructure).
    """

    args_model: ClassVar[type[BaseModel] | None] = KnowledgeArchitectWriteArgs

    def __init__(
        self,
        *,
        org_backend: OrgMemoryBackend,
        agent_id: NotBlankStr,
        autonomy_level: AutonomyLevel,
        architect_writes_enabled: bool = False,
    ) -> None:
        super().__init__(
            name="memory.write",
            description="Write to organizational memory",
            parameters_schema=KnowledgeArchitectWriteArgs.model_json_schema(),
            category=ToolCategory.MEMORY,
        )
        self._org_backend = org_backend
        self._agent_id = agent_id
        self._autonomy_level = autonomy_level
        self._architect_writes_enabled = architect_writes_enabled

    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        """Write to org memory with autonomy gating.

        Enforces FULL autonomy block and SEMI opt-in check at the
        tool boundary.  SUPERVISED and LOCKED gating (plan review +
        audit) is the agent runtime's responsibility.
        """
        if self._autonomy_level == AutonomyLevel.FULL:
            logger.warning(
                KNOWLEDGE_ARCHITECT_WRITE_DENIED,
                agent_id=self._agent_id,
                autonomy=self._autonomy_level.value,
                reason="FULL autonomy disables architect writes",
            )
            return ToolExecutionResult(
                content=(
                    "Write denied: FULL autonomy level "
                    "disables architect writes to org memory"
                ),
                is_error=True,
            )
        if (
            self._autonomy_level == AutonomyLevel.SEMI
            and not self._architect_writes_enabled
        ):
            logger.warning(
                KNOWLEDGE_ARCHITECT_WRITE_DENIED,
                agent_id=self._agent_id,
                autonomy=self._autonomy_level.value,
                reason="SEMI requires architect_writes_enabled opt-in",
            )
            return ToolExecutionResult(
                content=(
                    "Write denied: SEMI autonomy requires explicit "
                    "architect_writes_enabled opt-in"
                ),
                is_error=True,
            )

        try:
            category_str = arguments["category"]
            try:
                OrgFactCategory(category_str)
            except ValueError:
                return ToolExecutionResult(
                    content=f"Invalid category: {category_str!r}",
                    is_error=True,
                )
            request = OrgFactWriteRequest(
                content=arguments["content"],
                category=category_str,
                tags=tuple(arguments.get("tags", ())),
            )
            author = OrgFactAuthor(
                agent_id=self._agent_id,
                seniority=SeniorityLevel.SENIOR,
                is_human=False,
                autonomy_level=self._autonomy_level,
            )
            fact_id = await self._org_backend.write(
                request,
                author=author,
            )
        except Exception as exc:
            safe_error = safe_error_description(exc)
            logger.warning(
                KNOWLEDGE_ARCHITECT_WRITE_FAILED,
                agent_id=self._agent_id,
                category=arguments.get("category"),
                error_type=type(exc).__name__,
                error=safe_error,
            )
            return ToolExecutionResult(
                content=f"Write failed: {safe_error}",
                is_error=True,
            )
        logger.info(
            KNOWLEDGE_ARCHITECT_WRITE,
            agent_id=self._agent_id,
            entry_id=fact_id,
            category=arguments["category"],
            autonomy=self._autonomy_level.value,
        )
        return ToolExecutionResult(
            content=f"Written: {fact_id}",
            is_error=False,
        )


class KnowledgeArchitectDeleteTool(BaseTool):
    """``memory.delete`` -- archive an org memory entry.

    Per-autonomy gating mirrors ``KnowledgeArchitectWriteTool``:
    FULL disabled; SEMI requires explicit opt-in; SUPERVISED/LOCKED
    allowed (upstream approval/plan review gate expected).
    """

    args_model: ClassVar[type[BaseModel] | None] = KnowledgeArchitectDeleteArgs

    def __init__(
        self,
        *,
        org_backend: OrgMemoryBackend,
        fact_store: OrgFactStore | None = None,
        agent_id: NotBlankStr,
        autonomy_level: AutonomyLevel,
        architect_writes_enabled: bool = False,
    ) -> None:
        super().__init__(
            name="memory.delete",
            description="Archive an organizational memory entry",
            parameters_schema=KnowledgeArchitectDeleteArgs.model_json_schema(),
            category=ToolCategory.MEMORY,
        )
        self._org_backend = org_backend
        self._fact_store = fact_store
        self._agent_id = agent_id
        self._autonomy_level = autonomy_level
        self._architect_writes_enabled = architect_writes_enabled

    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        """Delete (archive) an org memory entry.

        Gated by autonomy level: FULL disabled, SEMI opt-in,
        SUPERVISED/LOCKED allowed (upstream approval gate expected).
        Requires ``fact_store`` to perform the actual retraction.
        """
        if self._autonomy_level == AutonomyLevel.FULL:
            logger.warning(
                KNOWLEDGE_ARCHITECT_WRITE_DENIED,
                agent_id=self._agent_id,
                autonomy=self._autonomy_level.value,
                reason="FULL autonomy disables architect deletes",
            )
            return ToolExecutionResult(
                content="Delete denied: FULL autonomy level",
                is_error=True,
            )
        if (
            self._autonomy_level == AutonomyLevel.SEMI
            and not self._architect_writes_enabled
        ):
            logger.warning(
                KNOWLEDGE_ARCHITECT_WRITE_DENIED,
                agent_id=self._agent_id,
                autonomy=self._autonomy_level.value,
                reason="SEMI requires architect_writes_enabled opt-in",
            )
            return ToolExecutionResult(
                content=(
                    "Delete denied: SEMI autonomy requires explicit "
                    "architect_writes_enabled opt-in"
                ),
                is_error=True,
            )
        if self._fact_store is None:
            return ToolExecutionResult(
                content="Delete not available: fact store not configured",
                is_error=True,
            )
        entry_id = arguments["entry_id"]
        try:
            author = OrgFactAuthor(
                agent_id=self._agent_id,
                seniority=SeniorityLevel.SENIOR,
                is_human=False,
            )
            deleted = await self._fact_store.delete(
                fact_id=entry_id,
                author=author,
            )
        except Exception as exc:
            safe_error = safe_error_description(exc)
            logger.warning(
                KNOWLEDGE_ARCHITECT_DELETE_FAILED,
                agent_id=self._agent_id,
                entry_id=entry_id,
                error_type=type(exc).__name__,
                error=safe_error,
            )
            return ToolExecutionResult(
                content=f"Delete failed: {safe_error}",
                is_error=True,
            )
        if not deleted:
            return ToolExecutionResult(
                content=f"Entry {entry_id!r} not found or already archived.",
                is_error=True,
            )
        logger.info(
            KNOWLEDGE_ARCHITECT_DELETE,
            agent_id=self._agent_id,
            entry_id=entry_id,
            autonomy=self._autonomy_level.value,
        )
        return ToolExecutionResult(
            content=f"Archived: {entry_id}",
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

    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        """Trigger wiki export and return summary.

        The ``include_raw`` argument controls whether the raw-tier
        count is surfaced in the human-readable summary.  Raw
        artifact content is always exported; the flag only toggles
        how the summary is presented to the agent.
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
