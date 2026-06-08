"""Operator request-ledger MCP handlers (infrastructure sub-domain)."""

from types import MappingProxyType
from typing import TYPE_CHECKING

from synthorg.infrastructure.state import requests_facade_service_of
from synthorg.meta.mcp.errors import ArgumentValidationError
from synthorg.meta.mcp.handler_protocol import ToolHandler
from synthorg.meta.mcp.handlers.common import PaginationMeta, err, ok
from synthorg.meta.mcp.handlers.common_args import coerce_pagination, require_actor_id
from synthorg.meta.mcp.handlers.common_logging import (
    log_handler_argument_invalid,
    log_handler_invoke_failed,
)
from synthorg.meta.mcp.handlers.infrastructure._shared import (
    _require_str,
    _require_uuid,
)
from synthorg.observability import get_logger

if TYPE_CHECKING:
    from collections.abc import Mapping

    from synthorg.api.state import AppState
    from synthorg.core.agent import AgentIdentity

logger = get_logger(__name__)


async def _requests_list(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """List operator request-ledger entries (paginated).

    Returns:
        Resulting string.
    """
    tool = "synthorg_requests_list"
    try:
        offset, limit = coerce_pagination(arguments)
        page, total = await requests_facade_service_of(app_state).list_requests(
            offset=offset,
            limit=limit,
        )
        pagination = PaginationMeta(total=total, offset=offset, limit=limit)
        return ok([r.to_dict() for r in page], pagination=pagination)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)


async def _requests_get(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Fetch a single operator request by ID.

    Returns:
        Resulting string.
    """
    tool = "synthorg_requests_get"
    try:
        request_id = _require_uuid(arguments, "request_id")
        record = await requests_facade_service_of(app_state).get_request(request_id)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    if record is None:
        return err(
            LookupError(f"Request {request_id} not found"),
            domain_code="not_found",
        )
    return ok(record.to_dict())


async def _requests_create(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Record a new operator request (non-destructive write).

    Returns:
        Resulting string.
    """
    tool = "synthorg_requests_create"
    try:
        title = _require_str(arguments, "title")
        body = _require_str(arguments, "body")
        record = await requests_facade_service_of(app_state).create_request(
            title=title,
            body=body,
            requested_by=require_actor_id(actor),
        )
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok(record.to_dict())


REQUESTS_HANDLERS: Mapping[str, ToolHandler] = MappingProxyType(
    {
        "synthorg_requests_list": _requests_list,
        "synthorg_requests_get": _requests_get,
        "synthorg_requests_create": _requests_create,
    },
)
