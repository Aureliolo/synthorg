"""External-connection MCP handlers (communication sub-domain)."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING

from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import NotFoundError
from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.field_metadata import (
    get_connection_type_metadata,
    list_connection_type_metadata,
)
from synthorg.integrations.connections.models import ConnectionType
from synthorg.integrations.connections.secret_capture import (
    PendingSecretCapture,
    resolve_credential_handles,
)
from synthorg.integrations.state import (
    connection_service_of,
    secret_capture_service_of,
)
from synthorg.meta.mcp.domains._remaining_args import (
    ConnectionsCheckHealthArgs,
    ConnectionsCreateArgs,
    ConnectionsDeleteArgs,
    ConnectionsFieldMetadataArgs,
    ConnectionsGetArgs,
    ConnectionsListArgs,
    ConnectionsRequestSecretCaptureArgs,
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
from synthorg.observability import get_logger
from synthorg.observability.events.integrations import SECRET_CAPTURE_REQUESTED
from synthorg.observability.events.mcp import (
    MCP_ADMIN_OP_EXECUTED,
    MCP_HANDLER_INVOKE_SUCCESS,
)

if TYPE_CHECKING:
    from synthorg.api.state import AppState

logger = get_logger(__name__)

_ARG_CONNECTION_TYPE = "connection_type"
_TY_CONNECTION_TYPE = "ConnectionType string"
_ARG_DRAFT_ID = "connection_draft_id"
_TY_DRAFT_ID_REQUIRED = "required when credential_handles are supplied"
_ARG_FIELD_NAME = "field_name"


async def _connections_list(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """List external-system connections (paginated).

    Returns:
        Resulting string.
    """
    tool = "synthorg_connections_list"
    try:
        page_args = typed_args(arguments, ConnectionsListArgs)
        offset, limit = page_args.offset, page_args.limit
        connections, total = await connection_service_of(app_state).list_connections(
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
    return ok(dump_many(connections), pagination=pagination)


async def _connections_get(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Fetch a single connection by name.

    Returns:
        Resulting string.
    """
    tool = "synthorg_connections_get"
    try:
        name = typed_args(arguments, ConnectionsGetArgs).name
        connection = await connection_service_of(app_state).get_connection(name)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    if connection is None:
        missing = NotFoundError(f"Connection {name} not found")
        log_handler_invoke_failed(tool, missing, connection_name=name)
        return err(missing, domain_code="not_found")
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(connection.model_dump(mode="json"))


async def _resolve_credentials(
    app_state: AppState,
    args: ConnectionsCreateArgs,
) -> dict[str, str]:
    """Merge inline non-secret fields with out-of-band handle-resolved secrets.

    Secret fields arrive as capture handles (never inline). Each handle is
    consumed exactly once against its ``(draft_id, field_name)`` binding, so
    the raw value only ever exists in-process here, never in a tool argument
    or the transcript.

    Returns:
        The full credentials mapping ready for ``create_connection``.

    Raises:
        ArgumentValidationError: If handles are supplied without a draft id.
        SecretCaptureHandleInvalidError: If a handle is invalid or expired.
    """
    if not args.credential_handles:
        return dict(args.credentials)
    if args.connection_draft_id is None:
        raise ArgumentValidationError(_ARG_DRAFT_ID, _TY_DRAFT_ID_REQUIRED)
    return await resolve_credential_handles(
        secret_capture_service_of(app_state),
        credentials=dict(args.credentials),
        credential_handles=dict(args.credential_handles),
        connection_draft_id=args.connection_draft_id,
    )


async def _connections_create(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Create a new external connection (admin op; enforces guardrails).

    Returns:
        Resulting string.

    Raises:
        ArgumentValidationError: When ``connection_type`` is not a known
            :class:`ConnectionType` value.
    """
    tool = "synthorg_connections_create"
    try:
        reason, resolved_actor = require_admin_guardrails(arguments, actor)
        args = typed_args(arguments, ConnectionsCreateArgs)
        try:
            connection_type = ConnectionType(args.connection_type)
        except ValueError as exc:
            bad = ArgumentValidationError(_ARG_CONNECTION_TYPE, _TY_CONNECTION_TYPE)
            raise bad from exc
        actor_id = require_actor_id(resolved_actor)
        credentials = await _resolve_credentials(app_state, args)
        connection = await connection_service_of(app_state).create_connection(
            name=args.name,
            connection_type=connection_type,
            auth_method=args.auth_method,
            credentials=credentials,
            actor_id=actor_id,
            base_url=args.base_url,
            metadata=args.metadata,
        )
        logger.info(
            MCP_ADMIN_OP_EXECUTED,
            tool_name=tool,
            actor_agent_id=actor_id,
            reason=reason,
            connection_name=args.name,
        )
        logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
        return ok(connection.model_dump(mode="json"))
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


async def _connections_delete(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Delete a connection (destructive; enforces guardrails).

    Returns:
        Resulting string.
    """
    tool = "synthorg_connections_delete"
    try:
        reason, resolved_actor = require_admin_guardrails(arguments, actor)
        name = typed_args(arguments, ConnectionsDeleteArgs).name
        actor_id = require_actor_id(resolved_actor)
        await connection_service_of(app_state).delete_connection(
            name=name,
            actor_id=actor_id,
            reason=reason,
        )
        logger.info(
            MCP_ADMIN_OP_EXECUTED,
            tool_name=tool,
            actor_agent_id=actor_id,
            reason=reason,
            connection_name=name,
        )
        logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
        return ok(None)
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


async def _connections_check_health(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Run an on-demand health probe against a connection.

    Returns:
        Resulting string.
    """
    tool = "synthorg_connections_check_health"
    try:
        name = typed_args(arguments, ConnectionsCheckHealthArgs).name
        connection = await connection_service_of(app_state).check_health(name=name)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    if connection is None:
        missing = NotFoundError(f"Connection {name} not found")
        log_handler_invoke_failed(tool, missing, connection_name=name)
        return err(missing, domain_code="not_found")
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(connection.model_dump(mode="json"))


async def _connections_field_metadata(
    *,
    app_state: AppState,  # noqa: ARG001 -- static registry, no app state needed
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return the connection-type + credential-field metadata registry.

    Returns:
        Resulting string. The payload is the ordered per-type metadata the
        setup flow prompts from and the dashboard form renders, so a
        conversational caller can discover a connection type's fields without
        hard-coding them.
    """
    tool = "synthorg_connections_field_metadata"
    try:
        typed_args(arguments, ConnectionsFieldMetadataArgs)
        entries = [
            metadata.model_dump(mode="json")
            for metadata in list_connection_type_metadata()
        ]
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(entries)


async def _connections_request_secret_capture(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Register a masked secret-capture request for the in-chat setup flow.

    Raised by the operator console when it reaches a secret field mid-setup:
    it records a *pending* capture the dashboard renders as a masked input and
    posts out of band under ``draft_id``. The request carries no value, and the
    field's kind and label come from the metadata registry, not the caller, so
    nothing sensitive enters the turn.

    Returns:
        Resulting string acknowledging the pending capture.

    Raises:
        ArgumentValidationError: When ``connection_type`` is unknown or
            ``field_name`` is not a secret field of that type (caught and
            returned as an error payload).
    """
    tool = "synthorg_connections_request_secret_capture"
    try:
        args = typed_args(arguments, ConnectionsRequestSecretCaptureArgs)
        try:
            connection_type = ConnectionType(args.connection_type)
        except ValueError as exc:
            bad = ArgumentValidationError(_ARG_CONNECTION_TYPE, _TY_CONNECTION_TYPE)
            raise bad from exc
        metadata = get_connection_type_metadata(connection_type)
        field = next(
            (f for f in metadata.fields if f.name == args.field_name and f.secret),
            None,
        )
        if field is None:
            not_secret = f"a secret field of connection type {connection_type.value!r}"
            raise ArgumentValidationError(_ARG_FIELD_NAME, not_secret)
        secret_capture_service_of(app_state).register_pending(
            PendingSecretCapture(
                draft_id=args.draft_id,
                connection_type=NotBlankStr(connection_type.value),
                field_name=field.name,
                secret_kind=field.name,
                label=field.label,
            )
        )
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(
        SECRET_CAPTURE_REQUESTED,
        draft_id=args.draft_id,
        field=field.name,
        connection_type=connection_type.value,
    )
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(
        {
            "status": "capture_requested",
            "field": field.name,
            "message": (
                f"Masked capture requested for {field.label!r}. Wait for the "
                "operator to provide it out of band; the handle arrives on a "
                "later turn. Do not call connections.create until then."
            ),
        }
    )


CONNECTIONS_HANDLERS: Mapping[str, ToolHandler] = MappingProxyType(
    {
        "synthorg_connections_list": _connections_list,
        "synthorg_connections_field_metadata": _connections_field_metadata,
        "synthorg_connections_get": _connections_get,
        "synthorg_connections_create": _connections_create,
        "synthorg_connections_delete": _connections_delete,
        "synthorg_connections_check_health": _connections_check_health,
        "synthorg_connections_request_secret_capture": (
            _connections_request_secret_capture
        ),
    },
)
