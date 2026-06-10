"""Agent CRUD + observability MCP handlers.

Shims agent create / read / update / delete and the performance,
activity, history, and health reads onto the HR services. The delete
handler enforces the admin guardrail triple and emits
``MCP_ADMIN_OP_EXECUTED`` on success.
"""

from typing import TYPE_CHECKING

from pydantic import ValidationError

from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.hr.errors import (
    AgentAlreadyRegisteredError,
    AgentNotFoundError,
)
from synthorg.hr.state import (
    HrStateSlice,
    activity_feed_service_of,
    agent_health_service_of,
    agent_registry_of,
    agent_version_service_of,
    performance_tracker_of,
)
from synthorg.meta.mcp.errors import (
    ArgumentValidationError,
    GuardrailViolationError,
)
from synthorg.meta.mcp.handlers.common import (
    PaginationMeta,
    capability_gap,
    dump_many,
    err,
    ok,
    paginate_sequence,
    require_admin_guardrails,
)
from synthorg.meta.mcp.handlers.common_args import (
    actor_id,
    coerce_pagination,
    require_arg,
    require_non_blank,
)
from synthorg.meta.mcp.handlers.common_logging import (
    log_handler_argument_invalid,
    log_handler_guardrail_violated,
    log_handler_invoke_failed,
)
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import (
    MCP_ADMIN_OP_EXECUTED,
    MCP_HANDLER_INVOKE_SUCCESS,
)

if TYPE_CHECKING:
    from synthorg.api.state import AppState

logger = get_logger(__name__)

_ARG_AGENT_NAME = "agent_name"
_ARG_AGENT_ID = "agent_id"

_WHY_ACTIVITY = (
    "activity feed derivation lives in hr.activity module; no "
    "streaming endpoint on app_state"
)
_WHY_HISTORY = (
    "career history reads via agent_identity_versions controller; "
    "not exposed on app_state"
)
_WHY_HEALTH = "agent health aggregation has no dedicated service method"


async def _agents_list(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Handle the ``synthorg_agents_list`` MCP tool.

    Returns:
        JSON-encoded MCP envelope string.
    """
    tool = "synthorg_agents_list"
    try:
        offset, limit = coerce_pagination(arguments)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    try:
        agents = await agent_registry_of(app_state).list_active()
        page, meta = paginate_sequence(agents, offset=offset, limit=limit)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=dump_many(page), pagination=meta)


async def _agents_get(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Handle the ``synthorg_agents_get`` MCP tool.

    Returns:
        JSON-encoded MCP envelope string.
    """
    tool = "synthorg_agents_get"
    try:
        name = require_non_blank(arguments, _ARG_AGENT_NAME)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    try:
        identity = await agent_registry_of(app_state).get_by_name(name)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    if identity is None:
        missing = AgentNotFoundError(f"Agent {name!r} not found")
        log_handler_invoke_failed(tool, missing)
        return err(missing, domain_code="not_found")
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=identity.model_dump(mode="json"))


async def _agents_create(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Handle the ``synthorg_agents_create`` MCP tool.

    Returns:
        JSON-encoded MCP envelope string.
    """
    tool = "synthorg_agents_create"
    try:
        identity_dict = require_arg(arguments, "identity", dict)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)

    try:
        identity = AgentIdentity.model_validate(identity_dict)
    except ValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc, domain_code="invalid_argument")

    saved_by = actor_id(actor) or "mcp"
    try:
        await agent_registry_of(app_state).register(identity, saved_by=saved_by)
    except AgentAlreadyRegisteredError as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc, domain_code="already_exists")
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=identity.model_dump(mode="json"))


async def _agents_update(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Handle the ``synthorg_agents_update`` MCP tool.

    Returns:
        JSON-encoded MCP envelope string.
    """
    tool = "synthorg_agents_update"
    try:
        agent_id = require_non_blank(arguments, _ARG_AGENT_ID)
        updates = require_arg(arguments, "updates", dict)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)

    saved_by = actor_id(actor) or "mcp"
    try:
        updated = await agent_registry_of(app_state).apply_identity_update(
            NotBlankStr(agent_id),
            updates,
            saved_by=saved_by,
        )
    except AgentNotFoundError as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc, domain_code="not_found")
    except ValueError as exc:
        # Blocked-field rejection from the registry surfaces here.
        log_handler_argument_invalid(tool, exc)
        return err(exc, domain_code="invalid_argument")
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=updated.model_dump(mode="json"))


async def _agents_delete(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Handle the ``synthorg_agents_delete`` MCP tool.

    Returns:
        JSON-encoded MCP envelope string.
    """
    tool = "synthorg_agents_delete"
    try:
        reason, resolved_actor = require_admin_guardrails(arguments, actor)
        agent_name = require_non_blank(arguments, _ARG_AGENT_NAME)
        identity = await agent_registry_of(app_state).get_by_name(agent_name)
        if identity is None:
            missing = AgentNotFoundError(f"Agent {agent_name!r} not found")
            log_handler_invoke_failed(tool, missing)
            return err(missing, domain_code="not_found")
        removed = await agent_registry_of(app_state).unregister(str(identity.id))
    except GuardrailViolationError as exc:
        log_handler_guardrail_violated(tool, exc)
        return err(exc)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except AgentNotFoundError as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc, domain_code="not_found")
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)

    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    logger.info(
        MCP_ADMIN_OP_EXECUTED,
        tool_name=tool,
        actor_agent_id=actor_id(resolved_actor),
        reason=reason,
        target_id=str(removed.id),
    )
    return ok(data=removed.model_dump(mode="json"))


async def _agents_get_performance(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Handle the ``synthorg_agents_get_performance`` MCP tool.

    Returns:
        JSON-encoded MCP envelope string.
    """
    tool = "synthorg_agents_get_performance"
    try:
        agent_name = require_non_blank(arguments, _ARG_AGENT_NAME)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    try:
        identity = await agent_registry_of(app_state).get_by_name(agent_name)
        if identity is None:
            missing = AgentNotFoundError(f"Agent {agent_name!r} not found")
            log_handler_invoke_failed(tool, missing)
            return err(missing, domain_code="not_found")
        snapshot = await performance_tracker_of(app_state).get_snapshot(
            str(identity.id),
        )
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=snapshot.model_dump(mode="json"))


async def _agents_get_activity(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Handle the ``synthorg_agents_get_activity`` MCP tool.

    Returns:
        JSON-encoded MCP envelope string.
    """
    tool = "synthorg_agents_get_activity"
    try:
        agent_name = require_non_blank(arguments, _ARG_AGENT_NAME)
        offset, limit = coerce_pagination(arguments)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    if app_state.slice(HrStateSlice).activity_feed_service is None:
        return capability_gap(tool, _WHY_ACTIVITY)
    try:
        identity = await agent_registry_of(app_state).get_by_name(agent_name)
        if identity is None:
            missing = AgentNotFoundError(f"Agent {agent_name!r} not found")
            log_handler_invoke_failed(tool, missing)
            return err(missing, domain_code="not_found")
        events, total = await activity_feed_service_of(app_state).get_agent_activity(
            NotBlankStr(str(identity.id)),
            offset=offset,
            limit=limit,
        )
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    meta = PaginationMeta(total=total, offset=offset, limit=limit)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=dump_many(events), pagination=meta)


async def _agents_get_history(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Handle the ``synthorg_agents_get_history`` MCP tool.

    Returns:
        JSON-encoded MCP envelope string.
    """
    tool = "synthorg_agents_get_history"
    try:
        agent_name = require_non_blank(arguments, _ARG_AGENT_NAME)
        offset, limit = coerce_pagination(arguments)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    if app_state.slice(HrStateSlice).agent_version_service is None:
        return capability_gap(tool, _WHY_HISTORY)
    try:
        identity = await agent_registry_of(app_state).get_by_name(agent_name)
        if identity is None:
            missing = AgentNotFoundError(f"Agent {agent_name!r} not found")
            log_handler_invoke_failed(tool, missing)
            return err(missing, domain_code="not_found")
        versions, total = await agent_version_service_of(app_state).list_versions(
            NotBlankStr(str(identity.id)),
            offset=offset,
            limit=limit,
        )
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    meta = PaginationMeta(total=total, offset=offset, limit=limit)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=dump_many(versions), pagination=meta)


async def _agents_get_health(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Handle the ``synthorg_agents_get_health`` MCP tool.

    Returns:
        JSON-encoded MCP envelope string.
    """
    tool = "synthorg_agents_get_health"
    try:
        agent_name = require_non_blank(arguments, _ARG_AGENT_NAME)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    if app_state.slice(HrStateSlice).agent_health_service is None:
        return capability_gap(tool, _WHY_HEALTH)
    try:
        identity = await agent_registry_of(app_state).get_by_name(agent_name)
        if identity is None:
            missing = AgentNotFoundError(f"Agent {agent_name!r} not found")
            log_handler_invoke_failed(tool, missing)
            return err(missing, domain_code="not_found")
        report = await agent_health_service_of(app_state).get_agent_health(
            NotBlankStr(str(identity.id)),
        )
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=report.model_dump(mode="json"))
