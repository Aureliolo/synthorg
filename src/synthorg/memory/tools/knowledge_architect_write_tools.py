"""Mutating Knowledge Architect memory tools.

Two ``BaseTool`` subclasses backing the write surface for the
Knowledge Architect role: ``memory.write`` and ``memory.delete``.

Both enforce autonomy gating: ``FULL`` disabled, ``SEMI`` requires
explicit opt-in, ``SUPERVISED`` / ``LOCKED`` allowed (upstream
approval / plan-review gate expected).
"""

from typing import ClassVar, override

from pydantic import BaseModel

from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.hr.seniority import SeniorityLevel
from synthorg.memory.enums import OrgFactCategory
from synthorg.memory.org.models import (
    OrgFactAuthor,
    OrgFactWriteRequest,
)
from synthorg.memory.org.protocol import OrgMemoryBackend
from synthorg.memory.tools._args import (
    KnowledgeArchitectDeleteArgs,
    KnowledgeArchitectWriteArgs,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.memory import (
    KNOWLEDGE_ARCHITECT_DELETE,
    KNOWLEDGE_ARCHITECT_DELETE_FAILED,
    KNOWLEDGE_ARCHITECT_WRITE,
    KNOWLEDGE_ARCHITECT_WRITE_DENIED,
    KNOWLEDGE_ARCHITECT_WRITE_FAILED,
)
from synthorg.persistence.memory_protocol import OrgFactRepository
from synthorg.security.autonomy.enums import ToolCategory
from synthorg.tools.base import BaseTool, ToolExecutionResult

logger = get_logger(__name__)


def _check_architect_autonomy(
    *,
    autonomy_level: AutonomyLevel,
    architect_writes_enabled: bool,
    agent_id: NotBlankStr,
    action: str,
) -> ToolExecutionResult | None:
    """Enforce the FULL/SEMI autonomy gate shared by the write tools.

    ``FULL`` disables architect mutations outright; ``SEMI`` requires
    the explicit ``architect_writes_enabled`` opt-in.  ``SUPERVISED``
    and ``LOCKED`` pass here (their gating fires in the agent runtime).

    Args:
        autonomy_level: Active autonomy level for the agent.
        architect_writes_enabled: SEMI opt-in flag from the tool config.
        agent_id: Agent identifier for log context.
        action: Mutation verb (``"write"`` / ``"delete"``) for messages.

    Returns:
        A denial ``ToolExecutionResult`` when blocked, else ``None``.
    """
    if autonomy_level == AutonomyLevel.FULL:
        logger.warning(
            KNOWLEDGE_ARCHITECT_WRITE_DENIED,
            agent_id=agent_id,
            autonomy=autonomy_level.value,
            reason=f"FULL autonomy disables architect {action}s",
        )
        return ToolExecutionResult(
            content=(
                f"{action.capitalize()} denied: FULL autonomy level "
                f"disables architect {action}s to org memory"
            ),
            is_error=True,
        )
    if autonomy_level == AutonomyLevel.SEMI and not architect_writes_enabled:
        logger.warning(
            KNOWLEDGE_ARCHITECT_WRITE_DENIED,
            agent_id=agent_id,
            autonomy=autonomy_level.value,
            reason="SEMI requires architect_writes_enabled opt-in",
        )
        return ToolExecutionResult(
            content=(
                f"{action.capitalize()} denied: SEMI autonomy requires "
                "explicit architect_writes_enabled opt-in"
            ),
            is_error=True,
        )
    return None


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

    @override
    async def execute(
        self,
        *,
        arguments: dict[str, object],
    ) -> ToolExecutionResult:
        """Write to org memory with autonomy gating.

        Enforces FULL autonomy block and SEMI opt-in check at the
        tool boundary.  SUPERVISED and LOCKED gating (plan review +
        audit) is the agent runtime's responsibility.

        Returns:
            Result of type ``ToolExecutionResult``.
        """
        denial = _check_architect_autonomy(
            autonomy_level=self._autonomy_level,
            architect_writes_enabled=self._architect_writes_enabled,
            agent_id=self._agent_id,
            action="write",
        )
        if denial is not None:
            return denial

        try:
            args = KnowledgeArchitectWriteArgs.model_validate(arguments)
            category_str = args.category
            try:
                category = OrgFactCategory(category_str)
            except ValueError:
                return ToolExecutionResult(
                    content=f"Invalid category: {category_str!r}",
                    is_error=True,
                )
            request = OrgFactWriteRequest(
                content=args.content,
                category=category,
                tags=args.tags,
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
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
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
            category=args.category,
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
        fact_store: OrgFactRepository | None = None,
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

    @override
    async def execute(
        self,
        *,
        arguments: dict[str, object],
    ) -> ToolExecutionResult:
        """Delete (archive) an org memory entry.

        Gated by autonomy level: FULL disabled, SEMI opt-in,
        SUPERVISED/LOCKED allowed (upstream approval gate expected).
        Requires ``fact_store`` to perform the actual retraction.

        Returns:
            Result of type ``ToolExecutionResult``.
        """
        denial = _check_architect_autonomy(
            autonomy_level=self._autonomy_level,
            architect_writes_enabled=self._architect_writes_enabled,
            agent_id=self._agent_id,
            action="delete",
        )
        if denial is not None:
            return denial
        if self._fact_store is None:
            return ToolExecutionResult(
                content="Delete not available: fact store not configured",
                is_error=True,
            )
        try:
            args = KnowledgeArchitectDeleteArgs.model_validate(arguments)
            entry_id = args.entry_id
            author = OrgFactAuthor(
                agent_id=self._agent_id,
                seniority=SeniorityLevel.SENIOR,
                is_human=False,
            )
            deleted = await self._fact_store.delete(
                entry_id,
                author=author,
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            safe_error = safe_error_description(exc)
            logger.warning(
                KNOWLEDGE_ARCHITECT_DELETE_FAILED,
                agent_id=self._agent_id,
                entry_id=arguments.get("entry_id"),
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
