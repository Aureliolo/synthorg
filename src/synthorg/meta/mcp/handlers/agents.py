"""Agent domain MCP handlers.

Shims the 18 agent tools onto the existing HR services --
``agent_registry`` (``AgentRegistryService``), ``performance_tracker``,
``training_service``. Identity create / update / autonomy update /
collaboration calibration are all live as of META-MCP-3.

The remaining ``capability_gap`` returns are reserved for genuinely
optional services (personality registry, agent activity feed, agent
health aggregation, training session metadata) that are not always
wired on ``app_state`` -- those handlers fall back to the gap envelope
only when their ``has_<service>`` flag is False.

Destructive ops
---------------
``synthorg_agents_delete`` enforces the full
``confirm=True`` + non-blank ``reason`` + non-``None`` ``actor`` guardrail
and emits ``MCP_DESTRUCTIVE_OP_EXECUTED`` on success.
"""

import copy
from collections.abc import Mapping  # noqa: TC003 -- PEP 649 annotation
from datetime import UTC, datetime
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from synthorg.core.enums import SeniorityLevel
from synthorg.core.types import NotBlankStr
from synthorg.hr.errors import (
    AgentAlreadyRegisteredError,
    AgentNotFoundError,
    PersonalityNotFoundError,
    TrainingSessionNotFoundError,
)
from synthorg.hr.training.models import ContentType, TrainingPlan
from synthorg.meta.mcp.errors import (
    ArgumentValidationError,
    GuardrailViolationError,
    invalid_argument,
)
from synthorg.meta.mcp.handler_protocol import (
    ToolHandler,  # noqa: TC001 -- PEP 649 annotation
)
from synthorg.meta.mcp.handlers.agents_autonomy import (
    autonomy_get as autonomy_get_impl,
)
from synthorg.meta.mcp.handlers.agents_autonomy import (
    autonomy_update as autonomy_update_impl,
)
from synthorg.meta.mcp.handlers.agents_autonomy import (
    collaboration_get_calibration as collaboration_get_calibration_impl,
)
from synthorg.meta.mcp.handlers.agents_autonomy import (
    collaboration_get_score as collaboration_get_score_impl,
)
from synthorg.meta.mcp.handlers.common import (
    PaginationMeta,
    capability_gap,
    dump_many,
    err,
    ok,
    paginate_sequence,
    require_destructive_guardrails,
)
from synthorg.meta.mcp.handlers.common_args import coerce_pagination, require_arg
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.mcp import (
    MCP_DESTRUCTIVE_OP_EXECUTED,
    MCP_HANDLER_ARGUMENT_INVALID,
    MCP_HANDLER_GUARDRAIL_VIOLATED,
    MCP_HANDLER_INVOKE_FAILED,
    MCP_HANDLER_INVOKE_SUCCESS,
)

if TYPE_CHECKING:
    from synthorg.core.agent import AgentIdentity

logger = get_logger(__name__)


_TY_NON_BLANK = "non-blank string"
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
_WHY_PERSONALITIES = (
    "personality registry is not exposed on app_state; personalities "
    "are stored on AgentIdentity.personality"
)
_WHY_TRAINING_LIST = (
    "training_service.execute() is the only public entry point; "
    "list/get session metadata is not materialised"
)
_WHY_TRAINING_START = (
    "training_service.execute() requires a TrainingPlan -- not "
    "representable in the current MCP tool schema"
)


def _log_invalid(tool: str, exc: Exception) -> None:
    logger.warning(
        MCP_HANDLER_ARGUMENT_INVALID,
        tool_name=tool,
        error_type=type(exc).__name__,
        error=safe_error_description(exc),
    )


def _log_failed(tool: str, exc: Exception) -> None:
    logger.warning(
        MCP_HANDLER_INVOKE_FAILED,
        tool_name=tool,
        error_type=type(exc).__name__,
        error=safe_error_description(exc),
    )


def _log_guardrail(tool: str, exc: GuardrailViolationError) -> None:
    logger.warning(
        MCP_HANDLER_GUARDRAIL_VIOLATED,
        tool_name=tool,
        violation=exc.violation,
    )


def _actor_id(actor: Any) -> str | None:
    """Return a stable audit identifier for ``actor`` (prefers ``.id``).

    Prefers ``actor.id`` (a ``UUID`` that never changes over the agent's
    lifetime) so destructive-op audit trails stay consistent even when
    the display name is later edited.  Falls back to ``actor.name`` only
    when id is absent.
    """
    if actor is None:
        return None
    agent_id = getattr(actor, "id", None)
    if agent_id is not None:
        return str(agent_id)
    name = getattr(actor, "name", None)
    return name if isinstance(name, str) and name else None


def _require_non_blank(arguments: dict[str, Any], key: str) -> str:
    """Extract a non-blank string argument, stripped of surrounding whitespace."""
    raw = require_arg(arguments, key, str)
    stripped = raw.strip()
    if not stripped:
        raise invalid_argument(key, _TY_NON_BLANK)
    return stripped


# --- Agent CRUD -----------------------------------------------------------


async def _agents_list(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    tool = "synthorg_agents_list"
    try:
        offset, limit = coerce_pagination(arguments)
    except ArgumentValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc)
    try:
        agents = await app_state.agent_registry.list_active()
        page, meta = paginate_sequence(agents, offset=offset, limit=limit)
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        _log_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=dump_many(page), pagination=meta)


async def _agents_get(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    tool = "synthorg_agents_get"
    try:
        name = _require_non_blank(arguments, _ARG_AGENT_NAME)
    except ArgumentValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc)
    try:
        identity = await app_state.agent_registry.get_by_name(name)
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        _log_failed(tool, exc)
        return err(exc)
    if identity is None:
        missing = AgentNotFoundError(f"Agent {name!r} not found")
        _log_failed(tool, missing)
        return err(missing, domain_code="not_found")
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=identity.model_dump(mode="json"))


async def _agents_create(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    tool = "synthorg_agents_create"
    try:
        identity_dict = require_arg(arguments, "identity", dict)
    except ArgumentValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc)

    # Local import: AgentIdentity transitively pulls heavy core modules
    # whose runtime cost we don't want to pay on every handler import.
    from synthorg.core.agent import AgentIdentity as _AgentIdentity  # noqa: PLC0415

    try:
        identity = _AgentIdentity.model_validate(identity_dict)
    except ValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc, domain_code="invalid_argument")

    saved_by = _actor_id(actor) or "mcp"
    try:
        await app_state.agent_registry.register(identity, saved_by=saved_by)
    except AgentAlreadyRegisteredError as exc:
        _log_failed(tool, exc)
        return err(exc, domain_code="already_exists")
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        _log_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=identity.model_dump(mode="json"))


async def _agents_update(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    tool = "synthorg_agents_update"
    try:
        agent_id = _require_non_blank(arguments, _ARG_AGENT_ID)
        updates = require_arg(arguments, "updates", dict)
    except ArgumentValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc)

    saved_by = _actor_id(actor) or "mcp"
    try:
        updated = await app_state.agent_registry.apply_identity_update(
            NotBlankStr(agent_id),
            updates,
            saved_by=saved_by,
        )
    except AgentNotFoundError as exc:
        _log_failed(tool, exc)
        return err(exc, domain_code="not_found")
    except ValueError as exc:
        # Blocked-field rejection from the registry surfaces here.
        _log_invalid(tool, exc)
        return err(exc, domain_code="invalid_argument")
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        _log_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=updated.model_dump(mode="json"))


async def _agents_delete(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    tool = "synthorg_agents_delete"
    try:
        agent_name = _require_non_blank(arguments, _ARG_AGENT_NAME)
    except ArgumentValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc)
    try:
        reason, resolved_actor = require_destructive_guardrails(arguments, actor)
    except GuardrailViolationError as exc:
        _log_guardrail(tool, exc)
        return err(exc)

    try:
        identity = await app_state.agent_registry.get_by_name(agent_name)
        if identity is None:
            missing = AgentNotFoundError(f"Agent {agent_name!r} not found")
            _log_failed(tool, missing)
            return err(missing, domain_code="not_found")
        removed = await app_state.agent_registry.unregister(str(identity.id))
    except AgentNotFoundError as exc:
        _log_failed(tool, exc)
        return err(exc, domain_code="not_found")
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        _log_failed(tool, exc)
        return err(exc)

    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    logger.info(
        MCP_DESTRUCTIVE_OP_EXECUTED,
        tool_name=tool,
        actor_agent_id=_actor_id(resolved_actor),
        reason=reason,
        target_id=str(removed.id),
    )
    return ok(data=removed.model_dump(mode="json"))


# --- Agent observability --------------------------------------------------


async def _agents_get_performance(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    tool = "synthorg_agents_get_performance"
    try:
        agent_name = _require_non_blank(arguments, _ARG_AGENT_NAME)
    except ArgumentValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc)
    try:
        identity = await app_state.agent_registry.get_by_name(agent_name)
        if identity is None:
            missing = AgentNotFoundError(f"Agent {agent_name!r} not found")
            _log_failed(tool, missing)
            return err(missing, domain_code="not_found")
        snapshot = await app_state.performance_tracker.get_snapshot(
            str(identity.id),
        )
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        _log_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    if snapshot is None:
        return ok(data=None)
    return ok(data=snapshot.model_dump(mode="json"))


async def _agents_get_activity(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    tool = "synthorg_agents_get_activity"
    try:
        agent_name = _require_non_blank(arguments, _ARG_AGENT_NAME)
        offset, limit = coerce_pagination(arguments)
    except ArgumentValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc)
    if not getattr(app_state, "has_activity_feed_service", False):
        return capability_gap(tool, _WHY_ACTIVITY)
    try:
        identity = await app_state.agent_registry.get_by_name(agent_name)
        if identity is None:
            missing = AgentNotFoundError(f"Agent {agent_name!r} not found")
            _log_failed(tool, missing)
            return err(missing, domain_code="not_found")
        events, total = await app_state.activity_feed_service.get_agent_activity(
            NotBlankStr(str(identity.id)),
            offset=offset,
            limit=limit,
        )
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        _log_failed(tool, exc)
        return err(exc)
    meta = PaginationMeta(total=total, offset=offset, limit=limit)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=dump_many(events), pagination=meta)


async def _agents_get_history(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    tool = "synthorg_agents_get_history"
    try:
        agent_name = _require_non_blank(arguments, _ARG_AGENT_NAME)
        offset, limit = coerce_pagination(arguments)
    except ArgumentValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc)
    if not getattr(app_state, "has_agent_version_service", False):
        return capability_gap(tool, _WHY_HISTORY)
    try:
        identity = await app_state.agent_registry.get_by_name(agent_name)
        if identity is None:
            missing = AgentNotFoundError(f"Agent {agent_name!r} not found")
            _log_failed(tool, missing)
            return err(missing, domain_code="not_found")
        versions, total = await app_state.agent_version_service.list_versions(
            NotBlankStr(str(identity.id)),
            offset=offset,
            limit=limit,
        )
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        _log_failed(tool, exc)
        return err(exc)
    meta = PaginationMeta(total=total, offset=offset, limit=limit)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=dump_many(versions), pagination=meta)


async def _agents_get_health(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    tool = "synthorg_agents_get_health"
    try:
        agent_name = _require_non_blank(arguments, _ARG_AGENT_NAME)
    except ArgumentValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc)
    if not getattr(app_state, "has_agent_health_service", False):
        return capability_gap(tool, _WHY_HEALTH)
    try:
        identity = await app_state.agent_registry.get_by_name(agent_name)
        if identity is None:
            missing = AgentNotFoundError(f"Agent {agent_name!r} not found")
            _log_failed(tool, missing)
            return err(missing, domain_code="not_found")
        report = await app_state.agent_health_service.get_agent_health(
            NotBlankStr(str(identity.id)),
        )
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        _log_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=report.model_dump(mode="json"))


# --- Personalities --------------------------------------------------------


async def _personalities_list(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    tool = "synthorg_personalities_list"
    try:
        offset, limit = coerce_pagination(arguments)
    except ArgumentValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc)
    if not getattr(app_state, "has_personality_service", False):
        return capability_gap(tool, _WHY_PERSONALITIES)
    try:
        entries, total = await app_state.personality_service.list_personalities(
            offset=offset,
            limit=limit,
        )
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        _log_failed(tool, exc)
        return err(exc)
    meta = PaginationMeta(total=total, offset=offset, limit=limit)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=dump_many(entries), pagination=meta)


async def _personalities_get(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    tool = "synthorg_personalities_get"
    try:
        name = _require_non_blank(arguments, "name")
    except ArgumentValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc)
    if not getattr(app_state, "has_personality_service", False):
        return capability_gap(tool, _WHY_PERSONALITIES)
    try:
        entry = await app_state.personality_service.get_personality(
            NotBlankStr(name),
        )
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        _log_failed(tool, exc)
        return err(exc)
    if entry is None:
        missing = PersonalityNotFoundError(f"Personality {name!r} not found")
        _log_failed(tool, missing)
        return err(missing, domain_code="not_found")
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=entry.model_dump(mode="json"))


# --- Training -------------------------------------------------------------


async def _training_list_sessions(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    tool = "synthorg_training_list_sessions"
    try:
        offset, limit = coerce_pagination(arguments)
    except ArgumentValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc)
    if not getattr(app_state, "has_training_service", False):
        return capability_gap(tool, _WHY_TRAINING_LIST)
    try:
        sessions, total = await app_state.training_service.list_sessions(
            offset=offset,
            limit=limit,
        )
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        _log_failed(tool, exc)
        return err(exc)
    meta = PaginationMeta(total=total, offset=offset, limit=limit)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=dump_many(sessions), pagination=meta)


async def _training_get_session(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    tool = "synthorg_training_get_session"
    try:
        plan_id = _require_non_blank(arguments, "session_id")
    except ArgumentValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc)
    if not getattr(app_state, "has_training_service", False):
        return capability_gap(tool, _WHY_TRAINING_LIST)
    try:
        session = await app_state.training_service.get_session(
            NotBlankStr(plan_id),
        )
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        _log_failed(tool, exc)
        return err(exc)
    if session is None:
        missing = TrainingSessionNotFoundError(
            f"Training session {plan_id!r} not found",
        )
        _log_failed(tool, missing)
        return err(missing, domain_code="not_found")
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=session.model_dump(mode="json"))


async def _training_start_session(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    tool = "synthorg_training_start_session"
    try:
        plan = _parse_training_plan(arguments)
    except ArgumentValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc)
    if not getattr(app_state, "has_training_service", False):
        return capability_gap(tool, _WHY_TRAINING_START)
    try:
        result = await app_state.training_service.start_session(plan)
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        _log_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=result.model_dump(mode="json"))


def _parse_training_plan(arguments: dict[str, Any]) -> TrainingPlan:
    """Construct a :class:`TrainingPlan` from MCP arguments.

    The MCP tool only surfaces the fields a caller needs to launch a
    fresh training session; richer fields (volume caps, custom
    selectors) stay at their :class:`TrainingPlan` defaults.
    """
    arg_level = "new_agent_level"
    arg_enabled = "enabled_content_types"
    arg_plan = "plan"
    expected_level = "one of junior/mid/senior"
    expected_enabled_list = "list of content type strings"
    expected_enabled_values = (
        "list of content type strings (procedural/semantic/tool_patterns)"
    )
    new_agent_id = _require_non_blank(arguments, "new_agent_id")
    new_agent_role = _require_non_blank(arguments, "new_agent_role")
    raw_level = _require_non_blank(arguments, arg_level)
    try:
        level = SeniorityLevel(raw_level)
    except ValueError as exc:
        raise invalid_argument(arg_level, expected_level) from exc
    department: NotBlankStr | None = None
    arg_department = "new_agent_department"
    expected_department = "non-blank string"
    if arg_department in arguments:
        department_raw = arguments[arg_department]
        if department_raw is not None:
            # Reject present-but-malformed values (e.g. ``""`` or a
            # non-string); silently dropping them would change the
            # plan the caller intended to submit.
            if not isinstance(department_raw, str) or not department_raw.strip():
                raise invalid_argument(arg_department, expected_department)
            department = NotBlankStr(department_raw.strip())
    enabled_raw = arguments.get("enabled_content_types")
    if enabled_raw is None:
        enabled = frozenset(ContentType)
    else:
        if not isinstance(enabled_raw, (list, tuple)):
            raise invalid_argument(arg_enabled, expected_enabled_list)
        try:
            enabled = frozenset(ContentType(v) for v in enabled_raw)
        except ValueError as exc:
            raise invalid_argument(arg_enabled, expected_enabled_values) from exc
    try:
        return TrainingPlan(
            new_agent_id=NotBlankStr(new_agent_id),
            new_agent_role=NotBlankStr(new_agent_role),
            new_agent_level=level,
            new_agent_department=department,
            enabled_content_types=enabled,
            created_at=datetime.now(UTC),
        )
    except ValidationError as exc:
        expected_plan = f"valid TrainingPlan shape ({len(exc.errors())} error(s))"
        raise invalid_argument(arg_plan, expected_plan) from exc


# --- Autonomy + Collaboration ---------------------------------------------
# Live handlers live in ``agents_autonomy.py`` to keep this module
# under the project's 800-line ceiling.

_autonomy_get = autonomy_get_impl
_autonomy_update = autonomy_update_impl
_collaboration_get_score = collaboration_get_score_impl
_collaboration_get_calibration = collaboration_get_calibration_impl


AGENT_HANDLERS: Mapping[str, ToolHandler] = MappingProxyType(
    copy.deepcopy(
        {
            "synthorg_agents_list": _agents_list,
            "synthorg_agents_get": _agents_get,
            "synthorg_agents_create": _agents_create,
            "synthorg_agents_update": _agents_update,
            "synthorg_agents_delete": _agents_delete,
            "synthorg_agents_get_performance": _agents_get_performance,
            "synthorg_agents_get_activity": _agents_get_activity,
            "synthorg_agents_get_history": _agents_get_history,
            "synthorg_agents_get_health": _agents_get_health,
            "synthorg_personalities_list": _personalities_list,
            "synthorg_personalities_get": _personalities_get,
            "synthorg_training_list_sessions": _training_list_sessions,
            "synthorg_training_get_session": _training_get_session,
            "synthorg_training_start_session": _training_start_session,
            "synthorg_autonomy_get": _autonomy_get,
            "synthorg_autonomy_update": _autonomy_update,
            "synthorg_collaboration_get_score": _collaboration_get_score,
            "synthorg_collaboration_get_calibration": _collaboration_get_calibration,
        },
    ),
)
