"""Message MCP handlers (communication sub-domain)."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING

from pydantic import ValidationError

from synthorg.communication.mcp_errors import CapabilityNotSupportedError
from synthorg.communication.message import Message
from synthorg.communication.state import message_service_of
from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import NotFoundError
from synthorg.meta.mcp.domains._remaining_args import (
    MessagesDeleteArgs,
    MessagesGetArgs,
    MessagesListArgs,
    MessagesSendArgs,
)
from synthorg.meta.mcp.errors import (
    ArgumentValidationError,
    GuardrailViolationError,
)
from synthorg.meta.mcp.handler_protocol import ToolHandler
from synthorg.meta.mcp.handlers._mcp_handler_common import typed_args
from synthorg.meta.mcp.handlers.common import (
    PaginationMeta,
    dump_many,
    err,
    ok,
    require_admin_guardrails,
)
from synthorg.meta.mcp.handlers.common_args import (
    require_actor_id,
)
from synthorg.meta.mcp.handlers.common_logging import (
    log_handler_argument_invalid,
    log_handler_guardrail_violated,
    log_handler_invoke_failed,
)
from synthorg.meta.mcp.handlers.communication._shared import (
    _map_capability_not_supported,
)
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import (
    MCP_ADMIN_OP_EXECUTED,
    MCP_HANDLER_INVOKE_SUCCESS,
)

if TYPE_CHECKING:
    from synthorg.api.state import AppState

logger = get_logger(__name__)

_ARG_MESSAGE = "message"
_TY_MESSAGE_OBJ = "Message object"


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
    tool = "synthorg_messages_list"
    try:
        page_args = typed_args(arguments, MessagesListArgs)
        offset, limit = page_args.offset, page_args.limit
        messages, total = await message_service_of(app_state).list_messages(
            channel=page_args.channel,
            offset=offset,
            limit=limit,
        )
        pagination = PaginationMeta(total=total, offset=offset, limit=limit)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(dump_many(messages), pagination=pagination)


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
    tool = "synthorg_messages_get"
    try:
        get_args = typed_args(arguments, MessagesGetArgs)
        message = await message_service_of(app_state).get_message(
            channel=get_args.channel,
            message_id=get_args.message_id,
        )
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    if message is None:
        missing = NotFoundError(f"Message {get_args.message_id} not found")
        log_handler_invoke_failed(tool, missing, message_id=get_args.message_id)
        return err(missing, domain_code="not_found")
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(message.model_dump(mode="json"))


async def _messages_send(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Publish a new message on a channel (non-destructive write).

    Returns:
        Resulting string.

    Raises:
        ArgumentValidationError: When ``message`` is not a valid Message.
    """
    tool = "synthorg_messages_send"
    try:
        raw_message = typed_args(arguments, MessagesSendArgs).message
        try:
            message = Message.model_validate(raw_message)
        except ValidationError as exc:
            raise ArgumentValidationError(_ARG_MESSAGE, _TY_MESSAGE_OBJ) from exc
        await message_service_of(app_state).send_message(
            message=message,
            actor_id=require_actor_id(actor),
        )
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok({"id": str(message.id)})


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
        message_id = typed_args(arguments, MessagesDeleteArgs).message_id
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
        logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
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
