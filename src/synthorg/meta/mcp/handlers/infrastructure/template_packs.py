"""Template-pack MCP handlers (infrastructure sub-domain)."""

from collections.abc import Mapping
from types import MappingProxyType

from synthorg.api.state import AppState
from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.infrastructure.state import template_pack_facade_service_of
from synthorg.meta.mcp.errors import (
    ArgumentValidationError,
    GuardrailViolationError,
)
from synthorg.meta.mcp.handler_protocol import ToolHandler
from synthorg.meta.mcp.handlers.common import (
    PaginationMeta,
    err,
    ok,
    require_admin_guardrails,
)
from synthorg.meta.mcp.handlers.common_args import coerce_pagination, require_actor_id
from synthorg.meta.mcp.handlers.common_logging import (
    log_handler_argument_invalid,
    log_handler_guardrail_violated,
    log_handler_invoke_failed,
)
from synthorg.meta.mcp.handlers.infrastructure._shared import (
    _require_str,
    _require_uuid,
)
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import MCP_ADMIN_OP_EXECUTED

logger = get_logger(__name__)


async def _template_packs_list(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """List installed template packs.

    Returns:
        Resulting string.
    """
    tool = "synthorg_template_packs_list"
    try:
        offset, limit = coerce_pagination(arguments)
        page, total = await template_pack_facade_service_of(app_state).list_packs(
            offset=offset,
            limit=limit,
        )
        pagination = PaginationMeta(total=total, offset=offset, limit=limit)
        return ok([p.to_dict() for p in page], pagination=pagination)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)


async def _template_packs_get(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Fetch a single template-pack record by ID.

    Returns:
        Resulting string.
    """
    tool = "synthorg_template_packs_get"
    try:
        pack_id = _require_uuid(arguments, "pack_id")
        pack = await template_pack_facade_service_of(app_state).get_pack(pack_id)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    if pack is None:
        return err(
            LookupError(f"Template pack {pack_id} not found"),
            domain_code="not_found",
        )
    return ok(pack.to_dict())


async def _template_packs_install(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Install a new template pack (admin op; enforces guardrails).

    Returns:
        Resulting string.
    """
    tool = "synthorg_template_packs_install"
    try:
        reason, resolved_actor = require_admin_guardrails(arguments, actor)
        name = _require_str(arguments, "name")
        version = _require_str(arguments, "version")
        actor_id = require_actor_id(resolved_actor)
        pack = await template_pack_facade_service_of(app_state).install_pack(
            name=name,
            version=version,
            actor_id=actor_id,
        )
        logger.info(
            MCP_ADMIN_OP_EXECUTED,
            tool_name=tool,
            actor_agent_id=actor_id,
            reason=reason,
            pack_name=name,
            pack_version=version,
        )
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
    return ok(pack.to_dict())


async def _template_packs_uninstall(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Uninstall a template pack (destructive; enforces guardrails).

    Returns:
        Resulting string.
    """
    tool = "synthorg_template_packs_uninstall"
    try:
        reason, resolved_actor = require_admin_guardrails(arguments, actor)
        pack_id = _require_uuid(arguments, "pack_id")
        actor_id = require_actor_id(resolved_actor)
        removed = await template_pack_facade_service_of(app_state).uninstall_pack(
            pack_id=pack_id,
            actor_id=actor_id,
            reason=reason,
        )
        if removed:
            logger.info(
                MCP_ADMIN_OP_EXECUTED,
                tool_name=tool,
                actor_agent_id=actor_id,
                reason=reason,
                pack_id=pack_id,
                removed=removed,
            )
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


TEMPLATE_PACKS_HANDLERS: Mapping[str, ToolHandler] = MappingProxyType(
    {
        "synthorg_template_packs_list": _template_packs_list,
        "synthorg_template_packs_get": _template_packs_get,
        "synthorg_template_packs_install": _template_packs_install,
        "synthorg_template_packs_uninstall": _template_packs_uninstall,
    },
)
