"""Backup MCP handlers (infrastructure sub-domain)."""

import hashlib
from collections.abc import Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING

from pydantic import ValidationError

from synthorg.backup.errors import BackupError, RestoreError
from synthorg.backup.models import BackupTrigger, RestoreConfirmation
from synthorg.communication.mcp_errors import CapabilityNotSupportedError
from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import ConflictError
from synthorg.infrastructure.state import (
    backup_facade_service_of,
    mcp_idempotency_service_of,
)
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
from synthorg.observability.events.idempotency import IDEMPOTENCY_CLAIM_IN_FLIGHT
from synthorg.observability.events.mcp import (
    MCP_ADMIN_OP_EXECUTED,
    MCP_HANDLER_INVOKE_SUCCESS,
)

if TYPE_CHECKING:
    from synthorg.api.state import AppState

logger = get_logger(__name__)


def _restore_idempotency_key(backup_id: str, idempotency_key: str) -> str:
    """Return a fixed-width restore dedup key.

    Hashes ``backup_id:idempotency_key`` into a SHA-256 digest so a
    max-length caller key cannot overflow the durable idempotency store's
    255-char key column, mirroring the REST restore path.

    Returns:
        The 64-char hex dedup key.
    """
    material = f"{backup_id}:{idempotency_key}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _create_idempotency_key(idempotency_key: str) -> str:
    """Return a fixed-width create dedup key.

    A create has no ``backup_id`` yet, so the caller key alone identifies the
    logical request; hashing bounds it to 64 hex chars so a max-length caller
    key cannot overflow the durable store's 255-char key column.

    Returns:
        The 64-char hex dedup key.
    """
    return hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()


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
        args = typed_args(arguments, BackupCreateArgs)
        actor_id = require_actor_id(resolved_actor)
        try:
            trigger = BackupTrigger(args.trigger)
        except ValueError as exc:
            raise ArgumentValidationError(_ARG_TRIGGER, _TY_BACKUP_TRIGGER) from exc

        async def _create() -> object:
            manifest = await backup_facade_service_of(app_state).create_backup(
                trigger=trigger,
            )
            # Logged inside the callback so a cache-hit (idempotent retry)
            # does not record a second admin-op execution for a backup that
            # never actually ran.
            logger.info(
                MCP_ADMIN_OP_EXECUTED,
                tool_name=tool,
                actor_agent_id=actor_id,
                reason=reason,
                trigger=trigger.value,
            )
            return _to_jsonable(manifest)

        # ``meta`` cannot import ``api.services`` (layering contract), so the
        # handler constructs the service over the neutral repository instead
        # of reaching for the api-side ``idempotency_service_of`` accessor;
        # the dedup state lives in the shared repo, so a per-call instance is
        # equivalent. The app clock seam threads an injected FakeClock so the
        # in-flight poll honours test time rather than waiting in real time.
        service = IdempotencyService(
            persistence_of(app_state).idempotency_keys,
            clock=app_state.clock,
        )
        outcome = await service.run_idempotent(
            scope="mcp:backup_create",
            key=_create_idempotency_key(args.idempotency_key),
            callback=_create,
        )
        if outcome.timed_out:
            logger.warning(
                IDEMPOTENCY_CLAIM_IN_FLIGHT,
                scope="mcp:backup_create",
                idempotency_key=args.idempotency_key,
                tool_name=tool,
            )
            msg = "Concurrent in-flight create with this idempotency key"
            return err(ConflictError(msg), domain_code="conflict")
        if not isinstance(outcome.result, dict):
            msg = "Cached create response failed validation; rerun the create"
            log_handler_invoke_failed(tool, TypeError(msg))
            return err(BackupError(msg))
        payload: dict[str, object] = outcome.result
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
        # the handler reads the lazily-cached idempotency service off the
        # meta-reachable facades slice rather than assembling one over the
        # raw persistence repo. The persistence reach lives in the
        # infrastructure-layer accessor; the clock seam is threaded so the
        # in-flight poll honours an injected FakeClock in tests.
        service = mcp_idempotency_service_of(app_state, clock=app_state.clock)
        outcome = await service.run_idempotent(
            scope="mcp:backup_restore",
            key=_restore_idempotency_key(backup_id, args.idempotency_key),
            callback=_restore,
        )
        if outcome.timed_out:
            # Log the in-flight conflict for observability parity with the
            # other error branches below and the REST controller.
            logger.warning(
                IDEMPOTENCY_CLAIM_IN_FLIGHT,
                scope="mcp:backup_restore",
                idempotency_key=args.idempotency_key,
                tool_name=tool,
                backup_id=backup_id,
            )
            msg = "Concurrent in-flight restore with this idempotency key"
            return err(ConflictError(msg), domain_code="conflict")
        # Validate the (possibly cached) payload shape before reporting
        # success, mirroring the REST restore path: a stale or corrupt
        # idempotency-store row must force a rerun rather than emit a
        # malformed success response. Return the validated model's dump so
        # any Pydantic coercion / normalisation is preserved rather than
        # echoing the raw cached dict.
        try:
            confirmation = RestoreConfirmation.model_validate(outcome.result)
        except (ValueError, TypeError, ValidationError) as exc:
            log_handler_invoke_failed(tool, exc)
            msg = "Cached restore response failed validation; rerun the restore"
            return err(RestoreError(msg))
        payload = confirmation.model_dump()
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
