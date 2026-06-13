"""Provider MCP handlers (infrastructure sub-domain)."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING

from synthorg.communication.mcp_errors import CapabilityNotSupportedError
from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.infrastructure.state import provider_read_service_of
from synthorg.meta.mcp.domains._remaining_args import ProvidersTestConnectionArgs
from synthorg.meta.mcp.errors import (
    ArgumentValidationError,
    GuardrailViolationError,
)
from synthorg.meta.mcp.handler_protocol import ToolHandler
from synthorg.meta.mcp.handlers._mcp_handler_common import typed_args
from synthorg.meta.mcp.handlers.common import err, ok, require_admin_guardrails
from synthorg.meta.mcp.handlers.common_args import get_optional_str, require_actor_id
from synthorg.meta.mcp.handlers.common_logging import (
    log_handler_argument_invalid,
    log_handler_guardrail_violated,
    log_handler_invoke_failed,
)
from synthorg.meta.mcp.handlers.infrastructure._shared import (
    _map_capability,
    _require_str,
    _to_jsonable,
)
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import MCP_ADMIN_OP_EXECUTED

if TYPE_CHECKING:
    from synthorg.api.state import AppState

logger = get_logger(__name__)


async def _providers_list(
    *,
    app_state: AppState,
    arguments: dict[str, object],  # noqa: ARG001
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """List registered LLM providers.

    Returns:
        Resulting string.
    """
    tool = "synthorg_providers_list"
    try:
        providers = await provider_read_service_of(app_state).list_providers()
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


# lint-allow: handler-arguments-get -- cataloged mismatch: handler reads
# `provider_id` but ProvidersGetArgs declares `provider_name`.
async def _providers_get(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Fetch a single provider registration by ID.

    Returns:
        Resulting string.
    """
    tool = "synthorg_providers_get"
    try:
        provider_id = _require_str(arguments, "provider_id")
        provider = await provider_read_service_of(app_state).get_provider(provider_id)
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    if provider is None:
        return err(
            LookupError(f"Provider {provider_id} not found"),
            domain_code="not_found",
        )
    return ok(_to_jsonable(provider))


# lint-allow: handler-arguments-get -- cataloged mismatch: handler reads an
# optional `provider_id`, but ProvidersGetHealthArgs declares a required
# `provider_name`.
async def _providers_get_health(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return provider-health roll-up (availability, latency, error rate).

    Returns:
        Resulting string.
    """
    tool = "synthorg_providers_get_health"
    try:
        provider_id = get_optional_str(arguments, "provider_id")
        result = await provider_read_service_of(app_state).get_health(provider_id)
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok({k: _to_jsonable(v) for k, v in dict(result).items()})


async def _providers_test_connection(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Perform an on-demand connectivity probe against a provider (admin op).

    Returns:
        Resulting string.
    """
    tool = "synthorg_providers_test_connection"
    try:
        reason, resolved_actor = require_admin_guardrails(arguments, actor)
        provider_name = typed_args(arguments, ProvidersTestConnectionArgs).provider_name
        provider_read = provider_read_service_of(app_state)
        result = await provider_read.test_connection(provider_name)
        logger.info(
            MCP_ADMIN_OP_EXECUTED,
            tool_name=tool,
            actor_agent_id=require_actor_id(resolved_actor),
            reason=reason,
            provider_name=provider_name,
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
    return ok({k: _to_jsonable(v) for k, v in dict(result).items()})


PROVIDERS_HANDLERS: Mapping[str, ToolHandler] = MappingProxyType(
    {
        "synthorg_providers_list": _providers_list,
        "synthorg_providers_get": _providers_get,
        "synthorg_providers_get_health": _providers_get_health,
        "synthorg_providers_test_connection": _providers_test_connection,
    },
)
