"""Backup MCP handlers (infrastructure sub-domain)."""

from collections.abc import Mapping
from types import MappingProxyType

from synthorg.api.state import AppState
from synthorg.backup.models import BackupTrigger
from synthorg.communication.mcp_errors import CapabilityNotSupportedError
from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.infrastructure.state import backup_facade_service_of
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
from synthorg.meta.mcp.handlers.common_args import (
    coerce_pagination,
    require_actor_id,
    require_arg,
)
from synthorg.meta.mcp.handlers.common_logging import (
    log_handler_argument_invalid,
    log_handler_guardrail_violated,
    log_handler_invoke_failed,
)
from synthorg.meta.mcp.handlers.infrastructure._shared import (
    _ARG_TRIGGER,
    _TY_BACKUP_TRIGGER,
    _map_capability,
    _require_str,
    _to_jsonable,
)
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import MCP_ADMIN_OP_EXECUTED

logger = get_logger(__name__)


async def _backup_list(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """List persistence backups recorded by the backup service.

    Returns:
        Resulting string.
    """
    tool = "synthorg_backup_list"
    try:
        offset, limit = coerce_pagination(arguments)
        page, total = await backup_facade_service_of(app_state).list_backups(
            offset=offset,
            limit=limit,
        )
        pagination = PaginationMeta(total=total, offset=offset, limit=limit)
        return ok([_to_jsonable(b) for b in page], pagination=pagination)
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)


async def _backup_get(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Fetch a single backup record by ID.

    Returns:
        Resulting string.
    """
    tool = "synthorg_backup_get"
    try:
        backup_id = _require_str(arguments, "backup_id")
        manifest = await backup_facade_service_of(app_state).get_backup(backup_id)
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except LookupError as exc:
        return err(exc, domain_code="not_found")
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok(_to_jsonable(manifest))


async def _backup_create(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Trigger a new backup run (admin op; records a new manifest).

    Returns:
        Resulting string.

    Raises:
        ArgumentValidationError: Raised on the corresponding failure path.
    """
    tool = "synthorg_backup_create"
    try:
        reason, resolved_actor = require_admin_guardrails(arguments, actor)
        trigger_raw = require_arg(arguments, _ARG_TRIGGER, str)
        try:
            trigger = BackupTrigger(trigger_raw)
        except ValueError as exc:
            raise ArgumentValidationError(_ARG_TRIGGER, _TY_BACKUP_TRIGGER) from exc
        manifest = await backup_facade_service_of(app_state).create_backup(
            trigger=trigger,
        )
        logger.info(
            MCP_ADMIN_OP_EXECUTED,
            tool_name=tool,
            actor_agent_id=require_actor_id(resolved_actor),
            reason=reason,
            trigger=trigger.value,
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
    return ok(_to_jsonable(manifest))


async def _backup_delete(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Delete a backup manifest (destructive; enforces guardrails).

    Returns:
        Resulting string.
    """
    tool = "synthorg_backup_delete"
    try:
        reason, resolved_actor = require_admin_guardrails(arguments, actor)
        backup_id = _require_str(arguments, "backup_id")
        actor_id = require_actor_id(resolved_actor)
        await backup_facade_service_of(app_state).delete_backup(
            backup_id=backup_id,
            actor_id=actor_id,
            reason=reason,
        )
        logger.info(
            MCP_ADMIN_OP_EXECUTED,
            tool_name=tool,
            actor_agent_id=actor_id,
            reason=reason,
            backup_id=backup_id,
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
    return ok(None)


async def _backup_restore(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Restore persistence state from a backup (destructive; enforces guardrails).

    Returns:
        Resulting string.
    """
    tool = "synthorg_backup_restore"
    try:
        reason, resolved_actor = require_admin_guardrails(arguments, actor)
        backup_id = _require_str(arguments, "backup_id")
        actor_id = require_actor_id(resolved_actor)
        result = await backup_facade_service_of(app_state).restore_backup(
            backup_id=backup_id,
            actor_id=actor_id,
            reason=reason,
        )
        logger.info(
            MCP_ADMIN_OP_EXECUTED,
            tool_name=tool,
            actor_agent_id=actor_id,
            reason=reason,
            backup_id=backup_id,
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
    return ok(dict(result))


BACKUP_HANDLERS: Mapping[str, ToolHandler] = MappingProxyType(
    {
        "synthorg_backup_create": _backup_create,
        "synthorg_backup_list": _backup_list,
        "synthorg_backup_get": _backup_get,
        "synthorg_backup_delete": _backup_delete,
        "synthorg_backup_restore": _backup_restore,
    },
)
