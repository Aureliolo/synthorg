"""``WriteBrainEntryTool`` -- agents record or revise project-brain entries.

One tool covers both create and revise: omitting ``entry_id`` appends a new
logical entry at revision 1; supplying it appends the next revision with the
provided field overrides. ``project_id`` and the author are bound from the
per-task execution context by the tool factory; cross-project authority is
checked by callers via the trust seam, not by the tool.
"""

from typing import TYPE_CHECKING, ClassVar, override

from pydantic import BaseModel

from synthorg.api.boundary import parse_typed
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.project_brain import (
    BRAIN_ENTRY_APPEND_FAILED,
    BRAIN_ENTRY_APPENDED,
)
from synthorg.project_brain.constants import BRAIN_WRITE_ACTION_TYPE
from synthorg.security.autonomy.enums import ToolCategory
from synthorg.tools.base import BaseTool, ToolExecutionResult
from synthorg.tools.brain._args import WriteBrainEntryArgs

if TYPE_CHECKING:
    from synthorg.core.types import NotBlankStr
    from synthorg.project_brain.models import BrainEntry
    from synthorg.project_brain.service import ProjectBrainService

logger = get_logger(__name__)


class WriteBrainEntryTool(BaseTool):
    """Agent tool that records or revises a project-brain entry.

    Args:
        brain_service: Engine entry point used to persist the entry.
        project_id: Active project for this tool instance, wired by the
            per-task tool factory from the execution context.
        author_agent_id: The calling agent's identifier, stamped as the
            entry author.
    """

    args_model: ClassVar[type[BaseModel] | None] = WriteBrainEntryArgs

    def __init__(
        self,
        *,
        brain_service: ProjectBrainService,
        project_id: NotBlankStr,
        author_agent_id: NotBlankStr,
    ) -> None:
        super().__init__(
            name="write_brain_entry",
            description=(
                "Record a project-brain entry (decision, open question, "
                "blocker, risk, dependency, or plan revision) for the active "
                "project, or revise an existing one by passing its entry_id. "
                "Each change is a new revision; the full history is retained."
            ),
            parameters_schema=WriteBrainEntryArgs.model_json_schema(),
            category=ToolCategory.OTHER,
            action_type=BRAIN_WRITE_ACTION_TYPE,
        )
        self._brain_service = brain_service
        self._project_id = project_id
        self._author_agent_id = author_agent_id

    @override
    async def execute(
        self,
        *,
        arguments: dict[str, object],
    ) -> ToolExecutionResult:
        """Persist a create or revise of a brain entry.

        Returns:
            Result of type ``ToolExecutionResult``.
        """
        try:
            parsed = parse_typed("mcp.tool", arguments, WriteBrainEntryArgs)
            entry = await self._dispatch(parsed)
        except (ValueError, TypeError) as exc:
            logger.warning(
                BRAIN_ENTRY_APPEND_FAILED,
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
                logger, BRAIN_ENTRY_APPEND_FAILED, exc, project_id=self._project_id
            )
            return ToolExecutionResult(
                content=(
                    f"Write failed: {type(exc).__name__} "
                    f"({safe_error_description(exc)})"
                ),
                is_error=True,
            )
        logger.info(
            BRAIN_ENTRY_APPENDED,
            project_id=self._project_id,
            entry_id=entry.entry_id,
            entry_kind=entry.entry_kind.value,
            revision=entry.revision,
        )
        return ToolExecutionResult(
            content=(
                f"Recorded brain {entry.entry_kind.value} {entry.entry_id} "
                f"r{entry.revision} ({entry.status.value})"
            ),
            metadata={
                "entry_id": entry.entry_id,
                "entry_kind": entry.entry_kind.value,
                "revision": entry.revision,
                "status": entry.status.value,
            },
        )

    async def _dispatch(self, parsed: WriteBrainEntryArgs) -> BrainEntry:
        """Route to append (create) or revise based on the parsed args.

        Returns:
            The persisted entry revision.

        Raises:
            ValueError: When a create is missing a required field.
        """
        if parsed.entry_id is None:
            return await self._create(parsed)
        return await self._revise(parsed, entry_id=parsed.entry_id)

    async def _create(self, parsed: WriteBrainEntryArgs) -> BrainEntry:
        """Append a new logical entry at revision 1.

        Returns:
            The persisted entry.

        Raises:
            ValueError: When ``title``, ``rationale``, ``status``, or ``payload``
                is missing (all required on create).
        """
        if (
            parsed.title is None
            or parsed.rationale is None
            or parsed.status is None
            or parsed.payload is None
        ):
            msg = "create requires title, rationale, status, and payload"
            raise ValueError(msg)
        return await self._brain_service.append_entry(
            project_id=self._project_id,
            title=parsed.title,
            rationale=parsed.rationale,
            status=parsed.status,
            author=self._author_agent_id,
            payload=parsed.payload,
            related_task_ids=parsed.related_task_ids or (),
            related_entry_ids=parsed.related_entry_ids or (),
            supersedes_entry_id=parsed.supersedes_entry_id,
            tags=parsed.tags or (),
            confidence=parsed.confidence,
            citations=parsed.citations or (),
        )

    async def _revise(
        self,
        parsed: WriteBrainEntryArgs,
        *,
        entry_id: NotBlankStr,
    ) -> BrainEntry:
        """Append the next revision of an existing entry.

        Omitted collection fields (``None``) inherit the current revision; an
        explicit empty list clears that collection. Omission never clears, so
        the agent cannot wipe links or tags by simply not mentioning them.

        Returns:
            The persisted new revision.
        """
        return await self._brain_service.revise_entry(
            project_id=self._project_id,
            entry_id=entry_id,
            author=self._author_agent_id,
            status=parsed.status,
            title=parsed.title,
            rationale=parsed.rationale,
            payload=parsed.payload,
            related_task_ids=parsed.related_task_ids,
            related_entry_ids=parsed.related_entry_ids,
            supersedes_entry_id=parsed.supersedes_entry_id,
            tags=parsed.tags,
            citations=parsed.citations,
            confidence=parsed.confidence,
        )


__all__ = ["WriteBrainEntryTool"]
