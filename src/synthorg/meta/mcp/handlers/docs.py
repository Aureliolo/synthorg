"""Living-documentation domain MCP handlers.

Delegates to :class:`DocsService` via ``app_state.docs_service``. The
write handler is admin-gated at the registry layer (``docs:write`` uses
``admin_tool``); read handlers (``docs:get``, ``docs:list``,
``docs:search``, ``docs:history``) need only the standard read scope.
"""

from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.core.enums import DocType
from synthorg.core.types import NotBlankStr
from synthorg.docs_engine.constants import (
    DOCS_HISTORY_DEFAULT_LIMIT,
    DOCS_LIST_DEFAULT_LIMIT,
    DOCS_SEARCH_DEFAULT_LIMIT,
)
from synthorg.docs_engine.errors import (
    DocCommitError,
    DocIndexError,
    DocNotFoundError,
    DocValidationError,
)
from synthorg.docs_engine.state import DocsStateSlice
from synthorg.meta.mcp.errors import ArgumentValidationError
from synthorg.meta.mcp.handler_protocol import (
    ToolHandler,
)
from synthorg.meta.mcp.handlers.common import err, ok, require_admin_guardrails
from synthorg.meta.mcp.handlers.common_args import require_arg
from synthorg.meta.mcp.handlers.common_logging import (
    log_handler_argument_invalid,
    log_handler_invoke_failed,
)
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import MCP_HANDLER_INVOKE_SUCCESS
from synthorg.tools.docs._args import (
    WriteLivingDocBlockArg,
    parse_block_arg,
)
from synthorg.tools.docs.write_living_doc import _materialise_body

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from synthorg.core.agent import AgentIdentity

logger = get_logger(__name__)

_TOOL_DOCS_WRITE = "synthorg_docs_write"
_TOOL_DOCS_GET = "synthorg_docs_get"
_TOOL_DOCS_LIST = "synthorg_docs_list"
_TOOL_DOCS_SEARCH = "synthorg_docs_search"
_TOOL_DOCS_HISTORY = "synthorg_docs_history"

_ARG_PROJECT_ID = "project_id"
_ARG_SLUG = "slug"
_ARG_TITLE = "title"
_ARG_DOC_TYPE = "doc_type"
_ARG_AUTHOR = "author_agent_id"
_ARG_BODY = "body"
_ARG_TAGS = "tags"
_ARG_TAG = "tag"
_ARG_RELATED = "related_task_ids"
_ARG_VERSION = "version"
_ARG_QUERY = "query"
_ARG_DOC_TYPES = "doc_types"
_ARG_LIMIT = "limit"
_ARG_OFFSET = "offset"

_TY_DOC_TYPE = "doc_type enum value"
_TY_STR_SEQ = "sequence of strings"
_TY_BLOCK_LIST = "non-empty list of block dicts"
_TY_POS_INT = "positive int"
_TY_NONNEG_INT = "non-negative int"
_TY_OPT_STR = "string or null"


def _require_docs_service(app_state: Any) -> Any:
    """Return the docs service or raise when unavailable.

    Raises:
        ServiceUnavailableError: Raised on the corresponding failure path.
    """
    svc = app_state.slice(DocsStateSlice).service
    if svc is None:
        msg = "docs service is not wired on app_state in this deployment"
        raise ServiceUnavailableError(msg)
    return svc


def _parse_doc_type(arguments: dict[str, Any], key: str) -> DocType:
    """Return parse doc type.

    Raises:
        ArgumentValidationError: Raised on the corresponding failure path.
    """
    raw = require_arg(arguments, key, str)
    try:
        return DocType(raw)
    except ValueError as exc:
        raise ArgumentValidationError(key, _TY_DOC_TYPE) from exc


def _parse_opt_doc_type(arguments: dict[str, Any], key: str) -> DocType | None:
    """Return parse opt doc type.

    Raises:
        ArgumentValidationError: Raised on the corresponding failure path.
    """
    raw = arguments.get(key)
    if raw is None or raw == "":
        return None
    if not isinstance(raw, str):
        raise ArgumentValidationError(key, _TY_DOC_TYPE)
    try:
        return DocType(raw)
    except ValueError as exc:
        raise ArgumentValidationError(key, _TY_DOC_TYPE) from exc


def _parse_str_tuple(arguments: dict[str, Any], key: str) -> tuple[NotBlankStr, ...]:
    """Return parse str tuple.

    Raises:
        ArgumentValidationError: Raised on the corresponding failure path.
    """
    raw = arguments.get(key, ())
    if isinstance(raw, str):
        raise ArgumentValidationError(key, _TY_STR_SEQ)
    try:
        items: Sequence[Any] = tuple(raw)
    except TypeError as exc:
        raise ArgumentValidationError(key, _TY_STR_SEQ) from exc
    out: list[NotBlankStr] = []
    for item in items:
        if not isinstance(item, str) or not item.strip():
            raise ArgumentValidationError(key, _TY_STR_SEQ)
        out.append(NotBlankStr(item))
    return tuple(out)


def _parse_block_list(
    arguments: dict[str, Any],
) -> tuple[WriteLivingDocBlockArg, ...]:
    """Return parse block list.

    Raises:
        ArgumentValidationError: Raised on the corresponding failure path.
    """
    raw = arguments.get(_ARG_BODY)
    if not isinstance(raw, (list, tuple)) or len(raw) == 0:
        raise ArgumentValidationError(_ARG_BODY, _TY_BLOCK_LIST)
    parsed: list[WriteLivingDocBlockArg] = []
    for block in raw:
        if not isinstance(block, dict):
            raise ArgumentValidationError(_ARG_BODY, _TY_BLOCK_LIST)
        try:
            parsed.append(parse_block_arg(block))
        except ValueError as exc:
            raise ArgumentValidationError(_ARG_BODY, _TY_BLOCK_LIST) from exc
    return tuple(parsed)


def _parse_positive_int(
    arguments: dict[str, Any],
    key: str,
    *,
    default: int,
) -> int:
    """Return parse positive int.

    Raises:
        ArgumentValidationError: Raised on the corresponding failure path.
    """
    raw = arguments.get(key)
    if raw in (None, ""):
        return default
    if not isinstance(raw, int) or isinstance(raw, bool) or raw < 1:
        raise ArgumentValidationError(key, _TY_POS_INT)
    return raw


def _parse_nonneg_int(
    arguments: dict[str, Any],
    key: str,
    *,
    default: int,
) -> int:
    """Return parse nonneg int.

    Raises:
        ArgumentValidationError: Raised on the corresponding failure path.
    """
    raw = arguments.get(key)
    if raw in (None, ""):
        return default
    if not isinstance(raw, int) or isinstance(raw, bool) or raw < 0:
        raise ArgumentValidationError(key, _TY_NONNEG_INT)
    return raw


def _parse_opt_nonblank_str(
    arguments: dict[str, Any],
    key: str,
) -> NotBlankStr | None:
    """Return a ``NotBlankStr`` for *key*, or ``None`` for null / blank.

    A present-but-non-string value is a caller bug, so it raises
    ``ArgumentValidationError`` rather than being silently coerced to ``None``.

    Returns:
        The ``NotBlankStr`` value when present, ``None`` otherwise.

    Raises:
        ArgumentValidationError: Raised on the corresponding failure path.
    """
    raw = arguments.get(key)
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ArgumentValidationError(key, _TY_OPT_STR)
    if not raw.strip():
        return None
    return NotBlankStr(raw)


def _parse_doc_type_filter(
    arguments: dict[str, Any],
) -> frozenset[DocType] | None:
    """Return parse doc type filter.

    Raises:
        ArgumentValidationError: Raised on the corresponding failure path.
    """
    raw = arguments.get(_ARG_DOC_TYPES)
    if raw is None:
        return None
    if not isinstance(raw, (list, tuple)) or len(raw) == 0:
        raise ArgumentValidationError(_ARG_DOC_TYPES, _TY_DOC_TYPE)
    parsed: list[DocType] = []
    for value in raw:
        if not isinstance(value, str):
            raise ArgumentValidationError(_ARG_DOC_TYPES, _TY_DOC_TYPE)
        try:
            parsed.append(DocType(value))
        except ValueError as exc:
            raise ArgumentValidationError(_ARG_DOC_TYPES, _TY_DOC_TYPE) from exc
    return frozenset(parsed)


async def _docs_write(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    """Return docs write."""
    try:
        require_admin_guardrails(arguments, actor)
        svc = _require_docs_service(app_state)
        project_id = NotBlankStr(require_arg(arguments, _ARG_PROJECT_ID, str))
        title = NotBlankStr(require_arg(arguments, _ARG_TITLE, str))
        doc_type = _parse_doc_type(arguments, _ARG_DOC_TYPE)
        author = NotBlankStr(require_arg(arguments, _ARG_AUTHOR, str))
        block_args = _parse_block_list(arguments)
        body = _materialise_body(block_args)
        tags = _parse_str_tuple(arguments, _ARG_TAGS)
        related = _parse_str_tuple(arguments, _ARG_RELATED)
        slug = _parse_opt_nonblank_str(arguments, _ARG_SLUG)
        metadata = await svc.write_doc(
            project_id=project_id,
            title=title,
            doc_type=doc_type,
            author_agent_id=author,
            body=body,
            tags=tags,
            related_task_ids=related,
            slug=slug,
        )
        logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=_TOOL_DOCS_WRITE)
        return ok(metadata.model_dump(mode="json"))
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(_TOOL_DOCS_WRITE, exc)
        return err(exc)
    except (DocCommitError, DocIndexError, DocValidationError) as exc:
        log_handler_invoke_failed(_TOOL_DOCS_WRITE, exc)
        return err(exc)
    except Exception as exc:
        reraise_critical(exc)
        log_handler_invoke_failed(_TOOL_DOCS_WRITE, exc)
        return err(exc)


async def _docs_get(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return docs get."""
    try:
        svc = _require_docs_service(app_state)
        project_id = NotBlankStr(require_arg(arguments, _ARG_PROJECT_ID, str))
        slug = NotBlankStr(require_arg(arguments, _ARG_SLUG, str))
        version = _parse_opt_nonblank_str(arguments, _ARG_VERSION)
        doc = await svc.read_doc(
            project_id=project_id,
            slug=slug,
            version=version,
        )
        logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=_TOOL_DOCS_GET)
        return ok(doc.model_dump(mode="json"))
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(_TOOL_DOCS_GET, exc)
        return err(exc)
    except DocNotFoundError as exc:
        log_handler_invoke_failed(_TOOL_DOCS_GET, exc)
        return err(exc)
    except Exception as exc:
        reraise_critical(exc)
        log_handler_invoke_failed(_TOOL_DOCS_GET, exc)
        return err(exc)


async def _docs_list(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return docs list."""
    try:
        svc = _require_docs_service(app_state)
        project_id = NotBlankStr(require_arg(arguments, _ARG_PROJECT_ID, str))
        doc_type = _parse_opt_doc_type(arguments, _ARG_DOC_TYPE)
        tag = _parse_opt_nonblank_str(arguments, _ARG_TAG)
        limit = _parse_positive_int(
            arguments, _ARG_LIMIT, default=DOCS_LIST_DEFAULT_LIMIT
        )
        offset = _parse_nonneg_int(arguments, _ARG_OFFSET, default=0)
        summaries = await svc.list_docs(
            project_id=project_id,
            doc_type=doc_type,
            tag=tag,
            limit=limit,
            offset=offset,
        )
        logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=_TOOL_DOCS_LIST)
        return ok([s.model_dump(mode="json") for s in summaries])
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(_TOOL_DOCS_LIST, exc)
        return err(exc)
    except Exception as exc:
        reraise_critical(exc)
        log_handler_invoke_failed(_TOOL_DOCS_LIST, exc)
        return err(exc)


async def _docs_search(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return docs search."""
    try:
        svc = _require_docs_service(app_state)
        project_id = NotBlankStr(require_arg(arguments, _ARG_PROJECT_ID, str))
        query = NotBlankStr(require_arg(arguments, _ARG_QUERY, str))
        doc_types = _parse_doc_type_filter(arguments)
        limit = _parse_positive_int(
            arguments, _ARG_LIMIT, default=DOCS_SEARCH_DEFAULT_LIMIT
        )
        hits = await svc.search(
            project_id=project_id,
            query=query,
            doc_types=doc_types,
            limit=limit,
        )
        logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=_TOOL_DOCS_SEARCH)
        return ok([h.model_dump(mode="json") for h in hits])
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(_TOOL_DOCS_SEARCH, exc)
        return err(exc)
    except Exception as exc:
        reraise_critical(exc)
        log_handler_invoke_failed(_TOOL_DOCS_SEARCH, exc)
        return err(exc)


async def _docs_history(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return docs history."""
    try:
        svc = _require_docs_service(app_state)
        project_id = NotBlankStr(require_arg(arguments, _ARG_PROJECT_ID, str))
        slug = NotBlankStr(require_arg(arguments, _ARG_SLUG, str))
        limit = _parse_positive_int(
            arguments, _ARG_LIMIT, default=DOCS_HISTORY_DEFAULT_LIMIT
        )
        versions = await svc.history(
            project_id=project_id,
            slug=slug,
            limit=limit,
        )
        logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=_TOOL_DOCS_HISTORY)
        return ok([v.model_dump(mode="json") for v in versions])
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(_TOOL_DOCS_HISTORY, exc)
        return err(exc)
    except DocNotFoundError as exc:
        log_handler_invoke_failed(_TOOL_DOCS_HISTORY, exc)
        return err(exc)
    except Exception as exc:
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
