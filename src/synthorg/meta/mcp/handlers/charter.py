"""Project charter domain MCP handlers.

Delegates to ``app_state.charter_service`` (interview / list / get /
cancel) and ``app_state.charter_dispatcher`` (approve). The interview
message is fenced as untrusted task data before reaching the model, and
``approve`` is admin-gated at the registry layer and re-checks the
guardrail here.
"""

from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.core.enums import CharterStatus
from synthorg.core.types import NotBlankStr
from synthorg.engine.prompt_safety import TAG_TASK_DATA, wrap_untrusted
from synthorg.meta.charter.models import InterviewTurnArgs
from synthorg.meta.mcp.errors import ArgumentValidationError, invalid_argument
from synthorg.meta.mcp.handler_protocol import (
    ToolHandler,  # noqa: TC001 -- PEP 649 annotation
)
from synthorg.meta.mcp.handlers.common import err, ok, require_admin_guardrails
from synthorg.meta.mcp.handlers.common_args import require_arg
from synthorg.meta.mcp.handlers.common_logging import (
    log_handler_argument_invalid,
    log_handler_invoke_failed,
)
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import MCP_HANDLER_INVOKE_SUCCESS

if TYPE_CHECKING:
    from collections.abc import Mapping

    from synthorg.core.agent import AgentIdentity

logger = get_logger(__name__)

_TOOL_INTERVIEW = "synthorg_charter_interview"
_TOOL_LIST = "synthorg_charter_list"
_TOOL_GET = "synthorg_charter_get"
_TOOL_CANCEL = "synthorg_charter_cancel"
_TOOL_APPROVE = "synthorg_charter_approve"

_ARG_MESSAGE = "message"
_ARG_CONVERSATION_ID = "conversation_id"
_ARG_PROJECT = "project"
_ARG_CHARTER_ID = "charter_id"
_ARG_STATUS = "status"
_ARG_PROJECT_ID = "project_id"
_ARG_CREATED_BY = "created_by"
_ARG_LIMIT = "limit"
_ARG_OFFSET = "offset"

_DEFAULT_LIMIT: int = 50
_MCP_OWNER_FALLBACK: NotBlankStr = NotBlankStr("mcp-operator")
_TY_STATUS = "charter status enum value"
_TY_NONNEG_INT = "non-negative int"
_TY_POS_INT = "positive int"


def _actor_id(actor: AgentIdentity | None) -> NotBlankStr:
    """Resolve the acting identity, falling back to a stable MCP owner."""
    if actor is None:
        return _MCP_OWNER_FALLBACK
    return NotBlankStr(str(actor.id))


def _require_charter_service(app_state: Any) -> Any:
    svc = getattr(app_state, "charter_service", None)
    if svc is None or not app_state.has_charter_service:
        msg = "charter interview service is not wired in this deployment"
        raise ServiceUnavailableError(msg)
    return app_state.charter_service


def _require_charter_dispatcher(app_state: Any) -> Any:
    if not app_state.has_charter_dispatcher:
        msg = "charter approval dispatcher is not wired in this deployment"
        raise ServiceUnavailableError(msg)
    return app_state.charter_dispatcher


def _opt_nonblank(arguments: dict[str, Any], key: str) -> NotBlankStr | None:
    raw = arguments.get(key)
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    if not isinstance(raw, str):
        raise invalid_argument(key, "string or null")
    return NotBlankStr(raw)


def _parse_status(arguments: dict[str, Any]) -> CharterStatus | None:
    raw = arguments.get(_ARG_STATUS)
    if raw is None or raw == "":
        return None
    if not isinstance(raw, str):
        raise invalid_argument(_ARG_STATUS, _TY_STATUS)
    try:
        return CharterStatus(raw)
    except ValueError as exc:
        raise invalid_argument(_ARG_STATUS, _TY_STATUS) from exc


def _parse_int(arguments: dict[str, Any], key: str, *, default: int, floor: int) -> int:
    raw = arguments.get(key)
    if raw in (None, ""):
        return default
    if not isinstance(raw, int) or isinstance(raw, bool) or raw < floor:
        ty = _TY_POS_INT if floor > 0 else _TY_NONNEG_INT
        raise invalid_argument(key, ty)
    return raw


async def _charter_interview(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    try:
        svc = _require_charter_service(app_state)
        message = require_arg(arguments, _ARG_MESSAGE, str)
        result = await svc.run_turn(
            InterviewTurnArgs(
                message=NotBlankStr(wrap_untrusted(TAG_TASK_DATA, message)),
                created_by=_actor_id(actor),
                conversation_id=_opt_nonblank(arguments, _ARG_CONVERSATION_ID),
                project=_opt_nonblank(arguments, _ARG_PROJECT),
            )
        )
        logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=_TOOL_INTERVIEW)
        return ok(result.model_dump(mode="json"))
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(_TOOL_INTERVIEW, exc)
        return err(exc)
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        log_handler_invoke_failed(_TOOL_INTERVIEW, exc)
        return err(exc)


async def _charter_list(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    try:
        svc = _require_charter_service(app_state)
        status = _parse_status(arguments)
        project_id = _opt_nonblank(arguments, _ARG_PROJECT_ID)
        created_by = _opt_nonblank(arguments, _ARG_CREATED_BY)
        limit = _parse_int(arguments, _ARG_LIMIT, default=_DEFAULT_LIMIT, floor=1)
        offset = _parse_int(arguments, _ARG_OFFSET, default=0, floor=0)
        charters = await svc.list_charters(
            status=status,
            project_id=project_id,
            created_by=created_by,
            limit=limit,
            offset=offset,
        )
        logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=_TOOL_LIST)
        return ok([c.model_dump(mode="json") for c in charters])
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(_TOOL_LIST, exc)
        return err(exc)
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        log_handler_invoke_failed(_TOOL_LIST, exc)
        return err(exc)


async def _charter_get(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    try:
        svc = _require_charter_service(app_state)
        charter_id = NotBlankStr(require_arg(arguments, _ARG_CHARTER_ID, str))
        charter = await svc.get(charter_id)
        logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=_TOOL_GET)
        return ok(charter.model_dump(mode="json"))
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(_TOOL_GET, exc)
        return err(exc)
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        log_handler_invoke_failed(_TOOL_GET, exc)
        return err(exc)


async def _charter_cancel(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    try:
        svc = _require_charter_service(app_state)
        charter_id = NotBlankStr(require_arg(arguments, _ARG_CHARTER_ID, str))
        cancelled = await svc.cancel_charter(charter_id, cancelled_by=_actor_id(actor))
        logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=_TOOL_CANCEL)
        return ok(cancelled.model_dump(mode="json"))
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(_TOOL_CANCEL, exc)
        return err(exc)
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        log_handler_invoke_failed(_TOOL_CANCEL, exc)
        return err(exc)


async def _charter_approve(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    try:
        require_admin_guardrails(arguments, actor)
        dispatcher = _require_charter_dispatcher(app_state)
        charter_id = NotBlankStr(require_arg(arguments, _ARG_CHARTER_ID, str))
        result = await dispatcher.approve(charter_id, approved_by=_actor_id(actor))
        logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=_TOOL_APPROVE)
        return ok(result.model_dump(mode="json"))
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(_TOOL_APPROVE, exc)
        return err(exc)
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        log_handler_invoke_failed(_TOOL_APPROVE, exc)
        return err(exc)


CHARTER_HANDLERS: Mapping[str, ToolHandler] = MappingProxyType(
    {
        "synthorg_charter_interview": _charter_interview,
        "synthorg_charter_list": _charter_list,
        "synthorg_charter_get": _charter_get,
        "synthorg_charter_cancel": _charter_cancel,
        "synthorg_charter_approve": _charter_approve,
    },
)

__all__ = ["CHARTER_HANDLERS"]
