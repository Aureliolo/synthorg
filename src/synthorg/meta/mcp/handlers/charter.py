"""Project charter domain MCP handlers.

Delegates to ``app_state.charter_service`` (interview / list / get /
cancel) and ``app_state.charter_dispatcher`` (approve). The interview
message is fenced as untrusted task data before reaching the model, and
``approve`` is admin-gated at the registry layer and re-checks the
guardrail here.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING

from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.core.types import NotBlankStr
from synthorg.engine.prompt_safety import TAG_TASK_DATA, wrap_untrusted
from synthorg.meta.charter.dispatch import CharterDispatcher
from synthorg.meta.charter.enums import CharterStatus
from synthorg.meta.charter.models import InterviewTurnArgs
from synthorg.meta.charter.service import CharterInterviewService
from synthorg.meta.charter.state import CharterStateSlice
from synthorg.meta.mcp.domains._charter_args import (
    CharterApproveArgs,
    CharterCancelArgs,
    CharterGetArgs,
    CharterInterviewArgs,
    CharterListArgs,
)
from synthorg.meta.mcp.errors import ArgumentValidationError
from synthorg.meta.mcp.handler_protocol import (
    ToolHandler,
)
from synthorg.meta.mcp.handlers._mcp_handler_common import typed_args
from synthorg.meta.mcp.handlers.common import err, ok, require_admin_guardrails
from synthorg.meta.mcp.handlers.common_logging import (
    log_handler_argument_invalid,
    log_handler_invoke_failed,
)
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import MCP_HANDLER_INVOKE_SUCCESS

if TYPE_CHECKING:
    from synthorg.api.state import AppState

logger = get_logger(__name__)

_TOOL_INTERVIEW = "synthorg_charter_interview"
_TOOL_LIST = "synthorg_charter_list"
_TOOL_GET = "synthorg_charter_get"
_TOOL_CANCEL = "synthorg_charter_cancel"
_TOOL_APPROVE = "synthorg_charter_approve"

_MCP_OWNER_FALLBACK: NotBlankStr = NotBlankStr("mcp-operator")


def _actor_id(actor: AgentIdentity | None) -> NotBlankStr:
    """Resolve the acting identity, falling back to a stable MCP owner.

    Returns:
        ``NotBlankStr`` instance.
    """
    if actor is None:
        return _MCP_OWNER_FALLBACK
    return NotBlankStr(str(actor.id))


def _require_charter_service(app_state: AppState) -> CharterInterviewService:
    """Return the charter service or raise when unavailable.

    Raises:
        ServiceUnavailableError: Raised on the corresponding failure path.
    """
    svc = app_state.slice(CharterStateSlice).interview_service
    if svc is None:
        msg = "charter interview service is not wired in this deployment"
        raise ServiceUnavailableError(msg)
    return svc


def _require_charter_dispatcher(app_state: AppState) -> CharterDispatcher:
    """Return the charter dispatcher or raise when unavailable.

    Raises:
        ServiceUnavailableError: Raised on the corresponding failure path.
    """
    dispatcher = app_state.slice(CharterStateSlice).dispatcher
    if dispatcher is None:
        msg = "charter approval dispatcher is not wired in this deployment"
        raise ServiceUnavailableError(msg)
    return dispatcher


async def _charter_interview(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Return charter interview."""
    try:
        svc = _require_charter_service(app_state)
        interview_args = typed_args(arguments, CharterInterviewArgs)
        result = await svc.run_turn(
            InterviewTurnArgs(
                message=NotBlankStr(
                    wrap_untrusted(TAG_TASK_DATA, interview_args.message)
                ),
                created_by=_actor_id(actor),
                conversation_id=interview_args.conversation_id,
                project=interview_args.project,
            )
        )
        logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=_TOOL_INTERVIEW)
        return ok(result.model_dump(mode="json"))
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(_TOOL_INTERVIEW, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(_TOOL_INTERVIEW, exc)
        return err(exc)


async def _charter_list(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return charter list."""
    try:
        svc = _require_charter_service(app_state)
        list_args = typed_args(arguments, CharterListArgs)
        status = (
            CharterStatus(list_args.status) if list_args.status is not None else None
        )
        charters = await svc.list_charters(
            status=status,
            project_id=list_args.project_id,
            created_by=list_args.created_by,
            limit=list_args.limit,
            offset=list_args.offset,
        )
        logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=_TOOL_LIST)
        return ok([c.model_dump(mode="json") for c in charters])
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(_TOOL_LIST, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(_TOOL_LIST, exc)
        return err(exc)


async def _charter_get(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return charter get."""
    try:
        svc = _require_charter_service(app_state)
        charter_id = typed_args(arguments, CharterGetArgs).charter_id
        charter = await svc.get(charter_id)
        logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=_TOOL_GET)
        return ok(charter.model_dump(mode="json"))
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(_TOOL_GET, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(_TOOL_GET, exc)
        return err(exc)


async def _charter_cancel(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Return charter cancel."""
    try:
        # MCP cancel is admin-gated at the registry; the local
        # guardrail re-check (mandated for every admin tool) is what
        # actually authorises the ``enforce_ownership=False`` bypass.
        # Without it a caller invoking the handler map directly could
        # cancel another user's draft.
        require_admin_guardrails(arguments, actor)
        svc = _require_charter_service(app_state)
        charter_id = typed_args(arguments, CharterCancelArgs).charter_id
        cancelled = await svc.cancel_charter(
            charter_id,
            cancelled_by=_actor_id(actor),
            enforce_ownership=False,
        )
        logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=_TOOL_CANCEL)
        return ok(cancelled.model_dump(mode="json"))
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(_TOOL_CANCEL, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(_TOOL_CANCEL, exc)
        return err(exc)


async def _charter_approve(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Return charter approve."""
    try:
        require_admin_guardrails(arguments, actor)
        dispatcher = _require_charter_dispatcher(app_state)
        charter_id = typed_args(arguments, CharterApproveArgs).charter_id
        result = await dispatcher.approve(charter_id, approved_by=_actor_id(actor))
        logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=_TOOL_APPROVE)
        return ok(result.model_dump(mode="json"))
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(_TOOL_APPROVE, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
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
