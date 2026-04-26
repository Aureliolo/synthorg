"""Infrastructure domain MCP handlers.

40 tools spanning health, settings, providers, backup, audit, events,
users, projects, requests, setup, simulations, template packs, and
integration health.  All handlers shim through the corresponding
facade on :class:`AppState`; capability gaps raise typed
``not_supported`` via :class:`CapabilityNotSupportedError`.
"""

from types import MappingProxyType
from typing import TYPE_CHECKING, Any
from uuid import UUID

from synthorg.backup.models import BackupTrigger
from synthorg.communication.mcp_errors import CapabilityNotSupportedError
from synthorg.meta.mcp.errors import GuardrailViolationError, invalid_argument
from synthorg.meta.mcp.handler_protocol import (
    ToolHandler,  # noqa: TC001 -- PEP 649 annotation
)
from synthorg.meta.mcp.handlers.common import (
    PaginationMeta,
    err,
    ok,
    require_destructive_guardrails,
)
from synthorg.meta.mcp.handlers.common_args import (
    actor_label,
    coerce_pagination,
    get_optional_str,
    require_arg,
    require_dict,
)
from synthorg.meta.mcp.handlers.common_logging import (
    log_handler_guardrail_violated,
    log_handler_invoke_failed,
)
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import (
    MCP_DESTRUCTIVE_OP_EXECUTED,
    MCP_HANDLER_CAPABILITY_GAP,
    MCP_HANDLER_INVOKE_SUCCESS,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from synthorg.core.agent import AgentIdentity
    from synthorg.core.types import NotBlankStr

logger = get_logger(__name__)

_TY_STRING = "non-blank string"
_TY_UUID = "UUID string"
_TY_BACKUP_TRIGGER = "BackupTrigger string"
_ARG_TRIGGER = "trigger"


def _map_capability(tool: str, exc: CapabilityNotSupportedError) -> str:
    """Translate a facade-side capability gap into a typed error envelope.

    Emits :data:`MCP_HANDLER_CAPABILITY_GAP` at INFO so capability
    telemetry is not classified as an invoke failure.
    """
    logger.info(
        MCP_HANDLER_CAPABILITY_GAP,
        tool_name=tool,
        capability=exc.capability,
    )
    return err(exc, domain_code=exc.domain_code)


def _require_str(arguments: dict[str, Any], key: str) -> NotBlankStr:
    """Extract a required non-blank string or raise ``ArgumentValidationError``."""
    value = get_optional_str(arguments, key)
    if value is None:
        raise invalid_argument(key, _TY_STRING)
    return value


def _get_dict(arguments: dict[str, Any], key: str) -> dict[str, str] | None:
    """Extract an optional ``dict[str, str]`` argument; ``None`` when absent."""
    raw = arguments.get(key)
    if raw in (None, ""):
        return None
    validated = require_dict(arguments, key, value_type=str)
    return dict(validated)


def _require_uuid(arguments: dict[str, Any], key: str) -> str:
    """Extract a required UUID-shaped string or raise ``ArgumentValidationError``."""
    value = require_arg(arguments, key, str)
    try:
        UUID(value)
    except ValueError as exc:
        raise invalid_argument(key, _TY_UUID) from exc
    return value


# ── health ──────────────────────────────────────────────────────────


async def _health_check(
    *,
    app_state: Any,
    arguments: dict[str, Any],  # noqa: ARG001
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return lightweight health status for the AppState subsystems."""
    tool = "synthorg_health_check"
    try:
        data = {
            "task_engine": app_state.has_task_engine,
            "cost_tracker": app_state.has_cost_tracker,
            "approval_store": getattr(app_state, "approval_store", None) is not None,
            "agent_registry": app_state.has_agent_registry,
        }
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=data)


# ── settings ────────────────────────────────────────────────────────


async def _settings_list(
    *,
    app_state: Any,
    arguments: dict[str, Any],  # noqa: ARG001
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """List runtime settings via the settings-read facade."""
    tool = "synthorg_settings_list"
    try:
        result = await app_state.settings_read_service.list_settings()
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok(dict(result))


async def _settings_get(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Fetch a single setting by key."""
    tool = "synthorg_settings_get"
    try:
        key = _require_str(arguments, "key")
        result = await app_state.settings_read_service.get_setting(key)
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok({"key": key, "value": result})


async def _settings_update(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    """Update or create a setting value (non-destructive write)."""
    tool = "synthorg_settings_update"
    try:
        key = _require_str(arguments, "key")
        value = arguments.get("value")
        await app_state.settings_read_service.update_setting(
            key=key,
            value=value,
            actor_id=actor_label(actor),
        )
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok(None)


async def _settings_delete(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    """Delete a setting key (destructive; enforces guardrails)."""
    tool = "synthorg_settings_delete"
    try:
        reason, resolved_actor = require_destructive_guardrails(arguments, actor)
        key = _require_str(arguments, "key")
        await app_state.settings_read_service.delete_setting(
            key=key,
            actor_id=actor_label(resolved_actor),
            reason=reason,
        )
        logger.info(
            MCP_DESTRUCTIVE_OP_EXECUTED,
            tool_name=tool,
            actor=actor_label(resolved_actor),
            reason=reason,
            key=key,
        )
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except GuardrailViolationError as exc:
        log_handler_guardrail_violated(tool, exc)
        return err(exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok(None)


# ── providers ───────────────────────────────────────────────────────


async def _providers_list(
    *,
    app_state: Any,
    arguments: dict[str, Any],  # noqa: ARG001
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """List registered LLM providers."""
    tool = "synthorg_providers_list"
    try:
        providers = await app_state.provider_read_service.list_providers()
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok([_to_jsonable(p) for p in providers])


async def _providers_get(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Fetch a single provider registration by ID."""
    tool = "synthorg_providers_get"
    try:
        provider_id = _require_str(arguments, "provider_id")
        provider = await app_state.provider_read_service.get_provider(provider_id)
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    if provider is None:
        return err(
            LookupError(f"Provider {provider_id} not found"),
            domain_code="not_found",
        )
    return ok(_to_jsonable(provider))


async def _providers_get_health(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return provider-health roll-up (availability, latency, error rate)."""
    tool = "synthorg_providers_get_health"
    try:
        provider_id = get_optional_str(arguments, "provider_id")
        result = await app_state.provider_read_service.get_health(provider_id)
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok({k: _to_jsonable(v) for k, v in dict(result).items()})


async def _providers_test_connection(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Perform an on-demand connectivity probe against a provider."""
    tool = "synthorg_providers_test_connection"
    try:
        provider_id = _require_str(arguments, "provider_id")
        result = await app_state.provider_read_service.test_connection(provider_id)
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok({k: _to_jsonable(v) for k, v in dict(result).items()})


def _to_jsonable(value: Any) -> Any:
    """Best-effort JSON-safe serialisation for facade returns.

    Pydantic models are dumped via ``model_dump``; other values pass
    through.  Keeps handlers thin when the underlying primitive
    returns a non-uniform shape.
    """
    dump_fn = getattr(value, "model_dump", None)
    if callable(dump_fn):
        return dump_fn(mode="json")
    return value


# ── backup ──────────────────────────────────────────────────────────


async def _backup_list(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """List persistence backups recorded by the backup service."""
    tool = "synthorg_backup_list"
    try:
        offset, limit = coerce_pagination(arguments)
        page, total = await app_state.backup_facade_service.list_backups(
            offset=offset,
            limit=limit,
        )
        pagination = PaginationMeta(total=total, offset=offset, limit=limit)
        return ok([_to_jsonable(b) for b in page], pagination=pagination)
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)


async def _backup_get(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Fetch a single backup record by ID."""
    tool = "synthorg_backup_get"
    try:
        backup_id = _require_str(arguments, "backup_id")
        manifest = await app_state.backup_facade_service.get_backup(backup_id)
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except LookupError as exc:
        return err(exc, domain_code="not_found")
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok(_to_jsonable(manifest))


async def _backup_create(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Trigger a new backup run (non-destructive; records a new manifest)."""
    tool = "synthorg_backup_create"
    try:
        trigger_raw = require_arg(arguments, _ARG_TRIGGER, str)
        try:
            trigger = BackupTrigger(trigger_raw)
        except ValueError as exc:
            raise invalid_argument(_ARG_TRIGGER, _TY_BACKUP_TRIGGER) from exc
        manifest = await app_state.backup_facade_service.create_backup(
            trigger=trigger,
        )
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok(_to_jsonable(manifest))


async def _backup_delete(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    """Delete a backup manifest (destructive; enforces guardrails)."""
    tool = "synthorg_backup_delete"
    try:
        reason, resolved_actor = require_destructive_guardrails(arguments, actor)
        backup_id = _require_str(arguments, "backup_id")
        await app_state.backup_facade_service.delete_backup(
            backup_id=backup_id,
            actor_id=actor_label(resolved_actor),
            reason=reason,
        )
        logger.info(
            MCP_DESTRUCTIVE_OP_EXECUTED,
            tool_name=tool,
            actor=actor_label(resolved_actor),
            reason=reason,
            backup_id=backup_id,
        )
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except GuardrailViolationError as exc:
        log_handler_guardrail_violated(tool, exc)
        return err(exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok(None)


async def _backup_restore(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    """Restore persistence state from a backup (destructive; enforces guardrails)."""
    tool = "synthorg_backup_restore"
    try:
        reason, resolved_actor = require_destructive_guardrails(arguments, actor)
        backup_id = _require_str(arguments, "backup_id")
        result = await app_state.backup_facade_service.restore_backup(
            backup_id=backup_id,
            actor_id=actor_label(resolved_actor),
            reason=reason,
        )
        logger.info(
            MCP_DESTRUCTIVE_OP_EXECUTED,
            tool_name=tool,
            actor=actor_label(resolved_actor),
            reason=reason,
            backup_id=backup_id,
        )
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except GuardrailViolationError as exc:
        log_handler_guardrail_violated(tool, exc)
        return err(exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok(dict(result))


# ── audit + events ──────────────────────────────────────────────────


async def _audit_list(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return recent audit log entries (paginated)."""
    tool = "synthorg_audit_list"
    try:
        offset, limit = coerce_pagination(arguments)
        page, total = await app_state.audit_read_service.list_entries(
            offset=offset,
            limit=limit,
        )
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    pagination = PaginationMeta(total=total, offset=offset, limit=limit)
    return ok([_to_jsonable(e) for e in page], pagination=pagination)


async def _events_list(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return recent events from the event-stream hub."""
    tool = "synthorg_events_list"
    try:
        offset, limit = coerce_pagination(arguments)
        page, total = await app_state.events_read_service.list_events(
            offset=offset,
            limit=limit,
        )
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    pagination = PaginationMeta(total=total, offset=offset, limit=limit)
    return ok([_to_jsonable(e) for e in page], pagination=pagination)


# ── users ───────────────────────────────────────────────────────────


async def _users_list(
    *,
    app_state: Any,
    arguments: dict[str, Any],  # noqa: ARG001
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """List registered API users."""
    tool = "synthorg_users_list"
    try:
        users = await app_state.user_facade_service.list_users()
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok([_to_jsonable(u) for u in users])


async def _users_get(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Fetch a single API user by ID."""
    tool = "synthorg_users_get"
    try:
        user_id = _require_str(arguments, "user_id")
        user = await app_state.user_facade_service.get_user(user_id)
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    if user is None:
        return err(
            LookupError(f"User {user_id} not found"),
            domain_code="not_found",
        )
    return ok(_to_jsonable(user))


async def _users_create(
    *,
    app_state: Any,
    arguments: dict[str, Any],  # noqa: ARG001
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Create a new API user (non-destructive write)."""
    tool = "synthorg_users_create"
    try:
        await app_state.user_facade_service.create_user()
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok(None)


async def _users_update(
    *,
    app_state: Any,
    arguments: dict[str, Any],  # noqa: ARG001
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Update an existing API user (partial patch)."""
    tool = "synthorg_users_update"
    try:
        await app_state.user_facade_service.update_user()
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok(None)


async def _users_delete(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    """Delete an API user (destructive; enforces guardrails)."""
    tool = "synthorg_users_delete"
    try:
        reason, resolved_actor = require_destructive_guardrails(arguments, actor)
        user_id = _require_str(arguments, "user_id")
        await app_state.user_facade_service.delete_user(
            user_id=user_id,
            actor_id=actor_label(resolved_actor),
            reason=reason,
        )
        logger.info(
            MCP_DESTRUCTIVE_OP_EXECUTED,
            tool_name=tool,
            actor=actor_label(resolved_actor),
            reason=reason,
            user_id=user_id,
        )
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except GuardrailViolationError as exc:
        log_handler_guardrail_violated(tool, exc)
        return err(exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok(None)


# ── projects ────────────────────────────────────────────────────────


async def _projects_list(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """List projects (paginated)."""
    tool = "synthorg_projects_list"
    try:
        offset, limit = coerce_pagination(arguments)
        page, total = await app_state.project_facade_service.list_projects(
            offset=offset,
            limit=limit,
        )
        pagination = PaginationMeta(total=total, offset=offset, limit=limit)
        return ok([p.to_dict() for p in page], pagination=pagination)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)


async def _projects_get(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Fetch a single project by ID."""
    tool = "synthorg_projects_get"
    try:
        project_id = _require_uuid(arguments, "project_id")
        project = await app_state.project_facade_service.get_project(project_id)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    if project is None:
        return err(
            LookupError(f"Project {project_id} not found"),
            domain_code="not_found",
        )
    return ok(project.to_dict())


async def _projects_create(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    """Create a new project (non-destructive write)."""
    tool = "synthorg_projects_create"
    try:
        name = _require_str(arguments, "name")
        description = _require_str(arguments, "description")
        metadata = _get_dict(arguments, "metadata")
        project = await app_state.project_facade_service.create_project(
            name=name,
            description=description,
            actor_id=actor_label(actor),
            metadata=metadata,
        )
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok(project.to_dict())


async def _projects_update(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    """Update an existing project (partial patch)."""
    tool = "synthorg_projects_update"
    try:
        project_id = _require_uuid(arguments, "project_id")
        name = get_optional_str(arguments, "name")
        description = get_optional_str(arguments, "description")
        metadata = _get_dict(arguments, "metadata")
        project = await app_state.project_facade_service.update_project(
            project_id=project_id,
            actor_id=actor_label(actor),
            name=name,
            description=description,
            metadata=metadata,
        )
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    if project is None:
        return err(
            LookupError(f"Project {project_id} not found"),
            domain_code="not_found",
        )
    return ok(project.to_dict())


async def _projects_delete(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    """Delete a project (destructive; enforces guardrails)."""
    tool = "synthorg_projects_delete"
    try:
        reason, resolved_actor = require_destructive_guardrails(arguments, actor)
        project_id = _require_uuid(arguments, "project_id")
        removed = await app_state.project_facade_service.delete_project(
            project_id=project_id,
            actor_id=actor_label(resolved_actor),
            reason=reason,
        )
        if removed:
            logger.info(
                MCP_DESTRUCTIVE_OP_EXECUTED,
                tool_name=tool,
                actor=actor_label(resolved_actor),
                reason=reason,
                project_id=project_id,
                removed=removed,
            )
    except GuardrailViolationError as exc:
        log_handler_guardrail_violated(tool, exc)
        return err(exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok({"removed": removed})


# ── requests ────────────────────────────────────────────────────────


async def _requests_list(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """List operator request-ledger entries (paginated)."""
    tool = "synthorg_requests_list"
    try:
        offset, limit = coerce_pagination(arguments)
        page, total = await app_state.requests_facade_service.list_requests(
            offset=offset,
            limit=limit,
        )
        pagination = PaginationMeta(total=total, offset=offset, limit=limit)
        return ok([r.to_dict() for r in page], pagination=pagination)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)


async def _requests_get(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Fetch a single operator request by ID."""
    tool = "synthorg_requests_get"
    try:
        request_id = _require_uuid(arguments, "request_id")
        record = await app_state.requests_facade_service.get_request(request_id)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    if record is None:
        return err(
            LookupError(f"Request {request_id} not found"),
            domain_code="not_found",
        )
    return ok(record.to_dict())


async def _requests_create(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    """Record a new operator request (non-destructive write)."""
    tool = "synthorg_requests_create"
    try:
        title = _require_str(arguments, "title")
        body = _require_str(arguments, "body")
        record = await app_state.requests_facade_service.create_request(
            title=title,
            body=body,
            requested_by=actor_label(actor),
        )
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok(record.to_dict())


# ── setup ───────────────────────────────────────────────────────────


async def _setup_get_status(
    *,
    app_state: Any,
    arguments: dict[str, Any],  # noqa: ARG001
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return current setup-wizard state."""
    tool = "synthorg_setup_get_status"
    try:
        status = await app_state.setup_facade_service.get_status()
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok(dict(status))


async def _setup_initialize(
    *,
    app_state: Any,
    arguments: dict[str, Any],  # noqa: ARG001
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Dispatch an initialisation step (delegates to setup controller)."""
    tool = "synthorg_setup_initialize"
    try:
        await app_state.setup_facade_service.initialize()
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok(None)


# ── simulations ─────────────────────────────────────────────────────


async def _simulations_list(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """List simulation scenarios loaded at start-up."""
    tool = "synthorg_simulations_list"
    try:
        offset, limit = coerce_pagination(arguments)
        page, total = await app_state.simulation_facade_service.list_simulations(
            offset=offset,
            limit=limit,
        )
        pagination = PaginationMeta(total=total, offset=offset, limit=limit)
        return ok([_to_jsonable(s) for s in page], pagination=pagination)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)


async def _simulations_get(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Fetch a single simulation scenario by ID."""
    tool = "synthorg_simulations_get"
    try:
        sim_id = _require_str(arguments, "simulation_id")
        sim = await app_state.simulation_facade_service.get_simulation(sim_id)
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
    """Capability gap: simulation scenarios are config-driven."""
    tool = "synthorg_simulations_create"
    try:
        await app_state.simulation_facade_service.create_simulation()
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok(None)


# ── template packs ─────────────────────────────────────────────────


async def _template_packs_list(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """List installed template packs."""
    tool = "synthorg_template_packs_list"
    try:
        offset, limit = coerce_pagination(arguments)
        page, total = await app_state.template_pack_facade_service.list_packs(
            offset=offset,
            limit=limit,
        )
        pagination = PaginationMeta(total=total, offset=offset, limit=limit)
        return ok([p.to_dict() for p in page], pagination=pagination)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)


async def _template_packs_get(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Fetch a single template-pack record by ID."""
    tool = "synthorg_template_packs_get"
    try:
        pack_id = _require_uuid(arguments, "pack_id")
        pack = await app_state.template_pack_facade_service.get_pack(pack_id)
    except Exception as exc:
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
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    """Install a new template pack (non-destructive write)."""
    tool = "synthorg_template_packs_install"
    try:
        name = _require_str(arguments, "name")
        version = _require_str(arguments, "version")
        pack = await app_state.template_pack_facade_service.install_pack(
            name=name,
            version=version,
            actor_id=actor_label(actor),
        )
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok(pack.to_dict())


async def _template_packs_uninstall(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    """Uninstall a template pack (destructive; enforces guardrails)."""
    tool = "synthorg_template_packs_uninstall"
    try:
        reason, resolved_actor = require_destructive_guardrails(arguments, actor)
        pack_id = _require_uuid(arguments, "pack_id")
        removed = await app_state.template_pack_facade_service.uninstall_pack(
            pack_id=pack_id,
            actor_id=actor_label(resolved_actor),
            reason=reason,
        )
        if removed:
            logger.info(
                MCP_DESTRUCTIVE_OP_EXECUTED,
                tool_name=tool,
                actor=actor_label(resolved_actor),
                reason=reason,
                pack_id=pack_id,
                removed=removed,
            )
    except GuardrailViolationError as exc:
        log_handler_guardrail_violated(tool, exc)
        return err(exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok({"removed": removed})


# ── integration health ────────────────────────────────────────────


async def _integration_health_get_all(
    *,
    app_state: Any,
    arguments: dict[str, Any],  # noqa: ARG001
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return health roll-ups for every integration."""
    tool = "synthorg_integration_health_get_all"
    try:
        snapshot = await app_state.integration_health_facade_service.get_all()
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
    """Return the health roll-up for a single integration."""
    tool = "synthorg_integration_health_get"
    try:
        integration_id = _require_str(arguments, "integration_id")
        status = await app_state.integration_health_facade_service.get_one(
            integration_id,
        )
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    if status is None:
        return err(
            LookupError(f"Integration {integration_id} not found"),
            domain_code="not_found",
        )
    return ok(_to_jsonable(status))


# ── dispatch table ─────────────────────────────────────────────────


INFRASTRUCTURE_HANDLERS: Mapping[str, ToolHandler] = MappingProxyType(
    {
        "synthorg_health_check": _health_check,
        "synthorg_settings_list": _settings_list,
        "synthorg_settings_get": _settings_get,
        "synthorg_settings_update": _settings_update,
        "synthorg_settings_delete": _settings_delete,
        "synthorg_providers_list": _providers_list,
        "synthorg_providers_get": _providers_get,
        "synthorg_providers_get_health": _providers_get_health,
        "synthorg_providers_test_connection": _providers_test_connection,
        "synthorg_backup_create": _backup_create,
        "synthorg_backup_list": _backup_list,
        "synthorg_backup_get": _backup_get,
        "synthorg_backup_delete": _backup_delete,
        "synthorg_backup_restore": _backup_restore,
        "synthorg_audit_list": _audit_list,
        "synthorg_events_list": _events_list,
        "synthorg_users_list": _users_list,
        "synthorg_users_get": _users_get,
        "synthorg_users_create": _users_create,
        "synthorg_users_update": _users_update,
        "synthorg_users_delete": _users_delete,
        "synthorg_projects_list": _projects_list,
        "synthorg_projects_get": _projects_get,
        "synthorg_projects_create": _projects_create,
        "synthorg_projects_update": _projects_update,
        "synthorg_projects_delete": _projects_delete,
        "synthorg_requests_list": _requests_list,
        "synthorg_requests_get": _requests_get,
        "synthorg_requests_create": _requests_create,
        "synthorg_setup_get_status": _setup_get_status,
        "synthorg_setup_initialize": _setup_initialize,
        "synthorg_simulations_list": _simulations_list,
        "synthorg_simulations_get": _simulations_get,
        "synthorg_simulations_create": _simulations_create,
        "synthorg_template_packs_list": _template_packs_list,
        "synthorg_template_packs_get": _template_packs_get,
        "synthorg_template_packs_install": _template_packs_install,
        "synthorg_template_packs_uninstall": _template_packs_uninstall,
        "synthorg_integration_health_get_all": _integration_health_get_all,
        "synthorg_integration_health_get": _integration_health_get,
    },
)
