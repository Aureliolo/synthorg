"""Backup MCP handlers (infrastructure sub-domain)."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING

from synthorg.backup.models import BackupTrigger
from synthorg.communication.mcp_errors import CapabilityNotSupportedError
from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import ConflictError
from synthorg.idempotency import IdempotencyService
from synthorg.infrastructure.state import backup_facade_service_of
from synthorg.meta.mcp.domains._remaining_args import (
    BackupCreateArgs,
    BackupDeleteArgs,
    BackupGetArgs,
    BackupListArgs,
    BackupRestoreArgs,
)
from synthorg.meta.mcp.errors import (
    ArgumentValidationError,
    GuardrailViolationError,
)
from synthorg.meta.mcp.handler_protocol import ToolHandler
from synthorg.meta.mcp.handlers._mcp_handler_common import typed_args
from synthorg.meta.mcp.handlers.common import (
    PaginationMeta,
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
from synthorg.meta.mcp.handlers.infrastructure._shared import (
    _ARG_TRIGGER,
    _TY_BACKUP_TRIGGER,
    _map_capability,
    _to_jsonable,
)
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import (
    MCP_ADMIN_OP_EXECUTED,
    MCP_HANDLER_INVOKE_SUCCESS,
)
from synthorg.persistence.state import persistence_of

if TYPE_CHECKING:
    from synthorg.api.state import AppState

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
        page_args = typed_args(arguments, BackupListArgs)
        offset, limit = page_args.offset, page_args.limit
        page, total = await backup_facade_service_of(app_state).list_backups(
            offset=offset,
            limit=limit,
        )
        pagination = PaginationMeta(total=total, offset=offset, limit=limit)
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok([_to_jsonable(b) for b in page], pagination=pagination)


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
        backup_id = typed_args(arguments, BackupGetArgs).backup_id
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
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
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
        trigger_raw = typed_args(arguments, BackupCreateArgs).trigger
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
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
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
        backup_id = typed_args(arguments, BackupDeleteArgs).backup_id
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
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
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
        args = typed_args(arguments, BackupRestoreArgs)
        backup_id = args.backup_id
        actor_id = require_actor_id(resolved_actor)

        async def _restore() -> dict[str, object]:
            result = await backup_facade_service_of(app_state).restore_backup(
                backup_id=backup_id,
                actor_id=actor_id,
                reason=reason,
            )
            # Logged inside the callback so a cache-hit (idempotent retry)
            # does not record a second admin-op execution for a restore
            # that never actually ran.
            logger.info(
                MCP_ADMIN_OP_EXECUTED,
                tool_name=tool,
                actor_agent_id=actor_id,
                reason=reason,
                backup_id=backup_id,
            )
            return dict(result)

        # ``meta`` cannot import ``api.services`` (layering contract), so
        # the handler constructs the service over the neutral repository
        # instead of reaching for the api-side ``idempotency_service_of``
        # accessor; the dedup state lives in the shared repo, so a
        # per-call instance is equivalent.
        # Thread the app clock seam so the in-flight poll honours an
        # injected FakeClock in tests rather than waiting in real time.
        service = IdempotencyService(
            persistence_of(app_state).idempotency_keys,
            clock=app_state.clock,
        )
        outcome = await service.run_idempotent(
            scope="mcp:backup_restore",
            key=f"{backup_id}:{args.idempotency_key}",
            callback=_restore,
        )
        if outcome.timed_out:
            msg = "Concurrent in-flight restore with this idempotency key"
            return err(ConflictError(msg), domain_code="conflict")
        payload = outcome.result
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
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(payload)


BACKUP_HANDLERS: Mapping[str, ToolHandler] = MappingProxyType(
    {
        "synthorg_backup_create": _backup_create,
        "synthorg_backup_list": _backup_list,
        "synthorg_backup_get": _backup_get,
        "synthorg_backup_delete": _backup_delete,
        "synthorg_backup_restore": _backup_restore,
    },
)
