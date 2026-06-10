"""Meeting MCP handlers (communication sub-domain)."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING

from synthorg.communication.mcp_errors import CapabilityNotSupportedError
from synthorg.communication.meeting.enums import MeetingStatus
from synthorg.communication.state import meeting_service_of
from synthorg.core.agent import AgentIdentity
from synthorg.meta.mcp.errors import (
    ArgumentValidationError,
    GuardrailViolationError,
)
from synthorg.meta.mcp.handler_protocol import ToolHandler
from synthorg.meta.mcp.handlers.common import (
    PaginationMeta,
    dump_many,
    err,
    ok,
    require_admin_guardrails,
)
from synthorg.meta.mcp.handlers.common_args import (
    coerce_pagination,
    get_optional_str,
    require_actor_id,
)
from synthorg.meta.mcp.handlers.common_logging import (
    log_handler_argument_invalid,
    log_handler_guardrail_violated,
    log_handler_invoke_failed,
)
from synthorg.meta.mcp.handlers.communication._shared import (
    _map_capability_not_supported,
    _require_str,
)
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import MCP_ADMIN_OP_EXECUTED

if TYPE_CHECKING:
    from synthorg.api.state import AppState

logger = get_logger(__name__)

_ARG_STATUS = "status"
_ARG_MEETING_TYPE = "meeting_type"
_ARG_MEETING_ID = "meeting_id"
_TY_STATUS = "MeetingStatus string"


def _parse_meeting_status(arguments: dict[str, object]) -> MeetingStatus | None:
    """Return parse meeting status.

    Raises:
        ArgumentValidationError: Raised on the corresponding failure path.
    """
    raw = arguments.get(_ARG_STATUS)
    if raw in (None, ""):
        return None
    if not isinstance(raw, str):
        raise ArgumentValidationError(_ARG_STATUS, _TY_STATUS)
    try:
        return MeetingStatus(raw)
    except ValueError as exc:
        raise ArgumentValidationError(_ARG_STATUS, _TY_STATUS) from exc


async def _meetings_list(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """List meeting records (paginated, optionally filtered).

    Returns:
        Resulting string.
    """
    try:
        status = _parse_meeting_status(arguments)
        meeting_type = get_optional_str(arguments, _ARG_MEETING_TYPE)
        offset, limit = coerce_pagination(arguments)
        records, total = await meeting_service_of(app_state).list_meetings(
            status=status,
            meeting_type=meeting_type,
            offset=offset,
            limit=limit,
        )
        pagination = PaginationMeta(total=total, offset=offset, limit=limit)
        return ok(dump_many(records), pagination=pagination)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid("synthorg_meetings_list", exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        log_handler_invoke_failed("synthorg_meetings_list", exc)
        return err(exc)


async def _meetings_get(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Fetch a single meeting record by ID.

    Returns:
        Resulting string.
    """
    try:
        meeting_id = _require_str(arguments, _ARG_MEETING_ID)
        record = await meeting_service_of(app_state).get_meeting(meeting_id)
        if record is None:
            return err(
                LookupError(f"Meeting {meeting_id} not found"),
                domain_code="not_found",
            )
        return ok(record.model_dump(mode="json"))
    except ArgumentValidationError as exc:
        log_handler_argument_invalid("synthorg_meetings_get", exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        log_handler_invoke_failed("synthorg_meetings_get", exc)
        return err(exc)


async def _meetings_create(
    *,
    app_state: AppState,
    arguments: dict[str, object],  # noqa: ARG001
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Capability gap: meetings are produced by the engine, not ad-hoc created.

    Returns:
        Resulting string.
    """
    tool = "synthorg_meetings_create"
    try:
        await meeting_service_of(app_state).create_meeting()
    except CapabilityNotSupportedError as exc:
        return _map_capability_not_supported(tool, exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok(None)


async def _meetings_update(
    *,
    app_state: AppState,
    arguments: dict[str, object],  # noqa: ARG001
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Capability gap: meeting records are updated by the engine only.

    Returns:
        Resulting string.
    """
    tool = "synthorg_meetings_update"
    try:
        await meeting_service_of(app_state).update_meeting()
    except CapabilityNotSupportedError as exc:
        return _map_capability_not_supported(tool, exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok(None)


async def _meetings_delete(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Delete a single meeting record by id.

    Returns:
        Resulting string.
    """
    tool = "synthorg_meetings_delete"
    try:
        reason, resolved_actor = require_admin_guardrails(arguments, actor)
        meeting_id = _require_str(arguments, _ARG_MEETING_ID)
        actor_id = require_actor_id(resolved_actor)
        try:
            removed = await meeting_service_of(app_state).delete_meeting(
                meeting_id=meeting_id,
                actor_id=actor_id,
                reason=reason,
            )
        except CapabilityNotSupportedError as exc:
            return _map_capability_not_supported(tool, exc)
        if removed:
            logger.info(
                MCP_ADMIN_OP_EXECUTED,
                tool_name=tool,
                actor_agent_id=actor_id,
                reason=reason,
                target_id=meeting_id,
            )
        return ok({"removed": removed})
    except GuardrailViolationError as exc:
        log_handler_guardrail_violated(tool, exc)
        return err(exc)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        log_handler_invoke_failed(tool, exc)
        return err(exc)


MEETINGS_HANDLERS: Mapping[str, ToolHandler] = MappingProxyType(
    {
        "synthorg_meetings_list": _meetings_list,
        "synthorg_meetings_get": _meetings_get,
        "synthorg_meetings_create": _meetings_create,
        "synthorg_meetings_update": _meetings_update,
        "synthorg_meetings_delete": _meetings_delete,
    },
)
