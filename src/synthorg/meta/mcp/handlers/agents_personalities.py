"""Personality MCP handlers.

Shims the personality registry reads onto the HR personality service.
Each handler degrades to a ``capability_gap`` envelope when the service
is not wired on ``app_state``.
"""

from typing import TYPE_CHECKING

from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.hr.errors import PersonalityNotFoundError
from synthorg.hr.state import (
    HrStateSlice,
    personality_service_of,
)
from synthorg.meta.mcp.domains._agents_args import (
    PersonalitiesGetArgs,
    PersonalitiesListArgs,
)
from synthorg.meta.mcp.errors import ArgumentValidationError
from synthorg.meta.mcp.handlers._mcp_handler_common import typed_args
from synthorg.meta.mcp.handlers.common import (
    PaginationMeta,
    capability_gap,
    dump_many,
    err,
    ok,
)
from synthorg.meta.mcp.handlers.common_logging import (
    log_handler_argument_invalid,
    log_handler_invoke_failed,
)
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import MCP_HANDLER_INVOKE_SUCCESS

if TYPE_CHECKING:
    from synthorg.api.state import AppState

logger = get_logger(__name__)

_WHY_PERSONALITY_NOT_WIRED = (
    "personality_service is not wired on app_state in this deployment"
)


async def _personalities_list(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Handle the ``synthorg_personalities_list`` MCP tool.

    Returns:
        JSON-encoded MCP envelope string.
    """
    tool = "synthorg_personalities_list"
    try:
        page = typed_args(arguments, PersonalitiesListArgs)
        offset, limit = page.offset, page.limit
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    if app_state.slice(HrStateSlice).personality_service is None:
        return capability_gap(tool, _WHY_PERSONALITY_NOT_WIRED)
    try:
        entries, total = await personality_service_of(app_state).list_personalities(
            offset=offset,
            limit=limit,
        )
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    meta = PaginationMeta(total=total, offset=offset, limit=limit)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=dump_many(entries), pagination=meta)


async def _personalities_get(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Handle the ``synthorg_personalities_get`` MCP tool.

    Returns:
        JSON-encoded MCP envelope string.
    """
    tool = "synthorg_personalities_get"
    try:
        name = typed_args(arguments, PersonalitiesGetArgs).name
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    if app_state.slice(HrStateSlice).personality_service is None:
        return capability_gap(tool, _WHY_PERSONALITY_NOT_WIRED)
    try:
        entry = await personality_service_of(app_state).get_personality(
            NotBlankStr(name),
        )
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    if entry is None:
        missing = PersonalityNotFoundError(f"Personality {name!r} not found")
        log_handler_invoke_failed(tool, missing)
        return err(missing, domain_code="not_found")
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=entry.model_dump(mode="json"))
