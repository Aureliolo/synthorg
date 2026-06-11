"""Message MCP handlers (communication sub-domain)."""

from collections.abc import Mapping
from types import MappingProxyType

from pydantic import ValidationError

from synthorg.api.state import AppState
from synthorg.communication.mcp_errors import CapabilityNotSupportedError
from synthorg.communication.message import Message
from synthorg.communication.state import message_service_of
from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
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

logger = get_logger(__name__)

_ARG_CHANNEL = "channel"
_ARG_MESSAGE_ID = "message_id"
_ARG_MESSAGE = "message"
_TY_MESSAGE_OBJ = "Message object"


def _parse_message(arguments: dict[str, object]) -> Message:
    """Decode the ``message`` argument into a validated :class:`Message`.

    Returns:
        ``Message`` instance.

    Raises:
        ArgumentValidationError: Raised on the corresponding failure path.
    """
    raw = arguments.get(_ARG_MESSAGE)
    if not isinstance(raw, dict):
        raise ArgumentValidationError(_ARG_MESSAGE, _TY_MESSAGE_OBJ)
    try:
        return Message.model_validate(raw)
    except ValidationError as exc:
        raise ArgumentValidationError(_ARG_MESSAGE, _TY_MESSAGE_OBJ) from exc


async def _messages_list(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """List messages on a channel (paginated).

    Returns:
        Resulting string.
    """
    try:
        channel = get_optional_str(arguments, _ARG_CHANNEL)
        offset, limit = coerce_pagination(arguments)
        messages, total = await message_service_of(app_state).list_messages(
            channel=channel,
            offset=offset,
            limit=limit,
        )
        pagination = PaginationMeta(total=total, offset=offset, limit=limit)
        return ok(dump_many(messages), pagination=pagination)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid("synthorg_messages_list", exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed("synthorg_messages_list", exc)
        return err(exc)


async def _messages_get(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Fetch a single message by channel + message ID.

    Returns:
        Resulting string.
    """
    try:
        channel = _require_str(arguments, _ARG_CHANNEL)
        message_id = _require_str(arguments, _ARG_MESSAGE_ID)
        message = await message_service_of(app_state).get_message(
            channel=channel,
            message_id=message_id,
        )
        if message is None:
            return err(
                LookupError(f"Message {message_id} not found"),
                domain_code="not_found",
            )
        return ok(message.model_dump(mode="json"))
    except ArgumentValidationError as exc:
        log_handler_argument_invalid("synthorg_messages_get", exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed("synthorg_messages_get", exc)
        return err(exc)


async def _messages_send(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Publish a new message on a channel (non-destructive write).

    Returns:
        Resulting string.
    """
    try:
        message = _parse_message(arguments)
        await message_service_of(app_state).send_message(
            message=message,
            actor_id=require_actor_id(actor),
        )
        return ok({"id": str(message.id)})
    except ArgumentValidationError as exc:
        log_handler_argument_invalid("synthorg_messages_send", exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed("synthorg_messages_send", exc)
        return err(exc)


async def _messages_delete(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Delete a single message by id.

    The destructive-op audit event fires only when a row was actually
    removed (``removed=True``); not-found responses are returned as a
    successful envelope with ``removed=False`` and no audit emission
    so the audit trail stays semantically clean.

    Returns:
        Resulting string.
    """
    tool = "synthorg_messages_delete"
    try:
        reason, resolved_actor = require_admin_guardrails(arguments, actor)
        message_id = _require_str(arguments, _ARG_MESSAGE_ID)
        actor_id = require_actor_id(resolved_actor)
        try:
            removed = await message_service_of(app_state).delete_message(
                message_id=message_id,
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
                target_id=message_id,
            )
        return ok({"removed": removed})
    except GuardrailViolationError as exc:
        log_handler_guardrail_violated(tool, exc)
        return err(exc)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)


MESSAGES_HANDLERS: Mapping[str, ToolHandler] = MappingProxyType(
    {
        "synthorg_messages_list": _messages_list,
        "synthorg_messages_get": _messages_get,
        "synthorg_messages_send": _messages_send,
        "synthorg_messages_delete": _messages_delete,
    },
)
