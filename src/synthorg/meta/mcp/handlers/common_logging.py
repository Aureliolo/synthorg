"""Centralized structured-logging helpers for MCP tool handlers.

Every handler used to define its own thin wrappers around
``logger.warning(MCP_HANDLER_*, ...)``; this module provides the
canonical, single-source-of-truth versions that every handler imports.

Three helpers cover the three log paths every domain handler exercises:

* :func:`log_handler_argument_invalid` -- caught
  ``ArgumentValidationError`` from input validation;
* :func:`log_handler_invoke_failed` -- any other ``Exception`` from the
  service layer (with optional correlation kwargs); and
* :func:`log_handler_guardrail_violated` -- caught
  ``GuardrailViolationError`` from a destructive-op precondition.

All three emit at WARNING and route every error message through
:func:`safe_error_description` so secret-shaped fragments (Authorization
headers, Fernet ciphertexts, URI userinfo, etc.) never reach logs.

The module owns its own logger keyed at ``synthorg.meta.mcp.handlers``
so handler-layer log assertions in tests have a single, stable event
source regardless of which domain emitted the event.
"""

from typing import TYPE_CHECKING, Any

from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.mcp import (
    MCP_HANDLER_ARGUMENT_INVALID,
    MCP_HANDLER_GUARDRAIL_VIOLATED,
    MCP_HANDLER_INVOKE_FAILED,
)

if TYPE_CHECKING:
    from synthorg.meta.mcp.errors import GuardrailViolationError

logger = get_logger("synthorg.meta.mcp.handlers")


def log_handler_argument_invalid(tool: str, exc: Exception) -> None:
    """Emit ``MCP_HANDLER_ARGUMENT_INVALID`` at WARNING with safe error context.

    Called by handlers that catch ``ArgumentValidationError`` after
    typed-arg extraction or validation. The wire shape is fixed:
    ``tool_name``, ``error_type``, ``error`` (sanitised).

    Args:
        tool: Full ``synthorg_<domain>_<action>`` tool name.
        exc: The caught exception.
    """
    logger.warning(
        MCP_HANDLER_ARGUMENT_INVALID,
        tool_name=tool,
        error_type=type(exc).__name__,
        error=safe_error_description(exc),
    )


def log_handler_invoke_failed(
    tool: str,
    exc: Exception,
    **context: Any,
) -> None:
    """Emit ``MCP_HANDLER_INVOKE_FAILED`` at WARNING with safe error context.

    Called by handlers that catch a generic ``Exception`` from the
    service layer.  ``context`` carries optional correlation ids
    (e.g. ``task_id``, ``decision_id``) so a 404 entry can be tied back
    to the originating request rather than appearing as an anonymous
    "record missing" line in the audit log.

    Args:
        tool: Full ``synthorg_<domain>_<action>`` tool name.
        exc: The caught exception.
        **context: Optional correlation kwargs added to the structured
            event verbatim.
    """
    logger.warning(
        MCP_HANDLER_INVOKE_FAILED,
        tool_name=tool,
        error_type=type(exc).__name__,
        error=safe_error_description(exc),
        **context,
    )


def log_handler_guardrail_violated(
    tool: str,
    exc: GuardrailViolationError,
) -> None:
    """Emit ``MCP_HANDLER_GUARDRAIL_VIOLATED`` for destructive-op rejections.

    Records only the typed ``violation`` code; the human-readable
    message stays in the response envelope and never enters structured
    logs.

    Args:
        tool: Full ``synthorg_<domain>_<action>`` tool name.
        exc: The caught guardrail violation; its ``violation`` attribute
            is one of ``"missing_actor"``, ``"missing_confirm"``, or
            ``"missing_reason"``.
    """
    logger.warning(
        MCP_HANDLER_GUARDRAIL_VIOLATED,
        tool_name=tool,
        violation=exc.violation,
    )
