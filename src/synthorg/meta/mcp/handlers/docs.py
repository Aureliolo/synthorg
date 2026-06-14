"""Living-documentation domain MCP handlers.

Delegates to :class:`DocsService` via ``app_state.docs_service``. The
write handler is admin-gated at the registry layer (``docs:write`` uses
``admin_tool``); read handlers (``docs:get``, ``docs:list``,
``docs:search``, ``docs:history``) need only the standard read scope.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING

from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.docs_engine.errors import (
    DocCommitError,
    DocIndexError,
    DocNotFoundError,
    DocValidationError,
)
from synthorg.docs_engine.service import DocsService
from synthorg.docs_engine.state import DocsStateSlice
from synthorg.meta.mcp.domains._docs_args import (
    DocsHistoryArgs,
    DocsListArgs,
    DocsReadArgs,
    DocsSearchArgs,
    DocsWriteArgs,
)
from synthorg.meta.mcp.errors import (
    ArgumentValidationError,
    GuardrailViolationError,
)
from synthorg.meta.mcp.handler_protocol import (
    ToolHandler,
)
from synthorg.meta.mcp.handlers._mcp_handler_common import typed_args
from synthorg.meta.mcp.handlers.common import err, ok, require_admin_guardrails
from synthorg.meta.mcp.handlers.common_logging import (
    log_handler_argument_invalid,
    log_handler_guardrail_violated,
    log_handler_invoke_failed,
)
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import MCP_HANDLER_INVOKE_SUCCESS
from synthorg.tools.docs.write_living_doc import _materialise_body

if TYPE_CHECKING:
    from synthorg.api.state import AppState

logger = get_logger(__name__)

_TOOL_DOCS_WRITE = "synthorg_docs_write"
_TOOL_DOCS_GET = "synthorg_docs_get"
_TOOL_DOCS_LIST = "synthorg_docs_list"
_TOOL_DOCS_SEARCH = "synthorg_docs_search"
_TOOL_DOCS_HISTORY = "synthorg_docs_history"


def _require_docs_service(app_state: AppState) -> DocsService:
    """Return the docs service or raise when unavailable.

    Raises:
        ServiceUnavailableError: Raised on the corresponding failure path.
    """
    svc = app_state.slice(DocsStateSlice).service
    if svc is None:
        msg = "docs service is not wired on app_state in this deployment"
        raise ServiceUnavailableError(msg)
    return svc


async def _docs_write(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Return docs write."""
    try:
        require_admin_guardrails(arguments, actor)
        svc = _require_docs_service(app_state)
        args = typed_args(arguments, DocsWriteArgs)
        body = _materialise_body(args.body)
        metadata = await svc.write_doc(
            project_id=args.project_id,
            title=args.title,
            doc_type=args.doc_type,
            author_agent_id=args.author_agent_id,
            body=body,
            tags=args.tags,
            related_task_ids=args.related_task_ids,
            slug=args.slug,
        )
        logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=_TOOL_DOCS_WRITE)
        return ok(metadata.model_dump(mode="json"))
    except GuardrailViolationError as exc:
        log_handler_guardrail_violated(_TOOL_DOCS_WRITE, exc)
        return err(exc)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(_TOOL_DOCS_WRITE, exc)
        return err(exc)
    except (DocCommitError, DocIndexError, DocValidationError) as exc:
        log_handler_invoke_failed(_TOOL_DOCS_WRITE, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(_TOOL_DOCS_WRITE, exc)
        return err(exc)


async def _docs_get(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return docs get."""
    try:
        svc = _require_docs_service(app_state)
        read_args = typed_args(arguments, DocsReadArgs)
        doc = await svc.read_doc(
            project_id=read_args.project_id,
            slug=read_args.slug,
            version=read_args.version,
        )
        logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=_TOOL_DOCS_GET)
        return ok(doc.model_dump(mode="json"))
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(_TOOL_DOCS_GET, exc)
        return err(exc)
    except DocNotFoundError as exc:
        log_handler_invoke_failed(_TOOL_DOCS_GET, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(_TOOL_DOCS_GET, exc)
        return err(exc)


async def _docs_list(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return docs list."""
    try:
        svc = _require_docs_service(app_state)
        args = typed_args(arguments, DocsListArgs)
        summaries = await svc.list_docs(
            project_id=args.project_id,
            doc_type=args.doc_type,
            tag=args.tag,
            limit=args.limit,
            offset=args.offset,
        )
        logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=_TOOL_DOCS_LIST)
        return ok([s.model_dump(mode="json") for s in summaries])
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(_TOOL_DOCS_LIST, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(_TOOL_DOCS_LIST, exc)
        return err(exc)


async def _docs_search(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return docs search."""
    try:
        svc = _require_docs_service(app_state)
        search_args = typed_args(arguments, DocsSearchArgs)
        doc_types = (
            frozenset(search_args.doc_types)
            if search_args.doc_types is not None
            else None
        )
        hits = await svc.search(
            project_id=search_args.project_id,
            query=search_args.query,
            doc_types=doc_types,
            limit=search_args.limit,
        )
        logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=_TOOL_DOCS_SEARCH)
        return ok([h.model_dump(mode="json") for h in hits])
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(_TOOL_DOCS_SEARCH, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(_TOOL_DOCS_SEARCH, exc)
        return err(exc)


async def _docs_history(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return docs history."""
    try:
        svc = _require_docs_service(app_state)
        history_args = typed_args(arguments, DocsHistoryArgs)
        versions = await svc.history(
            project_id=history_args.project_id,
            slug=history_args.slug,
            limit=history_args.limit,
        )
        logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=_TOOL_DOCS_HISTORY)
        return ok([v.model_dump(mode="json") for v in versions])
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(_TOOL_DOCS_HISTORY, exc)
        return err(exc)
    except DocNotFoundError as exc:
        log_handler_invoke_failed(_TOOL_DOCS_HISTORY, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(_TOOL_DOCS_HISTORY, exc)
        return err(exc)


DOCS_HANDLERS: Mapping[str, ToolHandler] = MappingProxyType(
    {
        "synthorg_docs_write": _docs_write,
        "synthorg_docs_get": _docs_get,
        "synthorg_docs_list": _docs_list,
        "synthorg_docs_search": _docs_search,
        "synthorg_docs_history": _docs_history,
    },
)
