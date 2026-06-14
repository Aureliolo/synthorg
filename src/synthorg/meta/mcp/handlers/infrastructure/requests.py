"""Operator request-ledger MCP handlers (infrastructure sub-domain)."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING
from uuid import UUID

from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.infrastructure.state import requests_facade_service_of
from synthorg.meta.mcp.domains._remaining_args import (
    RequestsCreateArgs,
    RequestsGetArgs,
    RequestsListArgs,
)
from synthorg.meta.mcp.errors import ArgumentValidationError
from synthorg.meta.mcp.handler_protocol import ToolHandler
from synthorg.meta.mcp.handlers._mcp_handler_common import typed_args
from synthorg.meta.mcp.handlers.common import PaginationMeta, err, ok
from synthorg.meta.mcp.handlers.common_args import require_actor_id
from synthorg.meta.mcp.handlers.common_logging import (
    log_handler_argument_invalid,
    log_handler_invoke_failed,
)
from synthorg.observability import get_logger

if TYPE_CHECKING:
    from synthorg.api.state import AppState

logger = get_logger(__name__)

_ARG_REQUEST_ID = "request_id"
_TY_UUID = "UUID string"


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
        page_args = typed_args(arguments, RequestsListArgs)
        offset, limit = page_args.offset, page_args.limit
        page, total = await requests_facade_service_of(app_state).list_requests(
            offset=offset,
            limit=limit,
        )
        pagination = PaginationMeta(total=total, offset=offset, limit=limit)
        return ok([r.to_dict() for r in page], pagination=pagination)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
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

    Raises:
        ArgumentValidationError: When ``request_id`` is not a UUID string.
    """
    tool = "synthorg_requests_get"
    try:
        request_id = typed_args(arguments, RequestsGetArgs).request_id
        try:
            UUID(request_id)
        except ValueError as uuid_exc:
            raise ArgumentValidationError(_ARG_REQUEST_ID, _TY_UUID) from uuid_exc
        record = await requests_facade_service_of(app_state).get_request(request_id)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
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
        args = typed_args(arguments, RequestsCreateArgs)
        record = await requests_facade_service_of(app_state).create_request(
            title=args.title,
            body=args.body,
            requested_by=require_actor_id(actor),
        )
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
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
