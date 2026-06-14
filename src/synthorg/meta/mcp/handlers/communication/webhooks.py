"""Webhook-definition MCP handlers (communication sub-domain)."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING
from uuid import uuid4

from pydantic import ValidationError

from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import NotFoundError
from synthorg.integrations.state import webhook_service_of
from synthorg.integrations.webhooks.models import WebhookDefinition
from synthorg.meta.mcp.domains._remaining_args import (
    WebhooksCreateArgs,
    WebhooksDeleteArgs,
    WebhooksGetArgs,
    WebhooksListArgs,
    WebhooksUpdateArgs,
)
from synthorg.meta.mcp.errors import (
    ArgumentValidationError,
    GuardrailViolationError,
)
from synthorg.meta.mcp.handler_protocol import ToolHandler
from synthorg.meta.mcp.handlers._mcp_handler_common import typed_args
from synthorg.meta.mcp.handlers.common import (
    PaginationMeta,
    dump_many,
    err,
    ok,
    require_admin_guardrails,
)
from synthorg.meta.mcp.handlers.common_args import require_actor_id
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

_ARG_DEFINITION = "definition"
_TY_WEBHOOK_OBJ = "WebhookDefinition object"


def _build_webhook_definition(
    raw: dict[str, object],
    *,
    require_id: bool,
) -> WebhookDefinition:
    """Validate a webhook ``definition`` payload into a model.

    Returns:
        ``WebhookDefinition`` instance.

    Raises:
        ArgumentValidationError: Raised on the corresponding failure path.
    """
    payload = dict(raw)
    if not require_id and "id" not in payload:
        payload["id"] = str(uuid4())
    try:
        return WebhookDefinition.model_validate(payload)
    except ValidationError as exc:
        raise ArgumentValidationError(_ARG_DEFINITION, _TY_WEBHOOK_OBJ) from exc


async def _webhooks_list(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """List registered webhook definitions (paginated).

    Returns:
        Resulting string.
    """
    tool = "synthorg_webhooks_list"
    try:
        page = typed_args(arguments, WebhooksListArgs)
        offset, limit = page.offset, page.limit
        definitions, total = await webhook_service_of(app_state).list_webhooks(
            offset=offset,
            limit=limit,
        )
        pagination = PaginationMeta(total=total, offset=offset, limit=limit)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(dump_many(definitions), pagination=pagination)


async def _webhooks_get(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Fetch a single webhook definition by ID.

    Returns:
        Resulting string.
    """
    tool = "synthorg_webhooks_get"
    try:
        webhook_id = typed_args(arguments, WebhooksGetArgs).webhook_id
        definition = await webhook_service_of(app_state).get_webhook(webhook_id)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    if definition is None:
        missing = NotFoundError(f"Webhook {webhook_id} not found")
        log_handler_invoke_failed(tool, missing, webhook_id=webhook_id)
        return err(missing, domain_code="not_found")
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(definition.model_dump(mode="json"))


async def _webhooks_create(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Register a new webhook definition (admin op; enforces guardrails).

    Returns:
        Resulting string.
    """
    tool = "synthorg_webhooks_create"
    try:
        reason, resolved_actor = require_admin_guardrails(arguments, actor)
        raw = typed_args(arguments, WebhooksCreateArgs).definition
        definition = _build_webhook_definition(raw, require_id=False)
        actor_id = require_actor_id(resolved_actor)
        stored = await webhook_service_of(app_state).create_webhook(
            definition=definition,
            actor_id=actor_id,
        )
        logger.info(
            MCP_ADMIN_OP_EXECUTED,
            tool_name=tool,
            actor_agent_id=actor_id,
            reason=reason,
            webhook_id=stored.id,
        )
        logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
        return ok(stored.model_dump(mode="json"))
    except GuardrailViolationError as exc:
        log_handler_guardrail_violated(tool, exc)
        return err(exc)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except KeyError as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc, domain_code="conflict")
    except ValueError as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc, domain_code="conflict")
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)


async def _webhooks_update(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Update an existing webhook definition (admin op; enforces guardrails).

    Returns:
        Resulting string.
    """
    tool = "synthorg_webhooks_update"
    try:
        reason, resolved_actor = require_admin_guardrails(arguments, actor)
        raw = typed_args(arguments, WebhooksUpdateArgs).definition
        definition = _build_webhook_definition(raw, require_id=True)
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
    return await _apply_webhook_update(
        tool=tool,
        app_state=app_state,
        definition=definition,
        reason=reason,
        actor_id=require_actor_id(resolved_actor),
    )


async def _apply_webhook_update(
    *,
    tool: str,
    app_state: AppState,
    definition: WebhookDefinition,
    reason: str,
    actor_id: str,
) -> str:
    """Apply a webhook update and emit the admin-op audit record.

    Returns:
        Resulting string.
    """
    try:
        stored = await webhook_service_of(app_state).update_webhook(
            definition=definition,
            actor_id=actor_id,
        )
    except KeyError as exc:
        missing = NotFoundError(f"Webhook {definition.id} not found")
        log_handler_invoke_failed(tool, exc)
        return err(missing, domain_code="not_found")
    except ValueError as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc, domain_code="conflict")
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(
        MCP_ADMIN_OP_EXECUTED,
        tool_name=tool,
        actor_agent_id=actor_id,
        reason=reason,
        webhook_id=stored.id,
    )
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(stored.model_dump(mode="json"))


async def _webhooks_delete(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Delete a webhook definition (destructive; enforces guardrails).

    Returns:
        Resulting string.
    """
    tool = "synthorg_webhooks_delete"
    try:
        reason, resolved_actor = require_admin_guardrails(arguments, actor)
        webhook_id = typed_args(arguments, WebhooksDeleteArgs).webhook_id
        actor_id = require_actor_id(resolved_actor)
        removed = await webhook_service_of(app_state).delete_webhook(
            definition_id=webhook_id,
            actor_id=actor_id,
            reason=reason,
        )
        if not removed:
            missing = NotFoundError(f"Webhook {webhook_id} not found")
            log_handler_invoke_failed(tool, missing, webhook_id=webhook_id)
            return err(missing, domain_code="not_found")
        logger.info(
            MCP_ADMIN_OP_EXECUTED,
            tool_name=tool,
            actor_agent_id=actor_id,
            reason=reason,
            webhook_id=webhook_id,
            removed=removed,
        )
        logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
        return ok({"removed": removed})
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


WEBHOOKS_HANDLERS: Mapping[str, ToolHandler] = MappingProxyType(
    {
        "synthorg_webhooks_list": _webhooks_list,
        "synthorg_webhooks_get": _webhooks_get,
        "synthorg_webhooks_create": _webhooks_create,
        "synthorg_webhooks_update": _webhooks_update,
        "synthorg_webhooks_delete": _webhooks_delete,
    },
)
