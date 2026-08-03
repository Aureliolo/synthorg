# module-kind: code
"""Minimal MCP JSON-RPC dispatch for the credentialed-tool server.

Handles the three methods a tool-consuming client needs, ``initialize``,
``tools/list`` and ``tools/call``, over a streamable-http transport. Kept
free of Litestar so the dispatch is unit-testable; the controller only
authenticates the per-run bearer, reads the body, and forwards each
message here. Tool failures surface as an ``isError`` tool result (so the
harness can react) while genuine protocol errors surface as JSON-RPC
errors.
"""

from collections.abc import Awaitable, Callable
from typing import Final

from synthorg.api.mcp_gateway.scoping import tool_schemas
from synthorg.api.mcp_gateway.tools import (
    CredentialedToolContext,
    invoke_credentialed_tool,
)
from synthorg.core.domain_errors import DomainError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.gateway import GATEWAY_DISPATCH_FAILED

logger = get_logger(__name__)

ToolContextProvider = Callable[[], Awaitable[CredentialedToolContext]]
"""Opens the host-side collaborators a ``tools/call`` executes against.

Deferred rather than passed in already-built, because only ``tools/call`` needs
them. The handshake methods do not, and a deployment that has configured no
forge or chat connection cannot build one at all: making them wait on it would
fail the ``initialize`` an embedded harness must complete before it can even
construct its agent.
"""

_PROTOCOL_VERSION: Final[str] = "2025-06-18"
_SERVER_NAME: Final[str] = "synthorg-credentialed-tools"
_METHOD_NOT_FOUND: Final[int] = -32601
_INVALID_PARAMS: Final[int] = -32602


async def dispatch_mcp(
    message: dict[str, object],
    *,
    open_context: ToolContextProvider,
    agent_id: str,
    capabilities: tuple[str, ...],
    allowed: tuple[str, ...] = (),
    denied: tuple[str, ...] = (),
) -> dict[str, object] | None:
    """Dispatch one MCP JSON-RPC message and return the response envelope.

    Args:
        message: A decoded JSON-RPC request or notification.
        open_context: Opens the host-side collaborators for tool execution;
            awaited only on ``tools/call``.
        agent_id: The authenticated actor id.
        capabilities: Capability patterns the actor is granted.
        allowed: Explicit tool-name allowances.
        denied: Explicit tool-name denials.

    Returns:
        The JSON-RPC response envelope, or ``None`` for a notification (a
        message with no ``id``).
    """
    message_id = message.get("id")
    method = message.get("method")
    if message_id is None:
        return None
    if method == "initialize":
        return _ok(message_id, _initialize_result())
    if method == "tools/list":
        schemas = tool_schemas(capabilities, allowed=allowed, denied=denied)
        return _ok(message_id, {"tools": schemas})
    if method == "tools/call":
        return await _tools_call(
            message_id,
            message.get("params"),
            open_context=open_context,
            agent_id=agent_id,
            capabilities=capabilities,
            allowed=allowed,
            denied=denied,
        )
    return _error(message_id, _METHOD_NOT_FOUND, f"unknown method: {method!r}")


def _initialize_result() -> dict[str, object]:
    """Return the ``initialize`` result advertising tool support.

    Returns:
        The MCP initialize result object.
    """
    return {
        "protocolVersion": _PROTOCOL_VERSION,
        "capabilities": {"tools": {}},
        "serverInfo": {"name": _SERVER_NAME, "version": "1"},
    }


async def _tools_call(
    message_id: object,
    params: object,
    *,
    open_context: ToolContextProvider,
    agent_id: str,
    capabilities: tuple[str, ...],
    allowed: tuple[str, ...],
    denied: tuple[str, ...],
) -> dict[str, object]:
    """Handle a ``tools/call`` request.

    Returns:
        A JSON-RPC response: a tool-result envelope on success (or a tool
        failure marked ``isError``), or a JSON-RPC error for malformed params.

    Raises:
        DomainError: The tool context could not be built. Building it is a
            deployment question (are the connections configured, is the catalog
            wired), not a property of the call, so it propagates to the
            transport and becomes an HTTP error. Folding it into the tool-result
            envelope would tell the agent its tool failed, inviting a retry or a
            different tool, when nothing it can do will help.
    """
    if not isinstance(params, dict):
        return _error(message_id, _INVALID_PARAMS, "params must be an object")
    name = params.get("name")
    arguments = params.get("arguments", {})
    if not isinstance(name, str) or not isinstance(arguments, dict):
        return _error(message_id, _INVALID_PARAMS, "invalid tool name or arguments")
    ctx = await open_context()
    try:
        text = await invoke_credentialed_tool(
            name,
            arguments,
            ctx=ctx,
            agent_id=agent_id,
            capabilities=capabilities,
            denied=denied,
            allowed=allowed,
        )
    except DomainError as exc:
        logger.warning(
            GATEWAY_DISPATCH_FAILED,
            surface="mcp",
            tool=name,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return _ok(message_id, _tool_error(safe_error_description(exc)))
    return _ok(message_id, _tool_text(text))


def _tool_text(text: str) -> dict[str, object]:
    """Return an MCP tool-result content block.

    Returns:
        The ``{content: [...]}`` result object.
    """
    return {"content": [{"type": "text", "text": text}], "isError": False}


def _tool_error(text: str) -> dict[str, object]:
    """Return an MCP tool-result block flagged as an error.

    Returns:
        The ``{content: [...], isError: true}`` result object.
    """
    return {"content": [{"type": "text", "text": text}], "isError": True}


def _ok(message_id: object, result: dict[str, object]) -> dict[str, object]:
    """Wrap *result* in a JSON-RPC success envelope.

    Returns:
        The JSON-RPC success envelope.
    """
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def _error(message_id: object, code: int, message: str) -> dict[str, object]:
    """Wrap an error in a JSON-RPC error envelope.

    Returns:
        The JSON-RPC error envelope.
    """
    return {
        "jsonrpc": "2.0",
        "id": message_id,
        "error": {"code": code, "message": message},
    }
