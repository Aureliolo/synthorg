"""Autonomy + collaboration MCP handlers for the agents domain.

Split out of ``meta/mcp/handlers/agents.py`` so the parent module stays
under the project's 800-line ceiling. Each handler still routes through
the same ``app_state`` services and returns the standard envelope; the
file only contains the four handlers and their argument helpers.
"""

from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from synthorg.core.types import NotBlankStr
from synthorg.hr.errors import AgentNotFoundError
from synthorg.meta.mcp.errors import ArgumentValidationError
from synthorg.meta.mcp.handlers.common import (
    err,
    ok,
)
from synthorg.meta.mcp.handlers.common_args import actor_id, require_non_blank
from synthorg.meta.mcp.handlers.common_logging import (
    log_handler_argument_invalid,
    log_handler_invoke_failed,
)
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import MCP_HANDLER_INVOKE_SUCCESS

if TYPE_CHECKING:
    from synthorg.core.agent import AgentIdentity
    from synthorg.core.enums import AutonomyLevel

logger = get_logger(__name__)

_ARG_AGENT_ID = "agent_id"


async def autonomy_get(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Read the agent's effective autonomy level."""
    tool = "synthorg_autonomy_get"
    try:
        agent_id = require_non_blank(arguments, _ARG_AGENT_ID)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    try:
        identity = await app_state.agent_registry.get(NotBlankStr(agent_id))
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
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


def _parse_autonomy_update_args(
    arguments: dict[str, Any],
) -> tuple[str, str, str]:
    """Validate the autonomy_update args and return ``(agent_id, level, reason)``.

    Extracted so the handler stays small enough to keep the agents.py
    file under its line budget.
    """
    arg_reason = "reason"
    agent_id = require_non_blank(arguments, _ARG_AGENT_ID)
    level_raw = require_non_blank(arguments, "level")
    reason = require_non_blank(arguments, arg_reason)
    return agent_id, level_raw, reason


async def autonomy_update(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    """Request an autonomy level change (routes through approval queue)."""
    tool = "synthorg_autonomy_update"
    try:
        agent_id, level_raw, reason = _parse_autonomy_update_args(arguments)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)

    # Local imports keep the agents handler module light at import time.
    from synthorg.core.enums import AutonomyLevel as _AutonomyLevel  # noqa: PLC0415
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

    approval_store = getattr(app_state, "approval_store", None)
    try:
        result = await app_state.agent_registry.update_autonomy(
            NotBlankStr(agent_id),
            update,
            approval_store=approval_store,
        )
    except AgentNotFoundError as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc, domain_code="not_found")
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=result.model_dump(mode="json"))


async def collaboration_get_score(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return the agent's current collaboration score."""
    tool = "synthorg_collaboration_get_score"
    try:
        agent_id = require_non_blank(arguments, _ARG_AGENT_ID)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    try:
        score = await app_state.performance_tracker.get_collaboration_score(
            NotBlankStr(agent_id),
        )
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
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
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return the curated calibration readout for the agent's score."""
    tool = "synthorg_collaboration_get_calibration"
    try:
        agent_id = require_non_blank(arguments, _ARG_AGENT_ID)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    try:
        calibration = await app_state.performance_tracker.get_collaboration_calibration(
            NotBlankStr(agent_id),
        )
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
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
