"""Autonomy + collaboration MCP handlers for the agents domain.

Split out of ``meta/mcp/handlers/agents.py`` so the parent module stays
under the project's 800-line ceiling. Each handler still routes through
the same ``app_state`` services and returns the standard envelope; the
file only contains the four handlers and their argument helpers.
"""

from typing import TYPE_CHECKING

from pydantic import ValidationError

from synthorg.approval.state import ApprovalStateSlice
from synthorg.core.agent import AgentIdentity
from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.hr.errors import AgentNotFoundError
from synthorg.hr.state import agent_registry_of, performance_tracker_of
from synthorg.meta.mcp.domains._agents_args import (
    AutonomyGetArgs,
    AutonomyUpdateArgs,
    CollaborationGetCalibrationArgs,
    CollaborationGetScoreArgs,
)
from synthorg.meta.mcp.errors import ArgumentValidationError
from synthorg.meta.mcp.handlers._mcp_handler_common import typed_args
from synthorg.meta.mcp.handlers.common import (
    err,
    ok,
)
from synthorg.meta.mcp.handlers.common_args import actor_id
from synthorg.meta.mcp.handlers.common_logging import (
    log_handler_argument_invalid,
    log_handler_invoke_failed,
)
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import MCP_HANDLER_INVOKE_SUCCESS

if TYPE_CHECKING:
    from synthorg.api.state import AppState

logger = get_logger(__name__)


async def autonomy_get(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Read the agent's effective autonomy level.

    Returns:
        Resulting string.
    """
    tool = "synthorg_autonomy_get"
    try:
        agent_id = typed_args(arguments, AutonomyGetArgs).agent_id
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    try:
        identity = await agent_registry_of(app_state).get(NotBlankStr(agent_id))
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    if identity is None:
        missing = AgentNotFoundError(f"Agent {agent_id!r} not found")
        log_handler_invoke_failed(tool, missing)
        return err(missing, domain_code="not_found")

    level: AutonomyLevel | None = identity.autonomy_level
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(
        data={
            "agent_id": str(identity.id),
            "agent_name": str(identity.name),
            "autonomy_level": level.value if level is not None else None,
        },
    )


async def autonomy_update(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
    # lint-allow: mcp-admin-guardrail -- routes through approval queue, no mutation
) -> str:
    """Request an autonomy level change (routes through approval queue).

    Returns:
        Resulting string.
    """
    tool = "synthorg_autonomy_update"
    try:
        update_args = typed_args(arguments, AutonomyUpdateArgs)
        agent_id = update_args.agent_id
        level_raw = update_args.level
        reason = update_args.reason
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)

    # Local imports keep the agents handler module light at import time.
    from synthorg.core.autonomy_enums import (  # noqa: PLC0415
        AutonomyLevel as _AutonomyLevel,
    )
    from synthorg.security.autonomy.models import (  # noqa: PLC0415
        AutonomyUpdate as _AutonomyUpdate,
    )

    try:
        level = _AutonomyLevel(level_raw)
    except ValueError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc, domain_code="invalid_argument")

    actor_str = actor_id(actor)
    try:
        update = _AutonomyUpdate(
            requested_level=level,
            reason=reason,
            requested_by=NotBlankStr(actor_str) if actor_str else None,
        )
    except ValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc, domain_code="invalid_argument")

    approval_store = app_state.slice(ApprovalStateSlice).store
    try:
        result = await agent_registry_of(app_state).update_autonomy(
            NotBlankStr(agent_id),
            update,
            approval_store=approval_store,
        )
    except AgentNotFoundError as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc, domain_code="not_found")
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=result.model_dump(mode="json"))


async def collaboration_get_score(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return the agent's current collaboration score.

    Returns:
        Resulting string.
    """
    tool = "synthorg_collaboration_get_score"
    try:
        agent_id = typed_args(arguments, CollaborationGetScoreArgs).agent_id
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    try:
        score = await performance_tracker_of(app_state).get_collaboration_score(
            NotBlankStr(agent_id),
        )
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    # ``CollaborationScoreResult`` is a Pydantic model; dump to JSON-mode
    # primitives before handing to ``ok()`` since ``ok()`` only json.dumps
    # the payload and would otherwise raise ``TypeError``.
    return ok(
        data={
            "agent_id": agent_id,
            "score": score.model_dump(mode="json"),
        },
    )


async def collaboration_get_calibration(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return the curated calibration readout for the agent's score.

    Returns:
        Resulting string.
    """
    tool = "synthorg_collaboration_get_calibration"
    try:
        agent_id = typed_args(arguments, CollaborationGetCalibrationArgs).agent_id
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    try:
        tracker = performance_tracker_of(app_state)
        calibration = await tracker.get_collaboration_calibration(
            NotBlankStr(agent_id),
        )
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=calibration.model_dump(mode="json"))


__all__ = [
    "autonomy_get",
    "autonomy_update",
    "collaboration_get_calibration",
    "collaboration_get_score",
]
