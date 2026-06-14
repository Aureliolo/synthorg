"""OAuth-provider and external-client integration handlers.

OAuth provider configuration (list / configure / remove) through
:func:`oauth_facade_service_of`, plus external client management
(list / get / create / deactivate / satisfaction) through
:func:`client_facade_service_of`. The destructive ``remove_provider`` /
``deactivate`` paths enforce the admin guardrail triple and emit
``MCP_ADMIN_OP_EXECUTED`` on success.
"""

from typing import TYPE_CHECKING
from uuid import UUID

from synthorg.communication.mcp_errors import CapabilityNotSupportedError
from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.infrastructure.state import (
    client_facade_service_of,
    oauth_facade_service_of,
)
from synthorg.meta.mcp.domains._remaining_args import (
    ClientsCreateArgs,
    ClientsDeactivateArgs,
    ClientsGetArgs,
    ClientsGetSatisfactionArgs,
    ClientsListArgs,
    OauthConfigureProviderArgs,
    OauthRemoveProviderArgs,
)
from synthorg.meta.mcp.errors import (
    ArgumentValidationError,
    GuardrailViolationError,
)
from synthorg.meta.mcp.handlers._mcp_handler_common import (
    _map_capability,
    _to_jsonable,
    typed_args,
)
from synthorg.meta.mcp.handlers.common import (
    err,
    ok,
    paginate_sequence,
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
from synthorg.observability.events.mcp import MCP_ADMIN_OP_EXECUTED

if TYPE_CHECKING:
    from synthorg.api.state import AppState

logger = get_logger(__name__)

_ARG_CLIENT_ID = "client_id"
_TY_UUID = "UUID string"


async def _oauth_list_providers(
    *,
    app_state: AppState,
    arguments: dict[str, object],  # noqa: ARG001
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """List configured OAuth providers.

    Returns:
        Resulting string.
    """
    tool = "synthorg_oauth_list_providers"
    try:
        providers = await oauth_facade_service_of(app_state).list_providers()
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok([_to_jsonable(p) for p in providers])


async def _oauth_configure_provider(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
    # lint-allow: mcp-admin-guardrail -- creds shape varies; remove path is guardrailed
) -> str:
    """Configure an OAuth provider (creates or updates credentials).

    Returns:
        Resulting string.
    """
    tool = "synthorg_oauth_configure_provider"
    try:
        args = typed_args(arguments, OauthConfigureProviderArgs)
        record = await oauth_facade_service_of(app_state).configure_provider(
            name=args.name,
            client_id=args.client_id,
            authorize_url=args.authorize_url,
            token_url=args.token_url,
            scopes=args.scopes,
            actor_id=require_actor_id(actor),
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
    return ok(record.to_dict())


async def _oauth_remove_provider(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Remove an OAuth provider (destructive; enforces guardrails).

    Returns:
        Resulting string.
    """
    tool = "synthorg_oauth_remove_provider"
    try:
        reason, resolved_actor = require_admin_guardrails(arguments, actor)
        name = typed_args(arguments, OauthRemoveProviderArgs).name
        actor_id = require_actor_id(resolved_actor)
        removed = await oauth_facade_service_of(app_state).remove_provider(
            name=name,
            actor_id=actor_id,
            reason=reason,
        )
        if removed:
            logger.info(
                MCP_ADMIN_OP_EXECUTED,
                tool_name=tool,
                actor_agent_id=actor_id,
                reason=reason,
                provider_name=name,
                removed=removed,
            )
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
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
    return ok({"removed": removed})


async def _clients_list(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """List registered client applications.

    Returns:
        Resulting string.
    """
    tool = "synthorg_clients_list"
    try:
        page_args = typed_args(arguments, ClientsListArgs)
        offset, limit = page_args.offset, page_args.limit
        clients = await client_facade_service_of(app_state).list_clients()
        page, pagination = paginate_sequence(
            clients,
            offset=offset,
            limit=limit,
            total=len(clients),
        )
        return ok([c.to_dict() for c in page], pagination=pagination)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)


async def _clients_get(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Fetch a single client by ID.

    Returns:
        Resulting string.

    Raises:
        ArgumentValidationError: When ``client_id`` is not a UUID string.
    """
    tool = "synthorg_clients_get"
    try:
        client_id = typed_args(arguments, ClientsGetArgs).client_id
        try:
            UUID(client_id)
        except ValueError as uuid_exc:
            raise ArgumentValidationError(_ARG_CLIENT_ID, _TY_UUID) from uuid_exc
        client = await client_facade_service_of(app_state).get_client(client_id)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    if client is None:
        return err(
            LookupError(f"Client {client_id} not found"),
            domain_code="not_found",
        )
    return ok(client.to_dict())


async def _clients_create(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
    # lint-allow: mcp-admin-guardrail -- non-destructive client registration
) -> str:
    """Create a new client application (non-destructive write).

    Returns:
        Resulting string.
    """
    tool = "synthorg_clients_create"
    try:
        args = typed_args(arguments, ClientsCreateArgs)
        client = await client_facade_service_of(app_state).create_client(
            name=args.name,
            actor_id=require_actor_id(actor),
            contact_email=args.contact_email,
            notes=args.notes,
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
    return ok(client.to_dict())


async def _clients_deactivate(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Deactivate a client (destructive; enforces guardrails).

    Returns:
        Resulting string.

    Raises:
        ArgumentValidationError: When ``client_id`` is not a UUID string.
    """
    tool = "synthorg_clients_deactivate"
    try:
        reason, resolved_actor = require_admin_guardrails(arguments, actor)
        client_id = typed_args(arguments, ClientsDeactivateArgs).client_id
        try:
            UUID(client_id)
        except ValueError as uuid_exc:
            raise ArgumentValidationError(_ARG_CLIENT_ID, _TY_UUID) from uuid_exc
        actor_id = require_actor_id(resolved_actor)
        deactivated = await client_facade_service_of(app_state).deactivate_client(
            client_id=client_id,
            actor_id=actor_id,
            reason=reason,
        )
        if deactivated:
            logger.info(
                MCP_ADMIN_OP_EXECUTED,
                tool_name=tool,
                actor_agent_id=actor_id,
                reason=reason,
                client_id=client_id,
                deactivated=deactivated,
            )
    except GuardrailViolationError as exc:
        log_handler_guardrail_violated(tool, exc)
        return err(exc)
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok({"deactivated": deactivated})


async def _clients_get_satisfaction(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return the client satisfaction score (roll-up over recent interactions).

    Returns:
        Resulting string.

    Raises:
        ArgumentValidationError: When ``client_id`` is not a UUID string.
    """
    tool = "synthorg_clients_get_satisfaction"
    try:
        client_id = typed_args(arguments, ClientsGetSatisfactionArgs).client_id
        try:
            UUID(client_id)
        except ValueError as uuid_exc:
            raise ArgumentValidationError(_ARG_CLIENT_ID, _TY_UUID) from uuid_exc
        result = await client_facade_service_of(app_state).get_satisfaction(client_id)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok(dict(result))
