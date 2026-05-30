"""Simulation-scenario MCP handlers (infrastructure sub-domain)."""

from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from synthorg.communication.mcp_errors import CapabilityNotSupportedError
from synthorg.infrastructure.state import simulation_facade_service_of
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
    _require_str,
    _to_jsonable,
)
from synthorg.observability import get_logger

if TYPE_CHECKING:
    from collections.abc import Mapping

    from synthorg.core.agent import AgentIdentity

logger = get_logger(__name__)


async def _simulations_list(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """List simulation scenarios loaded at start-up.

    Returns:
        Resulting string.
    """
    tool = "synthorg_simulations_list"
    try:
        offset, limit = coerce_pagination(arguments)
        page, total = await simulation_facade_service_of(app_state).list_simulations(
            offset=offset,
            limit=limit,
        )
        pagination = PaginationMeta(total=total, offset=offset, limit=limit)
        return ok([_to_jsonable(s) for s in page], pagination=pagination)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)


async def _simulations_get(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Fetch a single simulation scenario by ID.

    Returns:
        Resulting string.
    """
    tool = "synthorg_simulations_get"
    try:
        sim_id = _require_str(arguments, "simulation_id")
        sim = await simulation_facade_service_of(app_state).get_simulation(sim_id)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    if sim is None:
        return err(
            LookupError(f"Simulation {sim_id} not found"),
            domain_code="not_found",
        )
    return ok(_to_jsonable(sim))


async def _simulations_create(
    *,
    app_state: Any,
    arguments: dict[str, Any],  # noqa: ARG001
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Capability gap: simulation scenarios are config-driven.

    Returns:
        Resulting string.
    """
    tool = "synthorg_simulations_create"
    try:
        await simulation_facade_service_of(app_state).create_simulation()
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok(None)


SIMULATIONS_HANDLERS: Mapping[str, ToolHandler] = MappingProxyType(
    {
        "synthorg_simulations_list": _simulations_list,
        "synthorg_simulations_get": _simulations_get,
        "synthorg_simulations_create": _simulations_create,
    },
)
