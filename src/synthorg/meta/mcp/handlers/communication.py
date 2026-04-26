"""Communication domain MCP handlers.

21 tools spanning messages, meetings, connections, webhooks, and the
sandbox tunnel.  Every handler shims through the corresponding facade
on ``AppState``:

* ``messages.*`` -> :class:`MessageService`
* ``meetings.*`` -> :class:`MeetingService`
* ``connections.*`` -> :class:`ConnectionService`
* ``webhooks.*`` -> :class:`WebhookService`
* ``tunnel.*`` -> :class:`TunnelService`

Destructive ops (``*_delete``) run through
:func:`require_destructive_guardrails` and emit
``MCP_DESTRUCTIVE_OP_EXECUTED`` on success.
"""

from types import MappingProxyType
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from pydantic import ValidationError

from synthorg.communication.mcp_errors import CapabilityNotSupportedError
from synthorg.communication.meeting.enums import MeetingStatus
from synthorg.communication.message import Message
from synthorg.integrations.connections.models import ConnectionType
from synthorg.integrations.webhooks.models import WebhookDefinition
from synthorg.meta.mcp.errors import (
    ArgumentValidationError,
    GuardrailViolationError,
    invalid_argument,
)
from synthorg.meta.mcp.handler_protocol import (
    ToolHandler,  # noqa: TC001 -- PEP 649 annotation
)
from synthorg.meta.mcp.handlers.common import (
    PaginationMeta,
    dump_many,
    err,
    ok,
    require_destructive_guardrails,
)
from synthorg.meta.mcp.handlers.common_args import (
    actor_label,
    coerce_pagination,
    get_optional_str,
    require_arg,
    require_dict,
)
from synthorg.meta.mcp.handlers.common_logging import (
    log_handler_argument_invalid,
    log_handler_guardrail_violated,
    log_handler_invoke_failed,
)
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import (
    MCP_DESTRUCTIVE_OP_EXECUTED,
    MCP_HANDLER_CAPABILITY_GAP,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from synthorg.core.agent import AgentIdentity
    from synthorg.core.types import NotBlankStr

logger = get_logger(__name__)

_ARG_CHANNEL = "channel"
_ARG_MESSAGE_ID = "message_id"
_ARG_MESSAGE = "message"
_ARG_STATUS = "status"
_ARG_MEETING_TYPE = "meeting_type"
_ARG_MEETING_ID = "meeting_id"
_ARG_NAME = "name"
_ARG_CONNECTION_TYPE = "connection_type"
_ARG_AUTH_METHOD = "auth_method"
_ARG_CREDENTIALS = "credentials"
_ARG_BASE_URL = "base_url"
_ARG_METADATA = "metadata"
_ARG_WEBHOOK_ID = "webhook_id"
_ARG_DEFINITION = "definition"

_TY_STRING = "non-blank string"
_TY_UUID = "UUID string"
_TY_MESSAGE_OBJ = "Message object"
_TY_WEBHOOK_OBJ = "WebhookDefinition object"
_TY_STATUS = "MeetingStatus string"
_TY_CONNECTION_TYPE = "ConnectionType string"


# ── Shared helpers ───────────────────────────────────────────────────


def _require_str(arguments: dict[str, Any], key: str) -> NotBlankStr:
    """Extract a required non-blank string or raise ``ArgumentValidationError``."""
    value = get_optional_str(arguments, key)
    if value is None:
        raise invalid_argument(key, _TY_STRING)
    return value


def _get_dict(arguments: dict[str, Any], key: str) -> dict[str, str] | None:
    """Extract an optional ``dict[str, str]`` argument; ``None`` when absent."""
    raw = arguments.get(key)
    if raw in (None, ""):
        return None
    validated = require_dict(arguments, key, value_type=str)
    return dict(validated)


def _parse_message(arguments: dict[str, Any]) -> Message:
    """Decode the ``message`` argument into a validated :class:`Message`."""
    raw = arguments.get(_ARG_MESSAGE)
    if not isinstance(raw, dict):
        raise invalid_argument(_ARG_MESSAGE, _TY_MESSAGE_OBJ)
    try:
        return Message.model_validate(raw)
    except ValidationError as exc:
        raise invalid_argument(_ARG_MESSAGE, _TY_MESSAGE_OBJ) from exc


def _map_capability_not_supported(
    tool: str,
    exc: CapabilityNotSupportedError,
) -> str:
    """Translate facade-side capability gap into a typed envelope."""
    logger.info(
        MCP_HANDLER_CAPABILITY_GAP,
        tool_name=tool,
        capability=exc.capability,
    )
    return err(exc, domain_code=exc.domain_code)


# ── messages ────────────────────────────────────────────────────────


async def _messages_list(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """List messages on a channel (paginated)."""
    try:
        channel = get_optional_str(arguments, _ARG_CHANNEL)
        offset, limit = coerce_pagination(arguments)
        messages, total = await app_state.message_service.list_messages(
            channel=channel,
            offset=offset,
            limit=limit,
        )
        pagination = PaginationMeta(total=total, offset=offset, limit=limit)
        return ok(dump_many(messages), pagination=pagination)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid("synthorg_messages_list", exc)
        return err(exc)
    except Exception as exc:
        log_handler_invoke_failed("synthorg_messages_list", exc)
        return err(exc)


async def _messages_get(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Fetch a single message by channel + message ID."""
    try:
        channel = _require_str(arguments, _ARG_CHANNEL)
        message_id = _require_str(arguments, _ARG_MESSAGE_ID)
        message = await app_state.message_service.get_message(
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
    except Exception as exc:
        log_handler_invoke_failed("synthorg_messages_get", exc)
        return err(exc)


async def _messages_send(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    """Publish a new message on a channel (non-destructive write)."""
    try:
        message = _parse_message(arguments)
        await app_state.message_service.send_message(
            message=message,
            actor_id=actor_label(actor),
        )
        return ok({"id": str(message.id)})
    except ArgumentValidationError as exc:
        log_handler_argument_invalid("synthorg_messages_send", exc)
        return err(exc)
    except Exception as exc:
        log_handler_invoke_failed("synthorg_messages_send", exc)
        return err(exc)


async def _messages_delete(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    """Capability gap: message store is append-only by design."""
    tool = "synthorg_messages_delete"
    try:
        reason, resolved_actor = require_destructive_guardrails(arguments, actor)
        channel = _require_str(arguments, _ARG_CHANNEL)
        message_id = _require_str(arguments, _ARG_MESSAGE_ID)
        try:
            removed = await app_state.message_service.delete_message(
                channel=channel,
                message_id=message_id,
                actor_id=actor_label(resolved_actor),
                reason=reason,
            )
        except CapabilityNotSupportedError as exc:
            return _map_capability_not_supported(tool, exc)
        if removed:
            logger.info(
                MCP_DESTRUCTIVE_OP_EXECUTED,
                tool_name=tool,
                actor=actor_label(resolved_actor),
                reason=reason,
                removed=removed,
            )
        return ok({"removed": removed})
    except GuardrailViolationError as exc:
        log_handler_guardrail_violated(tool, exc)
        return err(exc)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)


# ── meetings ────────────────────────────────────────────────────────


def _parse_meeting_status(arguments: dict[str, Any]) -> MeetingStatus | None:
    raw = arguments.get(_ARG_STATUS)
    if raw in (None, ""):
        return None
    if not isinstance(raw, str):
        raise invalid_argument(_ARG_STATUS, _TY_STATUS)
    try:
        return MeetingStatus(raw)
    except ValueError as exc:
        raise invalid_argument(_ARG_STATUS, _TY_STATUS) from exc


async def _meetings_list(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """List meeting records (paginated, optionally filtered)."""
    try:
        status = _parse_meeting_status(arguments)
        meeting_type = get_optional_str(arguments, _ARG_MEETING_TYPE)
        offset, limit = coerce_pagination(arguments)
        records, total = await app_state.meeting_service.list_meetings(
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
    except Exception as exc:
        log_handler_invoke_failed("synthorg_meetings_list", exc)
        return err(exc)


async def _meetings_get(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Fetch a single meeting record by ID."""
    try:
        meeting_id = _require_str(arguments, _ARG_MEETING_ID)
        record = await app_state.meeting_service.get_meeting(meeting_id)
        if record is None:
            return err(
                LookupError(f"Meeting {meeting_id} not found"),
                domain_code="not_found",
            )
        return ok(record.model_dump(mode="json"))
    except ArgumentValidationError as exc:
        log_handler_argument_invalid("synthorg_meetings_get", exc)
        return err(exc)
    except Exception as exc:
        log_handler_invoke_failed("synthorg_meetings_get", exc)
        return err(exc)


async def _meetings_create(
    *,
    app_state: Any,
    arguments: dict[str, Any],  # noqa: ARG001
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Capability gap: meetings are produced by the engine, not ad-hoc created."""
    tool = "synthorg_meetings_create"
    try:
        await app_state.meeting_service.create_meeting()
    except CapabilityNotSupportedError as exc:
        return _map_capability_not_supported(tool, exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok(None)


async def _meetings_update(
    *,
    app_state: Any,
    arguments: dict[str, Any],  # noqa: ARG001
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Capability gap: meeting records are updated by the engine only."""
    tool = "synthorg_meetings_update"
    try:
        await app_state.meeting_service.update_meeting()
    except CapabilityNotSupportedError as exc:
        return _map_capability_not_supported(tool, exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok(None)


async def _meetings_delete(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    """Capability gap: meeting records are append-only by design."""
    tool = "synthorg_meetings_delete"
    try:
        _reason, _resolved_actor = require_destructive_guardrails(arguments, actor)
        try:
            await app_state.meeting_service.delete_meeting()
        except CapabilityNotSupportedError as exc:
            return _map_capability_not_supported(tool, exc)
    except GuardrailViolationError as exc:
        log_handler_guardrail_violated(tool, exc)
        return err(exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok(None)


# ── connections ─────────────────────────────────────────────────────


def _parse_connection_type(arguments: dict[str, Any]) -> ConnectionType:
    raw = require_arg(arguments, _ARG_CONNECTION_TYPE, str)
    try:
        return ConnectionType(raw)
    except ValueError as exc:
        raise invalid_argument(_ARG_CONNECTION_TYPE, _TY_CONNECTION_TYPE) from exc


async def _connections_list(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """List external-system connections (paginated)."""
    try:
        offset, limit = coerce_pagination(arguments)
        connections, total = await app_state.connection_service.list_connections(
            offset=offset,
            limit=limit,
        )
        pagination = PaginationMeta(total=total, offset=offset, limit=limit)
        return ok(dump_many(connections), pagination=pagination)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid("synthorg_connections_list", exc)
        return err(exc)
    except Exception as exc:
        log_handler_invoke_failed("synthorg_connections_list", exc)
        return err(exc)


async def _connections_get(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Fetch a single connection by name."""
    try:
        name = _require_str(arguments, _ARG_NAME)
        connection = await app_state.connection_service.get_connection(name)
        if connection is None:
            return err(
                LookupError(f"Connection {name} not found"),
                domain_code="not_found",
            )
        return ok(connection.model_dump(mode="json"))
    except ArgumentValidationError as exc:
        log_handler_argument_invalid("synthorg_connections_get", exc)
        return err(exc)
    except Exception as exc:
        log_handler_invoke_failed("synthorg_connections_get", exc)
        return err(exc)


async def _connections_create(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    """Create a new external connection (non-destructive write)."""
    try:
        name = _require_str(arguments, _ARG_NAME)
        connection_type = _parse_connection_type(arguments)
        auth_method = _require_str(arguments, _ARG_AUTH_METHOD)
        credentials = require_dict(arguments, _ARG_CREDENTIALS)
        base_url = get_optional_str(arguments, _ARG_BASE_URL)
        metadata = _get_dict(arguments, _ARG_METADATA)
        connection = await app_state.connection_service.create_connection(
            name=name,
            connection_type=connection_type,
            auth_method=auth_method,
            credentials=credentials,
            actor_id=actor_label(actor),
            base_url=base_url,
            metadata=metadata,
        )
        return ok(connection.model_dump(mode="json"))
    except ArgumentValidationError as exc:
        log_handler_argument_invalid("synthorg_connections_create", exc)
        return err(exc)
    except Exception as exc:
        log_handler_invoke_failed("synthorg_connections_create", exc)
        return err(exc)


async def _connections_delete(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    """Delete a connection (destructive; enforces guardrails)."""
    tool = "synthorg_connections_delete"
    try:
        reason, resolved_actor = require_destructive_guardrails(arguments, actor)
        name = _require_str(arguments, _ARG_NAME)
        await app_state.connection_service.delete_connection(
            name=name,
            actor_id=actor_label(resolved_actor),
            reason=reason,
        )
        logger.info(
            MCP_DESTRUCTIVE_OP_EXECUTED,
            tool_name=tool,
            actor=actor_label(resolved_actor),
            reason=reason,
            connection_name=name,
        )
        return ok(None)
    except GuardrailViolationError as exc:
        log_handler_guardrail_violated(tool, exc)
        return err(exc)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)


async def _connections_check_health(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Run an on-demand health probe against a connection."""
    try:
        name = _require_str(arguments, _ARG_NAME)
        connection = await app_state.connection_service.check_health(name=name)
        if connection is None:
            return err(
                LookupError(f"Connection {name} not found"),
                domain_code="not_found",
            )
        return ok(connection.model_dump(mode="json"))
    except ArgumentValidationError as exc:
        log_handler_argument_invalid("synthorg_connections_check_health", exc)
        return err(exc)
    except Exception as exc:
        log_handler_invoke_failed("synthorg_connections_check_health", exc)
        return err(exc)


# ── webhooks ────────────────────────────────────────────────────────


def _parse_webhook_definition(
    arguments: dict[str, Any],
    *,
    require_id: bool,
) -> WebhookDefinition:
    raw = arguments.get(_ARG_DEFINITION)
    if not isinstance(raw, dict):
        raise invalid_argument(_ARG_DEFINITION, _TY_WEBHOOK_OBJ)
    payload = dict(raw)
    if not require_id and "id" not in payload:
        payload["id"] = str(uuid4())
    try:
        return WebhookDefinition.model_validate(payload)
    except ValidationError as exc:
        raise invalid_argument(_ARG_DEFINITION, _TY_WEBHOOK_OBJ) from exc


async def _webhooks_list(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """List registered webhook definitions (paginated)."""
    try:
        offset, limit = coerce_pagination(arguments)
        definitions, total = await app_state.webhook_service.list_webhooks(
            offset=offset,
            limit=limit,
        )
        pagination = PaginationMeta(total=total, offset=offset, limit=limit)
        return ok(dump_many(definitions), pagination=pagination)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid("synthorg_webhooks_list", exc)
        return err(exc)
    except Exception as exc:
        log_handler_invoke_failed("synthorg_webhooks_list", exc)
        return err(exc)


async def _webhooks_get(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Fetch a single webhook definition by ID."""
    try:
        webhook_id = _require_str(arguments, _ARG_WEBHOOK_ID)
        definition = await app_state.webhook_service.get_webhook(webhook_id)
        if definition is None:
            return err(
                LookupError(f"Webhook {webhook_id} not found"),
                domain_code="not_found",
            )
        return ok(definition.model_dump(mode="json"))
    except ArgumentValidationError as exc:
        log_handler_argument_invalid("synthorg_webhooks_get", exc)
        return err(exc)
    except Exception as exc:
        log_handler_invoke_failed("synthorg_webhooks_get", exc)
        return err(exc)


async def _webhooks_create(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    """Register a new webhook definition (non-destructive write)."""
    try:
        definition = _parse_webhook_definition(arguments, require_id=False)
        stored = await app_state.webhook_service.create_webhook(
            definition=definition,
            actor_id=actor_label(actor),
        )
        return ok(stored.model_dump(mode="json"))
    except ArgumentValidationError as exc:
        log_handler_argument_invalid("synthorg_webhooks_create", exc)
        return err(exc)
    except KeyError as exc:
        log_handler_invoke_failed("synthorg_webhooks_create", exc)
        return err(exc, domain_code="conflict")
    except ValueError as exc:
        log_handler_invoke_failed("synthorg_webhooks_create", exc)
        return err(exc, domain_code="conflict")
    except Exception as exc:
        log_handler_invoke_failed("synthorg_webhooks_create", exc)
        return err(exc)


async def _webhooks_update(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    """Update an existing webhook definition by ID."""
    try:
        definition = _parse_webhook_definition(arguments, require_id=True)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid("synthorg_webhooks_update", exc)
        return err(exc)
    except Exception as exc:
        log_handler_invoke_failed("synthorg_webhooks_update", exc)
        return err(exc)
    try:
        stored = await app_state.webhook_service.update_webhook(
            definition=definition,
            actor_id=actor_label(actor),
        )
        return ok(stored.model_dump(mode="json"))
    except KeyError as exc:
        missing = LookupError(f"Webhook {definition.id} not found")
        log_handler_invoke_failed("synthorg_webhooks_update", exc)
        return err(missing, domain_code="not_found")
    except ValueError as exc:
        log_handler_invoke_failed("synthorg_webhooks_update", exc)
        return err(exc, domain_code="conflict")
    except Exception as exc:
        log_handler_invoke_failed("synthorg_webhooks_update", exc)
        return err(exc)


async def _webhooks_delete(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    """Delete a webhook definition (destructive; enforces guardrails)."""
    tool = "synthorg_webhooks_delete"
    try:
        reason, resolved_actor = require_destructive_guardrails(arguments, actor)
        webhook_id = _require_str(arguments, _ARG_WEBHOOK_ID)
        removed = await app_state.webhook_service.delete_webhook(
            definition_id=webhook_id,
            actor_id=actor_label(resolved_actor),
            reason=reason,
        )
        if not removed:
            return err(
                LookupError(f"Webhook {webhook_id} not found"),
                domain_code="not_found",
            )
        logger.info(
            MCP_DESTRUCTIVE_OP_EXECUTED,
            tool_name=tool,
            actor=actor_label(resolved_actor),
            reason=reason,
            webhook_id=webhook_id,
            removed=removed,
        )
        return ok({"removed": removed})
    except GuardrailViolationError as exc:
        log_handler_guardrail_violated(tool, exc)
        return err(exc)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)


# ── tunnel ──────────────────────────────────────────────────────────


async def _tunnel_get_status(
    *,
    app_state: Any,
    arguments: dict[str, Any],  # noqa: ARG001
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return the current tunnel service status."""
    try:
        status = await app_state.tunnel_service.get_status()
        return ok(status.to_dict())
    except Exception as exc:
        log_handler_invoke_failed("synthorg_tunnel_get_status", exc)
        return err(exc)


async def _tunnel_connect(
    *,
    app_state: Any,
    arguments: dict[str, Any],  # noqa: ARG001
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Trigger a tunnel reconnect attempt."""
    try:
        status = await app_state.tunnel_service.connect()
        return ok(status.to_dict())
    except Exception as exc:
        log_handler_invoke_failed("synthorg_tunnel_connect", exc)
        return err(exc)


COMMUNICATION_HANDLERS: Mapping[str, ToolHandler] = MappingProxyType(
    {
        "synthorg_messages_list": _messages_list,
        "synthorg_messages_get": _messages_get,
        "synthorg_messages_send": _messages_send,
        "synthorg_messages_delete": _messages_delete,
        "synthorg_meetings_list": _meetings_list,
        "synthorg_meetings_get": _meetings_get,
        "synthorg_meetings_create": _meetings_create,
        "synthorg_meetings_update": _meetings_update,
        "synthorg_meetings_delete": _meetings_delete,
        "synthorg_connections_list": _connections_list,
        "synthorg_connections_get": _connections_get,
        "synthorg_connections_create": _connections_create,
        "synthorg_connections_delete": _connections_delete,
        "synthorg_connections_check_health": _connections_check_health,
        "synthorg_webhooks_list": _webhooks_list,
        "synthorg_webhooks_get": _webhooks_get,
        "synthorg_webhooks_create": _webhooks_create,
        "synthorg_webhooks_update": _webhooks_update,
        "synthorg_webhooks_delete": _webhooks_delete,
        "synthorg_tunnel_get_status": _tunnel_get_status,
        "synthorg_tunnel_connect": _tunnel_connect,
    },
)
