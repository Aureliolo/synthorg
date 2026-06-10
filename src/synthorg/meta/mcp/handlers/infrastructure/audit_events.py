"""Audit-log + event-stream MCP handlers (infrastructure sub-domain)."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING

from synthorg.communication.mcp_errors import CapabilityNotSupportedError
from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.infrastructure.state import (
    audit_read_service_of,
    events_read_service_of,
)
from synthorg.meta.mcp.errors import ArgumentValidationError
from synthorg.meta.mcp.handler_protocol import ToolHandler
from synthorg.meta.mcp.handlers.common import PaginationMeta, err, ok
from synthorg.meta.mcp.handlers.common_args import coerce_pagination
from synthorg.meta.mcp.handlers.common_logging import (
    log_handler_argument_invalid,
    log_handler_invoke_failed,
)
from synthorg.meta.mcp.handlers.infrastructure._shared import (
    _map_capability,
    _to_jsonable,
)
from synthorg.observability import get_logger

if TYPE_CHECKING:
    from synthorg.api.state import AppState

logger = get_logger(__name__)


async def _audit_list(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return recent audit log entries (paginated).

    Returns:
        Resulting string.
    """
    tool = "synthorg_audit_list"
    try:
        offset, limit = coerce_pagination(arguments)
        page, total = await audit_read_service_of(app_state).list_entries(
            offset=offset,
            limit=limit,
        )
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    pagination = PaginationMeta(total=total, offset=offset, limit=limit)
    return ok([_to_jsonable(e) for e in page], pagination=pagination)


async def _events_list(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return recent events from the event-stream hub.

    Returns:
        Resulting string.
    """
    tool = "synthorg_events_list"
    try:
        offset, limit = coerce_pagination(arguments)
        page, total = await events_read_service_of(app_state).list_events(
            offset=offset,
            limit=limit,
        )
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    pagination = PaginationMeta(total=total, offset=offset, limit=limit)
    return ok([_to_jsonable(e) for e in page], pagination=pagination)


AUDIT_EVENTS_HANDLERS: Mapping[str, ToolHandler] = MappingProxyType(
    {
        "synthorg_audit_list": _audit_list,
        "synthorg_events_list": _events_list,
    },
)
