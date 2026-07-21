# module-kind: code
"""Credentialed tool registry and the governed invoke path.

Each credentialed tool wraps an existing ``GovernedConnectionTool``
(forge / chat) so the connection approval gate, action-signature binding,
credential brokering and egress pinning are reused verbatim, host-side.
The invoke path scopes visibility per actor, validates arguments, runs the
governed tool, and fences the result with ``wrap_untrusted`` before it
returns to the harness. This module holds no transport; the streamable-http
controller drives it.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Final

from pydantic import BaseModel

from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.core.boundary import parse_typed
from synthorg.core.clock import Clock
from synthorg.core.domain_errors import ForbiddenError, ResourceNotFoundError
from synthorg.engine.prompt_safety import TAG_TOOL_RESULT, wrap_untrusted
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.observability import get_logger
from synthorg.observability.events.gateway import (
    GATEWAY_DISPATCH_FAILED,
    GATEWAY_REQUEST_RECEIVED,
)
from synthorg.tools.base import BaseTool, ToolExecutionResult
from synthorg.tools.chat._args import ChatDirectoryArgs, ChatMessagesArgs
from synthorg.tools.chat._runtime import ChatToolDeps, ChatToolsRuntime
from synthorg.tools.chat.chat_tools import ChatDirectoryTool, ChatMessagesTool
from synthorg.tools.forge._args import (
    ForgeCiArgs,
    ForgeIssueArgs,
    ForgePullRequestArgs,
    ForgeRepoArgs,
)
from synthorg.tools.forge._runtime import ForgeToolDeps, ForgeToolsRuntime
from synthorg.tools.forge.forge_tools import (
    ForgeCiTool,
    ForgeIssueTool,
    ForgePullRequestTool,
    ForgeRepoTool,
)

logger = get_logger(__name__)

_ERROR_PREFIX: Final[str] = "tool error: "

# Optional host-side security pre-check: raises to deny a call before dispatch.
type SecurityPreCheck = Callable[[str, dict[str, object]], Awaitable[None]]


@dataclass(frozen=True)
class CredentialedToolContext:
    """Host-side collaborators the credentialed tools need per call.

    Credentials are brokered from ``connection_catalog`` inside the governed
    tool and never leave the process. The connection names bind which forge /
    chat connection every call targets; egress is pinned to that connection's
    host by construction.
    """

    connection_catalog: ConnectionCatalog
    approval_store: ApprovalStoreProtocol
    clock: Clock
    forge_connection: str
    chat_connection: str
    forge_timeout_seconds: float
    chat_timeout_seconds: float
    forge_max_read_chars: int
    security_pre_check: SecurityPreCheck | None = None


@dataclass(frozen=True)
class _CredentialedTool:
    """A credentialed tool spec: schema, capability, and a governed builder."""

    name: str
    description: str
    capability: str
    args_model: type[BaseModel]
    build: Callable[[CredentialedToolContext, str], BaseTool]


def _forge_deps(ctx: CredentialedToolContext, agent_id: str) -> ForgeToolDeps:
    """Build per-call forge deps bound to *agent_id* and the forge connection.

    Returns:
        The per-call :class:`ForgeToolDeps`.
    """
    return ForgeToolDeps(
        runtime=ForgeToolsRuntime(
            connection_catalog=ctx.connection_catalog,
            connection_name=ctx.forge_connection,
            timeout_seconds=ctx.forge_timeout_seconds,
            max_read_chars=ctx.forge_max_read_chars,
        ),
        approval_store=ctx.approval_store,
        agent_id=agent_id,
        clock=ctx.clock,
    )


def _chat_deps(ctx: CredentialedToolContext, agent_id: str) -> ChatToolDeps:
    """Build per-call chat deps bound to *agent_id* and the chat connection.

    Returns:
        The per-call :class:`ChatToolDeps`.
    """
    return ChatToolDeps(
        runtime=ChatToolsRuntime(
            connection_catalog=ctx.connection_catalog,
            connection_name=ctx.chat_connection,
            timeout_seconds=ctx.chat_timeout_seconds,
        ),
        approval_store=ctx.approval_store,
        agent_id=agent_id,
        clock=ctx.clock,
    )


CREDENTIALED_TOOLS: Final[tuple[_CredentialedTool, ...]] = (
    _CredentialedTool(
        name="forge_repo",
        description="Read forge repo metadata, a file, or a directory listing.",
        capability="forge:read",
        args_model=ForgeRepoArgs,
        build=lambda ctx, aid: ForgeRepoTool(deps=_forge_deps(ctx, aid)),
    ),
    _CredentialedTool(
        name="forge_issue",
        description="Read, open, or comment on forge issues (writes need approval).",
        capability="forge:write",
        args_model=ForgeIssueArgs,
        build=lambda ctx, aid: ForgeIssueTool(deps=_forge_deps(ctx, aid)),
    ),
    _CredentialedTool(
        name="forge_pull_request",
        description=(
            "Read, open, comment, review or merge forge pull requests "
            "(writes need approval)."
        ),
        capability="forge:write",
        args_model=ForgePullRequestArgs,
        build=lambda ctx, aid: ForgePullRequestTool(deps=_forge_deps(ctx, aid)),
    ),
    _CredentialedTool(
        name="forge_ci",
        description="Read continuous-integration runs for the bound forge repo.",
        capability="forge:read",
        args_model=ForgeCiArgs,
        build=lambda ctx, aid: ForgeCiTool(deps=_forge_deps(ctx, aid)),
    ),
    _CredentialedTool(
        name="chat_messages",
        description="Send or read messages on the bound chat connection.",
        capability="chat:write",
        args_model=ChatMessagesArgs,
        build=lambda ctx, aid: ChatMessagesTool(deps=_chat_deps(ctx, aid)),
    ),
    _CredentialedTool(
        name="chat_directory",
        description="List channels or look up a user on the bound chat connection.",
        capability="chat:read",
        args_model=ChatDirectoryArgs,
        build=lambda ctx, aid: ChatDirectoryTool(deps=_chat_deps(ctx, aid)),
    ),
)

_TOOLS_BY_NAME: Final[dict[str, _CredentialedTool]] = {
    tool.name: tool for tool in CREDENTIALED_TOOLS
}


def _capability_matches(capability: str, patterns: tuple[str, ...]) -> bool:
    """Return whether *capability* (``domain:action``) matches any pattern.

    Patterns support ``domain:action`` (exact), ``domain:*`` (all actions in
    a domain), ``*:action`` (an action across domains) and ``*`` (everything),
    mirroring :class:`MCPToolScoper`.

    Returns:
        ``True`` when any pattern matches *capability*.
    """
    domain, _, action = capability.partition(":")
    for pattern in patterns:
        if pattern in {"*", capability}:
            return True
        p_domain, _, p_action = pattern.partition(":")
        if p_domain in {"*", domain} and p_action in {"*", action}:
            return True
    return False


def visible_tool_names(
    *,
    capabilities: tuple[str, ...],
    allowed: tuple[str, ...] = (),
    denied: tuple[str, ...] = (),
) -> frozenset[str]:
    """Return the credentialed tool names visible to an actor.

    Resolution order (first match wins), mirroring :class:`MCPToolScoper`:
    an explicit ``denied`` name is excluded; an explicit ``allowed`` name is
    included; else a capability match includes it; otherwise it is excluded.
    Empty *capabilities* with no allowances grants nothing (secure default).

    Args:
        capabilities: Capability patterns the actor is granted.
        allowed: Explicit tool-name allowances (override capabilities).
        denied: Explicit tool-name denials (highest priority).

    Returns:
        The frozenset of visible tool names.
    """
    visible: set[str] = set()
    for spec in CREDENTIALED_TOOLS:
        if spec.name in denied:
            continue
        if spec.name in allowed or _capability_matches(spec.capability, capabilities):
            visible.add(spec.name)
    return frozenset(visible)


def tool_schemas(capabilities: tuple[str, ...]) -> list[dict[str, object]]:
    """Return MCP tool schemas for the tools visible under *capabilities*.

    Args:
        capabilities: Capability patterns the actor is granted.

    Returns:
        A list of ``{name, description, inputSchema}`` MCP tool descriptors.
    """
    visible = visible_tool_names(capabilities=capabilities)
    return [
        {
            "name": spec.name,
            "description": spec.description,
            "inputSchema": spec.args_model.model_json_schema(),
        }
        for spec in CREDENTIALED_TOOLS
        if spec.name in visible
    ]


async def invoke_credentialed_tool(  # noqa: PLR0913 -- scope + validate + dispatch surface
    name: str,
    arguments: dict[str, object],
    *,
    ctx: CredentialedToolContext,
    agent_id: str,
    capabilities: tuple[str, ...],
    allowed: tuple[str, ...] = (),
    denied: tuple[str, ...] = (),
) -> str:
    """Scope, validate, run and fence one credentialed tool call.

    Args:
        name: The tool name the harness requested.
        arguments: The raw tool arguments.
        ctx: Host-side collaborators (catalog, approval store, clock, config).
        agent_id: The calling actor's id, bound into the approval gate.
        capabilities: Capability patterns the actor is granted.
        allowed: Explicit tool-name allowances for this actor.
        denied: Explicit tool-name denials for this actor.

    Returns:
        The tool result, fenced with ``wrap_untrusted`` (or an approval-parking
        notice when a write awaits approval).

    Raises:
        ResourceNotFoundError: If *name* is not a credentialed tool.
        ForbiddenError: If the tool is not visible to the actor.
    """
    spec = _TOOLS_BY_NAME.get(name)
    if spec is None:
        msg = f"unknown credentialed tool: {name!r}"
        raise ResourceNotFoundError(msg)
    visible = visible_tool_names(
        capabilities=capabilities, allowed=allowed, denied=denied
    )
    if spec.name not in visible:
        msg = f"tool {name!r} is not permitted for this actor"
        raise ForbiddenError(msg)
    if ctx.security_pre_check is not None:
        await ctx.security_pre_check(name, arguments)
    logger.info(GATEWAY_REQUEST_RECEIVED, tool=name, agent_id=agent_id, surface="mcp")
    parse_typed("mcp_gateway.tool", arguments, spec.args_model)
    tool = spec.build(ctx, agent_id)
    result = await tool.execute(arguments=arguments)
    return wrap_untrusted(TAG_TOOL_RESULT, _render(result))


def _render(result: ToolExecutionResult) -> str:
    """Render a tool result to text for the harness.

    Returns:
        The result content, prefixed to flag an error result.
    """
    if result.is_error:
        logger.warning(GATEWAY_DISPATCH_FAILED, surface="mcp", is_error=True)
        return f"{_ERROR_PREFIX}{result.content}"
    return result.content
