"""Research-subsystem domain MCP handlers.

Delegates to :class:`ResearchService` via ``app_state.research_service``.
``run`` executes a brief and returns the cited report; ``get`` / ``list``
read the run record. All three are standard (non-admin) operations.
"""

import builtins
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ValidationError

from synthorg.core.clock import SystemClock
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.core.types import NotBlankStr
from synthorg.meta.mcp.domains._research_args import (
    ResearchGetArgs,
    ResearchListArgs,
    ResearchRunArgs,
)
from synthorg.meta.mcp.errors import ArgumentValidationError, invalid_argument
from synthorg.meta.mcp.handler_protocol import (
    ToolHandler,  # noqa: TC001 -- PEP 649 annotation
)
from synthorg.meta.mcp.handlers.common import err, ok
from synthorg.meta.mcp.handlers.common_logging import (
    log_handler_argument_invalid,
    log_handler_invoke_failed,
)
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import MCP_HANDLER_INVOKE_SUCCESS
from synthorg.persistence.research_protocol import ResearchRunFilter
from synthorg.research.errors import ResearchRunNotFoundError
from synthorg.research.tool import build_research_brief, derive_research_ids

if TYPE_CHECKING:
    from collections.abc import Mapping

    from synthorg.core.agent import AgentIdentity

logger = get_logger(__name__)

_TOOL_RUN = "synthorg_research_run"
_TOOL_GET = "synthorg_research_get"
_TOOL_LIST = "synthorg_research_list"

_OPERATOR = NotBlankStr("operator")
_ARG_ARGUMENTS = "arguments"


def _typed_args[ArgsT: BaseModel](
    arguments: dict[str, Any],
    model: type[ArgsT],
) -> ArgsT:
    """Validate a raw MCP argument dict into its typed args model."""
    try:
        return model.model_validate(arguments)
    except ValidationError as exc:
        expected = f"valid {model.__name__}"
        raise invalid_argument(_ARG_ARGUMENTS, expected) from exc


def _require_service(app_state: Any) -> Any:
    svc = getattr(app_state, "research_service", None)
    if svc is None:
        msg = "research service is not wired on app_state in this deployment"
        raise ServiceUnavailableError(msg)
    return svc


def _created_by(actor: AgentIdentity | None) -> NotBlankStr:
    return NotBlankStr(str(actor.id)) if actor is not None else _OPERATOR


def _now(app_state: Any) -> Any:
    """Read the wall clock through the app-state Clock seam.

    Falls back to a fresh ``SystemClock`` when the seam is absent so the
    handler stays usable against minimal app-state stubs.
    """
    clock = getattr(app_state, "clock", None)
    return (clock or SystemClock()).now()


async def _research_run(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    try:
        svc = _require_service(app_state)
        args = _typed_args(arguments, ResearchRunArgs)
        brief_id, run_id = derive_research_ids(args, project_id=args.project_id)
        brief = build_research_brief(
            args,
            brief_id=brief_id,
            project_id=args.project_id,
            created_at=_now(app_state),
        )
        run = await svc.run(brief, run_id=run_id, created_by=_created_by(actor))
        logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=_TOOL_RUN)
        return ok(run.model_dump(mode="json"))
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(_TOOL_RUN, exc)
        return err(exc)
    except builtins.MemoryError, RecursionError:
        raise
    except Exception as exc:
        log_handler_invoke_failed(_TOOL_RUN, exc)
        return err(exc)


async def _research_get(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    try:
        svc = _require_service(app_state)
        args = _typed_args(arguments, ResearchGetArgs)
        run = await svc.get_run(args.run_id)
        if run is None:
            msg = f"Research run {args.run_id!r} not found"
            not_found = ResearchRunNotFoundError(msg)
            log_handler_invoke_failed(_TOOL_GET, not_found)
            return err(not_found)
        logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=_TOOL_GET)
        return ok(run.model_dump(mode="json"))
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(_TOOL_GET, exc)
        return err(exc)
    except builtins.MemoryError, RecursionError:
        raise
    except Exception as exc:
        log_handler_invoke_failed(_TOOL_GET, exc)
        return err(exc)


async def _research_list(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    try:
        svc = _require_service(app_state)
        args = _typed_args(arguments, ResearchListArgs)
        runs = await svc.list_runs(
            ResearchRunFilter(
                brief_id=args.brief_id,
                project_id=args.project_id,
                status=args.status,
            ),
            limit=args.limit,
            offset=args.offset,
        )
        logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=_TOOL_LIST)
        return ok([r.model_dump(mode="json") for r in runs])
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(_TOOL_LIST, exc)
        return err(exc)
    except builtins.MemoryError, RecursionError:
        raise
    except Exception as exc:
        log_handler_invoke_failed(_TOOL_LIST, exc)
        return err(exc)


RESEARCH_HANDLERS: Mapping[str, ToolHandler] = MappingProxyType(
    {
        _TOOL_RUN: _research_run,
        _TOOL_GET: _research_get,
        _TOOL_LIST: _research_list,
    },
)
