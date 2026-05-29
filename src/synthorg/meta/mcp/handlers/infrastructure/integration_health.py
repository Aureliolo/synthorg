"""Integration-health MCP handlers (infrastructure sub-domain)."""

from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from synthorg.infrastructure.state import integration_health_facade_service_of
from synthorg.meta.mcp.errors import ArgumentValidationError
from synthorg.meta.mcp.handler_protocol import ToolHandler
from synthorg.meta.mcp.handlers.common import err, ok
from synthorg.meta.mcp.handlers.common_logging import (
    log_handler_argument_invalid,
    log_handler_invoke_failed,
)
from synthorg.meta.mcp.handlers.infrastructure._shared import (
    _require_str,
    _to_jsonable,
)
from synthorg.observability import get_logger

if TYPE_CHECKING:
    from collections.abc import Mapping

    from synthorg.core.agent import AgentIdentity

logger = get_logger(__name__)


async def _integration_health_get_all(
    *,
    app_state: Any,
    arguments: dict[str, Any],  # noqa: ARG001
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return health roll-ups for every integration.

    Returns:
        Resulting string.
    """
    tool = "synthorg_integration_health_get_all"
    try:
        snapshot = await integration_health_facade_service_of(app_state).get_all()
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok({k: _to_jsonable(v) for k, v in dict(snapshot).items()})


async def _integration_health_get(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return the health roll-up for a single integration.

    Returns:
        Resulting string.
    """
    tool = "synthorg_integration_health_get"
    try:
        integration_id = _require_str(arguments, "integration_id")
        status = await integration_health_facade_service_of(app_state).get_one(
            integration_id,
        )
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    if status is None:
        return err(
            LookupError(f"Integration {integration_id} not found"),
            domain_code="not_found",
        )
    return ok(_to_jsonable(status))


INTEGRATION_HEALTH_HANDLERS: Mapping[str, ToolHandler] = MappingProxyType(
    {
        "synthorg_integration_health_get_all": _integration_health_get_all,
        "synthorg_integration_health_get": _integration_health_get,
    },
)
