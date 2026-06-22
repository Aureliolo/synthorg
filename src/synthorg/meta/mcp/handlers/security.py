"""Security domain MCP handlers.

Three tools backing SecOps risk-tier overrides: create / revoke runtime
overrides and list the active set. The two mutating tools pass through
:func:`require_admin_guardrails`; the guardrail ``reason`` doubles as the
override's audit justification. All handlers shim through the
:class:`RiskOverrideService` published on the security state slice, which
is present only when a tiered approval-timeout policy is configured.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING

from synthorg.approval.enums import ApprovalRiskLevel
from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import NotFoundError
from synthorg.core.types import NotBlankStr
from synthorg.meta.mcp.domains._security_args import (
    RiskOverrideCreateArgs,
    RiskOverrideRevokeArgs,
)
from synthorg.meta.mcp.errors import (
    ArgumentValidationError,
    GuardrailViolationError,
)
from synthorg.meta.mcp.handler_protocol import ToolHandler
from synthorg.meta.mcp.handlers._mcp_handler_common import typed_args
from synthorg.meta.mcp.handlers.common import (
    err,
    ok,
    require_admin_guardrails,
)
from synthorg.meta.mcp.handlers.common_args import actor_id
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
from synthorg.security.state import risk_override_service_of

if TYPE_CHECKING:
    from synthorg.api.state import AppState

logger = get_logger(__name__)


async def _create_override(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Handle ``synthorg_security_risk_override_create``.

    Returns:
        JSON-encoded MCP envelope string.
    """
    tool_name = "synthorg_security_risk_override_create"
    try:
        reason, resolved_actor = require_admin_guardrails(arguments, actor)
        args = typed_args(arguments, RiskOverrideCreateArgs)
        override = await risk_override_service_of(app_state).create(
            action_type=NotBlankStr(args.action_type),
            override_tier=ApprovalRiskLevel(args.override_tier),
            reason=NotBlankStr(reason),
            created_by=NotBlankStr(actor_id(resolved_actor)),
            expires_at=args.expires_at,
        )
    except GuardrailViolationError as exc:
        log_handler_guardrail_violated(tool_name, exc)
        return err(exc)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool_name, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool_name, exc)
        return err(exc)
    response = ok(override.model_dump(mode="json"))
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool_name)
    logger.info(
        MCP_ADMIN_OP_EXECUTED,
        tool_name=tool_name,
        actor_agent_id=actor_id(resolved_actor),
        reason=reason,
        target_id=str(override.id),
    )
    return response


async def _revoke_override(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Handle ``synthorg_security_risk_override_revoke``.

    Returns:
        JSON-encoded MCP envelope string.
    """
    tool_name = "synthorg_security_risk_override_revoke"
    try:
        reason, resolved_actor = require_admin_guardrails(arguments, actor)
        args = typed_args(arguments, RiskOverrideRevokeArgs)
        revoked = await risk_override_service_of(app_state).revoke(
            NotBlankStr(args.override_id),
            revoked_by=NotBlankStr(actor_id(resolved_actor)),
        )
    except GuardrailViolationError as exc:
        log_handler_guardrail_violated(tool_name, exc)
        return err(exc)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool_name, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool_name, exc)
        return err(exc)
    if revoked is None:
        return err(NotFoundError(f"No active risk override {args.override_id}"))
    response = ok(revoked.model_dump(mode="json"))
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool_name)
    logger.info(
        MCP_ADMIN_OP_EXECUTED,
        tool_name=tool_name,
        actor_agent_id=actor_id(resolved_actor),
        reason=reason,
        target_id=str(revoked.id),
    )
    return response


async def _list_overrides(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Handle ``synthorg_security_risk_override_list``.

    Returns:
        JSON-encoded MCP envelope string.
    """
    tool_name = "synthorg_security_risk_override_list"
    _ = (arguments, actor)
    try:
        active = risk_override_service_of(app_state).list_active()
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool_name, exc)
        return err(exc)
    response = ok([o.model_dump(mode="json") for o in active])
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool_name)
    return response


SECURITY_HANDLERS: Mapping[str, ToolHandler] = MappingProxyType(
    {
        "synthorg_security_risk_override_create": _create_override,
        "synthorg_security_risk_override_revoke": _revoke_override,
        "synthorg_security_risk_override_list": _list_overrides,
    }
)
