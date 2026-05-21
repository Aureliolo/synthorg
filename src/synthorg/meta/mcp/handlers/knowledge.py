"""Knowledge-substrate domain MCP handlers.

Delegates to :class:`KnowledgeService` via ``app_state.knowledge_service``.
Search / list / get are read-only; ingest / reindex / delete are admin
(call ``require_admin_guardrails`` as the first body statement). Search
chunk text is wrapped via ``wrap_untrusted`` (SEC-1) because corpus
content may carry injected instructions.
"""

from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.core.enums import SourceType
from synthorg.core.types import NotBlankStr
from synthorg.engine.prompt_safety import TAG_MEMORY_ENTRY, wrap_untrusted
from synthorg.knowledge.constants import (
    KNOWLEDGE_LIST_DEFAULT_LIMIT,
    KNOWLEDGE_SEARCH_DEFAULT_LIMIT,
)
from synthorg.knowledge.errors import KnowledgeSourceNotFoundError
from synthorg.meta.mcp.errors import ArgumentValidationError, invalid_argument
from synthorg.meta.mcp.handler_protocol import (
    ToolHandler,  # noqa: TC001 -- PEP 649 annotation
)
from synthorg.meta.mcp.handlers.common import err, ok, require_admin_guardrails
from synthorg.meta.mcp.handlers.common_args import require_arg
from synthorg.meta.mcp.handlers.common_logging import (
    log_handler_argument_invalid,
    log_handler_invoke_failed,
)
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import MCP_HANDLER_INVOKE_SUCCESS

if TYPE_CHECKING:
    from collections.abc import Mapping

    from synthorg.core.agent import AgentIdentity
    from synthorg.knowledge.models import KnowledgeHit

logger = get_logger(__name__)

_TOOL_SEARCH = "synthorg_knowledge_search"
_TOOL_INGEST = "synthorg_knowledge_ingest"
_TOOL_REINDEX = "synthorg_knowledge_reindex"
_TOOL_LIST = "synthorg_knowledge_list"
_TOOL_GET = "synthorg_knowledge_get"
_TOOL_DELETE = "synthorg_knowledge_delete"

_ARG_PROJECT_ID = "project_id"
_ARG_QUERY = "query"
_ARG_LIMIT = "limit"
_ARG_OFFSET = "offset"
_ARG_SOURCE_TYPE = "source_type"
_ARG_URI = "uri"
_ARG_TITLE = "title"
_ARG_SOURCE_ID = "source_id"
_ARG_INCLUDE_GLOBAL = "include_global"
_ARG_STALE_ONLY = "stale_only"

_TY_SOURCE_TYPE = "source_type enum value"
_TY_POS_INT = "positive int"
_TY_NONNEG_INT = "non-negative int"
_TY_BOOL = "boolean"
_TY_OPT_STR = "string or null"


def _require_service(app_state: Any) -> Any:
    svc = getattr(app_state, "knowledge_service", None)
    if svc is None:
        msg = "knowledge service is not wired on app_state in this deployment"
        raise ServiceUnavailableError(msg)
    return svc


def _opt_project_id(arguments: dict[str, Any]) -> NotBlankStr | None:
    raw = arguments.get(_ARG_PROJECT_ID)
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise invalid_argument(_ARG_PROJECT_ID, _TY_OPT_STR)
    return NotBlankStr(raw)


def _source_type(arguments: dict[str, Any]) -> SourceType:
    raw = require_arg(arguments, _ARG_SOURCE_TYPE, str)
    try:
        return SourceType(raw)
    except ValueError as exc:
        raise invalid_argument(_ARG_SOURCE_TYPE, _TY_SOURCE_TYPE) from exc


def _positive_int(arguments: dict[str, Any], key: str, *, default: int) -> int:
    raw = arguments.get(key)
    if raw in (None, ""):
        return default
    if not isinstance(raw, int) or isinstance(raw, bool) or raw < 1:
        raise invalid_argument(key, _TY_POS_INT)
    return raw


def _nonneg_int(arguments: dict[str, Any], key: str, *, default: int) -> int:
    raw = arguments.get(key)
    if raw in (None, ""):
        return default
    if not isinstance(raw, int) or isinstance(raw, bool) or raw < 0:
        raise invalid_argument(key, _TY_NONNEG_INT)
    return raw


def _flag(arguments: dict[str, Any], key: str) -> bool:
    raw = arguments.get(key, False)
    if not isinstance(raw, bool):
        raise invalid_argument(key, _TY_BOOL)
    return raw


def _hit_dict(hit: KnowledgeHit) -> dict[str, Any]:
    """Serialise a hit, wrapping the untrusted chunk text (SEC-1)."""
    return {
        "chunk_text": wrap_untrusted(TAG_MEMORY_ENTRY, hit.chunk_text),
        "relevance_score": hit.relevance_score,
        "citation": hit.citation.model_dump(mode="json"),
    }


async def _knowledge_search(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    try:
        svc = _require_service(app_state)
        project_id = _opt_project_id(arguments)
        query = NotBlankStr(require_arg(arguments, _ARG_QUERY, str))
        limit = _positive_int(
            arguments, _ARG_LIMIT, default=KNOWLEDGE_SEARCH_DEFAULT_LIMIT
        )
        hits = await svc.search(query=query, project_id=project_id, limit=limit)
        logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=_TOOL_SEARCH)
        return ok([_hit_dict(h) for h in hits])
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(_TOOL_SEARCH, exc)
        return err(exc)
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        log_handler_invoke_failed(_TOOL_SEARCH, exc)
        return err(exc)


async def _knowledge_ingest(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    try:
        require_admin_guardrails(arguments, actor)
        svc = _require_service(app_state)
        project_id = _opt_project_id(arguments)
        source_type = _source_type(arguments)
        uri = NotBlankStr(require_arg(arguments, _ARG_URI, str))
        title = NotBlankStr(require_arg(arguments, _ARG_TITLE, str))
        source = await svc.ingest(
            source_type=source_type,
            uri=uri,
            title=title,
            project_id=project_id,
        )
        logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=_TOOL_INGEST)
        return ok(source.model_dump(mode="json"))
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(_TOOL_INGEST, exc)
        return err(exc)
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        log_handler_invoke_failed(_TOOL_INGEST, exc)
        return err(exc)


async def _knowledge_reindex(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    try:
        require_admin_guardrails(arguments, actor)
        svc = _require_service(app_state)
        source_id = NotBlankStr(require_arg(arguments, _ARG_SOURCE_ID, str))
        source = await svc.reindex(source_id)
        logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=_TOOL_REINDEX)
        return ok(source.model_dump(mode="json"))
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(_TOOL_REINDEX, exc)
        return err(exc)
    except KnowledgeSourceNotFoundError as exc:
        log_handler_invoke_failed(_TOOL_REINDEX, exc)
        return err(exc)
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        log_handler_invoke_failed(_TOOL_REINDEX, exc)
        return err(exc)


async def _knowledge_list(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    try:
        svc = _require_service(app_state)
        project_id = _opt_project_id(arguments)
        include_global = _flag(arguments, _ARG_INCLUDE_GLOBAL)
        stale_only = _flag(arguments, _ARG_STALE_ONLY)
        limit = _positive_int(
            arguments, _ARG_LIMIT, default=KNOWLEDGE_LIST_DEFAULT_LIMIT
        )
        offset = _nonneg_int(arguments, _ARG_OFFSET, default=0)
        sources = await svc.list_sources(
            project_id=project_id,
            include_global=include_global,
            stale_only=stale_only,
            limit=limit,
            offset=offset,
        )
        logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=_TOOL_LIST)
        return ok([s.model_dump(mode="json") for s in sources])
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(_TOOL_LIST, exc)
        return err(exc)
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        log_handler_invoke_failed(_TOOL_LIST, exc)
        return err(exc)


async def _knowledge_get(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    try:
        svc = _require_service(app_state)
        source_id = NotBlankStr(require_arg(arguments, _ARG_SOURCE_ID, str))
        source = await svc.get_source(source_id)
        logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=_TOOL_GET)
        return ok(source.model_dump(mode="json"))
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(_TOOL_GET, exc)
        return err(exc)
    except KnowledgeSourceNotFoundError as exc:
        log_handler_invoke_failed(_TOOL_GET, exc)
        return err(exc)
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        log_handler_invoke_failed(_TOOL_GET, exc)
        return err(exc)


async def _knowledge_delete(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    try:
        require_admin_guardrails(arguments, actor)
        svc = _require_service(app_state)
        source_id = NotBlankStr(require_arg(arguments, _ARG_SOURCE_ID, str))
        deleted = await svc.delete_source(source_id)
        logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=_TOOL_DELETE)
        return ok({"source_id": source_id, "deleted": deleted})
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(_TOOL_DELETE, exc)
        return err(exc)
    except KnowledgeSourceNotFoundError as exc:
        log_handler_invoke_failed(_TOOL_DELETE, exc)
        return err(exc)
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        log_handler_invoke_failed(_TOOL_DELETE, exc)
        return err(exc)


KNOWLEDGE_HANDLERS: Mapping[str, ToolHandler] = MappingProxyType(
    {
        "synthorg_knowledge_search": _knowledge_search,
        "synthorg_knowledge_ingest": _knowledge_ingest,
        "synthorg_knowledge_reindex": _knowledge_reindex,
        "synthorg_knowledge_list": _knowledge_list,
        "synthorg_knowledge_get": _knowledge_get,
        "synthorg_knowledge_delete": _knowledge_delete,
    },
)
