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

_RESERVED_INVOKE_FAILED_KWARGS = frozenset(
    {"tool_name", "error_type", "error", "event", "log_level"},
)
"""Reserved log-record keys :func:`log_handler_invoke_failed` injects.

A caller passing one of these as ``**context`` would silently overwrite
the canonical event field, corrupting audit trails. We reject the call
loudly instead.
"""

_SENSITIVE_CONTEXT_KEY_FRAGMENTS = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "token",
        "apikey",
        "api_key",
        "authorization",
        "credential",
        "credentials",
        "private_key",
        "privatekey",
    },
)
"""Substrings that, when found in a context kwarg key, indicate a credential.

``log_handler_invoke_failed`` forwards ``**context`` kwargs verbatim
without routing them through :func:`safe_error_description` -- they are
correlation ids, not error messages. To prevent a developer from
accidentally piping a credential into observability sinks, reject any
kwarg whose key (case-insensitively) contains one of these fragments.
"""


def _reject_sensitive_context_keys(context: dict[str, Any]) -> None:
    """Raise ``ValueError`` if any context key looks like a credential field.

    Defensive check on top of the reserved-kwarg guard. Catches mistakes
    like ``log_handler_invoke_failed(tool, exc, api_key="...")``. This
    runs before the actual log emission so the bad call is fail-fast at
    the point of misuse, not deferred to a downstream observability
    pipeline.
    """
    suspect = sorted(
        key
        for key in context
        if any(fragment in key.lower() for fragment in _SENSITIVE_CONTEXT_KEY_FRAGMENTS)
    )
    if suspect:
        msg = (
            f"context kwargs {suspect!r} look like credential fields; "
            "MCP_HANDLER_INVOKE_FAILED context is forwarded verbatim "
            "and would leak the value into observability sinks"
        )
        raise ValueError(msg)


def log_handler_argument_invalid(tool: str, exc: Exception) -> None:
    """Emit ``MCP_HANDLER_ARGUMENT_INVALID`` at WARNING with safe error context.

    Called by handlers that catch ``ArgumentValidationError`` after
    typed-arg extraction or validation. The wire shape is fixed:
    ``tool_name``, ``error_type``, ``error`` (sanitised). Error messages
    are routed through :func:`safe_error_description` so secret-shaped
    fragments (Authorization headers, Fernet ciphertexts, URI userinfo,
    etc.) are scrubbed before logging (SEC-1).

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

    Error messages are routed through :func:`safe_error_description` so
    secret-shaped fragments are scrubbed before logging (SEC-1). The
    ``**context`` kwargs are forwarded verbatim and are **not** scrubbed
    -- callers must not pass credentials, tokens, or other secrets
    through ``context``. This function defensively rejects context keys
    whose name suggests a credential (``password``, ``token``,
    ``api_key``, ``authorization``, ``secret``, etc.) so a slip is
    caught at the call site instead of leaking into observability
    sinks.

    Args:
        tool: Full ``synthorg_<domain>_<action>`` tool name.
        exc: The caught exception.
        **context: Optional correlation kwargs added to the structured
            event verbatim. Keys that would shadow the canonical event
            fields (``tool_name``, ``error_type``, ``error``, ``event``,
            ``log_level``) are rejected with ``ValueError`` so audit
            trails cannot be silently corrupted. Keys that look like
            credential fields are rejected for the same reason.

    Raises:
        ValueError: If ``context`` contains any reserved key listed
            above, or any key whose name suggests it carries a
            credential.
    """
    if reserved := _RESERVED_INVOKE_FAILED_KWARGS.intersection(context):
        msg = (
            f"context kwargs {sorted(reserved)!r} shadow reserved "
            "MCP_HANDLER_INVOKE_FAILED event fields"
        )
        raise ValueError(msg)
    _reject_sensitive_context_keys(context)
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
    logs (so the message itself cannot leak operator-supplied content
    into observability sinks).

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
