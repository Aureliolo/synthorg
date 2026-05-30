"""Long-horizon project-brain domain MCP handlers.

Delegates to :class:`ProjectBrainService` via the project-brain state slice.
Get / list / query / history are read-only; append / resolve / supersede /
clear-blocker are admin (each handler calls ``require_admin_guardrails`` as the
first body statement). Each handler re-validates the (already invoker-validated)
arguments through its typed args model for typed access to the discriminated
payload, then routes through the service.
"""

from types import MappingProxyType
from typing import TYPE_CHECKING, Any, cast

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.meta.mcp.domains._brain_args import (
    BrainAppendArgs,
    BrainClearBlockerArgs,
    BrainGetArgs,
    BrainHistoryArgs,
    BrainListArgs,
    BrainQueryArgs,
    BrainResolveArgs,
    BrainSupersedeArgs,
)
from synthorg.meta.mcp.errors import ArgumentValidationError
from synthorg.meta.mcp.handler_protocol import ToolHandler
from synthorg.meta.mcp.handlers.common import err, ok, require_admin_guardrails
from synthorg.meta.mcp.handlers.common_logging import (
    log_handler_argument_invalid,
    log_handler_invoke_failed,
)
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import MCP_HANDLER_INVOKE_SUCCESS
from synthorg.project_brain.errors import (
    BrainEntryNotFoundError,
    BrainEntryRevisionConflictError,
    BrainEntryValidationError,
)
from synthorg.project_brain.models import BrainEntry
from synthorg.project_brain.state import ProjectBrainStateSlice

if TYPE_CHECKING:
    from collections.abc import Mapping

    from synthorg.core.agent import AgentIdentity
    from synthorg.project_brain.service import ProjectBrainService

logger = get_logger(__name__)

_TOOL_APPEND = "synthorg_brain_append"
_TOOL_RESOLVE = "synthorg_brain_resolve"
_TOOL_SUPERSEDE = "synthorg_brain_supersede"
_TOOL_CLEAR_BLOCKER = "synthorg_brain_clear_blocker"
_TOOL_GET = "synthorg_brain_get"
_TOOL_LIST = "synthorg_brain_list"
_TOOL_QUERY = "synthorg_brain_query"
_TOOL_HISTORY = "synthorg_brain_history"

_ARG_PAYLOAD = "payload"
_CREATE_REQUIRED = "title, rationale, status, and payload (create)"


def _require_brain_service(app_state: Any) -> ProjectBrainService:
    """Return the project-brain service or raise when unavailable.

    Returns:
        The wired :class:`ProjectBrainService`.

    Raises:
        ServiceUnavailableError: When the brain service is not wired.
    """
    svc = app_state.slice(ProjectBrainStateSlice).service
    if svc is None:
        msg = "project brain service is not wired on app_state in this deployment"
        raise ServiceUnavailableError(msg)
    return cast("ProjectBrainService", svc)


async def _append_entry(svc: ProjectBrainService, args: BrainAppendArgs) -> BrainEntry:
    """Route a ``brain:append`` call to create or revise.

    Returns:
        The persisted entry revision.

    Raises:
        ArgumentValidationError: When a create is missing a required field.
    """
    if args.entry_id is None:
        if (
            args.title is None
            or args.rationale is None
            or args.status is None
            or args.payload is None
        ):
            raise ArgumentValidationError(_ARG_PAYLOAD, _CREATE_REQUIRED)
        return await svc.append_entry(
            project_id=args.project_id,
            title=args.title,
            rationale=args.rationale,
            status=args.status,
            author=args.author,
            payload=args.payload,
            related_task_ids=args.related_task_ids,
            related_entry_ids=args.related_entry_ids,
            supersedes_entry_id=args.supersedes_entry_id,
            tags=args.tags,
            confidence=args.confidence,
            citations=args.citations,
        )
    return await svc.revise_entry(
        project_id=args.project_id,
        entry_id=args.entry_id,
        author=args.author,
        status=args.status,
        title=args.title,
        rationale=args.rationale,
        payload=args.payload,
        related_task_ids=args.related_task_ids or None,
        related_entry_ids=args.related_entry_ids or None,
        supersedes_entry_id=args.supersedes_entry_id,
        tags=args.tags or None,
        citations=args.citations or None,
    )


async def _brain_append(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    """Return the created or revised brain entry envelope (admin)."""
    try:
        require_admin_guardrails(arguments, actor)
        svc = _require_brain_service(app_state)
        args = BrainAppendArgs.model_validate(arguments)
        entry = await _append_entry(svc, args)
        logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=_TOOL_APPEND)
        return ok(entry.model_dump(mode="json"))
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(_TOOL_APPEND, exc)
        return err(exc)
    except (BrainEntryValidationError, BrainEntryRevisionConflictError) as exc:
        log_handler_invoke_failed(_TOOL_APPEND, exc)
        return err(exc)
    except BrainEntryNotFoundError as exc:
        log_handler_invoke_failed(_TOOL_APPEND, exc)
        return err(exc)
    except Exception as exc:
        reraise_critical(exc)
        log_handler_invoke_failed(_TOOL_APPEND, exc)
        return err(exc)


async def _brain_resolve(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    """Return the resolved-entry envelope for a question or dependency (admin)."""
    try:
        require_admin_guardrails(arguments, actor)
        svc = _require_brain_service(app_state)
        args = BrainResolveArgs.model_validate(arguments)
        entry = await svc.resolve(
            project_id=args.project_id,
            entry_id=args.entry_id,
            author=args.author,
            answer=args.answer,
        )
        logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=_TOOL_RESOLVE)
        return ok(entry.model_dump(mode="json"))
    except BrainEntryValidationError as exc:
        log_handler_invoke_failed(_TOOL_RESOLVE, exc)
        return err(exc)
    except BrainEntryNotFoundError as exc:
        log_handler_invoke_failed(_TOOL_RESOLVE, exc)
        return err(exc)
    except Exception as exc:
        reraise_critical(exc)
        log_handler_invoke_failed(_TOOL_RESOLVE, exc)
        return err(exc)


async def _brain_supersede(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    """Return the superseded decision or plan-revision envelope (admin)."""
    try:
        require_admin_guardrails(arguments, actor)
        svc = _require_brain_service(app_state)
        args = BrainSupersedeArgs.model_validate(arguments)
        entry = await svc.supersede(
            project_id=args.project_id,
            entry_id=args.entry_id,
            by_entry_id=args.by_entry_id,
            author=args.author,
        )
        logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=_TOOL_SUPERSEDE)
        return ok(entry.model_dump(mode="json"))
    except BrainEntryValidationError as exc:
        log_handler_invoke_failed(_TOOL_SUPERSEDE, exc)
        return err(exc)
    except BrainEntryNotFoundError as exc:
        log_handler_invoke_failed(_TOOL_SUPERSEDE, exc)
        return err(exc)
    except Exception as exc:
        reraise_critical(exc)
        log_handler_invoke_failed(_TOOL_SUPERSEDE, exc)
        return err(exc)


async def _brain_clear_blocker(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    """Return the cleared-blocker envelope (admin)."""
    try:
        require_admin_guardrails(arguments, actor)
        svc = _require_brain_service(app_state)
        args = BrainClearBlockerArgs.model_validate(arguments)
        entry = await svc.clear_blocker(
            project_id=args.project_id,
            entry_id=args.entry_id,
            author=args.author,
            resolution=args.resolution,
        )
        logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=_TOOL_CLEAR_BLOCKER)
        return ok(entry.model_dump(mode="json"))
    except BrainEntryValidationError as exc:
        log_handler_invoke_failed(_TOOL_CLEAR_BLOCKER, exc)
        return err(exc)
    except BrainEntryNotFoundError as exc:
        log_handler_invoke_failed(_TOOL_CLEAR_BLOCKER, exc)
        return err(exc)
    except Exception as exc:
        reraise_critical(exc)
        log_handler_invoke_failed(_TOOL_CLEAR_BLOCKER, exc)
        return err(exc)


async def _brain_get(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return one brain entry envelope, latest or at an exact revision (read)."""
    try:
        svc = _require_brain_service(app_state)
        args = BrainGetArgs.model_validate(arguments)
        entry = await svc.get_entry(
            project_id=args.project_id,
            entry_id=args.entry_id,
            revision=args.revision,
        )
        logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=_TOOL_GET)
        return ok(entry.model_dump(mode="json"))
    except BrainEntryNotFoundError as exc:
        log_handler_invoke_failed(_TOOL_GET, exc)
        return err(exc)
    except Exception as exc:
        reraise_critical(exc)
        log_handler_invoke_failed(_TOOL_GET, exc)
        return err(exc)


async def _brain_list(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return the current-state projection envelope for a project (read)."""
    try:
        svc = _require_brain_service(app_state)
        args = BrainListArgs.model_validate(arguments)
        summaries = await svc.list_current(
            project_id=args.project_id,
            entry_kind=args.entry_kind,
            status=args.status,
            tag=args.tag,
            author=args.author,
            related_task_id=args.related_task_id,
            limit=args.limit,
            offset=args.offset,
        )
        logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=_TOOL_LIST)
        return ok([s.model_dump(mode="json") for s in summaries])
    except Exception as exc:
        reraise_critical(exc)
        log_handler_invoke_failed(_TOOL_LIST, exc)
        return err(exc)


async def _brain_query(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return semantic-search hits across a project's indexed brain (read)."""
    try:
        svc = _require_brain_service(app_state)
        args = BrainQueryArgs.model_validate(arguments)
        hits = await svc.query(
            project_id=args.project_id,
            query=args.query,
            limit=args.limit,
        )
        logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=_TOOL_QUERY)
        return ok([h.model_dump(mode="json") for h in hits])
    except Exception as exc:
        reraise_critical(exc)
        log_handler_invoke_failed(_TOOL_QUERY, exc)
        return err(exc)


async def _brain_history(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return the structured revision-chain envelope of one brain entry (read)."""
    try:
        svc = _require_brain_service(app_state)
        args = BrainHistoryArgs.model_validate(arguments)
        revisions = await svc.history(
            project_id=args.project_id,
            entry_id=args.entry_id,
            limit=args.limit,
        )
        logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=_TOOL_HISTORY)
        return ok([r.model_dump(mode="json") for r in revisions])
    except BrainEntryNotFoundError as exc:
        log_handler_invoke_failed(_TOOL_HISTORY, exc)
        return err(exc)
    except Exception as exc:
        reraise_critical(exc)
        log_handler_invoke_failed(_TOOL_HISTORY, exc)
        return err(exc)


BRAIN_HANDLERS: Mapping[str, ToolHandler] = MappingProxyType(
    {
        _TOOL_APPEND: _brain_append,
        _TOOL_RESOLVE: _brain_resolve,
        _TOOL_SUPERSEDE: _brain_supersede,
        _TOOL_CLEAR_BLOCKER: _brain_clear_blocker,
        _TOOL_GET: _brain_get,
        _TOOL_LIST: _brain_list,
        _TOOL_QUERY: _brain_query,
        _TOOL_HISTORY: _brain_history,
    },
)
